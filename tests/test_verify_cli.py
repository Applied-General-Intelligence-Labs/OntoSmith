"""verify CLI smoke tests: score / audit-sets / project /
report over tiny JSONL fixtures (the R10 one-command forms).
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from harness.verification.scoring import NormalizationTable, match_records
from harness.verification.verify_cli import load_table, main

PACK_TN = (Path(__file__).parent.parent
           / "packs" / "open-domain" / "tn_table.json")

PRED = [{"doc_id": "d1", "subject": "A", "relation": "r",
         "object": "B"}]
GOLD = [{"doc_id": "d1", "head_names": ["A"], "tail_names": ["B"],
         "relation": "r"},
        {"doc_id": "d1", "head_names": ["A"], "tail_names": ["C"],
         "relation": "r"}]


class TestVerifyCli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pred = self.tmp / "pred.jsonl"
        self.gold = self.tmp / "gold.jsonl"
        self.pred.write_text("\n".join(json.dumps(r) for r in PRED))
        self.gold.write_text("\n".join(json.dumps(r) for r in GOLD))

    def test_score(self, capsys=None):
        rc = main(["score", "--predicted", str(self.pred),
                   "--gold", str(self.gold)])
        self.assertEqual(rc, 0)

    def test_audit_sets_writes_files(self):
        out = self.tmp / "audit"
        rc = main(["audit-sets", "--predicted", str(self.pred),
                   "--gold", str(self.gold), "--out-dir", str(out),
                   "--seed", "3"])
        self.assertEqual(rc, 0)
        frame = json.loads((out / "frame.json").read_text())
        self.assertEqual(frame["matched_predicted"], 1)
        self.assertTrue((out / "set_A.jsonl").exists())

    def test_project_counts_only(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["project", "--counts", "pair=100",
                       "--scenario", "wide:pair=250,doc=3"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["total_units"]["base"], 100)
        self.assertEqual(payload["governing_scenario"], "wide")

    def test_report_to_file(self):
        out = self.tmp / "report.md"
        rc = main(["report", "--predicted", str(self.pred),
                   "--gold", str(self.gold), "--out", str(out)])
        self.assertEqual(rc, 0)
        self.assertIn("Evaluation report", out.read_text())


class TestLoadTableFormats(unittest.TestCase):
    def test_cli_format_still_loads(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.json"
            p.write_text(json.dumps({
                "symmetric": ["spouse"],
                "inverses": {"part of": "has part"},
                "relation_groups": {"G": ["a", "b"]},
                "inverse_expansions": {"father": ["child"]},
                "subsumptions": {"country": ["capital of"]}}))
            t = load_table(str(p))
        self.assertEqual(t.symmetric, frozenset({"spouse"}))
        self.assertEqual(t.inverses, {"part of": "has part"})
        self.assertEqual(t.relation_groups, {"a": "G", "b": "G"})
        self.assertEqual(t.inverse_expansions, {"father": ("child",)})
        self.assertEqual(t.subsumptions, {"country": ("capital of",)})

    def test_pack_tn_table_loads(self):
        # RE-TN-1: the shipped pack table (docred-tn v2) loads
        # through the product entry point with the adopted
        # TN-3/4/5 layers at their registered sizes.
        raw = json.loads(PACK_TN.read_text())
        self.assertEqual(raw["version"], 2)
        t = load_table(str(PACK_TN))
        self.assertEqual(len(t.symmetric), 3)
        self.assertEqual(len(t.inverses), 7)
        self.assertEqual(len(t.relation_groups), 16)
        self.assertEqual(len(t.inverse_expansions), 15)
        self.assertEqual(len(t.subsumptions), 4)
        self.assertEqual(t.relation_groups["country"], "CONTAINMENT")
        self.assertEqual(t.inverse_expansions["child"],
                         ("father", "mother"))

    def test_pack_table_extended_cases(self):
        # One case per adopted layer: each matches under the
        # pack table and does NOT match under a TN-1/TN-2-only
        # (frozen v1) table built from the same file.
        raw = json.loads(PACK_TN.read_text())
        frozen = NormalizationTable(
            symmetric=frozenset(raw["tn1_symmetric_types"]),
            inverses={p["inverse"]: p["primary"]
                      for p in raw["tn2_inverse_pairs"]})
        full = load_table(str(PACK_TN))
        cases = [
            # TN-3: gold 'country', predicted 'location'
            ({"doc_id": "d", "subject": "Vienna", "relation": "location",
              "object": "Austria"},
             {"doc_id": "d", "head_names": ["Vienna"],
              "tail_names": ["Austria"], "relation": "country"}),
            # TN-4: gold 'father', predicted 'child' (swapped)
            ({"doc_id": "d", "subject": "Son", "relation": "child",
              "object": "Dad"},
             {"doc_id": "d", "head_names": ["Dad"],
              "tail_names": ["Son"], "relation": "father"}),
            # TN-5: gold 'country', predicted 'capital of'
            ({"doc_id": "d", "subject": "Vienna",
              "relation": "capital of", "object": "Austria"},
             {"doc_id": "d", "head_names": ["Vienna"],
              "tail_names": ["Austria"], "relation": "country"}),
        ]
        for pred, gold in cases:
            self.assertEqual(
                match_records([pred], [gold], full).matched_gold, 1)
            self.assertEqual(
                match_records([pred], [gold], frozen).matched_gold, 0)


if __name__ == "__main__":
    unittest.main()
