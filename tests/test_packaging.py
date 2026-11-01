"""Packaging metadata (pyproject.toml).

The install proof itself (wheel build + throwaway-venv
install) is a recorded release step; this suite
pins the metadata the proof depends on: the project
identity, the console entry point, the package set, and —
critically — that every pack file the metadata promises
to ship actually exists in the tree (a data-files entry
naming a missing file would silently ship a broken pack).
Also pins that the new B3 commands are registered in the
unified CLI, and the launch metadata:
classifiers, project URLs, and keywords — a metadata edit
that drops one of these on the way to PyPI fails here
first.
"""
from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


class TestPyproject(unittest.TestCase):
    def test_identity(self):
        proj = _pyproject()["project"]
        self.assertEqual(proj["name"], "ontosmith")
        self.assertEqual(proj["version"], "0.1.2")
        self.assertEqual(proj["license"], "MIT")
        self.assertEqual(proj["requires-python"], ">=3.11")
        self.assertEqual(proj["authors"],
                         [{"name": "Dongbum Lee"}])
        self.assertTrue((ROOT / "LICENSE").is_file())
        self.assertTrue((ROOT / proj["readme"]).is_file())

    def test_entry_point_and_packages(self):
        data = _pyproject()
        self.assertEqual(
            data["project"]["scripts"],
            {"ontosmith": "pipeline.__main__:main"})
        pkgs = data["tool"]["setuptools"]["packages"]
        self.assertEqual(pkgs, ["pipeline", "harness",
                                "harness.verification", "api"])
        for pkg in pkgs:
            init = ROOT.joinpath(*pkg.split("."), "__init__.py")
            self.assertTrue(init.is_file(), pkg)

    def test_launch_metadata(self):
        proj = _pyproject()["project"]
        classifiers = proj["classifiers"]
        for pinned in (
            "Development Status :: 3 - Alpha",
            "Programming Language :: Python :: 3.11",
            "Programming Language :: Python :: 3.12",
            "Topic :: Scientific/Engineering :: "
            "Artificial Intelligence",
        ):
            self.assertIn(pinned, classifiers)
        # PEP 639: with an SPDX license expression, license
        # classifiers are invalid and break the build —
        # the license renders from the expression instead.
        self.assertFalse(any(c.startswith("License ::")
                             for c in classifiers))
        self.assertEqual(proj["urls"], {
            "Homepage": "https://github.com/Dongbumlee/ontosmith",
            "Documentation":
                "https://github.com/Dongbumlee/ontosmith#readme",
            "Repository": "https://github.com/Dongbumlee/ontosmith",
            "Issues":
                "https://github.com/Dongbumlee/ontosmith/issues",
            "Changelog":
                "https://github.com/Dongbumlee/ontosmith/"
                "blob/main/CHANGELOG.md",
        })
        for keyword in ("knowledge-graph", "ontology",
                        "information-extraction", "graphrag"):
            self.assertIn(keyword, proj["keywords"])
        # The Changelog URL must resolve to a file that
        # exists in this tree.
        self.assertTrue((ROOT / "CHANGELOG.md").is_file())

    def test_dependencies_classified(self):
        proj = _pyproject()["project"]
        self.assertEqual(sorted(proj["dependencies"]),
                         ["numpy", "requests"])
        extras = proj["optional-dependencies"]
        self.assertEqual(extras["neo4j"], ["neo4j"])
        # No "nlp" extra: the GLiNER+spaCy pipeline
        # candidate source was retired 2026-10-29 — there
        # is no local NER/RER surface left to install.
        self.assertNotIn("nlp", extras)
        # No "embed" extra: there is no local
        # embedding path for an extra to install.
        self.assertNotIn("embed", extras)

    def test_app_assets_ship(self):
        # The demo app is data, resolved by
        # api.server.app_candidates from the installed
        # share/ layout — the promised set is exactly the
        # five static files (the brand mark included).
        data_files = _pyproject()["tool"]["setuptools"][
            "data-files"]
        self.assertEqual(
            sorted(data_files["share/ontosmith/app"]),
            ["app/app.css", "app/app.js",
             "app/favicon.svg", "app/index.html",
             "app/logo-mark.svg"])

    def test_every_promised_pack_file_exists(self):
        data_files = _pyproject()["tool"]["setuptools"][
            "data-files"]
        shipped = [f for files in data_files.values()
                   for f in files]
        self.assertGreater(len(shipped), 20)
        for rel in shipped:
            self.assertTrue((ROOT / rel).is_file(), rel)
        # Every pack dir ships its manifest + conformance.
        for slug in ("bio-medical", "core",
                     "open-domain", "legal-contract"):
            self.assertIn(f"packs/{slug}/pack.json", shipped)
            self.assertIn(f"packs/{slug}/conformance.json",
                          shipped)


class TestCliRegistration(unittest.TestCase):
    def test_b3_commands_registered(self):
        import pipeline.__main__ as cli
        self.assertEqual(cli.COMMANDS["profile"][0],
                         "pipeline.profiler")
        self.assertEqual(cli.COMMANDS["inspect"][0],
                         "pipeline.inspect")


if __name__ == "__main__":
    unittest.main()
