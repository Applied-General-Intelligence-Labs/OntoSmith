"""Corpus profiler (pipeline/profiler.py): the stage-0
product command.

Covered: the extraction equivalence — the product surface
over a fixture corpus produces the identical stage-0 profile
artefact (modulo the created_at timestamp) as calling the
stage-0 service directly, with an identical profile_hash and
zero spend; the artefact set written (profile.json +
setup_config.json + profile.md); the summary/report-card and
--json forms; the profile feeding the ontology builder's
draft step (the consumer the artefact shape is defined by);
and the refusal paths — missing corpus directory, a corpus
with no .txt documents — refusing cleanly and writing
nothing.
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from pipeline import ontology_builder as ob
from pipeline import profiler, setup_service

DOCS = {
    "alpha.txt": "Acme Corp acquired Beta LLC in 2019. "
                 "Acme Corp is based in Springfield.",
    "beta.txt": "Beta LLC was founded by Jane Doe. The "
                "company employed 40 people in 2018.",
    "gamma.txt": "Springfield hosts the Acme Corp plant. "
                 "Jane Doe visited Springfield in 2020.",
    "delta.txt": "Gamma Inc partners with Beta LLC on "
                 "logistics. Gamma Inc is based in Shelbyville.",
}


def _corpus(tmp: Path) -> Path:
    corpus = tmp / "corpus"
    corpus.mkdir()
    for name, text in DOCS.items():
        (corpus / name).write_text(text)
    (corpus / "README.txt").write_text("download notes, "
                                       "not a document")
    return corpus


def _strip_volatile(profile: dict) -> dict:
    out = dict(profile)
    out.pop("created_at", None)
    return out


class TestProfilerEquivalence(unittest.TestCase):
    def test_product_surface_matches_service_directly(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = _corpus(tmp)
            direct = setup_service.run_setup(
                corpus, "fixfam", tmp / "direct",
                sample_n=4, draft=False)
            via = profiler.profile_corpus(
                corpus, "fixfam", tmp / "via", sample_n=4)
            self.assertEqual(via["profile_hash"],
                             direct["profile_hash"])
            canon = (json.dumps(_strip_volatile(via),
                                sort_keys=True),
                     json.dumps(_strip_volatile(direct),
                                sort_keys=True))
            self.assertEqual(canon[0], canon[1])
            self.assertEqual(via["spend"]["total_usd"], 0.0)
            self.assertIsNone(via["draft_model"])
            for name in ("profile.json", "setup_config.json",
                         "profile.md"):
                self.assertTrue((tmp / "via" / name).is_file(),
                                name)
            on_disk = json.loads(
                (tmp / "via" / "profile.json").read_text())
            self.assertEqual(on_disk["profile_hash"],
                             via["profile_hash"])

    def test_profile_feeds_ontology_draft(self):
        # The artefact shape is defined by its consumer:
        # the builder's draft step reads family + hash.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = _corpus(tmp)
            profile = profiler.profile_corpus(
                corpus, "fixfam", tmp / "out", sample_n=4)
            draft = ob.draft_schema(
                profile, "A small test domain.",
                family="fixfam", pack_id="fix",
                display_name="Fix")
            self.assertEqual(
                draft["provenance"]["profile_hash"],
                profile["profile_hash"])
            self.assertEqual(
                draft["provenance"]["corpus_family"], "fixfam")


class TestProfilerCli(unittest.TestCase):
    def test_cli_card_and_json(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            corpus = _corpus(tmp)
            out = tmp / "out"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = profiler.main(
                    [str(corpus), "--family", "fixfam",
                     "--out", str(out)])
            self.assertEqual(rc, 0)
            card = buf.getvalue()
            self.assertIn("Corpus profile", card)
            self.assertIn("fixfam", card)
            self.assertIn("documents       4", card)

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = profiler.main(
                    [str(corpus), "--family", "fixfam",
                     "--out", str(out),
                     "--json"])
            self.assertEqual(rc, 0)
            summary = json.loads(buf.getvalue())
            self.assertEqual(summary["family"], "fixfam")
            self.assertEqual(summary["documents"], 4)
            self.assertEqual(summary["spend_usd"], 0.0)
            self.assertEqual(
                summary["files"],
                ["profile.json", "setup_config.json",
                 "profile.md"])


class TestProfilerRefusals(unittest.TestCase):
    def test_missing_corpus_dir(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            with self.assertRaises(ValueError):
                profiler.profile_corpus(
                    tmp / "nope", "fam", tmp / "out")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = profiler.main(
                    [str(tmp / "nope"), "--family", "fam",
                     "--out", str(tmp / "out")])
            self.assertEqual(rc, 1)
            self.assertIn("refused", err.getvalue())
            self.assertFalse((tmp / "out").exists())

    def test_empty_corpus_refuses_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            empty = tmp / "empty"
            empty.mkdir()
            (empty / "notes.md").write_text("not a document")
            with self.assertRaises(ValueError):
                profiler.profile_corpus(empty, "fam",
                                        tmp / "out")
            self.assertFalse((tmp / "out").exists())


if __name__ == "__main__":
    unittest.main()
