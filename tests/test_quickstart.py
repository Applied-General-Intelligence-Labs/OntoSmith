"""Quickstart wrapper guards (no-spend).

The wrapper's contract is preflight honesty: a missing file
and a missing key refuse BEFORE anything is created, with
the remedy named; the batch id derives from the file stem.
The build/serve sequence itself is the batch/serve machinery
under its own tests — here only the door is pinned.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline.quickstart import _batch_id_for, main


class TestQuickstartPreflight(unittest.TestCase):
    def test_missing_file_refuses(self):
        self.assertEqual(main(["/no/such/file.txt"]), 2)

    def test_missing_key_refuses_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            doc = Path(td) / "mine.txt"
            doc.write_text("Some text.\n")
            env = {"ONTOSMITH_ROOT": td, "PATH": os.environ.get("PATH", "")}
            with mock.patch.dict(os.environ, env, clear=True):
                rc = main([str(doc), "--no-serve"])
            self.assertEqual(rc, 1)
            self.assertFalse((Path(td) / "batches").exists())

    def test_dead_neo4j_refuses_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            doc = Path(td) / "mine.txt"
            doc.write_text("Some text.\n")
            env = {"ONTOSMITH_ROOT": td,
                   "PATH": os.environ.get("PATH", ""),
                   "OPENROUTER_API_KEY": "dummy",
                   "NEO4J_URL": "http://127.0.0.1:9"}
            with mock.patch.dict(os.environ, env, clear=True):
                rc = main([str(doc), "--no-serve"])
            self.assertEqual(rc, 1)
            self.assertFalse((Path(td) / "batches").exists())

    def _captured_create(self, env):
        """Run quickstart up to batch create; return the
        create Namespace it built (nothing is created)."""
        seen = {}

        def fake_create(args):
            seen["args"] = args
            raise SystemExit(0)

        from pipeline import batch_cli, quickstart
        with tempfile.TemporaryDirectory() as td:
            doc = Path(td) / "mine.txt"
            doc.write_text("Some text.\n")
            full = {"ONTOSMITH_ROOT": td,
                    "PATH": os.environ.get("PATH", ""), **env}
            with mock.patch.dict(os.environ, full, clear=True), \
                    mock.patch.object(quickstart, "_neo4j_reachable",
                                      return_value=True), \
                    mock.patch.object(batch_cli, "cmd_create",
                                      fake_create):
                self.assertEqual(main([str(doc), "--no-serve"]), 0)
        return seen["args"]

    def test_metered_binding_gets_default_cap(self):
        # The preset (OpenRouter) reports cost: a first run is
        # capped at $2 unless ONTOSMITH_MAX_COST_USD says else.
        from pipeline.quickstart import QUICKSTART_CAP_USD
        args = self._captured_create({"OPENROUTER_API_KEY": "dummy"})
        self.assertEqual(args.default_max_cost_usd, QUICKSTART_CAP_USD)

    def test_cost_less_binding_runs_uncapped(self):
        # A binding that cannot meter cost gets no default
        # cap (it could never trip and would only refuse).
        args = self._captured_create({
            "ONTOSMITH_BASE_URL": "http://gw.example:9000/v1",
            "OPENROUTER_API_KEY": "dummy"})
        self.assertIsNone(args.default_max_cost_usd)

    def test_batch_id_derivation(self):
        self.assertEqual(
            _batch_id_for(Path("/x/My Notes (final).txt"), None),
            "quickstart-My-Notes-final")
        self.assertEqual(
            _batch_id_for(Path("/x/a.txt"), "given"), "given")

    def test_registered_in_cli(self):
        from pipeline.__main__ import COMMANDS
        self.assertIn("quickstart", COMMANDS)
        module, _group, _desc, _fixed = COMMANDS["quickstart"]
        self.assertEqual(module, "pipeline.quickstart")


if __name__ == "__main__":
    unittest.main()
