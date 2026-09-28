"""crystalliser.py — convert an approved architecture session into a searchable wiki page.

Usage:
    from brain.wiki.crystalliser import crystallise_session

    result = crystallise_session({
        "approved": True,                   # required — only approved sessions crystallise
        "description": "AKS private cluster with WAF and Key Vault",
        "form_values": {"region": "East US", "compliance": "PCI-DSS", ...},
        "messages": [...],                  # full LLM conversation, Bicep stripped
        "retrieved_chunks": [...],          # list of dicts from hybrid_search()
        "architecture_summary": "...",      # text extracted from last assistant message
    })
    # result.path → ".../wiki/semantic/2026-05-10-aks-private-cluster-with-waf.md"
    # result.indexed / result.warning → whether it became searchable, and why not

Pages are indexed under source "crystallised". A page is NOT indexed (but is still written,
with `index: false` in its frontmatter) when the scorer's Azure OpenAI region check flags
it — a wrong design must never be fed back into the Brain. Full rebuilds apply the same
check via page_block_reason().
"""

from __future__ import annotations

import datetime
import fcntl
import hashlib
import logging
import os
import re
from dataclasses import dataclass

from brain.config import CONFIG, BrainConfig
from brain.ingest.chunker import chunk_document
from brain.ingest.embedder import embed_chunks
from brain.models import IngestSource, RawDocument
from brain.search.bm25_index import BM25Index
from brain.store.vector_store import get_client, upsert_chunks

_WIKI_SEMANTIC_DIR = os.path.join(CONFIG.wiki_root, "wiki", "semantic")
SOURCE = "crystallised"

logger = logging.getLogger(__name__)


@dataclass
class CrystalliseResult:
    path: str
    indexed: bool
    warning: str = ""


_FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _frontmatter(text: str) -> dict[str, str]:
    m = _FM.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "-")):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip("'\"")
    return out


def region_block_reason(architecture_text: str, region: str) -> str:
    """The scorer's Azure OpenAI region check. Returns why the page must not be indexed,
    or "" when it passes."""
    from brain.eval.auto_scorer import openai_region_verdict
    verdict = openai_region_verdict(architecture_text, {"region": region})
    if verdict == "qatar":
        return ("region check failed (critical): the design places Azure OpenAI in Qatar "
                "Central, where no Azure OpenAI models can be deployed")
    if verdict == "uae_no_note":
        return ("region check failed (medium): the design calls Azure OpenAI in UAE North "
                "without stating that prompts and responses leave Qatar")
    return ""


def crystallisation_block_reason(architecture_text: str, region: str,
                                 flags: list | None = None) -> str:
    """Why an approved design must stay out of the Brain ("" = index it).

    Blocks on the region check run on the final text, on every critical scorer flag and on
    the Azure OpenAI data-flow flag. Other medium/low flags (budget risk etc.) never block."""
    from brain.eval.auto_scorer import blocks_crystallisation
    reasons = []
    region_reason = region_block_reason(architecture_text, region)
    if region_reason:
        reasons.append(region_reason)
    for f in flags or []:
        if not blocks_crystallisation(f) or (region_reason and f.get("rule")):
            continue  # rule flags duplicate the region check just run on the same text
        msg = " ".join(str(f.get("message", "")).split())[:200]
        reasons.append(f"{f.get('severity')} flag ({f.get('category', 'unknown')}): {msg}")
    return "; ".join(dict.fromkeys(reasons))


def page_block_reason(file_path: str) -> str:
    """Why a crystallised page must not be indexed ("" = index it). Used at crystallisation
    time and by full rebuilds, so a page blocked once can never slip in later."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError as exc:
        return f"unreadable: {exc}"
    fm = _frontmatter(text)
    if fm.get("index", "").lower() == "false":
        return fm.get("index_blocked") or "marked index: false"
    return region_block_reason(_FM.sub("", text, count=1), fm.get("region", ""))


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
) -> CrystalliseResult:
    """Generate a structured wiki page from an APPROVED architecture session and index it.

    Args:
        session: Dict with keys: approved (must be True), description, form_values,
                 messages, retrieved_chunks, architecture_summary, and optionally flags
                 (scorer flags for the FINAL design; see crystallisation_block_reason).
        config:  BrainConfig instance (defaults to project CONFIG).

    Raises:
        ValueError if the session is not approved.
    """
    if session.get("approved") is not True:
        raise ValueError("only approved sessions can be crystallised")
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

    block = crystallisation_block_reason(architecture_summary, region, session.get("flags"))
    index_fm = f"index: false\nindex_blocked: {' '.join(block.split())}\n" if block else ""

    page_content = f"""---
title: {_derive_title(description)}
date: {timestamp}
region: {region}
compliance: {compliance}
confidence: 0.85
source_chunks: {source_chunk_ids}
entity_types: {entity_types}
tags: {tags}
{index_fm}---

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

    if block:
        logger.warning("Crystallised page not indexed (%s): %s", output_path, block)
        return CrystalliseResult(output_path, False, f"Not added to the Brain — {block}.")

    warning = _ingest_wiki_page(output_path, config)
    return CrystalliseResult(output_path, not warning, warning)


def _ingest_wiki_page(file_path: str, config: BrainConfig) -> str:
    """Chunk, embed and index a crystallised page. Returns "" on success, else a
    user-facing warning. Never raises — a failed index must not lose the page."""
    from brain.kb.status import LOCK_PATH
    try:
        os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
        lock = open(LOCK_PATH, "a+")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return ("Page saved but not indexed yet: a knowledge base update is running. "
                    "It will be included in the next full rebuild.")
        try:
            return _index_page(file_path, config)
        finally:
            lock.close()
    except Exception as exc:
        logger.exception("Crystallised page indexing failed for %s", file_path)
        return f"Page saved but indexing failed: {type(exc).__name__}: {exc}"[:300]


def _index_page(file_path: str, config: BrainConfig) -> str:
    with open(file_path, "r", encoding="utf-8") as fh:
        content = fh.read()
    content = _FM.sub("", content, count=1).lstrip()

    doc = RawDocument(
        doc_id=hashlib.sha1(file_path.encode()).hexdigest(),
        content=content,
        source=IngestSource.LOCAL,
        source_repo=SOURCE,
        file_path=file_path,
        title=os.path.basename(file_path).replace(".md", "").replace("-", " ").title(),
        metadata={"source_type": "crystallised_session"},
    )
    chunks = chunk_document(doc, chunk_size=config.chunk_size_words,
                            overlap=config.chunk_overlap_words,
                            min_words=config.min_chunk_words)
    if not chunks:
        return "Page saved but produced no indexable text."

    embed_chunks(chunks, config)
    embedded = [c for c in chunks if c.embedding is not None]
    if embedded:
        # Only embedded chunks: upsert_chunks treats embedding=None as a payload update of
        # an existing point, which 404s for a brand-new page
        upsert_chunks(get_client(config), embedded, config)

    if os.path.exists(config.bm25_index_path):
        bm25 = BM25Index.load(config.bm25_index_path)
        bm25.remove_files({file_path})
        bm25.add(chunks)
        bm25.build()
        bm25.save(config.bm25_index_path)

    missing = len(chunks) - len(embedded)
    if missing:
        return (f"Page indexed for keyword search, but {missing} of {len(chunks)} chunks "
                "could not be embedded (Ollama busy?).")
    return ""
