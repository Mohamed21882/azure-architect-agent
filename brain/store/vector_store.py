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
    """Upsert embedded chunks into Qdrant. Returns the number of points processed.

    For chunks with embedding=None, only the confidence payload fields are updated
    via set_payload() — used for reinforcement without re-embedding.
    """
    points: list[PointStruct] = []
    payload_only: list[Chunk] = []

    for chunk in chunks:
        if chunk.embedding is None:
            payload_only.append(chunk)
            continue
        points.append(
            PointStruct(
                id=str(uuid.UUID(chunk.chunk_id)),
                vector=chunk.embedding,
                payload={
                    "chunk_id":              chunk.chunk_id,
                    "doc_id":                chunk.doc_id,
                    "source":                chunk.source,
                    "source_repo":           chunk.source_repo,
                    "file_path":             chunk.file_path,
                    "title":                 chunk.title,
                    "chunk_index":           chunk.chunk_index,
                    "token_count":           chunk.token_count,
                    "content":               chunk.content[:2000],
                    "confidence":            chunk.confidence,
                    "source_count":          chunk.source_count,
                    "reinforcement_count":   chunk.reinforcement_count,
                    "last_confirmed_at":     chunk.last_confirmed_at,
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

    # Partial update: only write confidence fields for payload-only chunks
    for chunk in payload_only:
        try:
            client.set_payload(
                collection_name=config.qdrant_collection,
                payload={
                    "confidence":          chunk.confidence,
                    "source_count":        chunk.source_count,
                    "reinforcement_count": chunk.reinforcement_count,
                    "last_confirmed_at":   chunk.last_confirmed_at,
                },
                points=[str(uuid.UUID(chunk.chunk_id))],
                wait=True,
            )
        except Exception:
            pass

    return len(points) + len(payload_only)


def mark_superseded(
    client: QdrantClient,
    chunk_ids: list[str],
    config: BrainConfig,
) -> None:
    """Delete superseded chunks from Qdrant. Called before re-indexing a changed file."""
    if not chunk_ids:
        return
    try:
        points = [str(uuid.UUID(cid)) for cid in chunk_ids]
        client.delete(
            collection_name=config.qdrant_collection,
            points_selector=points,
            wait=True,
        )
    except Exception:
        pass
