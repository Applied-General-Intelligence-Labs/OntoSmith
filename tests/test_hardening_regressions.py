"""Hardening regression tests.

Each test pins one branch the code audit found silently
dishonest: a join that overwrote or defaulted scores, a drift
audit that could pass without comparing anything, swallowed
extraction failures, an invented text-judge label, an echoed
profile hash, and a "confirmed" verdict no comparison produced.
The verify-guard and resolution tests live in
tests/test_drift_check.py beside the audit's own suite; the
gate-eval error shapes are pinned by the regenerated goldens in
tests/test_gate_cli.py.
"""
import json
import pickle
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from pipeline import (
    batch_run,
    connections,
    decisions,
    evaluate,
    promotion,
    proposer,
    query_stack,
    setup_service,
)
from pipeline import gate_cli as g
from pipeline.batch import Batch, BatchConfig, batch_dir, save_state


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


class TmpRoot(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)


# ---------------------------------------------------------------- F1

class TestAttestationJoin(TmpRoot):
    """query_stack.load_attestations: per-attestation scores."""

    def setUp(self):
        super().setUp()
        self.bdir = self.root / "batch"
        self.edir = self.bdir / "eval"
        self.edir.mkdir(parents=True)

    def _run(self, judged_rows, surface_rows):
        _write_jsonl(self.bdir / "routed_v6_judged.jsonl",
                     judged_rows)
        _write_jsonl(self.edir / "p3b_surface_map.jsonl",
                     surface_rows)
        return query_stack.load_attestations(self.bdir, self.edir)

    @staticmethod
    def _judged(p, tier=1, method="llm-full"):
        return {"subject": "S", "object": "O", "relation": "rel",
                "doc_id": "d1", "p_true": p, "tier": tier,
                "method": method}

    @staticmethod
    def _surface(p=None, tier=None, s_id="e1"):
        row = {"s_id": s_id, "o_id": "e2", "type": "rel",
               "doc_id": "d1", "surface_s": "S", "surface_o": "O",
               "quote": "q"}
        if p is not None:
            row["p_true"] = p
        if tier is not None:
            row["tier"] = tier
        return row

    def test_duplicate_judged_keys_keep_per_attestation_scores(self):
        # The pre-fix dict kept only the LAST judged row per key
        # (0.98) and applied it to every attestation of the key.
        judged = [self._judged(0.99, method="clause-llm"),
                  self._judged(0.98, method="llm-full")]
        surface = [self._surface(0.42, 1, "e1"),
                   self._surface(0.77, 2, "e3")]
        atts, _toks, stats = self._run(judged, surface)
        self.assertEqual([a["p_true"] for a in atts], [0.42, 0.77])
        self.assertEqual([a["tier"] for a in atts], [1, 2])
        self.assertEqual(stats, {"unmatched": 0, "unscored": 0})

    def test_unmatched_is_counted_not_defaulted(self):
        # No judged row for the key: the pre-fix code silently
        # scored (0.3, 2). The row's own projected values are
        # used and the miss is counted.
        atts, _toks, stats = self._run(
            [], [self._surface(0.66, 1)])
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0]["p_true"], 0.66)
        self.assertEqual(atts[0]["tier"], 1)
        self.assertEqual(stats["unmatched"], 1)
        self.assertEqual(stats["unscored"], 0)

    def test_fallback_to_judged_and_unscored_exclusion(self):
        # A surface row with no values of its own falls back to
        # the judged row for its key; one with no score anywhere
        # is excluded and counted, never defaulted.
        judged = [self._judged(0.55, 2)]
        bare = self._surface()
        ghost = self._surface(s_id="e9")
        ghost["surface_s"] = "Other"
        atts, _toks, stats = self._run(judged, [bare, ghost])
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0]["p_true"], 0.55)
        self.assertEqual(atts[0]["tier"], 2)
        self.assertEqual(stats["unscored"], 1)


# ------------------------------------------------------- F17(a) rer

class _FakeBinding:
    """Minimal seat-binding double (the bindings surface: the
    portable dialect, no capabilities, no reported cost)."""
    chat_url = "http://stub/chat"
    is_openrouter_dialect = False
    price_in_per_mtok = None
    price_out_per_mtok = None

    @property
    def reports_cost(self):
        return False

    def has_capability(self, _cap, _model=None):
        return False

    def key_or_none(self):
        return "stub-key"

    def require_key(self):
        return "stub-key"


class _FakeResolved:
    def __init__(self):
        self.declared = set()

    def seat(self, _name):
        return _FakeBinding()


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


# ------------------------------------------- F17(a/d/e) CANDIDATES

class TestCandidatesCounters(TmpRoot):
    def test_checkpoint_reset_is_stamped_never_silent(self):
        # The surviving intent of the retired pipeline-era
        # counters test: a checkpoint file that cannot be
        # parsed resets the stage's resume state — and the
        # reset must be logged and stamped into the metrics,
        # never silent. Driven on the LLM path (the only
        # candidate source since 2026-10-29) with the
        # proposer chat mocked to propose nothing.
        d = batch_dir(self.root, "cand-test")
        d.mkdir(parents=True)
        _write_jsonl(d / "texts.jsonl", [{
            "doc_id": "doc-1",
            "text": "Anna met Brian in Paris. Carol joined "
                    "them later that day.",
            "source_path": "doc-1.txt"}])
        (d / "candidates.ckpt.json").write_text("{not json")
        b = Batch(batch_id="cand-test", run_id="run-1",
                  source="corp", collection="docs",
                  doc_ids=["doc-1"], config=BatchConfig())

        def empty_chat(content, model, max_tokens, key):
            return "[]", {"cost": 0.0}

        import os
        with mock.patch.dict(os.environ,
                             {"OPENROUTER_API_KEY": "stub-key"}), \
                mock.patch.object(proposer, "_chat", empty_chat):
            batch_run.stage_candidates(
                b, self.root, ["INGEST", "NER"])
        m = b.metrics["candidates"]
        self.assertEqual(m["total"], 0)
        # The corrupted checkpoint reset is on the record.
        self.assertIn("error", m["checkpoint_reset"])


# ------------------------------------------------------------ F11

class _FakeEmbedder:
    def __init__(self):
        self.usage = {"input_tokens": 0, "cost": 0.0}

    def embed(self, chunks):
        import numpy as np
        return np.zeros((len(chunks), 3))


class TestStageIndexSorted(TmpRoot):
    def test_tier2_doc_order_is_sorted(self):
        d = batch_dir(self.root, "idx-test")
        d.mkdir(parents=True)
        text = "x" * 1500
        _write_jsonl(d / "texts.jsonl", [
            {"doc_id": "doc-b", "text": text},
            {"doc_id": "doc-a", "text": text}])
        _write_jsonl(d / "routed.jsonl", [
            {"doc_id": "doc-b", "tier": 2},
            {"doc_id": "doc-a", "tier": 2}])
        b = Batch(batch_id="idx-test", run_id="run-1",
                  source="corp", collection="docs",
                  doc_ids=["doc-a", "doc-b"], config=BatchConfig())
        with mock.patch.object(batch_run, "_index_embedder",
                               lambda model: _FakeEmbedder()):
            batch_run.stage_index(b, self.root,
                                  ["INGEST", "NER", "CANDIDATES",
                                   "GATES", "ROUTE", "WRITE"])
        with open(d / "tier2_index.pkl", "rb") as f:
            idx = pickle.load(f)
        self.assertEqual([m["doc_id"] for m in idx["meta"]],
                         ["doc-a", "doc-a", "doc-b", "doc-b"])


# ------------------------------------------------- F17(c) evaluate

class TestEvaluateErrorRecords(TmpRoot):
    def test_error_records_are_excluded_and_counted(self):
        rows = {"records": [
            {"p_true": 0.9, "label": 1, "doc_id": "a"},
            {"p_true": 0.0, "label": 1, "doc_id": "b",
             "error": "judge unreachable"},
            {"p_true": 0.2, "label": 0, "doc_id": "c"},
        ]}
        p = self.root / "r.json"
        p.write_text(json.dumps(rows))
        recs = evaluate.load_records(str(p))
        # The failed call (p_true=0.0, gold label 1) is not scored
        # as a judged negative; it is counted as an exclusion.
        self.assertEqual([r["doc_id"] for r in recs], ["a", "c"])
        self.assertEqual(evaluate.error_record_count(str(p)), 1)


# ------------------------------------------------------------- F8

class _EscBinding:
    """Minimal seat-binding double (the bindings surface, as
    _FakeBinding)."""
    chat_url = "http://stub/chat"
    is_openrouter_dialect = False
    price_in_per_mtok = None
    price_out_per_mtok = None

    @property
    def reports_cost(self):
        return False

    def has_capability(self, _cap, _model=None):
        return False

    def require_key(self):
        return "stub-key"


class TestTextJudgeParseFailure(TmpRoot):
    def _judge(self, content):
        payload = {"choices": [{"message": {"content": content}}],
                   "usage": {}, "provider": "FakeProv"}
        with mock.patch.object(connections, "seat",
                               lambda name: _EscBinding()), \
                mock.patch.object(requests, "post",
                                  lambda *a, **k: _FakeResp(payload)):
            return decisions.llm_text_judge(
                "doc", "S", "rel", "O", model="fake/model")

    def test_unparseable_output_is_marked(self):
        # Reasoning prose, not JSON: the answer is recovered from
        # the LAST yes/no token ("no") — and the recovery is
        # marked, not silent.
        label, usage = self._judge(
            "The text says yes to the place, but no to the date.")
        self.assertEqual(label, 0)
        self.assertTrue(usage["parse_failed"])

    def test_clean_json_is_not_marked(self):
        label, usage = self._judge('{"answer": "Yes"}')
        self.assertEqual(label, 1)
        self.assertFalse(usage["parse_failed"])

    def test_gate_cli_stamps_parse_failure(self):
        def fake_text(text, s, r, o, model=None, max_tokens=300):
            return 1, {"cost": 0.001, "latency_ms": 1.0,
                       "provider": "FakeProv",
                       "parse_failed": True}

        saved = g.llm_text_judge
        g.llm_text_judge = fake_text
        self.addCleanup(setattr, g, "llm_text_judge", saved)
        fix = Path(__file__).parent / "fixtures" / "llm.json"
        out = self.root / "llm_pf.json"
        g.main(["llm", "--candidates", str(fix), "--model",
                "fake/model", "--out", str(out)])
        data = json.loads(out.read_text())
        self.assertEqual(data["summary"]["parse_failures"], 3)
        self.assertEqual(data["summary"]["errors"], 0)
        self.assertTrue(all(r["parse_failed"]
                            for r in data["records"]))


# ------------------------------------------------------------- F9

class TestPromotionProfileHash(TmpRoot):
    def setUp(self):
        super().setUp()
        d = self.root / "batches" / "ph-test"
        d.mkdir(parents=True)
        (d / "batch.json").write_text(json.dumps(
            {"profile_hash": "sha256:abc"}))
        patcher = mock.patch.object(promotion, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_matching_hash_passes(self):
        out = promotion.check_profile_hash("ph-test", "sha256:abc")
        self.assertTrue(out["pass"])
        self.assertEqual(out["recorded_profile_hash"], "sha256:abc")

    def test_mismatched_hash_fails(self):
        out = promotion.check_profile_hash("ph-test", "sha256:zzz")
        self.assertFalse(out["pass"])
        self.assertIn("mismatch", out["missing"][0])

    def test_unrecorded_hash_fails(self):
        out = promotion.check_profile_hash("no-such-batch",
                                           "sha256:abc")
        self.assertFalse(out["pass"])


class TestSaveStateCarriesSource(TmpRoot):
    def test_state_json_writes_source(self):
        b = Batch(batch_id="st-test", run_id="run-1",
                  source="corp-x", collection="docs", doc_ids=[],
                  config=BatchConfig())
        batch_dir(self.root, "st-test").mkdir(parents=True)
        save_state(b, self.root, ["INGEST"])
        state = json.loads(
            (batch_dir(self.root, "st-test")
             / "state.json").read_text())
        self.assertEqual(state["source"], "corp-x")


# ------------------------------------------------------------ F12

class TestVerifyClaimsVerdicts(unittest.TestCase):
    def _run(self, claims, value):
        def fake_scanner(docs, gold, csv_path, params):
            return {"value": value}

        with mock.patch.dict(setup_service.SCANNERS,
                             {"fake_scan": fake_scanner}):
            return setup_service.verify_claims(
                claims, docs=[], gold=None, csv_path=None)

    def test_no_expected_is_unverifiable_not_confirmed(self):
        claims = [{"verify": {"scanner": "fake_scan"},
                   "expected": None}]
        out = self._run(claims, 5)
        self.assertEqual(out[0]["measured"], {"value": 5})
        self.assertEqual(out[0]["verdict"], "unverifiable")

    def test_nothing_measured_is_unverifiable(self):
        claims = [{"verify": {"scanner": "fake_scan"},
                   "expected": 5}]
        out = self._run(claims, None)
        self.assertEqual(out[0]["verdict"], "unverifiable")

    def test_compared_match_and_mismatch(self):
        claims = [{"verify": {"scanner": "fake_scan"},
                   "expected": 5},
                  {"verify": {"scanner": "fake_scan"},
                   "expected": 6}]
        out = self._run(claims, 5)
        self.assertEqual(out[0]["verdict"], "confirmed")
        self.assertEqual(out[1]["verdict"], "mismatch")


if __name__ == "__main__":
    unittest.main()
