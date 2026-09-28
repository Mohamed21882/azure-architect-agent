"""Re-index ONE source in the serving index (Qdrant + BM25), leaving every other source as is.

    python -m brain.ingest.reindex_source region-availability

Used for curated sources that changed between rebuilds. Holds the knowledge base update
lock, embeds all new chunks BEFORE deleting anything (an embedding failure leaves the
index untouched), then swaps the source's chunks in Qdrant and BM25 (atomic save).
"""
from __future__ import annotations

import argparse
import sys

from qdrant_client.http.models import FieldCondition, Filter, FilterSelector, MatchValue

from brain.config import CONFIG, BrainConfig
from brain.ingest.chunker import chunk_document
from brain.ingest.embedder import embed_chunks
from brain.ingest.local_reader import _SOURCE_METADATA, walk_source
from brain.search.bm25_index import BM25Index
from brain.store.vector_store import get_client, upsert_chunks


def reindex_source(source: str, config: BrainConfig = CONFIG) -> dict:
    root = config.source_dirs.get(source)
    if not root:
        raise ValueError(f"unknown source '{source}' (known: {', '.join(config.source_dirs)})")

    chunks = []
    docs = 0
    for doc in walk_source(source, root, extra_metadata=_SOURCE_METADATA.get(source),
                           exclude_dirs=config.source_excludes.get(source)):
        docs += 1
        chunks += chunk_document(doc, chunk_size=config.chunk_size_words,
                                 overlap=config.chunk_overlap_words,
                                 min_words=config.min_chunk_words)
    if not chunks:
        raise RuntimeError(f"no chunks produced from {root} — nothing changed")
    embed_chunks(chunks, config)
    failed = sum(1 for c in chunks if c.embedding is None)
    if failed:
        raise RuntimeError(f"{failed}/{len(chunks)} chunks failed to embed — nothing changed")

    client = get_client(config)
    selector = FilterSelector(filter=Filter(must=[
        FieldCondition(key="source_repo", match=MatchValue(value=source))]))
    old_vectors = client.count(config.qdrant_collection, count_filter=selector.filter, exact=True).count
    bm25 = BM25Index.load(config.bm25_index_path)
    old_files = {fp for fp, sr in zip(bm25.file_paths, bm25.source_repos) if sr == source}

    client.delete(config.qdrant_collection, points_selector=selector, wait=True)
    upsert_chunks(client, chunks, config)
    removed = bm25.remove_files(old_files)
    bm25.add(chunks)
    bm25.build()
    bm25.save(config.bm25_index_path)

    new_vectors = client.count(config.qdrant_collection, count_filter=selector.filter, exact=True).count
    return {"source": source, "collection": config.qdrant_collection, "docs": docs,
            "chunks_removed": removed, "vectors_removed": old_vectors,
            "chunks_added": len(chunks), "vectors_now": new_vectors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", help="source name from BrainConfig.source_dirs")
    args = parser.parse_args()

    from brain.kb.updater import _acquire_lock
    lock = _acquire_lock()
    if lock is None:
        print("A knowledge base update is running — try again when it finishes.", file=sys.stderr)
        return 3
    try:
        print(f"Re-indexing '{args.source}' into '{CONFIG.qdrant_collection}' + {CONFIG.bm25_index_path}")
        print(reindex_source(args.source))
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
