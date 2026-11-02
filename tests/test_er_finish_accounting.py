"""ER finish-line accounting tests (issue #51).

The defect: a build could finish VERIFIED with most of its
ER decisions held for human settlement and nothing at the
finish line said so — WRITE's stage metrics carried the
count, but the batch's own completion report never totaled
ER decisions by terminal state (multilingual pilot,
2026-10-27: 49 decisions, 44 held, finish line silent).

The hold itself is the product's measured design (a human
decision, never an auto-merge) and is NOT changed here.
What is pinned: at completion the batch accounts merged /
settled / held-unactioned in its metrics and log, and
unactioned holds produce a loud PENDING line naming the
settlement command; once settled, the line is gone.
"""
import argparse
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from pipeline import batch_cli, batch_run
from pipeline.batch import (
    Batch,
    BatchConfig,
    batch_dir,
    load_batch,
    save_batch,
)
from pipeline.file_registry import SqliteEntityRegistry

BID = "er-finish"


def _registry(tmp: Path) -> SqliteEntityRegistry:
    reg = SqliteEntityRegistry(tmp / "data" / "registry.db")
    # One automatic merge (gate / deterministic channel).
    reg.record_er_decision(
        BID, "run-1", "Auto Merged Ltd", "ent:a", "Auto Merged",
        model="typesafe/jev-1.13", p_same=0.91, outcome="merged")
    # Held pair 1: never settled.
    reg.record_er_decision(
        BID, "run-1", "Held One", "ent:b", "Held One Canonical",
        model="typesafe/jev-1.13", p_same=0.97, outcome="review")
    # Held pair 2: settled keep-separate.
    reg.record_er_decision(
        BID, "run-1", "Held Two", "ent:c", "Held Two Canonical",
        model="typesafe/jev-1.13", p_same=0.66, outcome="review")
    reg.record_er_decision(
        BID, "run-1", "Held Two", "ent:c", "Held Two Canonical",
        model="human", p_same=0.66, outcome="kept_separate",
        reason="human settlement of review pair #3")
    # Held pair 3: settled merge (the fold's own merged row
    # carries the settlement marker as its reason).
    reg.record_er_decision(
        BID, "run-1", "Held Three", "ent:d",
        "Held Three Canonical", model="typesafe/jev-1.13",
        p_same=0.98, outcome="review")
    reg.record_er_decision(
        BID, "run-1", "Held Three", "ent:d",
        "Held Three Canonical", model="human", p_same=0.98,
        outcome="merged",
        reason="human settlement of review pair #5")
    return reg


class TestErTerminalAccount(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "data").mkdir(parents=True)
        self.reg = _registry(self.tmp)
        self.addCleanup(self.reg.close)

    def test_counts_by_terminal_state(self):
        account = batch_run._er_terminal_account(self.reg, BID)
        self.assertEqual(account, {
            "merged": 2, "held_total": 3, "held_settled_merge": 1,
            "held_settled_keep_separate": 1, "held_unactioned": 1})

    def test_other_batch_is_not_counted(self):
        account = batch_run._er_terminal_account(self.reg, "nope")
        self.assertEqual(account["merged"], 0)
        self.assertEqual(account["held_total"], 0)
        self.assertEqual(account["held_unactioned"], 0)

    def test_registry_without_decision_store_returns_none(self):
        self.assertIsNone(
            batch_run._er_terminal_account(object(), BID))


class TestFinishSummary(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "data").mkdir(parents=True)
        self.reg = _registry(self.tmp)
        self.addCleanup(self.reg.close)
        self.b = Batch(batch_id=BID, run_id="run-1",
                       source="corpus", collection="docs",
                       doc_ids=[],
                       config=BatchConfig(max_cost_usd=None))
        batch_dir(self.tmp, BID).mkdir(parents=True, exist_ok=True)
        save_batch(self.b, self.tmp)

    def _finish(self) -> str:
        out = io.StringIO()
        with redirect_stdout(out):
            batch_run._finish_summary(self.b, self.tmp, self.reg)
        return out.getvalue()

    def test_unactioned_holds_are_loud_and_in_metrics(self):
        text = self._finish()
        self.assertIn("ER SETTLEMENT PENDING: 1 held pair(s)",
                      text)
        self.assertIn(f"ontosmith batch er-review {BID}", text)
        er = self.b.metrics["finish"]["er"]
        self.assertEqual(er["held_unactioned"], 1)
        self.assertEqual(er["merged"], 2)

    def test_settling_the_last_hold_silences_the_flag(self):
        self._finish()
        self.reg.record_er_decision(
            BID, "run-1", "Held One", "ent:b",
            "Held One Canonical", model="human", p_same=0.97,
            outcome="kept_separate",
            reason="human settlement of review pair #2")
        text = self._finish()
        self.assertNotIn("SETTLEMENT PENDING", text)
        self.assertEqual(
            self.b.metrics["finish"]["er"]["held_unactioned"], 0)


class TestStatusErAccounting(unittest.TestCase):
    """`batch status` presents the finish ER account from
    the decisions ledger, not the finish-time snapshot
    (0.1.4 hands-on gate finding): a batch finished with
    held pairs, every pair was settled afterwards, and
    status still reported the finish-time counts —
    held_settled_keep_separate = 0, held_unactioned = 11 —
    while er-review, reading the ledger live, correctly
    showed all settled. Settlement postdates the finish
    by design, so the stored snapshot can never move;
    status must derive the account at read time. The
    stored batch.json stays the at-finish record."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "data").mkdir(parents=True)
        self.env = mock.patch.dict(
            os.environ, {"ONTOSMITH_ROOT": str(self.tmp)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.reg = _registry(self.tmp)
        self.addCleanup(self.reg.close)
        self.b = Batch(batch_id=BID, run_id="run-1",
                       source="corpus", collection="docs",
                       doc_ids=[],
                       config=BatchConfig(max_cost_usd=None))
        batch_dir(self.tmp, BID).mkdir(parents=True, exist_ok=True)
        save_batch(self.b, self.tmp)
        # The finish, as run_batch performs it: snapshots
        # the account (one hold still unactioned) into
        # batch.json.
        with redirect_stdout(io.StringIO()):
            batch_run._finish_summary(self.b, self.tmp, self.reg)

    def _status_er(self) -> dict:
        out = io.StringIO()
        with redirect_stdout(out):
            batch_cli.cmd_status(
                argparse.Namespace(batch_id=BID))
        return json.loads(out.getvalue())["metrics"]["finish"]["er"]

    def test_status_reflects_post_finish_settlement(self):
        # Before settlement, status agrees with the finish.
        self.assertEqual(self._status_er()["held_unactioned"], 1)
        # Settle the last hold, recorded exactly as
        # cmd_er_decide records it — in the ledger only;
        # nothing rewrites batch.json.
        self.reg.record_er_decision(
            BID, "run-1", "Held One", "ent:b",
            "Held One Canonical", model="human", p_same=0.97,
            outcome="kept_separate",
            reason="human settlement of review pair #2")
        self.assertEqual(self._status_er(), {
            "merged": 2, "held_total": 3, "held_settled_merge": 1,
            "held_settled_keep_separate": 2, "held_unactioned": 0})
        # The stored record is untouched: status is a read,
        # and batch.json keeps the at-finish snapshot for
        # every other reader.
        stored = load_batch(self.tmp, BID)
        self.assertEqual(
            stored.metrics["finish"]["er"]["held_unactioned"], 1)

    def test_status_falls_back_when_no_ledger(self):
        # A state root with no registry file: status
        # presents the stored snapshot rather than
        # manufacturing zero counts from an empty ledger.
        other = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, other, True)
        snapshot = {"merged": 0, "held_total": 11,
                    "held_settled_merge": 0,
                    "held_settled_keep_separate": 0,
                    "held_unactioned": 11}
        b2 = Batch(batch_id="snap", run_id="run-1",
                   source="corpus", collection="docs",
                   doc_ids=[],
                   config=BatchConfig(max_cost_usd=None))
        b2.metrics["finish"] = {"er": snapshot}
        batch_dir(other, "snap").mkdir(parents=True,
                                       exist_ok=True)
        save_batch(b2, other)
        out = io.StringIO()
        with mock.patch.dict(os.environ,
                             {"ONTOSMITH_ROOT": str(other)}):
            with redirect_stdout(out):
                batch_cli.cmd_status(
                    argparse.Namespace(batch_id="snap"))
        er = json.loads(out.getvalue())["metrics"]["finish"]["er"]
        self.assertEqual(er, snapshot)


if __name__ == "__main__":
    unittest.main()
