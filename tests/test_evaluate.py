"""Tests for pipeline.evaluate — staged this pass (held-item
ruling: INCLUDE; it is the generalized gate-evaluation harness
the unified CLI's `evaluate` command has always pointed at).

Pins load_records' field filtering and evaluate()'s split /
threshold / metric plumbing against the staged analyze_hardened
primitives it composes (the primitives themselves are the
judgment group's shipped-as-is code).
"""
import json
import tempfile
import unittest
from pathlib import Path

from pipeline import evaluate as ev
from pipeline.analyze_hardened import prf, split_by_doc, tune_threshold


def _records(n_pos=30, n_neg=30):
    recs = []
    for i in range(n_pos):
        recs.append({"p_true": 0.55 + (i % 40) / 100.0, "label": 1,
                     "doc_id": f"pos{i % 6}"})
    for i in range(n_neg):
        recs.append({"p_true": (i % 40) / 100.0, "label": 0,
                     "doc_id": f"neg{i % 6}"})
    return recs


class TestLoadRecords(unittest.TestCase):
    def test_filters_and_coerces(self):
        rows = {"records": [
            {"p_true": "0.7", "label": 1, "doc_id": 42,
             "subject": "A"},
            {"p_true": 0.2, "label": 0},               # no doc_id
            {"label": 1, "doc_id": "x"},               # no p_true
            {"p_true": 0.9, "label": 1, "doc_id": "y"},
        ]}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "r.json"
            p.write_text(json.dumps(rows))
            recs = ev.load_records(str(p))
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["p_true"], 0.7)
        self.assertEqual(recs[0]["doc_id"], "42")
        self.assertEqual(recs[0]["_raw"]["subject"], "A")
        self.assertEqual(recs[1]["label"], 1)


class TestEvaluate(unittest.TestCase):
    def test_plumbing_matches_primitives(self):
        recs = _records()
        out = ev.evaluate(recs, seed=7, iters=50)
        dev, held = split_by_doc(recs, seed=7)
        t = tune_threshold(dev)
        self.assertEqual(out["n_dev"], len(dev))
        self.assertEqual(out["n_held"], len(held))
        self.assertEqual(out["threshold"], t)
        self.assertEqual(out["held"],
                         {k: round(v, 3)
                          for k, v in prf(held, t).items()})
        lo, hi = out["f1_ci95"]
        self.assertLessEqual(lo, hi)

    def test_document_level_split_is_disjoint(self):
        recs = _records()
        dev, held = split_by_doc(recs, seed=7)
        dev_docs = {r["doc_id"] for r in dev}
        held_docs = {r["doc_id"] for r in held}
        self.assertFalse(dev_docs & held_docs)


class TestCli(unittest.TestCase):
    def test_compare_runs(self):
        # Regression: --compare read the removed --p-field
        # attributes and crashed with AttributeError.
        import contextlib
        import io
        import sys
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            a, b = Path(td) / "a.json", Path(td) / "b.json"
            a.write_text(json.dumps({"records": _records()}))
            b.write_text(json.dumps({"records": _records()[::-1]}))
            argv = ["evaluate", "--result", str(a),
                    "--compare", str(b), "--iters", "10"]
            buf = io.StringIO()
            with mock.patch.object(sys, "argv", argv), \
                    contextlib.redirect_stdout(buf):
                ev.main()
        out = json.loads(buf.getvalue())
        self.assertIn("compare_error_records_excluded", out)


if __name__ == "__main__":
    unittest.main()
