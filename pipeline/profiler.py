"""Corpus profiler: the stage-0 product command.

Profiles a corpus directory *before any build*: document
inventory and distributions, corpus conventions (as verified
scanner claims), decision points for a human, and the
stage-0 ``setup_config`` the batch flow consumes. The output
artefact — ``profile.json`` beside ``setup_config.json`` and
a rendered ``profile.md`` — is the same stage-0 profile the
ontology builder's draft step takes as input.

The census itself is the lab-proven stage-0 service
(``pipeline.setup_service``); this module is its product
surface. The product command is the deterministic path:
mechanical census only, **no LLM drafting, no spend** — the
profile's claim set is the scanners' verified claims. (The
drafting-assisted variant stays available through the setup
service for pack authoring, where a human approves claims
before they bind.)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import setup_service


def profile_corpus(corpus_dir, family: str, out_dir,
                   sample_n: int = 12) -> dict:
    """Profile ``corpus_dir`` into ``out_dir``; return the
    stage-0 profile dict.

    Deterministic: the same corpus yields the same profile
    (``profile_hash`` identical) on every run — the only
    volatile field is the ``created_at`` timestamp. Raises
    ValueError when the corpus directory does not exist or
    holds no .txt documents; nothing is written on refusal.
    """
    corpus = Path(corpus_dir)
    if not corpus.is_dir():
        raise ValueError(f"corpus directory not found: {corpus}")
    try:
        return setup_service.run_setup(
            corpus, family, Path(out_dir),
            sample_n=sample_n, draft=False)
    except RuntimeError as e:
        raise ValueError(str(e)) from e


def summarize(profile: dict, out_dir) -> dict:
    """The machine summary of a finished profile run: the
    fields a caller (or the ``--json`` form) needs without
    parsing the whole profile artefact."""
    inv = profile.get("inventory") or {}
    return {
        "family": profile.get("family"),
        "status": profile.get("status"),
        "profile_hash": profile.get("profile_hash"),
        "documents": inv.get("n_files"),
        "claims": len(profile.get("claims") or []),
        "decision_points": len(profile.get("decision_points")
                               or []),
        "spend_usd": (profile.get("spend") or {}).get(
            "total_usd", 0.0),
        "out_dir": str(out_dir),
        "files": ["profile.json", "setup_config.json",
                  "profile.md"],
    }


def render_card(summary: dict) -> str:
    """The human report card for a profile run (R15 style:
    headline numbers, not a log tail)."""
    lines = [
        "Corpus profile",
        f"  family          {summary['family']}",
        f"  status          {summary['status']}",
        f"  documents       {summary['documents']}",
        f"  claims          {summary['claims']}",
        f"  decision points {summary['decision_points']}",
        f"  profile hash    {summary['profile_hash']}",
        f"  spend           ${summary['spend_usd']:.4f}",
        f"  written         {summary['out_dir']} "
        f"({', '.join(summary['files'])})",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    """``ontosmith profile <corpus_dir> --family F [--out D]
    [--json]`` — deterministic stage-0 census."""
    ap = argparse.ArgumentParser(
        prog="ontosmith profile",
        description="Corpus profiler: stage-0 "
                    "profile of a corpus directory — "
                    "deterministic, no LLM calls, no spend")
    ap.add_argument("corpus_dir",
                    help="directory of .txt documents "
                         "(read recursively)")
    ap.add_argument("--family", required=True,
                    help="corpus family name (names the "
                         "profile and its setup_config)")
    ap.add_argument("--out", default=None,
                    help="artefact directory (default: "
                         "profiles/<family> under the "
                         "current directory)")
    ap.add_argument("--json", action="store_true",
                    help="print the machine summary as JSON "
                         "instead of the report card")
    args = ap.parse_args(argv)

    out_dir = (Path(args.out) if args.out
               else Path("profiles") / args.family)
    try:
        profile = profile_corpus(args.corpus_dir, args.family,
                                 out_dir)
    except ValueError as e:
        print(f"profile refused: {e}", file=sys.stderr)
        return 1
    summary = summarize(profile, out_dir)
    if args.json:
        print(json.dumps(summary, indent=1, ensure_ascii=False))
    else:
        print(render_card(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
