"""Draft-context input patch (fix-plan lever: census + survey
delivery).

Covered: the semantic census scanner returns the LISTS
(recurring defined terms with bindings, recurring binding
names, recurring clause/section headings) rather than counts;
heading extraction accepts numbered/ALL-CAPS headings and
rejects sentence lines; the document survey carries head AND
tail; build_draft_context delivers the census, the claims'
measured details and the survey to the drafting tier (and
stays total on profiles without them); a profile run carries
the census claim and the survey deterministically; and the
drafting system prompt carries the census-use and boundary-
declaration instructions.
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from pipeline import ontology_builder as ob
from pipeline import profiler
from pipeline import setup_service as ss


def _doc(name: str, text: str) -> ss.Doc:
    return ss.Doc(name=name, path=f"/x/{name}.txt", text=text,
                  sha256=hashlib.sha256(
                      text.encode()).hexdigest())


CONTRACT_A = (
    "SUPPLY AGREEMENT\n"
    "\n"
    "Acme Corp (the \"Supplier\") and Beta LLC (the \"Buyer\") "
    "agree as follows.\n"
    "\n"
    "1. DEFINITIONS\n"
    "The Products are listed in Exhibit A.\n"
    "\n"
    "9. GOVERNING LAW\n"
    "This Agreement is governed by the laws of Delaware.\n"
    "3. The Supplier shall deliver goods within thirty days.\n")

CONTRACT_B = (
    "DISTRIBUTION AGREEMENT\n"
    "\n"
    "Gamma Inc (the \"Supplier\") appoints Delta Co "
    "(the \"Distributor\").\n"
    "\n"
    "1. DEFINITIONS\n"
    "Territory means the United States.\n"
    "\n"
    "9. GOVERNING LAW\n"
    "This Agreement is governed by the laws of New York.\n")


class TestSemanticCensus(unittest.TestCase):

    def test_recurring_terms_carry_bindings(self):
        docs = [_doc("a", CONTRACT_A), _doc("b", CONTRACT_B)]
        r = ss.scan_semantic_census(docs, None, None, {})
        detail = r["detail"]
        self.assertEqual(detail["defined_terms_total"], 3)
        terms = {e["term"]: e
                 for e in detail["defined_terms_recurring"]}
        self.assertIn("Supplier", terms)
        self.assertEqual(terms["Supplier"]["n_docs"], 2)
        self.assertEqual(terms["Supplier"]["bindings"],
                         ["Acme Corp", "Gamma Inc"])
        # Buyer / Distributor bind in one document each: not
        # corpus terrain, not in the recurring list.
        self.assertNotIn("Buyer", terms)
        names = {e["name"]
                 for e in detail["binding_names_recurring"]}
        self.assertEqual(names, set())  # no name binds in 2 docs

    def test_recurring_headings(self):
        docs = [_doc("a", CONTRACT_A), _doc("b", CONTRACT_B)]
        r = ss.scan_semantic_census(docs, None, None, {})
        headings = {e["heading"]: e["n_docs"]
                    for e in r["detail"]["headings_recurring"]}
        self.assertEqual(headings.get("DEFINITIONS"), 2)
        self.assertEqual(headings.get("GOVERNING LAW"), 2)
        self.assertEqual(r["value"],
                         len(r["detail"]["defined_terms_recurring"])
                         + len(r["detail"]["binding_names_recurring"])
                         + len(r["detail"]["headings_recurring"]))

    def test_heading_extraction_rejects_sentences(self):
        heads = ss._doc_headings(CONTRACT_A)
        self.assertIn("SUPPLY AGREEMENT", heads)
        self.assertIn("DEFINITIONS", heads)
        self.assertIn("GOVERNING LAW", heads)
        # the numbered sentence line is not a heading
        self.assertFalse(any("shall deliver" in h for h in heads))
        # a one-word page furniture line is not a heading
        self.assertEqual(ss._doc_headings("- 3 -\n"), [])


class TestSurvey(unittest.TestCase):

    def test_head_and_tail(self):
        text = "H" * 2000 + "M" * 5000 + "T" * 2000
        (entry,) = ss.survey_documents([_doc("long", text)])
        self.assertEqual(entry["head"], text[:1500])
        self.assertEqual(entry["tail"], text[-1500:])
        self.assertEqual(entry["n_chars"], len(text))

    def test_short_doc_has_no_tail(self):
        (entry,) = ss.survey_documents([_doc("s", "tiny body")])
        self.assertEqual(entry["head"], "tiny body")
        self.assertEqual(entry["tail"], "")

    def test_mid_doc_tail_is_the_remainder(self):
        text = "a" * 1600 + "b" * 400
        (entry,) = ss.survey_documents([_doc("m", text)])
        self.assertEqual(entry["tail"], "a" * 100 + "b" * 400)


class TestDraftContextDelivery(unittest.TestCase):

    def _profile(self) -> dict:
        census_detail = {
            "defined_terms_total": 12,
            "defined_terms_recurring": [
                {"term": "Supplier", "n_docs": 2,
                 "bindings": ["Acme Corp"]}]}
        return {
            "family": "contracts",
            "inventory": {"n_files": 2},
            "claims": [
                {"section": "identity", "kind": "fact",
                 "grade": "FULL", "statement": "Semantic census…",
                 "verify": {"scanner": "semantic_census",
                            "params": {}},
                 "measured": {"value": 1,
                              "detail": census_detail}},
                {"section": "inventory", "kind": "fact",
                 "grade": "FULL",
                 "statement": "Corpus holds 2 files."},
            ],
            "document_survey": [
                {"name": "a", "n_chars": 10, "head": "0123456789",
                 "tail": "", "headings": []}],
        }

    def test_census_and_survey_reach_the_drafter(self):
        ctx = ob.build_draft_context(self._profile(), "brief")
        self.assertEqual(
            ctx["semantic_census"]["defined_terms_total"], 12)
        self.assertEqual(
            ctx["claims"][0]["measured_detail"]
            ["defined_terms_recurring"][0]["term"], "Supplier")
        # a claim without a measurement stays bare
        self.assertNotIn("measured_detail", ctx["claims"][1])
        self.assertEqual(ctx["document_survey"][0]["name"], "a")

    def test_profile_without_census_is_total(self):
        ctx = ob.build_draft_context(
            {"family": "f", "claims": []}, None)
        self.assertIsNone(ctx["semantic_census"])
        self.assertEqual(ctx["document_survey"], [])
        self.assertEqual(ctx["claims"], [])


class TestProfileCarriesCensus(unittest.TestCase):

    def test_profile_run_delivers_census_and_survey(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = tmp / "corpus"
            corpus.mkdir()
            (corpus / "a.txt").write_text(CONTRACT_A)
            (corpus / "b.txt").write_text(CONTRACT_B)
            profile = profiler.profile_corpus(
                corpus, "fixfam", tmp / "out", sample_n=2)
            scanners = [(c.get("verify") or {}).get("scanner")
                        for c in profile["claims"]]
            self.assertIn("semantic_census", scanners)
            self.assertEqual(len(profile["document_survey"]), 2)
            ctx = ob.build_draft_context(profile, "brief")
            self.assertIsNotNone(ctx["semantic_census"])
            terms = {e["term"] for e in
                     ctx["semantic_census"]
                     ["defined_terms_recurring"]}
            self.assertIn("Supplier", terms)
            again = profiler.profile_corpus(
                corpus, "fixfam", tmp / "out2", sample_n=2)
            self.assertEqual(profile["profile_hash"],
                             again["profile_hash"])


class TestDraftingPrompt(unittest.TestCase):

    def test_prompt_requires_census_use_and_boundary_pass(self):
        self.assertIn("semantic census", ob._SCHEMA_DRAFT_SYSTEM)
        self.assertIn("Boundary declarations",
                      ob._SCHEMA_DRAFT_SYSTEM)
        self.assertIn("decision_points", ob._SCHEMA_DRAFT_SYSTEM)
        self.assertIn("TAILS", ob._SCHEMA_DRAFT_SYSTEM)

    def test_prompt_forbids_definition_over_narrowing(self):
        # The boundary pass once sharpened a definition with
        # a condition its own declared pair test never used
        # (Concept gained "with named originators"), silently
        # excluding corpus-attested instances. The rule that
        # definitions stay at corpus breadth pins against a
        # repeat.
        self.assertIn("breadth the corpus attests",
                      ob._SCHEMA_DRAFT_SYSTEM)
        self.assertIn("over-narrowing", ob._SCHEMA_DRAFT_SYSTEM)
        self.assertIn("never by narrowing a definition",
                      ob._SCHEMA_DRAFT_SYSTEM)


if __name__ == "__main__":
    unittest.main()
