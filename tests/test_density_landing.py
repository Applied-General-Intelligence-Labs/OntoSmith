"""0.1.4 density-program landing tests.

Items 2/5/6 (relation declarations): the open-domain pack
ships its (name, orientation) relation coverage as a pinned
relation_declarations artefact; pipeline/relation_map.py loads
it, and WRITE applies its canonicalizations — 'parent of' is
stored as 'child of' with endpoints swapped — while its
declared vocabulary (formed from / named after / teacher of /
commander of) is stored verbatim as first-class relations.

Item 4 (alias retention): spaced-hyphen surface variants
('Charles - Édouard' vs 'Charles-Édouard') share one registry
index key, resolve as form variants of one name, and BOTH
attested forms stay on the entity (name + alias); a merge
unions the incoming entity's aliases instead of dropping
them, and a declared attested surface (the demonym layer's
*_attested fields) is retained as an alias at WRITE.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from pipeline.batch import Batch, BatchConfig, batch_dir, save_batch
from pipeline.file_registry import SqliteEntityRegistry
from pipeline.profile_config import (
    pack_relation_declarations,
    setup_config_errors,
)
from pipeline.registry import Entity, index_normalize, is_form_variant
from pipeline.relation_map import (
    RelationNormalizer,
    acceptance_mapping,
    load_relation_declarations,
    write_map,
)

ROOT = Path(__file__).resolve().parent.parent
PACK_DIR = ROOT / "packs" / "open-domain"
ARTEFACT = PACK_DIR / "relation_declarations.json"

# The density program's Test A additions (orientation test,
# 2026-11-01): (canonical, surface, flipped).
TEST_A_PAIRS = [
    ("participant", "participant in", False),
    ("conflict", "participant in", False),
    ("performer", "performed by", False),
    ("creator", "written by", False),
    ("author", "written by", False),
    ("publisher", "published by", True),
    ("mother", "parent of", True),
    ("mother", "child of", False),
    ("father", "parent of", True),
    ("father", "child of", False),
    ("parent organization", "part of", False),
    ("subsidiary", "part of", True),
    ("series", "part of", False),
    ("spouse", "married to", True),
]


class TestShippedDeclarations(unittest.TestCase):
    """Item 2: the shipped artefact, loaded through product code."""

    @classmethod
    def setUpClass(cls):
        cls.decl = pack_relation_declarations(PACK_DIR)
        cls.mapping = acceptance_mapping(cls.decl)

    def test_pack_accessor_loads_pin_checked_artefact(self):
        self.assertIsNotNone(self.decl)
        self.assertEqual(self.decl["table_id"],
                         "open-domain-relation-declarations")

    def test_test_a_pairs_all_present(self):
        for canon, surface, flipped in TEST_A_PAIRS:
            self.assertIn((surface, flipped), self.mapping[canon],
                          f"{canon} += ({surface}, {flipped})")

    def test_base_pairs_preserved(self):
        # S4-declared base entries survive transcription.
        self.assertIn(("participant in", True),
                      self.mapping["participant"])
        self.assertIn(("written by", True), self.mapping["creator"])
        self.assertIn(("part of", False), self.mapping["country"])
        self.assertIn(("sibling of", False), self.mapping["sibling"])

    def test_counts(self):
        additions = [d for d in self.decl["declarations"]
                     if d.get("origin") == "density_test_a"]
        self.assertEqual(len(additions), 14)
        self.assertEqual(len(self.decl["declarations"]), 69)

    def test_declared_but_unstatable_relations_carry_empty(self):
        self.assertEqual(self.mapping["educated at"], [])

    def test_loader_rejects_malformed_artefact(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        bad = tmp / "bad.json"
        bad.write_text(json.dumps({"kind": "relation_declarations",
                                   "declarations": [{"canonical": "x"}]}))
        with self.assertRaises(ValueError):
            load_relation_declarations(bad)
        with self.assertRaises(ValueError):
            load_relation_declarations(tmp / "missing.json")


class TestWriteMap(unittest.TestCase):
    """Items 5/6: the declaration's WRITE view."""

    @classmethod
    def setUpClass(cls):
        cls.wmap = write_map(pack_relation_declarations(PACK_DIR))

    def test_child_of_is_canonical_for_the_dual(self):
        self.assertEqual(self.wmap["parent of"],
                         {"canonical": "child of", "flipped": True})

    def test_vocabulary_surfaces_are_their_own_canonical(self):
        for surface in ("formed from", "named after",
                        "teacher of", "commander of"):
            self.assertEqual(self.wmap[surface],
                             {"canonical": surface, "flipped": False})

    def test_conflicting_write_targets_raise(self):
        decl = {"vocabulary": ["x"],
                "write_canonicalizations": [
                    {"surface": "x", "canonical": "y",
                     "flipped": False}]}
        with self.assertRaises(ValueError):
            write_map(decl)


class TestNormalizerOrientation(unittest.TestCase):
    """Item 5: flipped declarations rename AND swap at WRITE."""

    def setUp(self):
        self.wmap = write_map(pack_relation_declarations(PACK_DIR))
        self.norm = RelationNormalizer(profile_map=self.wmap)

    def test_flipped_triple_swaps_endpoints(self):
        out = self.norm.normalize_triple(
            "Marie Curie", "parent of", "Irène Joliot-Curie")
        self.assertEqual(out, ("Irène Joliot-Curie", "child of",
                               "Marie Curie", "map"))

    def test_vocabulary_surface_stored_verbatim_as_declared(self):
        stored, layer = self.norm.normalize("formed from")
        self.assertEqual((stored, layer), ("formed from", "map"))
        self.assertNotIn("formed from",
                         self.norm.report()["unmapped_surfaces"])

    def test_string_entries_behave_as_before(self):
        norm = RelationNormalizer(
            profile_map={"is located in": "located_in"})
        self.assertEqual(norm.normalize("is located in"),
                         ("located_in", "map"))
        self.assertEqual(
            norm.normalize_triple("A", "is located in", "B"),
            ("A", "located_in", "B", "map"))

    def test_unmapped_surface_still_passes_through(self):
        # Legacy layers unchanged: a single-token surface with
        # no map entry and no canonical-form change is stored
        # verbatim and counted as an unmapped surface.
        stored, layer = self.norm.normalize("vexed")
        self.assertEqual((stored, layer), ("vexed", "surface"))
        self.assertIn("vexed",
                      self.norm.report()["unmapped_surfaces"])


class TestProfileValidation(unittest.TestCase):
    def test_declarations_source_key_registered(self):
        self.assertEqual(setup_config_errors(
            {"relation_declarations_source":
             "packs/open-domain/relation_declarations.json"}), [])
        self.assertTrue(setup_config_errors(
            {"relation_declarations_source": ""}))

    def test_relation_map_dict_entries_validate(self):
        good = {"relation_map": {
            "parent of": {"canonical": "child of", "flipped": True},
            "formed from": "formed from"}}
        self.assertEqual(setup_config_errors(good), [])
        bad = {"relation_map": {"x": {"canonical": "y"}}}
        self.assertTrue(setup_config_errors(bad))


class TestAliasRetention(unittest.TestCase):
    """Item 4: hyphen variants + alias union in the registry."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.reg = SqliteEntityRegistry(self.tmp / "registry.db")
        self.addCleanup(self.reg.close)

    def test_spaced_hyphen_shares_index_key(self):
        self.assertEqual(index_normalize("Charles - Édouard"),
                         index_normalize("Charles-Édouard"))
        self.assertEqual(index_normalize("Kang Won - yong"),
                         index_normalize("Kang Won-yong"))

    def test_hyphen_variants_are_form_variants(self):
        self.assertTrue(is_form_variant("Charles - Édouard",
                                        "Charles-Édouard"))

    def test_merge_retains_both_hyphen_forms(self):
        eid = self.reg.create(Entity(
            entity_id="", name="Charles-Édouard", label="Person",
            batch_id="b1", run_id="r1", doc_id="d1"))
        self.reg.merge(eid, Entity(
            entity_id="", name="Charles - Édouard", label="Person",
            batch_id="b1", run_id="r1", doc_id="d1"))
        ent = self.reg.get(eid)
        forms = {ent.name} | set(ent.aliases)
        self.assertEqual(forms,
                         {"Charles-Édouard", "Charles - Édouard"})
        # Both forms stay addressable.
        self.assertEqual(
            self.reg.resolve("Charles - Édouard").entity_id, eid)
        self.assertEqual(
            self.reg.resolve("Charles-Édouard").entity_id, eid)

    def test_merge_unions_incoming_aliases(self):
        eid = self.reg.create(Entity(
            entity_id="", name="Germany", label="Place",
            batch_id="b1", run_id="r1", doc_id="d1"))
        self.reg.merge(eid, Entity(
            entity_id="", name="Germany", label="Place",
            aliases=["German"], batch_id="b1", run_id="r1",
            doc_id="d1"))
        self.assertIn("German", self.reg.get(eid).aliases)
        self.assertEqual(self.reg.resolve("German").entity_id, eid)

    def test_create_retains_declared_aliases(self):
        eid = self.reg.create(Entity(
            entity_id="", name="Norway", label="Place",
            aliases=["Norwegian"], batch_id="b1", run_id="r1",
            doc_id="d1"))
        self.assertEqual(self.reg.resolve("Norwegian").entity_id,
                         eid)


class _FakeGraph:
    """stage_write graph double (same shape as the ER tests')."""

    def __init__(self):
        self.entities = []
        self.triples = []

    def upsert_entities(self, records):
        self.entities.extend(records)
        return len(records)

    def write_triples(self, triples):
        self.triples.extend(triples)
        return len(triples)


class TestStageWriteDeclarations(unittest.TestCase):
    """Items 2/5/6 end-to-end at WRITE, plus item 4's attested
    surface riding into the registry as an alias."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.reg = SqliteEntityRegistry(self.tmp / "registry.db")
        self.addCleanup(self.reg.close)
        # A minimal declarations artefact in the workspace tree.
        art = {"kind": "relation_declarations",
               "declarations": [
                   {"canonical": "child", "surface": "child of",
                    "flipped": True}],
               "vocabulary": ["formed from"],
               "write_canonicalizations": [
                   {"surface": "parent of", "canonical": "child of",
                    "flipped": True}]}
        self.art_path = self.tmp / "decls.json"
        self.art_path.write_text(json.dumps(art))

    def _run_write(self, rows):
        from pipeline import batch_run
        b = Batch(batch_id="bw", run_id="run-w", source="corpus",
                  collection="coll", doc_ids=["d1"],
                  profile_family="fam",
                  config=BatchConfig(max_cost_usd=None))
        d = batch_dir(self.tmp, "bw")
        d.mkdir(parents=True, exist_ok=True)
        (d / "routed.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows))
        (d / "setup_config.resolved.json").write_text(json.dumps(
            {"relation_declarations_source": str(self.art_path)}))
        save_batch(b, self.tmp)
        graph = _FakeGraph()
        batch_run.stage_write(b, self.tmp, [], self.reg, graph)
        return graph, d

    def test_parent_of_stored_as_child_of_swapped(self):
        graph, _ = self._run_write([
            {"doc_id": "d1", "subject": "Marie Curie",
             "relation": "parent of",
             "object": "Irène Joliot-Curie",
             "context": "Marie Curie, mother of Irène Joliot-Curie.",
             "tier": 1, "p_true": 0.9}])
        self.assertEqual(len(graph.triples), 1)
        t = graph.triples[0]
        self.assertEqual(t.relation, "child of")
        self.assertEqual(self.reg.get(t.subject_id).name,
                         "Irène Joliot-Curie")
        self.assertEqual(self.reg.get(t.object_id).name,
                         "Marie Curie")

    def test_vocabulary_surface_declared_not_unmapped(self):
        _, d = self._run_write([
            {"doc_id": "d1", "subject": "Acme Holdings",
             "relation": "formed from", "object": "Acme Corp",
             "context": "Acme Holdings was formed from Acme Corp.",
             "tier": 1, "p_true": 0.9}])
        report = json.loads((d / "relation_map.json").read_text())
        self.assertEqual(report["layers"].get("map"), 1)
        self.assertNotIn("formed from", report["unmapped_surfaces"])

    def test_attested_surface_retained_as_alias(self):
        self._run_write([
            {"doc_id": "d1", "subject": "Germany",
             "subject_attested": "German",
             "relation": "addressed", "object": "the parliament",
             "context": "The German chancellor addressed the "
                        "parliament.",
             "tier": 1, "p_true": 0.9}])
        ent = self.reg.resolve("Germany")
        self.assertIsNotNone(ent)
        self.assertIn("German", ent.aliases)
        self.assertEqual(self.reg.resolve("German").entity_id,
                         ent.entity_id)


if __name__ == "__main__":
    unittest.main()
