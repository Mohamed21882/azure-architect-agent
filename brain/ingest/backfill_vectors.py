"""Embed chunks that are in the BM25 index but have no vector in Qdrant.

    python -m brain.ingest.backfill_vectors            # active index
    python -m brain.ingest.backfill_vectors --dry-run  # report only

Chunks are rebuilt by re-chunking their source file with the same reader and chunker
as the pipeline; only chunks whose id AND content match the BM25 entry are embedded,
so nothing is written that differs from what keyword search already serves.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict

from brain.config import CONFIG, BrainConfig
from brain.ingest.chunker import chunk_document
from brain.ingest.embedder import embed_chunks
from brain.ingest.local_reader import _SOURCE_METADATA, walk_source
from brain.search.bm25_index import BM25Index
from brain.store.vector_store import get_client, upsert_chunks


def missing_chunk_ids(config: BrainConfig) -> tuple[BM25Index, set[str]]:
    bm25 = BM25Index.load(config.bm25_index_path)
    client = get_client(config)
    have: set[str] = set()
    offset = None
    while True:
        records, offset = client.scroll(config.qdrant_collection, limit=2000, offset=offset,
                                        with_payload=False, with_vectors=False)
        have.update(str(r.id) for r in records)
        if offset is None:
            break
    return bm25, {cid for cid in bm25.chunk_ids if cid not in have}


def backfill(config: BrainConfig = CONFIG, dry_run: bool = False) -> dict:
    bm25, missing = missing_chunk_ids(config)
    by_file: dict[str, set[str]] = defaultdict(set)
    expected: dict[str, str] = {}
    for cid, fp, content in zip(bm25.chunk_ids, bm25.file_paths, bm25.contents):
        if cid in missing:
            by_file[fp].add(cid)
            expected[cid] = content
    per_source = Counter(sr for cid, sr in zip(bm25.chunk_ids, bm25.source_repos) if cid in missing)
    print(f"Missing vectors: {len(missing)} in {len(by_file)} files {dict(per_source)}")
    result = {"missing": len(missing), "embedded": 0, "unmatched": 0, "failed": 0}
    if dry_run or not missing:
        return result

    client = get_client(config)
    for repo, root in config.source_dirs.items():
        files = {fp for fp in by_file if fp.startswith(root.rstrip("/") + "/")}
        if not files:
            continue
        todo = []
        # No excludes here: match whatever the existing index was built from
        for doc in walk_source(repo, root, extra_metadata=_SOURCE_METADATA.get(repo)):
            if doc.file_path not in files:
                continue
            for c in chunk_document(doc, chunk_size=config.chunk_size_words,
                                    overlap=config.chunk_overlap_words,
                                    min_words=config.min_chunk_words):
                if c.chunk_id in by_file[doc.file_path] and c.content == expected[c.chunk_id]:
                    todo.append(c)
        embed_chunks(todo, config)
        ok = [c for c in todo if c.embedding is not None]
        if ok:
            upsert_chunks(client, ok, config)
        result["embedded"] += len(ok)
        result["failed"] += len(todo) - len(ok)
        print(f"  [{repo}] embedded {len(ok)} / {len(todo)} matched chunks")
    result["unmatched"] = result["missing"] - result["embedded"] - result["failed"]
    print(f"Done: {result}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    r = backfill(CONFIG, dry_run=args.dry_run)
    return 0 if args.dry_run or (r["failed"] == 0 and r["unmatched"] == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
