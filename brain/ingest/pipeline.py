"""pipeline.py — main ingest runner.

Flow per repo:
  local_reader.walk_source()
      → chunker.chunk_document()        (heading-aware sliding window)
      → embedder.embed_chunks()         (Ollama /api/embed in batches)
      → vector_store.upsert_chunks()    (Qdrant, batches of 256)
      → bm25_index.BM25Index.add()      (in-memory, persisted at end)

Run:
    python -m brain.ingest.pipeline
    python -m brain.ingest.pipeline --incremental
    python -m brain.ingest.pipeline --model mistral-small:latest --collection my_wiki
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass

from tqdm import tqdm

from brain.config import BrainConfig, CONFIG
from brain.ingest.chunker import chunk_document
from brain.ingest.embedder import embed_chunks
from brain.ingest.local_reader import read_all_sources, walk_source
from brain.models import IngestSource, RawDocument
from brain.search.bm25_index import BM25Index
from brain.store.vector_store import (
    ensure_collection,
    get_client,
    mark_superseded,
    reset_collection,
    upsert_chunks,
)

_QDRANT_BATCH = 256
_MANIFEST_PATH = os.path.join(CONFIG.wiki_root, "data", "ingest_manifest.json")


@dataclass
class RepoStats:
    repo: str
    docs: int = 0
    chunks: int = 0
    embedded: int = 0
    upserted: int = 0
    skipped_docs: int = 0
    skipped_embed: int = 0
    elapsed_s: float = 0.0


def _print_stats_table(stats: dict[str, RepoStats]) -> None:
    cols = ("Repo", "Docs", "Chunks", "Embedded", "Upserted", "Skip-doc", "Skip-emb", "Time(s)")
    widths = (30, 6, 8, 9, 9, 9, 9, 8)
    header = "  ".join(f"{c:<{w}}" for c, w in zip(cols, widths))
    sep = "-" * len(header)

    print("\n" + "=" * len(header))
    print(header)
    print(sep)

    totals = RepoStats(repo="TOTAL")
    for s in stats.values():
        row = (
            s.repo, s.docs, s.chunks, s.embedded, s.upserted,
            s.skipped_docs, s.skipped_embed, f"{s.elapsed_s:.1f}",
        )
        print("  ".join(f"{str(v):<{w}}" for v, w in zip(row, widths)))
        totals.docs += s.docs
        totals.chunks += s.chunks
        totals.embedded += s.embedded
        totals.upserted += s.upserted
        totals.skipped_docs += s.skipped_docs
        totals.skipped_embed += s.skipped_embed
        totals.elapsed_s += s.elapsed_s

    print(sep)
    total_row = (
        "TOTAL", totals.docs, totals.chunks, totals.embedded, totals.upserted,
        totals.skipped_docs, totals.skipped_embed, f"{totals.elapsed_s:.1f}",
    )
    print("  ".join(f"{str(v):<{w}}" for v, w in zip(total_row, widths)))
    print("=" * len(header) + "\n")


def _load_manifest(config: BrainConfig) -> dict:
    manifest_path = os.path.join(config.wiki_root, "data", "ingest_manifest.json")
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_run": "", "files": {}}


def _save_manifest(manifest: dict, config: BrainConfig) -> None:
    manifest_path = os.path.join(config.wiki_root, "data", "ingest_manifest.json")
    try:
        os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
    except PermissionError:
        print(f"  [WARN] Cannot write manifest (permission denied: {manifest_path})")
        print("         Run: sudo chown -R $USER data/ to fix.")


def run_pipeline(config: BrainConfig = CONFIG, reset: bool = False) -> None:
    print("\n╔══════════════════════════════════════╗")
    print("║   TE-1 Brain Ingest Pipeline         ║")
    print("╚══════════════════════════════════════╝\n")

    # ── Phase 0: Scan sources ──────────────────────────────────────────────
    print("Scanning source directories...")
    repo_docs: dict[str, list] = defaultdict(list)
    for repo, doc in read_all_sources(config):
        repo_docs[repo].append(doc)

    total_docs = sum(len(v) for v in repo_docs.values())
    print(f"\n  {'Repo':<30} {'Files':>6}")
    print(f"  {'-'*37}")
    for repo, docs in repo_docs.items():
        print(f"  {repo:<30} {len(docs):>6}")
    print(f"  {'-'*37}")
    print(f"  {'TOTAL':<30} {total_docs:>6}\n")

    if total_docs == 0:
        print("No documents found. Check that raw/ source directories exist.")
        sys.exit(1)

    # ── Phase 1: Connect to Qdrant ─────────────────────────────────────────
    print("Connecting to Qdrant...")
    try:
        qdrant = get_client(config)
        if reset:
            print(f"  --reset: dropping '{config.qdrant_collection}' and recreating "
                  f"(vector_size={config.vector_size})...")
            reset_collection(qdrant, config)
            print(f"  Collection '{config.qdrant_collection}' recreated clean.\n")
        else:
            ensure_collection(qdrant, config)
            print(f"  Collection '{config.qdrant_collection}' ready "
                  f"(vector_size={config.vector_size}).\n")
    except Exception as exc:
        print(f"\n[ERROR] Cannot reach Qdrant at {config.qdrant_url}: {exc}")
        print("        Start Qdrant: docker run -p 6333:6333 qdrant/qdrant")
        sys.exit(1)

    bm25 = BM25Index.empty()
    stats: dict[str, RepoStats] = {
        repo: RepoStats(repo=repo, docs=len(docs))
        for repo, docs in repo_docs.items()
    }

    # Track file → chunk_ids for manifest
    manifest_chunks: dict[str, list[str]] = defaultdict(list)
    doc_content_by_path: dict[str, str] = {}
    for repo, docs in repo_docs.items():
        for doc in docs:
            doc_content_by_path[doc.file_path] = doc.content

    # ── Phase 2: Per-repo ingest ───────────────────────────────────────────
    for repo, docs in repo_docs.items():
        s = stats[repo]
        t0 = time.perf_counter()
        all_chunks = []

        # Step A — Chunk
        with tqdm(docs, desc=f"Chunking   [{repo}]", unit="doc", leave=True) as bar:
            for doc in bar:
                try:
                    chunks = chunk_document(
                        doc,
                        chunk_size=config.chunk_size_words,
                        overlap=config.chunk_overlap_words,
                        min_words=config.min_chunk_words,
                    )
                    all_chunks.extend(chunks)
                    s.chunks += len(chunks)
                    for chunk in chunks:
                        manifest_chunks[chunk.file_path].append(chunk.chunk_id)
                except Exception as exc:
                    s.skipped_docs += 1
                    tqdm.write(f"    [WARN] chunk {doc.file_path}: {exc}")

        if not all_chunks:
            tqdm.write(f"  [WARN] No chunks produced for {repo} — skipping embed/upsert.")
            s.elapsed_s = round(time.perf_counter() - t0, 1)
            continue

        # Step B — Embed
        failed_batches = 0
        with tqdm(
            total=len(all_chunks),
            desc=f"Embedding  [{repo}]",
            unit="chunk",
            leave=True,
        ) as bar:
            for i in range(0, len(all_chunks), config.embed_batch_size):
                batch = all_chunks[i : i + config.embed_batch_size]
                try:
                    embed_chunks(batch, config)
                    embedded_in_batch = sum(1 for c in batch if c.embedding is not None)
                    s.embedded += embedded_in_batch
                except Exception as exc:
                    failed_batches += 1
                    s.skipped_embed += len(batch)
                    tqdm.write(f"    [WARN] embed batch {i//config.embed_batch_size}: {exc}")
                bar.update(len(batch))

        if failed_batches:
            tqdm.write(
                f"    [{repo}] {failed_batches} embed batches failed — "
                f"those chunks will be BM25-only."
            )

        # Step C — Upsert to Qdrant
        embedded_chunks = [c for c in all_chunks if c.embedding is not None]
        with tqdm(
            total=len(embedded_chunks),
            desc=f"Qdrant     [{repo}]",
            unit="chunk",
            leave=True,
        ) as bar:
            for i in range(0, len(embedded_chunks), _QDRANT_BATCH):
                batch = embedded_chunks[i : i + _QDRANT_BATCH]
                try:
                    n = upsert_chunks(qdrant, batch, config)
                    s.upserted += n
                except Exception as exc:
                    tqdm.write(f"    [WARN] Qdrant upsert at {i}: {exc}")
                bar.update(len(batch))

        # Step D — Stage in BM25 (all chunks, not just embedded ones)
        bm25.add(all_chunks)

        s.elapsed_s = round(time.perf_counter() - t0, 1)
        print()  # blank line between repos

    # ── Phase 3: Persist BM25 ─────────────────────────────────────────────
    print(f"Building BM25 index over {len(bm25)} chunks...")
    bm25.build()
    bm25.save(config.bm25_index_path)
    print(f"  Saved → {config.bm25_index_path}\n")

    # ── Phase 4: Write ingest manifest ────────────────────────────────────
    now_str = datetime.datetime.utcnow().isoformat()
    manifest_files: dict[str, dict] = {}
    for file_path, chunk_ids in manifest_chunks.items():
        rel_path = os.path.relpath(file_path, config.wiki_root)
        raw_content = doc_content_by_path.get(file_path, "")
        content_hash = hashlib.sha256(raw_content.encode()).hexdigest()
        manifest_files[rel_path] = {
            "hash": content_hash,
            "chunk_ids": chunk_ids,
            "ingested_at": now_str,
        }
    _save_manifest({"last_run": now_str, "files": manifest_files}, config)
    print(f"  Manifest saved: {len(manifest_files)} files → data/ingest_manifest.json\n")

    # ── Phase 5: Summary ──────────────────────────────────────────────────
    _print_stats_table(stats)
    print("Pipeline complete.\n")


def run_incremental(config: BrainConfig = CONFIG) -> None:
    """Incremental ingest: git pull each source repo, re-index only changed .md files,
    then run a confidence decay pass over all Qdrant chunks."""
    print("\n╔══════════════════════════════════════╗")
    print("║   TE-1 Brain Incremental Ingest      ║")
    print("╚══════════════════════════════════════╝\n")

    # ── Phase 0: Load manifest ────────────────────────────────────────────
    manifest = _load_manifest(config)
    files_manifest: dict[str, dict] = manifest.get("files", {})
    print(f"Manifest loaded: {len(files_manifest)} tracked files "
          f"(last run: {manifest.get('last_run', 'never')})\n")

    # ── Phase 1: Connect to Qdrant + load BM25 ───────────────────────────
    print("Connecting to Qdrant...")
    try:
        qdrant = get_client(config)
        ensure_collection(qdrant, config)
        print(f"  Collection '{config.qdrant_collection}' ready.\n")
    except Exception as exc:
        print(f"\n[ERROR] Cannot reach Qdrant: {exc}")
        sys.exit(1)

    bm25 = BM25Index.empty()
    if os.path.exists(config.bm25_index_path):
        try:
            bm25 = BM25Index.load(config.bm25_index_path)
            print(f"  BM25 index loaded ({len(bm25)} chunks).\n")
        except Exception:
            print("  BM25 index not found or corrupt — starting fresh.\n")

    # ── Phase 2: git pull + diff each source repo ─────────────────────────
    changed_files: list[str] = []
    for repo_name, root_dir in config.source_dirs.items():
        if not os.path.isdir(root_dir):
            continue

        try:
            result = subprocess.run(
                ["git", "pull"],
                cwd=root_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )
            summary = result.stdout.strip().splitlines()[0] if result.stdout.strip() else "ok"
            print(f"  [{repo_name}] git pull: {summary}")
        except Exception as exc:
            print(f"  [{repo_name}] git pull skipped: {exc}")

        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD@{1}", "HEAD"],
                cwd=root_dir,
                capture_output=True,
                text=True,
                timeout=30,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.endswith(".md"):
                    abs_path = os.path.join(root_dir, line)
                    if os.path.isfile(abs_path):
                        changed_files.append(abs_path)
        except Exception as exc:
            print(f"  [{repo_name}] git diff skipped: {exc}")

    print(f"\nChanged .md files detected: {len(changed_files)}")

    # ── Phase 3: Re-index changed files ───────────────────────────────────
    updated_count = 0
    for file_path in changed_files:
        rel_path = os.path.relpath(file_path, config.wiki_root)

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                raw_content = fh.read()
        except OSError:
            continue

        new_hash = hashlib.sha256(raw_content.encode()).hexdigest()
        stored = files_manifest.get(rel_path, {})

        if stored.get("hash") == new_hash:
            continue  # content identical — skip

        print(f"  Re-indexing: {rel_path}")

        # Delete old chunks from Qdrant
        old_chunk_ids = stored.get("chunk_ids", [])
        if old_chunk_ids:
            mark_superseded(qdrant, old_chunk_ids, config)

        # Determine source repo from path
        source_repo = "unknown"
        for rn, rd in config.source_dirs.items():
            if file_path.startswith(rd):
                source_repo = rn
                break

        # Build RawDocument — strip YAML frontmatter inline
        import re as _re
        clean = _re.sub(r"^---\s*\n.*?\n---\s*\n", "", raw_content, count=1, flags=_re.DOTALL).lstrip()
        title_match = _re.search(r"^#\s+(.+)", clean, _re.MULTILINE)
        title = (title_match.group(1).strip() if title_match
                 else os.path.basename(file_path)[:-3].replace("-", " ").title())

        doc = RawDocument(
            doc_id=hashlib.sha1(file_path.encode()).hexdigest(),
            content=clean,
            source=IngestSource.MICROSOFT_LEARN,
            source_repo=source_repo,
            file_path=file_path,
            title=title,
        )

        chunks = chunk_document(
            doc,
            chunk_size=config.chunk_size_words,
            overlap=config.chunk_overlap_words,
            min_words=config.min_chunk_words,
        )
        if not chunks:
            continue

        try:
            embed_chunks(chunks, config)
        except Exception as exc:
            print(f"    [WARN] Embed failed for {rel_path}: {exc}")
            continue

        embedded = [c for c in chunks if c.embedding is not None]
        if embedded:
            upsert_chunks(qdrant, embedded, config)

        bm25.add(chunks)

        files_manifest[rel_path] = {
            "hash": new_hash,
            "chunk_ids": [c.chunk_id for c in chunks],
            "ingested_at": datetime.datetime.utcnow().isoformat(),
        }
        updated_count += 1

    print(f"  Re-indexed {updated_count} file(s).\n")

    # ── Phase 4: Confidence decay pass ────────────────────────────────────
    print("Running confidence decay pass (batch size 500)...")
    decay_updates = 0
    try:
        offset = None
        while True:
            records, offset = qdrant.scroll(
                collection_name=config.qdrant_collection,
                limit=500,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )

            for point in records:
                payload = point.payload or {}
                stored_confidence = payload.get("confidence")
                last_confirmed = payload.get("last_confirmed_at", "")
                if stored_confidence is None or not last_confirmed:
                    continue

                try:
                    confirmed = datetime.datetime.fromisoformat(last_confirmed)
                    days_since = max(0, (datetime.datetime.utcnow() - confirmed).days)
                    decayed = max(0.1, stored_confidence * (0.95 ** (days_since / 30)))
                except (ValueError, TypeError):
                    continue

                if abs(decayed - stored_confidence) > 0.01:
                    qdrant.set_payload(
                        collection_name=config.qdrant_collection,
                        payload={"confidence": round(decayed, 4)},
                        points=[point.id],
                        wait=False,
                    )
                    decay_updates += 1

            if offset is None:
                break

    except Exception as exc:
        print(f"  [WARN] Decay pass error: {exc}")

    print(f"  Confidence decay updates applied: {decay_updates}\n")

    # ── Phase 5: Persist BM25 + manifest ──────────────────────────────────
    if updated_count > 0:
        print(f"Rebuilding BM25 index ({len(bm25)} chunks)...")
        bm25.build()
        bm25.save(config.bm25_index_path)
        print(f"  Saved → {config.bm25_index_path}\n")

    now_str = datetime.datetime.utcnow().isoformat()
    manifest["last_run"] = now_str
    manifest["files"] = files_manifest
    _save_manifest(manifest, config)
    print(f"  Manifest updated → data/ingest_manifest.json\n")
    print("Incremental ingest complete.\n")


def _parse_args() -> tuple[BrainConfig, bool, bool]:
    parser = argparse.ArgumentParser(description="TE-1 Brain Ingest Pipeline")
    parser.add_argument("--collection", default=CONFIG.qdrant_collection,
                        help="Qdrant collection name")
    parser.add_argument("--model",      default=CONFIG.embed_model,
                        help="Ollama embedding model")
    parser.add_argument("--batch-size", type=int, default=CONFIG.embed_batch_size,
                        help="Embedding batch size")
    parser.add_argument("--chunk-size", type=int, default=CONFIG.chunk_size_words,
                        help="Target words per chunk")
    parser.add_argument("--qdrant-url", default=CONFIG.qdrant_url,
                        help="Qdrant base URL")
    parser.add_argument("--reset", action="store_true",
                        help="Delete and recreate the Qdrant collection before ingesting")
    parser.add_argument("--incremental", action="store_true",
                        help="Only re-index files changed since last run (git diff)")
    args = parser.parse_args()

    cfg = BrainConfig(
        qdrant_url=args.qdrant_url,
        qdrant_collection=args.collection,
        embed_model=args.model,
        embed_batch_size=args.batch_size,
        chunk_size_words=args.chunk_size,
    )
    return cfg, args.reset, args.incremental


if __name__ == "__main__":
    cfg, reset, incremental = _parse_args()
    if incremental:
        run_incremental(cfg)
    else:
        run_pipeline(cfg, reset)
