USAGE: After /clear, paste this into Claude Code: read ~/.claude/skills/te1-context.md and use as session context

---
name: te1-context
description: Project brief for TensorEdge-1 (TE-1) — autonomous Azure Architect Agent. Load this to restore full build context before any TE-1 task.
---

## TE-1 — TensorEdge Infrastructure Architect

Agent-as-a-Service (AaaS) autonomous Azure Architect Agent
Owner: Mohamed Helal (MoHelal)
GitHub: https://github.com/Mohamed21882/azure-architect-agent
Project root: ~/Azure-Architect-Wiki

## How to Run

| Action | Command |
|---|---|
| Start | `cd ~/Azure-Architect-Wiki && ./start.sh` |
| Stop | `./stop.sh` |
| Access URL | http://192.168.18.31:8501 |
| Claude Code | `cd ~/Azure-Architect-Wiki && claude` |

## Tech Stack

- Python 3.12, Streamlit (UI)
- Qdrant (vector store, Docker, persistent volume at data/qdrant/)
- nomic-embed-text via Ollama (768-dim embeddings)
- rank-bm25 (BM25 keyword index, persisted at brain/store/bm25.pkl)
- SQLite at brain/store/te1.db (auth + saved architectures + evaluations)
- Ollama local LLMs (mistral-small, qwen3.5, qwen3:14b) for dev/testing
- External APIs: Claude, OpenAI, OpenRouter, Gemini (for production)
- Docker (Qdrant container named te1-qdrant)
- mcp==1.27.1 (Microsoft Learn MCP Server client)

## Directory Structure

```
~/Azure-Architect-Wiki/
├── brain/
│   ├── models.py              # Chunk, RawDocument, SearchResult, WikiPage, IngestRun
│   ├── config.py              # Settings, half-life constants, use_learn_mcp flag, learn_mcp_url
│   ├── ingest/
│   │   ├── chunker.py         # Token-aware heading-based chunker
│   │   ├── embedder.py        # nomic-embed-text via Ollama /api/embed
│   │   ├── local_reader.py    # Walks raw/ repos, yields RawDocuments
│   │   └── pipeline.py        # Main ingest runner, --incremental flag, manifest
│   ├── search/
│   │   ├── bm25_index.py      # BM25Okapi, persisted to brain/store/bm25.pkl
│   │   ├── hybrid_search.py   # RRF fusion, temporal reranking, reinforcement
│   │   └── learn_mcp.py       # Microsoft Learn MCP Server live retrieval
│   ├── store/
│   │   └── vector_store.py    # Qdrant wrapper, upsert, set_payload, supersession
│   ├── wiki/
│   │   └── crystalliser.py    # Session → wiki/semantic/*.md + re-ingest
│   ├── eval/
│   │   └── auto_scorer.py     # Architecture quality scorer, structured flags, context_chunks grounding
│   └── db/
│       └── database.py        # SQLite: users, architectures, sessions, evaluations, chunk_feedback_log
├── ui/
│   ├── app.py                 # Full Streamlit portal (1300+ lines)
│   └── pages/
│       └── evals_dashboard.py # Evaluation metrics dashboard
├── wiki/
│   ├── semantic/              # Crystallised session wiki pages (growing)
│   ├── procedural/            # Deployment runbooks (empty, v0.2)
│   └── entities/              # Client configurations (empty, v0.2)
├── raw/
│   ├── architecture-center/   # 534 MS docs (gitignored)
│   ├── azure-ai/              # 4,354 MS docs (gitignored)
│   ├── azure-foundry/         # 791 MS docs (gitignored)
│   ├── cli/                   # 137 MS docs (gitignored)
│   ├── region-availability/   # VERSIONED: Qatar Central + UAE North curated data
│   └── microsoft-fabric/      # VERSIONED: WAF for Microsoft Fabric (6 pages)
├── data/
│   ├── qdrant/                # Qdrant persistent storage (gitignored)
│   └── ingest_manifest.json   # Tracks ingested files for incremental re-ingest
├── .claude/
│   └── commands/
│       └── te1-context.md     # This file
├── .claude.md                 # Agent constitution (AGENTS.md)
├── .env / .env.example        # Config (gitignored)
├── requirements.txt
├── start.sh                   # Starts Qdrant + Streamlit, prints LAN IP — confirmed working
└── stop.sh                    # Stops Qdrant container + Streamlit — confirmed working
```

## Knowledge Base State

- 63,921 chunks in Qdrant (768-dim cosine)
- 63,921 chunks in BM25 index (brain/store/bm25.pkl, 117MB)
- Sources: architecture-center (534 docs), azure-ai (4,354), azure-foundry (791), cli (137), region-availability (3 curated files), microsoft-fabric (6 curated files)
- Qdrant collection name: azure_wiki

## What Is Fully Shipped (Alpha v0.1)

1. Full ingest pipeline — local_reader → chunker → embedder → Qdrant + BM25
2. Hybrid search — BM25 + vector + RRF (k=60)
3. Temporal reranking — live Ebbinghaus decay, freshness multipliers (×1.15 <30d, ×0.75 >548d), hard expiry filter, corroboration requirement, per-source half-lives (region: 30d, API: 60d, architecture: 180d, procedural: 365d)
4. LLM Wiki V2 — session crystallisation → wiki/semantic/.md, chunk reinforcement on approval, confidence decay
5. Incremental re-ingest — --incremental flag, git diff, supersession, manifest tracking
6. Evaluation system — auto_scorer.py (4 dimensions: constraint_adherence, security_posture, completeness, overall), human feedback (👍/👎), category tags, chunk confidence updates, quarantine at 3 flags
7. Evals dashboard — ui/pages/evals_dashboard.py, metrics, flagged chunks, quarantine alerts
8. User authentication — SQLite, bcrypt, 30-day session tokens, login/register/guest
9. Saved architectures — per user, full session restore including messages and diagram
10. UI — structured job form (description + region/compliance/budget/hub VNet + additional constraints), Mermaid diagram with semantic colour injection via inject_mermaid_styles(), iterative refinement chat, Bicep on approval only, download as .bicep file
11. Architecture Status bar — "Production-Ready | All constraints honoured | Budget-aligned | Zero public exposure"
12. Brain Context sidebar — shows retrieved chunks with RRF scores after every generation
13. Regional availability knowledge — Qatar Central + UAE North verified and ingested
14. Microsoft Fabric WAF knowledge — 6 pages ingested
15. Microsoft Learn MCP Server — brain/search/learn_mcp.py; query_learn_mcp() connects to https://learn.microsoft.com/api/mcp via mcp.client.streamable_http; runs in parallel with hybrid_search() via ThreadPoolExecutor(max_workers=2); 1-hour in-memory cache per query; 5-second timeout with asyncio.wait_for; silent fallback (returns [] on any error, never raises); response format {"results":[{title,content,contentUrl}]}; fused into LLM prompt under "## Live API Reference" header; shown in sidebar as "🌐 Microsoft Learn Live"; config.use_learn_mcp=True, config.learn_mcp_url set
16. Issues UX redesign — render_issues() function in ui/app.py; groups by severity: 🔴 Critical / 🟡 Medium always visible, 🔵 Minor notes collapsed in st.expander; human-readable category labels (_CATEGORY_LABELS dict); "💡 Fix this" button for actionable categories (budget_risk, incomplete_specification, operational_gap, wrong_service_behaviour, constraint_violation) with pre-built refinement message templates; "📋 Note" label for non-fixable issues; summary line above ("✅ No issues" or "⚠️ N issues found")
17. Auto-fix handler — clicking "💡 Fix this" sets auto_fix_triggered=True + auto_fix_message in session state then reruns; on next rerun the handler between st.chat_input and the refinement processor picks it up and routes through the exact same LLM pipeline as a typed refinement (same Brain context fetch, same SYSTEM_ARCH, same history management)
18. max_tokens=4096 for architecture generation — both initial generation and refinement calls use _llm(max_tokens=4096, temperature=0.2); Bicep stays at max_tokens=-1
19. Auto-scorer structured flags — scorer prompt updated to request {"severity":"critical|medium|low","category":"budget_risk|...","message":"plain English"} objects; _parse() normalises plain-string flags to dicts for backward compat
20. Auto-scorer grounded in regional availability chunks — score_architecture() accepts context_chunks: list[dict] | None = None; app.py filters last_hits for source_repo containing "region-availability" and passes as {"text":..., "title":...} dicts; prepended to scorer prompt under "## Verified Regional Knowledge (use this as ground truth)"
21. Azure OpenAI Qatar Central false flag fixed — explicit hard instruction added to scorer prompt: "Azure OpenAI IS available in Qatar Central (qatarcentral) — this is confirmed. Do NOT flag Azure OpenAI availability in Qatar Central as uncertain or unconfirmed. Qatar Central is a supported Microsoft Foundry project region with Azure OpenAI GA."
22. start.sh / stop.sh — confirmed working one-command launch, auto-detects LAN IP

## What Is NOT Built Yet (v0.2 Targets)

1. Azure execution engine — Bicep → live deployment via scoped Service Principal
2. Programmatic HITL approval gate — real button, not convention
3. Deployment audit log — immutable, per-tenant
4. Drift detection — compare live tenant state vs desired state
5. Regional availability weekly auto-update scheduler
6. Wiki-lint metabolism — scheduled contradiction detection
7. Graph traversal search — third search stream
8. Multi-tenant isolation — per-tenant vector namespaces
9. Commercial layer — Paddle payments, provisioning webhook, pricing page on tensoredge.net
10. Microsoft Marketplace SaaS offer listing

## Key Non-Obvious Patterns (Read Before Modifying)

- Bicep is NEVER generated speculatively — only on "Approve Architecture & Generate Bicep" button click
- Bicep is ALWAYS stripped from LLM history before refinement calls (strip_bicep_from_history())
- Chunk reinforcement fires ONLY on approval, not on every query (reinforce=True only in _do_approve())
- Negative feedback confidence decay only fires for categories: "wrong_service_behaviour" or "wrong_region_availability" — not for user preference issues
- Quarantine threshold is 3 flags — chunk.quarantined=1 after 3 negative flags from technical categories
- inject_mermaid_styles() strips all LLM-generated classDef lines and injects canonical 6-class semantic colour system: network(blue), security(green), compute(purple), storage(orange), monitor(yellow), dns(cyan)
- temperature=0.2 for architecture generation and refinement; temperature=1.0 (default) for Bicep generation
- max_tokens=4096 for architecture generation/refinement; max_tokens=-1 (unlimited) for Bicep
- _do_approve() is the single canonical approve path — called from both Approve button and "Skip and Approve →"
- Learn MCP falls back silently on timeout/error — never blocks generation; asyncio.run() safe in ThreadPoolExecutor threads
- Auto-scorer must NOT flag mainstream Azure services (Firewall, VPN Gateway, Bastion, AKS, AI Search, Storage, Key Vault) as unavailable in any GA region without confirmed evidence
- Azure OpenAI IS confirmed available in Qatar Central — do not re-add a flag for it
- Azure Firewall, Bastion, VPN Gateway all require public IPs by design — this is NOT a constraint violation
- "Hub VNet: No" means CREATE a new hub, not omit the hub
- Auto-fix buttons in render_issues() use key=f"fix_{idx}" — idx is global across critical+medium+low groups to avoid key collisions
- get_brain_context() returns tuple[str, list[SearchResult], list[dict]] — third element is learn_hits; all three call sites must unpack all three values

## Commercial Status

- GitHub repo: public, https://github.com/Mohamed21882/azure-architect-agent
- LinkedIn post: published
- Paddle registration: started but paused (needs 4 pages on tensoredge.net first)
- Microsoft Partner Centre: registration started (needs work account for Commercial Marketplace enrollment)
- tensoredge.net: WordPress, hosted locally on X1 Pro (WordPress root directory not yet located)
- Hackathon: Microsoft Agents League — registration deadline June 12, submission June 4-14, $55k prizes, targeting Reasoning Agents track

## Pending Questions Not Yet Resolved

1. WordPress root directory still unknown (docker ps / find /srv needed on X1 Pro)
2. Hermes agent on tensoredge.net still unexplained
3. Public URL for TE-1 customers not yet decided (te1.tensoredge.net?)
4. Static vs dynamic public IP on X1 Pro not confirmed

## Next Session Priorities

1. tensoredge.net commercial pages — locate WordPress root, build pricing/ToS/privacy/refund pages, complete Paddle registration
2. Deploy engine design — design here in chat first, then implement in Claude Code
3. Hackathon submission prep — deadline June 14
