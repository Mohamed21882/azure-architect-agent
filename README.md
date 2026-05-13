# TE-1 — Autonomous Azure Architect Agent

> An Agent-as-a-Service (AaaS) system that acts as a senior Azure architect inside your tenant.  
> Built on Karpathy's LLM Wiki pattern · Powered by 63k+ Azure knowledge chunks · Generates production Bicep templates

---

## What is TE-1?

TE-1 is not a chatbot. It is an autonomous Azure architect agent that:

- **Knows Azure deeply** — ingests and indexes official Microsoft documentation (Architecture Center, Azure AI, Azure CLI, Azure Foundry) into a hybrid vector + BM25 knowledge base
- **Designs architectures** — takes a plain-language brief and generates a complete, constraint-aware Azure architecture with a Mermaid diagram
- **Generates Bicep** — produces complete, deployment-ready Bicep templates on approval
- **Iterates with you** — accepts natural language refinements ("add a Redis cache", "change AKS to Container Apps") and updates the architecture coherently
- **Remembers your work** — authenticated users can save, reload, and continue past architecture sessions

The target user is an **Azure admin who lacks architectural expertise** — not a developer building yet another AI chatbot.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Streamlit UI                       │
│  Job Form → Architecture → Refine → Approve → Bicep │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│              Brain (LLM Wiki V1)                     │
│                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ │
│  │   Qdrant    │  │  BM25 Index │  │  Hybrid RRF │ │
│  │  63k chunks │  │  63k chunks │  │   Search    │ │
│  │  768-dim    │  │  117 MB pkl │  │   k=60      │ │
│  └─────────────┘  └─────────────┘  └─────────────┘ │
│                                                     │
│  ┌─────────────────────────────────────────────────┐│
│  │  Ingest Pipeline                                ││
│  │  local_reader → chunker → embedder → upsert    ││
│  └─────────────────────────────────────────────────┘│
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│              Raw Source Corpus                       │
│  architecture-center │ azure-ai │ azure-foundry │ cli│
│         534          │  4,354   │     791       │ 137│
└─────────────────────────────────────────────────────┘
```

**Search strategy:** Reciprocal Rank Fusion combining vector similarity (nomic-embed-text, 768-dim) and BM25 keyword matching. Top-6 chunks are injected into the LLM prompt as grounded context.

**Knowledge base:** Built from 5,816 official Microsoft documentation files across 4 repositories. ~39 minutes to ingest on a mid-range machine.

---

## Quick Start

> [!IMPORTANT]
> **Prerequisites:** Python 3.11+ · Docker · Ollama · Git

**1. Clone the repo**
```bash
git clone https://github.com/Mohamed21882/azure-architect-agent.git
cd azure-architect-agent
```

**2. Set up the Python environment**
```bash
python -m venv venv && source venv/bin/activate && pip install -r requirements.txt
```

**3. Clone the 4 Microsoft source repos into `raw/`**
```bash
mkdir -p raw && cd raw
git clone https://github.com/MicrosoftDocs/architecture-center.git architecture-center
git clone https://github.com/MicrosoftDocs/azure-ai-docs.git azure-ai
git clone https://github.com/MicrosoftDocs/azure-ai-docs.git azure-foundry
git clone https://github.com/MicrosoftDocs/azure-docs-cli.git cli
cd ..
```

**4. Copy `.env.example` to `.env`**
```bash
cp .env.example .env
```

**5. Pull the embedding model**
```bash
ollama pull nomic-embed-text
```

**6. Run the ingest pipeline** *(~40 min, produces 63k+ chunks)*
```bash
PYTHONPATH=. venv/bin/python -m brain.ingest.pipeline
```

**7. Launch TE-1**
```bash
./start.sh
```
The terminal will print your network access URL (e.g. `http://192.168.x.x:8501`).

---

## Features

| Feature | Status |
|---------|--------|
| Natural language architecture brief | ✅ |
| Structured constraints (region, compliance, budget) | ✅ |
| Additional free-text constraints | ✅ |
| Mermaid architecture diagram with semantic colours | ✅ |
| Iterative refinement chat | ✅ |
| Bicep template generation (on approval) | ✅ |
| Bicep download as `.bicep` file | ✅ |
| Brain Context sidebar (retrieved chunks) | ✅ |
| User authentication (login / register / guest) | ✅ |
| Save & reload architectures (SQLite) | ✅ |
| Local LLM support (Ollama) | ✅ |
| External API support (Claude, OpenAI, OpenRouter, Gemini) | ✅ |
| Deploy to Azure tenant | 🔜 v0.2 |
| LLM Wiki V2 (crystallisation, reinforcement, decay) | 🔜 v0.2 |

---

## Getting Started

### Prerequisites

- Python 3.11+
- Docker (for Qdrant)
- Ollama (for local LLM) **or** an API key for Claude / OpenAI / OpenRouter / Gemini
- Git

### 1. Clone the repo

```bash
git clone https://github.com/Mohamed21882/azure-architect-agent.git
cd azure-architect-agent
```

### 2. Set up the Python environment

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Clone the Azure documentation source repos

```bash
mkdir -p raw
cd raw
git clone https://github.com/MicrosoftDocs/architecture-center.git architecture-center
git clone https://github.com/MicrosoftDocs/azure-ai-docs.git azure-ai
git clone https://github.com/MicrosoftDocs/azure-ai-docs.git azure-foundry
git clone https://github.com/MicrosoftDocs/azure-docs-cli.git cli
cd ..
```

> These are public Microsoft repositories. Total size ~2GB. The raw docs are excluded from this repo via `.gitignore`.

### 4. Configure environment

```bash
cp .env.example .env
# Edit .env — set your preferred embedding model and Qdrant settings
```

### 5. Start Qdrant

```bash
docker run -p 6333:6333 \
  -v $(pwd)/data/qdrant:/qdrant/storage \
  qdrant/qdrant
```

### 6. Pull the embedding model

```bash
ollama pull nomic-embed-text
```

### 7. Run the ingest pipeline

```bash
PYTHONPATH=. venv/bin/python -m brain.ingest.pipeline
```

> First run takes ~40 minutes and produces 63k+ chunks. Subsequent runs support `--incremental` (coming in v0.2).

### 8. Start TE-1

```bash
# Local only
PYTHONPATH=. venv/bin/streamlit run ui/app.py

# Network accessible (access from another machine on the same network)
PYTHONPATH=. venv/bin/streamlit run ui/app.py --server.address 0.0.0.0 --server.port 8501
```

Or use the convenience script:

```bash
./start.sh
```

---

## Project Structure

```
te-1/
├── brain/
│   ├── ingest/
│   │   ├── chunker.py          # Token-aware heading-based chunker
│   │   ├── embedder.py         # nomic-embed-text via Ollama
│   │   ├── local_reader.py     # Walks raw/ repos, yields RawDocuments
│   │   └── pipeline.py         # Main ingest runner (CLI)
│   ├── search/
│   │   ├── bm25_index.py       # BM25Okapi index with disk persistence
│   │   └── hybrid_search.py    # RRF fusion of vector + BM25
│   ├── store/
│   │   └── vector_store.py     # Qdrant wrapper
│   ├── db/
│   │   └── database.py         # SQLite auth + saved architectures
│   ├── models.py               # Pydantic models (Chunk, WikiPage, etc.)
│   └── config.py               # Settings from .env
├── ui/
│   └── app.py                  # Streamlit portal
├── wiki/
│   ├── semantic/               # Curated Azure knowledge pages
│   ├── procedural/             # Deployment runbooks (populated by TE-1)
│   └── entities/               # Client configurations
├── raw/                        # Gitignored — clone Microsoft repos here
├── data/                       # Gitignored — generated at runtime
├── .claude.md                  # Agent constitution (AGENTS.md)
├── .env.example
├── requirements.txt
└── start.sh
```

---

## The Knowledge Architecture

TE-1's brain is built on [Andrej Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — the idea that an agent should stop re-deriving knowledge and start compiling it.

**What makes this different from standard RAG:**

| Standard RAG | TE-1 Brain |
|---|---|
| Retrieves and forgets | Accumulates and indexes |
| Static document chunks | Confidence-scored chunks with metadata |
| Single embedding model | Hybrid BM25 + vector + RRF fusion |
| Generic knowledge | Azure-specific entity taxonomy |
| No memory of past sessions | Saved architectures with full session state |

**Roadmap to LLM Wiki V2:**  
The next version implements the full lifecycle layer from [LLM Wiki V2](https://gist.github.com/): confidence decay (Ebbinghaus curve), crystallisation (session → wiki page), chunk reinforcement on retrieval, and incremental re-ingest with supersession. This transforms TE-1 from a static knowledge base into a compounding one.

---

## Configuration

Key settings in `.env`:

```env
# Embedding model (local via Ollama)
EMBEDDING_MODEL=nomic-embed-text:latest

# Qdrant
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=azure_wiki

# Chunking
CHUNK_SIZE_TOKENS=400
CHUNK_OVERLAP_TOKENS=60

# Confidence decay half-life in days
CONFIDENCE_DECAY_DAYS=90
```

---

## Roadmap

### v0.1 — Alpha (current)
- [x] Full ingest pipeline (5,816 docs → 63k chunks)
- [x] Hybrid search (BM25 + vector + RRF)
- [x] Architecture generation with Mermaid diagrams with semantic colour coding
- [x] Bicep template generation (on approval only)
- [x] Iterative refinement chat with full conversation history
- [x] User authentication + saved architectures (SQLite)
- [x] Guest mode
- [x] LLM Wiki V2: session crystallisation, chunk reinforcement, Ebbinghaus confidence decay
- [x] Incremental re-ingest with supersession
- [x] Temporal reranking layer (freshness boost, staleness detection, per-source half-life decay)
- [x] Evaluation system: auto-scorer, human feedback (👍/👎), chunk confidence updates
- [x] Evals dashboard
- [x] Regional availability knowledge source (Qatar Central + UAE North)
- [x] One-command launch via start.sh / stop.sh

### v0.2 — Deploy (next)
- [ ] Azure execution engine: Bicep → live deployment via scoped Service Principal
- [ ] Programmatic HITL approval gate
- [ ] Deployment audit log
- [ ] Drift detection
- [ ] Regional availability data weekly auto-update

### v0.3 — Scale
- [ ] Multi-tenant isolation (per-tenant vector namespaces, credential vaults)
- [ ] Graph traversal search (third search stream)
- [ ] Wiki-lint metabolism (scheduled contradiction detection)
- [ ] MSP white-label support
- [ ] Azure Marketplace listing

---

## Known Limitations

- **Regional service availability data** — The auto-scorer may incorrectly flag service availability for mainstream Azure services in newer regions (e.g. Qatar Central). The ingested corpus covers architectural guidance but not the live service-by-region availability matrix. Remediation planned for v0.2: ingest structured regional availability data from the official Microsoft availability table as a dedicated knowledge source, updated weekly via the incremental re-ingest pipeline.

---

## Contributing

Pull requests welcome. Please open an issue first to discuss what you'd like to change.

---

## License

MIT

---

## Acknowledgements

- [Andrej Karpathy](https://github.com/karpathy) — LLM Wiki pattern
- [Microsoft Docs](https://github.com/MicrosoftDocs) — Azure documentation source repos
- [Qdrant](https://qdrant.tech) — vector database
- [nomic-ai](https://nomic.ai) — nomic-embed-text embedding model
