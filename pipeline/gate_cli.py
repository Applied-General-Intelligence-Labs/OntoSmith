"""Gate + model evaluation CLI — the shipped form of the lab's
``run_*`` drivers.

Each subcommand evaluates one judge over a candidate file (or,
for ``triple``/``cascade``, over candidates built from a corpus)
and writes a ``{"summary": ..., "records": [...]}`` JSON result.
These are *evaluation* runners: they measure a gate or a model
against labeled candidates; the production build path is the
batch CLI (``pipeline.batch_cli``).

    triple    decision-gate validation on DocRED candidates
    er        ER gate: Jev judges mention-pair coreference
    conflict  conflict gate: Jev classifies claims vs the graph
    onto      ontology gate: Jev judges proposed axioms
    llm       LLM-as-judge (text Yes/No)
    ollama    triple judgment via a local Ollama model
    cascade   choice-cascade dense graph builder/evaluator

Behavior contract: this module is a collapse of the lab's
per-gate runner scripts onto one shared candidate-loop engine.
Argument surfaces, defaults, record shapes, summary math, and
output formats are the originals', pinned by golden tests
(tests/test_gate_cli.py) captured from the lab runners.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

from .decisions import (
    choice_gate,
    conflict_judge,
    er_judge,
    llm_text_judge,
    ontology_judge,
    score_gate,
    validate_triple,
)
from .docred import build_candidates, doc_text, entity_name, load_dev
from .hard_negatives import build_hard_negatives, confusion_choices
from .reference_profile import ESCALATION_MODEL, JUDGE_MODEL

#: The Jev gate binding (pinned product component).
JEV = JUDGE_MODEL

_ZERO_USAGE = {"cost": 0.0, "latency_ms": 0.0, "provider": None}

def _default_results_dir() -> Path:
    """Where gate-eval outputs are written: the state root's
    results/ directory (batch_cli.state_root) — the operator
    workspace, never the installed package tree."""
    from .batch_cli import state_root
    return state_root() / "results"


#: The gate-eval output directory. A module attribute on
#: purpose: the writer reads the global at call time, and the
#: test suite patches it to redirect outputs.
RESULTS_DIR = _default_results_dir()


# ------------------------------------------------------------------
# Shared candidate-loop engine (the collapsed runners' common core)
# ------------------------------------------------------------------

def _load_candidates(path: str, max_candidates: int) -> list[dict]:
    """Load a candidate JSON file, capped at ``max_candidates``."""
    return json.loads(Path(path).read_text())[:max_candidates]


def _eval_loop(cands, judge_fields, error_fields):
    """Run one judge over all candidates.

    ``judge_fields(c)`` returns ``(record, usage)`` for candidate
    ``c`` and raises on judge failure; ``error_fields(c)`` returns
    the record to store for a failed judge call (the failure is
    printed to stderr and the record carries the runner's error
    shape — a failed call is data, never a silent skip).
    Returns ``(records, latencies, total_cost)``.

    Every error record additionally carries an
    explicit ``error`` field (the exception text) — before, an
    er/onto failure was a bare p_true=0.0 with provider=None,
    indistinguishable in the output file from a judged negative —
    and every arm's summary reports the error count.
    """
    records, lat, cost = [], [], 0.0
    for i, c in enumerate(cands):
        if i % 25 == 0:
            print(f"  {i}/{len(cands)}", flush=True)
        try:
            rec, usage = judge_fields(c)
        except Exception as e:
            # Judge failures are data: the runner stores the
            # error record and continues (originals' contract).
            print(f"  !! {i}: {e}", file=sys.stderr)
            rec, usage = error_fields(c), _ZERO_USAGE
            rec["error"] = str(e)
        records.append(rec)
        lat.append(usage.get("latency_ms", 0.0) or 0.0)
        # None-aware: a successful call whose
        # provider reported no cost makes the run's total
        # unknown (None), never a silent 0.0 fold-in. The
        # error path's _ZERO_USAGE (cost 0.0) is a genuinely
        # unspent call and stays a known zero.
        if usage.get("cost") is None and usage is not _ZERO_USAGE:
            cost = None
        elif cost is not None:
            cost += usage.get("cost") or 0.0
    return records, lat, cost


def _summary(model: str, records, lat, cost,
             extra: dict | None = None) -> dict:
    """The runners' common summary block (latency percentiles are
    the originals' index math, kept verbatim). The error count is
    computed here so EVERY arm reports it (previously
    only the llm arm did)."""
    lat_sorted = sorted(lat)
    summary = {
        "model": model, "n": len(records),
        "total_cost_usd": None if cost is None else round(cost, 6),
        "latency_ms_p50": round(lat_sorted[len(lat_sorted) // 2], 1),
        "latency_ms_p95": round(lat_sorted[int(len(lat_sorted) * 0.95)], 1),
        "providers": sorted({r["provider"] for r in records
                             if r["provider"]}),
        "errors": sum(1 for r in records if r.get("error")),
    }
    if extra:
        summary.update(extra)
    return summary


def _write_result(out: str, summary: dict, records) -> None:
    """Write the standard {"summary", "records"} result file."""
    Path(out).write_text(json.dumps(
        {"summary": summary, "records": records}, ensure_ascii=False))


def _report(out: str, summary: dict, with_errors: bool = False) -> None:
    total = summary["total_cost_usd"]
    cost_s = f"${total:.4f}" if total is not None else "not metered"
    line = (f"Wrote {out} | cost={cost_s} "
            f"p50={summary['latency_ms_p50']:.0f}ms")
    if with_errors:
        line += f" errors={summary['errors']}"
    print(line)


# ------------------------------------------------------------------
# er — ER gate evaluation
# ------------------------------------------------------------------

def _er_cmd(args) -> None:
    cands = _load_candidates(args.candidates, args.max_candidates)
    print(f"{len(cands)} ER candidates; calling Jev gate...")

    def base(c):
        return {"doc_id": c["doc_id"], "mention_a": c["mention_a"],
                "mention_b": c["mention_b"], "label": c["label"],
                "neg_kind": c["neg_kind"]}

    def judge(c):
        p_true, usage = er_judge(
            c["context"], c["mention_a"], c["mention_b"],
            model=args.model)
        return ({**base(c), "p_true": round(p_true, 4),
                 "provider": usage.get("provider")}, usage)

    def error(c):
        return {**base(c), "p_true": 0.0, "provider": None}

    records, lat, cost = _eval_loop(cands, judge, error)
    summary = _summary(args.model, records, lat, cost)
    _write_result(args.out, summary, records)
    _report(args.out, summary, with_errors=True)


# ------------------------------------------------------------------
# conflict — conflict gate evaluation
# ------------------------------------------------------------------

def _conflict_cmd(args) -> None:
    cands = _load_candidates(args.candidates, args.max_candidates)
    print(f"{len(cands)} conflict candidates; calling Jev gate...")

    def base(c):
        return {"doc_id": c["doc_id"], "subject": c["subject"],
                "relation": c["relation"], "object": c["object"],
                "gold": c["gold"]}

    def judge(c):
        choice, conf, probs, usage = conflict_judge(
            [(r, o) for r, o in c["existing_facts"]],
            c["subject"], c["relation"], c["object"], model=args.model)
        return ({**base(c), "choice": choice,
                 "confidence": round(conf, 4),
                 "probabilities": {k: round(v, 4)
                                   for k, v in probs.items()},
                 "provider": usage.get("provider")}, usage)

    def error(c):
        return {**base(c), "choice": "error", "confidence": 0.0,
                "probabilities": {}, "provider": None}

    records, lat, cost = _eval_loop(cands, judge, error)
    summary = _summary(args.model, records, lat, cost)
    _write_result(args.out, summary, records)
    _report(args.out, summary, with_errors=True)


# ------------------------------------------------------------------
# onto — ontology gate evaluation
# ------------------------------------------------------------------

def _onto_cmd(args) -> None:
    cands = _load_candidates(args.candidates, args.max_candidates)
    print(f"{len(cands)} ontology candidates; calling Jev gate...")

    def base(c):
        return {"doc_id": c["doc_id"], "ontology": c["ontology"],
                "axiom": c["axiom"], "a": c["a"], "b": c["b"],
                "label": c["gold"]}

    def judge(c):
        p, usage = ontology_judge(
            c["ontology"], c["axiom"], c["a"], c["b"], model=args.model)
        return ({**base(c), "p_true": round(p, 4),
                 "provider": usage.get("provider")}, usage)

    def error(c):
        return {**base(c), "p_true": 0.0, "provider": None}

    records, lat, cost = _eval_loop(cands, judge, error)
    summary = _summary(args.model, records, lat, cost)
    _write_result(args.out, summary, records)
    _report(args.out, summary, with_errors=True)


# ------------------------------------------------------------------
# llm — LLM-as-judge evaluation (text Yes/No)
# ------------------------------------------------------------------

def _llm_cmd(args) -> None:
    cands = _load_candidates(args.candidates, args.max_candidates)
    print(f"{len(cands)} candidates; LLM judge: {args.model}")

    def base(c):
        return {"doc_id": c["doc_id"], "subject": c["subject"],
                "relation": c["relation"], "object": c["object"],
                "label": c["label"]}

    def judge(c):
        label, usage = llm_text_judge(
            c["text"], c["subject"], c["relation"], c["object"],
            model=args.model, max_tokens=args.max_tokens)
        rec = {**base(c), "provider": usage.get("provider"),
               "pred": label}
        if usage.get("parse_failed"):
            # The label came from the raw-text
            # fallback, not a parsed answer — visible on the
            # record and counted in the summary.
            rec["parse_failed"] = True
        return rec, usage

    def error(c):
        return {**base(c), "provider": None, "pred": -1}

    records, lat, cost = _eval_loop(cands, judge, error)
    summary = _summary(args.model, records, lat, cost,
                       extra={"parse_failures": sum(
                           1 for r in records
                           if r.get("parse_failed"))})
    _write_result(args.out, summary, records)
    _report(args.out, summary, with_errors=True)


# ------------------------------------------------------------------
# triple — decision-gate validation on DocRED candidates
# ------------------------------------------------------------------

def calibration_bins(probs: list[float], labels: list[int],
                     n_bins: int = 10) -> list[dict]:
    """Bin predicted probabilities into ``n_bins`` equal-width bins
    with per-bin mean probability and empirical accuracy."""
    bins = []
    for i in range(n_bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        idx = [j for j, p in enumerate(probs)
               if lo <= p < hi or (hi == 1.0 and p == 1.0)]
        if not idx:
            bins.append({"bin": [lo, hi], "n": 0,
                         "mean_p": None, "acc": None})
            continue
        mean_p = sum(probs[j] for j in idx) / len(idx)
        acc = sum(labels[j] for j in idx) / len(idx)
        bins.append({"bin": [lo, hi], "n": len(idx),
                     "mean_p": mean_p, "acc": acc})
    return bins


def summarize(records: list[dict]) -> dict:
    """Summary metrics for triple-gate records: accuracy,
    precision/recall at p>=0.5, mean p_true per class, latency
    percentiles, cost/token totals, calibration bins, and a
    per-negative-kind rejection breakdown."""
    import statistics
    probs = [r["p_true"] for r in records]
    labels = [r["label"] for r in records]
    preds = [1 if p >= 0.5 else 0 for p in probs]
    tp = sum(1 for pr, lb in zip(preds, labels, strict=True) if pr == 1 and lb == 1)
    fp = sum(1 for pr, lb in zip(preds, labels, strict=True) if pr == 1 and lb == 0)
    fn = sum(1 for pr, lb in zip(preds, labels, strict=True) if pr == 0 and lb == 1)
    lat = [r["latency_ms"] for r in records]
    lat_sorted = sorted(lat)
    p95 = lat_sorted[max(0, int(0.95 * len(lat_sorted)) - 1)]
    summary = {
        "n": len(records),
        "accuracy": sum(1 for pr, lb in zip(preds, labels, strict=True)
                        if pr == lb) / len(records),
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
        "mean_p_true_on_pos": statistics.fmean(
            p for p, lb in zip(probs, labels, strict=True) if lb == 1),
        "mean_p_true_on_neg": statistics.fmean(
            p for p, lb in zip(probs, labels, strict=True) if lb == 0),
        "latency_ms_p50": statistics.median(lat),
        "latency_ms_p95": p95,
        "total_cost_usd": (None if any(
            r["cost"] is None for r in records)
            else sum(r["cost"] for r in records)),
        "total_input_tokens": sum(r["input_tokens"] for r in records),
        "calibration": calibration_bins(probs, labels),
    }
    # per negative-kind breakdown (v0-hard)
    kinds: dict[str, dict[str, int]] = {}
    for r, pr in zip(records, preds, strict=True):
        if r["label"] == 0:
            k = r.get("neg_kind", "unknown")
            d = kinds.setdefault(k, {"n": 0, "caught": 0})
            d["n"] += 1
            if pr == 0:
                d["caught"] += 1
    if kinds:
        summary["neg_kind_breakdown"] = {
            k: {"n": v["n"], "rejection_rate": v["caught"] / v["n"]}
            for k, v in sorted(kinds.items())
        }
    return summary


def _triple_cmd(args) -> None:
    print("Loading DocRED dev set...")
    docs, rel_info = load_dev()
    import random as _random
    rng = _random.Random(args.seed)
    sampled_docs = rng.sample(docs, min(args.n_docs, len(docs)))

    candidates: list[dict]
    if args.load_candidates:
        candidates = json.loads(Path(args.load_candidates).read_text())
        print(f"Loaded {len(candidates)} candidates from "
              f"{args.load_candidates}")
    elif args.negatives == "random":
        candidates = build_candidates(
            sampled_docs, rel_info, n_docs=len(sampled_docs),
            seed=args.seed)
    else:
        candidates = []
        for doc in sampled_docs:
            text = doc_text(doc)
            pos = []
            for label in doc.get("labels", []):
                pos.append({
                    "doc_id": doc["title"], "text": text,
                    "subject": entity_name(doc, label["h"]),
                    "relation": rel_info.get(label["r"], label["r"]),
                    "object": entity_name(doc, label["t"]),
                    "label": 1, "neg_kind": None,
                })
            negs = build_hard_negatives(doc, rel_info, seed=args.seed)
            neg_recs = [{
                "doc_id": doc["title"], "text": text,
                "subject": n["subject"], "relation": n["relation"],
                "object": n["object"], "label": 0,
                "neg_kind": n["neg_kind"],
            } for n in negs]
            # balance classes per doc: at most as many negatives
            # as positives
            if len(neg_recs) > len(pos):
                neg_recs = rng.sample(neg_recs, len(pos))
            candidates.extend(pos + neg_recs)
        rng.shuffle(candidates)
    candidates = candidates[: args.max_candidates]
    if args.dump_candidates:
        Path(args.dump_candidates).write_text(
            json.dumps(candidates, indent=2))
        print(f"Dumped {len(candidates)} candidates to "
              f"{args.dump_candidates}")
        return
    print(f"{len(candidates)} candidates from {args.n_docs} docs; "
          f"calling decision gate...")

    records: list[dict] = []
    for i, c in enumerate(candidates):
        if args.gate in ("noul", "noul_v2"):
            p_true, usage = validate_triple(
                c["text"], c["subject"], c["relation"], c["object"],
                model=args.model,
                variant="v2" if args.gate == "noul_v2" else "v1",
            )
            record = {"p_true": p_true}
        elif args.gate == "score":
            score, confidence, usage = score_gate(
                c["text"], c["subject"], c["relation"], c["object"],
                model=args.model
            )
            record = {
                "p_true": (score - 1.0) / 4.0,  # normalize 1..5 -> 0..1
                "raw_score": score,
                "score_confidence": confidence,
            }
        else:
            options = [c["relation"]]
            for r in confusion_choices(c["relation"]):
                if r not in options:
                    options.append(r)
            options = [*options[:5], "none"]
            choice, confidence, probs, usage = choice_gate(
                c["text"], c["subject"], c["object"], options,
                model=args.model
            )
            # probability mass assigned to the claimed relation
            p_true = float(probs.get(c["relation"], 0.0))
            record = {
                "p_true": p_true,
                "choice": choice,
                "choice_confidence": confidence,
            }
        record.update(
            {
                "doc_id": c["doc_id"],
                "subject": c["subject"],
                "relation": c["relation"],
                "object": c["object"],
                "label": c["label"],
                "neg_kind": c.get("neg_kind"),
                "latency_ms": usage.get("latency_ms", 0.0),
                "cost": usage.get("cost"),
                "input_tokens": usage.get("input_tokens", 0),
                "provider": usage.get("provider"),
            }
        )
        records.append(record)
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(candidates)} done")

    summary = summarize(records)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / Path(args.out).name
    out.write_text(json.dumps({"summary": summary, "records": records},
                              indent=2))
    print(json.dumps(summary, indent=2, default=str))
    print(f"\nWrote {out}")


# ------------------------------------------------------------------
# ollama — triple judgment via a local Ollama model
# ------------------------------------------------------------------

#: Local Ollama endpoint (the runner targets a local model server).
OLLAMA_URL = "http://localhost:11434/api/generate"


def ollama_judge(prompt: str, model: str,
                 timeout: int = 120) -> tuple[int, dict]:
    """Judge one prompt with a local Ollama model; returns the
    Yes/No label (1/0) parsed from the reply, plus latency."""
    t0 = time.perf_counter()
    resp = requests.post(
        OLLAMA_URL,
        json={"model": model, "prompt": prompt, "stream": False,
              "think": False,  # disable chain-of-thought: fast Yes/No
              "options": {"temperature": 0, "num_predict": 10}},
        timeout=timeout,
    )
    latency_ms = (time.perf_counter() - t0) * 1000.0
    resp.raise_for_status()
    text = (resp.json().get("response") or "").strip().lower()
    m = re.search(r"\b(yes|no)\b", text)
    label = 1 if (m and m.group(1) == "yes") else 0
    return label, {"latency_ms": latency_ms}


def load_records(path: str) -> list[dict]:
    """Load judgment records from a finetune .jsonl file
    (instruction/output pairs) or a candidates .json file;
    returns [{"prompt", "label", "doc_id"}]."""
    if path.endswith(".jsonl"):
        recs = [json.loads(line)
                for line in open(path, encoding="utf-8")
                if line.strip()]
        # finetune format: instruction/output
        out = []
        for r in recs:
            out.append({
                "prompt": r["instruction"],
                "label": 1 if r["output"].lower() == "yes" else 0,
                "doc_id": r.get("doc_id", ""),
            })
        return out
    # candidates format
    cands = json.load(open(path, encoding="utf-8"))
    out = []
    for c in cands:
        prompt = (
            "Document:\n" + c["text"] + "\n\n"
            f"Candidate triple: ({c['subject']} | {c['relation']} | "
            f"{c['object']})\n\n"
            f"Does the document support that the relation "
            f"'{c['relation']}' holds from '{c['subject']}' to "
            f"'{c['object']}' (in this direction)? "
            "Pay close attention to direction: if the document "
            "only supports the reverse direction, answer No. "
            "Answer with exactly one word: Yes or No."
        )
        out.append({"prompt": prompt, "label": c["label"],
                    "doc_id": c.get("doc_id", "")})
    return out


def _ollama_cmd(args) -> None:
    recs = load_records(args.candidates)
    if args.limit:
        recs = recs[: args.limit]
    print(f"model={args.model} n={len(recs)}", flush=True)

    correct, lat = 0, []
    results = []
    errors = 0
    for i, r in enumerate(recs):
        if i % 25 == 0:
            print(f"  {i}/{len(recs)}", flush=True)
        err = None
        try:
            pred, usage = ollama_judge(r["prompt"], args.model)
        except Exception as e:
            print(f"  !! {i}: {e}", file=sys.stderr)
            pred, usage = -1, {"latency_ms": 0.0}
            err = str(e)
        ok = pred == r["label"]
        correct += ok
        lat.append(usage["latency_ms"])
        row = {"doc_id": r["doc_id"], "label": r["label"],
               "pred": pred,
               "latency_ms": usage["latency_ms"]}
        if err is not None:
            # A failed judge call carries an explicit
            # error field and is counted in the summary — a
            # pred=-1 alone read as an ordinary wrong answer.
            row["error"] = err
            errors += 1
        results.append(row)

    lat_sorted = sorted(lat)
    p50 = lat_sorted[len(lat_sorted) // 2] if lat_sorted else 0
    acc = correct / len(recs) if recs else 0
    print(f"accuracy: {correct}/{len(recs)} = {acc:.3f} | "
          f"p50 latency: {p50:.0f}ms")

    import os
    os.makedirs(os.path.dirname(os.path.abspath(args.out)),
                exist_ok=True)
    json.dump({
        "summary": {"model": args.model, "n": len(recs),
                    "accuracy": acc, "latency_ms_p50": p50,
                    "errors": errors},
        "records": results,
    }, open(args.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"Wrote {args.out}")


# ------------------------------------------------------------------
# cascade — choice-cascade dense graph builder/evaluator
# ------------------------------------------------------------------

def build_type_signatures(docs, rel_info):
    """Map each relation to the set of (head_type, tail_type)
    pairs it is attested with in the docs' gold labels."""
    from collections import defaultdict
    sig: dict[str, set] = defaultdict(set)
    for doc in docs:
        for label in doc.get("labels", []):
            h_type = doc["vertexSet"][label["h"]][0]["type"]
            t_type = doc["vertexSet"][label["t"]][0]["type"]
            r = rel_info.get(label["r"], label["r"])
            sig[r].add((h_type, t_type))
    return sig


def gen_pairs(docs, rel_info, sig, n_docs, seed):
    """Generate same-sentence entity pairs whose endpoint types
    are compatible with at least one relation, sampled from
    ``n_docs`` docs under ``seed``."""
    import random
    from collections import defaultdict
    sampled = random.Random(seed).sample(docs, min(n_docs, len(docs)))
    pairs = []
    for doc in sampled:
        text = doc_text(doc)
        sent_ents: dict[int, list[int]] = defaultdict(list)
        for eid, mentions in enumerate(doc["vertexSet"]):
            for m in mentions:
                if eid not in sent_ents[m["sent_id"]]:
                    sent_ents[m["sent_id"]].append(eid)
        for _sent_id, ents in sent_ents.items():
            for i in ents:
                for j in ents:
                    if i == j:
                        continue
                    ht = doc["vertexSet"][i][0]["type"]
                    tt = doc["vertexSet"][j][0]["type"]
                    options = [r for r, ts in sig.items()
                               if (ht, tt) in ts]
                    if not options:
                        continue
                    pairs.append({
                        "doc_id": doc["title"],
                        "text": text,
                        "subject": entity_name(doc, i),
                        "object": entity_name(doc, j),
                        "options": sorted(options),
                    })
    return pairs


def _cascade_cmd(args) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    docs, rel_info = load_dev()
    sig = build_type_signatures(docs, rel_info)
    pairs = gen_pairs(docs, rel_info, sig, args.n_docs, args.seed)
    if args.smoke:
        pairs = pairs[:args.smoke]
    print(f"pairs: {len(pairs)}", flush=True)

    out_path = Path(args.out)
    done: dict[int, dict] = {}
    if out_path.exists():
        prev = json.loads(out_path.read_text())
        for r in prev.get("records", []):
            done[r["idx"]] = r
        print(f"resuming: {len(done)} done", flush=True)

    lock = threading.Lock()
    n_choice = [0]
    n_verify = [0]
    n_accept = [0]

    def work(idx_pair):
        idx, p = idx_pair
        if idx in done:
            return
        try:
            choice, conf, _probs, _usage = choice_gate(
                p["text"], p["subject"], p["object"],
                [*p["options"], "none"], model=JEV)
            with lock:
                n_choice[0] += 1
            rec = {"idx": idx, "doc_id": p["doc_id"],
                   "subject": p["subject"], "object": p["object"],
                   "choice": choice, "choice_conf": conf}
            if choice != "none":
                p_true, _usage2 = validate_triple(
                    p["text"], p["subject"], choice, p["object"],
                    model=JEV, variant="v2")
                with lock:
                    n_verify[0] += 1
                rec["p_true"] = p_true
                rec["accepted"] = bool(p_true >= args.threshold)
                if rec["accepted"]:
                    with lock:
                        n_accept[0] += 1
            else:
                rec["accepted"] = False
            with lock:
                done[idx] = rec
                if len(done) % 50 == 0:
                    save()
                    print(f"  {len(done)}/{len(pairs)} "
                          f"accept={n_accept[0]}", flush=True)
        except Exception as exc:
            with lock:
                done[idx] = {"idx": idx, "error": str(exc)[:200]}

    def save():
        with open(out_path, "w") as f:
            json.dump({"records": [done[i] for i in sorted(done)],
                       "n_pairs": len(pairs),
                       "threshold": args.threshold,
                       "accepted": n_accept[0]},
                      f, ensure_ascii=False)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, enumerate(pairs)))
    save()
    print(f"DONE pairs={len(pairs)} choice={n_choice[0]} "
          f"verify={n_verify[0]} accepted={n_accept[0]}")


# ------------------------------------------------------------------
# CLI entry
# ------------------------------------------------------------------

def main(argv=None) -> None:
    """Dispatch one gate/model evaluation subcommand. Argument
    surfaces and defaults are the collapsed lab runners' own."""
    ap = argparse.ArgumentParser(
        prog="python -m pipeline.gate_cli",
        description="Gate + model evaluation runners")
    sub = ap.add_subparsers(dest="gate", required=True)

    t = sub.add_parser(
        "triple", help="Decision-gate validation on DocRED candidates")
    t.add_argument("--n-docs", type=int, default=50)
    t.add_argument("--seed", type=int, default=42)
    t.add_argument("--model", default=ESCALATION_MODEL)
    t.add_argument("--max-candidates", type=int, default=200)
    t.add_argument("--negatives", choices=["random", "hard"],
                   default="random",
                   help="random: corrupt with a random relation (v0); "
                        "hard: realistic error types (v0-hard)")
    t.add_argument("--gate",
                   choices=["noul", "noul_v2", "choice", "score"],
                   default="noul",
                   help="noul: binary 'does R hold?' gate; "
                        "noul_v2: noul with direction emphasis; "
                        "choice: 'which relation holds?' gate; "
                        "score: 5-point support strength gate")
    t.add_argument("--dump-candidates", default=None,
                   help="write the candidate list to this JSON file "
                        "and exit")
    t.add_argument("--load-candidates", default=None,
                   help="load candidates from a JSON file instead "
                        "of building")
    t.add_argument("--out", default="results/v0_results.json")
    t.set_defaults(fn=_triple_cmd)

    e = sub.add_parser("er", help="ER gate evaluation (Jev)")
    e.add_argument("--candidates",
                   default="results/candidates_er1000.json")
    e.add_argument("--out", default="results/er_jev_noul.json")
    e.add_argument("--max-candidates", type=int, default=1000)
    e.add_argument("--model", default=JEV)
    e.set_defaults(fn=_er_cmd)

    c = sub.add_parser("conflict", help="Conflict gate evaluation (Jev)")
    c.add_argument("--candidates",
                   default="results/candidates_conflict1000.json")
    c.add_argument("--out", default="results/conflict_jev.json")
    c.add_argument("--max-candidates", type=int, default=1000)
    c.add_argument("--model", default=JEV)
    c.set_defaults(fn=_conflict_cmd)

    o = sub.add_parser("onto", help="Ontology gate evaluation (Jev)")
    o.add_argument("--candidates",
                   default="results/candidates_onto1000.json")
    o.add_argument("--out", default="results/onto_jev.json")
    o.add_argument("--max-candidates", type=int, default=1000)
    o.add_argument("--model", default=JEV)
    o.set_defaults(fn=_onto_cmd)

    ll = sub.add_parser("llm", help="LLM-as-judge evaluation")
    ll.add_argument("--candidates",
                    default="results/candidates_s1000.json")
    ll.add_argument("--out", default="results/llm_triple.json")
    ll.add_argument("--max-candidates", type=int, default=1000)
    ll.add_argument("--model", required=True)
    ll.add_argument("--max-tokens", type=int, default=300,
                    help="max completion tokens (raise for "
                         "reasoning models)")
    ll.set_defaults(fn=_llm_cmd)

    ol = sub.add_parser("ollama",
                        help="Triple judgment via local Ollama")
    ol.add_argument("--model", default="qwen3:4b")
    ol.add_argument("--candidates",
                    default="data/finetune/finetune_triple_dev.jsonl")
    ol.add_argument("--out", default="results/ollama_local.json")
    ol.add_argument("--limit", type=int, default=0)
    ol.set_defaults(fn=_ollama_cmd)

    ca = sub.add_parser("cascade",
                        help="Choice-cascade dense graph builder")
    ca.add_argument("--n-docs", type=int, default=200)
    ca.add_argument("--seed", type=int, default=123)
    ca.add_argument("--workers", type=int, default=8)
    ca.add_argument("--threshold", type=float, default=0.10)
    ca.add_argument("--out", default="results/cascade_dense.json")
    ca.add_argument("--smoke", type=int, default=0,
                    help="only run first N pairs (mechanics check)")
    ca.set_defaults(fn=_cascade_cmd)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
