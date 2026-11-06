"""Ontology builder inheritance (pipeline/ontology_builder.py):
seeded revisions, hole reports, and the drift diff.

Covered: the draft context carries the seed schema_front and
the hole report; a seeded draft records its lineage and the
deterministic path inherits the seed front; validation holds
a revision to the inheritance rules — a dropped seed type, a
dropped signature pair, or a rewritten inherited definition
each need the matching structured decision point, a pair
addition needs its definition revision or an extend_signature
declaration, and free-text points never justify; malformed
seed-change records justify nothing and are findings
themselves; the drift diff reports (never merges) removals,
definition rewrites, signature changes and inverse
candidates; and the CLI grows draft --seed/--holes and the
diff subcommand. With the measured adoption (fix-run4),
the CLI also revises the published pack by default: a
draft for an existing pack id inherits it unless --fresh
is given, and an explicit --seed wins over the default.
"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import ontology_builder as ob


def _profile() -> dict:
    return {
        "family": "fieldnotes",
        "profile_hash": "sha256:0123456789abcdef",
        "inventory": {"n_files": 40, "n_unique_content": 40,
                      "total_chars": 120000},
        "claims": [],
    }


def _good_schema() -> dict:
    return {
        "purpose": "Field-note schema: people, the organisations "
                   "they work for, and employment links.",
        "entity_inventory": [
            {"type": "Person", "role": "entity",
             "definition": "A named individual person.",
             "form_examples": ["a full personal name"],
             "not_entities": {"counts": ["3 researchers"]}},
            {"type": "Organization", "role": "entity",
             "definition": "A named company, agency or "
                           "institution.",
             "form_examples": ["a company name"],
             "not_entities": {"generic": ["the company"]}},
        ],
        "relation_inventory": [
            {"type": "WorksFor",
             "definition": "The text states the person is "
                           "employed by the organisation.",
             "endpoint_signature": ["Person", "Organization"],
             "boundary_examples": [
                 "'joined Acme in 2020' states employment; "
                 "merely mentioning both names does not"],
             "direction": "directed"},
        ],
    }


def _seed_payload() -> dict:
    return {"pack_id": "field-notes", "pack_version": "0.1.0",
            "schema_front": _good_schema()}


def _stub_drafter(raw):
    def drafter(context):
        return copy.deepcopy(raw)
    return drafter


def _seeded_draft(raw, **kw) -> dict:
    return ob.draft_schema(
        _profile(), "field notes about researchers",
        family="fieldnotes", pack_id="field-notes",
        display_name="Field Notes",
        drafter=_stub_drafter(raw), seed=_seed_payload(), **kw)


def _codes(findings) -> set[str]:
    return {f["code"] for f in findings}


class TestSeedContext(unittest.TestCase):

    def test_context_carries_seed_and_hole_report(self):
        ctx = ob.build_draft_context(
            _profile(), "brief", seed=_seed_payload(),
            hole_report=["venue in",
                         {"family": "member of",
                          "kind": "signature"}])
        self.assertEqual(ctx["seed_schema_front"],
                         _good_schema())
        self.assertEqual(ctx["hole_report"],
                         [{"family": "venue in"},
                          {"family": "member of",
                           "kind": "signature"}])

    def test_context_without_seed_is_explicitly_unseeded(self):
        ctx = ob.build_draft_context(_profile(), "brief")
        self.assertIsNone(ctx["seed_schema_front"])
        self.assertEqual(ctx["hole_report"], [])

    def test_bad_seed_and_bad_hole_report_refuse_loudly(self):
        with self.assertRaises(ValueError):
            ob.build_draft_context(_profile(), "b",
                                   seed={"no_schema": 1})
        with self.assertRaises(ValueError):
            ob.build_draft_context(
                _profile(), "b", hole_report=[{"kind": "x"}])
        with self.assertRaises(ValueError):
            ob.build_draft_context(_profile(), "b",
                                   hole_report="not-a-list")


class TestSeededDrafting(unittest.TestCase):

    def test_deterministic_seeded_draft_inherits_front(self):
        draft = ob.draft_schema(
            _profile(),
            {"text": "researcher field notes",
             "entity_types": ["Robot"],
             "relation_types": []},
            family="fieldnotes", pack_id="field-notes",
            display_name="Field Notes", seed=_seed_payload())
        # The seed front is inherited whole; a brief-named
        # type the seed lacks is appended as an empty entry
        # whose gaps validation names.
        names = draft["entity_types"]
        self.assertIn("Person", names)
        self.assertIn("Organization", names)
        self.assertIn("Robot", names)
        self.assertEqual(draft["seed"]["source"]["pack_id"],
                         "field-notes")
        self.assertTrue(draft["seed"]["schema_hash"]
                        .startswith("sha256:"))
        self.assertEqual(draft["provenance"]["seed"]
                         ["schema_hash"],
                         draft["seed"]["schema_hash"])
        codes = _codes(ob.validate_schema_draft(draft))
        self.assertIn("MISSING_DEFINITION", codes)  # Robot
        self.assertNotIn("SEED_REMOVAL_UNJUSTIFIED", codes)

    def test_unchanged_revision_validates_clean(self):
        draft = _seeded_draft({"schema_front": _good_schema()})
        self.assertEqual(ob.validate_schema_draft(draft), [])


class TestSeedLineageValidation(unittest.TestCase):

    def test_dropped_type_needs_recorded_reason(self):
        schema = _good_schema()
        schema["entity_inventory"] = \
            schema["entity_inventory"][:1]
        schema["relation_inventory"][0]["endpoint_signature"] = \
            ["Person"]
        draft = _seeded_draft({"schema_front": schema})
        codes = _codes(ob.validate_schema_draft(draft))
        self.assertIn("SEED_REMOVAL_UNJUSTIFIED", codes)
        # The same drop with a structured record validates.
        draft = _seeded_draft({
            "schema_front": schema,
            "decision_points": [
                {"action": "remove_type", "kind": "entity",
                 "type": "Organization",
                 "reason": "the revised corpus carries no "
                           "organisations"},
                {"action": "remove_signature", "kind": "relation",
                 "type": "WorksFor", "detail": "Organization",
                 "reason": "no organisations remain to employ "
                           "anyone"}]})
        codes = _codes(ob.validate_schema_draft(draft))
        self.assertNotIn("SEED_REMOVAL_UNJUSTIFIED", codes)
        self.assertNotIn("SEED_SIGNATURE_UNJUSTIFIED", codes)

    def test_free_text_point_never_justifies(self):
        schema = _good_schema()
        schema["entity_inventory"] = \
            schema["entity_inventory"][:1]
        schema["relation_inventory"][0]["endpoint_signature"] = \
            ["Person"]
        draft = _seeded_draft({
            "schema_front": schema,
            "decision_points": [
                "Organization is dropped in this revision"]})
        self.assertIn("SEED_REMOVAL_UNJUSTIFIED",
                      _codes(ob.validate_schema_draft(draft)))

    def test_dropped_signature_pair_needs_its_own_record(self):
        schema = _good_schema()
        schema["relation_inventory"][0]["endpoint_signature"] = \
            ["Person"]
        draft = _seeded_draft({"schema_front": schema})
        self.assertIn("SEED_SIGNATURE_UNJUSTIFIED",
                      _codes(ob.validate_schema_draft(draft)))
        draft = _seeded_draft({
            "schema_front": schema,
            "decision_points": [
                {"action": "remove_signature", "kind": "relation",
                 "type": "WorksFor", "detail": "Organization",
                 "reason": "employment statements no longer "
                           "appear in the corpus"}]})
        self.assertEqual(ob.validate_schema_draft(draft), [])

    def test_silent_definition_rewrite_is_a_finding(self):
        schema = _good_schema()
        schema["entity_inventory"][0]["definition"] = \
            "A named individual person holding a doctorate."
        draft = _seeded_draft({"schema_front": schema})
        self.assertIn("SEED_DEFINITION_UNDECLARED",
                      _codes(ob.validate_schema_draft(draft)))
        draft = _seeded_draft({
            "schema_front": schema,
            "decision_points": [
                {"action": "narrow_definition", "kind": "entity",
                 "type": "Person",
                 "reason": "the corpus's persons are all "
                           "doctorate holders; recorded so "
                           "the narrowing is a decision"}]})
        self.assertEqual(ob.validate_schema_draft(draft), [])

    def test_pair_addition_rides_with_definition_or_record(self):
        schema = _good_schema()
        schema["relation_inventory"][0]["endpoint_signature"] = \
            ["Person", "Organization", "Person~Organization"]
        # Definition unchanged, no record: undeclared.
        draft = _seeded_draft({"schema_front": schema})
        self.assertIn("SEED_SIGNATURE_UNDECLARED",
                      _codes(ob.validate_schema_draft(draft)))
        # An extend_signature record settles it — with the
        # evidence the pair rests on.
        draft = _seeded_draft({
            "schema_front": schema,
            "decision_points": [
                {"action": "extend_signature", "kind": "relation",
                 "type": "WorksFor",
                 "evidence": "the corpus states employment "
                             "with person first and organisation "
                             "second throughout ('Ada joined "
                             "Acme in 2020')",
                 "reason": "the definition already speaks of "
                           "employment statements generally; "
                           "the pair form names the same "
                           "reading"}]})
        self.assertEqual(ob.validate_schema_draft(draft), [])
        # The same record without evidence justifies nothing.
        draft = _seeded_draft({
            "schema_front": schema,
            "decision_points": [
                {"action": "extend_signature", "kind": "relation",
                 "type": "WorksFor",
                 "reason": "the definition already speaks of "
                           "employment statements generally"}]})
        self.assertIn("BAD_SEED_CHANGE",
                      _codes(ob.validate_schema_draft(draft)))
        # Or the definition revision itself, recorded.
        schema2 = _good_schema()
        schema2["relation_inventory"][0][
            "endpoint_signature"] = ["Person", "Organization",
                                     "Person~Organization"]
        schema2["relation_inventory"][0]["definition"] = (
            "The text states the person is employed by the "
            "organisation, in either argument order.")
        draft = _seeded_draft({
            "schema_front": schema2,
            "decision_points": [
                {"action": "revise_definition", "kind": "relation",
                 "type": "WorksFor",
                 "reason": "widened with the pair addition so "
                           "the definition covers both "
                           "orders"}]})
        self.assertEqual(ob.validate_schema_draft(draft), [])

    def test_malformed_seed_change_record_justifies_nothing(self):
        schema = _good_schema()
        schema["entity_inventory"] = \
            schema["entity_inventory"][:1]
        schema["relation_inventory"][0]["endpoint_signature"] = \
            ["Person"]
        draft = _seeded_draft({
            "schema_front": schema,
            "decision_points": [
                {"action": "remove_type", "kind": "entity",
                 "type": "Organization"}]})  # no reason
        codes = _codes(ob.validate_schema_draft(draft))
        self.assertIn("BAD_SEED_CHANGE", codes)
        self.assertIn("SEED_REMOVAL_UNJUSTIFIED", codes)


class TestDriftDiff(unittest.TestCase):

    def test_diff_reports_removals_and_signature_changes(self):
        a = _good_schema()
        b = _good_schema()
        b["entity_inventory"] = b["entity_inventory"][:1]
        b["relation_inventory"][0]["endpoint_signature"] = \
            ["Person"]
        b["relation_inventory"][0]["definition"] = \
            "Employment, stated in the text."
        b["relation_inventory"].append({
            "type": "Mentors",
            "definition": "Guidance of a junior person.",
            "endpoint_signature": ["Person~Person"],
            "boundary_examples": ["'took her under his wing'"]})
        diff = ob.schema_front_diff(a, b)
        self.assertEqual(diff["entities_removed"],
                         ["Organization"])
        self.assertEqual(diff["relations_added"], ["Mentors"])
        self.assertEqual(diff["signature_changes"],
                         [{"type": "WorksFor", "added": [],
                           "removed": ["Organization"]}])
        self.assertIn({"kind": "relation", "type": "WorksFor"},
                      diff["definitions_changed"])
        report = ob.render_schema_diff(diff, "seed", "fresh")
        self.assertIn("REMOVED entity: Organization", report)
        self.assertIn("never", report)  # report, never a merge

    def test_inverse_candidates_need_declarations(self):
        a = _good_schema()
        b = _good_schema()
        b["relation_inventory"].append({
            "type": "Employs",
            "definition": "The organisation employs the person.",
            "endpoint_signature": ["Organization~Person"],
            "boundary_examples": ["'Acme employs her'"]})
        b["relation_inventory"][0]["endpoint_signature"] = \
            ["Person~Organization"]
        diff = ob.schema_front_diff(a, b)
        self.assertEqual(len(diff["inverse_candidates"]), 1)
        cand = diff["inverse_candidates"][0]
        self.assertEqual({cand["a_relation"],
                          cand["b_relation"]},
                         {"WorksFor", "Employs"})
        # Declaring the inverse retires the candidate.
        b["relation_inventory"][1]["inverse_of"] = "WorksFor"
        diff = ob.schema_front_diff(a, b)
        self.assertEqual(diff["inverse_candidates"], [])


class TestSeededCli(unittest.TestCase):

    def test_cli_draft_with_seed_and_diff(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            profile_path = td / "profile.json"
            profile_path.write_text(json.dumps(_profile()))
            seed_path = td / "seed.json"
            seed_path.write_text(json.dumps(_seed_payload()))
            draft_path = td / "draft.json"
            rc = ob.main(["draft", "--profile",
                          str(profile_path),
                          "--pack-id", "field-notes",
                          "--seed", str(seed_path),
                          "--out", str(draft_path)])
            self.assertEqual(rc, 0)
            draft = json.loads(draft_path.read_text())
            self.assertEqual(draft["entity_types"],
                             ["Person", "Organization"])
            self.assertIn("seed", draft)
            self.assertEqual(
                ob.main(["validate", str(draft_path)]), 0)
            report_path = td / "drift.md"
            rc = ob.main(["diff", str(seed_path),
                          str(draft_path),
                          "--out", str(report_path)])
            self.assertEqual(rc, 0)
            self.assertIn("Schema drift report",
                          report_path.read_text())

    def test_cli_draft_with_hole_report(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            profile_path = td / "profile.json"
            profile_path.write_text(json.dumps(_profile()))
            holes_path = td / "holes.json"
            holes_path.write_text(json.dumps(
                {"families": [{"family": "member of",
                               "kind": "signature"}]}))
            draft_path = td / "draft.json"
            rc = ob.main(["draft", "--profile",
                          str(profile_path),
                          "--pack-id", "field-notes",
                          "--holes", str(holes_path),
                          "--out", str(draft_path)])
            self.assertEqual(rc, 0)
            self.assertTrue(draft_path.exists())


class TestCliDefaultRevision(unittest.TestCase):
    """Drafting for a pack id that already exists in the
    repo revises the published pack by default — the
    measured standard path. The repo root is redirected
    by pointing the module's __file__ at a scratch tree
    holding a packs/ layout, so the real tree is never
    touched."""

    def _publish(self, td: Path) -> None:
        cfg = (td / "packs" / "field-notes" / "profile"
               / "setup_config.json")
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps(
            {"pack_id": "field-notes",
             "schema_front": _good_schema()}))

    def _run_draft(self, td: Path, *extra: str) -> dict:
        profile_path = td / "profile.json"
        profile_path.write_text(json.dumps(_profile()))
        draft_path = td / "draft.json"
        fake = str(td / "pipeline" / "ontology_builder.py")
        with mock.patch.object(ob, "__file__", fake):
            rc = ob.main(["draft", "--profile",
                          str(profile_path),
                          "--pack-id", "field-notes",
                          "--out", str(draft_path), *extra])
        self.assertEqual(rc, 0)
        return json.loads(draft_path.read_text())

    def test_draft_revises_published_pack_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            self._publish(td)
            draft = self._run_draft(td)
            self.assertIn("seed", draft)
            self.assertEqual(draft["entity_types"],
                             ["Person", "Organization"])

    def test_draft_fresh_ignores_published_pack(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            self._publish(td)
            draft = self._run_draft(td, "--fresh")
            self.assertNotIn("seed", draft)

    def test_explicit_seed_wins_over_published_pack(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            self._publish(td)
            front = _good_schema()
            front["entity_inventory"] = \
                front["entity_inventory"][:1]
            front["relation_inventory"] = []
            seed_path = td / "seed.json"
            seed_path.write_text(json.dumps(
                {"schema_front": front}))
            draft = self._run_draft(td, "--seed",
                                    str(seed_path))
            self.assertIn("seed", draft)
            self.assertEqual(draft["entity_types"], ["Person"])


class TestBriefLineage(unittest.TestCase):

    def test_brief_shows_lineage_and_structured_points(self):
        schema = _good_schema()
        schema["entity_inventory"] = \
            schema["entity_inventory"][:1]
        schema["relation_inventory"][0]["endpoint_signature"] = \
            ["Person"]
        draft = _seeded_draft({
            "schema_front": schema,
            "decision_points": [
                {"action": "remove_type", "kind": "entity",
                 "type": "Organization",
                 "reason": "no organisations in the corpus"},
                {"action": "remove_signature", "kind": "relation",
                 "type": "WorksFor", "detail": "Organization",
                 "reason": "follows the type removal"}]})
        brief = ob.render_approval_brief(draft)
        self.assertIn("Lineage: revision inheriting seed pack",
                      brief)
        self.assertIn("[seed change: remove_type]", brief)
        self.assertIn("no organisations in the corpus", brief)


if __name__ == "__main__":
    unittest.main()
