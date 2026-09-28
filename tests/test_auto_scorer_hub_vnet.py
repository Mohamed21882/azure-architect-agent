"""Regression: "Existing Hub VNet: No" means CREATE a hub — never a constraint violation.

Run: PYTHONPATH=. venv/bin/python -m unittest discover -s tests -v
The live case scores with local Ollama (TE1_TEST_MODEL, default mistral-small:latest)
and is skipped when Ollama is not reachable.
"""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import requests

from brain.eval import auto_scorer
from brain.eval.auto_scorer import _build_prompt, score_architecture

HUB_SPOKE_DESIGN = """## Architecture Summary
Hub-spoke topology in Qatar Central. A NEW hub VNet (vnet-hub-qc, 10.1.0.0/16) hosts
Azure Firewall (AzureFirewallSubnet 10.1.0.0/26), Azure Bastion (AzureBastionSubnet
10.1.1.0/26) and a VPN Gateway (GatewaySubnet 10.1.2.0/27). A spoke VNet (vnet-spoke-rag,
10.2.0.0/16) is peered to the hub and hosts a private AKS cluster and Azure AI Search
behind private endpoints with Private DNS zones linked to the hub. Azure OpenAI runs in
UAE North (Qatar Central has no Azure OpenAI models); prompts and responses leave Qatar.
Key Vault and Storage use private endpoints; NSGs on every subnet; egress forced through
the firewall via UDRs. Log Analytics + Azure Monitor collect diagnostics.
Estimated cost: $4.2k/month within the $5k budget.
"""

FORM_NO_HUB = {
    "description": "Private RAG pipeline with AKS agents and Azure AI Search",
    "region": "Qatar Central",
    "compliance": "None",
    "budget": "$5k–$20k",
    "hub_vnet": "No",
    "additional_constraints": "",
}

REPORTED_FALSE_POSITIVE = {
    "severity": "critical",
    "category": "constraint_violation",
    "message": (
        "constraint states Hub VNet: No — architecture violates this by proposing a "
        "hub-spoke topology with dedicated hub VNet"
    ),
}


def _hub_violations(flags: list) -> list[dict]:
    return [
        f for f in flags
        if isinstance(f, dict)
        and f.get("category") == "constraint_violation"
        and "hub" in str(f.get("message", "")).lower()
    ]


class HubVnetPromptTests(unittest.TestCase):
    def test_prompt_states_create_hub_rule(self):
        prompt = _build_prompt(HUB_SPOKE_DESIGN, FORM_NO_HUB, [])
        self.assertIn("Existing Hub VNet: No (the customer has no hub VNet", prompt)
        self.assertIn("MUST CREATE a new hub VNet", prompt)
        self.assertIn("Do NOT flag a new or dedicated hub VNet", prompt)

    def test_rule_survives_tenant_context(self):
        # 0e81aa4 added the tenant section — the hub rule must still be present with it
        prompt = _build_prompt(
            HUB_SPOKE_DESIGN, FORM_NO_HUB, [], None,
            "Address ranges already in use (do NOT overlap):\n- 10.0.0.0/16 — TestVnet (TE-1)",
        )
        self.assertIn("MUST CREATE a new hub VNet", prompt)
        self.assertIn("Live Azure Tenant", prompt)


class HubVnetScoringTests(unittest.TestCase):
    """score_architecture() with a stubbed LLM that emits the reported false positive."""

    def _score_with_llm_output(self, flags: list, form: dict) -> dict:
        raw = json.dumps({
            "constraint_adherence": 0.4, "security_posture": 0.9,
            "completeness": 0.9, "overall": 0.7, "flags": flags,
        })
        with mock.patch.object(auto_scorer, "_call_llm", return_value=raw):
            return score_architecture(HUB_SPOKE_DESIGN, form, [])

    def test_reported_false_positive_is_dropped(self):
        result = self._score_with_llm_output([REPORTED_FALSE_POSITIVE], FORM_NO_HUB)
        self.assertEqual(_hub_violations(result["flags"]), [])

    def test_real_overlap_violation_is_kept(self):
        overlap = {
            "severity": "critical", "category": "constraint_violation",
            "message": "New hub VNet 10.0.0.0/16 overlaps existing TestVnet 10.0.0.0/16",
        }
        result = self._score_with_llm_output([overlap], FORM_NO_HUB)
        self.assertEqual(result["flags"], [overlap])

    def test_other_categories_untouched(self):
        budget = {"severity": "medium", "category": "budget_risk",
                  "message": "Hub firewall adds significant cost against the budget"}
        result = self._score_with_llm_output([budget], FORM_NO_HUB)
        self.assertEqual(result["flags"], [budget])

    def test_guard_inactive_when_hub_exists(self):
        form = dict(FORM_NO_HUB, hub_vnet="Yes")
        second_hub = {"severity": "critical", "category": "constraint_violation",
                      "message": "Creates a new hub VNet although an existing hub is available"}
        result = self._score_with_llm_output([second_hub], form)
        self.assertEqual(result["flags"], [second_hub])


def _ollama_up() -> bool:
    try:
        return requests.get("http://localhost:11434/api/tags", timeout=2).ok
    except Exception:
        return False


@unittest.skipUnless(_ollama_up(), "Ollama not reachable on localhost:11434")
class HubVnetLiveScoringTest(unittest.TestCase):
    def test_hub_spoke_with_no_existing_hub_is_not_a_violation(self):
        model = os.environ.get("TE1_TEST_MODEL", "mistral-small:latest")
        result = score_architecture(HUB_SPOKE_DESIGN, FORM_NO_HUB, [], model=model)
        self.assertNotEqual(result.get("flags"), ["auto-score unavailable"],
                            "scorer LLM call failed")
        self.assertEqual(_hub_violations(result["flags"]), [], result["flags"])


if __name__ == "__main__":
    unittest.main()
