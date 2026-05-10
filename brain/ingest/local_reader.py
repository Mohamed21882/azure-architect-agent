"""local_reader.py — walk the 4 Microsoft raw-source directories and yield RawDocument objects.

Sources configured in BrainConfig.source_dirs:
  architecture-center  raw/architecture-center          (all .md recursively)
  azure-ai             raw/azure-ai                     (all .md recursively)
  azure-foundry        raw/azure-foundry/articles/foundry  (Foundry-specific only)
  cli                  raw/cli                          (all .md recursively)

All documents are tagged with IngestSource.MICROSOFT_LEARN.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Iterator

from brain.config import BrainConfig
from brain.models import IngestSource, RawDocument

_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_TITLE_RE = re.compile(r"^#\s+(.+)", re.MULTILINE)

# Files that are redirect manifests, TOC metadata, or otherwise not prose
_SKIP_FILENAMES = {
    "toc.yml", "TOC.yml", "breadcrumb.yml",
    ".openpublishing.redirection.json",
}


def _doc_id(file_path: str) -> str:
    """Stable, collision-resistant ID derived from the absolute file path."""
    return hashlib.sha1(file_path.encode()).hexdigest()


def _strip_frontmatter(content: str) -> str:
    """Remove YAML front-matter block (--- ... ---) if present."""
    return _FRONTMATTER_RE.sub("", content, count=1).lstrip()


def _extract_title(content: str) -> str:
    m = _TITLE_RE.search(content)
    return m.group(1).strip() if m else ""


def walk_source(source_repo: str, root_dir: str) -> Iterator[RawDocument]:
    """Recursively yield one RawDocument per .md file under root_dir.

    Skips:
      - Non-existent or empty directories.
      - Files in _SKIP_FILENAMES.
      - Files whose cleaned content is shorter than 50 characters (stubs /
        redirect manifests masquerading as .md).
    """
    if not os.path.isdir(root_dir):
        return

    for dirpath, _dirs, filenames in os.walk(root_dir):
        for fname in sorted(filenames):
            if not fname.endswith(".md"):
                continue
            if fname in _SKIP_FILENAMES:
                continue

            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                    raw = fh.read()
            except OSError:
                continue

            content = _strip_frontmatter(raw)
            if len(content.strip()) < 50:
                continue

            fallback_title = fname[:-3].replace("-", " ").title()
            title = _extract_title(content) or fallback_title

            yield RawDocument(
                doc_id=_doc_id(fpath),
                content=content,
                source=IngestSource.MICROSOFT_LEARN,
                source_repo=source_repo,
                file_path=fpath,
                title=title,
                metadata={
                    "repo":     source_repo,
                    "filename": fname,
                    # Relative path within the repo root — useful for display
                    "rel_path": os.path.relpath(fpath, root_dir),
                },
            )


def read_all_sources(
    config: BrainConfig,
) -> Iterator[tuple[str, RawDocument]]:
    """Yield (source_repo_name, RawDocument) for every .md file across all 4 sources."""
    for repo_name, root_dir in config.source_dirs.items():
        for doc in walk_source(repo_name, root_dir):
            yield repo_name, doc
