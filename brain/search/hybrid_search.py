from __future__ import annotations

import math
import os
import uuid as _uuid
from datetime import datetime, timezone

from brain.config import BrainConfig
from brain.ingest.embedder import embed_texts
from brain.models import Chunk, IngestSource, SearchResult
from brain.search.bm25_index import BM25Index
from brain.store.vector_store import get_client, upsert_chunks

# Age assigned to chunks with no timestamp information available
_NEUTRAL_AGE_DAYS: float = 180.0


def _rrf_score(rank: int, k: int = 60) -> float:
    return 1.0 / (k + rank + 1)


def _compute_age_days(last_confirmed_at: str, file_path: str = "") -> float:
    """Return the effective age in days for a chunk.

    Priority:
    1. last_confirmed_at ISO string (set on human approval)
    2. file mtime (for freshly ingested files with no human confirmation)
    3. _NEUTRAL_AGE_DAYS fallback
    """
    if last_confirmed_at:
        try:
            confirmed = datetime.fromisoformat(last_confirmed_at)
            if confirmed.tzinfo is None:
                confirmed = confirmed.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - confirmed
            return max(0.0, delta.total_seconds() / 86400)
        except ValueError:
            pass

    if file_path:
        try:
            mtime = os.path.getmtime(file_path)
            delta_s = datetime.now(timezone.utc).timestamp() - mtime
            return max(0.0, delta_s / 86400)
        except OSError:
            pass

    return _NEUTRAL_AGE_DAYS


def _get_half_life(chunk: Chunk, config: BrainConfig) -> float:
    """Return the appropriate half-life (days) based on source_repo."""
    repo = (chunk.source_repo or "").lower()
    if "region" in repo or "availability" in repo:
        return float(config.half_life_region_availability)
    if repo == "cli" or "api" in repo:
        return float(config.half_life_api_docs)
    if repo in ("architecture-center", "azure-foundry", "azure-ai"):
        return float(config.half_life_architecture)
    return float(config.half_life_procedural)


def _live_decayed_confidence(chunk: Chunk, half_life_days: float, age_days: float) -> float:
    """Exponential decay: conf × exp(-ln2 × age / half_life). No decay without confirmation."""
    if not chunk.last_confirmed_at:
        return chunk.confidence
    decay = math.exp(-math.log(2) * age_days / half_life_days)
    return round(chunk.confidence * decay, 4)


def _freshness_multiplier(age_days: float) -> float:
    if age_days <= 30:
        return 1.15
    elif age_days <= 90:
        return 1.05
    elif age_days <= 365:
        return 1.0
    elif age_days <= 548:
        return 0.90
    return 0.75


def _payload_to_chunk(payload: dict) -> Chunk:
    return Chunk(
        chunk_id=payload.get("chunk_id", ""),
        doc_id=payload.get("doc_id", ""),
        content=payload.get("content", ""),
        source=IngestSource(payload.get("source", "microsoft_learn")),
        source_repo=payload.get("source_repo", ""),
        file_path=payload.get("file_path", ""),
        title=payload.get("title", ""),
        chunk_index=payload.get("chunk_index", 0),
        token_count=payload.get("token_count", 0),
        confidence=float(payload.get("confidence", 0.5)),
        source_count=payload.get("source_count", 1),
        reinforcement_count=payload.get("reinforcement_count", 0),
        last_confirmed_at=payload.get("last_confirmed_at", ""),
        embedding=None,
    )


def hybrid_search(
    query: str,
    bm25: BM25Index,
    config: BrainConfig,
    top_k: int = 10,
    rrf_k: int = 60,
    dense_candidates: int = 0,
    reinforce: bool = False,
    max_age_days: float = 548.0,
) -> list[SearchResult]:
    """Fuse dense (Qdrant cosine) and sparse (BM25) results via RRF with temporal reranking.

    Args:
        query:            Natural-language search string.
        bm25:             Loaded BM25Index instance.
        config:           BrainConfig with Qdrant / Ollama settings.
        top_k:            Number of results to return.
        rrf_k:            RRF smoothing constant (default 60).
        dense_candidates: Candidates per retriever before fusion. Defaults to top_k * 3.
        reinforce:        If True, call reinforce() on returned chunks and write
                          updated confidence scores back to Qdrant. Only pass True
                          on human-approved architectures.
        max_age_days:     Hard expiry threshold. Chunks older than this are dropped
                          unless no fresh results exist (fallback: return all as stale).
    """
    if dense_candidates <= 0:
        dense_candidates = top_k * 3

    # --- Dense retrieval ---
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
        pass

    # --- Sparse retrieval ---
    sparse_hits = bm25.search(query, top_k=dense_candidates)

    # --- RRF fusion ---
    rrf_scores: dict[str, float] = {}
    payloads: dict[str, dict] = {}
    bm25_meta: dict[str, tuple[int, float]] = {}  # cid -> (bm25_rank, bm25_score)

    for rank, hit in enumerate(dense_hits):
        cid = hit.payload["chunk_id"]
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + _rrf_score(rank, rrf_k)
        payloads[cid] = {**hit.payload, "dense_rank": rank + 1}

    for rank, hit in enumerate(sparse_hits):
        cid = hit["chunk_id"]
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + _rrf_score(rank, rrf_k)
        if cid not in payloads:
            payloads[cid] = {**hit, "dense_rank": None}
        bm25_meta[cid] = (rank + 1, hit.get("bm25_score", 0.0))

    ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

    # --- Build SearchResult objects with temporal scoring ---
    results: list[SearchResult] = []
    for cid, rrf in ranked:
        p = payloads[cid]
        chunk = _payload_to_chunk(p)
        age = _compute_age_days(chunk.last_confirmed_at, chunk.file_path)
        half_life = _get_half_life(chunk, config)
        decayed_conf = _live_decayed_confidence(chunk, half_life, age)
        freshness = _freshness_multiplier(age)
        fused = rrf * decayed_conf * freshness
        bm25_rank, bm25_score = bm25_meta.get(cid, (None, 0.0))
        results.append(SearchResult(
            chunk=chunk,
            rrf_score=rrf,
            fused_score=round(fused, 6),
            age_days=round(age, 1),
            is_stale=False,
            dense_rank=p.get("dense_rank"),
            bm25_rank=bm25_rank,
            bm25_score=bm25_score,
        ))

    # --- Hard expiry filter ---
    fresh = [r for r in results if r.age_days <= max_age_days]
    if fresh:
        results = fresh
    else:
        # All results are beyond max_age — return them all as stale
        for r in results:
            r.is_stale = True

    # --- Corroboration: mark old chunks lacking a fresh peer as stale ---
    # A chunk > 180 days is corroborated if another result from the same
    # source_repo with age <= 90 days also appears in the result set.
    fresh_repos = {r.chunk.source_repo for r in results if r.age_days <= 90}
    for r in results:
        if r.age_days > 180 and r.chunk.source_repo not in fresh_repos:
            r.is_stale = True

    # Re-sort by fused_score after temporal weighting
    results.sort(key=lambda r: r.fused_score, reverse=True)

    if reinforce and results:
        _reinforce_results(results, qdrant_client, config)

    return results


def _reinforce_results(
    results: list[SearchResult],
    qdrant_client,
    config: BrainConfig,
) -> None:
    """Call reinforce() on each result chunk and write confidence back to Qdrant."""
    client = qdrant_client
    if client is None:
        try:
            client = get_client(config)
        except Exception:
            return

    chunks_to_update: list[Chunk] = []
    for sr in results:
        try:
            sr.chunk.reinforce()
            chunks_to_update.append(sr.chunk)
        except Exception:
            pass

    if chunks_to_update:
        try:
            upsert_chunks(client, chunks_to_update, config)
        except Exception:
            pass


_TECHNICAL_CATEGORIES = {"wrong_service_behaviour", "wrong_region_availability"}


def apply_feedback_to_chunks(
    chunk_ids: list,
    rating: str,
    category: str = None,
) -> None:
    """Update chunk confidence in Qdrant based on human feedback.

    Positive: reinforce() each chunk (confidence boost + increment count).
    Negative with technical category: apply -0.05 decay (floor 0.1).
    Other negative categories: no confidence change.
    """
    from brain.config import CONFIG

    if not chunk_ids:
        return
    if rating not in ("positive", "negative"):
        return
    if rating == "negative" and category not in _TECHNICAL_CATEGORIES:
        return

    try:
        client = get_client(CONFIG)
    except Exception:
        return

    chunks_to_update: list[Chunk] = []
    for cid in chunk_ids:
        try:
            point_id = str(_uuid.UUID(cid))
        except (ValueError, AttributeError):
            continue
        try:
            records = client.retrieve(
                collection_name=CONFIG.qdrant_collection,
                ids=[point_id],
                with_payload=True,
                with_vectors=False,
            )
        except Exception:
            continue
        if not records:
            continue

        payload = records[0].payload or {}
        try:
            chunk = _payload_to_chunk({**payload, "chunk_id": cid})
        except Exception:
            continue

        if rating == "positive":
            chunk.reinforce()
        else:
            chunk.confidence = max(0.1, chunk.confidence - 0.05)

        chunks_to_update.append(chunk)

    if chunks_to_update:
        try:
            upsert_chunks(client, chunks_to_update, CONFIG)
        except Exception:
            pass
