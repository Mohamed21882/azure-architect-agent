from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Union


class IngestSource(str, Enum):
    MICROSOFT_LEARN = "microsoft_learn"
    LOCAL = "local"


class EntityType(str, Enum):
    API_OPERATION         = "api_operation"
    ARCHITECTURE_PATTERN  = "architecture_pattern"
    REFERENCE_ARCH        = "reference_arch"
    REGION_AVAILABILITY   = "region_availability"
    GENERAL               = "general"


class MemoryTier(str, Enum):
    SEMANTIC   = "semantic"
    PROCEDURAL = "procedural"
    ENTITY     = "entity"


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
    confidence: float = 0.5
    source_count: int = 1
    reinforcement_count: int = 0
    last_confirmed_at: str = ""
    entity_type: Optional[EntityType] = None
    memory_tier: Optional[MemoryTier] = None

    def reinforce(self) -> None:
        """Increment reinforcement count and boost confidence. Called on human approval."""
        self.reinforcement_count += 1
        self.last_confirmed_at = datetime.datetime.utcnow().isoformat()
        self.confidence = min(1.0, 0.5 + 0.1 * self.reinforcement_count)

    def decayed_confidence(self) -> float:
        """Return confidence after time-based exponential decay since last confirmation."""
        if not self.last_confirmed_at:
            return self.confidence
        try:
            confirmed = datetime.datetime.fromisoformat(self.last_confirmed_at)
            days_since = max(0, (datetime.datetime.utcnow() - confirmed).days)
            return max(0.1, self.confidence * (0.95 ** (days_since / 30)))
        except ValueError:
            return self.confidence


@dataclass
class SearchResult:
    chunk: Chunk
    rrf_score: float
    fused_score: float
    age_days: float = 0.0
    is_stale: bool = False
    dense_rank: Optional[int] = None
    bm25_rank: Optional[int] = None
    bm25_score: float = 0.0
