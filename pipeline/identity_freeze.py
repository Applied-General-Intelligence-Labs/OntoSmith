"""Freeze per-point identity-seat decisions into a
global decisions file (identity redesign D2/D3).

The per-document decisions file keys verdicts by
document: the same (fragment, candidate) pair can be
judged again in every document it appears in, and two
documents can disagree. The freeze step is the corpus
level above it: it groups the per-point verdicts by
the global pair, freezes each unanimous pair to its
one verdict, and promotes disagreeing pairs to
first-class ambiguous records (both surfaces kept,
routed to the review band) instead of letting a later
record silently overwrite an earlier one.

Usage:
    ontosmith identity-freeze POINTS.jsonl FREEZE.jsonl

POINTS.jsonl is the per-point decisions shape
({"doc", "ep", "cand", "decision"}; decision
"complete" | "stand", or null/absent where the seat
abstained). FREEZE.jsonl is the global form a profile
declares as identity_seat_decisions_global.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .proposer import freeze_identity_decisions


def read_points(path) -> list[dict]:
    """Read per-point decisions, failing closed on any
    malformed record (the freeze is a contract step,
    not a best-effort import)."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ep = rec["ep"]
                cand = rec["cand"]
            except Exception as e:
                raise ValueError(
                    f"{path}:{lineno}: malformed record: "
                    f"{e}") from e
            decision = rec.get("decision")
            if decision not in ("complete", "stand", None):
                raise ValueError(
                    f"{path}:{lineno}: decision must be "
                    "'complete', 'stand' or null")
            if not all(isinstance(v, str) and v
                       for v in (ep, cand)):
                raise ValueError(
                    f"{path}:{lineno}: ep/cand must be "
                    "non-empty strings")
            rows.append({"doc": rec.get("doc"), "ep": ep,
                         "cand": cand, "decision": decision})
    return rows


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        prog="identity-freeze",
        description="Freeze per-point identity-seat "
                    "decisions into a global decisions "
                    "file (unanimous pairs freeze; "
                    "disagreeing pairs become ambiguous "
                    "records).")
    ap.add_argument("points",
                    help="per-point decisions JSONL "
                         "({doc, ep, cand, decision})")
    ap.add_argument("freeze",
                    help="output global freeze JSONL")
    args = ap.parse_args(argv)
    try:
        rows = read_points(Path(args.points))
    except (OSError, ValueError) as e:
        print(f"identity-freeze: {e}", file=sys.stderr)
        raise SystemExit(2) from e
    frozen, ambiguous = freeze_identity_decisions(rows)
    with open(args.freeze, "w", encoding="utf-8") as f:
        for rec in frozen:
            f.write(json.dumps(rec, ensure_ascii=False)
                    + "\n")
        for rec in ambiguous:
            f.write(json.dumps(rec, ensure_ascii=False)
                    + "\n")
    decided = sum(1 for r in frozen
                  if r["decision"] is not None)
    print(f"identity-freeze: {len(rows)} points -> "
          f"{len(frozen)} frozen pairs ({decided} decided, "
          f"{len(frozen) - decided} no-opinion), "
          f"{len(ambiguous)} ambiguous -> {args.freeze}")


if __name__ == "__main__":
    main()
