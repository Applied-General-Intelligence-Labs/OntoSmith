"""Interactive CLI reviewer for the review band.

Usage:
    python -m pipeline.review --input results/scale1k_jev-1.13_noul_v2.json

Keys:
    y = Yes (triple holds)    n = No (triple does not hold)
    a = abstain (unjudgeable from this document → separate queue)
    s = skip → revisited in a second pass
    u = undo last label        q = quit and save

Skipped cases are collected and offered again in a second pass. Cases
skipped twice are recorded as abstained (label=null) so the hardest
cases are never silently dropped.
"""
from __future__ import annotations

import argparse
import json
import os
import textwrap

# --- ANSI colors (Windows Terminal friendly) ---------------------------------
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[96m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"
BG_BLUE = "\033[44m"


def pbar(p: float, width: int = 30) -> str:
    filled = int(p * width)
    bar = "█" * filled + "░" * (width - filled)
    color = RED if p < 0.35 else (YELLOW if p < 0.65 else GREEN)
    return f"{color}{bar}{RESET} {p:.2f}"


def header(i: int, n: int, p: float, doc_id: str, pass_no: int) -> str:
    line = "─" * 64
    tag = f" (pass {pass_no})" if pass_no > 1 else ""
    return (
        f"\n{DIM}{line}{RESET}\n"
        f"{BG_BLUE}{BOLD} CASE {i}/{n}{tag} {RESET}  "
        f"Jev p_true {pbar(p)}  {DIM}{doc_id}{RESET}\n"
        f"{DIM}{line}{RESET}"
    )


def relevant_sentences(text: str, subj: str, obj: str, top_k: int = 3) -> list[str]:
    """Mechanical extraction: sentences mentioning subject/object. No reasoning."""
    import re
    sents = re.split(r"(?<=[.!?])\s+", text)
    subj_l, obj_l = subj.lower(), obj.lower()
    scored = []
    for s in sents:
        sl = s.lower()
        score = (2 if subj_l in sl else 0) + (2 if obj_l in sl else 0)
        # partial credit: any word overlap for multi-word entities
        if score == 0:
            sw = set(subj_l.split()) | set(obj_l.split())
            if sw & set(re.findall(r"\w+", sl)):
                score = 1
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:top_k]]


def show_case(rec: dict) -> None:
    subj, rel, obj = rec["subject"], rec["relation"], rec["object"]
    print(
        f"\n  Triple: {BOLD}{CYAN}{subj}{RESET}  "
        f"{DIM}|{RESET}  {BOLD}{MAGENTA}{rel}{RESET}  "
        f"{DIM}|{RESET}  {BOLD}{CYAN}{obj}{RESET}"
    )
    rel_sents = relevant_sentences(rec.get("text", ""), subj, obj)
    if rel_sents:
        print(f"\n  {BOLD}{YELLOW}Key sentences:{RESET}")
        for s in rel_sents:
            for line in textwrap.wrap(s, width=62):
                print(f"  {GREEN}▸{RESET} {line}")
    print(f"\n  {DIM}Full document:{RESET}")
    for para in textwrap.wrap(rec.get("text", ""), width=62):
        print(f"  {DIM}{para}{RESET}")
    print(f"  {DIM}Rule: definitional inference OK (city council = legislative body);")
    print(f"  if the reading is ambiguous between plausible alternatives → No.{RESET}")
    print()


def load_records(args) -> list[dict]:
    path = args.input
    if path.endswith(".jsonl"):
        recs = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        # review-band format: {doc_id, text, subject, relation, object, p_true}
        if recs and "p_true" in recs[0]:
            band = [r for r in recs if args.p_lo <= r.get("p_true", -1) <= args.p_hi]
            return band
        # finetune format has no p_true → nothing to review
        return []
    # scored-run JSON (list of dicts with p_true)
    data = json.load(open(path, encoding="utf-8"))
    recs = data["records"] if isinstance(data, dict) else data
    return [r for r in recs if args.p_lo <= r.get("p_true", -1) <= args.p_hi]


def case_key(rec: dict) -> tuple:
    """A review case's identity: the triple, not just its
    document. Two band cases from one document are distinct
    triples; keying resume/undo on doc_id alone silently
    retired a document's second case (never shown, never
    recorded) and made undo jump to the first case sharing
    the doc_id, possibly a different triple."""
    return (rec.get("doc_id", ""), rec.get("subject", ""),
            rec.get("relation", ""), rec.get("object", ""))


def load_seen(out_path: str) -> tuple[set, set, int]:
    """(case keys, legacy doc_ids, row count) from an
    existing labels file. Rows written before case keys
    existed carry only doc_id: they still suppress their
    whole document (the data cannot say which of its cases
    they covered). Rows written now carry the full key and
    suppress exactly one case."""
    keys: set = set()
    legacy_docs: set = set()
    n = 0
    if os.path.exists(out_path):
        for line in open(out_path, encoding="utf-8"):
            if not line.strip():
                continue
            row = json.loads(line)
            n += 1
            if "subject" in row:
                keys.add(case_key(row))
            else:
                legacy_docs.add(row.get("doc_id", ""))
    return keys, legacy_docs, n


def is_labeled(rec: dict, keys: set, legacy_docs: set) -> bool:
    """True when a case is already labeled: by its full
    case key, or by a legacy doc_id-only row for its
    document."""
    return case_key(rec) in keys \
        or rec.get("doc_id", "") in legacy_docs


def find_case_index(cases: list[dict], label: dict) -> int | None:
    """Index of the case a label belongs to (undo target):
    the exact triple, never just the first case sharing
    its doc_id."""
    key = case_key(label)
    for j, t in enumerate(cases):
        if case_key(t) == key:
            return j
    return None


def _label_row(r: dict, label, pass_no: int, **extra) -> dict:
    """One written label row: carries the full case key so
    resume and undo can tell same-document cases apart."""
    return {"doc_id": r["doc_id"], "subject": r.get("subject"),
            "relation": r.get("relation"),
            "object": r.get("object"), "label": label,
            "p_true": r.get("p_true"), "pass": pass_no, **extra}


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m pipeline.review")
    ap.add_argument("--input", required=True, help="scored run JSON")
    ap.add_argument("--out", default="results/review_labels.jsonl")
    ap.add_argument("--p-lo", type=float, default=0.30)
    ap.add_argument("--p-hi", type=float, default=0.50)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    recs = load_records(args)
    if args.limit:
        recs = recs[: args.limit]
    if not recs:
        print("No review-band cases found.")
        return

    # resume: skip already-labeled cases — by full case key;
    # legacy doc_id-only rows suppress their whole document
    seen_keys, seen_docs, n_seen = load_seen(args.out)
    if n_seen:
        print(f"{DIM}Resuming: {n_seen} already labeled.{RESET}")

    todo = [r for r in recs if not is_labeled(r, seen_keys, seen_docs)]
    labels: list[dict] = []
    skipped: list[dict] = []
    quit_early = False

    print(f"{BOLD}Review band: {len(todo)} cases to review{RESET}  "
          f"{DIM}(y=yes n=no s=skip u=undo q=quit){RESET}")

    def run_pass(cases: list[dict], pass_no: int) -> list[dict]:
        """Review one pass; return cases skipped in this pass."""
        nonlocal quit_early
        still_skipped: list[dict] = []
        idx = 0
        while idx < len(cases):
            if quit_early:
                # remaining cases stay for next time
                still_skipped.extend(cases[idx:])
                break
            r = cases[idx]
            print(header(idx + 1, len(cases), r.get("p_true", 0.0),
                         r.get("doc_id", ""), pass_no))
            show_case(r)
            while True:
                try:
                    ans = input(
                        f"  {BOLD}Your call?{RESET} "
                        f"{GREEN}[y]{RESET}/{RED}[n]{RESET}/"
                        f"{YELLOW}[a]bstain{RESET}/{DIM}[s]kip [u]ndo [q]uit{RESET}: "
                    ).strip().lower()
                except (EOFError, KeyboardInterrupt):
                    ans = "q"
                    print()
                if ans in ("y", "yes"):
                    labels.append(_label_row(r, 1, pass_no))
                    idx += 1
                    break
                elif ans in ("n", "no"):
                    labels.append(_label_row(r, 0, pass_no))
                    idx += 1
                    break
                elif ans in ("a", "abstain"):
                    labels.append(_label_row(r, None, pass_no,
                                             abstained=True))
                    print(f"  {YELLOW}Marked as unjudgeable → separate queue.{RESET}")
                    idx += 1
                    break
                elif ans in ("s", "skip"):
                    still_skipped.append(r)
                    idx += 1
                    break
                elif ans == "u":
                    if labels:
                        undone = labels.pop()
                        print(f"  {DIM}Undid label for {undone['doc_id']}.{RESET}")
                        j = find_case_index(cases, undone)
                        if j is not None:
                            idx = j
                    else:
                        print(f"  {DIM}Nothing to undo.{RESET}")
                    break
                elif ans in ("q", "quit"):
                    quit_early = True
                    break
                else:
                    print(f"  {DIM}Press y / n / a / s / u / q.{RESET}")
        return still_skipped

    try:
        skipped = run_pass(todo, pass_no=1)
        # second pass over skipped cases
        if skipped and not quit_early:
            print(f"\n{YELLOW}{BOLD}Second pass: {len(skipped)} skipped cases.{RESET}")
            print(f"{DIM}Skipped again → recorded as abstained (not silently dropped).{RESET}")
            skipped2 = run_pass(skipped, pass_no=2)
            for r in skipped2:
                labels.append(_label_row(r, None, 2, abstained=True))
    finally:
        if labels:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            mode = "a" if n_seen else "w"
            with open(args.out, mode, encoding="utf-8") as f:
                for lab in labels:
                    f.write(json.dumps(lab, ensure_ascii=False) + "\n")
            n_yes = sum(1 for l in labels if l["label"] == 1)
            n_no = sum(1 for l in labels if l["label"] == 0)
            n_abs = sum(1 for l in labels if l["label"] is None)
            print(f"\n{BOLD}Saved {len(labels)} → {args.out}{RESET} "
                  f"{DIM}(yes={n_yes} no={n_no} abstained={n_abs}){RESET}")
        else:
            print(f"\n{DIM}No new labels.{RESET}")


if __name__ == "__main__":
    main()
