"""Mechanical documentation-drift guards.

The 2026-10-23 doc-sync audit (DS-1…DS-12) found drift in
surfaces that predate the feature they describe. Three of
those drift classes are mechanically checkable, so they
are guarded here instead of waiting for the next manual
sweep:

1. Generated-file freshness — the committed PyPI README
   variant must equal a fresh run of its generator
   (DS-1's class).
2. Printed-command existence — every `ontosmith <cmd>`
   invocation printed in the main docs must name a real
   command, a real subcommand, and only flags that exist
   on that command's `--help` (the audit's command check,
   mechanized).
3. Key-doc presence — the docs that teach binding setup
   must mention `configure`, the declaration front door
   (DS-2/DS-3's class).
4. Packaging coverage — every file under packs/ in the
   source tree must be covered by the pyproject data-files
   declaration, so it ships in the wheel (the 0.1.4
   omission class: relation_declarations.json never
   reached wheel installs).

Prose-claim consistency and stale numbers are judgment
work; they stay with the per-feature doc-sync sweep.
Nothing here edits a document — a failure names the
surface to fix.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

COMMAND_DOCS = (
    "README.md",
    "docs/hands-on.md",
    "docs/cli-reference.md",
    "examples/README.md",
)
BINDING_DOCS = (
    "README.md",
    "examples/README.md",
    "docs/hands-on.md",
)

_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_INLINE_RE = re.compile(r"`([^`\n]+)`")
_INVOKE_RE = re.compile(r"ontosmith\s+([a-z][a-z0-9-]*)([^\n]*)")
_FLAG_RE = re.compile(r"--[a-z][a-z0-9-]*")
_WORD_RE = re.compile(r"[a-z][a-z0-9-]*")


def _code_spans(text: str) -> list[str]:
    """Fenced blocks + inline code spans, continuations joined."""
    text = text.replace("\\\n", " ")
    return ([m.group(1) for m in _FENCE_RE.finditer(text)]
            + [m.group(1) for m in _INLINE_RE.finditer(text)])


def test_pypi_readme_variant_is_fresh() -> None:
    """DS-1 guard: committed variant == fresh generator output."""
    spec = importlib.util.spec_from_file_location(
        "build_pypi_readme", ROOT / "scripts" / "build_pypi_readme.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    master = (ROOT / "README.md").read_text(encoding="utf-8")
    variant, _counts = module.build_variant(master)
    committed = (ROOT / "build" / "pypi" / "README.md").read_text(
        encoding="utf-8")
    assert committed == variant, (
        "build/pypi/README.md is stale: regenerate it with "
        "`python scripts/build_pypi_readme.py` and commit the result")


_HELP_CACHE: dict[tuple[str, ...], str] = {}


def _help_text(*argv: str) -> str:
    if argv not in _HELP_CACHE:
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        proc = subprocess.run(
            [sys.executable, "-m", "pipeline", *argv, "--help"],
            cwd=ROOT, env=env, capture_output=True, text=True,
            timeout=60)
        assert proc.returncode == 0, (
            f"`ontosmith {' '.join(argv)} --help` failed: "
            f"{proc.stderr.strip()[:200]}")
        _HELP_CACHE[argv] = proc.stdout + proc.stderr
    return _HELP_CACHE[argv]


def _subcommands(command: str) -> set[str]:
    match = re.search(r"\{([^}]*)\}", _help_text(command))
    if not match:
        return set()
    return {part.strip() for part in match.group(1).split(",")}


def test_printed_commands_exist() -> None:
    """Printed invocations name real commands/subcommands/flags."""
    from pipeline.__main__ import COMMANDS

    problems: list[str] = []
    for rel in COMMAND_DOCS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for span in _code_spans(text):
            for match in _INVOKE_RE.finditer(span):
                command, rest = match.group(1), match.group(2)
                if command not in COMMANDS:
                    problems.append(
                        f"{rel}: unknown command in "
                        f"{match.group(0)[:60]!r}")
                    continue
                tokens = [t.strip("`'\".,;:()")
                          for t in rest.split()]
                argv: tuple[str, ...] = (command,)
                if (tokens and _WORD_RE.fullmatch(tokens[0])
                        and tokens[0] in _subcommands(command)):
                    argv = (command, tokens[0])
                help_flags = set(_FLAG_RE.findall(_help_text(*argv)))
                for token in tokens:
                    flag = token.split("=")[0]
                    if (_FLAG_RE.fullmatch(flag)
                            and flag not in help_flags):
                        problems.append(
                            f"{rel}: `ontosmith {' '.join(argv)}` "
                            f"has no flag {flag} "
                            f"(in {match.group(0)[:60]!r})")
    assert not problems, "\n".join(problems)


def test_binding_docs_mention_configure() -> None:
    """DS-2/DS-3 guard: binding-setup docs name the front door."""
    for rel in BINDING_DOCS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "ontosmith configure" in text, (
            f"{rel} teaches binding setup but never mentions "
            "`ontosmith configure`")


def _declared_pack_files() -> set[str]:
    """Source-relative paths the pyproject data-files
    declaration ships for the packs tree: every pattern
    under a share/ontosmith/packs* target, glob-expanded
    against the source tree (the same expansion the
    build performs)."""
    import glob as glob_module
    import tomllib

    with open(ROOT / "pyproject.toml", "rb") as fh:
        pyproject = tomllib.load(fh)
    data_files = pyproject["tool"]["setuptools"]["data-files"]
    shipped: set[str] = set()
    for target, patterns in data_files.items():
        if not target.startswith("share/ontosmith/packs"):
            continue
        for pattern in patterns:
            shipped.update(glob_module.glob(pattern,
                                            root_dir=ROOT))
    return shipped


def test_pack_data_files_cover_the_pack_tree() -> None:
    """Packaging guard: a pack file the declaration does
    not cover would be missing from the wheel.

    The 0.1.4 wheel silently omitted
    packs/open-domain/relation_declarations.json — the
    packaging kept an explicit per-file list and nobody
    extended it when the artefact landed — so every wheel
    install's open-domain batch failed closed at WRITE.
    The declaration is per-directory globs now; this test
    evaluates them, so a future pack file no pattern
    covers fails here, in the suite, instead of in a
    user's first build."""
    shipped = _declared_pack_files()
    missing = []
    for path in sorted((ROOT / "packs").rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel not in shipped:
            missing.append(rel)
    assert not missing, (
        "pack files the packaging declaration does not "
        "ship (they would be missing from the wheel): "
        + ", ".join(missing))


# The settled store story (RR program): the pre-BO story —
# "the graph is a rebuildable projection / no database
# needed" — has resurfaced in entry docs three times. The
# current-tense telling surfaces must not carry its marker
# phrases. Deliberately NOT covered: docs/whitepaper.md
# (the term there is the data-model name for the
# registry→graph mapping, kept by the BO program's ruling)
# and CHANGELOG history sections (a record of what was
# true when written).
STORE_STORY_DOCS = (
    "README.md",
    "llms.txt",
    "docs/connections.md",
    "docs/serving.md",
    "docs/hands-on.md",
    "docs/cli-reference.md",
    "docs/release-notes-v0.1.0-draft.md",
    "examples/README.md",
)

BANNED_STORE_PHRASES = (
    "rebuildable",
    "no database to run",
    "registry→neo4j projection",
    "registry->neo4j projection",
    "no external store",
)


def test_store_story_docs_tell_the_settled_story() -> None:
    """RR guard: entry surfaces tell the settled store story."""
    offenders = []
    for rel in STORE_STORY_DOCS:
        text = (ROOT / rel).read_text(encoding="utf-8").lower()
        for phrase in BANNED_STORE_PHRASES:
            if phrase in text:
                offenders.append(f"{rel}: {phrase!r}")
    assert not offenders, (
        "the settled store story (Neo4j is the store where "
        "the graph lives; the file store is the keyless "
        "read-back) is contradicted by: " + "; ".join(offenders))
