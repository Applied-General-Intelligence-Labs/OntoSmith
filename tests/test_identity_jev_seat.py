"""The Jev identity seat (frozen completion process):
dossier assembly, the zone rule, the verdict ledger, and
the read-through verdict map.

The contract under test is the measured process, not a
preference: dossiers carry evidence and no rule outcomes;
auto-complete requires the rule chain's completion AND a
judge score at the fitted high threshold; a score at the
low threshold auto-stands; everything between is the
review band (None). A judged point stays judged — the
ledger answers for it, because the decision model's score
is not bit-stable across calls.
"""
import json
import tempfile
import unittest
from pathlib import Path

from pipeline import identity_seat
from pipeline.identity_dossier import assemble_dossiers
from pipeline.identity_ledger import VerdictLedger

DOC = (
    "The Agency Agreement was signed in March. "
    "Agency Agreement means the Advertising Sale Agency "
    "Agreement dated March 1. The Agency Agreement binds "
    "both parties. Either party may terminate the Agency "
    "Agreement early. The Advertising Sale Agency Agreement "
    "is governed by the laws of Delaware. "
    "John Smith reviewed the Agency Agreement. "
    "Smith approved it.")


class TestDossierAssembly(unittest.TestCase):
    def test_dossier_carries_evidence_not_outcomes(self):
        dossiers = assemble_dossiers(
            DOC, [("Agency Agreement",
                   "Advertising Sale Agency Agreement")])
        d = dossiers[("Agency Agreement",
                      "Advertising Sale Agency Agreement")]
        self.assertIn("Mention A 'Agency Agreement' appears", d)
        self.assertIn("mention B 'Advertising Sale Agency "
                      "Agreement' appears", d)
        self.assertIn("Other candidates paired with A in this "
                      "document: none.", d)
        self.assertIn("Mention A sentences", d)
        self.assertIn("Mention B sentences", d)

    def test_pairing_lists_come_from_the_documents_pairs(self):
        pairs = [("Agency Agreement",
                  "Advertising Sale Agency Agreement"),
                 ("Agency Agreement", "Agency")]
        dossiers = assemble_dossiers(DOC, pairs)
        d = dossiers[("Agency Agreement",
                      "Advertising Sale Agency Agreement")]
        self.assertIn("'Agency'", d.split("\n")[2])

    def test_duplicate_pairs_collapse(self):
        pairs = [("Smith", "John Smith")] * 3
        self.assertEqual(len(assemble_dossiers(DOC, pairs)), 1)


class TestFrozenClaim(unittest.TestCase):
    def test_claim_wording_is_pinned_verbatim(self):
        # The claim is half the measured instrument: any
        # wording drift silently changes every score (the
        # W4 equivalence run caught a paraphrase doing
        # exactly that). Pin the text character for
        # character; changing it is a recalibration, not
        # an edit.
        self.assertEqual(
            identity_seat.INSTR_B,
            "Is '{b}' the complete, full name of the same "
            "entity that the mention '{a}' refers to in the "
            "text? Answer true only if the text shows that "
            "'{a}' is a shortened or partial form of exactly "
            "this entity whose full name is '{b}'. If '{b}' "
            "is itself a shortened, generic, or partial "
            "form, or the mentions refer to different "
            "entities, answer false.")


class TestZoneVerdict(unittest.TestCase):
    def test_auto_complete_needs_rules_and_high_score(self):
        self.assertIs(identity_seat.zone_verdict(0.79, True), True)
        self.assertIs(identity_seat.zone_verdict(0.95, True), True)
        # A high score alone never completes.
        self.assertIsNone(identity_seat.zone_verdict(0.95, False))
        self.assertIsNone(identity_seat.zone_verdict(0.78, True))

    def test_auto_stand_at_low_score(self):
        self.assertIs(identity_seat.zone_verdict(0.05, False), False)
        self.assertIs(identity_seat.zone_verdict(0.0, True), False)
        self.assertIsNone(identity_seat.zone_verdict(0.06, False))

    def test_review_band_between(self):
        self.assertIsNone(identity_seat.zone_verdict(0.5, True))
        self.assertIsNone(identity_seat.zone_verdict(0.5, False))


class TestVerdictLedger(unittest.TestCase):
    def test_round_trip_and_structural_invalidation(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.jsonl"
            ledger = VerdictLedger(path)
            self.assertIsNone(
                ledger.get("doc1", "A", "B", "dossier text"))
            ledger.put("doc1", "A", "B", "dossier text", 0.9, True)
            hit = ledger.get("doc1", "A", "B", "dossier text")
            self.assertEqual(hit["verdict"], True)
            self.assertEqual(hit["p"], 0.9)
            # Different evidence -> different key -> no answer.
            self.assertIsNone(
                ledger.get("doc1", "A", "B", "other dossier"))
            # The record survives a reload.
            ledger2 = VerdictLedger(path)
            self.assertEqual(
                ledger2.get("doc1", "A", "B", "dossier text")
                ["verdict"], True)

    def test_review_verdict_is_recorded_not_missing(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = VerdictLedger(Path(td) / "ledger.jsonl")
            ledger.put("doc1", "A", "B", "dossier", 0.5, None)
            hit = ledger.get("doc1", "A", "B", "dossier")
            self.assertIsNotNone(hit)
            self.assertIsNone(hit["verdict"])

    def test_malformed_record_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.jsonl"
            path.write_text('{"doc": 1}\n')
            with self.assertRaises(ValueError):
                VerdictLedger(path)


class TestVerdictMap(unittest.TestCase):
    def _run(self, ledger, scores, rules):
        points = [("Agency Agreement",
                   "Advertising Sale Agency Agreement"),
                  ("Smith", "John Smith")]
        dossiers = assemble_dossiers(
            DOC, points)
        calls = []

        def fake_score(dossier, frag, cand):
            calls.append((frag, cand))
            outcome = scores[(frag, cand)]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome, {"cost": 0.001}

        orig = identity_seat.score_dossier
        identity_seat.score_dossier = fake_score
        try:
            stats = {}
            verdicts = identity_seat.build_verdict_map(
                "doc1", points, dossiers, rules, ledger,
                stats=stats)
        finally:
            identity_seat.score_dossier = orig
        return verdicts, stats, calls

    def test_first_run_judges_second_run_reads_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = VerdictLedger(Path(td) / "ledger.jsonl")
            rules = {("Agency Agreement",
                      "Advertising Sale Agency Agreement"): True,
                     ("Smith", "John Smith"): False}
            scores = {("Agency Agreement",
                       "Advertising Sale Agency Agreement"): 0.9,
                      ("Smith", "John Smith"): 0.02}
            verdicts, stats, calls = self._run(ledger, scores, rules)
            self.assertIs(
                verdicts[("Agency Agreement",
                          "Advertising Sale Agency Agreement")], True)
            self.assertIs(verdicts[("Smith", "John Smith")], False)
            self.assertEqual(stats["judge_calls"], 2)
            self.assertEqual(stats["ledger_hits"], 0)
            # Second run: no judge calls at all.
            verdicts2, stats2, calls2 = self._run(
                ledger, scores, rules)
            self.assertEqual(verdicts2, verdicts)
            self.assertEqual(stats2["judge_calls"], 0)
            self.assertEqual(stats2["ledger_hits"], 2)
            self.assertEqual(calls2, [])

    def test_binding_failure_fails_closed_not_demoted(self):
        # BindingKeyError (a RuntimeError subclass) means
        # the seat is misconfigured: it must propagate, not
        # demote every point to a silent review.
        from pipeline.connections import BindingKeyError
        with tempfile.TemporaryDirectory() as td:
            ledger = VerdictLedger(Path(td) / "ledger.jsonl")
            points = [("Agency Agreement",
                       "Advertising Sale Agency Agreement")]
            dossiers = assemble_dossiers(DOC, points)

            def broken_score(dossier, frag, cand):
                raise BindingKeyError("judge seat: key unset")

            orig = identity_seat.score_dossier
            identity_seat.score_dossier = broken_score
            try:
                with self.assertRaises(BindingKeyError):
                    identity_seat.build_verdict_map(
                        "doc1", points, dossiers,
                        {points[0]: True}, ledger)
            finally:
                identity_seat.score_dossier = orig

    def test_point_failure_demotes_to_review(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = VerdictLedger(Path(td) / "ledger.jsonl")
            rules = {("Agency Agreement",
                      "Advertising Sale Agency Agreement"): True,
                     ("Smith", "John Smith"): False}
            scores = {("Agency Agreement",
                       "Advertising Sale Agency Agreement"):
                      RuntimeError("api down"),
                      ("Smith", "John Smith"): 0.02}
            verdicts, stats, _ = self._run(ledger, scores, rules)
            self.assertIsNone(
                verdicts[("Agency Agreement",
                          "Advertising Sale Agency Agreement")])
            self.assertEqual(stats["judge_errors"], 1)
            # The failed point was NOT recorded: a later run
            # may judge it again.
            self.assertIsNone(ledger.get(
                "doc1", "Agency Agreement",
                "Advertising Sale Agency Agreement",
                assemble_dossiers(
                    DOC, [("Agency Agreement",
                           "Advertising Sale Agency Agreement")])
                [("Agency Agreement",
                  "Advertising Sale Agency Agreement")]))


if __name__ == "__main__":
    unittest.main()
