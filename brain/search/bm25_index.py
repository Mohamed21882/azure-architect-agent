from __future__ import annotations

import os
import pickle
import re
from dataclasses import dataclass, field
from typing import Optional

from rank_bm25 import BM25Okapi

from brain.models import Chunk

_TOKEN_RE = re.compile(r"\b[a-z0-9][a-z0-9\-]{1,}\b")


def _tokenise(text: str) -> list[str]:
    """Lowercase, strip punctuation, return word tokens of length ≥ 2."""
    return _TOKEN_RE.findall(text.lower())


@dataclass
class BM25Index:
    """Persistent BM25 index over chunk content.

    Usage pattern:
        idx = BM25Index.empty()
        idx.add(chunks)          # can be called multiple times
        idx.build()              # must call before search
        results = idx.search("azure networking", top_k=10)
        idx.save("/path/bm25.pkl")

        idx2 = BM25Index.load("/path/bm25.pkl")
        results = idx2.search("query")   # build() happens lazily
    """

    chunk_ids: list[str] = field(default_factory=list)
    file_paths: list[str] = field(default_factory=list)
    source_repos: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    contents: list[str] = field(default_factory=list)
    _bm25: Optional[BM25Okapi] = field(default=None, repr=False)

    @classmethod
    def empty(cls) -> "BM25Index":
        return cls()

    def add(self, chunks: list[Chunk]) -> None:
        """Append chunks to the index. Invalidates any built BM25 object."""
        for c in chunks:
            self.chunk_ids.append(c.chunk_id)
            self.file_paths.append(c.file_path)
            self.source_repos.append(c.source_repo)
            self.titles.append(c.title)
            self.contents.append(c.content)
        self._bm25 = None  # force rebuild on next search

    def build(self) -> None:
        """Fit BM25Okapi over the accumulated corpus. Call after all add() calls."""
        corpus = [_tokenise(text) for text in self.contents]
        self._bm25 = BM25Okapi(corpus)

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        """Return up to top_k results sorted by BM25 score descending."""
        if self._bm25 is None:
            self.build()

        tokens = _tokenise(query)
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)

        results: list[dict] = []
        for idx, score in ranked:
            if score <= 0 or len(results) >= top_k:
                break
            results.append(
                {
                    "chunk_id":    self.chunk_ids[idx],
                    "file_path":   self.file_paths[idx],
                    "source_repo": self.source_repos[idx],
                    "title":       self.titles[idx],
                    "content":     self.contents[idx][:500],
                    "bm25_score":  float(score),
                }
            )
        return results

    def __len__(self) -> int:
        return len(self.chunk_ids)

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump(self, fh, protocol=pickle.HIGHEST_PROTOCOL)

    @classmethod
    def load(cls, path: str) -> "BM25Index":
        with open(path, "rb") as fh:
            return pickle.load(fh)
