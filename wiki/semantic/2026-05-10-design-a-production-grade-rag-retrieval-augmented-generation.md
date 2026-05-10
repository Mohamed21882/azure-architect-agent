---
title: Design A Production-Grade Rag (Retrieval-Augmented Generation) Infrastructure In
date: 2026-05-10T19:10:57.672769
region: Qatar Central
compliance: None
confidence: 0.85
source_chunks: ['33ab7a83-55aa-5cd5-9f05-9298c096dd3d', '4e5bff2a-f8dc-556b-846b-fd58e7ee83a9', '8298ce8f-dfcf-56ca-914e-73b9e794f800', 'e12835f3-e081-5e7f-affc-96fe4e763e0b', 'c2b2c679-fad0-5ccc-bdb3-485296d46fe0', '1898fdd4-2177-553f-8915-a4c1c280cdaf']
entity_types: []
tags: ['Qatar Central', 'None', 'design', 'a', 'production', 'grade', 'rag', 'retrieval', 'augmented', 'generation']
---

## Brief

Design a production-grade RAG (Retrieval-Augmented Generation) infrastructure in Qatar Central. Use a Hub-Spoke VNet topology. The Spoke should contain an AKS cluster for AI agents and an Azure AI Search instance. The Hub should have an Azure Firewall and a VPN Gateway. Ensure all backend data services use Private Endpoints.
Budget: Under $5k

## Architecture

# Azure RAG Infrastructure – Qatar Central

## Architecture Summary

### Component Overview
This production-grade RAG infrastructure implements a **Hub-Spoke VNet topology** in Qatar Central, optimised for AI agent workloads with strict cost and security controls. The architecture isolates AI compute and search services in a dedicated spoke while centralising network security and gateway functions in the hub.

**Hub VNet (10.0.0.0/16):**
- Azure Firewall (for egress filtering and inter-VNet routing)
- VPN Gateway (for hybrid connectivity)
- Gateway Subnet, Firewall Subnet, Bastion Subnet

**Spoke VNet (10.1.0.0/16):**
- AKS Cluster (system and user node pools)
- Azure AI Search instance
- Private Endpoints for all backend services
- Dedicated subnets for compute and data services

**Data Services (Private Endpoints only):**
- Azure Storage Account (for RAG document corpus)
- Azure Cosmos DB (for vector embeddings cache)
- Azure Key Vault (for credential management)
- Azure Container Registry (for AKS image pulls)

### Network Topology
- **VNet Peering:** Hub ↔ Spoke (bidirectional, transit enabled)
- **Routing:** All spoke egress routes through Hub Firewall via User Defined Routes (UDRs)
- **Private Endpoints:** All backend services consume via private DNS zones (no public IPs)
- **NSGs:** Granular rules per subnet (AKS ingress from Bastion, AI Search from AKS only)
- **DNS Resolution:** Private DNS zones linked to both Hub and Spoke VNets

### Security Best Practices
- **Network Segmentation:** Dedicated subnets for AKS system/user nodes, AI Search, private endpoints, and gateway functions
- **Zero Public IPs:** All resources (VMs, containers, services) communicate via private IPs; no public endpoints exposed
- **Azure Firewall:** Centralized egress filtering; deny-by-default outbound rules; allow only required destinations (Azure services, package registries)
- **Private Endpoints:** All data services (Storage, Cosmos DB, Key Vault, ACR) accessed exclusively via private endpoints in dedicated subnet
- **RBAC:** Managed identities for AKS pods; Key Vault access via Azure AD authentication
- **Azure Bastion:** Jumphost in Hub for secure RDP/SSH to AKS nodes (no public IPs on nodes)
- **NSG Rules:** Explicit allow rules; AKS ingress from Bastion only; AI Search ingress from AKS subnet only
- **Encryption in Transit:** TLS 1.2+ enforced on all private endpoints; VPN Gateway uses IPSec
- **Encryption at Rest:** Storage Account encryption (Microsoft-managed keys); Cosmos DB encryption enabled; Key Vault for secret rotation
- **Azure Defender for Cloud:** Enable for AKS, Storage, and Key Vault; continuous vulnerability scanning
- **Azure Sentinel:** Ingest NSG flow logs and Firewall logs for threat detection
- **Pod Security Policies:** Enforce in AKS (no privileged containers, read-only root filesystem)
- **Network Policies:** Calico CNI for AKS; restrict pod-to-pod traffic (AI agent pods → AI Search only)
- **Audit Logging:** Enable diagnostic settings on Firewall, VPN Gateway, NSGs, and Key Vault; ship to Log Analytics

### Compliance Considerations
- **No explicit compliance framework specified** (constraint: "None"); architecture follows Azure Well-Architected Framework security pillar
- **Data Residency:** All resources deployed in Qatar Central (single region)
- **Audit Trail:** All control plane and data plane actions logged to Log Analytics Workspace
- **Access Reviews:** Quarterly RBAC reviews via Azure AD Privileged Identity Management (PIM)

### Cost Optimisation Notes
- **Budget Ceiling:** $5k/month
- **Cost Drivers & Mitigation:**
  - **AKS:** Use B-series VMs (burstable) for node pools; enable cluster autoscaling; reserved instances for baseline capacity
  - **AI Search:** Standard tier (not Premium); optimize replica count (1 replica minimum); disable unused features
  - **Storage:** Cool tier for RAG document archive; lifecycle policies to move old data to Archive
  - **Cosmos DB:** Serverless mode (pay-per-request) for variable workloads; auto-scale RU limits
  - **Networking:** Single VPN Gateway (not redundant); no ExpressRoute (cost-prohibitive for $5k budget)
  - **Monitoring:** Log Analytics retention 30 days (not 90+); Application Insights sampling at 10%
  - **Compute:** Spot VMs for non-critical node pools (up to 70% savings)
  - **Egress:** Minimize cross-region traffic; use Azure CDN for static RAG assets if needed

---

```mermaid
graph TB
    subgraph Hub["Hub VNet (10.0.0.0/16)"]
        FW["Azure Firewall"]:::security
        VPNGW["VPN Gateway"]:::network
        Bastion["Azure Bastion"]:::security
        GWSubnet["Gateway Subnet"]:::network
        FWSubnet["Firewall Subnet"]:::network
        BastionSubnet["Bastion Subnet"]:::network
    end

    subgraph Spoke["Spoke VNet (10.1.0.0/16)"]
        AKS["AKS Cluster"]:::compute
        AKSSystem["System NodePool"]:::compute
        AKSUser["User NodePool"]:::compute
        AISrch["AI Search"]:::storage
        AKSSubnet["AKS Subnet"]:::network
        SearchSubnet["Search Subnet"]:::network
        PESubnet["Private Endpoint Subnet"]:::network
    end

    subgraph DataServices["Data Services (Private Endpoints)"]
        Storage["Storage Account"]:::storage
        CosmosDB["Cosmos DB"]:::storage
        KV["Key Vault"]:::security
        ACR["Container Registry"]:::storage
    end

    subgraph Monitoring["Monitoring & Logging"]
        LogAnalytics["Log Analytics"]:::monitor
        AppInsights["App Insights"]:::monitor
        Sentinel["Sentinel"]:::monitor
    end

    subgraph DNS["Private DNS Zones"]
        StorageDNS["storage.dns"]:::dns
        CosmosDNS["cosmos.dns"]:::dns
        KVDNS["vault.dns"]:::dns
        ACRDNS["acr.dns"]:::dns
    end

    %% Hub Internal
    GWSubnet --> VPNGW
    FWSubnet --> FW
    BastionSubnet --> Bastion

    %% Spoke Internal
    AKSSubnet --> AKS
    AKS --> AKSSystem
    AKS --> AKSUser
    SearchSubnet --> AISrch
    PESubnet --> Storage
    PESubnet --> CosmosDB
    PESubnet --> KV
    PESubnet --> ACR

    %% Hub-Spoke Peering
    FW -.->|VNet Peering| AKS
    VPNGW -.->|VNet Peering| AKS

    %% AKS to Data Services
    AKS -->|Private Endpoint| Storage
    AKS -->|Private Endpoint| CosmosDB
    AKS -->|Private Endpoint| KV
    AKS -->|Private Endpoint| ACR
    AISrch -->|Private Endpoint| Storage

    %% Egress via Firewall
    AKS -->|UDR| FW
    AISrch -->|UDR| FW

    %% Bastion Access
    Bastion -->|SSH/RDP| AKSSystem

    %% DNS Resolution
    Storage -.->|Private DNS| StorageDNS
    CosmosDB -.->|Private DNS| CosmosDNS
    KV -.->|Private DNS| KVDNS
    ACR -.->|Private DNS| ACRDNS

    %% Monitoring
    FW -->|Diagnostics| LogAnalytics
    AKS -->|Diagnostics| LogAnalytics
    AISrch -->|Diagnostics| LogAnalytics
    LogAnalytics --> AppInsights
    LogAnalytics --> Sentinel

    classDef network  fill:#1a3a5c,stroke:#4a9ede,color:#ffffff
    classDef security fill:#1a2a1a,stroke:#4ade80,color:#ffffff
    classDef compute  fill:#2a1a3a,stroke:#a78bfa,color:#ffffff
    classDef storage  fill:#3a2a1a,stroke:#fb923c,color:#ffffff
    classDef monitor  fill:#2a2a1a,stroke:#facc15,color:#ffffff
    classDef dns      fill:#1a2a3a,stroke:#67e8f9,color:#ffffff
```

## Key Design Decisions

- Azure Firewall (for egress filtering and inter-VNet routing)
- VPN Gateway (for hybrid connectivity)
- Gateway Subnet, Firewall Subnet, Bastion Subnet
- AKS Cluster (system and user node pools)
- Azure AI Search instance

## Source Knowledge

| Title | Repo | Score |
|-------|------|-------|
| Sap Whole Landscape Content | architecture-center | 0.016 |
| Azure Firewall and Application Gateway for virtual networks | architecture-center | 0.016 |
| Baseline Aks Content | architecture-center | 0.016 |
| Hub Spoke Virtual Wan Architecture Content | architecture-center | 0.016 |
| Baseline Landing Zone Content | architecture-center | 0.015 |
| Aks Multi Cluster Content | architecture-center | 0.015 |
