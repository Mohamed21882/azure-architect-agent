# Wiki Ingest Log

| Date | Operation | Source Paths | Output | Confidence | Ingested By |
|---|---|---|---|---|---|
| 2026-04-28 | wiki-ingest | `raw/azure-ai/articles/foundry/agents/` (overview, concepts/agent-identity, concepts/hosted-agent-permissions, concepts/standard-agent-setup) · `raw/azure-ai/articles/foundry/concepts/rbac-foundry.md` · `raw/architecture/docs/ai-ml/guide/ai-agent-design-patterns.md` | `wiki/semantic/foundry-agent-service-and-task-adherence.md` | 92/100 | TE-1 |

## Notes
- `raw/azure-ai/articles/ai-foundry/` contained only redirect manifests (`.openpublishing.redirection.ai-studio.json`). Substantive content was sourced from `raw/azure-ai/articles/foundry/` (current canonical path post-rename) and `raw/architecture/`.
- Hosted Agents and Workflow Agents are still in **public preview** as of source date 2026-04-13 to 2026-04-21. Review trigger: any GA announcement for either type.
- `Azure AI Developer` built-in role confirmed insufficient for hosted agent scenarios — documented as a known gotcha.
