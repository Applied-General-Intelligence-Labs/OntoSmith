"""Global identity freeze (identity redesign D2/D3):
the freeze step keys verdicts by the (fragment,
candidate) pair corpus-wide, promotes disagreeing
pairs to first-class ambiguous records, and the
CANDIDATES stage consumes the freeze through the
profile's identity_seat_decisions_global declaration.
Ambiguous pairs defer to the rule chain and are
surfaced in the batch's identity_ambiguous.jsonl.
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

from pipeline import batch_run, identity_freeze, proposer
from pipeline.batch import Batch, BatchConfig, BatchStatus, batch_dir, save_batch
from pipeline.profile_config import setup_config_errors

DOC = (
    '"Surreal "(stylized as "SURREAL ") is a song by '
    "Japanese recording artist Ayumi Hamasaki, taken "
    "from her third studio album Duty (2000). It was "
    "written by Hamasaki and produced by Max Matsuura. "
    "The song is a rock with elements of alternative "
    'rock. "Surreal "describes Hamasaki \'s madness and '
    "sense of confusion, while the themes of "
    '"Surreal "are based on Hamasaki \'s concept of '
    "loneliness, chaos, confusion, and the burden of "
    "her responsibilities, aimed mostly toward her "
    "public image as a recording artist. It was "
    "released as the fourth single from the album on "
    "27 September 2000 by Avex Trax and Avex Taiwan. "
    "Critical reception towards \"Surreal \"has been "
    "positive; the majority of critics commended the "
    "songwriting and musical delivery, and highlighted "
    "it as an album and career standout. In Japan, "
    '"Surreal "became her sixth number one on the '
    "Oricon Singles Chart, and also reached the top "
    "spot on the Japanese Count Down TV chart. "
    '"Surreal "was certified platinum by the Recording '
    "Industry Association of Japan (RIAJ) for shipments "
    "of 250,000 units. Released as a DVD Single in "
    "September 2000, it reached number three on the "
    "Oricon Singles Chart. The accompanying music video "
    "was directed by Wataru Takeishi, featuring "
    "Hamasaki walking along a beach front. With "
    "mysterious lighting hovering over several "
    "objects, she enters a jungle - like room to find "
    "a clone of herself; the clone wears the same "
    "outfit for the Duty and \"Surreal \"photo shoot. "
    '"Surreal "has been included on several concert '
    "tours and New Year Countdown shows held and "
    "hosted by Hamasaki, and has featured on majority "
    "of her greatest hits compilations.")

ITEMS = [{
    "subject": "Surreal", "subject_type": "CreativeWork",
    "relation": "written by", "object": "Hamasaki",
    "object_type": "Person",
    "quote": "It was written by Hamasaki and produced "
             "by Max Matsuura"}]


def _write_jsonl(path, rows):
    path.write_text("".join(
        json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def _run_stage(root, batch_id, setup_cfg):
    cfg = BatchConfig(max_cost_usd=None)
    b = Batch(batch_id=batch_id, run_id="run-0001",
              source="src", collection="coll",
              doc_ids=["d1"], config=cfg,
              status=BatchStatus.NER_DONE,
              profile_family="seat-test")
    d = batch_dir(root, batch_id)
    d.mkdir(parents=True)
    (d / "texts.jsonl").write_text(json.dumps(
        {"doc_id": "d1", "text": DOC,
         "source_path": "d1.txt"}) + "\n")
    if setup_cfg is not None:
        (d / "setup_config.resolved.json").write_text(
            json.dumps(setup_cfg))
    save_batch(b, root)

    def chat(content, model, max_tokens, key):
        return json.dumps(ITEMS), {"cost": 0.0}

    with mock.patch.dict(os.environ,
                         {"OPENROUTER_API_KEY": "stub-key"}), \
            mock.patch.object(proposer, "_chat", chat), \
            redirect_stdout(io.StringIO()):
        batch_run.stage_candidates(b, root, ["INGEST", "NER"])
    return b, d


class TestFreezeSemantics(unittest.TestCase):
    def test_unanimous_pair_freezes(self):
        frozen, ambiguous = proposer.freeze_identity_decisions([
            {"doc": "a", "ep": "x", "cand": "x full",
             "decision": "complete"},
            {"doc": "b", "ep": "x", "cand": "x full",
             "decision": "complete"},
            {"doc": "b", "ep": "x", "cand": "x full",
             "decision": None}])
        self.assertEqual(ambiguous, [])
        self.assertEqual(len(frozen), 1)
        self.assertEqual(frozen[0]["decision"], "complete")
        self.assertEqual(frozen[0]["points"], 3)

    def test_abstain_only_pair_freezes_as_no_opinion(self):
        frozen, ambiguous = proposer.freeze_identity_decisions([
            {"doc": "a", "ep": "x", "cand": "y",
             "decision": None}])
        self.assertEqual(ambiguous, [])
        self.assertIsNone(frozen[0]["decision"])

    def test_disagreeing_pair_is_promoted_ambiguous(self):
        frozen, ambiguous = proposer.freeze_identity_decisions([
            {"doc": "a", "ep": "x", "cand": "y",
             "decision": "complete"},
            {"doc": "b", "ep": "x", "cand": "y",
             "decision": "stand"}])
        self.assertEqual(frozen, [])
        self.assertEqual(len(ambiguous), 1)
        self.assertTrue(ambiguous[0]["identity_ambiguous"])
        self.assertEqual(ambiguous[0]["keep"], "both_surfaces")
        self.assertEqual(len(ambiguous[0]["conflicting"]), 2)


class TestFreezeFile(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_cli_round_trip_and_loader(self):
        points = self.root / "points.jsonl"
        freeze = self.root / "freeze.jsonl"
        _write_jsonl(points, [
            {"doc": "a", "ep": "x", "cand": "x full",
             "decision": "complete"},
            {"doc": "a", "ep": "p", "cand": "q",
             "decision": "complete"},
            {"doc": "b", "ep": "p", "cand": "q",
             "decision": "stand"}])
        with redirect_stdout(io.StringIO()):
            identity_freeze.main([str(points), str(freeze)])
        decisions, ambiguous = \
            proposer.load_identity_decisions_global(freeze)
        self.assertEqual(decisions, {("x", "x full"): True})
        self.assertEqual(ambiguous, {("p", "q")})

    def test_loader_rejects_duplicate_pair(self):
        freeze = self.root / "freeze.jsonl"
        _write_jsonl(freeze, [
            {"ep": "x", "cand": "y", "decision": "complete"},
            {"ep": "x", "cand": "y", "decision": "stand"}])
        with self.assertRaises(ValueError):
            proposer.load_identity_decisions_global(freeze)

    def test_loader_rejects_decided_ambiguous(self):
        freeze = self.root / "freeze.jsonl"
        _write_jsonl(freeze, [
            {"ep": "x", "cand": "y",
             "identity_ambiguous": True,
             "decision": "complete"}])
        with self.assertRaises(ValueError):
            proposer.load_identity_decisions_global(freeze)

    def test_loader_rejects_malformed(self):
        freeze = self.root / "freeze.jsonl"
        freeze.write_text('{"ep": "x"}\n')
        with self.assertRaises(ValueError):
            proposer.load_identity_decisions_global(freeze)


class TestGlobalSeat(unittest.TestCase):
    def test_seat_verdicts_and_contested_accounting(self):
        stats = {"consulted": 0, "opinions": 0}
        log: dict = {}
        seat = proposer.identity_seat_global(
            {("a", "a full"): True}, {("b", "b full")},
            stats=stats, contested_log=log)
        self.assertIs(seat("a", "a full", None), True)
        self.assertIsNone(seat("b", "b full", None))
        self.assertIsNone(seat("never", "judged", None))
        self.assertEqual(stats["consulted"], 3)
        self.assertEqual(stats["opinions"], 1)
        self.assertEqual(stats["contested"], 1)
        self.assertEqual(log, {("b", "b full"): 1})


class TestProfileValidation(unittest.TestCase):
    def test_both_file_forms_refused(self):
        errors = setup_config_errors({
            "identity_seat_decisions": "a.jsonl",
            "identity_seat_decisions_global": "b.jsonl"})
        self.assertTrue(any(
            "both identity decisions file forms" in e
            for e in errors), errors)

    def test_global_must_be_a_string(self):
        errors = setup_config_errors(
            {"identity_seat_decisions_global": 7})
        self.assertTrue(any(
            "identity_seat_decisions_global" in e
            for e in errors), errors)


class TestGlobalWiring(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def _candidates(self, d):
        return [json.loads(line) for line in
                (d / "candidates.jsonl").read_text().splitlines()
                if line.strip()]

    def test_global_freeze_decides_without_doc_key(self):
        _write_jsonl(self.root / "freeze.jsonl", [
            {"ep": "hamasaki", "cand": "ayumi hamasaki",
             "decision": "complete", "points": 2}])
        b, d = _run_stage(
            self.root, "global-on",
            {"identity_seat_decisions_global": "freeze.jsonl"})
        cands = self._candidates(d)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["object"], "Ayumi Hamasaki")
        seat_metrics = b.metrics["candidates"]["identity_seat"]
        self.assertEqual(seat_metrics["mode"], "global_freeze")
        self.assertEqual(seat_metrics["n_decisions"], 1)
        self.assertEqual(seat_metrics["n_ambiguous"], 0)
        self.assertTrue((d / "identity_ambiguous.jsonl").exists())
        self.assertEqual(
            (d / "identity_ambiguous.jsonl").read_text(), "")

    def test_ambiguous_pair_defers_and_is_surfaced(self):
        _write_jsonl(self.root / "freeze.jsonl", [
            {"ep": "hamasaki", "cand": "ayumi hamasaki",
             "identity_ambiguous": True,
             "keep": "both_surfaces", "route": "review_band",
             "points": 2}])
        b, d = _run_stage(
            self.root, "global-amb",
            {"identity_seat_decisions_global": "freeze.jsonl"})
        cands = self._candidates(d)
        self.assertEqual(len(cands), 1)
        # No verdict: the rule chain stands, as with no seat.
        self.assertEqual(cands[0]["object"], "Hamasaki")
        seat_metrics = b.metrics["candidates"]["identity_seat"]
        self.assertEqual(seat_metrics["n_ambiguous"], 1)
        self.assertGreaterEqual(seat_metrics["contested"], 1)
        surfaced = [json.loads(line) for line in
                    (d / "identity_ambiguous.jsonl")
                    .read_text().splitlines() if line.strip()]
        self.assertEqual(len(surfaced), 1)
        self.assertEqual(surfaced[0]["ep"], "hamasaki")
        self.assertEqual(surfaced[0]["cand"], "ayumi hamasaki")
        self.assertGreaterEqual(surfaced[0]["consultations"], 1)

    def test_both_file_forms_fail_the_stage_closed(self):
        _write_jsonl(self.root / "freeze.jsonl", [
            {"ep": "hamasaki", "cand": "ayumi hamasaki",
             "decision": "complete"}])
        _write_jsonl(self.root / "seat_decisions.jsonl", [
            {"doc": proposer.identity_doc_key(DOC),
             "ep": "hamasaki", "cand": "ayumi hamasaki",
             "decision": "complete"}])
        with self.assertRaises(ValueError) as cm:
            _run_stage(
                self.root, "global-both",
                {"identity_seat_decisions": "seat_decisions.jsonl",
                 "identity_seat_decisions_global": "freeze.jsonl"})
        self.assertIn("both identity decisions file forms",
                      str(cm.exception))

    def test_declared_but_missing_freeze_fails_closed(self):
        with self.assertRaises(ValueError) as cm:
            _run_stage(
                self.root, "global-missing",
                {"identity_seat_decisions_global": "nope.jsonl"})
        self.assertIn("identity seat freeze", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
