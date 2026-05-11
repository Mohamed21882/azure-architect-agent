# Azure Regional Service Availability — Index

This knowledge source documents which Azure services are Generally Available (GA) in specific Azure regions. It is used by the TE-1 auto-scorer and architecture engine to validate service selection against the target deployment region.

## Covered Regions

| Region | Geography | Region Code | File |
|---|---|---|---|
| Qatar Central | Qatar | qatarcentral | qatar-central-services.md |
| UAE North | United Arab Emirates | uaenorth | uae-north-services.md |

## How to Read This Data

- **GA** — Generally Available. Production-safe. SLA-backed.
- **Preview** — Public or Private Preview. Not SLA-backed. Do not recommend for production.
- **Not Available** — Service not deployed to this region. Architect must select an alternative region for this component, or use a paired region with data residency controls.

## Architecture Guidance for Gulf Regions

When designing for Qatar Central or UAE North, apply these rules:

1. **Data residency is the primary constraint.** Both Qatar Central and UAE North offer local data residency guarantees. If data must not leave the Gulf Cooperation Council (GCC) geography, use Qatar Central or UAE North. Do not design cross-region replication to non-Gulf regions without explicit client approval.

2. **Core networking services are fully GA in both regions.** Azure Firewall, VPN Gateway, ExpressRoute, Azure Bastion, Private Link, Private DNS Zones, Application Gateway, and Azure Load Balancer are all GA. Never flag these services as unavailable for Qatar Central or UAE North.

3. **AKS is GA in both regions.** Azure Kubernetes Service is available in Qatar Central and UAE North. It is a valid compute choice for container-based workloads in both regions.

4. **AI Search is GA in Qatar Central.** Azure AI Search (formerly Cognitive Search) is generally available in Qatar Central and is a valid component for RAG pipelines and enterprise search workloads.

5. **Azure OpenAI and Microsoft Foundry: Qatar Central is a supported Foundry project region.** Azure OpenAI is available — verify specific model quota availability per subscription at the Azure OpenAI quotas page before deployment, as quota is allocated per region per subscription.

6. **Storage, Key Vault, and SQL are fully GA in both regions.** No availability concerns for these foundational services.

## Update Cadence

This knowledge source is updated monthly as part of the incremental re-ingest pipeline. Service availability changes are sourced from the official Azure products by region page.
