"""Live identity seat, batch wiring: the CANDIDATES stage
with identity_seat_live declared judges rule-4 decision
points in-run through the frozen completion process and
records verdicts in the batch's ledger.

The fixture pair is chosen so the deterministic chain
alone completes it: "Agency Agreement" resolves to
"Advertising Sale Agency Agreement" with no seat. The
live seat's score therefore decides the outcome — a low
score (auto-stand zone) vetoes the completion the rules
would have made, and a high score (auto-complete zone,
rules agreeing) keeps it. A second batch seeded with the
first batch's ledger makes no judge calls at all.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from pipeline import batch_run, identity_seat, proposer
from pipeline.batch import Batch, BatchConfig, BatchStatus, batch_dir, save_batch

DOC = (
    "The Advertising Sale Agency Agreement was signed in "
    "March. Agency Agreement means the Advertising Sale "
    "Agency Agreement dated March 1. The Agency Agreement "
    "binds both parties. Either party may terminate the "
    "Agency Agreement early. The Advertising Sale Agency "
    "Agreement is governed by Delaware law.")

ITEMS = [{
    "subject": "Either party", "subject_type": "Person",
    "relation": "may terminate",
    "object": "Agency Agreement", "object_type": "Agreement",
    "quote": "Either party may terminate the Agency "
             "Agreement early"}]


def _run_stage(root, batch_id, setup_cfg, score):
    cfg = BatchConfig(max_cost_usd=None)
    b = Batch(batch_id=batch_id, run_id="run-0001",
              source="src", collection="coll",
              doc_ids=["d1"], config=cfg,
              status=BatchStatus.NER_DONE,
              profile_family="seat-test")
    d = batch_dir(root, batch_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "texts.jsonl").write_text(json.dumps(
        {"doc_id": "d1", "text": DOC,
         "source_path": "d1.txt"}) + "\n")
    if setup_cfg is not None:
        (d / "setup_config.resolved.json").write_text(
            json.dumps(setup_cfg))
    save_batch(b, root)

    def chat(content, model, max_tokens, key):
        return json.dumps(ITEMS), {"cost": 0.0}

    calls = []

    def stub_score(dossier, frag, cand):
        calls.append((frag, cand))
        return score, {"cost": 0.0001}

    with mock.patch.dict(os.environ,
                         {"OPENROUTER_API_KEY": "stub-key"}), \
            mock.patch.object(proposer, "_chat", chat), \
            mock.patch.object(identity_seat, "score_dossier",
                              stub_score), \
            redirect_stdout(io.StringIO()):
        batch_run.stage_candidates(b, root, ["INGEST", "NER"])
    return b, d, calls


class TestLiveIdentitySeatWiring(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def _candidates(self, d):
        return [json.loads(line) for line in
                (d / "candidates.jsonl").read_text().splitlines()
                if line.strip()]

    def test_low_score_vetoes_the_rules_completion(self):
        b, d, calls = _run_stage(
            self.root, "live-low", {"identity_seat_live": True},
            score=0.02)
        cands = self._candidates(d)
        self.assertEqual(len(cands), 1)
        # Rules alone complete this pair; the seat's
        # auto-stand verdict keeps the fragment.
        self.assertEqual(cands[0]["object"], "Agency Agreement")
        self.assertTrue(calls)
        seat_metrics = b.metrics["candidates"]["identity_seat"]
        self.assertEqual(seat_metrics["mode"], "live")
        self.assertGreaterEqual(seat_metrics["judge_calls"], 1)
        # The verdict is in the ledger, zone-consistent.
        records = [json.loads(line) for line in
                   (d / "identity_verdicts.jsonl")
                   .read_text().splitlines() if line.strip()]
        target = [r for r in records
                  if r["cand"] ==
                  "Advertising Sale Agency Agreement"]
        self.assertEqual(len(target), 1)
        self.assertEqual(target[0]["p"], 0.02)
        self.assertIs(target[0]["verdict"], False)

    def test_high_score_keeps_the_rules_completion(self):
        _b, d, _calls = _run_stage(
            self.root, "live-high", {"identity_seat_live": True},
            score=0.95)
        cands = self._candidates(d)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["object"],
                         "Advertising Sale Agency Agreement")

    def test_seeded_ledger_answers_without_judge_calls(self):
        _b1, d1, calls1 = _run_stage(
            self.root, "live-first", {"identity_seat_live": True},
            score=0.02)
        self.assertTrue(calls1)
        d2 = batch_dir(self.root, "live-second")
        d2.mkdir(parents=True)
        shutil.copy(d1 / "identity_verdicts.jsonl",
                    d2 / "identity_verdicts.jsonl")
        b2, d2, calls2 = _run_stage(
            self.root, "live-second",
            {"identity_seat_live": True}, score=0.95)
        # The ledger's frozen low-score verdict answers —
        # the (different) live score is never consulted.
        self.assertEqual(calls2, [])
        cands = self._candidates(d2)
        self.assertEqual(cands[0]["object"], "Agency Agreement")
        seat_metrics = b2.metrics["candidates"]["identity_seat"]
        self.assertEqual(seat_metrics["judge_calls"], 0)
        self.assertGreaterEqual(seat_metrics["ledger_hits"], 1)

    def test_decisions_file_supersedes_the_live_default(self):
        # A resolved config carrying both forms is the
        # normal post-merge shape (core live default +
        # a pack's decisions file): the file form governs
        # and no live judging happens.
        (self.root / "seat_decisions.jsonl").write_text("")
        b, d, calls = _run_stage(
            self.root, "live-both",
            {"identity_seat_live": True,
             "identity_seat_decisions": "seat_decisions.jsonl"},
            score=0.5)
        seat_metrics = b.metrics["candidates"]["identity_seat"]
        self.assertIn("path", seat_metrics)
        self.assertNotEqual(seat_metrics.get("mode"), "live")
        self.assertFalse(calls)
        self.assertFalse((d / "identity_verdicts.jsonl").exists())

    def test_live_seat_is_the_core_pack_default(self):
        # The default lives in pack data: resolving the
        # core baseline yields the live seat on, and a
        # stage run under that config judges live.
        from pipeline.profile_config import resolve_setup_config
        root = Path(__file__).resolve().parents[1]
        cfg = resolve_setup_config(root)
        self.assertIs(cfg.get("identity_seat_live"), True)
        b, _d, calls = _run_stage(
            self.root, "live-default", cfg, score=0.02)
        seat_metrics = b.metrics["candidates"]["identity_seat"]
        self.assertEqual(seat_metrics["mode"], "live")
        self.assertTrue(calls)


class TestSeatFormValidation(unittest.TestCase):
    """One authored layer declaring both seat forms is
    refused; the merged shape is not."""

    def test_single_layer_both_forms_is_an_error(self):
        from pipeline.profile_config import setup_config_errors
        errs = setup_config_errors(
            {"identity_seat_live": True,
             "identity_seat_decisions": "x.jsonl"})
        self.assertTrue(any("both identity seat forms" in e
                            for e in errs), errs)

    def test_merged_both_forms_is_the_normal_supersede(self):
        from pipeline.profile_config import setup_config_errors
        errs = setup_config_errors(
            {"identity_seat_live": True,
             "identity_seat_decisions": "x.jsonl"}, merged=True)
        self.assertFalse(any("both identity seat forms" in e
                             for e in errs), errs)

    def test_live_flag_must_be_boolean(self):
        from pipeline.profile_config import setup_config_errors
        errs = setup_config_errors({"identity_seat_live": "yes"})
        self.assertTrue(any("must be a boolean" in e
                            for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
