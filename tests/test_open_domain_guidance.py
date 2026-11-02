"""Open-domain guidance guards.

The open-domain pack's profile declares relation_types, so
without an explicit proposer_guidance the guidance channel
falls back to rendering those vocabularies as a CLOSED
inventory ("Extract ONLY these relations ...") — the defect
that emptied the 2026-11-01 fiction pilot (proposer proposed
9 kinship-shaped facts where a guidance-free call proposed
23). The pack now declares its guidance verbatim: canonical
names are preferred forms, and stated facts are never dropped
for being unlisted.

These tests pin both halves of that contract:
- the open-domain profile's derived guidance IS its declared
  open guidance (and carries no closed-inventory instruction);
- a profile that declares vocabularies but no guidance still
  gets the closed rendering — the measured families' prompt
  channel is unchanged by the open-domain declaration.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pipeline.profile_config as pc
from pipeline.profile_config import derive_guidance, resolve_pack_artefact

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "packs" / "open-domain" / "profile" / "setup_config.json"


class TestOpenDomainGuidance(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads(PROFILE.read_text())

    def test_declared_guidance_is_used_verbatim(self):
        declared = self.cfg.get("proposer_guidance")
        self.assertIsInstance(declared, str)
        self.assertTrue(declared.strip())
        self.assertEqual(derive_guidance(self.cfg), declared)

    def test_guidance_is_open(self):
        g = derive_guidance(self.cfg)
        self.assertNotIn("Extract ONLY these relations", g)
        self.assertNotIn("Relation inventory (closed)", g)
        self.assertIn("Never drop a stated fact", g)
        # The canonical names are present as preferred forms.
        for name in ("spouse", "place of birth", "member of"):
            self.assertIn(name, g)

    def test_framing_is_genre_neutral(self):
        self.assertEqual(
            self.cfg.get("proposer_framing"),
            "Extract factual relations from this document excerpt.")

    def test_closed_fallback_unchanged_without_declaration(self):
        cfg = {"relation_types": ["spouse", "child"],
               "entity_types": ["PER"]}
        g = derive_guidance(cfg)
        self.assertIn("Relation inventory (closed)", g)
        self.assertIn("Extract ONLY these relations", g)


class TestResolvePackArtefact(unittest.TestCase):
    """WRITE resolves the declarations artefact through the
    same trees the pack lookup consults — a fresh root with no
    packs/ of its own must not fail at WRITE (the 2026-11-01
    bare-root pilot failure)."""

    REL = "packs/open-domain/relation_declarations.json"

    def test_root_wins(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            f = root / self.REL
            f.parent.mkdir(parents=True)
            f.write_text("{}")
            self.assertEqual(resolve_pack_artefact(root, self.REL), f)

    def test_installed_tree(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            installed = base / "share" / "ontosmith" / "packs"
            f = installed / "open-domain" / "relation_declarations.json"
            f.parent.mkdir(parents=True)
            f.write_text("{}")
            with mock.patch.object(
                    pc, "installed_packs_dir", return_value=installed):
                got = resolve_pack_artefact(base / "root", self.REL)
            self.assertEqual(got, f)

    def test_package_tree_fallback(self):
        # An empty root still resolves the real artefact from
        # the source tree beside the package.
        with tempfile.TemporaryDirectory() as td:
            got = resolve_pack_artefact(Path(td), self.REL)
            self.assertIsNotNone(got)
            self.assertTrue(got.exists())

    def test_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(resolve_pack_artefact(
                Path(td), "packs/open-domain/no-such-file.json"))


if __name__ == "__main__":
    unittest.main()
