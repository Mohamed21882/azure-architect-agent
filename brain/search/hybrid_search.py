from __future__ import annotations

from brain.config import BrainConfig
from brain.ingest.embedder import embed_texts
from brain.search.bm25_index import BM25Index
from brain.store.vector_store import get_client


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
    """
    if dense_candidates <= 0:
        dense_candidates = top_k * 3

    # --- Dense retrieval (falls back to BM25-only if Qdrant/Ollama unavailable) ---
    dense_hits = []
    try:
        embedding = embed_texts([query], config)[0]
        client = get_client(config)
        response = client.query_points(
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
    return [{"rrf_score": score, **payloads[cid]} for cid, score in ranked]
