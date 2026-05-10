from __future__ import annotations

import re
import uuid

from brain.models import Chunk, RawDocument

# Matches H1 / H2 / H3 headings at the start of a line
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)", re.MULTILINE)
_TITLE_RE = re.compile(r"^#\s+(.+)", re.MULTILINE)


def _word_count(text: str) -> int:
    return len(text.split())


def _extract_title(content: str) -> str:
    m = _TITLE_RE.search(content)
    return m.group(1).strip() if m else ""


def _split_into_sections(content: str) -> list[tuple[str, str]]:
    """Return (heading_text, body_text) pairs split on H1-H3 boundaries."""
    sections: list[tuple[str, str]] = []
    pos = 0
    current_heading = ""

    for match in _HEADING_RE.finditer(content):
        body = content[pos : match.start()].strip()
        if body or current_heading:
            sections.append((current_heading, body))
        current_heading = match.group(2).strip()
        pos = match.end()

    # Trailing text after the last heading
    tail = content[pos:].strip()
    if tail or current_heading:
        sections.append((current_heading, tail))

    return sections or [("", content)]


def _sliding_window(heading: str, body: str, chunk_size: int, overlap: int) -> list[str]:
    """Slide a word-level window over body, prepending the heading context."""
    words = body.split()
    if not words:
        return []

    prefix = f"# {heading}\n\n" if heading else ""
    chunks: list[str] = []
    start = 0

    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunks.append(prefix + " ".join(words[start:end]))
        if end == len(words):
            break
        start += chunk_size - overlap

    return chunks


def chunk_document(
    doc: RawDocument,
    chunk_size: int = 300,
    overlap: int = 50,
    min_words: int = 20,
) -> list[Chunk]:
    """Split a RawDocument into a list of Chunk objects.

    Strategy:
      1. Split content on H1-H3 headings.
      2. Sections ≤ chunk_size words → single chunk (no split).
      3. Sections > chunk_size words → sliding-window sub-chunks with overlap.
      4. Discard fragments below min_words.
    """
    sections = _split_into_sections(doc.content)
    raw_texts: list[str] = []

    for heading, body in sections:
        wc = _word_count(body)
        if wc == 0:
            continue
        if wc <= chunk_size:
            prefix = f"# {heading}\n\n" if heading else ""
            raw_texts.append(prefix + body)
        else:
            raw_texts.extend(_sliding_window(heading, body, chunk_size, overlap))

    raw_texts = [t for t in raw_texts if _word_count(t) >= min_words]

    # Resolve title: prefer explicit, then first H1, then filename stem
    title = (
        doc.title
        or _extract_title(doc.content)
        or doc.file_path.rsplit("/", 1)[-1].replace("-", " ").replace(".md", "").title()
    )

    chunks: list[Chunk] = []
    for i, text in enumerate(raw_texts):
        chunk_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc.doc_id}#{i}"))
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                doc_id=doc.doc_id,
                content=text,
                source=doc.source,
                source_repo=doc.source_repo,
                file_path=doc.file_path,
                title=title,
                chunk_index=i,
                token_count=_word_count(text),
                metadata={**doc.metadata},
            )
        )

    return chunks
