from __future__ import annotations

import json
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

    # --- Retrieval ---
    brain_search_timeout: int = 180  # CPU Ollama may queue the query embedding behind a chat request

    # --- Knowledge base updates ---
    git_pull_timeout: int = 1800  # months of upstream history can take a while

    # --- Microsoft Learn MCP ---
    use_learn_mcp: bool = True
    learn_mcp_url: str = "https://learn.microsoft.com/api/mcp"

    # --- Azure MCP Server (read-only tenant context) ---
    use_azure_mcp: bool = True
    azure_mcp_image: str = "mcr.microsoft.com/azure-sdk/azure-mcp:latest"
    azure_mcp_timeout: int = 30

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
            "microsoft-fabric":     os.path.join(r, "raw", "microsoft-fabric"),
        }

    @property
    def source_excludes(self) -> dict[str, list[str]]:
        """Sub-folders (relative to the source dir) never ingested for that source.
        azure-ai and azure-foundry are both clones of azure-ai-docs; Foundry content is
        owned by the azure-foundry source, so azure-ai must not index it too."""
        return {"azure-ai": ["articles/foundry"]}

    def is_excluded(self, repo_name: str, abs_path: str) -> bool:
        root = self.source_dirs.get(repo_name, "")
        rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
        return any(rel == ex or rel.startswith(ex.rstrip("/") + "/")
                   for ex in self.source_excludes.get(repo_name, []))

    @property
    def manifest_path(self) -> str:
        """One ingest manifest per index, so a candidate rebuild never overwrites the
        manifest of the index that is serving."""
        name = "ingest_manifest.json" if self.qdrant_collection == "azure_wiki" \
            else f"ingest_manifest_{self.qdrant_collection}.json"
        return os.path.join(self.wiki_root, "data", name)

    @property
    def active_index_path(self) -> str:
        return os.path.join(self.wiki_root, "data", "active_index.json")

    def apply_active_index(self) -> None:
        """Point this config at the index recorded in data/active_index.json (written by
        brain.kb.index_switch). Absent file = the original azure_wiki + bm25.pkl."""
        try:
            with open(self.active_index_path, "r", encoding="utf-8") as fh:
                active = json.load(fh)
            self.qdrant_collection = active["collection"]
            self.bm25_index_path = active["bm25_path"]
        except (OSError, KeyError, ValueError):
            pass


# Module-level singleton — import this in other modules
CONFIG = BrainConfig()
CONFIG.apply_active_index()
