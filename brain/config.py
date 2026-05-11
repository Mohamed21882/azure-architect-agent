from __future__ import annotations

import os
from dataclasses import dataclass, field

_WIKI_ROOT = "/home/mohelal/Azure-Architect-Wiki"


@dataclass
class BrainConfig:
    # --- Paths ---
    wiki_root: str = _WIKI_ROOT

    # --- Qdrant ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "azure_wiki"
    vector_size: int = 768  # nomic-embed-text produces 768-dim vectors

    # --- Ollama embedding ---
    ollama_url: str = "http://localhost:11434"
    embed_model: str = "nomic-embed-text:latest"
    embed_batch_size: int = 32

    # --- Chunking ---
    chunk_size_words: int = 300    # target words per chunk
    chunk_overlap_words: int = 50  # word overlap between adjacent chunks
    min_chunk_words: int = 20      # discard fragments below this

    # --- BM25 persistence ---
    bm25_index_path: str = ""

    # --- Temporal decay half-lives (days) ---
    half_life_region_availability: int = 30
    half_life_api_docs: int = 60
    half_life_architecture: int = 180
    half_life_procedural: int = 365

    def __post_init__(self) -> None:
        if not self.bm25_index_path:
            self.bm25_index_path = os.path.join(
                self.wiki_root, "brain", "store", "bm25.pkl"
            )

    @property
    def region_availability_dir(self) -> str:
        return os.path.join(self.wiki_root, "raw", "region-availability")

    @property
    def source_dirs(self) -> dict[str, str]:
        """Mapping of logical repo name → root directory to walk."""
        r = self.wiki_root
        return {
            "architecture-center":  os.path.join(r, "raw", "architecture-center"),
            "azure-ai":             os.path.join(r, "raw", "azure-ai"),
            "azure-foundry":        os.path.join(r, "raw", "azure-foundry", "articles", "foundry"),
            "cli":                  os.path.join(r, "raw", "cli"),
            "region-availability":  os.path.join(r, "raw", "region-availability"),
        }


# Module-level singleton — import this in other modules
CONFIG = BrainConfig()
