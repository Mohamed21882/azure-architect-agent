"""pipeline.py — main ingest runner.

Flow per repo:
  local_reader.walk_source()
      → chunker.chunk_document()        (heading-aware sliding window)
      → embedder.embed_chunks()         (Ollama /api/embed in batches)
      → vector_store.upsert_chunks()    (Qdrant, batches of 256)
      → bm25_index.BM25Index.add()      (in-memory, persisted at end)

Run:
    python -m brain.ingest.pipeline
    # or with a custom embed model / collection:
    python -m brain.ingest.pipeline --model mistral-small:latest --collection my_wiki
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from dataclasses import dataclass

from tqdm import tqdm

from brain.config import BrainConfig, CONFIG
from brain.ingest.chunker import chunk_document
from brain.ingest.embedder import embed_chunks
from brain.ingest.local_reader import read_all_sources, walk_source
from brain.search.bm25_index import BM25Index
from brain.store.vector_store import ensure_collection, get_client, reset_collection, upsert_chunks

# Max chunks sent to Qdrant in a single upsert call
_QDRANT_BATCH = 256


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

    # ── Phase 4: Summary ──────────────────────────────────────────────────
    _print_stats_table(stats)
    print("Pipeline complete.\n")


def _parse_args() -> tuple[BrainConfig, bool]:
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
    args = parser.parse_args()

    cfg = BrainConfig(
        qdrant_url=args.qdrant_url,
        qdrant_collection=args.collection,
        embed_model=args.model,
        embed_batch_size=args.batch_size,
        chunk_size_words=args.chunk_size,
    )
    return cfg, args.reset


if __name__ == "__main__":
    run_pipeline(*_parse_args())
