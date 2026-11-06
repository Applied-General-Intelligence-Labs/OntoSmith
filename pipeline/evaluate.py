"""Generalized eval harness: the hardened protocol for any gate.

Mandatory protocol (SPEC):
  1. document-level dev/held-out split (no doc in both)
  2. threshold tuned on dev only
  3. held-out metrics + cluster-bootstrap 95% CI
  4. optional paired McNemar vs a second result file
  5. optional --stability (bootstrap t*) and --calibration (ECE/bins)

Record schema is fixed: p_true / label / doc_id.
Input files: {"records": [...]} JSON.

Usage:
  python -m pipeline.evaluate --result results/X.json
  python -m pipeline.evaluate --result results/A.json --compare results/B.json
  python -m pipeline.evaluate --result results/X.json --stability --calibration
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyze_hardened import (
    bootstrap_f1,
    mcnemar,
    prf,
    split_by_doc,
    tune_threshold,
)


REQUIRED_FIELDS = ("p_true", "label", "doc_id")


def load_records(path: str) -> list[dict]:
    """Load judgment records from a result file, keeping only
    rows that carry all three fields (p_true, label, doc_id);
    scores/labels are coerced to float/int and the doc id to
    str. The original row is kept under ``_raw``.

    Rows carrying an explicit ``error`` field are judge FAILURES,
    not judgments: they are excluded here —
    the gate runners used to store a failed call as p_true=0.0,
    and scoring it as a judged negative would silently tax the
    gate for an infrastructure failure. ``error_record_count``
    reports how many a file carries, and the CLI surfaces the
    excluded count in its output."""
    raw = json.loads(Path(path).read_text())["records"]
    recs = []
    for r in raw:
        if r.get("error"):
            continue
        if any(f not in r for f in REQUIRED_FIELDS):
            continue
        recs.append({"p_true": float(r["p_true"]),
                     "label": int(r["label"]),
                     "doc_id": str(r["doc_id"]),
                     "_raw": r})
    return recs


def error_record_count(path: str) -> int:
    """How many records in a result file are judge failures
    (explicit ``error`` field) rather than judgments."""
    raw = json.loads(Path(path).read_text())["records"]
    return sum(1 for r in raw if r.get("error"))


def evaluate(recs: list[dict], seed: int = 7,
             iters: int = 500) -> dict:
    """Document-level dev/held-out split; tune the threshold on
    dev, report held-out P/R/F1 at that threshold plus a
    bootstrap 95% CI for F1. The split is by document, so no
    document appears on both sides."""
    dev, held = split_by_doc(recs, seed=seed)
    t = tune_threshold(dev)
    m = prf(held, t)
    lo, hi = bootstrap_f1(held, t, iters=iters)
    return {
        "n_dev": len(dev), "n_held": len(held),
        "threshold": t,
        "held": {k: round(v, 3) for k, v in m.items()},
        "f1_ci95": [round(lo, 3), round(hi, 3)],
    }


def main() -> None:
    """CLI: evaluate one result file; optionally compare two
    (paired McNemar), add threshold-stability and calibration
    reports. See the module docstring for usage."""
    ap = argparse.ArgumentParser(prog="python -m pipeline.evaluate")
    ap.add_argument("--result", required=True)
    ap.add_argument("--compare", default=None,
                    help="second result file for paired McNemar")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--iters", type=int, default=500)
    ap.add_argument("--stability", action="store_true")
    ap.add_argument("--calibration", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    recs = load_records(args.result)
    if not recs:
        sys.exit("no usable records: each record needs "
                 + ", ".join(REQUIRED_FIELDS))
    out = {"result": args.result, **evaluate(recs, args.seed, args.iters)}
    out["n_error_records_excluded"] = error_record_count(args.result)

    if args.compare:
        recs_b = load_records(args.compare)
        out["compare_error_records_excluded"] = error_record_count(
            args.compare)
        dev_a, _ = split_by_doc(recs, seed=args.seed)
        dev_b, _ = split_by_doc(recs_b, seed=args.seed)
        t_a = tune_threshold(dev_a)
        t_b = tune_threshold(dev_b)
        # align schemas for mcnemar (needs subject/relation/object keys)
        out["mcnemar_vs"] = args.compare
        out["mcnemar"] = mcnemar(
            [{"doc_id": r["doc_id"], "subject": r["_raw"].get("subject", ""),
              "relation": r["_raw"].get("relation", ""),
              "object": r["_raw"].get("object", ""),
              "p_true": r["p_true"], "label": r["label"]} for r in recs],
            [{"doc_id": r["doc_id"], "subject": r["_raw"].get("subject", ""),
              "relation": r["_raw"].get("relation", ""),
              "object": r["_raw"].get("object", ""),
              "p_true": r["p_true"], "label": r["label"]} for r in recs_b],
            t_a, t_b)

    if args.stability:
        import random

        import numpy as np

        from . import threshold_stability  # noqa: F401
        dev, _held = split_by_doc(recs, seed=args.seed)
        dev = list(dev)
        rng = random.Random(0)
        ts = [tune_threshold([dev[i] for i in
              (rng.randrange(len(dev)) for _ in dev)]) for _ in range(200)]
        out["t_star_stability"] = {
            "median": round(float(np.median(ts)), 3),
            "sd": round(float(np.std(ts)), 3),
            "p5": round(float(np.percentile(ts, 5)), 2),
            "p95": round(float(np.percentile(ts, 95)), 2),
        }

    if args.calibration:
        from .calibration import analyze
        raw = [{"p_true": r["p_true"], "label": r["label"]} for r in recs]
        out["calibration"] = analyze(raw)

    print(json.dumps(out, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
