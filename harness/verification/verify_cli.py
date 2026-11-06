"""Command-line surface for the verification cores.

One-command forms of the three user-runnable loops,
over JSONL inputs in the documented record shapes
(see :mod:`harness.verification.scoring`):

    python -m pipeline verify score --predicted P.jsonl \
        --gold G.jsonl [--normalization T.json] [--out R.json]
    python -m pipeline verify audit-sets --predicted P.jsonl \
        --gold G.jsonl --out-dir DIR [--seed N] \
        [--a-n N] [--b-n N] [--c-n N]
    python -m pipeline verify project --counts unit=n,... \
        [--scenario name:unit=n,...]
    python -m pipeline verify report --predicted P.jsonl \
        --gold G.jsonl [--out R.md] [--title T]

The normalization table may also come from the
ONTOSMITH_NORMALIZATION_TABLE environment variable.

A normalization table file is JSON:
``{"symmetric": [...], "inverses": {"inv": "primary"}}`` —
the domain pack's TN declarations, supplied as data. The
pack's own tn_table.json (tn1_…tn5_ declaration keys) loads
through the same entry point.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .audit import build_audit_sets
from .report import build_report, render_markdown
from .scoring import NormalizationTable, match_records


def load_jsonl(path) -> list[dict]:
    """Read a JSONL file into a list of dicts."""
    return [json.loads(line) for line in
            Path(path).read_text().splitlines() if line.strip()]


def load_table(path) -> NormalizationTable | None:
    """Load a TN normalization table from JSON, or None.

    Keys: ``symmetric`` (TN-1 list), ``inverses`` (TN-2 map),
    and the opt-in layers ``relation_groups`` (TN-3, written
    group label -> member labels), ``inverse_expansions``
    (TN-4, gold relation -> additional inverse labels) and
    ``subsumptions`` (TN-5, gold relation -> stronger labels).

    The domain pack's own tn_table.json (declaration format:
    ``tn1_symmetric_types``, ``tn2_inverse_pairs``,
    ``tn3_relation_groups``, ``tn4_inverse_expansions``,
    ``tn5_subsumptions``) loads through here as well, so the
    shipped pack file is directly usable with ``--normalization``.
    """
    if not path:
        return None
    d = json.loads(Path(path).read_text())
    groups = {}
    for group, labels in (d.get("relation_groups")
                          or d.get("tn3_relation_groups")
                          or {}).items():
        for label in labels:
            groups[label] = group
    inverses = dict(d.get("inverses") or {})
    if not inverses:
        inverses = {p["inverse"]: p["primary"]
                    for p in d.get("tn2_inverse_pairs") or []}
    return NormalizationTable(
        symmetric=frozenset(d.get("symmetric")
                            or d.get("tn1_symmetric_types") or []),
        inverses=inverses,
        relation_groups=groups,
        inverse_expansions={k: tuple(v) for k, v in
                            (d.get("inverse_expansions")
                             or d.get("tn4_inverse_expansions")
                             or {}).items()},
        subsumptions={k: tuple(v) for k, v in
                      (d.get("subsumptions")
                       or d.get("tn5_subsumptions") or {}).items()})


def _kv_pairs(text: str) -> dict[str, float]:
    """Parse 'unit=value,unit=value' into a float dict."""
    out: dict[str, float] = {}
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        unit, _, value = part.partition("=")
        out[unit.strip()] = float(value)
    return out


def main(argv=None) -> int:
    """CLI entry point; returns a process exit code."""
    ap = argparse.ArgumentParser(
        prog="ontosmith verify", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_score = sub.add_parser("score", help="raw exact-match scores")
    p_score.add_argument("--predicted", required=True)
    p_score.add_argument("--gold", required=True)
    p_score.add_argument("--normalization", help=argparse.SUPPRESS)
    p_score.add_argument("--out")

    p_audit = sub.add_parser("audit-sets",
                             help="build the A/B/C judgment sets")
    p_audit.add_argument("--predicted", required=True)
    p_audit.add_argument("--gold", required=True)
    p_audit.add_argument("--normalization", help=argparse.SUPPRESS)
    p_audit.add_argument("--out-dir", required=True)
    p_audit.add_argument("--seed", type=int, default=0, help=argparse.SUPPRESS)
    p_audit.add_argument("--a-n", type=int, help=argparse.SUPPRESS)
    p_audit.add_argument("--b-n", type=int, help=argparse.SUPPRESS)
    p_audit.add_argument("--c-n", type=int, help=argparse.SUPPRESS)

    p_proj = sub.add_parser(
        "project", help="counted workload projection")
    p_proj.add_argument("--counts", required=True,
                        help="unit=count,... for the base scenario")
    p_proj.add_argument("--scenario", action="append", default=[],
                        help="name:unit=count,... (extra scenario)")

    p_rep = sub.add_parser("report", help="full evaluation report")
    p_rep.add_argument("--predicted", required=True)
    p_rep.add_argument("--gold", required=True)
    p_rep.add_argument("--normalization", help=argparse.SUPPRESS)
    p_rep.add_argument("--out")
    p_rep.add_argument("--title", default="Evaluation report", help=argparse.SUPPRESS)

    args = ap.parse_args(argv)
    if getattr(args, "normalization", None) is None:
        args.normalization = os.environ.get(
            "ONTOSMITH_NORMALIZATION_TABLE") or None

    if args.cmd == "score":
        m = match_records(load_jsonl(args.predicted),
                          load_jsonl(args.gold),
                          load_table(args.normalization))
        payload = json.dumps(m.raw_scores(), indent=1)
    elif args.cmd == "audit-sets":
        sets = build_audit_sets(
            load_jsonl(args.predicted), load_jsonl(args.gold),
            sizes={"A": args.a_n, "B": args.b_n, "C": args.c_n},
            seed=args.seed, table=load_table(args.normalization))
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for tag in ("A", "B", "C"):
            with open(out_dir / f"set_{tag}.jsonl", "w") as f:
                for item in sets[tag]:
                    f.write(json.dumps(item) + "\n")
        (out_dir / "frame.json").write_text(
            json.dumps(sets["frame"], indent=1))
        payload = json.dumps(
            {"frame": sets["frame"],
             "sets": {t: len(sets[t]) for t in "ABC"}}, indent=1)
    elif args.cmd == "project":
        # Counts-only projection: the counted workload per
        # scenario. Counts of different units (pairs, docs)
        # are never added together, so the governing scenario
        # is named per unit; `governing_scenario` is the one
        # that governs every unit, or null when units
        # disagree. Pricing a workload is the caller's own
        # arithmetic, outside the product surface.
        scenarios = {"base": _kv_pairs(args.counts)}
        for spec in args.scenario:
            name, _, counts = spec.partition(":")
            scenarios[name.strip()] = _kv_pairs(counts)
        units = sorted({u for items in scenarios.values()
                        for u in items})
        by_unit = {u: max(scenarios,
                          key=lambda s, u=u: scenarios[s].get(u, 0))
                   for u in units}
        leaders = set(by_unit.values())
        governing = leaders.pop() if len(leaders) == 1 else None
        payload = json.dumps(
            {"scenarios": scenarios,
             "governing_by_unit": by_unit,
             "governing_scenario": governing}, indent=1)
    else:  # report
        rep = build_report(load_jsonl(args.predicted),
                           load_jsonl(args.gold),
                           load_table(args.normalization))
        payload = render_markdown(rep, title=args.title)
        if args.out:
            Path(args.out).write_text(payload)
            print(f"report written: {args.out}", file=sys.stderr)
            return 0
    out = getattr(args, "out", None)
    if args.cmd in ("score",) and out:
        Path(out).write_text(payload)
        print(f"scores written: {out}", file=sys.stderr)
        return 0
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
