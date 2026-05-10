"""crystalliser.py — convert an approved architecture session into a searchable wiki page.

Usage:
    from brain.wiki.crystalliser import crystallise_session

    path = crystallise_session({
        "description": "AKS private cluster with WAF and Key Vault",
        "form_values": {"region": "East US", "compliance": "PCI-DSS", ...},
        "messages": [...],                  # full LLM conversation, Bicep stripped
        "retrieved_chunks": [...],          # list of dicts from hybrid_search()
        "architecture_summary": "...",      # text extracted from last assistant message
    })
    # path → "wiki/semantic/2026-05-10-aks-private-cluster-with-waf.md"
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re

from brain.config import CONFIG, BrainConfig
from brain.ingest.chunker import chunk_document
from brain.ingest.embedder import embed_chunks
from brain.models import IngestSource, RawDocument
from brain.search.bm25_index import BM25Index
from brain.store.vector_store import get_client, upsert_chunks

_WIKI_SEMANTIC_DIR = os.path.join(CONFIG.wiki_root, "wiki", "semantic")


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:60].rstrip("-")


def _derive_title(description: str) -> str:
    words = description.split()[:8]
    return " ".join(words).rstrip(".,;:").title()


def _extract_key_decisions(messages: list[dict]) -> list[str]:
    """Pull up to 5 bullet-point decisions from the assistant messages."""
    decisions: list[str] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for line in msg.get("content", "").split("\n"):
            line = line.strip()
            if line.startswith(("- ", "* ", "• ")) and len(line) > 20:
                decisions.append(line.lstrip("-*• "))
                if len(decisions) >= 5:
                    return decisions
    return decisions or ["Architecture follows Azure Well-Architected Framework principles."]


def crystallise_session(
    session: dict,
    config: BrainConfig = CONFIG,
) -> str:
    """Generate a structured wiki page from an approved architecture session.

    Args:
        session: Dict with keys: description, form_values, messages,
                 retrieved_chunks, architecture_summary.
        config:  BrainConfig instance (defaults to project CONFIG).

    Returns:
        Absolute path of the written wiki page.
    """
    description: str = session.get("description", "Azure Architecture")
    form_values: dict = session.get("form_values", {})
    messages: list[dict] = session.get("messages", [])
    retrieved_chunks: list[dict] = session.get("retrieved_chunks", [])
    architecture_summary: str = session.get("architecture_summary", "")

    now = datetime.datetime.utcnow()
    date_str = now.date().isoformat()
    timestamp = now.isoformat()

    slug = _slugify(description)
    filename = f"{date_str}-{slug}.md"
    output_path = os.path.join(_WIKI_SEMANTIC_DIR, filename)

    region = form_values.get("region", "")
    compliance = form_values.get("compliance", "")
    budget = form_values.get("budget", "")
    constraints = form_values.get("constraints", "")

    source_chunk_ids = [
        r["chunk_id"] for r in retrieved_chunks if r.get("chunk_id")
    ]
    entity_types = sorted({
        et
        for r in retrieved_chunks
        for et in (r.get("entity_types") or [])
        if isinstance(et, str)
    })
    tags = [t for t in [region, compliance] + slug.replace("-", " ").split() if t]

    key_decisions = _extract_key_decisions(messages)

    source_rows = "\n".join(
        f"| {r.get('title', 'Unknown')[:60]} | {r.get('source_repo', '')} | {r.get('rrf_score', 0.0):.3f} |"
        for r in retrieved_chunks[:10]
    ) or "| No sources retrieved | | |"

    brief_lines = [description]
    if budget:
        brief_lines.append(f"Budget: {budget}")
    if constraints:
        brief_lines.append(f"Constraints: {constraints}")

    decisions_md = "\n".join(f"- {d}" for d in key_decisions)

    page_content = f"""---
title: {_derive_title(description)}
date: {timestamp}
region: {region}
compliance: {compliance}
confidence: 0.85
source_chunks: {source_chunk_ids}
entity_types: {entity_types}
tags: {tags}
---

## Brief

{chr(10).join(brief_lines)}

## Architecture

{architecture_summary or "See conversation history for full architecture details."}

## Key Design Decisions

{decisions_md}

## Source Knowledge

| Title | Repo | Score |
|-------|------|-------|
{source_rows}
"""

    os.makedirs(_WIKI_SEMANTIC_DIR, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(page_content)

    _ingest_wiki_page(output_path, config)

    return output_path


def _ingest_wiki_page(file_path: str, config: BrainConfig) -> None:
    """Chunk, embed, and upsert a crystallised page so it's immediately searchable."""
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            content = fh.read()

        doc = RawDocument(
            doc_id=hashlib.sha1(file_path.encode()).hexdigest(),
            content=content,
            source=IngestSource.LOCAL,
            source_repo="wiki-semantic",
            file_path=file_path,
            title=os.path.basename(file_path).replace(".md", "").replace("-", " ").title(),
        )

        chunks = chunk_document(doc, chunk_size=300, overlap=50, min_words=20)
        if not chunks:
            return

        embed_chunks(chunks, config)

        qdrant = get_client(config)
        upsert_chunks(qdrant, chunks, config)

        bm25_path = config.bm25_index_path
        if os.path.exists(bm25_path):
            bm25 = BM25Index.load(bm25_path)
            bm25.add(chunks)
            bm25.build()
            bm25.save(bm25_path)

    except Exception:
        pass  # ingest failure must not block wiki page save
