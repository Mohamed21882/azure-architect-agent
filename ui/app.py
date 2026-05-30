import sys
import os
import re
import time
import json
import uuid
import concurrent.futures
import requests
import streamlit as st
import streamlit.components.v1 as components

# Brain package lives one level up from ui/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain.config import CONFIG
from brain.models import SearchResult
from brain.search.bm25_index import BM25Index
from brain.search.hybrid_search import hybrid_search
from brain.search.learn_mcp import query_learn_mcp
from brain.db.database import (
    init_db,
    create_user,
    authenticate_user,
    create_session_token,
    validate_session,
    delete_session,
    save_architecture,
    get_user_architectures,
    load_architecture,
    delete_architecture,
    save_evaluation,
    update_chunk_feedback_log,
)

st.set_page_config(page_title="TE-1: Azure Architect Portal", layout="wide")

st.markdown("""
<style>
    .stButton>button[kind="primary"],
    .stButton>button:not([kind]) {
        background-color: #0078d4; color: white;
        border-radius: 4px; border: none;
    }
    .stButton>button[kind="secondary"] {
        background-color: transparent; color: #0078d4;
        border: 1.5px solid #0078d4; border-radius: 4px;
    }
    .stButton>button[kind="secondary"]:hover {
        background-color: #e6f2fb; color: #005a9e; border-color: #005a9e;
    }
    [data-testid="stStatusWidget"] { display: none !important; }
    .chunk-card {
        background: #1e1e1e; border-left: 3px solid #0078d4;
        padding: 8px 12px; margin-bottom: 8px;
        border-radius: 0 4px 4px 0; font-size: 0.82em;
    }
    .chunk-score { color: #50e6ff; font-weight: 600; }
    .chunk-repo  { color: #a0a0a0; }
    .stale-badge { color: #ffa500; font-size: 0.78em; font-weight: 600; margin-left: 6px; }
    .guest-banner {
        background: #1a1a2e; border: 1px solid #444; border-radius: 6px;
        padding: 8px 14px; margin-bottom: 12px; font-size: 0.88em;
        display: flex; align-items: center; gap: 12px;
    }
</style>
""", unsafe_allow_html=True)

# ── Constants ──────────────────────────────────────────────────────────────

OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"

SYSTEM_ARCH = (
    "You are TE-1, a Lead Azure Cloud & AI Architect Agent operating under an "
    "Agent-as-a-Service (AaaS) model.\n\n"
    "When given an Azure infrastructure brief, respond with:\n"
    "1. A comprehensive Architecture Summary that includes: component overview, "
    "network topology, a detailed Security Best Practices bulleted list, "
    "compliance considerations, and cost optimisation notes.\n"
    "2. An architecture diagram in a ```mermaid block.\n\n"
    "Mermaid diagram rules — follow EXACTLY:\n"
    "- Use 'graph TB' as the layout directive (top-to-bottom).\n"
    "- Use double quotes for ALL node labels (e.g. A[\"Hub VNet\"]).\n"
    "- Node labels must be concise: MAXIMUM 3 words each. Abbreviate if needed.\n"
    "- Begin every diagram with these exact classDef lines (copy verbatim):\n"
    "    classDef network  fill:#1a3a5c,stroke:#4a9ede,color:#ffffff\n"
    "    classDef security fill:#1a2a1a,stroke:#4ade80,color:#ffffff\n"
    "    classDef compute  fill:#2a1a3a,stroke:#a78bfa,color:#ffffff\n"
    "    classDef storage  fill:#3a2a1a,stroke:#fb923c,color:#ffffff\n"
    "    classDef monitor  fill:#2a2a1a,stroke:#facc15,color:#ffffff\n"
    "    classDef dns      fill:#1a2a3a,stroke:#67e8f9,color:#ffffff\n"
    "- Apply a class to EVERY node using the :::className suffix "
    "(e.g. A[\"Hub VNet\"]:::network). No node may be left unclassed.\n"
    "- Category mapping — use the FIRST matching category:\n"
    "    network  → VNet, Subnet, NSG, Firewall, Load Balancer, Gateway, ExpressRoute\n"
    "    security → Azure AD, Entra ID, Key Vault, Private Endpoint, RBAC, Defender, Bastion\n"
    "    compute  → AKS, VM, App Service, Function App, Container, ACI, APIM\n"
    "    storage  → Storage Account, SQL, Cosmos DB, Redis, Data Lake, AI Search, ACR\n"
    "    monitor  → Log Analytics, App Insights, Azure Monitor, Alerts, Sentinel\n"
    "    dns      → Private DNS Zone, DNS Resolver, Traffic Manager, Front Door\n\n"
    "Do NOT generate Bicep or any infrastructure code. "
    "The user will request Bicep separately via the Approve button.\n\n"
    "Do NOT output any text-based diagrams, ASCII art, tables, or topology descriptions. "
    "Output ONLY the Mermaid diagram block and the architecture summary text. No exceptions."
)

SYSTEM_BICEP = (
    "You are TE-1, a Lead Azure Cloud & AI Architect Agent.\n"
    "Generate a COMPLETE, production-ready Bicep template for the described architecture.\n"
    "Rules:\n"
    "- Include ALL resources discussed. Do not omit or abbreviate any section.\n"
    "- Do not truncate. If the template is long, keep writing until it is complete.\n"
    "- Output ONLY valid Bicep inside a single ```bicep code block.\n"
    "- Add brief inline comments for non-obvious resource properties."
)

# ── Brain ──────────────────────────────────────────────────────────────────

@st.cache_resource(show_spinner="Loading Brain index…")
def load_bm25() -> BM25Index | None:
    try:
        return BM25Index.load(CONFIG.bm25_index_path)
    except Exception:
        return None


def get_brain_context(
    query: str,
    bm25: BM25Index,
    reinforce: bool = False,
) -> tuple[str, list[SearchResult], list[dict]]:
    local_hits: list[SearchResult] = []
    learn_hits: list[dict] = []
    brain_err = ""

    if bm25 is not None and CONFIG.use_learn_mcp:
        # Thread 1: local Brain, Thread 2: Microsoft Learn MCP — parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            brain_f = ex.submit(
                hybrid_search, query, bm25, CONFIG, top_k=6, reinforce=reinforce
            )
            learn_f = ex.submit(query_learn_mcp, query)
            try:
                local_hits = brain_f.result(timeout=30.0)
            except Exception as exc:
                brain_err = f"[Brain unavailable: {exc}]\n"
            try:
                learn_hits = learn_f.result(timeout=7.0)
            except Exception:
                learn_hits = []

    elif bm25 is not None:
        try:
            local_hits = hybrid_search(query, bm25, CONFIG, top_k=6, reinforce=reinforce)
        except Exception as exc:
            brain_err = f"[Brain unavailable: {exc}]\n"

    elif CONFIG.use_learn_mcp:
        brain_err = "[Brain index unavailable — no retrieval context]\n"
        learn_hits = query_learn_mcp(query)

    else:
        brain_err = "[Brain index unavailable — no retrieval context]\n"

    # Build Brain section for LLM prompt
    if brain_err:
        brain_section = (
            "## Architecture Knowledge (TE-1 Brain — curated, confidence-scored)\n"
            f"{brain_err}"
        )
    else:
        lines = ["## Architecture Knowledge (TE-1 Brain — curated, confidence-scored)\n"]
        for i, h in enumerate(local_hits, 1):
            content = h.chunk.content[:800].replace("\n", " ").strip()
            stale_note = " [STALE]" if h.is_stale else ""
            lines.append(
                f"[{i}] Title:  {h.chunk.title or 'Untitled'}{stale_note}\n"
                f"    Source: {h.chunk.source_repo}\n"
                f"    Score:  {h.fused_score:.5f}\n"
                f"    Text:   {content}\n"
            )
        lines.append("--- END CONTEXT ---\n")
        brain_section = "\n".join(lines)

    # Build Learn MCP section for LLM prompt
    if learn_hits:
        learn_lines = ["\n## Live API Reference (Microsoft Learn — always current)\n"]
        for r in learn_hits:
            learn_lines.append(
                f"Title: {r['title']}\nURL: {r['url']}\n{r['snippet'][:600]}\n"
            )
        learn_section = "\n".join(learn_lines)
    else:
        learn_section = ""

    return brain_section + learn_section, local_hits, learn_hits


# ── LLM ────────────────────────────────────────────────────────────────────

def strip_bicep(text: str) -> str:
    return re.sub(
        r"```bicep.*?(?:```|$)", "[Bicep template omitted]",
        text, flags=re.DOTALL | re.IGNORECASE,
    )


def strip_bicep_from_history(messages: list[dict]) -> list[dict]:
    return [{"role": m["role"], "content": strip_bicep(m["content"])} for m in messages]


def call_llm(
    messages: list[dict],
    system: str,
    engine_mode: str,
    model: str,
    provider: str = "OpenRouter",
    api_key: str = "",
    max_tokens: int = 8192,
    temperature: float = 1.0,
) -> str:
    system_msg    = {"role": "system", "content": system}
    full_messages = [system_msg] + messages

    if engine_mode == "Local (Ollama)":
        num_predict = -1 if max_tokens == -1 else max_tokens
        try:
            res = requests.post(
                OLLAMA_CHAT_URL,
                json={
                    "model": model,
                    "messages": full_messages,
                    "stream": False,
                    "options": {"num_predict": num_predict, "temperature": temperature},
                },
                timeout=600,
            ).json()
            return res.get("message", {}).get("content", "") or "❌ Empty response from Ollama"
        except Exception as exc:
            return f"❌ Ollama error: {exc}"

    api_key = (api_key or "").strip()
    if not api_key:
        return "⚠️ No API key provided. Enter your key in the sidebar."

    try:
        if provider == "OpenRouter":
            mt  = 32768 if max_tokens == -1 else max_tokens
            res = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": "http://localhost:8501",
                    "X-Title": "TE-1 Azure Architect",
                    "Content-Type": "application/json",
                },
                json={"model": model or "anthropic/claude-3.5-sonnet",
                      "max_tokens": mt, "temperature": temperature,
                      "messages": full_messages},
                timeout=600,
            )

        elif provider == "OpenAI":
            mt  = 16384 if max_tokens == -1 else max_tokens
            res = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model or "gpt-4o",
                      "max_tokens": mt, "temperature": temperature,
                      "messages": full_messages},
                timeout=600,
            )

        elif provider == "Claude":
            mt          = 8192 if max_tokens == -1 else max_tokens
            non_system  = [m for m in full_messages if m["role"] != "system"]
            res = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": model or "claude-3-5-haiku-20241022",
                      "max_tokens": mt, "temperature": temperature,
                      "system": system, "messages": non_system},
                timeout=600,
            )

        elif provider == "Gemini":
            gemini_msgs = []
            for m in messages:
                role = "model" if m["role"] == "assistant" else "user"
                gemini_msgs.append({"role": role, "parts": [{"text": m["content"]}]})
            if gemini_msgs:
                gemini_msgs[0]["parts"][0]["text"] = (
                    system + "\n\n" + gemini_msgs[0]["parts"][0]["text"]
                )
            else:
                gemini_msgs = [{"role": "user", "parts": [{"text": system}]}]
            res = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model or 'gemini-1.5-pro'}:generateContent?key={api_key}",
                json={"contents": gemini_msgs,
                      "generationConfig": {"temperature": temperature}},
                timeout=600,
            )

        else:
            return "❌ Unknown provider"

        payload = res.json()
        if res.status_code != 200:
            err = payload.get("error") or payload
            msg = err.get("message", str(payload)) if isinstance(err, dict) else str(err)
            return f"⚠️ {provider} Error {res.status_code}: {msg}"

        if provider in ["OpenRouter", "OpenAI"]:
            return payload["choices"][0]["message"]["content"]
        if provider == "Claude":
            return payload["content"][0]["text"]
        if provider == "Gemini":
            return payload["candidates"][0]["content"]["parts"][0]["text"]

    except Exception as exc:
        return f"❌ System Failure: {exc}"


# ── Rendering ──────────────────────────────────────────────────────────────

def render_mermaid(code: str) -> None:
    code = code.strip()
    if code.startswith("raph"):
        code = "g" + code
    code = re.sub(r"```mermaid\s*", "", code, flags=re.IGNORECASE)
    code = re.sub(r"```", "", code)
    code = code.replace('""', '"')
    code = re.sub(r"\[([^\"'].*?)\]", r'["\1"]', code)

    html = """
    <div id="graph" class="mermaid"
         style="display:flex;justify-content:center;background:transparent;">
        {code}
    </div>
    <style>
        .node text    { fill: white !important; font-family: 'Segoe UI', sans-serif !important; }
        .edgeLabel text { fill: #0078d4 !important; }
        .cluster rect   { fill: #333 !important; stroke: #0078d4 !important; }
    </style>
    <script type="module">
        import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
        mermaid.initialize({ startOnLoad: true, theme: 'dark', securityLevel: 'loose',
            flowchart: { useMaxWidth: true, htmlLabels: true, curve: 'basis' } });
        async function run() {
            try { await mermaid.run(); }
            catch (e) { document.getElementById('graph').innerHTML =
                '<p style="color:red;">Mermaid render failed — check diagram syntax.</p>'; }
        }
        run();
    </script>
    """
    components.html(html.replace("{code}", code), height=800, scrolling=True)


_CLASSDEFS = [
    "classDef network  fill:#1a3a5c,stroke:#4a9ede,color:#fff",
    "classDef security fill:#1a2a1a,stroke:#4ade80,color:#fff",
    "classDef compute  fill:#2a1a3a,stroke:#a78bfa,color:#fff",
    "classDef storage  fill:#3a2a1a,stroke:#fb923c,color:#fff",
    "classDef monitor  fill:#2a2a1a,stroke:#facc15,color:#fff",
    "classDef dns      fill:#1a2a3a,stroke:#67e8f9,color:#fff",
]

_KEYWORD_MAP: list[tuple[str, list[str]]] = [
    ("network",  ["vnet", "subnet", "firewall", "gateway", "nsg", "peering", "hub", "spoke"]),
    ("security", ["bastion", "keyvault", "key vault", "identity", " ad", "rbac",
                  "private endpoint", "pe:"]),
    ("compute",  ["aks", "cluster", "app service", "container", "function", "vm",
                  "agent", "pods"]),
    ("storage",  ["storage", "cosmos", "sql", "database", "redis", "acr", "registry", "search"]),
    ("monitor",  ["log analytics", "insights", "monitor", "diagnostic"]),
    ("dns",      ["dns", "privatelink"]),
]

# Matches node definitions like: ID["label"], ID(["label"]), ID{{"label"}}, ID[/"label"/]
_NODE_RE = re.compile(
    r'(\b[A-Za-z_]\w*\b)'   # node ID
    r'([\[({/]+)'            # opening bracket(s)
    r'("(?:[^"\\]|\\.)*")'  # double-quoted label
    r'([)\]}/\\]*)'          # closing bracket(s)
    r'(?::::\w+)?'           # strip any pre-existing class suffix
)


def _classify_label(label: str) -> str:
    label_lower = label.lower()
    for cls, keywords in _KEYWORD_MAP:
        if any(kw in label_lower for kw in keywords):
            return cls
    return "compute"


def inject_mermaid_styles(diagram: str) -> str:
    """Remove LLM-generated classDef/class lines, inject canonical classDefs,
    and apply :::className to every node based on its label keywords."""
    lines = diagram.splitlines()

    # Strip existing classDef and class-assignment lines
    cleaned: list[str] = []
    for line in lines:
        s = line.strip()
        if re.match(r"classDef\s+", s) or re.match(r"class\s+[\w,\s]+$", s):
            continue
        cleaned.append(line)

    # Insert canonical classDefs right after the graph directive
    result: list[str] = []
    inserted = False
    for line in cleaned:
        result.append(line)
        if not inserted and re.match(r"\s*graph\s+\w+", line, re.IGNORECASE):
            result.extend(_CLASSDEFS)
            inserted = True
    if not inserted:
        result = _CLASSDEFS + result

    # Apply :::class suffix to every node definition line
    final: list[str] = []
    for line in result:
        s = line.strip()
        if (not s
                or s.startswith("%%")
                or s.lower().startswith("subgraph")
                or s == "end"
                or re.match(r"graph\s+\w+", s, re.IGNORECASE)
                or re.match(r"classDef\s+", s)
                or re.match(r"class\s+[\w,\s]+$", s)):
            final.append(line)
            continue

        def _replace(m: re.Match) -> str:
            label = m.group(3).strip('"')
            cls   = _classify_label(label)
            return f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}:::{cls}"

        final.append(_NODE_RE.sub(_replace, line))

    return "\n".join(final)


def render_assistant_message(content: str) -> None:
    mermaid_match = re.search(r"```mermaid\s*(.*?)\s*```", content, re.DOTALL | re.IGNORECASE)
    summary = re.sub(r"```mermaid.*?```", "", content, flags=re.DOTALL | re.IGNORECASE)
    summary = re.sub(r"```bicep.*?(?:```|$)", "", summary, flags=re.DOTALL | re.IGNORECASE)
    summary = summary.strip()
    if summary:
        st.markdown(summary)
    if mermaid_match:
        st.markdown("---")
        st.subheader("📊 Architecture Diagram")
        render_mermaid(inject_mermaid_styles(mermaid_match.group(1)))


# ── Prompt builders ────────────────────────────────────────────────────────

def build_initial_prompt(fv: dict, context_block: str) -> str:
    additional_section = ""
    if fv.get("additional_constraints", "").strip():
        additional_section = (
            f"\n## Additional Constraints\n"
            f"> These are non-negotiable. TE-1 must not deviate from them.\n\n"
            f"{fv['additional_constraints'].strip()}\n"
        )
    return (
        f"## Primary Goal\n\n"
        f"{fv['description'].strip()}\n\n"
        f"## Hard Constraints\n"
        f"> These are non-negotiable architectural constraints. "
        f"TE-1 must not deviate from any of them under any circumstances.\n\n"
        f"| Constraint | Value |\n|---|---|\n"
        f"| Region | {fv['region']} |\n"
        f"| Compliance Framework | {fv['compliance']} |\n"
        f"| Monthly Budget Ceiling | {fv['budget']} |\n"
        f"| Existing Hub VNet | {fv['hub_vnet']} |\n"
        f"{additional_section}\n"
        f"{context_block}\n"
        f"Design a production-ready Azure architecture that fulfils the Primary Goal "
        f"while strictly honouring every Hard Constraint and Additional Constraint listed above."
    )


def build_bicep_messages(fv: dict, llm_history: list[dict]) -> list[dict]:
    stripped        = strip_bicep_from_history(llm_history)
    additional_line = ""
    if fv.get("additional_constraints", "").strip():
        additional_line = f"- Additional Constraints: {fv['additional_constraints'].strip()}\n"
    bicep_request = (
        f"The architecture above has been approved. Now generate the COMPLETE Bicep template.\n\n"
        f"Brief summary:\n"
        f"- Goal: {fv['description'].strip()}\n"
        f"- Region: {fv['region']}\n"
        f"- Compliance: {fv['compliance']}\n"
        f"- Budget: {fv['budget']}\n"
        f"- Hub VNet: {fv['hub_vnet']}\n"
        f"{additional_line}\n"
        f"Include ALL resources. Do not truncate. "
        f"Output ONLY Bicep code inside a ```bicep block."
    )
    return stripped + [{"role": "user", "content": bicep_request}]


# ── Session / architecture helpers ─────────────────────────────────────────

def _extract_mermaid(llm_history: list[dict]) -> str:
    for msg in reversed(llm_history):
        if msg["role"] == "assistant":
            m = re.search(r"```mermaid\s*(.*?)\s*```", msg["content"],
                          re.DOTALL | re.IGNORECASE)
            if m:
                return m.group(1).strip()
    return ""


def _reconstruct_chat_display(llm_history: list[dict]) -> list[dict]:
    """Rebuild chat_display from stored llm_history.

    The first user message is the form brief (not shown in chat).
    Subsequent user messages are refinements with appended brain context — strip that suffix.
    """
    display = []
    for i, msg in enumerate(llm_history):
        if i == 0 and msg["role"] == "user":
            continue  # skip initial brief
        if msg["role"] == "user":
            text = msg["content"].split("\n\nAdditional context from Brain:")[0]
            display.append({"role": "user", "content": text})
        else:
            display.append({"role": "assistant", "content": msg["content"]})
    return display


def _reset_arch_state() -> None:
    st.session_state.llm_history            = []
    st.session_state.chat_display           = []
    st.session_state.last_hits              = []
    st.session_state.last_learn_hits        = []
    st.session_state.architecture_generated = False
    st.session_state.form_values            = {}
    st.session_state.approved_bicep         = None
    st.session_state.bicep_filename         = ""
    st.session_state.show_deploy_msg        = False
    st.session_state.show_save_input        = False
    st.session_state.arch_saved             = False
    st.session_state.eval_rating            = None
    st.session_state.eval_submitted         = False
    st.session_state.eval_session_id        = str(uuid.uuid4())
    st.session_state.auto_scores            = None
    st.session_state.auto_fix_triggered     = False
    st.session_state.auto_fix_message       = ""


def _load_arch_into_session(arch_id: int) -> None:
    data = load_architecture(arch_id, st.session_state.user_id)
    if data is None:
        st.error("Architecture not found.")
        return

    fv = data.get("form_values") or {}
    if not fv:
        fv = {
            "description":           data.get("description", ""),
            "region":                data.get("region", ""),
            "compliance":            data.get("compliance", ""),
            "budget":                data.get("budget", ""),
            "hub_vnet":              "No",
            "additional_constraints": data.get("additional_constraints", ""),
        }

    llm_history = data.get("llm_history", [])

    st.session_state.form_values            = fv
    st.session_state.llm_history            = llm_history
    st.session_state.chat_display           = _reconstruct_chat_display(llm_history)
    st.session_state.architecture_generated = True
    st.session_state.approved_bicep         = data.get("bicep_code") or None
    st.session_state.bicep_filename         = (
        f"te1-architecture-{time.strftime('%Y%m%d-%H%M%S')}.bicep"
        if data.get("bicep_code") else ""
    )
    st.session_state.show_deploy_msg        = False
    st.session_state.last_hits              = []
    st.session_state.last_learn_hits        = []
    st.session_state.show_save_input        = False
    st.session_state.arch_saved             = True  # already persisted
    st.session_state.eval_rating            = None
    st.session_state.eval_submitted         = False
    st.session_state.eval_session_id        = str(uuid.uuid4())
    st.session_state.auto_scores            = None


# ── Landing screen ─────────────────────────────────────────────────────────

def _show_landing() -> None:
    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.markdown("## 🛡️ TE-1 | TensorEdge Architect")
        st.markdown("*Lead Azure Cloud & AI Architect Agent — powered by TE-1 Brain*")
        st.divider()

        tab_idx = 0 if st.session_state.get("landing_tab", "login") == "login" else 1
        tab = st.radio(
            "", ["Login", "Register"],
            horizontal=True, index=tab_idx,
            key="landing_radio", label_visibility="collapsed",
        )

        if tab == "Login":
            with st.form("login_form"):
                username = st.text_input("Username")
                password = st.text_input("Password", type="password")
                do_login = st.form_submit_button("Sign in", use_container_width=True)

            if do_login:
                if not username or not password:
                    st.error("Please enter your username and password.")
                else:
                    user = authenticate_user(username, password)
                    if user:
                        token = create_session_token(user["id"])
                        st.session_state.mode          = "authenticated"
                        st.session_state.user_id       = user["id"]
                        st.session_state.username      = user["username"]
                        st.session_state.session_token = token
                        st.rerun()
                    else:
                        st.error("Invalid username or password.")

        else:  # Register
            with st.form("register_form"):
                username = st.text_input("Username")
                email    = st.text_input("Email")
                password = st.text_input("Password", type="password")
                confirm  = st.text_input("Confirm Password", type="password")
                do_reg   = st.form_submit_button("Create account", use_container_width=True)

            if do_reg:
                errors: list[str] = []
                if not all([username, email, password, confirm]):
                    errors.append("All fields are required.")
                elif len(username) < 3:
                    errors.append("Username must be at least 3 characters.")
                elif len(password) < 8:
                    errors.append("Password must be at least 8 characters.")
                elif password != confirm:
                    errors.append("Passwords do not match.")
                elif "@" not in email or "." not in email.split("@")[-1]:
                    errors.append("Please enter a valid email address.")

                if errors:
                    for e in errors:
                        st.error(e)
                else:
                    user = create_user(username, email, password)
                    if user is None:
                        st.error("That username or email is already taken.")
                    else:
                        token = create_session_token(user["id"])
                        st.session_state.mode          = "authenticated"
                        st.session_state.user_id       = user["id"]
                        st.session_state.username      = user["username"]
                        st.session_state.session_token = token
                        st.rerun()

        st.divider()
        if st.button("Continue as Guest →", use_container_width=True, type="secondary"):
            st.session_state.mode = "guest"
            st.rerun()
        st.caption("Guest sessions are temporary and cannot be saved.")


# ── Startup ────────────────────────────────────────────────────────────────

init_db()
bm25 = load_bm25()

# ── Session state defaults ─────────────────────────────────────────────────

_defaults: dict = {
    "mode":                     None,   # None (landing) | "guest" | "authenticated"
    "user_id":                  None,
    "username":                 None,
    "session_token":            None,
    "landing_tab":              "login",
    "llm_history":              [],
    "chat_display":             [],
    "last_hits":                [],
    "last_learn_hits":          [],
    "architecture_generated":   False,
    "form_values":              {},
    "approved_bicep":           None,
    "bicep_filename":           "",
    "show_deploy_msg":          False,
    "show_save_input":          False,
    "arch_saved":               False,
    "crystallised_path":        "",
    "eval_rating":              None,
    "eval_submitted":           False,
    "eval_session_id":          "",
    "auto_scores":              None,
    "auto_fix_triggered":       False,
    "auto_fix_message":         "",
}
for _k, _v in _defaults.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ── Landing screen — show and halt if not yet authenticated / guest ─────────

if st.session_state.mode is None:
    _show_landing()
    st.stop()

# ── Everything below here runs only when mode is "guest" or "authenticated" ──

# ── Sidebar ────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("🛡️ TE-1 Settings")
    engine_mode = st.radio("Engine:", ["Local (Ollama)", "BYOM (External API)"])

    provider = "OpenRouter"
    api_key  = ""

    if engine_mode == "Local (Ollama)":
        selected_model = st.selectbox("Model:", ["mistral-small:latest", "qwen3.5:latest"])
    else:
        provider       = st.selectbox("Provider:", ["OpenRouter", "OpenAI", "Gemini", "Claude"])
        selected_model = st.text_input("Model ID:", value="claude-haiku-4-5-20251001")
        api_key        = st.text_input("API Key:", type="password").strip()
        if not api_key:
            st.caption("⚠️ Enter an API key before generating.")

    st.divider()

    with st.expander("🔬 Brain Audit"):
        if bm25 is not None:
            st.metric("BM25 chunks", f"{len(bm25):,}")
            st.metric("Embed model", CONFIG.embed_model)
            st.metric("Vector size", CONFIG.vector_size)
            st.metric("Collection",  CONFIG.qdrant_collection)
        else:
            st.warning("Brain index not loaded.")

    # ── Brain Context placeholders — populated by update_brain_context() ───
    st.divider()
    st.subheader("🧠 Brain Context")
    brain_ctx_container = st.sidebar.container()
    st.subheader("🌐 Microsoft Learn Live")
    learn_ctx_container = st.sidebar.container()

    # ── Evals Dashboard link ──────────────────────────────────────────────
    st.divider()
    st.page_link("pages/evals_dashboard.py", label="📊 Evals Dashboard")

    # ── My Architectures / Guest prompt ───────────────────────────────────
    st.divider()

    if st.session_state.mode == "authenticated":
        with st.expander("📁 My Architectures", expanded=False):
            archs = get_user_architectures(st.session_state.user_id)
            if not archs:
                st.caption("No saved architectures yet.")
            else:
                for arch in archs:
                    st.markdown(f"**{arch['title'][:40]}**")
                    st.caption(f"{arch['region']} · {arch['created_at'][:10]}")
                    c_load, c_del = st.columns(2)
                    with c_load:
                        if st.button("Load", key=f"load_{arch['id']}",
                                     use_container_width=True):
                            _load_arch_into_session(arch["id"])
                            st.rerun()
                    with c_del:
                        if st.button("✕", key=f"del_{arch['id']}",
                                     use_container_width=True,
                                     help="Delete this architecture"):
                            delete_architecture(arch["id"], st.session_state.user_id)
                            st.rerun()
                    st.markdown("---")
    else:
        st.caption("Sign in to save and reload your architectures.")

    # ── Username + Logout ──────────────────────────────────────────────────
    if st.session_state.mode == "authenticated":
        st.divider()
        c_user, c_logout = st.columns([2, 1])
        with c_user:
            st.caption(f"👤 **{st.session_state.username}**")
        with c_logout:
            if st.button("Logout", key="logout_btn", use_container_width=True):
                if st.session_state.session_token:
                    delete_session(st.session_state.session_token)
                for k in list(st.session_state.keys()):
                    del st.session_state[k]
                st.rerun()


# ── LLM kwargs helper ──────────────────────────────────────────────────────

def _llm(max_tokens: int = 8192, temperature: float = 1.0) -> dict:
    return dict(
        engine_mode=engine_mode,
        model=selected_model,
        provider=provider,
        api_key=api_key,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def update_brain_context() -> None:
    """Write current Brain Context hits into the sidebar containers."""
    hits = st.session_state.get("last_hits", [])
    with brain_ctx_container:
        if hits:
            st.caption(f"{len(hits)} chunks retrieved")
            for i, h in enumerate(hits, 1):
                title = (h.chunk.title or "Untitled")[:55]
                repo  = h.chunk.source_repo
                score = h.fused_score
                stale_html = '<span class="stale-badge">⚠️ Stale</span>' if h.is_stale else ""
                st.markdown(
                    f'<div class="chunk-card">'
                    f"<strong>[{i}] {title}</strong>{stale_html}<br>"
                    f'<span class="chunk-repo">{repo}</span>&nbsp;&nbsp;'
                    f'<span class="chunk-score">Score {score:.5f}</span>'
                    f"</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.caption("No query run yet.")

    learn_hits = st.session_state.get("last_learn_hits", [])
    with learn_ctx_container:
        if learn_hits:
            for r in learn_hits:
                title = r.get("title", "Microsoft Learn")[:60]
                url   = r.get("url", "")
                snip  = r.get("snippet", "")[:100]
                st.markdown(
                    f"**{title}**  \n"
                    f"[{url[:70]}]({url})  \n"
                    f"_{snip}_"
                )
        else:
            st.caption("⚠️ Live docs unavailable")


update_brain_context()  # populate from session state on every rerun


# ── Issues display ─────────────────────────────────────────────────────────

_CATEGORY_LABELS: dict[str, str] = {
    "budget_risk":              "Budget Risk",
    "incomplete_specification": "Incomplete Specification",
    "operational_gap":          "Operational Gap",
    "wrong_service_behaviour":  "Service Configuration",
    "wrong_region_availability": "Regional Availability",
    "constraint_violation":     "Constraint Violation",
}
_SEVERITY_ICON: dict[str, str] = {
    "critical": "🔴",
    "medium":   "🟡",
    "low":      "🔵",
}


def render_issues(issues: list, form_values: dict) -> None:
    """Render auto-scorer issues as human-readable cards with auto-fix buttons."""
    # Normalize — support both dict flags (new) and plain strings (backward compat)
    normalized: list[dict] = []
    for iss in issues:
        if isinstance(iss, dict):
            normalized.append(iss)
        elif isinstance(iss, str) and iss.strip():
            normalized.append({"severity": "medium", "category": "operational_gap", "message": iss})

    if not normalized:
        st.success("✅ No issues found — architecture looks good")
        return

    n = len(normalized)
    st.warning(f"⚠️ {n} issue{'s' if n != 1 else ''} found — review before approving")

    critical = [i for i in normalized if i.get("severity") == "critical"]
    medium   = [i for i in normalized if i.get("severity") == "medium"]
    low      = [i for i in normalized if i.get("severity") not in ("critical", "medium")]

    def _card(issue: dict, idx: int) -> None:
        severity = issue.get("severity", "medium")
        category = issue.get("category", "operational_gap")
        message  = issue.get("message", "")
        icon     = _SEVERITY_ICON.get(severity, "🟡")
        label    = _CATEGORY_LABELS.get(category, category.replace("_", " ").title())

        if category == "budget_risk":
            fix_msg: str | None = (
                f"Reduce the architecture cost to fit within "
                f"{form_values.get('budget', 'the specified')} budget. "
                f"Remove or replace the most expensive optional components (Firewall, Bastion) "
                f"with cost-effective alternatives (NSG-only egress, jump box VM) "
                f"while maintaining the security posture."
            )
        elif category == "incomplete_specification":
            fix_msg = (
                "The architecture specification was incomplete. Please provide the full, "
                "complete architecture with all sections properly finished — especially "
                "the Identity & Access Control and networking sections."
            )
        elif category == "operational_gap":
            fix_msg = f"Add the missing operational component to the architecture: {message}"
        elif category == "wrong_service_behaviour":
            fix_msg = f"Fix the following service configuration issue: {message}"
        elif category == "constraint_violation":
            fix_msg = f"Redesign to fix this constraint violation: {message}"
        else:
            fix_msg = None

        with st.container():
            c_icon, c_body = st.columns([0.07, 0.93])
            with c_icon:
                st.markdown(f"## {icon}")
            with c_body:
                st.markdown(f"**{label}**")
                st.markdown(message)
                if fix_msg:
                    if st.button("💡 Fix this", key=f"fix_{idx}", use_container_width=False):
                        st.session_state.auto_fix_triggered = True
                        st.session_state.auto_fix_message   = fix_msg
                        st.rerun()
                else:
                    st.caption("📋 Note")

    for idx, issue in enumerate(critical):
        _card(issue, idx)
    for idx, issue in enumerate(medium, start=len(critical)):
        _card(issue, idx)

    if low:
        n_low = len(low)
        with st.expander(
            f"ℹ️ {n_low} minor note{'s' if n_low != 1 else ''} — click to expand"
        ):
            for idx, issue in enumerate(low, start=len(critical) + len(medium)):
                _card(issue, idx)


# ── Approve / eval helpers ─────────────────────────────────────────────────

def _do_approve() -> None:
    """Reinforce Brain, generate Bicep, and crystallise the session."""
    fv_approve    = st.session_state.form_values
    approve_query = (
        f"{fv_approve.get('description', '')} "
        f"{fv_approve.get('compliance', '')} Azure "
        f"{fv_approve.get('region', '')} architecture"
    )
    with st.spinner("🔍 Reinforcing Brain…"):
        _, reinforce_hits, _ = get_brain_context(approve_query, bm25, reinforce=True)
        if reinforce_hits:
            st.session_state.last_hits = reinforce_hits
            update_brain_context()

    bicep_msgs = build_bicep_messages(
        st.session_state.form_values,
        st.session_state.llm_history,
    )
    with st.spinner("⚙️ Generating complete Bicep template…"):
        bicep_response = call_llm(bicep_msgs, SYSTEM_BICEP, **_llm(max_tokens=-1))

    bicep_match = re.search(
        r"```bicep\s*(.*?)(?:```|$)", bicep_response, re.DOTALL | re.IGNORECASE
    )
    st.session_state.approved_bicep = (
        bicep_match.group(1).strip() if bicep_match else bicep_response.strip()
    )
    st.session_state.bicep_filename = (
        f"te1-architecture-{time.strftime('%Y%m%d-%H%M%S')}.bicep"
    )
    st.session_state.arch_saved = False

    try:
        from brain.wiki.crystalliser import crystallise_session
        last_assist = next(
            (msg["content"] for msg in reversed(st.session_state.llm_history)
             if msg["role"] == "assistant"),
            "",
        )
        wiki_path = crystallise_session({
            "description":          fv_approve.get("description", ""),
            "form_values":          fv_approve,
            "messages":             strip_bicep_from_history(st.session_state.llm_history),
            "retrieved_chunks":     st.session_state.last_hits,
            "architecture_summary": strip_bicep(last_assist),
        })
        st.session_state.crystallised_path = wiki_path
    except Exception:
        pass

    st.rerun()


def _save_eval_and_update_chunks(
    rating: str,
    category: str,
    feedback_text: str,
) -> None:
    """Persist evaluation and propagate feedback to chunk confidence scores."""
    chunk_ids = [
        h.chunk.chunk_id
        for h in st.session_state.get("last_hits", [])
        if h.chunk.chunk_id
    ]
    last_assist = next(
        (msg["content"] for msg in reversed(st.session_state.get("llm_history", []))
         if msg["role"] == "assistant"),
        "",
    )
    try:
        save_evaluation(
            user_id=st.session_state.get("user_id"),
            session_id=st.session_state.get("eval_session_id", ""),
            rating=rating,
            category=category,
            feedback_text=feedback_text,
            retrieved_chunk_ids=chunk_ids,
            auto_scores=st.session_state.get("auto_scores") or {},
            architecture_summary=last_assist[:2000],
            form_values=st.session_state.get("form_values", {}),
        )
    except Exception:
        pass
    try:
        update_chunk_feedback_log(chunk_ids, rating, category)
    except Exception:
        pass
    try:
        from brain.search.hybrid_search import apply_feedback_to_chunks
        apply_feedback_to_chunks(chunk_ids, rating, category)
    except Exception:
        pass


# ── Main UI ────────────────────────────────────────────────────────────────

st.title("🛡️ TE-1 | TensorEdge Infrastructure Architect")
st.markdown("---")

# ── Guest banner ───────────────────────────────────────────────────────────

if st.session_state.mode == "guest":
    c_msg, c_signin, c_reg = st.columns([6, 1, 1])
    with c_msg:
        st.caption("🔒 **Guest mode** — your session will not be saved.")
    with c_signin:
        if st.button("Sign in", key="banner_signin", use_container_width=True):
            _reset_arch_state()
            if "landing_radio" in st.session_state:
                del st.session_state["landing_radio"]
            st.session_state.landing_tab = "login"
            st.session_state.mode = None
            st.rerun()
    with c_reg:
        if st.button("Register", key="banner_register", use_container_width=True):
            _reset_arch_state()
            if "landing_radio" in st.session_state:
                del st.session_state["landing_radio"]
            st.session_state.landing_tab = "register"
            st.session_state.mode = None
            st.rerun()

# ── Job Form ───────────────────────────────────────────────────────────────

with st.form("job_form"):
    st.subheader("📋 Architecture Brief")

    description = st.text_area(
        "Describe what you need to build",
        placeholder=(
            "e.g. We need a RAG pipeline for internal documents with AKS agents "
            "and Azure AI Search, accessible only within our private network"
        ),
        height=120,
    )

    col1, col2 = st.columns(2)

    with col1:
        region = st.selectbox(
            "Region",
            ["Qatar Central", "UAE North", "UAE Central", "West Europe", "East US"],
        )
        compliance = st.selectbox(
            "Compliance Framework",
            ["None", "PCI-DSS", "HIPAA", "ISO 27001"],
        )
    with col2:
        budget = st.selectbox(
            "Monthly Budget Ceiling",
            ["Under $5k", "$5k–$20k", "$20k–$50k", "$50k+"],
        )
        hub_vnet = st.selectbox("Existing Hub VNet", ["No", "Yes"])

    additional_constraints = st.text_area(
        "Additional Constraints (optional)",
        placeholder=(
            "e.g. Must use existing ExpressRoute circuit, no public IPs anywhere, "
            "all storage must be in Qatar Central only"
        ),
        height=80,
    )

    submit = st.form_submit_button("Generate Architecture", use_container_width=True)

# ── Handle form submission ─────────────────────────────────────────────────

if submit:
    if not description.strip():
        st.error("Please describe what you need to build before generating an architecture.")
        st.stop()

    fv = {
        "description":           description,
        "region":                region,
        "compliance":            compliance,
        "budget":                budget,
        "hub_vnet":              hub_vnet,
        "additional_constraints": additional_constraints,
    }
    st.session_state.form_values = fv
    _reset_arch_state()
    st.session_state.form_values = fv  # restore after reset

    query = f"{description} {compliance} Azure {region} architecture"
    with st.spinner("🔍 Consulting Brain…"):
        context_block, hits, learn_hits = get_brain_context(query, bm25)
        st.session_state.last_hits = hits
        st.session_state.last_learn_hits = learn_hits
        update_brain_context()

    user_msg = build_initial_prompt(fv, context_block)
    st.session_state.llm_history = [{"role": "user", "content": user_msg}]

    with st.spinner("⚙️ Generating architecture…"):
        response = call_llm(st.session_state.llm_history, SYSTEM_ARCH,
                            **_llm(max_tokens=4096, temperature=0.2))

    st.session_state.llm_history.append({"role": "assistant", "content": response})
    st.session_state.chat_display           = [{"role": "assistant", "content": response}]
    st.session_state.architecture_generated = True

    try:
        from brain.eval.auto_scorer import score_architecture
        with st.spinner("📊 Scoring architecture…"):
            _region_chunks = [
                {"text": h.chunk.content, "title": h.chunk.title or ""}
                for h in hits
                if "region-availability" in (h.chunk.source_repo or "")
            ]
            st.session_state.auto_scores = score_architecture(
                architecture_summary=response,
                form_values=fv,
                retrieved_chunks=hits,
                engine_mode=engine_mode,
                model=selected_model,
                provider=provider,
                api_key=api_key,
                context_chunks=_region_chunks or None,
            )
    except Exception:
        st.session_state.auto_scores = None

    st.rerun()

# ── Architecture output & conversation ────────────────────────────────────

if st.session_state.architecture_generated:
    fv = st.session_state.form_values

    st.info(
        f"{fv.get('description', '').strip()} \n\n"
        f"**{fv.get('region', '')}** · Compliance: **{fv.get('compliance', '')}** · "
        f"Budget: **{fv.get('budget', '')}** · Hub VNet: **{fv.get('hub_vnet', '')}**"
    )

    for msg in st.session_state.chat_display:
        if msg["role"] == "assistant":
            with st.chat_message("assistant", avatar="🤖"):
                render_assistant_message(msg["content"])
        else:
            with st.chat_message("user"):
                st.markdown(msg["content"])

    # ── Refinement input ──────────────────────────────────────────────────

    refinement = st.chat_input("Refine the architecture — describe any changes needed")

    # Auto-fix button from issues panel injects a refinement message
    if not refinement and st.session_state.get("auto_fix_triggered"):
        refinement = st.session_state.auto_fix_message
        st.session_state.auto_fix_triggered = False
        st.session_state.auto_fix_message   = ""

    if refinement:
        with st.spinner("🔍 Consulting Brain…"):
            context_block, hits, learn_hits = get_brain_context(refinement, bm25)
            st.session_state.last_hits = hits
            st.session_state.last_learn_hits = learn_hits
            update_brain_context()

        refine_llm_msg = (
            f"{refinement}\n\nAdditional context from Brain:\n{context_block}"
        )
        st.session_state.llm_history.append({"role": "user", "content": refine_llm_msg})
        st.session_state.chat_display.append({"role": "user", "content": refinement})

        history_clean = strip_bicep_from_history(st.session_state.llm_history)

        with st.spinner("⚙️ Refining architecture…"):
            response = call_llm(history_clean, SYSTEM_ARCH, **_llm(max_tokens=4096, temperature=0.2))

        st.session_state.llm_history.append({"role": "assistant", "content": response})
        st.session_state.chat_display.append({"role": "assistant", "content": response})
        st.session_state.arch_saved = False  # mark unsaved after refinement
        st.rerun()

    st.markdown("---")

    # ── Architecture Quality ──────────────────────────────────────────────

    st.subheader("📊 Architecture Quality")
    _scores = st.session_state.get("auto_scores") or {}
    _flags  = [
        f for f in _scores.get("flags", [])
        if f and (isinstance(f, dict) or f != "auto-score unavailable")
    ]

    if _scores and _scores.get("overall") is not None:
        _overall = _scores.get("overall", 0.5)
        _ca      = _scores.get("constraint_adherence", 0.5)
        _sp      = _scores.get("security_posture", 0.5)
        _co      = _scores.get("completeness", 0.5)

        def _dim_icon(s: float) -> str:
            return "✓" if s > 0.7 else "△" if s >= 0.5 else "✗"

        st.progress(_overall, text=f"Architecture Quality: {_overall:.2f}")
        _dc1, _dc2, _dc3 = st.columns(3)
        _dc1.caption(f"Constraints {_dim_icon(_ca)}")
        _dc2.caption(f"Security {_dim_icon(_sp)}")
        _dc3.caption(f"Completeness {_dim_icon(_co)}")
        render_issues(_flags, fv)
    else:
        st.caption("Auto-score unavailable — scorer requires a running LLM.")

    st.markdown("─────────────────────────────────────────")
    st.markdown("**Rate this architecture before approving**")

    _eval_rating    = st.session_state.eval_rating
    _eval_submitted = st.session_state.eval_submitted

    if _eval_submitted:
        st.success("✓ Feedback recorded — thank you for improving TE-1")

    elif _eval_rating is None:
        _cg, _cb = st.columns(2)
        with _cg:
            if st.button("👍 Good architecture", use_container_width=True, key="eval_good"):
                st.session_state.eval_rating = "positive"
                st.rerun()
        with _cb:
            if st.button("👎 Needs work", use_container_width=True, key="eval_bad"):
                st.session_state.eval_rating = "negative"
                st.rerun()

    elif _eval_rating == "positive":
        _pos_text = st.text_area(
            "What makes this well-suited for your needs?",
            placeholder="(optional)",
            key="eval_pos_text",
        )
        _cs, _csk = st.columns(2)
        with _cs:
            if st.button("Submit Feedback", use_container_width=True, key="eval_pos_submit"):
                _save_eval_and_update_chunks("positive", "", _pos_text)
                st.session_state.eval_submitted = True
                st.rerun()
        with _csk:
            if st.button("Skip and Approve →", use_container_width=True,
                         type="secondary", key="eval_skip"):
                _save_eval_and_update_chunks("skipped", "", "")
                st.session_state.eval_submitted = True
                _do_approve()

    elif _eval_rating == "negative":
        _NEG_OPTS = [
            "Wrong Azure service behaviour",
            "Wrong region/service availability",
            "Over-engineered for budget",
            "Doesn't match our existing infrastructure",
            "Missing required components",
            "Other",
        ]
        _NEG_CODES = {
            "Wrong Azure service behaviour":             "wrong_service_behaviour",
            "Wrong region/service availability":         "wrong_region_availability",
            "Over-engineered for budget":                "over_engineered",
            "Doesn't match our existing infrastructure": "infrastructure_mismatch",
            "Missing required components":               "missing_components",
            "Other":                                     "other",
        }
        st.markdown("**What's the issue? (required)**")
        _neg_label = st.radio(
            "What's the issue?",
            _NEG_OPTS,
            key="eval_neg_category",
            label_visibility="collapsed",
        )
        _neg_text = st.text_area(
            "Describe the issue — TE-1 will learn from this.",
            key="eval_neg_text",
        )
        if st.button("Submit Feedback", use_container_width=True, key="eval_neg_submit"):
            if not _neg_text.strip():
                st.error("Please describe the issue before submitting.")
            else:
                _neg_code = _NEG_CODES.get(_neg_label, "other")
                _save_eval_and_update_chunks("negative", _neg_code, _neg_text)
                st.session_state.eval_submitted = True
                st.rerun()

    st.markdown("---")

    # ── Approve + Deploy buttons ──────────────────────────────────────────

    _approve_locked = (_eval_rating == "negative" and not _eval_submitted)
    col_approve, col_deploy = st.columns(2)

    with col_approve:
        if _approve_locked:
            st.info("Submit feedback above to unlock the Approve button.")
        else:
            if st.button("✅ Approve Architecture & Generate Bicep",
                         use_container_width=True, key="approve_btn"):
                _do_approve()

    with col_deploy:
        if st.button("🌐 Deploy to Azure Tenant",
                     use_container_width=True, key="deploy_btn",
                     type="secondary"):
            st.session_state.show_deploy_msg = True

    # ── Download Bicep ────────────────────────────────────────────────────

    if st.session_state.crystallised_path:
        st.info(
            f"🧠 Session crystallised into "
            f"`wiki/semantic/{os.path.basename(st.session_state.crystallised_path)}`"
        )

    if st.session_state.approved_bicep:
        st.success(f"Bicep template ready: **{st.session_state.bicep_filename}**")
        st.download_button(
            label="⬇️ Download Bicep Template",
            data=st.session_state.approved_bicep,
            file_name=st.session_state.bicep_filename,
            mime="text/plain",
            use_container_width=True,
            key="download_bicep",
        )

        # ── Save Architecture (authenticated users only) ───────────────────
        if st.session_state.mode == "authenticated":
            if st.session_state.arch_saved:
                st.success("✅ Architecture saved to your profile.")
            elif not st.session_state.show_save_input:
                if st.button("💾 Save Architecture", use_container_width=True,
                             key="save_arch_btn"):
                    st.session_state.show_save_input = True
                    st.rerun()
            else:
                with st.form("save_arch_form"):
                    save_title = st.text_input(
                        "Architecture title",
                        placeholder="e.g. RAG Pipeline — Qatar Central",
                    )
                    c_save, c_cancel = st.columns(2)
                    with c_save:
                        do_save = st.form_submit_button("💾 Save", use_container_width=True)
                    with c_cancel:
                        do_cancel = st.form_submit_button("Cancel", use_container_width=True)

                if do_save:
                    if not save_title.strip():
                        st.error("Please enter a title for this architecture.")
                    else:
                        mermaid = _extract_mermaid(st.session_state.llm_history)
                        save_architecture(
                            st.session_state.user_id,
                            save_title.strip(),
                            st.session_state.form_values,
                            st.session_state.llm_history,
                            mermaid,
                            st.session_state.approved_bicep or "",
                        )
                        st.session_state.arch_saved       = True
                        st.session_state.show_save_input  = False
                        st.rerun()

                if do_cancel:
                    st.session_state.show_save_input = False
                    st.rerun()

    if st.session_state.show_deploy_msg:
        st.info(
            "Deployment execution coming soon. This will deploy the approved Bicep template "
            "to your Azure tenant via a scoped Service Principal."
        )
