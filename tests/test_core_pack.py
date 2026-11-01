"""Core/default pack tests.

The core pack is the domain-neutral baseline a new user
starts with: core default declarations, the neutral
framing as data, and the enumeration window default — and
no domain content. These tests pin:

- the pack contract (manifest + one sha-pinned payload +
  conformance record), same as the domain packs;
- domain neutrality of the payload (no schema, inventories,
  label categories, merge prohibitions, model names,
  endpoints, or domain framing text);
- the framing values' byte-equality with the core constants
  in pipeline/proposer.py (the payload carries the defaults
  as data, never a fork of them);
- resolution order core -> pack (resolve_setup_config):
  core alone, core + domain override per key, with
  undeclared keys keeping the core defaults;
- fail-closed behavior: a payload whose bytes drift from
  the manifest pin refuses to load, and a specified pack
  whose payload fails validation refuses the whole
  resolution (no fallback to the baseline under its name).
"""
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from pipeline import proposer
from pipeline.profile_config import (
    derive_guidance,
    load_pack_manifest,
    pack_setup_config,
    resolve_setup_config,
)

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "packs" / "core"

# Payload keys that would make the baseline a domain pack.
DOMAIN_CONTENT_KEYS = {
    "schema_front", "entity_types", "relation_types",
    "label_categories", "merge_prohibited_type_pairs",
    "role_word_rules_from_profile", "c1_whitelist",
    "relation_map", "corpus_scope", "proposer_guidance",
}

# Strings that must never appear in the core payload:
# domain framing/inventories, model names, endpoints, secrets.
FORBIDDEN_FRAGMENTS = (
    "commercial contract", "encyclopedia", "association",
    "openrouter", "https://", "api_key",
    "claude", "gpt-", "text-embedding", "jev-1.13", "gliner",
)


class TestCorePackContract(unittest.TestCase):
    def test_manifest_identity(self):
        m = load_pack_manifest(CORE)
        self.assertEqual(m["pack_id"], "core")
        self.assertEqual(m["profile_family"], "core")
        self.assertEqual(m["pack_format"], 1)
        self.assertEqual(m["payload"]["kind"], "setup_config")
        self.assertTrue((ROOT / m["payload"]["path"]).exists())

    def test_payload_loads_pin_verified(self):
        cfg = pack_setup_config(CORE)
        self.assertEqual(cfg["family"], "core")
        self.assertEqual(cfg["pack_id"], "core")

    def test_conformance_record_shape(self):
        rec = json.loads((CORE / "conformance.json").read_text())
        self.assertEqual(rec["pack_id"], "core")
        ids = [e["id"] for e in rec["entries"]]
        self.assertEqual(ids, ["GC-1", "GC-2", "GC-3", "GC-4",
                               "GC-5"])
        for e in rec["entries"]:
            for field in ("content", "treatment", "baseline",
                          "population", "instrument", "delta",
                          "gate", "verdict", "status"):
                self.assertTrue(e[field], f"{e['id']}:{field}")
            self.assertEqual(e["status"], "adopted")


class TestDomainNeutrality(unittest.TestCase):
    def setUp(self):
        self.cfg = pack_setup_config(CORE)

    def test_no_domain_content_keys(self):
        self.assertFalse(DOMAIN_CONTENT_KEYS & set(self.cfg),
                         DOMAIN_CONTENT_KEYS & set(self.cfg))

    def test_no_domain_or_model_strings(self):
        blob = json.dumps(self.cfg).lower()
        for frag in FORBIDDEN_FRAGMENTS:
            self.assertNotIn(frag, blob)

    def test_no_guidance_rendered(self):
        # No schema / vocabularies declared -> the guidance
        # channel renders "" and the proposer prompt is the
        # neutral core construction.
        self.assertEqual(derive_guidance(self.cfg), "")

    def test_framing_is_the_core_constants_as_data(self):
        self.assertEqual(self.cfg["proposer_framing"],
                         proposer.TRIPLE_FRAMING_DEFAULT)
        self.assertEqual(self.cfg["slot_framing"],
                         proposer.SLOT_FRAMING_DEFAULT)
        self.assertEqual(self.cfg["slot_rules"],
                         proposer.SLOT_RULES_DEFAULT)


class TestCoreDefaults(unittest.TestCase):
    def setUp(self):
        self.cfg = pack_setup_config(CORE)

    def test_threshold_declarations_match_core(self):
        from pipeline.batch import BatchConfig
        from pipeline.batch_run import ER_XDOC_MERGE_MIN
        bc = BatchConfig()
        self.assertEqual(self.cfg["triple_threshold"],
                         bc.triple_threshold)
        self.assertEqual(self.cfg["review_band"],
                         [bc.review_band_lo, bc.review_band_hi])
        self.assertEqual(self.cfg["er_threshold"], bc.er_threshold)
        self.assertEqual(self.cfg["batch_cost_cap_usd"],
                         bc.max_cost_usd)
        self.assertEqual(self.cfg["er_cross_doc_min_p_same"],
                         ER_XDOC_MERGE_MIN)

    def test_window_default_declared_not_forced(self):
        # max_dist=1 ('adjacent') is the baseline window
        # default, but the baseline does not force the
        # pair-enumeration branch on for every corpus.
        self.assertEqual(self.cfg["pair_window"], "adjacent")
        self.assertNotIn("pair_enumeration", self.cfg)

    def test_identity_seat_live_by_default(self):
        # Judging is the product's mechanism, not an
        # opt-in: the baseline declares the live identity
        # seat on. A domain pack may declare it false
        # (rule chain alone) or declare the frozen
        # decisions file instead.
        self.assertIs(self.cfg["identity_seat_live"], True)

    def test_jf1_not_activated_without_inventory(self):
        # JF-F1 is an adopted core mechanism, but its symmetric
        # set is domain-pack data: the core payload declares
        # the F1 variant only, never the format or a source.
        self.assertEqual(self.cfg["pair_judge_context"], "sentence")
        self.assertEqual(self.cfg["pair_judge_instruction"],
                         "standard")
        self.assertNotIn("pair_judge_format", self.cfg)
        self.assertNotIn("pair_judge_symmetric_source", self.cfg)


class TestResolution(unittest.TestCase):
    def test_core_alone(self):
        self.assertEqual(resolve_setup_config(ROOT),
                         pack_setup_config(CORE))
        self.assertEqual(resolve_setup_config(ROOT, CORE),
                         pack_setup_config(CORE))

    def test_domain_pack_overrides_per_key(self):
        cfg = resolve_setup_config(ROOT, ROOT / "packs"
                                   / "bio-medical")
        # Declared by Bio Medical -> the pack governs.
        self.assertEqual(cfg["family"], "biored")
        self.assertEqual(cfg["pair_window"], "sentence")
        self.assertEqual(cfg["pair_judge_context"], "abstract")
        self.assertEqual(cfg["pair_judge_instruction"], "evidence")
        self.assertTrue(cfg["pair_enumeration"])
        self.assertIn("schema_front", cfg)
        # Undeclared by Bio Medical -> the core defaults
        # remain (byte-equal to the core code defaults).
        self.assertEqual(cfg["proposer_framing"],
                         proposer.TRIPLE_FRAMING_DEFAULT)
        self.assertEqual(cfg["slot_rules"],
                         proposer.SLOT_RULES_DEFAULT)
        # Shared declarations agree on both layers.
        self.assertEqual(cfg["triple_threshold"], 0.25)

    def test_domain_framing_overrides_neutral(self):
        cfg = resolve_setup_config(ROOT, ROOT / "packs"
                                   / "legal-contract")
        self.assertEqual(
            cfg["proposer_framing"],
            "Extract factual relations from this commercial "
            "contract excerpt.")
        # Legal Contract declares no pair window: the
        # baseline default is inherited (inert — the pack does
        # not select pair enumeration).
        self.assertEqual(cfg["pair_window"], "adjacent")
        self.assertNotIn("pair_enumeration", cfg)

    def test_open_domain_resolution_validates(self):
        cfg = resolve_setup_config(ROOT, ROOT / "packs"
                                   / "open-domain")
        self.assertEqual(cfg["family"], "redocred")
        self.assertEqual(len(cfg["relation_types"]), 96)
        # Its run-specific cost cap overrides the baseline's.
        self.assertEqual(cfg["batch_cost_cap_usd"], 2.0)


class TestFailClosed(unittest.TestCase):
    def _stage_tree(self, tmp: Path) -> Path:
        root = tmp / "root"
        shutil.copytree(CORE, root / "packs" / "core")
        return root

    def test_pin_mismatch_refuses(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._stage_tree(Path(td))
            payload = (root / "packs" / "core" / "profile"
                       / "setup_config.json")
            data = json.loads(payload.read_text())
            data["triple_threshold"] = 0.99  # drifted bytes
            payload.write_text(json.dumps(data))
            with self.assertRaises(ValueError) as cm:
                pack_setup_config(root / "packs" / "core")
            self.assertIn("pin mismatch", str(cm.exception))
            with self.assertRaises(ValueError):
                resolve_setup_config(root)

    def test_invalid_specified_pack_refuses_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._stage_tree(Path(td))
            bad = root / "packs" / "bad-pack"
            (bad / "profile").mkdir(parents=True)
            payload = {"family": "bad", "pair_judge_format": "jf1"}
            pbytes = json.dumps(payload).encode()
            (bad / "profile" / "setup_config.json").write_bytes(
                pbytes)
            (bad / "pack.json").write_text(json.dumps({
                "pack_format": 1, "pack_id": "bad-pack",
                "pack_version": "0.1.0", "profile_family": "bad",
                # Schema-complete: the refusal this
                # test prices is the PAYLOAD's, so the manifest
                # itself must satisfy pack.schema.json.
                "status": "in_force",
                "display_name": "Bad Pack",
                "assembled": "2026-10-22",
                "conformance_record": "conformance.json",
                "payload": {
                    "kind": "setup_config",
                    "path": "packs/bad-pack/profile/"
                            "setup_config.json",
                    "sha256": hashlib.sha256(pbytes).hexdigest(),
                },
            }))
            # 'jf1' without its symmetric source fails payload
            # validation; the resolution must refuse as a whole,
            # never fall back to the baseline under this name.
            with self.assertRaises(ValueError) as cm:
                resolve_setup_config(root, bad)
            self.assertIn("pair_judge_symmetric_source",
                          str(cm.exception))


if __name__ == "__main__":
    unittest.main()
