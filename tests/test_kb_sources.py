"""Source rules for the knowledge base (decisions of 2026-09-28):

- azure-ai excludes articles/foundry (owned by azure-foundry) and articles/foundry-local
- azure-ai articles/foundry-classic is kept but tagged legacy: true and presented under a
  separate prompt heading, never for new designs
- wiki/semantic is the "crystallised" source; only approved sessions crystallise. A page is
  written but never indexed when the final design has a critical scorer flag or the Azure
  OpenAI data-flow flag; other medium/low flags (budget risk etc.) do not block

Run: PYTHONPATH=. venv/bin/python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest import mock

import requests

from brain.config import BrainConfig
from brain.ingest.local_reader import read_all_sources, walk_source
from brain.models import Chunk, IngestSource, SearchResult
from brain.search.context_format import (
    CRYSTALLISED_LABEL, LEGACY_HEADING, format_brain_section,
)
from brain.eval.auto_scorer import blocks_crystallisation
from brain.search.hybrid_search import _payload_to_chunk
from brain.wiki import crystalliser

QATAR_OPENAI = ("Private RAG in Qatar Central. Azure OpenAI gpt-4o is deployed in Qatar Central "
                "behind a private endpoint.")
GOOD_DESIGN = ("Private RAG in Qatar Central with Azure AI Search and Storage. Azure OpenAI runs "
               "in UAE North; prompts and responses leave Qatar for inference.")
UAE_NO_NOTE = ("Private RAG in Qatar Central with Azure AI Search and Storage. Azure OpenAI is "
               "deployed in UAE North with a private endpoint.")

BUDGET_MEDIUM = {"severity": "medium", "category": "budget_risk",
                 "message": "Azure Firewall Premium pushes cost close to the budget ceiling"}
LOW_NOTE = {"severity": "low", "category": "operational_gap", "message": "Consider tagging policy"}
CRITICAL_OTHER = {"severity": "critical", "category": "constraint_violation",
                  "message": "Storage account allows public network access despite the no-public-IP constraint"}
DATA_FLOW_FLAG = {"severity": "medium", "category": "incomplete_specification",
                  "rule": "openai_data_flow",
                  "message": "The design calls Azure OpenAI in UAE North but does not state that "
                             "prompts and responses leave Qatar; document this cross-border data flow."}


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _body(topic: str) -> str:
    return f"# {topic}\n\n" + " ".join(f"{topic} azure word{i}" for i in range(80)) + "\n"


class FixtureRoot(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="te1-kb-")
        self.cfg = BrainConfig(wiki_root=self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class ExcludeAndLegacyTests(FixtureRoot):
    def test_path_rules(self):
        ai = os.path.join(self.root, "raw", "azure-ai", "articles")
        self.assertTrue(self.cfg.is_excluded("azure-ai", f"{ai}/foundry/a.md"))
        self.assertTrue(self.cfg.is_excluded("azure-ai", f"{ai}/foundry-local/a.md"))
        self.assertFalse(self.cfg.is_excluded("azure-ai", f"{ai}/foundry-classic/a.md"))
        self.assertTrue(self.cfg.is_legacy("azure-ai", f"{ai}/foundry-classic/a.md"))
        self.assertFalse(self.cfg.is_legacy("azure-ai", f"{ai}/search/a.md"))
        foundry = os.path.join(self.root, "raw", "azure-foundry", "articles", "foundry", "a.md")
        self.assertFalse(self.cfg.is_excluded("azure-foundry", foundry))

    def test_scan_excludes_and_tags(self):
        ai = os.path.join(self.root, "raw", "azure-ai", "articles")
        for sub in ("foundry", "foundry-local", "foundry-classic", "search"):
            _write(f"{ai}/{sub}/doc.md", _body(sub))
        docs = {os.path.relpath(d.file_path, ai): d for repo, d in read_all_sources(self.cfg)
                if repo == "azure-ai"}
        self.assertEqual(sorted(docs), ["foundry-classic/doc.md", "search/doc.md"])
        self.assertTrue(docs["foundry-classic/doc.md"].metadata.get("legacy"))
        self.assertNotIn("legacy", docs["search/doc.md"].metadata)

    def test_bm25_only_hit_gets_legacy_from_path(self):
        fp = os.path.join(self.root, "raw", "azure-ai", "articles", "foundry-classic", "x.md")
        chunk = _payload_to_chunk({"chunk_id": "c", "source_repo": "azure-ai", "file_path": fp}, self.cfg)
        self.assertTrue(chunk.metadata.get("legacy"))


def _hit(title: str, repo: str, legacy: bool = False) -> SearchResult:
    c = Chunk(chunk_id=title, doc_id="d", content=f"{title} text", source=IngestSource.LOCAL,
              source_repo=repo, file_path=f"/x/{title}.md", title=title, chunk_index=0,
              metadata={"legacy": True} if legacy else {})
    return SearchResult(chunk=c, rrf_score=0.1, fused_score=0.1, age_days=1.0)


class PromptFormatTests(unittest.TestCase):
    def test_legacy_chunks_under_separate_heading(self):
        out = format_brain_section([_hit("Hub project", "azure-ai", legacy=True),
                                    _hit("Foundry project", "azure-foundry")])
        self.assertIn(LEGACY_HEADING, out)
        head, legacy_part = out.split(LEGACY_HEADING)
        self.assertIn("Foundry project", head)
        self.assertNotIn("Hub project", head)
        self.assertIn("Hub project", legacy_part)
        self.assertIn("NEVER use them as the basis for a new design", legacy_part)

    def test_no_legacy_heading_without_legacy_hits(self):
        self.assertNotIn(LEGACY_HEADING, format_brain_section([_hit("A", "cli")]))

    def test_crystallised_hits_are_labelled(self):
        out = format_brain_section([_hit("Past design", "crystallised")])
        self.assertIn(CRYSTALLISED_LABEL, out)


class BlockingRuleTests(unittest.TestCase):
    def test_what_blocks(self):
        self.assertTrue(blocks_crystallisation(CRITICAL_OTHER))
        self.assertTrue(blocks_crystallisation(DATA_FLOW_FLAG))
        self.assertTrue(blocks_crystallisation({"severity": "critical", "category": "budget_risk",
                                                "message": "10x over budget"}))

    def test_what_does_not_block(self):
        self.assertFalse(blocks_crystallisation(BUDGET_MEDIUM))
        self.assertFalse(blocks_crystallisation(LOW_NOTE))
        self.assertFalse(blocks_crystallisation({"severity": "medium", "category": "incomplete_specification",
                                                 "message": "Add diagnostics settings"}))
        self.assertFalse(blocks_crystallisation("auto-score unavailable"))


class CrystalliserGateTests(FixtureRoot):
    def setUp(self) -> None:
        super().setUp()
        self.wiki = os.path.join(self.root, "wiki", "semantic")
        self._patch = mock.patch.object(crystalliser, "_WIKI_SEMANTIC_DIR", self.wiki)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        super().tearDown()

    def _session(self, summary: str, approved=True, flags=None) -> dict:
        return {"approved": approved, "description": "Private RAG test",
                "form_values": {"region": "Qatar Central"}, "messages": [],
                "retrieved_chunks": [], "architecture_summary": summary, "flags": flags or []}

    def _crystallise(self, summary: str, flags=None):
        with mock.patch.object(crystalliser, "_ingest_wiki_page", return_value="") as ingest:
            res = crystalliser.crystallise_session(self._session(summary, flags=flags), self.cfg)
        return res, ingest

    def test_budget_and_low_flags_do_not_block(self):
        res, ingest = self._crystallise(GOOD_DESIGN, [BUDGET_MEDIUM, LOW_NOTE])
        ingest.assert_called_once()
        self.assertTrue(res.indexed, res.warning)

    def test_critical_flag_blocks(self):
        res, ingest = self._crystallise(GOOD_DESIGN, [BUDGET_MEDIUM, CRITICAL_OTHER])
        ingest.assert_not_called()
        self.assertFalse(res.indexed)
        self.assertIn("critical flag (constraint_violation)", res.warning)
        self.assertNotIn("budget", res.warning.lower())
        # the block is recorded in the page, so rebuilds skip it too
        self.assertTrue(crystalliser.page_block_reason(res.path))

    def test_data_flow_flag_blocks(self):
        res, ingest = self._crystallise(GOOD_DESIGN, [DATA_FLOW_FLAG])
        ingest.assert_not_called()
        self.assertIn("medium flag (incomplete_specification)", res.warning)

    def test_data_flow_detected_from_text_without_flags(self):
        res, ingest = self._crystallise(UAE_NO_NOTE, [])
        ingest.assert_not_called()
        self.assertIn("region check failed (medium)", res.warning)

    def test_unapproved_session_is_refused(self):
        with self.assertRaises(ValueError):
            crystalliser.crystallise_session(self._session(GOOD_DESIGN, approved=False), self.cfg)
        self.assertFalse(os.path.isdir(self.wiki) and os.listdir(self.wiki))

    def test_region_failure_writes_page_but_never_indexes(self):
        with mock.patch.object(crystalliser, "_ingest_wiki_page") as ingest:
            res = crystalliser.crystallise_session(self._session(QATAR_OPENAI), self.cfg)
        ingest.assert_not_called()
        self.assertFalse(res.indexed)
        self.assertIn("region check failed (critical)", res.warning)
        with open(res.path, encoding="utf-8") as fh:
            self.assertIn("index: false", fh.read())
        self.assertIn("region check failed", crystalliser.page_block_reason(res.path))

    def test_passing_page_is_indexed(self):
        with mock.patch.object(crystalliser, "_ingest_wiki_page", return_value="") as ingest:
            res = crystalliser.crystallise_session(self._session(GOOD_DESIGN), self.cfg)
        ingest.assert_called_once()
        self.assertTrue(res.indexed)
        self.assertEqual(res.warning, "")

    def test_rebuild_scan_skips_blocked_pages(self):
        _write(f"{self.wiki}/ok.md", "---\nregion: Qatar Central\n---\n" + GOOD_DESIGN + "\n" + _body("ok"))
        _write(f"{self.wiki}/bad.md", "---\nregion: Qatar Central\n---\n" + QATAR_OPENAI + "\n" + _body("bad"))
        _write(f"{self.wiki}/off.md", "---\nindex: false\nindex_blocked: manual\n---\n" + _body("off"))
        names = sorted(os.path.basename(d.file_path) for repo, d in read_all_sources(self.cfg)
                       if repo == "crystallised")
        self.assertEqual(names, ["ok.md"])


def _services_up() -> bool:
    try:
        return (requests.get("http://localhost:11434/api/tags", timeout=2).ok
                and requests.get("http://localhost:6333/collections", timeout=2).ok)
    except Exception:
        return False


@unittest.skipUnless(_services_up(), "Ollama or Qdrant not reachable")
class CrystalliserLiveIndexTest(FixtureRoot):
    """Real embed + upsert into a throwaway collection and scratch BM25 file."""

    def test_update_running_skips_indexing_with_warning(self):
        import fcntl
        import brain.kb.status as kb_status
        lock_path = os.path.join(self.root, "update.lock")
        with open(lock_path, "a+") as held, \
             mock.patch.object(kb_status, "LOCK_PATH", lock_path), \
             mock.patch.object(crystalliser, "_WIKI_SEMANTIC_DIR", os.path.join(self.root, "wiki", "semantic")):
            fcntl.flock(held, fcntl.LOCK_EX)
            res = crystalliser.crystallise_session({
                "approved": True, "description": "Locked", "form_values": {"region": "Qatar Central"},
                "messages": [], "retrieved_chunks": [], "architecture_summary": GOOD_DESIGN}, self.cfg)
        self.assertFalse(res.indexed)
        self.assertIn("update is running", res.warning)

    def test_page_lands_in_qdrant_and_bm25_as_crystallised(self):
        from brain.search.bm25_index import BM25Index
        from brain.store.vector_store import ensure_collection, get_client
        cfg = BrainConfig(wiki_root=self.root, qdrant_collection="te1_test_crystallised",
                          bm25_index_path=os.path.join(self.root, "bm25.pkl"))
        BM25Index.empty().save(cfg.bm25_index_path)
        client = get_client(cfg)
        ensure_collection(client, cfg)
        import brain.kb.status as kb_status
        try:
            # private lock file: the real one may be held by a running rebuild
            with mock.patch.object(crystalliser, "_WIKI_SEMANTIC_DIR", os.path.join(self.root, "wiki", "semantic")), \
                 mock.patch.object(kb_status, "LOCK_PATH", os.path.join(self.root, "update.lock")):
                res = crystalliser.crystallise_session({
                    "approved": True, "description": "Private RAG live test",
                    "form_values": {"region": "Qatar Central"}, "messages": [],
                    "retrieved_chunks": [],
                    "architecture_summary": GOOD_DESIGN + "\n\n" + _body("design"),
                }, cfg)
            self.assertTrue(res.indexed, res.warning)
            bm25 = BM25Index.load(cfg.bm25_index_path)
            self.assertGreater(len(bm25), 0)
            self.assertEqual(set(bm25.source_repos), {"crystallised"})
            self.assertEqual(client.count(cfg.qdrant_collection).count, len(bm25))
        finally:
            client.delete_collection(cfg.qdrant_collection)


if __name__ == "__main__":
    unittest.main()
