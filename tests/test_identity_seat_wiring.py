"""Identity seat, batch wiring (identity-surgery
program, 2026-10-30): the CANDIDATES stage resolves
the profile's identity_seat_decisions declaration,
loads the frozen verdicts once, and threads a
per-document seat into the guard. These tests run
the real stage with a scripted proposer: with the
declaration, the seat's frozen verdict decides the
surname completion the rule chain refuses; without
it, the same run stands; a declared file that
cannot be loaded fails the stage closed by name.
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

from pipeline import batch_run, proposer
from pipeline.batch import Batch, BatchConfig, BatchStatus, batch_dir, save_batch

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
    "December 2000, it reached number three on the "
    "Oricon DVD Chart. The accompanying music video "
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


class TestIdentitySeatWiring(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def _candidates(self, d):
        return [json.loads(line) for line in
                (d / "candidates.jsonl").read_text().splitlines()
                if line.strip()]

    def test_declared_seat_decides_the_completion(self):
        decisions = [{
            "doc": proposer.identity_doc_key(DOC),
            "ep": "hamasaki", "cand": "ayumi hamasaki",
            "decision": "complete"}]
        (self.root / "seat_decisions.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in decisions))
        b, d = _run_stage(
            self.root, "seat-on",
            {"identity_seat_decisions": "seat_decisions.jsonl"})
        cands = self._candidates(d)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["object"], "Ayumi Hamasaki")
        seat_metrics = b.metrics["candidates"]["identity_seat"]
        self.assertEqual(seat_metrics["n_decisions"], 1)
        self.assertGreaterEqual(seat_metrics["opinions"], 1)
        self.assertEqual(
            b.metrics["candidates"]["proposer"]
            ["identity_seat_opinions"],
            seat_metrics["opinions"])

    def test_no_declaration_rules_decide(self):
        b, d = _run_stage(self.root, "seat-off", {})
        cands = self._candidates(d)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["object"], "Hamasaki")
        self.assertNotIn("identity_seat",
                         b.metrics["candidates"])
        self.assertEqual(
            b.metrics["candidates"]["proposer"]
            ["identity_seat_opinions"], 0)

    def test_declared_but_missing_file_fails_closed(self):
        with self.assertRaises(ValueError) as cm:
            _run_stage(
                self.root, "seat-missing",
                {"identity_seat_decisions": "nope.jsonl"})
        self.assertIn("identity seat decisions",
                      str(cm.exception))


if __name__ == "__main__":
    unittest.main()
