from __future__ import annotations

import time

import requests

from brain.config import BrainConfig
from brain.models import Chunk

# Ollama batch embed endpoint (available since Ollama 0.1.32)
_EMBED_ENDPOINT = "/api/embed"


_RETRY_DELAYS = (2, 5, 15)  # seconds between attempts; CPU Ollama can be briefly busy


def embed_texts(texts: list[str], config: BrainConfig) -> list[list[float]]:
    """embed_texts_once() with retries — transient Ollama errors used to drop whole
    batches of 32 chunks from the vector index during ingest."""
    for delay in _RETRY_DELAYS:
        try:
            return embed_texts_once(texts, config)
        except Exception:
            time.sleep(delay)
    return embed_texts_once(texts, config)


def embed_texts_once(texts: list[str], config: BrainConfig) -> list[list[float]]:
    """Send a list of texts to Ollama /api/embed and return the embedding matrix.

    Raises requests.HTTPError on non-2xx responses so the caller can decide
    whether to skip or abort.
    """
    url = config.ollama_url.rstrip("/") + _EMBED_ENDPOINT
    resp = requests.post(
        url,
        json={"model": config.embed_model, "input": texts},
        timeout=300,  # large batches on big models can be slow
    )
    resp.raise_for_status()
    data = resp.json()
    embeddings = data.get("embeddings")
    if not embeddings or len(embeddings) != len(texts):
        raise ValueError(
            f"Ollama returned {len(embeddings or [])} embeddings for {len(texts)} inputs"
        )
    return embeddings


def embed_chunks(chunks: list[Chunk], config: BrainConfig) -> list[Chunk]:
    """Embed chunks in-place in batches of config.embed_batch_size.

    Chunks whose embedding fails are left with embedding=None so the pipeline
    can skip them at the upsert stage without aborting the whole run.
    """
    batch_size = config.embed_batch_size
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        try:
            embeddings = embed_texts([c.content for c in batch], config)
            for chunk, emb in zip(batch, embeddings):
                chunk.embedding = emb
        except Exception:
            # Batch still failing after retries — isolate the bad chunk(s) one by one
            for chunk in batch:
                try:
                    chunk.embedding = embed_texts([chunk.content], config)[0]
                except Exception:
                    chunk.embedding = None  # caller counts and reports these
    return chunks
