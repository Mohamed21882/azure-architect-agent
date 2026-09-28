---
title: Azure Service Availability — UAE North (uaenorth)
region: uaenorth
last_verified: 2026-09-28
sources:
  - https://learn.microsoft.com/azure/reliability/regions-list
  - https://learn.microsoft.com/azure/reliability/regions-paired
  - https://learn.microsoft.com/azure/foundry/reference/region-support
  - https://learn.microsoft.com/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure-region-availability
  - https://learn.microsoft.com/azure/search/search-region-support
  - https://learn.microsoft.com/azure/expressroute/expressroute-locations
  - https://learn.microsoft.com/azure/azure-sql/database/region-availability
  - https://learn.microsoft.com/azure/azure-netapp-files/replication
---

# Azure Service Availability — UAE North (uaenorth)

UAE North is a generally available Azure region in Dubai. It is the Gulf region with Azure OpenAI models, which makes it the inference region for Qatar Central designs too.

- **Region code:** `uaenorth`
- **Availability zones:** 3
- **Paired region:** UAE Central (Abu Dhabi). UAE Central is access-restricted and meant for disaster recovery within the UAE.
- **ExpressRoute locations:** Dubai, Dubai2, Abu Dhabi

## Azure OpenAI (verified)

Confirmed with `az cognitiveservices model list --location uaenorth` on 2026-09-28. Available model families include gpt-6 (astra, luna, sol), gpt-5.6 (sol, terra, luna), gpt-5.5, gpt-5.4 (including mini, nano, pro), gpt-5.x codex models, gpt-4.1, gpt-4o, o1, o3, o3-mini, o4-mini, model-router, gpt-image models, text-embedding-3-large, text-embedding-3-small, text-embedding-ada-002 and whisper. The list depends on the subscription, so re-check before committing.

Deployment type decides where inference runs:

| Deployment type | What's available in UAE North | Where inference runs |
|---|---|---|
| Global Standard | Most chat and reasoning models | Any Azure region where the model is deployed |
| Regional Provisioned | gpt-4.1, gpt-4o, gpt-5-mini, gpt-5.1, gpt-6-sol, o1, o3-mini, o4-mini | UAE North only (needs reserved throughput units) |
| Standard (regional, pay-per-token) | text-embedding-3-large, text-embedding-3-small, text-embedding-ada-002, whisper | UAE North only |
| Data Zone | Not offered for the Middle East | — |

Data at rest stays in the Middle East and Africa geography for all types. For strict UAE residency of chat inference, recommend Regional Provisioned.

## AI and search (verified)

| Service | Status | Notes |
|---|---|---|
| Microsoft Foundry project | Verified | |
| Azure AI Search | Verified, **capacity-constrained** | Supports AI enrichment, agentic retrieval, semantic ranker, serverless and availability zones. Microsoft currently reports high demand that **prevents creating new search services** here. For new designs, place AI Search in Qatar Central or another region and say why. |
| Azure Speech | Verified | |
| Grounding with Bing Search (Foundry Agent Service) | Verified | UAE North is a supported region. |
| Model router | Verified | Global Standard only. |

## Networking

| Service | Status | Notes |
|---|---|---|
| Virtual Network, NSG, UDR, VNet peering, Virtual WAN | Assumed GA | |
| Azure Firewall, Bastion | Assumed GA | Need public IPs by design. |
| VPN Gateway | Verified | AZ SKUs only for new gateways (VpnGw1AZ–VpnGw5AZ). |
| ExpressRoute | Verified | Dubai, Dubai2, Abu Dhabi. |
| Private Link, Private Endpoints, Private DNS, Application Gateway v2, Load Balancer, NAT Gateway | Assumed GA | |

## Compute, storage and databases

| Service | Status | Notes |
|---|---|---|
| AKS, VMs, App Service, Functions, Container Apps, Container Registry | Assumed GA | |
| Blob, Files, Data Lake Gen2, Managed Disks | Assumed GA | GRS replicates to UAE Central, so data stays in the UAE. |
| Azure NetApp Files | Verified | Cross-region replication pairs UAE North with **Sweden Central**, so replicated data leaves the Gulf. |
| Azure SQL Database | Verified | Serverless supported, including zone redundancy at 80 vCores. |
| SQL Managed Instance, PostgreSQL Flexible, MySQL Flexible, Cosmos DB, Redis | Assumed GA | Confirm API and tier support in the portal. |

## Security and monitoring

| Service | Status | Notes |
|---|---|---|
| Key Vault, Managed HSM, Managed Identity, Policy, RBAC | Assumed GA | |
| Defender for Cloud, Sentinel, Azure Monitor, Log Analytics, Application Insights | Assumed GA | |

## Design guidance for UAE North

- For RAG with UAE data residency, keep all components in UAE North and use Regional Provisioned for chat models, with Standard regional embeddings.
- New AI Search services currently can't be created here. Check before designing around it.
- When serving a Qatar Central architecture, UAE North hosts the model endpoints. Connect over private endpoints and cross-region VNet peering, and document the cross-border data flow.
