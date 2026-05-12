USAGE: After /clear, paste this into Claude Code: read ~/.claude/skills/te1-context.md and use as session context

---
name: te1-context
description: Project brief for TensorEdge-1 (TE-1) — autonomous Azure Architect Agent. Load this to restore full build context before any TE-1 task.
---

# TensorEdge-1 (TE-1) — Project Brief

## What is TE-1?

TE-1 is an **Agent-as-a-Service (AaaS) autonomous Azure architect agent** deployed at `~/Azure-Architect-Wiki`. It acts as a senior Azure architect: takes a plain-language infrastructure brief, retrieves grounded context from a 63k-chunk Azure knowledge base, generates a complete architecture with a Mermaid diagram, and produces a deployment-ready Bicep template on human approval.

The agent constitution is in `.claude.md` (HITL safety protocol, Wiki V2 knowledge tiers, Visual Reasoning rules, Azure Deployment Journal, mcpvault MCP server).

---

## Tech Stack

| Layer | Technology |
|---|---|
| UI | Streamlit (`ui/app.py`) — multi-page, multi-provider |
| LLM routing | Ollama (local) · OpenRouter · OpenAI · Claude · Gemini |
| Vector search | Qdrant (`localhost:6333`) · nomic-embed-text 768-dim |
| Sparse search | BM25Okapi (`brain/store/bm25.pkl`) |
| Fusion | Reciprocal Rank Fusion (RRF, k=60) |
| Persistence | SQLite (`brain/store/te1.db`) via `brain/db/database.py` |
| Embeddings | nomic-embed-text via Ollama |
| Language | Python 3.11 · venv at `venv/` |

---

## Directory Structure

```
Azure-Architect-Wiki/
├── .claude.md                  # Agent constitution (persona, HITL, Wiki V2, governance)
├── brain/
│   ├── models.py               # RawDocument, Chunk dataclasses (confidence, decay, reinforce)
│   ├── config.py               # BrainConfig (Qdrant URL, embed model, paths)
│   ├── db/
│   │   └── database.py         # SQLite: users, sessions, architectures, evaluations, chunk_feedback_log
│   ├── eval/
│   │   └── auto_scorer.py      # score_architecture() — 4-dimension LLM quality scorer
│   ├── ingest/
│   │   ├── chunker.py          # Token-aware heading-based chunker
│   │   ├── embedder.py         # nomic-embed-text via Ollama
│   │   ├── local_reader.py     # Walks raw/ repos, yields RawDocuments
│   │   └── pipeline.py         # Ingest CLI runner
│   ├── search/
│   │   ├── bm25_index.py       # BM25Okapi with disk persistence
│   │   └── hybrid_search.py    # RRF fusion + apply_feedback_to_chunks()
│   ├── store/
│   │   └── vector_store.py     # Qdrant client wrapper; upsert_chunks handles payload-only updates
│   └── wiki/
│       ├── crystalliser.py     # Session → wiki/semantic/ page on approval
│       └── logs/               # azure-deployment-journal.md, weekly briefings
├── ui/
│   ├── app.py                  # Main Streamlit portal (1100+ lines)
│   └── pages/
│       └── evals_dashboard.py  # Multi-page eval dashboard
├── wiki/
│   ├── semantic/               # Tier 1: theory, architecture patterns
│   ├── procedural/             # Tier 2: runbooks, step-by-step guides
│   └── entities/               # Tier 3: client-specific configs
├── raw/                        # Gitignored — Microsoft doc repos cloned here
└── store/                      # Gitignored — te1.db, bm25.pkl live here at runtime
```

---

## Key Files

### `brain/models.py`
- `IngestSource` enum: `MICROSOFT_LEARN`, `LOCAL`
- `RawDocument`: `doc_id` (SHA-1 of path), `content`, `source`, `source_repo`, `file_path`, `title`, `metadata`
- `Chunk`: `chunk_id` (UUID5), `doc_id`, `content`, `confidence` (0.1–1.0), `reinforcement_count`, `last_confirmed_at`
  - `reinforce()` — increments count, boosts confidence by `0.1` per confirmation (cap 1.0)
  - `decayed_confidence()` — exponential decay at 5%/30 days since last confirmation

### `brain/db/database.py`
SQLite with WAL mode. Tables:
- `users` — bcrypt passwords, 30-day sessions
- `architectures` — stores `form_values` + `llm_history` as JSON blob
- `sessions` — token auth, 30-day TTL
- `evaluations` — per-session human rating + auto-scores (overall, constraints, security, completeness)
- `chunk_feedback_log` — per-chunk flag/positive counters; quarantine at `flag_count >= 3`

Key functions: `save_evaluation()`, `update_chunk_feedback_log()`, `get_eval_dashboard_stats()`, `get_recent_evaluations()`

### `brain/search/hybrid_search.py`
- `hybrid_search(query, bm25, config, top_k, rrf_k, dense_candidates, reinforce)` — fuses Qdrant cosine + BM25, falls back to BM25-only if Qdrant/Ollama unavailable
- `_reinforce_results()` — called on human approval; writes boosted confidence back to Qdrant via `upsert_chunks` payload-only path
- `apply_feedback_to_chunks(chunk_ids, rating, category)` — positive: `reinforce()`; negative with `wrong_service_behaviour` or `wrong_region_availability`: −0.05 decay (floor 0.1)

### `ui/app.py`
Single-file Streamlit app. Top-level flow:
1. `init_db()` + `load_bm25()` on startup
2. Landing screen (login / register / guest) — halts with `st.stop()` if unauthenticated
3. Sidebar: engine selector (Ollama / BYOM), Brain Audit expander, Brain Context, Evals Dashboard link, My Architectures, logout
4. Architecture Brief form → `get_brain_context()` → `call_llm(SYSTEM_ARCH)` → Mermaid + summary
5. Eval block: auto-score bar, 👍/👎 rating, positive/negative/skip flows, `_save_eval_and_update_chunks()`
6. Approve button (gated on eval state) → `_do_approve()` → Bicep generation → crystallisation
7. Refinement chat: appends to `llm_history`, strips Bicep from context to save tokens

System prompts: `SYSTEM_ARCH` (architecture + strict Mermaid rules), `SYSTEM_BICEP` (complete Bicep only)

Session state keys: `mode`, `user_id`, `llm_history`, `chat_display`, `last_hits`, `form_values`, `approved_bicep`, `eval_rating`, `eval_submitted`, `eval_session_id`, `auto_scores`, `crystallised_path`

### `brain/eval/auto_scorer.py`
- `score_architecture(architecture_summary, form_values, retrieved_chunks, engine_mode, model, provider, api_key) -> dict`
- Scores four dimensions (0.0–1.0): `constraint_adherence`, `security_posture`, `completeness`, `overall`
- `overall = constraint_adherence×0.4 + security_posture×0.3 + completeness×0.3`
- Returns fallback dict `{all: 0.5, flags: ["auto-score unavailable"]}` on any error
- Prompt explicitly prohibits flagging mainstream Azure services (Firewall, VPN Gateway, Bastion, AKS, AI Search, Storage, Key Vault) for regional availability unless unavailability is confirmed

---

## Current Build State

| Component | Status |
|---|---|
| Ingest pipeline (63k chunks, 4 repos) | ✅ Alpha v0.1 |
| Hybrid search (BM25 + Qdrant + RRF) | ✅ |
| Architecture generation + Mermaid diagrams | ✅ |
| Bicep template generation | ✅ |
| Iterative refinement chat | ✅ |
| User auth + saved architectures (SQLite) | ✅ |
| LLM Wiki V2 (crystallisation, reinforcement, confidence decay) | ✅ |
| Evaluation system (auto-scorer + human rating + chunk feedback) | ✅ |
| Evals Dashboard (`ui/pages/evals_dashboard.py`) | ✅ |
| Incremental re-ingest | ✅ |
| Temporal reranking layer (recency boost on RRF results) | ✅ |
| Regional availability knowledge source (Qatar Central, UAE North) | ✅ |
| Azure execution engine (Bicep → live deploy via scoped SP) | 🔜 v0.2 |
| Programmatic HITL approval gate | 🔜 v0.2 |
| Deployment audit log | 🔜 v0.2 |
| Drift detection | 🔜 v0.2 |
| Regional availability scheduler (automated refresh) | 🔜 v0.2 |
| Wiki-lint metabolism (weekly /wiki-lint) | 🔜 v0.2 |
| Graph traversal search stream | 🔜 v0.2 |

---

## Patterns to Know

- **`upsert_chunks` with `embedding=None`** uses `set_payload()` — safe for confidence-only updates without re-embedding
- **Mermaid style injection** (`inject_mermaid_styles`) strips LLM classDefs and re-applies canonical ones keyed to Azure resource categories
- **Bicep is stripped from `llm_history`** before refinement calls to save context tokens (`strip_bicep_from_history`)
- **`_do_approve()`** is the canonical approve flow — called from both the Approve button and "Skip and Approve →"
- **Quarantine** requires 3+ flags via `update_chunk_feedback_log`; single negative never quarantines
- **`eval_session_id`** is a UUID4 generated fresh in `_reset_arch_state()` for every new architecture
