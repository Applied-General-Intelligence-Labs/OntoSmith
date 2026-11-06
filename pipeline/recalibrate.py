"""Auto-calibration closed loop.

Accumulates judged records plus human review labels, re-tunes the
threshold on a recent window, and flags drift when the retuned
threshold or the deployed threshold's F1 moves beyond tolerance.

History format: JSONL, one record per line:
  {"p_true": 0.7, "label": 1, "ts": "2026-10-10T01:00:00", "source": "human"}

Usage:
  python -m pipeline.recalibrate --history results/judgments.jsonl \\
      --deployed-threshold 0.25 --window 500 --out results/recalibration.json

Exit code 2 + "DRIFT" in output when drift is detected (for cron alerting).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyze_hardened import prf, tune_threshold

T_THRESHOLD_DRIFT = 0.10
F1_DROP_DRIFT = 0.03


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m pipeline.recalibrate")
    ap.add_argument("--history", required=True)
    ap.add_argument("--deployed-threshold", type=float, required=True)
    ap.add_argument("--window", type=int, default=500)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    recs = [json.loads(line) for line in Path(args.history).read_text().splitlines()
            if line.strip()]
    # most recent first by ts if present
    recs.sort(key=lambda r: r.get("ts", ""), reverse=True)
    window = recs[:args.window]
    if len(window) < 50:
        sys.exit(f"not enough labeled records in window: {len(window)}")

    t_new = tune_threshold(window)
    f1_new = prf(window, t_new)["f1"]
    f1_deployed = prf(window, args.deployed_threshold)["f1"]

    drift_reasons = []
    if abs(t_new - args.deployed_threshold) > T_THRESHOLD_DRIFT:
        drift_reasons.append(
            f"threshold moved {args.deployed_threshold:.2f} -> {t_new:.2f}")
    if f1_deployed < f1_new - F1_DROP_DRIFT:
        drift_reasons.append(
            f"F1 at deployed t fell to {f1_deployed:.3f} "
            f"(retuned: {f1_new:.3f})")

    out = {
        "n_window": len(window),
        "n_total": len(recs),
        "deployed_threshold": args.deployed_threshold,
        "retuned_threshold": t_new,
        "f1_at_deployed": round(f1_deployed, 3),
        "f1_at_retuned": round(f1_new, 3),
        "drift": bool(drift_reasons),
        "drift_reasons": drift_reasons,
        "recommendation": (t_new if drift_reasons
                           else args.deployed_threshold),
    }
    print(("DRIFT " if out["drift"] else "OK ") + json.dumps(out))
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1))
    sys.exit(2 if out["drift"] else 0)


if __name__ == "__main__":
    main()
