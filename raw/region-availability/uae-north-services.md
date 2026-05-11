# Azure Service Availability — UAE North (uaenorth)

UAE North is a Generally Available Azure region located in Dubai, United Arab Emirates. It is the most mature Azure region in the Gulf, launched in 2019, and offers the broadest service coverage in the GCC geography. It is the recommended primary region for UAE-based workloads and the disaster recovery pair for Qatar Central.

**Region code:** `uaenorth`  
**Geography:** United Arab Emirates  
**Paired region:** UAE Central (for disaster recovery)  
**Data residency:** All data remains within the UAE  

---

## Networking — All GA

| Service | Status | Notes |
|---|---|---|
| Azure Virtual Network (VNet) | **GA** | Full feature parity |
| Network Security Groups (NSG) | **GA** | |
| User Defined Routes (UDR) | **GA** | |
| Azure Firewall | **GA** | Standard and Premium SKUs |
| Azure Firewall Policy | **GA** | |
| Azure Bastion | **GA** | Standard and Developer SKUs |
| VPN Gateway | **GA** | All SKUs |
| ExpressRoute | **GA** | Multiple local providers; DE-CIX Frankfurt peering |
| ExpressRoute Global Reach | **GA** | |
| Azure Application Gateway | **GA** | v2 WAF and standard |
| Azure Load Balancer | **GA** | Standard SKU |
| Azure Private Link | **GA** | |
| Private Endpoints | **GA** | |
| Azure Private DNS Zones | **GA** | |
| Azure DDoS Protection | **GA** | |
| NAT Gateway | **GA** | |
| Azure Virtual WAN | **GA** | |
| Azure Traffic Manager | **GA** | |
| Azure Front Door | **GA** | |

---

## Compute — All GA

| Service | Status | Notes |
|---|---|---|
| Azure Kubernetes Service (AKS) | **GA** | All features including AGIC, Workload Identity |
| Azure Virtual Machines | **GA** | Broad SKU availability |
| Virtual Machine Scale Sets | **GA** | |
| Azure App Service | **GA** | All tiers |
| Azure Functions | **GA** | All plans |
| Azure Container Instances | **GA** | |
| Azure Container Apps | **GA** | |
| Azure Batch | **GA** | |
| Azure Dedicated Hosts | **GA** | |

---

## Storage — All GA

| Service | Status | Notes |
|---|---|---|
| Azure Blob Storage | **GA** | LRS, ZRS, GRS (paired to UAE Central) |
| Azure Files | **GA** | |
| Azure Queues | **GA** | |
| Azure Tables | **GA** | |
| Azure Managed Disks | **GA** | All tiers including Ultra Disk |
| Azure NetApp Files | **GA** | |
| Azure Data Lake Storage Gen2 | **GA** | |

---

## Databases — All GA

| Service | Status | Notes |
|---|---|---|
| Azure SQL Database | **GA** | All tiers |
| Azure SQL Managed Instance | **GA** | |
| Azure Database for PostgreSQL Flexible Server | **GA** | |
| Azure Database for MySQL Flexible Server | **GA** | |
| Azure Cosmos DB | **GA** | All APIs: NoSQL, MongoDB, Cassandra, Gremlin, Table |
| Azure Cache for Redis | **GA** | All SKUs |
| Azure Synapse Analytics | **GA** | |

---

## Security and Identity — All GA

| Service | Status | Notes |
|---|---|---|
| Azure Key Vault | **GA** | Standard, Premium, Managed HSM |
| Microsoft Entra ID | **GA** | |
| Managed Identity | **GA** | |
| Azure Policy | **GA** | |
| Azure RBAC | **GA** | |
| Microsoft Defender for Cloud | **GA** | |
| Microsoft Sentinel | **GA** | |
| Microsoft Defender for Endpoint | **GA** | |
| Azure Confidential Computing | **GA** | DCsv3, DCdsv3 VMs |

---

## Monitoring — All GA

| Service | Status | Notes |
|---|---|---|
| Azure Monitor | **GA** | |
| Azure Log Analytics | **GA** | |
| Application Insights | **GA** | |
| Azure Alerts | **GA** | |
| Azure Automation | **GA** | |

---

## AI and Machine Learning — Strong Coverage

| Service | Status | Notes |
|---|---|---|
| Azure OpenAI Service | **GA** | GPT-4o, GPT-4, GPT-35-Turbo, text-embedding-ada-002. Key differentiator vs Qatar Central. |
| Azure AI Search | **GA** | All tiers |
| Azure AI Services (multi-service) | **GA** | Vision, Language, Speech, Translator |
| Azure Machine Learning | **GA** | |
| Azure AI Studio | **GA** | |
| Azure AI Foundry | **GA** | |
| Azure Document Intelligence | **GA** | |
| Azure Bot Service | **GA** | |

---

## Integration — All GA

| Service | Status | Notes |
|---|---|---|
| Azure Service Bus | **GA** | All tiers |
| Azure Event Hubs | **GA** | All tiers |
| Azure Event Grid | **GA** | |
| Azure Logic Apps | **GA** | |
| Azure API Management | **GA** | All tiers |
| Azure Data Factory | **GA** | |

---

## Containers — All GA

| Service | Status | Notes |
|---|---|---|
| Azure Container Registry | **GA** | All tiers |
| Azure Container Apps | **GA** | |
| Azure Kubernetes Service | **GA** | |

---

## Architecture Recommendations for UAE North

### Azure OpenAI in the Gulf
Both UAE North and Qatar Central are supported Foundry project regions with Azure OpenAI GA. UAE North has a more mature deployment with broader model availability. When designing for Qatar Central, Azure OpenAI can be deployed locally — verify model quota availability per subscription before committing to the region.

### For RAG architectures with Gulf data residency
Deploy all components — AI Search, Azure OpenAI, and orchestration — within the same region (Qatar Central or UAE North) to satisfy data residency requirements. Cross-region patterns are only required if a specific model or quota is unavailable in the target region.

### Hub-and-spoke in UAE North
- Mature region with full hub networking support
- Azure Virtual WAN available for large enterprise topologies
- ExpressRoute from DU, Etisalat, and international carrier PoPs

### Compliance
UAE North supports ISO 27001, PCI-DSS, SOC 1/2/3, and GDPR compliance frameworks. UAE Central Cybersecurity Council (UAE CSSA) requirements are met by UAE North deployments.
