"""Ontology builder (pipeline/ontology_builder.py): the
draft -> validate -> approve -> emit loop.

Covered: deterministic + tier-drafted schema production;
fail-closed validation with named findings (missing definitions
and boundary material, duplicate/colliding names, unknown
endpoints, overlap without a declared boundary, definition
forks, payload-contract violations); approval bound to the
schema hash and refused for invalid drafts; emission refused
without / with a stale / with a non-approving approval record;
the emitted pack loading through the staged pack loader; and
the BioRED round-trip equivalence — the lab's certified
schema_front carried through the builder renders byte-identical
guidance to the shipped pack's payload.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import ontology_builder as ob
from pipeline import profile_config

ROOT = Path(__file__).resolve().parent.parent


def _profile() -> dict:
    return {
        "family": "fieldnotes",
        "profile_hash": "sha256:0123456789abcdef",
        "inventory": {"n_files": 40, "n_unique_content": 40,
                      "total_chars": 120000},
        "claims": [
            {"section": "inventory", "kind": "fact",
             "grade": "FULL",
             "statement": "Corpus holds 40 document files."},
            {"section": "labels", "kind": "fact", "grade": "FULL",
             "statement": "No gold annotations."},
        ],
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


def _stub_drafter(raw):
    def drafter(context):
        return copy.deepcopy(raw)
    return drafter


def _good_draft(**overrides) -> dict:
    draft = ob.draft_schema(
        _profile(), "field notes about researchers",
        family="fieldnotes", pack_id="field-notes",
        display_name="Field Notes",
        drafter=_stub_drafter({"schema_front": _good_schema()}))
    draft.update(overrides)
    return draft


def _codes(findings) -> set[str]:
    return {f["code"] for f in findings}


class TestDrafting(unittest.TestCase):

    def test_deterministic_seed_names_gaps(self):
        draft = ob.draft_schema(
            _profile(),
            {"text": "researcher field notes",
             "entity_types": ["Person"],
             "relation_types": ["WorksFor"]},
            family="fieldnotes", pack_id="field-notes",
            display_name="Field Notes")
        self.assertEqual(draft["kind"], ob.DRAFT_KIND)
        self.assertEqual(draft["status"], "DRAFT_NOT_APPROVED")
        self.assertEqual(draft["entity_types"], ["Person"])
        self.assertEqual(draft["relation_types"], ["WorksFor"])
        self.assertEqual(
            draft["provenance"]["drafting_source"],
            "deterministic")
        self.assertEqual(draft["provenance"]["drafting_tier"],
                         "drafting")
        codes = _codes(ob.validate_schema_draft(draft))
        self.assertIn("MISSING_DEFINITION", codes)
        self.assertIn("MISSING_BOUNDARY_EXAMPLES", codes)
        self.assertIn("BAD_ENDPOINT_SIGNATURE", codes)

    def test_brief_object_entries_refuse_loudly(self):
        # : a brief whose type
        # fields carry objects instead of name strings must
        # be refused by name — not str()-coerced into a
        # repr-shaped type name the validator then reports
        # nonsense findings against.
        with self.assertRaises(ValueError) as cm:
            ob.draft_schema(
                _profile(),
                {"entity_types": [{"name": "Person",
                                   "definition": "a human"}]},
                family="fieldnotes", pack_id="field-notes",
                display_name="Field Notes")
        self.assertIn("entity_types", str(cm.exception))
        self.assertIn("type-name", str(cm.exception))

    def test_tier_drafter_receives_context_and_names_no_model(self):
        seen = {}

        def drafter(context):
            seen.update(context)
            return {"schema_front": _good_schema()}

        draft = ob.draft_schema(
            _profile(), "brief text", family="fieldnotes",
            pack_id="field-notes", display_name="Field Notes",
            drafter=drafter)
        self.assertEqual(seen["family"], "fieldnotes")
        self.assertEqual(seen["brief"], "brief text")
        self.assertEqual(len(seen["claims"]), 2)
        self.assertEqual(draft["provenance"]["drafting_source"],
                         "llm")
        # R14: the draft document is domain data — it declares
        # the tier, never a model binding.
        blob = json.dumps(draft)
        self.assertNotIn("gpt", blob)
        self.assertNotIn("claude", blob)
        self.assertEqual(ob.validate_schema_draft(draft), [])

    def test_chat_drafter_parses_tier_output(self):
        import pipeline.setup_service as ss
        original = ss._chat
        try:
            ss._chat = lambda *a, **k: (
                json.dumps({"schema_front": _good_schema()}), {})
            drafter = ob.make_chat_drafter("some/model", "key")
            raw = drafter({"family": "fieldnotes"})
        finally:
            ss._chat = original
        self.assertIn("entity_inventory", raw["schema_front"])


class TestValidation(unittest.TestCase):

    def test_missing_definition_and_boundary(self):
        draft = _good_draft()
        draft["schema_front"]["entity_inventory"][0][
            "definition"] = ""
        draft["schema_front"]["entity_inventory"][0][
            "form_examples"] = []
        draft["schema_front"]["entity_inventory"][0][
            "not_entities"] = {}
        codes = _codes(ob.validate_schema_draft(draft))
        self.assertIn("MISSING_DEFINITION", codes)
        self.assertIn("MISSING_BOUNDARY_EXAMPLES", codes)

    def test_duplicate_and_colliding_names(self):
        draft = _good_draft()
        inv = draft["schema_front"]["entity_inventory"]
        inv.append(dict(inv[0]))
        codes = _codes(ob.validate_schema_draft(draft))
        self.assertIn("DUPLICATE_TYPE", codes)
        draft = _good_draft()
        draft["schema_front"]["entity_inventory"].append(
            {"type": "person", "role": "entity",
             "definition": "A human being.",
             "form_examples": ["a name"], "not_entities": {}})
        codes = _codes(ob.validate_schema_draft(draft))
        self.assertIn("TYPE_NAME_COLLISION", codes)

    def test_unknown_endpoint_and_inverse(self):
        draft = _good_draft()
        rel = draft["schema_front"]["relation_inventory"][0]
        rel["endpoint_signature"] = ["Person", "Robot"]
        rel["inverse_of"] = "Employs"
        codes = _codes(ob.validate_schema_draft(draft))
        self.assertIn("UNKNOWN_ENDPOINT_TYPE", codes)
        self.assertIn("UNKNOWN_INVERSE", codes)

    def test_overlap_identical_definitions(self):
        draft = _good_draft()
        draft["schema_front"]["entity_inventory"][1][
            "definition"] = draft["schema_front"][
                "entity_inventory"][0]["definition"]
        codes = _codes(ob.validate_schema_draft(draft))
        self.assertIn("OVERLAP_UNDECLARED", codes)

    def test_definition_fork_and_inventory_mismatch(self):
        draft = _good_draft()
        draft["entity_types"] = [
            {"type": "Person",
             "definition": "A completely different definition."},
            "Organization"]
        codes = _codes(ob.validate_schema_draft(draft))
        self.assertIn("DEFINITION_FORK", codes)
        draft = _good_draft()
        draft["definitions"] = {"Person": "A named individual."}
        codes = _codes(ob.validate_schema_draft(draft))
        self.assertIn("DEFINITION_FORK", codes)
        draft = _good_draft()
        draft["relation_types"] = ["WorksFor", "Extra"]
        codes = _codes(ob.validate_schema_draft(draft))
        self.assertIn("INVENTORY_MISMATCH", codes)

    def test_bad_slug_and_merge_pairs(self):
        draft = _good_draft(pack_id="Field Notes!")
        self.assertIn("PACK_ID_SLUG",
                      _codes(ob.validate_schema_draft(draft)))
        draft = _good_draft()
        draft["merge_prohibited_type_pairs"] = [
            ["Person", "Robot"], ["Person", "Person"]]
        codes = _codes(ob.validate_schema_draft(draft))
        self.assertIn("UNKNOWN_MERGE_PAIR_TYPE", codes)
        self.assertIn("BAD_MERGE_PAIR", codes)

    def test_payload_contract_violation_is_named(self):
        draft = _good_draft()
        draft["schema_front"]["relation_inventory"][0][
            "direction"] = "sideways"
        codes = _codes(ob.validate_schema_draft(draft))
        self.assertIn("PAYLOAD_INVALID", codes)

    def test_empty_inventories(self):
        draft = _good_draft()
        draft["schema_front"]["entity_inventory"] = []
        draft["schema_front"]["relation_inventory"] = []
        draft["entity_types"] = []
        draft["relation_types"] = []
        codes = _codes(ob.validate_schema_draft(draft))
        self.assertIn("EMPTY_ENTITY_INVENTORY", codes)
        self.assertIn("EMPTY_RELATION_INVENTORY", codes)


class TestApproval(unittest.TestCase):

    def test_approval_record_binds_schema_hash(self):
        draft = _good_draft()
        record = ob.approve_schema_draft(
            draft, approver="Dongbum", issue=73)
        self.assertEqual(record["pack_id"], "field-notes")
        self.assertEqual(record["profile"], "fieldnotes")
        self.assertEqual(record["schema_hash"],
                         ob.schema_hash(draft))
        self.assertIn("approved", record["outcome"])

    def test_approval_refused_for_invalid_draft(self):
        draft = _good_draft()
        draft["schema_front"]["entity_inventory"][0][
            "definition"] = ""
        with self.assertRaises(ValueError):
            ob.approve_schema_draft(draft, approver="Dongbum")

    def test_approval_refused_for_non_approving_outcome(self):
        draft = _good_draft()
        with self.assertRaises(ValueError):
            ob.approve_schema_draft(
                draft, approver="Dongbum",
                outcome="rejected — needs rework")


class TestEmission(unittest.TestCase):

    def test_unapproved_emission_refused(self):
        draft = _good_draft()
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                ob.emit_pack(draft, None, td)

    def test_stale_approval_refused(self):
        draft = _good_draft()
        record = ob.approve_schema_draft(draft, approver="D")
        draft["schema_front"]["entity_inventory"][0][
            "definition"] = "Edited after approval."
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                ob.emit_pack(draft, record, td)

    def test_wrong_pack_and_non_approving_records_refused(self):
        draft = _good_draft()
        record = ob.approve_schema_draft(draft, approver="D")
        with tempfile.TemporaryDirectory() as td:
            bad = dict(record, pack_id="other-pack")
            with self.assertRaises(ValueError):
                ob.emit_pack(draft, bad, td)
            bad = dict(record, outcome="noted, no decision")
            with self.assertRaises(ValueError):
                ob.emit_pack(draft, bad, td)

    def test_emit_loads_through_staged_loader(self):
        draft = _good_draft()
        record = ob.approve_schema_draft(draft, approver="D")
        with tempfile.TemporaryDirectory() as td:
            manifest = ob.emit_pack(draft, record, td)
            pack_dir = Path(td) / "packs" / "field-notes"
            self.assertTrue((pack_dir / "pack.json").exists())
            self.assertTrue((pack_dir / "conformance.json")
                            .exists())
            payload_path = pack_dir / "profile" / \
                "setup_config.json"
            pin = hashlib.sha256(
                payload_path.read_bytes()).hexdigest()
            self.assertEqual(manifest["payload"]["sha256"], pin)
            cfg = profile_config.pack_setup_config(pack_dir)
            self.assertEqual(cfg["entity_types"],
                             ["Person", "Organization"])
            self.assertIn("WorksFor",
                          profile_config.derive_guidance(cfg))
            conformance = json.loads(
                (pack_dir / "conformance.json").read_text())
            self.assertEqual(conformance["entries"][0]["status"],
                             "emitted_unmeasured")
            # A second emit over the same tree refuses: a
            # changed pack is a new version, not an overwrite.
            with self.assertRaises(ValueError):
                ob.emit_pack(draft, record, td)


class TestBioRedRoundTrip(unittest.TestCase):
    """Equivalence: the lab's certified BioRED schema_front,
    carried through draft -> validate -> approve -> emit,
    renders byte-identical guidance to the shipped pack's
    payload (the builder neither gains nor loses schema
    content against the lab flow's own artifact)."""

    def test_biored_schema_round_trip(self):
        bio_cfg = profile_config.pack_setup_config(
            ROOT / "packs" / "bio-medical")
        raw = {
            "schema_front": copy.deepcopy(bio_cfg["schema_front"]),
            "merge_prohibited_type_pairs": copy.deepcopy(
                bio_cfg["merge_prohibited_type_pairs"]),
        }
        draft = ob.draft_schema(
            {"family": "biored",
             "profile_hash": "sha256:roundtrip"},
            None, family="biored", pack_id="bio-medical-rt",
            display_name="Bio Medical (round-trip)",
            drafter=_stub_drafter(raw))
        self.assertEqual(ob.validate_schema_draft(draft), [])
        record = ob.approve_schema_draft(draft, approver="D")
        with tempfile.TemporaryDirectory() as td:
            ob.emit_pack(draft, record, td)
            emitted = profile_config.pack_setup_config(
                Path(td) / "packs" / "bio-medical-rt")
        self.assertEqual(emitted["schema_front"],
                         bio_cfg["schema_front"])
        self.assertEqual(
            profile_config.derive_guidance(emitted),
            profile_config.derive_guidance(bio_cfg))
        self.assertEqual(set(emitted["entity_types"]),
                         set(bio_cfg["entity_types"]))
        self.assertEqual(set(emitted["relation_types"]),
                         set(bio_cfg["relation_types"]))


class TestCli(unittest.TestCase):

    def test_llm_draft_without_bound_model_refuses(self):
        # The drafting seat ships unbound. `draft
        # --llm` with no --model and no ONTOSMITH_DRAFTING_
        # MODEL refuses with the named error (exit 2)
        # before any request — and writes no draft.
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            profile_path = td / "profile.json"
            profile_path.write_text(json.dumps(_profile()))
            draft_path = td / "draft.json"
            with mock.patch.dict(os.environ, {}):
                for name in list(os.environ):
                    if name.startswith("ONTOSMITH_") \
                            or name == "OPENROUTER_API_KEY":
                        del os.environ[name]
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    rc = ob.main(["draft", "--profile",
                                  str(profile_path),
                                  "--pack-id", "field-notes",
                                  "--out", str(draft_path),
                                  "--llm"])
            self.assertEqual(rc, 2)
            self.assertIn("drafting", err.getvalue())
            self.assertIn("ONTOSMITH_DRAFTING_MODEL",
                          err.getvalue())
            self.assertFalse(draft_path.exists())

    def test_cli_loop_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            profile_path = td / "profile.json"
            profile_path.write_text(json.dumps(_profile()))
            draft_path = td / "draft.json"
            rc = ob.main(["draft", "--profile", str(profile_path),
                          "--pack-id", "field-notes",
                          "--out", str(draft_path)])
            self.assertEqual(rc, 0)
            # The deterministic skeleton fails validation, so
            # approve/emit refuse through the CLI too.
            rc = ob.main(["validate", str(draft_path)])
            self.assertEqual(rc, 1)
            rc = ob.main(["approve", str(draft_path),
                          "--approver", "Dongbum"])
            self.assertEqual(rc, 1)
            # Fill the skeleton in by hand (the human/drafter
            # seat), re-validate, approve, emit.
            draft = json.loads(draft_path.read_text())
            draft["schema_front"] = _good_schema()
            draft["entity_types"] = ["Person", "Organization"]
            draft["relation_types"] = ["WorksFor"]
            draft_path.write_text(json.dumps(draft))
            self.assertEqual(ob.main(["validate",
                                      str(draft_path)]), 0)
            self.assertEqual(ob.main(["brief",
                                      str(draft_path)]), 0)
            approval_path = td / "approval.json"
            rc = ob.main(["approve", str(draft_path),
                          "--approver", "Dongbum",
                          "--out", str(approval_path)])
            self.assertEqual(rc, 0)
            rc = ob.main(["emit", str(draft_path),
                          "--approval", str(approval_path),
                          "--out-root", str(td)])
            self.assertEqual(rc, 0)
            self.assertTrue((td / "packs" / "field-notes"
                             / "pack.json").exists())
            # Unapproved emit through the CLI refuses.
            rc = ob.main(["emit", str(draft_path),
                          "--approval", str(draft_path),
                          "--out-root", str(td / "other")])
            self.assertEqual(rc, 1)

    def test_cli_missing_file_is_a_clean_refusal(self):
        rc = ob.main(["validate", "/nonexistent/draft.json"])
        self.assertEqual(rc, 1)


class LoadJsonErrorWordingTests(unittest.TestCase):
    """Issue #49: a missing input file reports as not found; only
    an unparsable file is "not valid JSON"."""

    def test_missing_file_reports_not_found(self):
        with tempfile.TemporaryDirectory() as td:
            missing = os.path.join(td, "nope.json")
            with self.assertRaises(ValueError) as cm:
                ob._load_json(missing)
            self.assertIn("not found", str(cm.exception))
            self.assertNotIn("not valid JSON", str(cm.exception))

    def test_malformed_file_reports_not_valid_json(self):
        with tempfile.TemporaryDirectory() as td:
            bad = os.path.join(td, "bad.json")
            with open(bad, "w") as f:
                f.write("{not json")
            with self.assertRaises(ValueError) as cm:
                ob._load_json(bad)
            self.assertIn("is not valid JSON", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
