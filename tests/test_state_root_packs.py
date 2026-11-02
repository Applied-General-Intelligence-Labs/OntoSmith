"""Pack resolution anchored at the state root.

Regression for the cap-probe defect (2026-11-02): batch
creation resolved packs against the code tree, so a pack
placed in the state root's ``packs/`` — the layout a user
with ONTOSMITH_ROOT set naturally reaches for — was
silently ignored and the shipped pack bound instead. The
create path now anchors at state_root() and the candidate
list falls back through the installed tree to the tree
beside the code, so a state root WITHOUT its own packs
still binds the shipped ones.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import batch_cli
from pipeline import profile_config as pc

ROOT = Path(__file__).resolve().parent.parent
SHIPPED_PACK = ROOT / "packs" / "open-domain"


def _variant_pack(dest: Path, cap: int) -> str:
    """Copy the shipped pack, change one payload key, and
    re-pin; returns the variant payload sha."""
    shutil.copytree(SHIPPED_PACK, dest,
                    ignore=shutil.ignore_patterns("__pycache__"))
    sc = dest / "profile" / "setup_config.json"
    cfg = json.loads(sc.read_text())
    cfg["proposer_max_per_chunk"] = cap
    sc.write_text(json.dumps(cfg, indent=1))
    pin = hashlib.sha256(sc.read_bytes()).hexdigest()
    pj = dest / "pack.json"
    man = json.loads(pj.read_text())
    man["payload"]["sha256"] = pin
    pj.write_text(json.dumps(man, indent=1))
    return pin


class StateRootPackCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.state = base / "state"
        self.state.mkdir()
        (self.state / "doc.txt").write_text(
            "Acme Corp was founded by Jane Doe in 2010.")
        self.code = base / "site-packages"  # code only
        self.code.mkdir()
        self.installed = base / "share" / "ontosmith" / "packs"
        self.installed.parent.mkdir(parents=True)
        shutil.copytree(ROOT / "packs", self.installed,
                        ignore=shutil.ignore_patterns("__pycache__"))
        patcher = mock.patch.object(pc, "installed_packs_dir",
                                    return_value=self.installed)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _create(self, batch_id: str) -> dict:
        args = argparse.Namespace(
            source="src", collection="coll",
            docs=[str(self.state / "*.txt")], profile=None,
            pack="open-domain", batch_id=batch_id)
        with mock.patch.dict(
                os.environ, {"ONTOSMITH_ROOT": str(self.state)}):
            with mock.patch.object(batch_cli, "ROOT", self.code):
                batch_cli.cmd_create(args)
        return json.loads(
            (self.state / "batches" / batch_id
             / "batch.json").read_text())


class TestStateRootPackShadows(StateRootPackCase):
    def test_state_root_pack_binds_not_the_shipped_one(self):
        pin = _variant_pack(
            self.state / "packs" / "open-domain", 41)
        record = self._create("shadow-batch")
        prov = record["pack_provenance"]
        self.assertEqual(prov["pack_id"], "open-domain")
        self.assertEqual(prov["pack_payload_sha256"], pin)
        resolved = json.loads(
            (self.state / "batches" / "shadow-batch"
             / "setup_config.resolved.json").read_text())
        self.assertEqual(resolved["proposer_max_per_chunk"], 41)


class TestShippedPackStillBinds(StateRootPackCase):
    def test_no_state_packs_falls_back_to_shipped(self):
        shipped_pin = json.loads(
            (SHIPPED_PACK / "pack.json").read_text()
        )["payload"]["sha256"]
        record = self._create("fallback-batch")
        self.assertEqual(
            record["pack_provenance"]["pack_payload_sha256"],
            shipped_pin)


class TestCandidateOrder(unittest.TestCase):
    def test_root_packs_first_and_package_tree_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "packs").mkdir()
            with mock.patch.object(
                    pc, "installed_packs_dir",
                    return_value=Path(tmp) / "absent"):
                cands = pc.packs_dir_candidates(root)
            self.assertEqual(cands[0], root / "packs")
            self.assertIn(ROOT / "packs", cands)


if __name__ == "__main__":
    unittest.main()
