"""pipeline.py — main ingest runner.

Flow per repo:
  local_reader.walk_source()
      → chunker.chunk_document()        (heading-aware sliding window)
      → embedder.embed_chunks()         (Ollama /api/embed in batches)
      → vector_store.upsert_chunks()    (Qdrant, batches of 256)
      → bm25_index.BM25Index.add()      (in-memory, persisted at end)

Sources ingested (configured in BrainConfig.source_dirs):
  architecture-center   raw/architecture-center
  azure-ai              raw/azure-ai
  azure-foundry         raw/azure-foundry/articles/foundry
  cli                   raw/cli
  region-availability   raw/region-availability   [priority:high — regional service availability]

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
    delete_by_file_paths,
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
    repo_commits: dict[str, str] = {}
    project_top = os.path.realpath(config.wiki_root)
    for repo_name, root_dir in config.source_dirs.items():
        top = _git_toplevel(root_dir) if os.path.isdir(root_dir) else ""
        if top and top != project_top:
            repo_commits[repo_name] = _git(["rev-parse", "HEAD"], root_dir).stdout.strip()
    _save_manifest(
        {"last_run": now_str, "files": manifest_files, "repo_commits": repo_commits}, config
    )
    print(f"  Manifest saved: {len(manifest_files)} files → data/ingest_manifest.json\n")

    # ── Phase 5: Summary ──────────────────────────────────────────────────
    _print_stats_table(stats)
    print("Pipeline complete.\n")


def _git(args: list[str], cwd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _git_toplevel(path: str) -> str:
    try:
        r = _git(["rev-parse", "--show-toplevel"], path, timeout=15)
        return os.path.realpath(r.stdout.strip()) if r.returncode == 0 else ""
    except Exception:
        return ""


def _phase(name: str) -> None:
    # Marker parsed by brain.kb.updater for the admin status panel
    print(f"::phase:: {name}", flush=True)


def _changed_md(root_dir: str, old: str, new: str) -> tuple[list[str], list[str], list[str]]:
    """(added, modified, deleted) absolute .md paths under root_dir between two commits.
    --relative scopes the diff to root_dir (e.g. azure-foundry/articles/foundry) and makes
    paths relative to it; renames count as delete old + add new."""
    r = _git(["diff", "--name-status", "--relative", "-M", old, new], root_dir, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200] or "git diff failed")
    added: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        status = parts[0][:1]
        paths = [os.path.join(root_dir, p) for p in parts[1:]]
        if status == "R" and len(paths) == 2:
            if paths[0].endswith(".md"):
                deleted.append(paths[0])
            if paths[1].endswith(".md"):
                added.append(paths[1])
        elif paths and paths[-1].endswith(".md"):
            {"A": added, "C": added, "D": deleted}.get(status, modified).append(paths[-1])
    return added, modified, deleted


def run_incremental(config: BrainConfig = CONFIG, summary_path: str = "") -> dict:
    """Incremental ingest: git pull each Microsoft source repo, re-index added/changed .md
    files, remove chunks of deleted/renamed files, then run a confidence decay pass.

    Old chunks are removed by file_path (Qdrant filter + BM25), so supersession works even
    without an ingest manifest. Curated sources inside the TE-1 repo are never pulled.
    """
    print("\n╔══════════════════════════════════════╗")
    print("║   TE-1 Brain Incremental Ingest      ║")
    print("╚══════════════════════════════════════╝\n")
    t_start = time.perf_counter()
    summary: dict = {"started_at": datetime.datetime.utcnow().isoformat(), "repos": {}}

    # ── Phase 0: Load manifest ────────────────────────────────────────────
    manifest = _load_manifest(config)
    files_manifest: dict[str, dict] = manifest.get("files", {})
    repo_commits: dict[str, str] = manifest.get("repo_commits", {})
    print(f"Manifest loaded: {len(files_manifest)} tracked files "
          f"(last run: {manifest.get('last_run') or 'never'})\n")

    # ── Phase 1: Connect to Qdrant + load BM25 ───────────────────────────
    _phase("connect")
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
    bm25_counts: dict[str, int] = defaultdict(int)
    for fp in bm25.file_paths:
        bm25_counts[fp] += 1

    # ── Phase 2: git pull + diff each Microsoft source repo ──────────────
    _phase("git pull")
    project_top = os.path.realpath(config.wiki_root)
    work: list[tuple[str, str, str]] = []  # (repo_name, abs_path, kind: added|modified|deleted)
    for repo_name, root_dir in config.source_dirs.items():
        if not os.path.isdir(root_dir):
            continue
        top = _git_toplevel(root_dir)
        if not top:
            print(f"  [{repo_name}] not a git repo — skipped")
            continue
        if top == project_top:
            print(f"  [{repo_name}] curated source in the TE-1 repo — not pulled (review manually)")
            continue

        rs = {"pull": "", "old_commit": "", "new_commit": "", "added": 0, "updated": 0,
              "superseded": 0, "chunks_added": 0, "chunks_removed": 0, "embed_failed": 0}
        summary["repos"][repo_name] = rs
        head = _git(["rev-parse", "HEAD"], root_dir).stdout.strip()
        # Diff from the commit last ingested; first run assumes the index matches HEAD.
        # Persist that baseline BEFORE pulling, so a run killed after the pull still
        # re-diffs from the right commit next time instead of silently skipping changes.
        if not repo_commits.get(repo_name) and head:
            repo_commits[repo_name] = head
            manifest["repo_commits"] = repo_commits
            _save_manifest(manifest, config)
        base = repo_commits.get(repo_name) or head
        rs["old_commit"] = base
        try:
            r = _git(["pull", "--ff-only"], root_dir, timeout=config.git_pull_timeout)
            out = (r.stdout.strip() or r.stderr.strip()).splitlines()
            rs["pull"] = ("ok: " if r.returncode == 0 else "FAILED: ") + (out[0] if out else "")
        except subprocess.TimeoutExpired:
            rs["pull"] = f"FAILED: timed out after {config.git_pull_timeout}s"
        print(f"  [{repo_name}] git pull {rs['pull']}")
        new = _git(["rev-parse", "HEAD"], root_dir).stdout.strip()
        rs["new_commit"] = new
        if base == new:
            continue
        try:
            added, modified, deleted = _changed_md(root_dir, base, new)
        except Exception as exc:
            print(f"  [{repo_name}] git diff failed: {exc}")
            rs["pull"] += f" | diff failed: {exc}"
            rs["new_commit"] = base  # don't advance — retry this range next run
            continue
        print(f"  [{repo_name}] {base[:10]}..{new[:10]}: "
              f"{len(added)} added, {len(modified)} modified, {len(deleted)} deleted .md")
        work += [(repo_name, p, "added") for p in added]
        work += [(repo_name, p, "modified") for p in modified]
        work += [(repo_name, p, "deleted") for p in deleted]

    # ── Phase 3: Supersede + re-index ─────────────────────────────────────
    _phase("re-index")
    total = len(work)
    print(f"\nChanged .md files to process: {total}")
    touched_bm25 = False
    import re as _re
    for n, (repo_name, file_path, kind) in enumerate(work, 1):
        rs = summary["repos"][repo_name]
        rel_path = os.path.relpath(file_path, config.wiki_root)
        print(f"[{n}/{total}] {kind:8} {rel_path}", flush=True)

        # Supersede: remove every existing chunk for this path (Qdrant + BM25)
        old_chunks = bm25_counts.pop(file_path, 0)
        try:
            delete_by_file_paths(qdrant, [file_path], config)
            mark_superseded(qdrant, files_manifest.get(rel_path, {}).get("chunk_ids", []), config)
        except Exception as exc:
            print(f"    [WARN] Qdrant delete failed for {rel_path}: {exc}")
        if bm25.remove_files({file_path}):
            touched_bm25 = True
        rs["chunks_removed"] += old_chunks
        files_manifest.pop(rel_path, None)

        if kind == "deleted" or not os.path.isfile(file_path):
            rs["superseded"] += 1
            continue

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                raw_content = fh.read()
        except OSError as exc:
            print(f"    [WARN] read failed: {exc}")
            continue

        clean = _re.sub(r"^---\s*\n.*?\n---\s*\n", "", raw_content, count=1, flags=_re.DOTALL).lstrip()
        title_match = _re.search(r"^#\s+(.+)", clean, _re.MULTILINE)
        title = (title_match.group(1).strip() if title_match
                 else os.path.basename(file_path)[:-3].replace("-", " ").title())

        doc = RawDocument(
            doc_id=hashlib.sha1(file_path.encode()).hexdigest(),
            content=clean,
            source=IngestSource.MICROSOFT_LEARN,
            source_repo=repo_name,
            file_path=file_path,
            title=title,
        )
        chunks = chunk_document(
            doc,
            chunk_size=config.chunk_size_words,
            overlap=config.chunk_overlap_words,
            min_words=config.min_chunk_words,
        )
        rs["added" if old_chunks == 0 else "updated"] += 1
        if not chunks:
            continue

        try:
            embed_chunks(chunks, config)
        except Exception as exc:
            print(f"    [WARN] Embed failed for {rel_path}: {exc}")
        embedded = [c for c in chunks if c.embedding is not None]
        rs["embed_failed"] += len(chunks) - len(embedded)
        if embedded:
            try:
                upsert_chunks(qdrant, embedded, config)
            except Exception as exc:
                print(f"    [WARN] Qdrant upsert failed for {rel_path}: {exc}")

        bm25.add(chunks)  # BM25 keeps un-embedded chunks too, as in the full pipeline
        touched_bm25 = True
        rs["chunks_added"] += len(chunks)
        files_manifest[rel_path] = {
            "hash": hashlib.sha256(raw_content.encode()).hexdigest(),
            "chunk_ids": [c.chunk_id for c in chunks],
            "ingested_at": datetime.datetime.utcnow().isoformat(),
        }

    for repo_name, rs in summary["repos"].items():
        if rs["new_commit"]:
            repo_commits[repo_name] = rs["new_commit"]
        print(f"  [{repo_name}] added {rs['added']}, updated {rs['updated']}, "
              f"superseded {rs['superseded']} file(s); chunks +{rs['chunks_added']} "
              f"-{rs['chunks_removed']}")

    # ── Phase 4: Confidence decay pass ────────────────────────────────────
    _phase("confidence decay")
    print("\nRunning confidence decay pass (batch size 500)...")
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
    summary["decay_updates"] = decay_updates

    # ── Phase 5: Persist BM25 + manifest ──────────────────────────────────
    _phase("save index")
    if touched_bm25:
        print(f"Rebuilding BM25 index ({len(bm25)} chunks)...")
        bm25.build()
        bm25.save(config.bm25_index_path)
        print(f"  Saved → {config.bm25_index_path}\n")

    now_str = datetime.datetime.utcnow().isoformat()
    manifest["last_run"] = now_str
    manifest["files"] = files_manifest
    manifest["repo_commits"] = repo_commits
    _save_manifest(manifest, config)
    print(f"  Manifest updated → data/ingest_manifest.json\n")

    summary["finished_at"] = now_str
    summary["duration_s"] = round(time.perf_counter() - t_start, 1)
    if summary_path:
        with open(summary_path, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
    print("Incremental ingest complete.\n")
    return summary


def _parse_args() -> tuple[BrainConfig, bool, bool, str]:
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
    parser.add_argument("--summary-json", default="",
                        help="With --incremental: write a JSON run summary to this path")
    args = parser.parse_args()

    cfg = BrainConfig(
        qdrant_url=args.qdrant_url,
        qdrant_collection=args.collection,
        embed_model=args.model,
        embed_batch_size=args.batch_size,
        chunk_size_words=args.chunk_size,
    )
    return cfg, args.reset, args.incremental, args.summary_json


if __name__ == "__main__":
    cfg, reset, incremental, summary_json = _parse_args()
    if incremental:
        run_incremental(cfg, summary_json)
    else:
        run_pipeline(cfg, reset)
