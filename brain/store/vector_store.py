from __future__ import annotations

import uuid

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    OptimizersConfigDiff,
    PointStruct,
    VectorParams,
)

from brain.config import BrainConfig
from brain.models import Chunk


def get_client(config: BrainConfig) -> QdrantClient:
    return QdrantClient(url=config.qdrant_url, timeout=60)


def ensure_collection(client: QdrantClient, config: BrainConfig) -> None:
    """Create the Qdrant collection if it does not already exist."""
    existing = {c.name for c in client.get_collections().collections}
    if config.qdrant_collection not in existing:
        client.create_collection(
            collection_name=config.qdrant_collection,
            vectors_config=VectorParams(
                size=config.vector_size,
                distance=Distance.COSINE,
            ),
            # Delay HNSW build until enough vectors accumulate — faster ingest
            optimizers_config=OptimizersConfigDiff(indexing_threshold=20_000),
        )


def reset_collection(client: QdrantClient, config: BrainConfig) -> None:
    """Delete the collection if it exists, then recreate it with current config."""
    existing = {c.name for c in client.get_collections().collections}
    if config.qdrant_collection in existing:
        client.delete_collection(config.qdrant_collection)
    client.create_collection(
        collection_name=config.qdrant_collection,
        vectors_config=VectorParams(
            size=config.vector_size,
            distance=Distance.COSINE,
        ),
        optimizers_config=OptimizersConfigDiff(indexing_threshold=20_000),
    )


def upsert_chunks(
    client: QdrantClient,
    chunks: list[Chunk],
    config: BrainConfig,
) -> int:
    """Upsert embedded chunks into Qdrant. Returns the number of points sent."""
    points: list[PointStruct] = []
    for chunk in chunks:
        if chunk.embedding is None:
            continue
        points.append(
            PointStruct(
                id=str(uuid.UUID(chunk.chunk_id)),
                vector=chunk.embedding,
                payload={
                    "chunk_id":    chunk.chunk_id,
                    "doc_id":      chunk.doc_id,
                    "source":      chunk.source,
                    "source_repo": chunk.source_repo,
                    "file_path":   chunk.file_path,
                    "title":       chunk.title,
                    "chunk_index": chunk.chunk_index,
                    "token_count": chunk.token_count,
                    # Truncate content so the payload stays lean;
                    # full text lives in the BM25 index
                    "content":     chunk.content[:2000],
                    **{k: v for k, v in chunk.metadata.items()
                       if isinstance(v, (str, int, float, bool))},
                },
            )
        )
    if points:
        client.upsert(
            collection_name=config.qdrant_collection,
            points=points,
            wait=True,
        )
    return len(points)
