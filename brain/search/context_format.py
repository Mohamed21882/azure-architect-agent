"""Render retrieved Brain chunks for the architecture LLM prompt."""
from __future__ import annotations

from brain.models import SearchResult

BRAIN_HEADING = "## Architecture Knowledge (TE-1 Brain — curated, confidence-scored)"
LEGACY_HEADING = "## Legacy reference — hub-based Azure AI Foundry (existing deployments only)"
LEGACY_NOTE = (
    "The chunks below describe the OLDER hub-based Foundry model (Azure AI Foundry hubs and "
    "hub projects, documented under foundry-classic). Use them only to reason about "
    "existing hub-based deployments. NEVER use them as the basis for a new design — new "
    "designs use the current Foundry resource and project model from the section above."
)
CRYSTALLISED_LABEL = "crystallised TE-1 session (previously approved design, not Microsoft documentation)"


def is_legacy(hit: SearchResult) -> bool:
    return bool(hit.chunk.metadata.get("legacy"))


def _entry(i: int, h: SearchResult) -> str:
    content = h.chunk.content[:800].replace("\n", " ").strip()
    stale_note = " [STALE]" if h.is_stale else ""
    source = h.chunk.source_repo
    if source == "crystallised":
        source = f"{source} — {CRYSTALLISED_LABEL}"
    return (
        f"[{i}] Title:  {h.chunk.title or 'Untitled'}{stale_note}\n"
        f"    Source: {source}\n"
        f"    Score:  {h.fused_score:.5f}\n"
        f"    Text:   {content}\n"
    )


def format_brain_section(local_hits: list[SearchResult], brain_err: str = "") -> str:
    if brain_err:
        return f"{BRAIN_HEADING}\n{brain_err}"
    current = [h for h in local_hits if not is_legacy(h)]
    legacy = [h for h in local_hits if is_legacy(h)]
    lines = [f"{BRAIN_HEADING}\n"]
    lines += [_entry(i, h) for i, h in enumerate(current, 1)]
    if legacy:
        lines.append(f"\n{LEGACY_HEADING}\n{LEGACY_NOTE}\n")
        lines += [_entry(i, h) for i, h in enumerate(legacy, len(current) + 1)]
    lines.append("--- END CONTEXT ---\n")
    return "\n".join(lines)
