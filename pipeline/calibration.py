"""Calibration analysis: reliability bins + ECE per model/gate.

Offline — uses stored p_true and labels from the 1K runs.
Usage: python -m pipeline.calibration --out results/calibration.json
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

import numpy as np


def analyze(recs: list[dict], n_bins: int = 10) -> dict:
    ps = np.array([r["p_true"] for r in recs], dtype=float)
    ys = np.array([r["label"] for r in recs], dtype=float)
    bins = []
    ece = 0.0
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        m = (ps >= lo) & (ps < hi) if b < n_bins - 1 else (ps >= lo) & (ps <= hi)
        if m.sum() == 0:
            continue
        acc = float(ys[m].mean())
        conf = float(ps[m].mean())
        ece += m.sum() / len(ps) * abs(acc - conf)
        bins.append({"lo": lo, "hi": hi, "n": int(m.sum()),
                     "mean_pred": round(conf, 3), "empirical_acc": round(acc, 3)})
    hi_m = ps >= 0.9
    return {
        "n": len(recs),
        "ece": round(float(ece), 3),
        "bins": bins,
        "prec_at_p_ge_0_9": (round(float(ys[hi_m].mean()), 3)
                             if hi_m.sum() else None),
        "n_at_p_ge_0_9": int(hi_m.sum()),
    }


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m pipeline.calibration")
    ap.add_argument("--out", default="results/calibration.json")
    args = ap.parse_args()
    out = {}
    for f in sorted(glob.glob("results/scale1k_*.json")):
        name = Path(f).stem.replace("scale1k_", "")
        recs = json.loads(open(f).read())["records"]
        out[name] = analyze(recs)
    Path(args.out).write_text(json.dumps(out, indent=1))
    for name, a in out.items():
        print(f"{name}: ECE={a['ece']} prec@p>=0.9={a['prec_at_p_ge_0_9']} "
              f"(n={a['n_at_p_ge_0_9']})")


if __name__ == "__main__":
    main()
