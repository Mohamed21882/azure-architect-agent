"""Microsoft Learn MCP Server — live retrieval alongside TE-1 Brain."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

logger = logging.getLogger(__name__)

_MCP_URL = "https://learn.microsoft.com/api/mcp"
_CACHE_TTL = 3600  # 1 hour

_cache: dict[str, tuple[float, list[dict]]] = {}


def query_learn_mcp(query: str, top_k: int = 3) -> list[dict]:
    """Query Microsoft Learn MCP Server. Returns [] on any error — never raises."""
    now = time.time()
    cached = _cache.get(query)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    try:
        results = asyncio.run(asyncio.wait_for(_fetch(query, top_k), timeout=5.0))
    except Exception as exc:
        logger.debug("Learn MCP unavailable: %s", exc)
        results = []

    _cache[query] = (time.time(), results)
    return results


async def _fetch(query: str, top_k: int) -> list[dict]:
    from mcp.client.streamable_http import streamablehttp_client
    from mcp import ClientSession

    async with streamablehttp_client(_MCP_URL, timeout=4.5) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "microsoft_docs_search", {"query": query}
            )
            return _parse(result, top_k)


def _parse(result: object, top_k: int) -> list[dict]:
    out: list[dict] = []
    for item in getattr(result, "content", []):
        text = getattr(item, "text", "") or ""
        if not text.strip():
            continue

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            data = None

        if isinstance(data, dict):
            # Microsoft Learn format: {"results": [{title, content, contentUrl}, ...]}
            entries = data.get("results") if isinstance(data.get("results"), list) else [data]
            for entry in entries:
                if isinstance(entry, dict):
                    out.append(_entry_to_dict(entry))
                    if len(out) >= top_k:
                        return out
            continue

        if isinstance(data, list):
            for entry in data:
                if isinstance(entry, dict):
                    out.append(_entry_to_dict(entry))
                    if len(out) >= top_k:
                        return out
            continue

        # Fallback: extract URL and title from unstructured text
        url_match = re.search(r'https?://\S+', text)
        url = url_match.group(0).rstrip(').,') if url_match else ""
        title_match = re.search(r'^#+\s*(.+)$', text, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else "Microsoft Learn"
        out.append({
            "title": title,
            "url": url,
            "snippet": text[:500].strip(),
            "source": "microsoft_learn_live",
        })
        if len(out) >= top_k:
            break

    return out[:top_k]


def _entry_to_dict(entry: dict) -> dict:
    # Microsoft Learn MCP uses contentUrl; fall back to url/href
    url = entry.get("contentUrl", entry.get("url", entry.get("href", "")))
    snippet = entry.get("content", entry.get("excerpt", entry.get("snippet", "")))
    return {
        "title": str(entry.get("title", "Microsoft Learn")),
        "url": str(url),
        "snippet": str(snippet),
        "source": "microsoft_learn_live",
    }
