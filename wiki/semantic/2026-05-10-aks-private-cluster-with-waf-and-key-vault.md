---
title: Aks Private Cluster With Waf And Key Vault
date: 2026-05-10T18:55:57.873710
region: East US
compliance: PCI-DSS
confidence: 0.85
source_chunks: ['550e8400-e29b-41d4-a716-446655440000', '550e8400-e29b-41d4-a716-446655440001']
entity_types: []
tags: ['East US', 'PCI-DSS', 'aks', 'private', 'cluster', 'with', 'waf', 'and', 'key', 'vault']
---

## Brief

AKS private cluster with WAF and Key Vault
Budget: 5000
Constraints: No public IPs, must pass pen test

## Architecture

Three-tier AKS architecture with private cluster, WAF ingress, and Key Vault secrets management.

## Key Design Decisions

- Use Azure CNI networking for full VNet integration
- Enable OIDC issuer and Workload Identity
- Route egress through Azure Firewall
- Store secrets in Key Vault with CSI driver
- Enable Defender for Containers

## Source Knowledge

| Title | Repo | Score |
|-------|------|-------|
| AKS Baseline Architecture | architecture-center | 0.031 |
| WAF on Application Gateway | azure-ai | 0.024 |
