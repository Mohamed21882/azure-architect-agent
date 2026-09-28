"""Azure tenant context — live, READ-ONLY grounding from the user's subscription.

Subscriptions and resource groups come from the Azure MCP Server (Docker, stdio,
--read-only, explicit --tool allowlist). The azure-mcp image (3.0.0-beta.47) has
no network namespace, so VNets/subnets come from a single ARM REST GET using the
same Reader Service Principal. Nothing in this module can write to Azure: the MCP
server is started read-only with only list tools exposed, the ARM helper issues
GET requests only, and the SP holds the Reader role.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field

import requests

from brain.config import CONFIG

logger = logging.getLogger(__name__)

_CACHE_TTL = 600        # 10 minutes for successful fetches
_ERROR_CACHE_TTL = 60   # don't hammer Docker/Azure when something is broken
_ARM = "https://management.azure.com"
_NETWORK_API = "2024-05-01"

# Discovered via `server-binary <ns> --learn` — only read tools, nothing else is exposed.
_MCP_TOOLS = ("subscription_list", "group_list")

_REQUIRED_VARS = (
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_SUBSCRIPTION_ID",
)

_lock = threading.Lock()
_cache: tuple[float, "TenantContext"] | None = None


@dataclass
class Subnet:
    name: str
    prefixes: list[str]


@dataclass
class VNet:
    name: str
    resource_group: str
    location: str
    address_prefixes: list[str]
    subnets: list[Subnet] = field(default_factory=list)


@dataclass
class TenantContext:
    subscription_name: str = ""
    subscription_id: str = ""
    resource_groups: list[dict] = field(default_factory=list)  # {"name", "location"}
    vnets: list[VNet] = field(default_factory=list)
    fetched_at: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(CONFIG.wiki_root, ".env"), override=False)
    except Exception:
        pass


def get_tenant_context() -> TenantContext:
    """Fetch read-only tenant context. Never raises — sets `error` on failure."""
    global _cache
    try:
        _load_env()
        if not all(os.environ.get(v, "").strip() for v in _REQUIRED_VARS):
            return TenantContext(fetched_at=time.time(), error="Azure credentials not configured")

        with _lock:
            now = time.time()
            if _cache is not None:
                ts, ctx = _cache
                ttl = _ERROR_CACHE_TTL if ctx.error else _CACHE_TTL
                if now - ts < ttl:
                    return ctx

            try:
                ctx = asyncio.run(
                    asyncio.wait_for(_fetch(), timeout=CONFIG.azure_mcp_timeout)
                )
            except asyncio.TimeoutError:
                ctx = TenantContext(error=f"timed out after {CONFIG.azure_mcp_timeout}s")
            except Exception as exc:
                ctx = TenantContext(error=_describe(exc))

            ctx.fetched_at = time.time()
            _cache = (ctx.fetched_at, ctx)
            return ctx
    except Exception as exc:  # belt and braces — this function must never raise
        return TenantContext(fetched_at=time.time(), error=_describe(exc))


def _describe(exc: BaseException) -> str:
    # Unwrap anyio/TaskGroup ExceptionGroups so the UI shows the real cause
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    msg = str(exc).strip() or type(exc).__name__
    return msg[:200]


async def _fetch() -> TenantContext:
    sub_id = os.environ["AZURE_SUBSCRIPTION_ID"].strip()
    mcp_task = asyncio.create_task(_fetch_mcp(sub_id))
    vnet_task = asyncio.create_task(asyncio.to_thread(_fetch_vnets, sub_id))
    try:
        sub_name, groups = await mcp_task
        vnets = await vnet_task
    finally:
        for t in (mcp_task, vnet_task):
            if not t.done():
                t.cancel()
    return TenantContext(
        subscription_name=sub_name,
        subscription_id=sub_id,
        resource_groups=groups,
        vnets=vnets,
    )


async def _fetch_mcp(sub_id: str) -> tuple[str, list[dict]]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    args = ["run", "--rm", "-i"]
    for v in _REQUIRED_VARS:
        args += ["-e", v]  # docker copies values from our env — secrets never hit argv
    args += [CONFIG.azure_mcp_image, "--read-only"]
    for tool in _MCP_TOOLS:
        args += ["--tool", tool]

    env = {k: os.environ[k] for k in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG") if k in os.environ}
    env.update({v: os.environ[v].strip() for v in _REQUIRED_VARS})

    params = StdioServerParameters(command="docker", args=args, env=env)
    with open(os.devnull, "w") as devnull:
        async with stdio_client(params, errlog=devnull) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                subs = _tool_json(await session.call_tool("subscription_list", {}))
                groups = _tool_json(
                    await session.call_tool("group_list", {"subscription": sub_id})
                )

    sub_name = ""
    for s in (subs.get("results") or {}).get("subscriptions") or []:
        if str(s.get("subscriptionId", "")).lower() == sub_id.lower():
            sub_name = str(s.get("displayName", ""))
            break

    rgs = [
        {"name": str(g.get("name", "")), "location": str(g.get("location", ""))}
        for g in (groups.get("results") or {}).get("groups") or []
    ]
    return sub_name, sorted(rgs, key=lambda g: g["name"].lower())


def _tool_json(result: object) -> dict:
    if getattr(result, "isError", False):
        raise RuntimeError(_first_text(result)[:200] or "Azure MCP tool error")
    text = _first_text(result)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        raise RuntimeError(f"Azure MCP returned non-JSON: {text[:120]}")
    status = data.get("status")
    if status is not None and status != 200:
        raise RuntimeError(f"Azure MCP status {status}: {data.get('message', '')}"[:200])
    return data


def _first_text(result: object) -> str:
    content = getattr(result, "content", None) or []
    return (getattr(content[0], "text", "") or "") if content else ""


def _fetch_vnets(sub_id: str) -> list[VNet]:
    """List VNets + subnets via ARM. GET only — this helper never writes."""
    token = _arm_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = (
        f"{_ARM}/subscriptions/{sub_id}/providers/Microsoft.Network/"
        f"virtualNetworks?api-version={_NETWORK_API}"
    )
    vnets: list[VNet] = []
    pages = 0
    while url and pages < 20:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            raise RuntimeError(f"ARM VNet list HTTP {resp.status_code}")
        body = resp.json()
        for v in body.get("value", []):
            props = v.get("properties") or {}
            subnets = []
            for s in props.get("subnets") or []:
                sp = s.get("properties") or {}
                prefixes = sp.get("addressPrefixes") or (
                    [sp["addressPrefix"]] if sp.get("addressPrefix") else []
                )
                subnets.append(Subnet(name=str(s.get("name", "")), prefixes=list(prefixes)))
            vnets.append(VNet(
                name=str(v.get("name", "")),
                resource_group=_rg_from_id(v.get("id", "")),
                location=str(v.get("location", "")),
                address_prefixes=list((props.get("addressSpace") or {}).get("addressPrefixes") or []),
                subnets=subnets,
            ))
        url = body.get("nextLink")
        pages += 1
    return sorted(vnets, key=lambda v: (v.resource_group.lower(), v.name.lower()))


def _arm_token() -> str:
    tenant = os.environ["AZURE_TENANT_ID"].strip()
    resp = requests.post(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={
            "grant_type":    "client_credentials",
            "client_id":     os.environ["AZURE_CLIENT_ID"].strip(),
            "client_secret": os.environ["AZURE_CLIENT_SECRET"].strip(),
            "scope":         f"{_ARM}/.default",
        },
        timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Entra token request failed (HTTP {resp.status_code})")
    return resp.json()["access_token"]


def _rg_from_id(resource_id: str) -> str:
    parts = resource_id.split("/")
    for i, p in enumerate(parts):
        if p.lower() == "resourcegroups" and i + 1 < len(parts):
            return parts[i + 1]
    return ""


def _clean(name: str) -> str:
    # Resource names are untrusted data headed for an LLM prompt
    return " ".join(str(name).split())[:80]


def used_address_ranges(ctx: TenantContext) -> list[tuple[str, str]]:
    """(cidr, owner) for every VNet address space, collapsed and sorted."""
    out: list[tuple[str, str]] = []
    for v in ctx.vnets:
        for p in v.address_prefixes:
            out.append((p, f"{_clean(v.name)} ({_clean(v.resource_group)})"))

    def _key(item: tuple[str, str]):
        try:
            net = ipaddress.ip_network(item[0], strict=False)
            return (net.version, int(net.network_address), net.prefixlen)
        except ValueError:
            return (9, 0, 0)

    return sorted(out, key=_key)


def format_for_prompt(ctx: TenantContext) -> str:
    """Compact text summary of the tenant for LLM prompts."""
    if ctx.error:
        return f"Tenant context unavailable: {ctx.error}"

    lines = [
        f"Subscription: {_clean(ctx.subscription_name) or '(unnamed)'} ({ctx.subscription_id})",
        f"Fetched: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(ctx.fetched_at))}",
        "",
        f"Resource groups ({len(ctx.resource_groups)}):",
    ]
    if ctx.resource_groups:
        lines += [f"- {_clean(g['name'])} — {g['location']}" for g in ctx.resource_groups]
    else:
        lines.append("- (none)")

    lines += ["", f"Virtual networks ({len(ctx.vnets)}):"]
    if ctx.vnets:
        for v in ctx.vnets:
            lines.append(
                f"- {_clean(v.name)} [rg: {_clean(v.resource_group)}, {v.location}] "
                f"address space: {', '.join(v.address_prefixes) or 'n/a'}"
            )
            for s in v.subnets:
                lines.append(f"    - subnet {_clean(s.name)}: {', '.join(s.prefixes) or 'n/a'}")
    else:
        lines.append("- (none)")

    ranges = used_address_ranges(ctx)
    lines += ["", "Address ranges already in use (do NOT overlap):"]
    if ranges:
        lines += [f"- {cidr} — {owner}" for cidr, owner in ranges]
    else:
        lines.append("- (none — no VNets exist in this subscription)")
    return "\n".join(lines)
