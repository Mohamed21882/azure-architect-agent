from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class IngestSource(str, Enum):
    MICROSOFT_LEARN = "microsoft_learn"
    LOCAL = "local"


@dataclass
class RawDocument:
    doc_id: str           # SHA-1 of absolute file path — stable across re-runs
    content: str
    source: IngestSource
    source_repo: str      # "architecture-center" | "azure-ai" | "azure-foundry" | "cli"
    file_path: str        # absolute path on disk
    title: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class Chunk:
    chunk_id: str         # UUID5(NAMESPACE_URL, f"{doc_id}#{chunk_index}")
    doc_id: str
    content: str
    source: IngestSource
    source_repo: str
    file_path: str
    title: str
    chunk_index: int
    token_count: int = 0  # approximate word count
    metadata: dict = field(default_factory=dict)
    embedding: Optional[list[float]] = None
