"""Tests for pipeline.ontology_import — the BYO-schema door:
an external ontology inventory + a scoping spec -> a builder
draft + the scoping record, fail-closed like the builder.

The fixture is a miniature contracts ontology in the FIBO
shape: a scoped module file (classes, object properties, one
datatype property, a party-role class, a droppable class) and
a reference file drawn on for exactly one concept.
"""
import os
import tempfile
import unittest

from pipeline import ontology_builder as ob
from pipeline import ontology_import as oi

B = "https://example.org/onto/FND/Agreements/Contracts"
P = "https://example.org/onto/FND/AgentsAndPeople/People"
CONTRACTS = "FND/Agreements/Contracts.rdf"
PEOPLE = "FND/AgentsAndPeople/People.rdf"


def _entry(iri, kind, definition=None, parents=(), domain=None,
           range_=None, source=CONTRACTS):
    return {"iri": iri, "kind": kind, "label": None,
            "definition": definition, "parents": list(parents),
            "domain": domain, "range": range_,
            "source_file": source}


def _inventory():
    return [
        _entry(f"{B}/Contract", "class",
               "a binding agreement between parties",
               parents=[f"{B}/Agreement"]),
        _entry(f"{B}/Agreement", "class",
               "an understanding between parties"),
        _entry(f"{B}/WrittenContract", "class",
               "a contract in written form",
               parents=[f"{B}/Contract"]),
        _entry(f"{B}/ContractualCommitment", "class",
               "a provision the parties agree to perform"),
        _entry(f"{B}/TerminationProvision", "class",
               "a provision for ending the contract",
               parents=[f"{B}/ContractualCommitment"]),
        _entry(f"{B}/EarlyTerminationProvision", "class",
               "a provision for ending the contract early",
               parents=[f"{B}/TerminationProvision"]),
        _entry(f"{B}/ContractParty", "class",
               "a party to a contract"),
        _entry(f"{B}/TermSheet", "class",
               "a summary of proposed terms",
               parents=[f"{B}/Agreement"]),
        _entry(f"{B}/hasContractParty", "object_property",
               "indicates a party to the contract",
               domain=f"{B}/Contract", range_=f"{B}/ContractParty"),
        _entry(f"{B}/hasContractualElement", "object_property",
               "indicates a component of an agreement",
               domain=f"{B}/Agreement",
               range_=f"{B}/ContractualCommitment"),
        _entry(f"{B}/hasTerminationProvision", "object_property",
               "indicates a termination provision of a contract",
               parents=[f"{B}/hasContractualElement"],
               domain=f"{B}/Contract",
               range_=f"{B}/TerminationProvision"),
        _entry(f"{B}/isAssignable", "datatype_property",
               domain=f"{B}/Contract", range_="boolean"),
    ]


def _reference_inventory():
    return [
        _entry(f"{P}/Person", "class", "individual human being",
               parents=["https://example.org/commons/Party"],
               source=PEOPLE),
        _entry(f"{P}/PersonName", "class", "a person's name",
               source=PEOPLE),
    ]


def _spec():
    return {
        "kind": oi.SPEC_KIND,
        "source": {"name": "Mini FIBO", "license": "MIT",
                   "scoped_files": [CONTRACTS]},
        "family": "mini-cuad",
        "pack_id": "mini-contracts",
        "display_name": "Mini Contracts (imported)",
        "purpose": "Miniature imported contract schema.",
        "brief": "Import of the mini contracts ontology.",
        "entities": [
            {"type": "Contract", "action": "merged",
             "sources": [f"{B}/Contract", f"{B}/Agreement",
                         f"{B}/WrittenContract"],
             "definition_from": f"{B}/Contract",
             "note": "kind taxonomy collapses",
             "form_examples": ["a signed services agreement"],
             "not_entities": {"clauses": ["the termination "
                                         "clause"]}},
            {"type": "ContractualCommitment", "action": "kept",
             "sources": [f"{B}/ContractualCommitment"],
             "definition_from": f"{B}/ContractualCommitment",
             "form_examples": ["a covenant"],
             "not_entities": {"facts": ["a statement of fact"]}},
            {"type": "TerminationProvision", "action": "flattened",
             "sources": [f"{B}/TerminationProvision",
                         f"{B}/EarlyTerminationProvision"],
             "definition_from": f"{B}/TerminationProvision",
             "form_examples": ["a termination clause"],
             "not_entities": {"dates": ["the termination date"]}},
            {"type": "Person", "action": "kept",
             "sources": [f"{P}/Person"],
             "definition_from": f"{P}/Person",
             "form_examples": ["an individual's name"],
             "not_entities": {"titles": ["the General Counsel"]}},
        ],
        "external_entities": [
            {"type": "Organization",
             "external_source":
                 "https://www.omg.org/spec/Commons/"
                 "Organizations/Organization",
             "definition": "A named company or agency that can "
                           "be a party to a contract.",
             "note": "referenced by the source, defined in "
                     "Commons",
             "form_examples": ["a company name"],
             "not_entities": {"generic": ["the company"]}},
        ],
        "relations": [
            {"type": "HasContractParty", "action": "kept",
             "sources": [f"{B}/hasContractParty"],
             "definition_from": f"{B}/hasContractParty",
             "endpoint_signature": ["Contract~Organization",
                                    "Contract~Person"],
             "boundary_examples": [
                 "a signatory block states the relation; a "
                 "mentioned customer does not"]},
            {"type": "HasContractualElement", "action": "merged",
             "sources": [f"{B}/hasContractualElement",
                         f"{B}/hasTerminationProvision"],
             "definition_from": f"{B}/hasContractualElement",
             "endpoint_signature": [
                 "Contract~ContractualCommitment",
                 "Contract~TerminationProvision"],
             "boundary_examples": [
                 "a provision of this contract; a provision "
                 "quoted from another contract is not"]},
        ],
        "role_conversions": [
            {"iri": f"{B}/ContractParty",
             "into_relation": "HasContractParty",
             "note": "the role becomes the linking relation"},
        ],
        "demoted": [
            {"iri": f"{B}/isAssignable", "into": "Contract",
             "note": "no datatype channel in a flat contract"},
        ],
        "dropped": [
            {"iri": f"{B}/TermSheet",
             "reason": "pre-contractual summary, not a "
                       "contract"},
        ],
        "merge_prohibited_type_pairs": [
            ["TerminationProvision", "ContractualCommitment"]],
        "decision_points": ["kind taxonomy merge"],
    }


class TestBuildImport(unittest.TestCase):

    def test_draft_validates_clean(self):
        draft, _ = oi.build_import(_spec(), [_inventory(),
                                             _reference_inventory()])
        self.assertEqual(draft["kind"], ob.DRAFT_KIND)
        self.assertEqual(draft["status"], "DRAFT_NOT_APPROVED")
        self.assertEqual(ob.validate_schema_draft(draft), [])
        self.assertEqual(
            draft["provenance"]["drafting_source"],
            "ontology-import (Mini FIBO)")
        contract = draft["schema_front"]["entity_inventory"][0]
        self.assertEqual(contract["type"], "Contract")
        self.assertEqual(contract["definition"],
                         "a binding agreement between parties")

    def test_scoping_record_is_a_complete_account(self):
        _, record = oi.build_import(
            _spec(), [_inventory(), _reference_inventory()])
        counts = record["counts"]
        self.assertEqual(counts["kept"], 3)
        self.assertEqual(counts["merged"], 5)
        self.assertEqual(counts["flattened"], 2)
        self.assertEqual(counts["role_converted"], 1)
        self.assertEqual(counts["demoted"], 1)
        self.assertEqual(counts["dropped"], 1)
        self.assertEqual(counts["scoped_concepts"], 13)
        self.assertEqual(counts["external_entities"], 1)
        self.assertEqual(counts["entity_types"], 5)
        self.assertEqual(counts["relation_types"], 2)
        by_iri = {c["iri"]: c for c in record["concepts"]}
        # every scoped-file concept is in the account
        scoped = {e["iri"] for e in _inventory()}
        self.assertTrue(scoped <= set(by_iri))
        sheet = by_iri[f"{B}/TermSheet"]
        self.assertEqual(sheet["action"], "dropped")
        self.assertIn("pre-contractual", sheet["reason"])
        contract = by_iri[f"{B}/Contract"]
        self.assertEqual(contract["action"], "merged")
        self.assertEqual(contract["target"], "Contract")
        self.assertEqual(contract["ancestors_in_source"],
                         [f"{B}/Agreement"])
        # the reference file's undrawn concept is not in the
        # account at all — the boundary is the scoped files
        self.assertNotIn(f"{P}/PersonName", by_iri)
        org = next(t for t in record["types"]
                   if t["type"] == "Organization")
        self.assertEqual(org["definition_provenance"], "authored")

    def test_unaccounted_scoped_concept_refuses(self):
        spec = _spec()
        spec["dropped"] = []  # TermSheet now unaccounted
        with self.assertRaises(ValueError) as cm:
            oi.build_import(spec, [_inventory(),
                                   _reference_inventory()])
        self.assertIn("TermSheet", str(cm.exception))

    def test_double_accounting_refuses(self):
        spec = _spec()
        spec["dropped"].append(
            {"iri": f"{B}/isAssignable", "reason": "also dropped"})
        with self.assertRaises(ValueError) as cm:
            oi.build_import(spec, [_inventory(),
                                   _reference_inventory()])
        self.assertIn("twice", str(cm.exception))

    def test_drop_without_reason_refuses(self):
        spec = _spec()
        spec["dropped"] = [{"iri": f"{B}/TermSheet"}]
        with self.assertRaises(ValueError):
            oi.build_import(spec, [_inventory(),
                                   _reference_inventory()])

    def test_definition_from_must_be_a_source(self):
        spec = _spec()
        spec["entities"][0]["definition_from"] = f"{B}/TermSheet"
        with self.assertRaises(ValueError):
            oi.build_import(spec, [_inventory(),
                                   _reference_inventory()])

    def test_kept_means_one_source(self):
        spec = _spec()
        spec["entities"][1]["sources"].append(f"{B}/TermSheet")
        spec["dropped"] = []
        with self.assertRaises(ValueError):
            oi.build_import(spec, [_inventory(),
                                   _reference_inventory()])

    def test_datatype_property_cannot_be_a_relation(self):
        spec = _spec()
        spec["relations"][0]["sources"] = [f"{B}/isAssignable"]
        spec["demoted"] = []
        with self.assertRaises(ValueError):
            oi.build_import(spec, [_inventory(),
                                   _reference_inventory()])

    def test_unknown_concept_refuses(self):
        spec = _spec()
        spec["dropped"] = [{"iri": f"{B}/NoSuchConcept",
                            "reason": "ghost"}]
        with self.assertRaises(ValueError):
            oi.build_import(spec, [_inventory(),
                                   _reference_inventory()])

    def test_restated_declaration_accounted_once(self):
        # FIBO restates concepts across files (Contracts.rdf
        # re-declares People classes to attach restrictions):
        # the first input's declaration is the one carried,
        # the other site is named on the record — never a
        # silent field merge.
        scoped = _entry(f"{P}/PersonName", "class", None,
                        source=CONTRACTS)
        spec = _spec()
        spec["dropped"].append(
            {"iri": f"{P}/PersonName",
             "reason": "restated augmentation, no definition "
                       "in the scoped file"})
        _, record = oi.build_import(
            spec, [[*_inventory(), scoped],
                   _reference_inventory()])
        entry = next(c for c in record["concepts"]
                     if c["iri"] == f"{P}/PersonName")
        self.assertEqual(entry["action"], "dropped")
        self.assertEqual(entry["source_file"], CONTRACTS)
        self.assertEqual(entry["also_declared_in"], [PEOPLE])

    def test_imported_draft_approves_and_emits(self):
        import tempfile
        from pathlib import Path

        from pipeline import pack_schema
        draft, _ = oi.build_import(
            _spec(), [_inventory(), _reference_inventory()])
        approval = ob.approve_schema_draft(
            draft, approver="test record")
        with tempfile.TemporaryDirectory() as td:
            manifest = ob.emit_pack(draft, approval, Path(td))
            self.assertEqual(manifest["pack_id"], "mini-contracts")
            defects = pack_schema.validate_pack_dir(
                Path(td) / "packs" / "mini-contracts")
            self.assertEqual(defects, [])


class LoadJsonFileErrorWordingTests(unittest.TestCase):
    """Issue #49: a missing input file reports as not found; only
    an unparsable file is "not valid JSON"."""

    def test_missing_file_reports_not_found(self):
        with tempfile.TemporaryDirectory() as td:
            missing = os.path.join(td, "nope.json")
            with self.assertRaises(ValueError) as cm:
                oi.load_json_file(missing)
            self.assertIn("not found", str(cm.exception))
            self.assertNotIn("not valid JSON", str(cm.exception))

    def test_malformed_file_reports_not_valid_json(self):
        with tempfile.TemporaryDirectory() as td:
            bad = os.path.join(td, "bad.json")
            with open(bad, "w") as f:
                f.write("{not json")
            with self.assertRaises(ValueError) as cm:
                oi.load_json_file(bad)
            self.assertIn("is not valid JSON", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
