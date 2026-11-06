"""Boundary declarations (pipeline/ontology_builder.py):
structured declare_boundary decision points and the
settlement gate for seeded revisions.

Covered: a declaration record is held to its shape (kind,
two distinct inventory types, a known boundary value, a
reason — a malformed record settles nothing and is a
finding itself); in a seeded revision, a relation that is
added, re-signed (a signature pair gained) or redefined
must have its boundary declared against every relation it
shares a directed signature pair with (or be linked to it
by inverse_of), while untouched relations owe nothing and
fresh drafts are not gated; an entity-kind declaration
(the Product/IntellectualProperty decision-rule form) is
shape-checked and accepted; and an extend_signature
record without evidence justifies nothing.
"""
from __future__ import annotations

import copy
import unittest

from pipeline import ontology_builder as ob


def _profile() -> dict:
    return {
        "family": "fieldnotes",
        "profile_hash": "sha256:0123456789abcdef",
        "inventory": {"n_files": 40, "n_unique_content": 40,
                      "total_chars": 120000},
        "claims": [],
    }


def _ent(name: str, definition: str) -> dict:
    return {"type": name, "role": "entity",
            "definition": definition,
            "form_examples": ["a surface form"],
            "not_entities": {"generic": ["a generic mention"]}}


def _rel(name: str, definition: str, signature: list,
         **kw) -> dict:
    entry = {"type": name, "definition": definition,
             "endpoint_signature": signature,
             "boundary_examples": ["a boundary case"]}
    entry.update(kw)
    return entry


def _schema(relations: list) -> dict:
    return {
        "purpose": "Field-note schema: organisations, their "
                   "affiliates and subsidiaries.",
        "entity_inventory": [
            _ent("Organization",
                 "A named company, agency or institution."),
            _ent("Person", "A named individual person."),
        ],
        "relation_inventory": relations,
    }


def _seed_relations() -> list:
    return [
        _rel("AffiliateOf",
             "The subject is declared an affiliate of the "
             "object company.",
             ["Organization~Organization"]),
        _rel("SubsidiaryOf",
             "The subject is a subsidiary of the object "
             "company, as the text states it.",
             ["Organization~Organization"]),
    ]


def _seed_payload() -> dict:
    return {"pack_id": "field-notes", "pack_version": "0.1.0",
            "schema_front": _schema(_seed_relations())}


def _stub_drafter(raw):
    def drafter(context):
        return copy.deepcopy(raw)
    return drafter


def _draft(raw, seed=None) -> dict:
    return ob.draft_schema(
        _profile(), "field notes", family="fieldnotes",
        pack_id="field-notes", display_name="Field Notes",
        drafter=_stub_drafter(raw), seed=seed)


def _codes(findings) -> set:
    return {f["code"] for f in findings}


def _declare(a: str, b: str, boundary: str = "distinct",
             kind: str = "relation", **kw) -> dict:
    point = {"action": "declare_boundary", "kind": kind,
             "types": [a, b], "boundary": boundary,
             "reason": "the definitions separate them on "
                       "the stated test"}
    point.update(kw)
    return point


class TestDeclarationShape(unittest.TestCase):

    def test_wellformed_declaration_is_accepted(self):
        raw = {"schema_front": _schema(_seed_relations()),
               "decision_points": [
                   _declare("AffiliateOf", "SubsidiaryOf",
                            "includes")]}
        draft = _draft(raw)
        self.assertEqual(ob.validate_schema_draft(draft), [])

    def test_entity_declaration_is_accepted(self):
        schema = _schema(_seed_relations())
        schema["entity_inventory"].append(
            _ent("Product", "A named good or service."))
        schema["entity_inventory"].append(
            _ent("IntellectualProperty",
                 "A named intangible asset."))
        raw = {"schema_front": schema,
               "merge_prohibited_type_pairs":
                   [["Product", "IntellectualProperty"]],
               "decision_points": [
                   _declare("Product", "IntellectualProperty",
                            "distinct", kind="entity",
                            reason="a term licensed as an "
                                   "asset is typed "
                                   "IntellectualProperty; "
                                   "Product when the operative "
                                   "treatment is supply")]}
        draft = _draft(raw)
        self.assertEqual(ob.validate_schema_draft(draft), [])

    def test_malformed_declarations_settle_nothing(self):
        bad_points = [
            _declare("AffiliateOf", "AffiliateOf"),       # same
            _declare("AffiliateOf", "NoSuchRelation"),    # absent
            _declare("AffiliateOf", "SubsidiaryOf", "near"),
            _declare("AffiliateOf", "SubsidiaryOf", reason=""),
            {"action": "declare_boundary", "kind": "relation",
             "types": ["AffiliateOf"], "boundary": "distinct",
             "reason": "one side only"},
            {"action": "declare_boundary",
             "types": ["AffiliateOf", "SubsidiaryOf"],
             "boundary": "distinct", "reason": "no kind"},
        ]
        for point in bad_points:
            raw = {"schema_front": _schema(_seed_relations()),
                   "decision_points": [point]}
            draft = _draft(raw)
            self.assertIn("BAD_BOUNDARY_DECLARATION",
                          _codes(ob.validate_schema_draft(draft)),
                          msg=f"point {point!r} must be a finding")


class TestRevisionGate(unittest.TestCase):

    def test_untouched_revision_owes_no_declarations(self):
        raw = {"schema_front": _schema(_seed_relations()),
               "decision_points": []}
        draft = _draft(raw, seed=_seed_payload())
        self.assertEqual(ob.validate_schema_draft(draft), [])

    def test_fresh_draft_is_not_gated(self):
        raw = {"schema_front": _schema(_seed_relations()),
               "decision_points": []}
        draft = _draft(raw)
        self.assertNotIn("BOUNDARY_UNDECLARED",
                         _codes(ob.validate_schema_draft(draft)))

    def test_new_relation_sharing_a_pair_is_gated(self):
        relations = _seed_relations()
        relations.append(_rel(
            "Acquired",
            "The subject acquired the object company.",
            ["Organization~Organization"]))
        raw = {"schema_front": _schema(relations),
               "decision_points": []}
        draft = _draft(raw, seed=_seed_payload())
        findings = ob.validate_schema_draft(draft)
        self.assertIn("BOUNDARY_UNDECLARED", _codes(findings))
        # One finding per unsettled pair: Acquired against
        # each of the two inherited relations.
        unset = [f for f in findings
                 if f["code"] == "BOUNDARY_UNDECLARED"]
        self.assertEqual(len(unset), 2)
        raw["decision_points"] = [
            _declare("Acquired", "AffiliateOf"),
            _declare("Acquired", "SubsidiaryOf")]
        draft = _draft(raw, seed=_seed_payload())
        self.assertEqual(ob.validate_schema_draft(draft), [])

    def test_redefined_relation_is_gated(self):
        relations = _seed_relations()
        relations[0] = _rel(
            "AffiliateOf",
            "The subject is declared an affiliate of the "
            "object company (the umbrella term).",
            ["Organization~Organization"])
        raw = {"schema_front": _schema(relations),
               "decision_points": [
                   {"action": "revise_definition",
                    "kind": "relation", "type": "AffiliateOf",
                    "reason": "states the umbrella reading "
                              "explicitly"}]}
        draft = _draft(raw, seed=_seed_payload())
        self.assertIn("BOUNDARY_UNDECLARED",
                      _codes(ob.validate_schema_draft(draft)))
        raw["decision_points"].append(
            _declare("AffiliateOf", "SubsidiaryOf", "includes"))
        draft = _draft(raw, seed=_seed_payload())
        self.assertEqual(ob.validate_schema_draft(draft), [])

    def test_resigned_relation_is_gated(self):
        relations = _seed_relations()
        relations.append(_rel(
            "Employs", "The organisation employs the person.",
            ["Organization~Person"]))
        seed_relations = _seed_relations()
        seed_relations.append(_rel(
            "Employs", "The organisation employs the person.",
            ["Organization~Person"]))
        seed = {"pack_id": "field-notes", "pack_version": "0.1.0",
                "schema_front": _schema(seed_relations)}
        relations[2] = _rel(
            "Employs", "The organisation employs the person.",
            ["Organization~Person", "Person~Organization"])
        relations.append(
            _rel("Mentors",
                 "The person mentors the named person.",
                 ["Person~Organization"]))
        raw = {"schema_front": _schema(relations),
               "decision_points": [
                   {"action": "extend_signature",
                    "kind": "relation", "type": "Employs",
                    "evidence": "the corpus also states "
                                "employment person-first "
                                "('Ada, of Acme, ...')",
                    "reason": "the definition already covers "
                              "both argument orders"}]}
        draft = _draft(raw, seed=seed)
        # Employs gained a pair and now shares
        # Person~Organization with the new Mentors; nothing
        # declares that pair yet.
        self.assertIn("BOUNDARY_UNDECLARED",
                      _codes(ob.validate_schema_draft(draft)))
        raw["decision_points"].append(
            _declare("Employs", "Mentors"))
        draft = _draft(raw, seed=seed)
        self.assertEqual(ob.validate_schema_draft(draft), [])

    def test_inverse_link_settles_the_pair(self):
        relations = _seed_relations()
        relations.append(_rel(
            "ParentOf",
            "The subject is the parent of the object "
            "company.", ["Organization~Organization"],
            inverse_of="SubsidiaryOf"))
        raw = {"schema_front": _schema(relations),
               "decision_points": [
                   _declare("ParentOf", "AffiliateOf")]}
        draft = _draft(raw, seed=_seed_payload())
        # ParentOf~AffiliateOf is declared; ParentOf~
        # SubsidiaryOf is settled by the inverse link.
        self.assertEqual(ob.validate_schema_draft(draft), [])


if __name__ == "__main__":
    unittest.main()
