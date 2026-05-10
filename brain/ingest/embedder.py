from __future__ import annotations

import requests

from brain.config import BrainConfig
from brain.models import Chunk

# Ollama batch embed endpoint (available since Ollama 0.1.32)
_EMBED_ENDPOINT = "/api/embed"


def embed_texts(texts: list[str], config: BrainConfig) -> list[list[float]]:
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
            # Leave chunk.embedding = None; pipeline.py will warn and skip
            raise
    return chunks
