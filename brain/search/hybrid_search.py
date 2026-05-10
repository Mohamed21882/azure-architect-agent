from __future__ import annotations

from brain.config import BrainConfig
from brain.ingest.embedder import embed_texts
from brain.search.bm25_index import BM25Index
from brain.store.vector_store import get_client, upsert_chunks


def _rrf_score(rank: int, k: int = 60) -> float:
    """Reciprocal Rank Fusion score for a result at zero-based rank."""
    return 1.0 / (k + rank + 1)


def hybrid_search(
    query: str,
    bm25: BM25Index,
    config: BrainConfig,
    top_k: int = 10,
    rrf_k: int = 60,
    dense_candidates: int = 0,
    reinforce: bool = False,
) -> list[dict]:
    """Fuse dense (Qdrant cosine) and sparse (BM25) results via RRF.

    Args:
        query:            Natural-language search string.
        bm25:             Loaded BM25Index instance.
        config:           BrainConfig with Qdrant / Ollama settings.
        top_k:            Number of results to return.
        rrf_k:            RRF smoothing constant (default 60).
        dense_candidates: How many candidates to fetch per retriever before
                          fusion. Defaults to top_k * 3.
        reinforce:        If True, call reinforce() on each returned chunk and
                          write updated confidence scores back to Qdrant.
                          Only pass True on human-approved architectures.
    """
    if dense_candidates <= 0:
        dense_candidates = top_k * 3

    # --- Dense retrieval (falls back to BM25-only if Qdrant/Ollama unavailable) ---
    dense_hits = []
    qdrant_client = None
    try:
        embedding = embed_texts([query], config)[0]
        qdrant_client = get_client(config)
        response = qdrant_client.query_points(
            collection_name=config.qdrant_collection,
            query=embedding,
            limit=dense_candidates,
            with_payload=True,
        )
        dense_hits = response.points
    except Exception:
        pass  # Qdrant or embedding unavailable — use BM25 only

    # --- Sparse retrieval ---
    sparse_hits = bm25.search(query, top_k=dense_candidates)

    # --- RRF fusion ---
    rrf_scores: dict[str, float] = {}
    payloads: dict[str, dict] = {}

    for rank, hit in enumerate(dense_hits):
        cid = hit.payload["chunk_id"]
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + _rrf_score(rank, rrf_k)
        payloads[cid] = {**hit.payload, "dense_rank": rank + 1}

    for rank, hit in enumerate(sparse_hits):
        cid = hit["chunk_id"]
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + _rrf_score(rank, rrf_k)
        if cid not in payloads:
            payloads[cid] = {**hit, "dense_rank": None}
        payloads[cid]["bm25_rank"] = rank + 1
        payloads[cid]["bm25_score"] = hit["bm25_score"]

    ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    results = [{"rrf_score": score, **payloads[cid]} for cid, score in ranked]

    if reinforce and results:
        _reinforce_results(results, qdrant_client, config)

    return results


def _reinforce_results(
    results: list[dict],
    qdrant_client,
    config: BrainConfig,
) -> None:
    """Call reinforce() on each result chunk and write confidence back to Qdrant."""
    from brain.models import Chunk, IngestSource

    client = qdrant_client
    if client is None:
        try:
            client = get_client(config)
        except Exception:
            return

    chunks_to_update: list[Chunk] = []
    for r in results:
        try:
            chunk = Chunk(
                chunk_id=r["chunk_id"],
                doc_id=r.get("doc_id", ""),
                content=r.get("content", ""),
                source=IngestSource(r.get("source", "microsoft_learn")),
                source_repo=r.get("source_repo", ""),
                file_path=r.get("file_path", ""),
                title=r.get("title", ""),
                chunk_index=r.get("chunk_index", 0),
                token_count=r.get("token_count", 0),
                confidence=r.get("confidence", 0.5),
                source_count=r.get("source_count", 1),
                reinforcement_count=r.get("reinforcement_count", 0),
                last_confirmed_at=r.get("last_confirmed_at", ""),
                embedding=None,
            )
            chunk.reinforce()
            chunks_to_update.append(chunk)
        except Exception:
            pass

    if chunks_to_update:
        try:
            upsert_chunks(client, chunks_to_update, config)
        except Exception:
            pass
