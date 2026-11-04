"""Batch CLI tests: profile resolution + pack provenance wiring.

cmd_create takes the profile family from the profile document.
For a family it must (a) resolve the family's setup_config
through the pack RESOLVER (core baseline + the family's
pack), freeze the resolved config into the batch
directory, and (b) stamp the batch with the binding pack's
provenance. Runs against a temporary root mirroring the
staging layout; the batch engine itself is unchanged shipped
code.
"""
import argparse
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import batch_cli

STAGING = Path(__file__).resolve().parent.parent


class TestCmdCreateProfileWiring(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # mirror the pack layout: one pack (Bio Medical) + docs
        pack_profile = self.tmp / "packs" / "bio-medical" / "profile"
        pack_profile.mkdir(parents=True)
        for name in ("pack.json",):
            shutil.copy(STAGING / "packs" / "bio-medical" / name,
                        self.tmp / "packs" / "bio-medical" / name)
        for name in ("profile.json", "setup_config.json",
                     "setup_config_v4.json"):
            shutil.copy(STAGING / "packs" / "bio-medical" / "profile"
                        / name, pack_profile / name)
        # the resolver's baseline pack must exist in the
        # mirrored root (resolution fails closed
        # without it)
        shutil.copytree(STAGING / "packs" / "core",
                        self.tmp / "packs" / "core")
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "a.txt").write_text("hello world")

    def _create(self):
        args = argparse.Namespace(
            source="src", collection="coll",
            docs=[str(self.tmp / "docs" / "*.txt")],
            profile=str(self.tmp / "profile.json"),
            batch_id="testbatch1")
        (self.tmp / "profile.json").write_text(json.dumps({
            "family": "biored", "profile_hash": "testhash"}))
        with mock.patch.object(batch_cli, "ROOT", self.tmp):
            batch_cli.cmd_create(args)
        from pipeline.batch import load_batch
        return load_batch(self.tmp, "testbatch1")

    def test_profile_family_resolves_and_stamps_provenance(self):
        batch = self._create()
        self.assertEqual(batch.profile_family, "biored")
        self.assertEqual(batch.profile_hash, "testhash")
        prov = batch.pack_provenance
        self.assertTrue(prov)
        self.assertEqual(prov["pack_id"], "bio-medical")
        self.assertIn("pack_payload_sha256", prov)

    def test_resolved_config_frozen_into_batch_dir(self):
        batch = self._create()
        from pipeline.batch import batch_dir
        from pipeline.profile_config import resolve_setup_config
        frozen = batch_dir(self.tmp, "testbatch1") \
            / "setup_config.resolved.json"
        self.assertTrue(frozen.exists())
        resolved = json.loads(frozen.read_text())
        expected = resolve_setup_config(
            self.tmp, self.tmp / "packs" / "bio-medical")
        self.assertEqual(resolved, expected)
        # the run step executes the frozen resolved config
        from pipeline.batch_run import _load_setup_config
        self.assertEqual(_load_setup_config(batch, self.tmp),
                         resolved)

    def _create_with_generated_profile(self, batch_id,
                                       generated_cfg):
        """The pack path with a generated profile present in
        the state root (profiles/<family>/setup_config.json)
        — the shape `ontosmith profile` leaves behind for a
        user's own corpus."""
        prof_dir = self.tmp / "profiles" / "biored"
        prof_dir.mkdir(parents=True)
        (prof_dir / "setup_config.json").write_text(
            generated_cfg if isinstance(generated_cfg, str)
            else json.dumps(generated_cfg))
        (self.tmp / "profile.json").write_text(json.dumps({
            "family": "biored", "profile_hash": "testhash"}))
        args = argparse.Namespace(
            source="src", collection="coll",
            docs=[str(self.tmp / "docs" / "*.txt")],
            profile=str(self.tmp / "profile.json"),
            batch_id=batch_id)
        out = io.StringIO()
        with mock.patch.object(batch_cli, "ROOT", self.tmp):
            with contextlib.redirect_stdout(out):
                batch_cli.cmd_create(args)
        from pipeline.batch import batch_dir
        frozen = batch_dir(self.tmp, batch_id) \
            / "setup_config.resolved.json"
        return json.loads(frozen.read_text()), out.getvalue()

    def test_profile_framing_overrides_pack_framing(self):
        novel = ("Extract factual relations from this "
                 "excerpt of a novel: the characters, their "
                 "family and social relations, the places, "
                 "and what happens.")
        resolved, out = self._create_with_generated_profile(
            "testframing1", {
                "family": "biored",
                "proposer_framing": novel,
                "er_threshold": 0.99})
        from pipeline.profile_config import resolve_setup_config
        expected = resolve_setup_config(
            self.tmp, self.tmp / "packs" / "bio-medical")
        # the profile's framing governs…
        self.assertEqual(resolved["proposer_framing"], novel)
        # …and ONLY the framing crosses: the profile's
        # threshold does not overlay the pack's resolution
        self.assertEqual(resolved["er_threshold"],
                         expected["er_threshold"])
        self.assertEqual(
            {k: v for k, v in resolved.items()
             if k != "proposer_framing"},
            {k: v for k, v in expected.items()
             if k != "proposer_framing"})
        # the effective framing is printed before any spend
        self.assertIn(f"proposer_framing: {novel}", out)

    def test_profile_without_framing_keeps_pack_resolution(
            self):
        resolved, out = self._create_with_generated_profile(
            "testframing2", {"family": "biored"})
        from pipeline.profile_config import resolve_setup_config
        expected = resolve_setup_config(
            self.tmp, self.tmp / "packs" / "bio-medical")
        # no framing declared, no overlay: the frozen
        # resolution is the pack's, byte-identical — the
        # measured path is untouched
        self.assertEqual(resolved, expected)
        self.assertIn(
            "proposer_framing: Extract factual relations "
            "from the excerpt below.", out)

    def test_unreadable_generated_profile_refuses_create(self):
        with self.assertRaises(SystemExit):
            self._create_with_generated_profile(
                "testframing3", "{not json")

    def test_explicit_pack_without_profile(self):
        args = argparse.Namespace(
            source="src", collection="coll",
            docs=[str(self.tmp / "docs" / "*.txt")],
            profile=None, pack="bio-medical", batch_id="testpack1")
        with mock.patch.object(batch_cli, "ROOT", self.tmp):
            batch_cli.cmd_create(args)
        from pipeline.batch import batch_dir, load_batch
        batch = load_batch(self.tmp, "testpack1")
        self.assertEqual(batch.profile_family, "biored")
        self.assertEqual(batch.pack_provenance["pack_id"],
                         "bio-medical")
        self.assertTrue((batch_dir(self.tmp, "testpack1")
                         / "setup_config.resolved.json").exists())

    def _create_packless(self, batch_id, profile_cfg):
        """A family no pack binds (the hands-on Track 1 shape):
        profiles/<family>/setup_config.json is the only family
        layer; the core baseline must still apply under it."""
        prof_dir = self.tmp / "profiles" / "demo"
        prof_dir.mkdir(parents=True)
        (prof_dir / "setup_config.json").write_text(
            json.dumps(profile_cfg))
        (self.tmp / "profile.json").write_text(json.dumps({
            "family": "demo", "profile_hash": "testhash"}))
        args = argparse.Namespace(
            source="src", collection="coll",
            docs=[str(self.tmp / "docs" / "*.txt")],
            profile=str(self.tmp / "profile.json"),
            batch_id=batch_id)
        with mock.patch.object(batch_cli, "ROOT", self.tmp):
            batch_cli.cmd_create(args)
        from pipeline.batch import batch_dir
        frozen = batch_dir(self.tmp, batch_id) \
            / "setup_config.resolved.json"
        self.assertTrue(frozen.exists())
        return json.loads(frozen.read_text())

    def test_packless_family_baseline_applies_by_default(self):
        resolved = self._create_packless("testnopack1", {
            "family": "demo", "er_threshold": 0.42})
        # baseline declarations reach the frozen config…
        self.assertIs(resolved["identity_seat_live"], True)
        # …and the profile layer governs where it declares
        self.assertEqual(resolved["er_threshold"], 0.42)

    def test_packless_family_decisions_file_supersedes(self):
        resolved = self._create_packless("testnopack2", {
            "family": "demo",
            "identity_seat_decisions": "decisions.jsonl"})
        self.assertEqual(resolved["identity_seat_decisions"],
                         "decisions.jsonl")
        self.assertIs(resolved["identity_seat_live"], True)

    def test_pack_profile_family_mismatch_refused(self):
        (self.tmp / "profile.json").write_text(json.dumps({
            "family": "docred", "profile_hash": "testhash"}))
        args = argparse.Namespace(
            source="src", collection="coll",
            docs=[str(self.tmp / "docs" / "*.txt")],
            profile=str(self.tmp / "profile.json"),
            pack="bio-medical", batch_id="testmismatch")
        with mock.patch.object(batch_cli, "ROOT", self.tmp):
            with self.assertRaises(SystemExit):
                batch_cli.cmd_create(args)

    def test_unresolvable_pack_refused(self):
        args = argparse.Namespace(
            source="src", collection="coll",
            docs=[str(self.tmp / "docs" / "*.txt")],
            profile=None, pack="no-such-pack", batch_id="testbad")
        with mock.patch.object(batch_cli, "ROOT", self.tmp):
            with self.assertRaises(SystemExit):
                batch_cli.cmd_create(args)

    def test_cost_config_surface(self):
        args = argparse.Namespace(
            source="src", collection="coll",
            docs=[str(self.tmp / "docs" / "*.txt")],
            profile=None, pack="bio-medical", batch_id="testcfg",
            max_cost_usd=3.25)
        with mock.patch.object(batch_cli, "ROOT", self.tmp):
            batch_cli.cmd_create(args)
        from pipeline.batch import load_batch
        cfg = load_batch(self.tmp, "testcfg").config
        self.assertEqual(cfg.max_cost_usd, 3.25)

    def test_frontier_escalation_surface_removed(self):
        # The recorded-but-unwired frontier switch was deleted
        # in the CLI simplification: no config fields remain.
        batch = self._create()
        self.assertFalse(hasattr(
            batch.config, "frontier_escalation_enabled"))

    def test_no_profile_family_no_provenance(self):
        args = argparse.Namespace(
            source="src", collection="coll",
            docs=[str(self.tmp / "docs" / "*.txt")],
            profile=str(self.tmp / "profile.json"),
            batch_id="testbatch2")
        (self.tmp / "profile.json").write_text(json.dumps({
            "family": "", "profile_hash": "testhash"}))
        with mock.patch.object(batch_cli, "ROOT", self.tmp):
            batch_cli.cmd_create(args)
        from pipeline.batch import load_batch
        batch = load_batch(self.tmp, "testbatch2")
        self.assertEqual(batch.pack_provenance, {})


class TestCmdCreateRefusesExistingId(unittest.TestCase):
    """Issue #46: `batch create` over an existing batch id
    half-replaced the record — batch.json was rewritten with a
    new run_id/status while state.json and the stage artifacts
    of the previous run survived, leaving `batch status`
    incoherent. Create is fresh-only (the pack-emission idiom:
    a changed thing is a new identity, not an overwrite): it
    refuses BY NAME and touches nothing."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        shutil.copytree(STAGING / "packs" / "core",
                        self.tmp / "packs" / "core")
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "a.txt").write_text("hello world")

    def _args(self, batch_id):
        return argparse.Namespace(
            source="src", collection="coll",
            docs=[str(self.tmp / "docs" / "*.txt")],
            profile=None, pack=None, batch_id=batch_id)

    def _create(self, batch_id="dupbatch"):
        with mock.patch.object(batch_cli, "ROOT", self.tmp):
            batch_cli.cmd_create(self._args(batch_id))

    def test_create_over_existing_id_refuses_and_touches_nothing(self):
        import io
        from contextlib import redirect_stderr

        from pipeline.batch import batch_dir, save_state
        self._create()
        # Simulate a completed run: state.json with stages.
        from pipeline.batch import load_batch
        b = load_batch(self.tmp, "dupbatch")
        save_state(b, self.tmp, ["INGEST", "NER", "CANDIDATES"])
        d = batch_dir(self.tmp, "dupbatch")
        before = {name: (d / name).read_bytes()
                  for name in ("batch.json", "state.json",
                               "doc_paths.json")}
        err = io.StringIO()
        with mock.patch.object(batch_cli, "ROOT", self.tmp):
            with redirect_stderr(err):
                with self.assertRaises(SystemExit) as cm:
                    batch_cli.cmd_create(self._args("dupbatch"))
        self.assertEqual(cm.exception.code, 2)
        msg = err.getvalue()
        self.assertIn("dupbatch", msg)
        self.assertIn("already exists", msg)
        # The remedies are named: run the existing batch, or
        # pick a new --batch-id.
        self.assertIn("batch run dupbatch", msg)
        self.assertIn("--batch-id", msg)
        # Nothing was rewritten: the record stays coherent.
        for name, data in before.items():
            self.assertEqual((d / name).read_bytes(), data, name)
        b2 = load_batch(self.tmp, "dupbatch")
        self.assertEqual(b2.run_id, b.run_id)

    def test_create_with_fresh_id_still_works_after_refusal(self):
        self._create()
        with mock.patch.object(batch_cli, "ROOT", self.tmp):
            with self.assertRaises(SystemExit):
                batch_cli.cmd_create(self._args("dupbatch"))
            batch_cli.cmd_create(self._args("otherbatch"))
        from pipeline.batch import load_batch
        self.assertEqual(load_batch(self.tmp, "otherbatch").batch_id,
                         "otherbatch")


class TestIndexEmbedder(unittest.TestCase):
    """INDEX resolves hosted embedder models only."""

    def test_non_hosted_name_refuses(self):
        from pipeline.batch_run import _index_embedder
        with self.assertRaises(ValueError) as cm:
            _index_embedder("all-MiniLM-L6-v2")
        msg = str(cm.exception)
        self.assertIn("all-MiniLM-L6-v2", msg)
        self.assertIn("embedder seat", msg)
        self.assertIn("no local embedding", msg)

    def test_hosted_name_constructs_hosted_embedder(self):
        from pipeline.batch_run import _index_embedder
        from pipeline.embedder import OpenRouterEmbedder
        env = {"OPENROUTER_API_KEY": "stub-key",
               "ONTOSMITH_EMBEDDER_KEY_ENV": "OPENROUTER_API_KEY"}
        with mock.patch.dict(os.environ, env):
            emb = _index_embedder("openai/text-embedding-3-large")
        self.assertIsInstance(emb, OpenRouterEmbedder)
        self.assertEqual(emb.model_name,
                         "openai/text-embedding-3-large")


if __name__ == "__main__":
    unittest.main()
