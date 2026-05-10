# Foundry Agent Service — Architecture & Task Adherence Patterns

**Wiki Tier:** Semantic (Theory & Concepts)
**Confidence Score:** 92 / 100
**Source Corpus:** `raw/azure-ai/articles/foundry/agents/` · `raw/architecture/docs/ai-ml/guide/ai-agent-design-patterns.md`
**Ingested:** 2026-04-28
**Supersedes:** *(none — first entry)*
**Review Trigger:** Any release note in `raw/azure-ai/articles/foundry/agents/` that changes agent types, RBAC role definitions, or orchestration primitives.

---

## 1. What is Foundry Agent Service?

Foundry Agent Service (FAS) is Microsoft's **fully managed platform** for building, deploying, and scaling AI agents inside Microsoft Foundry. It abstracts hosting, scaling, identity, observability, and enterprise security so that practitioners focus solely on agent logic.

Every agent is composed of three primitive elements:

| Element | Role |
|---|---|
| **Model (LLM)** | Reasoning and language — e.g. GPT-4o, Llama, DeepSeek |
| **Instructions** | Goals, constraints, behavior — prompt, YAML workflow, or container code |
| **Tools** | Capability extensions — web search, file search, MCP servers, custom functions |

---

## 2. Agent Type Taxonomy

FAS exposes three distinct agent types. Selecting the wrong type is the primary source of over-engineering in early pilots.

| Type | Code Required | Hosting | Orchestration | Best For |
|---|---|---|---|---|
| **Prompt Agent** | No | Fully managed | Single-agent, portal-configured | Rapid prototyping, simple tools |
| **Workflow Agent** (preview) | No (YAML optional) | Fully managed | Multi-agent, branching, HITL steps | Repeatable multi-step automation |
| **Hosted Agent** (preview) | Yes | Container-based, managed | Custom logic, any framework | Full control, complex orchestration |

> **TE-1 Pilot Recommendation:** Start with a **Prompt Agent** for functional validation. Promote to **Hosted Agent** (using Microsoft Agent Framework or LangGraph) only after HITL patterns and tool permissions are confirmed in DEV.

---

## 3. Agent Service Architecture (Standard Setup)

Standard Setup gives you **full data sovereignty** using customer-managed, single-tenant resources. This is the correct posture for any enterprise or regulated pilot.

### 3.1 Resource Topology

```
Foundry Account
├── Model Deployment  (e.g. gpt-4o)
└── Foundry Project  ──── has System/User-Assigned Managed Identity
    ├── Agent (Prompt / Hosted / Workflow)
    │   └── Agent Identity (distinct Entra ID service principal post-publish)
    ├── Connection → Azure Container Registry  (hosted agents)
    └── Connection → Application Insights  (telemetry)

Customer-Managed BYO Resources:
├── Azure Cosmos DB for NoSQL  — thread / message / agent-metadata storage
├── Azure Storage Account      — uploaded files & intermediate data
└── Azure AI Search            — vector stores
```

### 3.2 Capability Hosts

Capability hosts are sub-resources that wire the Foundry project to BYO resources:

- **Account capability host** — signals `capabilityHostKind = "Agents"` at account level.
- **Project capability host** — declares which Cosmos DB, Storage, and AI Search connections to use. **Cannot be updated after creation.** Delete and recreate the project if reconfiguration is needed.

### 3.3 Cosmos DB Throughput Sizing

| Container | Purpose | Min RU/s |
|---|---|---|
| `thread-message-store` | End-user conversations | 1 000 |
| `system-thread-message-store` | Internal system messages | 1 000 |
| `agent-entity-store` | Agent config (instructions, tools, name) | 1 000 |

**Formula:** `Total RU/s ≥ 3 000 × number_of_projects`

---

## 4. Orchestration Patterns (Multi-Agent)

The following patterns are technology-agnostic and apply to FAS Workflow Agents, FAS Connected Agents, Microsoft Agent Framework, Semantic Kernel, LangChain, and CrewAI.

### Complexity Ladder — Start Low

Before adopting multi-agent orchestration, evaluate against this hierarchy:

1. **Direct model call** — single prompt, no agent logic. Correct for classification, summarization, translation.
2. **Single agent with tools** — one agent, multiple tool invocations, iteration loops. Default for most enterprise use cases.
3. **Multi-agent orchestration** — justified only when cross-domain security boundaries, prompt complexity, or parallelism *cannot* be achieved by a single agent.

### 4.1 Sequential (Pipeline)

Agents execute in a **predefined linear order**; each agent processes the output of the previous one.

- Routing: **deterministic**.
- Use when: step-by-step dependencies, progressive refinement (draft → review → polish), predictable pipeline.
- Avoid when: stages are parallelizable, workflow requires backtracking, early-stage failures cascade.

### 4.2 Concurrent (Fan-out / Fan-in)

Multiple agents process the **same input simultaneously**; results are aggregated.

- Routing: deterministic or dynamic agent selection.
- Aggregation strategies: majority voting, weighted scoring, LLM-synthesized summary.
- Use when: independent perspectives are needed, latency-sensitive scenarios.
- Avoid when: agents require each other's output, results conflict without a resolution strategy.

### 4.3 Group Chat (Roundtable)

Agents collaborate in a **shared conversation thread** managed by a Chat Manager that controls turn order.

- Agents operate in read-only mode (no direct tool mutations to external systems).
- Supports human-in-the-loop (HITL) participation.
- Limit to ≤ 3 agents to maintain conversation control.
- **Maker-Checker Loop** is a formal subset: Maker proposes → Checker evaluates → iterate until acceptance criteria are met or iteration cap is reached.

### 4.4 Handoff (Routing / Triage)

A single active agent **dynamically transfers control** to a more capable agent based on context.

- Only one agent active at a time.
- Use when: required specialization emerges during processing, not predictable from the initial input.
- Avoid when: routing is deterministic (use a dispatcher instead); risk of infinite handoff loops.

### 4.5 Magentic (Task-Ledger Orchestration)

A Manager agent **builds and refines a task ledger** through collaboration with specialist agents before executing. Agents in this pattern actively mutate external systems via tools.

- Ledger contains: goals, sub-goals, task statuses, execution history (full audit trail).
- Manager continuously re-evaluates whether the goal is satisfied; backtracks and reassigns as needed.
- Use when: open-ended problems, no predetermined solution path, human review of the plan is required before execution.
- Avoid when: solution path is deterministic, task is time-sensitive, stalls are hard to detect.

### Pattern Comparison Matrix

| Pattern | Routing | Agents Active | External Mutations | HITL Support | Watch Out For |
|---|---|---|---|---|---|
| Sequential | Deterministic | One at a time | Yes | Optional | Cascade failures |
| Concurrent | Deterministic/dynamic | All simultaneously | Yes | Optional | Conflict resolution |
| Group Chat | Chat Manager | All (read-only) | No | Native | Loop control |
| Handoff | Agent decides | One at a time | Yes | Optional | Infinite routing loops |
| Magentic | Manager assigns | Manager + delegatees | Yes | Native (plan review gate) | Slow convergence |

---

## 5. Task Adherence Patterns

Task adherence is the set of mechanisms that prevent an agent from drifting, looping, or exceeding its authorized scope. This directly maps to TE-1's HITL Constitution requirement.

### 5.1 Iteration Limits

- Single-agent: set a maximum tool-call loop count to prevent infinite reasoning spirals.
- Multi-agent: cap magentic manager iterations; define fallback on cap breach (escalate to human or return best-effort result with quality warning).

### 5.2 Output Validation Before Propagation

- The orchestrator or receiving agent **validates output quality** before passing it downstream.
- For low-confidence, malformed, or off-topic responses: retry, request clarification, or halt the workflow.
- Do not silently propagate bad inputs through a pipeline.

### 5.3 Maker-Checker as a HITL Gate

- Formalizes the HITL requirement: the Checker agent enforces acceptance criteria.
- Requires **explicit acceptance criteria** so the Checker makes consistent pass/fail decisions.
- Mandatory iteration cap + fallback (escalate to human reviewer) if cap is reached.

### 5.4 Context Compaction

- Multi-agent context windows grow rapidly. Monitor accumulated size between agent transitions.
- Compact via summarization or selective pruning before handing off to the next agent.
- For long-running or multi-session tasks: persist shared state externally (Cosmos DB), not in-memory.

### 5.5 Security Trimming

- Every agent in the chain enforces security trimming independently — shared context ≠ shared access.
- Apply content safety guardrails at: user input, tool calls, tool responses, final output.

### 5.6 Content Safety Guardrails

- Foundry's integrated guardrails mitigate prompt injection risks including **cross-prompt injection attacks (XPIA)**.
- Apply at multiple points in the orchestration chain, not only at the perimeter.

### 5.7 Circuit Breaker Pattern

- Treat agent dependencies the same as distributed service dependencies.
- Implement timeout, retry, and circuit breaker per agent; surface errors upstream rather than swallowing them.
- Design for compute isolation between agents to prevent shared rate-limit failures under concurrent patterns.

---

## 6. JIT Role Assignments for TE-1 Agent ID — Pilot

The following table is the **minimum JIT role set** required to register and operate TE-1's Agent Identity in a DEV Foundry Project. These assignments follow the principle of least privilege and are scoped tightly to the pilot resource group.

> **HITL Gate:** Role assignments must not be applied until `EXECUTION APPROVED` is received from the client.

### 6.1 Human Operators (TE-1 Architect / Developer)

| Role | Scope | Why Required |
|---|---|---|
| **Azure AI Project Manager** | Foundry Project | Create/update agents (data plane write); assign `Azure AI User` to the agent identity |
| **Azure AI User** | Foundry Project | Build, test, and interact with agents in the playground |
| **Owner** or **Role Based Access Control Administrator** | Resource Group | Required to create role assignments on ACR, Log Analytics, and other non-Foundry resources |

> **Caution:** `Azure AI Developer` is **insufficient** for hosted agent scenarios — it targets Azure Machine Learning hub scopes, not Foundry project resources.

### 6.2 TE-1 Agent Identity (Unpublished — Shared Project Identity)

Unpublished (DEV) agents share the project's managed identity. The following roles are automatically assigned by `azd` or must be manually assigned:

| Role | Scope | Why Required |
|---|---|---|
| **Azure AI User** | Foundry Account | Model inferencing through the project endpoint |
| **Azure AI User** | Foundry Project | Data plane operations (create threads, invoke tools, read agent config) |

### 6.3 TE-1 Agent Identity (Published — Distinct Identity)

When TE-1 is promoted from DEV to a published Agent Application, a **new distinct `agentIdentityId`** is created. Roles from the shared project identity **do not carry over** — the following must be re-assigned to the new identity:

| Role | Scope | Why Required |
|---|---|---|
| **Azure AI User** | Foundry Project | Model inferencing + agent interaction under distinct identity |
| **Storage Blob Data Contributor** | Storage Account (`<workspaceId>-azureml-blobstore` container) | File reads/writes |
| **Storage Blob Data Owner** | Storage Account (`<workspaceId>-agents-blobstore` container) | Agent-managed file storage |
| **Search Index Data Contributor** | Azure AI Search resource | Vector store operations |
| **Search Service Contributor** | Azure AI Search resource | Search index management |
| **Cosmos DB Built-in Data Contributor** | Cosmos DB `enterprise_memory` database | Thread / message / metadata read-write |

### 6.4 Project Managed Identity (Infrastructure Roles)

| Role | Scope | Why Required |
|---|---|---|
| **Azure AI User** | Foundry Account | Project proxies inference calls to the account-level deployment |
| **Container Registry Repository Reader** | ACR registry | Pull hosted-agent container images at runtime |
| **Log Analytics Data Reader** | Log Analytics Workspace | Read telemetry for evaluations |
| **Cosmos DB Operator** | Cosmos DB Account | Provision containers during capability host setup |
| **Storage Account Contributor** | Storage Account | Provision blob containers during capability host setup |

### 6.5 Optional — Direct Account-Level Access

Only required if agent code **bypasses the project endpoint** and calls the account-level endpoint directly:

| Role | Scope | Covers |
|---|---|---|
| **Cognitive Services OpenAI User** | Foundry Account | OpenAI data actions only |
| **Cognitive Services User** | Foundry Account | Speech, Vision, Language, Translator (non-OpenAI) |
| **Azure AI User** | Foundry Account | All CognitiveServices data actions (single grant covering both above) |

---

## 7. Agent Identity Lifecycle Summary

```
DEV (Unpublished)
└── Shared project agent identity blueprint + shared agent identity
    └── Auth chain: Project Managed Identity → Blueprint → Agent Identity → Downstream Resource Token

PROD (Published)
└── Distinct agent identity blueprint + distinct agent identity (bound to Agent Application resource)
    └── Same auth chain but with NEW agentIdentityId
    └── All RBAC role assignments MUST be re-applied to the new identity
```

**Identity governance entry point:** `Entra Admin Center → Entra ID → Agent ID → All agent identities`
Available controls: Conditional Access, Identity Protection, Network Access, Governance (expiration, owners, sponsors).

---

## 8. Key Architectural Decisions for TE-1 Pilot

| Decision | Recommendation | Rationale |
|---|---|---|
| Agent type for pilot | Prompt Agent | Lowest complexity; validate HITL and tool permissions before containerizing |
| Setup mode | Standard (BYO resources) | Full data sovereignty, compliance alignment |
| Orchestration pattern | Magentic (with mandatory HITL plan-review gate) | Matches TE-1's plan → EXECUTION APPROVED → execute Constitution |
| Context persistence | External (Cosmos DB) | Multi-session, long-running architectural tasks |
| Identity model | Shared project identity in DEV → distinct identity on PROD publish | Prevents identity sprawl during iteration; clean blast radius separation |
| HITL gate location | Before any tool call that mutates cloud resources | Maps directly to TE-1's `EXECUTION APPROVED` protocol |

---

*End of semantic entry. Next Wiki V2 action: after first DEV pilot deployment, update `wiki/procedural/` with validated provisioning scripts and observed role assignment edge cases.*
