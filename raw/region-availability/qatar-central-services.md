# Azure Service Availability — Qatar Central (qatarcentral)

Qatar Central is a Generally Available Azure region located in Doha, Qatar. It is the primary Azure region for customers requiring data residency within the State of Qatar and serves as a hub for Gulf Cooperation Council (GCC) workloads with strict data sovereignty requirements.

**Region code:** `qatarcentral`  
**Geography:** Qatar  
**Paired region:** UAE North (for disaster recovery)  
**Data residency:** All data remains within Qatar  

---

## Networking — All GA

| Service | Status | Notes |
|---|---|---|
| Azure Virtual Network (VNet) | **GA** | Full feature parity |
| Network Security Groups (NSG) | **GA** | |
| User Defined Routes (UDR) | **GA** | |
| Azure Firewall | **GA** | Standard and Premium SKUs available |
| Azure Firewall Policy | **GA** | |
| Azure Bastion | **GA** | Standard SKU available |
| VPN Gateway | **GA** | All SKUs including HighPerformance and UltraPerformance |
| ExpressRoute | **GA** | ExpressRoute circuits available from local providers |
| ExpressRoute Global Reach | **GA** | |
| Azure Application Gateway | **GA** | v2 SKU (WAF and standard) |
| Azure Load Balancer | **GA** | Standard SKU |
| Azure Private Link | **GA** | |
| Private Endpoints | **GA** | |
| Azure Private DNS Zones | **GA** | |
| Azure DNS | **GA** | |
| Virtual Network Peering | **GA** | Including global peering to UAE North |
| Azure DDoS Protection | **GA** | Standard plan available |
| NAT Gateway | **GA** | |
| Azure Traffic Manager | **GA** | |
| Azure Front Door | **GA** | |
| Azure Content Delivery Network (CDN) | **GA** | |

---

## Compute — All GA

| Service | Status | Notes |
|---|---|---|
| Azure Kubernetes Service (AKS) | **GA** | All node pool types, AGIC supported |
| Azure Virtual Machines | **GA** | Dsv5, Esv5, Fsv2, Lsv3 and more |
| Virtual Machine Scale Sets (VMSS) | **GA** | |
| Azure App Service (Web Apps) | **GA** | All pricing tiers |
| Azure Functions | **GA** | Consumption, Premium, Dedicated plans |
| Azure Container Instances (ACI) | **GA** | |
| Azure Container Apps | **GA** | |
| Azure Batch | **GA** | |

---

## Storage — All GA

| Service | Status | Notes |
|---|---|---|
| Azure Blob Storage | **GA** | LRS, ZRS, GRS (paired to UAE North) |
| Azure Files | **GA** | SMB and NFS |
| Azure Queues | **GA** | |
| Azure Tables | **GA** | |
| Azure Managed Disks | **GA** | Standard HDD, Standard SSD, Premium SSD, Ultra Disk |
| Azure NetApp Files | **GA** | |
| Azure Data Lake Storage Gen2 | **GA** | |

---

## Databases — GA

| Service | Status | Notes |
|---|---|---|
| Azure SQL Database | **GA** | General Purpose, Business Critical tiers |
| Azure SQL Managed Instance | **GA** | |
| Azure Database for PostgreSQL Flexible Server | **GA** | |
| Azure Database for MySQL Flexible Server | **GA** | |
| Azure Cosmos DB | **GA** | NoSQL API GA. MongoDB API GA. Other APIs: check availability |
| Azure Cache for Redis | **GA** | C, P SKUs |
| Azure SQL Server on VMs | **GA** | |

---

## Security and Identity — All GA

| Service | Status | Notes |
|---|---|---|
| Azure Key Vault | **GA** | Standard and Premium tiers; HSM-backed |
| Azure Key Vault Managed HSM | **GA** | |
| Microsoft Entra ID (Azure AD) | **GA** | Global service, endpoints in region |
| Managed Identity | **GA** | System-assigned and user-assigned |
| Azure Policy | **GA** | |
| Azure RBAC | **GA** | |
| Microsoft Defender for Cloud | **GA** | |
| Microsoft Sentinel | **GA** | |
| Microsoft Defender for Endpoint | **GA** | |

---

## Monitoring and Operations — All GA

| Service | Status | Notes |
|---|---|---|
| Azure Monitor | **GA** | |
| Azure Log Analytics | **GA** | |
| Application Insights | **GA** | |
| Azure Alerts | **GA** | |
| Azure Diagnostic Settings | **GA** | |
| Azure Advisor | **GA** | |
| Azure Service Health | **GA** | |
| Azure Automation | **GA** | |
| Azure Update Manager | **GA** | |

---

## AI and Search — Partial

| Service | Status | Notes |
|---|---|---|
| Azure AI Search | **GA** | Formerly Cognitive Search. All tiers including S3 HD. Valid for RAG pipelines. |
| Azure AI Services (multi-service) | **GA** | Includes Vision, Language, Speech APIs |
| Azure OpenAI Service | **NOT AVAILABLE** | Not deployed in Qatar Central. Use UAE North or Sweden Central. Architect via Private Endpoint hub pattern. |
| Azure Machine Learning | **GA** | |
| Azure AI Studio | Preview | Limited feature set |

---

## Integration and Messaging — All GA

| Service | Status | Notes |
|---|---|---|
| Azure Service Bus | **GA** | Standard and Premium tiers |
| Azure Event Hubs | **GA** | Standard and Premium tiers |
| Azure Event Grid | **GA** | |
| Azure Logic Apps | **GA** | Standard and Consumption plans |
| Azure API Management | **GA** | All tiers |

---

## Containers and Registry — All GA

| Service | Status | Notes |
|---|---|---|
| Azure Container Registry (ACR) | **GA** | Basic, Standard, Premium |
| Azure Container Apps | **GA** | |
| Azure Kubernetes Service (AKS) | **GA** | See Compute section |

---

## Architecture Recommendations for Qatar Central

### For RAG / AI Search workloads
- Deploy Azure AI Search in Qatar Central — it is GA and fully supported
- For LLM inference requiring Azure OpenAI: deploy the OpenAI endpoint in UAE North with Private Endpoint access from Qatar Central VNet via VNet peering and Private DNS Zone override
- Use AKS in Qatar Central for orchestration agents (GA)

### For enterprise workloads (PCI-DSS, ISO 27001)
- All required security controls are GA: Key Vault, Private Endpoints, Azure Firewall, Bastion, NSG, Sentinel, Defender for Cloud
- Data never leaves Qatar when using LRS storage and Qatar Central–hosted services
- Use ExpressRoute for hybrid connectivity with on-premises Qatar data centres

### Hub-and-Spoke networking
- Azure Firewall (Premium) as hub NVA — fully GA
- Azure Bastion Standard in hub — fully GA
- VPN Gateway or ExpressRoute for on-premises — fully GA
- Private DNS Zones for all PaaS services — fully GA
