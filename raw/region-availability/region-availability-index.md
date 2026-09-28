---
title: Azure Regional Service Availability — Index
last_verified: 2026-09-28
sources:
  - https://learn.microsoft.com/azure/reliability/regions-list
  - https://learn.microsoft.com/azure/reliability/regions-paired
  - https://learn.microsoft.com/azure/foundry/reference/region-support
  - https://learn.microsoft.com/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure-region-availability
  - https://learn.microsoft.com/azure/search/search-region-support
  - https://learn.microsoft.com/azure/vpn-gateway/gateway-sku-consolidation
---

# Azure Regional Service Availability — Index

This curated source records verified service availability for the Gulf regions TE-1 designs for. The architecture engine and the auto-scorer treat it as ground truth. Every claim below was checked against Microsoft Learn on the `last_verified` date, and the Azure OpenAI rows were also confirmed against a live subscription with `az cognitiveservices model list`.

## Covered regions

| Region | Code | Availability zones | Paired region | File |
|---|---|---|---|---|
| Qatar Central | `qatarcentral` | 3 | **None** (non-paired region) | qatar-central-services.md |
| UAE North | `uaenorth` | 3 | UAE Central (access-restricted) | uae-north-services.md |

## Rules for designing in Gulf regions

1. **Azure OpenAI cannot be deployed in Qatar Central.** Qatar Central supports creating a Microsoft Foundry *project*, but no Azure OpenAI models are available to deploy there. A live model list for `qatarcentral` returns nothing. Never place an Azure OpenAI deployment in Qatar Central.

2. **For LLM inference in a Qatar Central architecture, use UAE North and say so explicitly.** Keep data services (AI Search, Storage, Cosmos DB, Key Vault) and compute in Qatar Central, and call Azure OpenAI in UAE North. State in the design that prompts and responses leave Qatar. If the client requires data to stay in Qatar, flag this as a blocker and ask before proceeding.

3. **Deployment type decides where inference runs.** In UAE North, chat models are available as Global Standard (inference may run in any Azure region) or Regional Provisioned (inference stays in UAE North, needs reserved throughput units). Standard regional deployments in UAE North cover only embeddings and Whisper. There is no Data Zone option for the Middle East. For UAE data residency of inference, recommend Regional Provisioned.

4. **Qatar Central has no paired region.** Resilience comes from its three availability zones. Use zone-redundant SKUs (ZRS storage, zone-redundant gateways, AKS across zones). Cross-region disaster recovery must name a target region explicitly and state that data leaves Qatar.

5. **Core networking is available in both regions.** Azure Firewall, Bastion, VPN Gateway, ExpressRoute, Private Link and Private DNS are all available. Never flag them as unavailable in Qatar Central or UAE North.

6. **New VPN gateways must use AZ SKUs (VpnGw1AZ–VpnGw5AZ).** Non-AZ SKUs (VpnGw1–5) can't be created since November 2025 and retire on 30 September 2026. Never propose non-AZ VpnGw SKUs, or the legacy Standard/HighPerformance SKUs.

7. **Azure AI Search has regional limits.** In Qatar Central it has no AI enrichment (skillsets) and lower storage limits. In UAE North, Microsoft currently blocks creation of new search services due to demand. Check both before recommending AI Search.

8. **Public IPs on Firewall, Bastion and VPN Gateway are by design.** These services need a public IP to work. They are not a violation of a "no public IPs on workloads" constraint.

9. **"Existing Hub VNet: No" means create a new hub.** A hub-spoke design with a new hub is the correct response.

## How to read the service files

- **Verified**: checked against Microsoft Learn on the `last_verified` date.
- **Assumed GA**: a mainstream service expected in every GA region, not checked individually. Confirm SKU-level details in the portal before deployment.
- **Not available**: confirmed unavailable.

## Update cadence

Re-verify at least every 30 days. Set `last_verified` after each review. The Knowledge Base panel blocks rebuilds while this source is older than 30 days.
