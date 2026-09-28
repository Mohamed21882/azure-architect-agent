---
title: Azure Service Availability — Qatar Central (qatarcentral)
region: qatarcentral
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

# Azure Service Availability — Qatar Central (qatarcentral)

Qatar Central is a generally available Azure region in Doha, used for workloads that need data residency in Qatar.

- **Region code:** `qatarcentral`
- **Availability zones:** 3
- **Paired region:** none. Qatar Central is a non-paired region and relies on availability zones for resilience.
- **ExpressRoute locations:** Doha, Doha2

## AI and search (verified)

| Service | Status | Notes |
|---|---|---|
| Azure OpenAI models | **Not available** | No Azure OpenAI models can be deployed in Qatar Central (confirmed with `az cognitiveservices model list --location qatarcentral`, which returns nothing). Use UAE North for inference. See the index rules. |
| Microsoft Foundry project | Verified | You can create a Foundry project here, but models must be deployed from a region that has them. |
| Azure AI Search | Verified, with limits | Semantic ranker, agentic retrieval, query rewrite and availability zones are supported. **No AI enrichment (skillsets)**, no serverless, and higher storage limits are not available. Plan chunking and embedding outside the search service. |
| Azure Speech | Verified | Qatar Central is a supported Speech region. |

## Networking

| Service | Status | Notes |
|---|---|---|
| Virtual Network, NSG, UDR, VNet peering | Assumed GA | Core networking. |
| Azure Firewall (Standard, Premium) | Assumed GA | Needs a public IP by design. |
| Azure Bastion | Assumed GA | Needs a public IP and a /26 AzureBastionSubnet by design. |
| VPN Gateway | Verified | New gateways must use AZ SKUs (VpnGw1AZ–VpnGw5AZ). Non-AZ SKUs can't be created and retire on 30 September 2026. |
| ExpressRoute | Verified | Peering locations Doha and Doha2. |
| Private Link, Private Endpoints, Private DNS Zones | Assumed GA | |
| Application Gateway v2, Load Balancer Standard, NAT Gateway, DDoS Protection | Assumed GA | |

Front Door, Traffic Manager and CDN are global services, not regional ones, so they don't appear here.

## Compute and containers

| Service | Status | Notes |
|---|---|---|
| AKS | Assumed GA | Spread node pools across the 3 zones. |
| Virtual Machines, VM Scale Sets | Assumed GA | Check the specific VM series in the portal; availability varies by SKU. |
| App Service, Functions, Container Apps, Container Instances | Assumed GA | |
| Container Registry | Assumed GA | |

## Storage

| Service | Status | Notes |
|---|---|---|
| Blob, Files, Queues, Tables, Data Lake Gen2 | Assumed GA | Use **LRS or ZRS** for data that must stay in Qatar. Qatar Central has no paired region, so don't assume GRS replicates to UAE North. Confirm any geo-redundant option in the portal before using it. |
| Managed Disks | Assumed GA | |
| Azure NetApp Files | Verified | Cross-region replication pairs Qatar Central with **West Europe**, so replicated data leaves the Gulf. |

## Databases

| Service | Status | Notes |
|---|---|---|
| Azure SQL Database | Verified | Serverless supported, up to 80 vCores. |
| SQL Managed Instance, PostgreSQL Flexible, MySQL Flexible, Cosmos DB, Azure Cache for Redis | Assumed GA | Confirm API and tier support in the portal. |

## Security and monitoring

| Service | Status | Notes |
|---|---|---|
| Key Vault, Managed HSM, Managed Identity, Policy, RBAC | Assumed GA | Microsoft Entra ID is a global service. |
| Defender for Cloud, Sentinel, Azure Monitor, Log Analytics, Application Insights | Assumed GA | |

## Design guidance for Qatar Central

### RAG and AI workloads
- Host AI Search, Storage, Cosmos DB, Key Vault and AKS in Qatar Central.
- Call Azure OpenAI in UAE North. State clearly that prompts and responses leave Qatar.
- For UAE-resident inference, use a Regional Provisioned deployment in UAE North. Global Standard may process data in any Azure region.
- Because AI Search here has no skillsets, run chunking and embedding in the application (for example in AKS) and push documents to the index.
- If the client requires that no data leaves Qatar, the LLM layer is a blocker. Flag it rather than designing around it silently.

### Resilience
- Use zone-redundant SKUs throughout. Qatar Central has no paired region.
- Any cross-region disaster recovery must name its target region and note that data leaves Qatar.

### Hybrid connectivity
- ExpressRoute from Doha or Doha2, or VPN Gateway with an AZ SKU.
