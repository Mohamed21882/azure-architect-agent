"""Regression: Azure OpenAI is NOT deployable in Qatar Central (verified 2026-09-28 with
`az cognitiveservices model list --location qatarcentral`, which returns no models).

- An Azure OpenAI deployment placed in Qatar Central → critical flag.
- Calling Azure OpenAI in UAE North from a Qatar Central design and stating that prompts
  and responses leave Qatar → no region flag.
- UAE North without that statement → medium flag.

Run: PYTHONPATH=. venv/bin/python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import requests

from brain.eval import auto_scorer
from brain.eval.auto_scorer import _build_prompt, openai_region_verdict, score_architecture

FORM_QATAR = {
    "description": "Private RAG assistant for internal documents",
    "region": "Qatar Central",
    "compliance": "None",
    "budget": "$5k–$20k",
    "hub_vnet": "No",
    "additional_constraints": "",
}

OPENAI_IN_QATAR = """## Architecture Summary
Private RAG in Qatar Central. Azure AI Search, Blob Storage and Key Vault in the spoke VNet
(10.2.0.0/16), all behind private endpoints.
| Component | Region | Notes |
|---|---|---|
| Azure OpenAI (gpt-4o, text-embedding-3-large) | Qatar Central | Private endpoint in the spoke |
| Azure AI Search | Qatar Central | Semantic ranker |
All resources are deployed in Qatar Central for data residency.
"""

OPENAI_IN_UAE_WITH_NOTE = """## Architecture Summary
Private RAG in Qatar Central. Azure AI Search, Blob Storage, Key Vault and AKS stay in
Qatar Central behind private endpoints.
| Component | Region | Notes |
|---|---|---|
| Azure OpenAI (gpt-4.1, Regional Provisioned) | UAE North | Private endpoint over cross-region VNet peering |
| Azure AI Search | Qatar Central | Chunking and embedding run in AKS |
Data flow: prompts and responses leave Qatar and are processed in UAE North; documents,
index and keys stay in Qatar Central.
"""

OPENAI_IN_UAE_NO_NOTE = """## Architecture Summary
Private RAG in Qatar Central. Azure AI Search, Storage and Key Vault in Qatar Central.
Azure OpenAI (gpt-4.1) is deployed in UAE North with a private endpoint.
"""

NEGATED_MENTION = """Azure OpenAI is not available in Qatar Central, so the models run in
UAE North. Prompts and responses leave Qatar for inference in UAE North.
"""


def _critical_region(flags: list) -> list[dict]:
    return [f for f in flags if isinstance(f, dict) and f.get("severity") == "critical"
            and "openai" in str(f.get("message", "")).lower()]


def _region_flags(flags: list) -> list[dict]:
    return [f for f in flags if isinstance(f, dict)
            and "openai" in str(f.get("message", "")).lower()
            and ("qatar" in str(f.get("message", "")).lower() or "uae" in str(f.get("message", "")).lower())]


class VerdictTests(unittest.TestCase):
    def test_qatar_placement(self):
        self.assertEqual(openai_region_verdict(OPENAI_IN_QATAR, FORM_QATAR), "qatar")

    def test_uae_with_note(self):
        self.assertEqual(openai_region_verdict(OPENAI_IN_UAE_WITH_NOTE, FORM_QATAR), "uae_with_note")

    def test_uae_without_note(self):
        self.assertEqual(openai_region_verdict(OPENAI_IN_UAE_NO_NOTE, FORM_QATAR), "uae_no_note")

    def test_negated_qatar_mention_is_not_placement(self):
        self.assertEqual(openai_region_verdict(NEGATED_MENTION, FORM_QATAR), "uae_with_note")

    def test_implicit_placement_in_qatar_design(self):
        text = "Azure OpenAI gpt-4o with a private endpoint. All resources in the spoke VNet."
        self.assertEqual(openai_region_verdict(text, FORM_QATAR), "qatar")

    def test_non_qatar_design_untouched(self):
        form = dict(FORM_QATAR, region="West Europe")
        text = "Azure OpenAI gpt-4o in West Europe with a private endpoint."
        self.assertEqual(openai_region_verdict(text, form), "other")

    def test_prompt_no_longer_claims_availability(self):
        prompt = _build_prompt(OPENAI_IN_QATAR, FORM_QATAR, [])
        self.assertNotIn("Azure OpenAI IS available in Qatar Central", prompt)
        self.assertIn("Azure OpenAI is NOT deployable in Qatar Central", prompt)


class ScoringTests(unittest.TestCase):
    """score_architecture() with a stubbed scorer LLM."""

    def _score(self, design: str, llm_flags: list) -> dict:
        raw = json.dumps({"constraint_adherence": 0.8, "security_posture": 0.9,
                          "completeness": 0.9, "overall": 0.85, "flags": llm_flags})
        with mock.patch.object(auto_scorer, "_call_llm", return_value=raw):
            return score_architecture(design, FORM_QATAR, [])

    def test_openai_in_qatar_gets_critical_flag_even_if_llm_misses_it(self):
        result = self._score(OPENAI_IN_QATAR, [])
        crit = _critical_region(result["flags"])
        self.assertEqual(len(crit), 1, result["flags"])
        self.assertEqual(crit[0]["category"], "wrong_region_availability")

    def test_uae_north_with_note_gets_no_region_flag(self):
        wrong = {"severity": "critical", "category": "constraint_violation",
                 "message": "Azure OpenAI in UAE North violates the Qatar Central region constraint"}
        result = self._score(OPENAI_IN_UAE_WITH_NOTE, [wrong])
        self.assertEqual(_critical_region(result["flags"]), [])
        self.assertEqual(_region_flags(result["flags"]), [])

    def test_uae_north_without_note_gets_medium_flag(self):
        result = self._score(OPENAI_IN_UAE_NO_NOTE, [])
        flags = _region_flags(result["flags"])
        self.assertEqual([f["severity"] for f in flags], ["medium"], result["flags"])

    def test_rule_applies_when_scorer_llm_fails(self):
        with mock.patch.object(auto_scorer, "_call_llm", side_effect=RuntimeError("LLM down")):
            result = score_architecture(OPENAI_IN_QATAR, FORM_QATAR, [])
        self.assertEqual(len(_critical_region(result["flags"])), 1, result["flags"])


def _ollama_up() -> bool:
    try:
        return requests.get("http://localhost:11434/api/tags", timeout=2).ok
    except Exception:
        return False


@unittest.skipUnless(_ollama_up(), "Ollama not reachable on localhost:11434")
class LiveScoringTests(unittest.TestCase):
    model = os.environ.get("TE1_TEST_MODEL", "mistral-small:latest")

    def test_live_qatar_placement_is_critical(self):
        result = score_architecture(OPENAI_IN_QATAR, FORM_QATAR, [], model=self.model)
        self.assertEqual(len(_critical_region(result["flags"])), 1, result["flags"])

    def test_live_uae_with_note_is_not_critical(self):
        result = score_architecture(OPENAI_IN_UAE_WITH_NOTE, FORM_QATAR, [], model=self.model)
        self.assertEqual(_critical_region(result["flags"]), [], result["flags"])


if __name__ == "__main__":
    unittest.main()
