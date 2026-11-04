"""Setup service (stage 0): corpus-family profile production.

Implements the corpus setup playbook as a runnable stage.
Runs ONCE per corpus family,
before the first batch:

  1. A dispersed sample is drawn (length strata, deterministic
     spread — never head-only).
  2. A frontier model DRAFTS profile claims from the sample, in the
     fixed claim schema (statement / kind / mechanically-checkable
     verify spec / decision-point flag). Drafting is the frontier
     model's only seat; it never judges (playbook doctrine).
  3. A deterministic scanner library re-measures every mechanically
     checkable claim over the FULL corpus. Measured values are
     authoritative; draft claims that mismatch are bounced back to
     the drafter once, then escalated to the human decision-point
     list — never silently dropped. Unmeasurable claims are graded
     [JUDGMENT] and listed for human approval.

Artifacts (profiles/<family>/):
  profile.json       claims + evidence grades + measured values
  profile.md         human view
  setup_config.json  batch-consumable settings the profile implies

Every claim carries an evidence grade: FULL (machine count over the
whole corpus), SAMPLE n=<k> (read from the dispersed sample), or
JUDGMENT (a human/model call awaiting approval). profile_hash is a
stable content hash over the profile's substantive content; batches
record it in their manifest (pipeline/batch.py write_manifest) so the
section-3 determinism premise ("same profile hash") is testable.

Usage:
    python -m pipeline.setup_service profile <corpus_dir> --family <name>
        [--sample-n 12] [--model <drafting model>] [--out DIR]
        [--no-draft]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import connections
from .reference_profile import CHAT_URL as CHAT_URL  # preset route (re-export)
from .reference_profile import EMBED_MODEL, PROVIDER_SORT

ROOT = Path(__file__).resolve().parent.parent
SERVICE_VERSION = "setup-service/1.0"

# Playbook Part B defaults (measured results carried to every family).
PLAYBOOK_DEFAULTS = {
    "triple_threshold": 0.25,
    "review_band": [0.25, 0.50],
    "er_threshold": 0.40,
    "embed_model": EMBED_MODEL,
    "chunk_window_chars": 1000,
    "chunk_min_chars": 100,
    "route_baseline": "arm_c",
    "er_auto_merge": "form_variants_only",
    "er_alias_sources": "text_attested_only",
    "batch_cost_cap_usd": 10.0,
    "provider_sort": PROVIDER_SORT,
}

_META_NAMES = {"readme", "download", "license", "changelog"}


# ---------------------------------------------------------------- corpus

@dataclass
class Doc:
    name: str          # file stem (CUAD: contract title)
    path: str
    text: str
    sha256: str

    @property
    def n_chars(self) -> int:
        return len(self.text)


def load_documents(corpus_dir: Path) -> tuple[list[Doc], list[str]]:
    """All .txt documents under corpus_dir (recursive), sorted by name.

    Meta files (README/download notes) are excluded and reported."""
    docs: list[Doc] = []
    excluded: list[str] = []
    for p in sorted(corpus_dir.rglob("*.txt")):
        stem_l = p.stem.lower()
        if stem_l.split(".")[0] in _META_NAMES or "readme" in stem_l:
            excluded.append(str(p))
            continue
        raw = p.read_bytes()
        docs.append(Doc(name=p.stem, path=str(p),
                        text=raw.decode("utf-8", errors="replace"),
                        sha256=hashlib.sha256(raw).hexdigest()))
    docs.sort(key=lambda d: d.name)
    return docs, excluded


def find_gold(corpus_dir: Path) -> dict | None:
    """SQuAD-shaped annotation JSON ({"data": [{title, paragraphs}]}).

    CUAD_v1.json is the legal family's span gold; the CSV derived
    from it is a trap (see scan_csv_flag_audit)."""
    for p in sorted(corpus_dir.rglob("*.json")):
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        if isinstance(d, dict) and isinstance(d.get("data"), list) and \
                d["data"] and "paragraphs" in d["data"][0]:
            d["_path"] = str(p)
            return d
    return None


def find_csv(corpus_dir: Path) -> Path | None:
    for p in sorted(corpus_dir.rglob("*.csv")):
        return p
    return None


def gold_categories(gold: dict) -> list[str]:
    cats = set()
    for title in gold["data"]:
        for para in title["paragraphs"]:
            for qa in para["qas"]:
                cats.add(qa["id"].rsplit("__", 1)[-1])
    return sorted(cats)


# ------------------------------------------------------- normalization

_CORP_SUFFIX = re.compile(
    r"\b(INC|LLC|LTD|CORP|CORPORATION|COMPANY|CO|LP|LLP|PLC|GMBH|SA|NV|BV)\b")


def normalize_party_name(raw: str) -> str:
    """Party identity rule from the hand-made CUAD profile section 2
    (same rule as cuad_slice_build.norm_party): name before any
    parenthetical defined term, uppercased, punctuation stripped,
    corporate suffixes removed."""
    name = raw.split("(")[0].strip().strip('"').strip()
    name = re.sub(r"[^A-Z0-9 ]", " ", name.upper())
    name = _CORP_SUFFIX.sub("", name)
    return re.sub(r"\s+", " ", name).strip()


def _name_key(s: str) -> str:
    """Loose title<->filename key: NFKD, accents stripped, casefolded,
    non-alphanumerics collapsed (catches the Leclanche E-acute
    filename-encoding artifact)."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.casefold()).strip()


# ------------------------------------------------------------ scanners
# Every scanner takes (docs, gold, csv_path, params) and returns
# {"value": <primary measured value>, "detail": {...}}.
# Scanners are deterministic and run over the FULL corpus. [FULL]

def scan_inventory(docs, gold, csv_path, params):
    by_hash: dict[str, list[str]] = {}
    for d in docs:
        by_hash.setdefault(d.sha256, []).append(d.name)
    dup_pairs = [sorted(v) for v in by_hash.values() if len(v) > 1]
    chars = sorted(d.n_chars for d in docs)
    n = len(chars)
    return {"value": len(by_hash), "detail": {
        "n_files": len(docs),
        "n_unique_content": len(by_hash),
        "duplicate_groups": dup_pairs,
        "total_chars": sum(chars),
        "mean_chars": round(sum(chars) / n) if n else 0,
        "median_chars": chars[n // 2] if n else 0,
        "min_chars": chars[0] if n else 0,
        "max_chars": chars[-1] if n else 0}}


def scan_annotation_titles(docs, gold, csv_path, params):
    if gold is None:
        return {"value": None, "detail": {"reason": "no gold annotations"}}
    titles = [t["title"] for t in gold["data"]]
    file_keys = {_name_key(d.name) for d in docs}
    title_keys = {_name_key(t) for t in titles}
    exact_files = {d.name for d in docs}
    unmatched_exact = [t for t in titles if t not in exact_files]
    unmatched_norm = [t for t in titles if _name_key(t) not in file_keys]
    orphan_files = [d.name for d in docs if _name_key(d.name)
                    not in title_keys]
    return {"value": len(unmatched_norm), "detail": {
        "n_gold_titles": len(titles),
        "n_unmatched_exact": len(unmatched_exact),
        "unmatched_exact": unmatched_exact[:5],
        "n_unmatched_normalized": len(unmatched_norm),
        "unmatched_normalized": unmatched_norm[:5],
        "n_files_without_gold_title": len(orphan_files),
        "files_without_gold_title": orphan_files[:5]}}


def scan_category_census(docs, gold, csv_path, params):
    if gold is None:
        return {"value": None, "detail": {"reason": "no gold annotations"}}
    presence: dict[str, set] = {}
    impossible: dict[str, int] = {}
    total_qas = 0
    for t in gold["data"]:
        for para in t["paragraphs"]:
            for qa in para["qas"]:
                total_qas += 1
                cat = qa["id"].rsplit("__", 1)[-1]
                if qa.get("answers"):
                    presence.setdefault(cat, set()).add(t["title"])
                if qa.get("is_impossible"):
                    impossible[cat] = impossible.get(cat, 0) + 1
    return {"value": len(presence), "detail": {
        "n_categories": len(presence),
        "categories": sorted(presence),
        "presence_docs": {c: len(s) for c, s in sorted(presence.items())},
        "total_qas": total_qas,
        "impossible_counts": dict(sorted(impossible.items()))}}


def scan_pattern_prevalence(docs, gold, csv_path, params):
    pat = re.compile(params["regex"])
    docs_with = 0
    total = 0
    for d in docs:
        hits = len(pat.findall(d.text))
        if hits:
            docs_with += 1
            total += hits
    return {"value": docs_with, "detail": {
        "pattern": params["regex"], "docs_with": docs_with,
        "docs_total": len(docs),
        "share": round(docs_with / len(docs), 4) if docs else 0,
        "total_matches": total}}


# Definition capture rule (stated, applied uniformly): a
# parenthetical of the form ("Term") or (the "Term"); the binding
# name is the text segment immediately before the "(" (trimmed at
# the last sentence/clause boundary). Legal text overwhelmingly
# uses the (the "Term") form — a quotes-only pattern misses it.
_DEFINED_TERM_RE = re.compile(
    r"\((?:the\s+)?\"([^\"]{1,80})\"\)")
_NAME_SEGMENT_RE = re.compile(r"[.!?\n;:]\s*")


def _defined_term_captures(text: str) -> list[tuple[str, str]]:
    out = []
    for m in _DEFINED_TERM_RE.finditer(text):
        term = re.sub(r"^the\s+", "", m.group(1).strip())
        before = text[max(0, m.start() - 160):m.start()]
        name = _NAME_SEGMENT_RE.split(before)[-1].strip(" ,")
        out.append((name, term))
    return out


def scan_defined_terms(docs, gold, csv_path, params):
    docs_with = 0
    total = 0
    for d in docs:
        caps = _defined_term_captures(d.text)
        if caps:
            docs_with += 1
            total += len(caps)
    return {"value": total, "detail": {
        "docs_with": docs_with, "docs_total": len(docs),
        "total_captures": total}}


def scan_defined_term_scope(docs, gold, csv_path, params):
    """Are defined terms document-scoped? For terms bound in >=2
    documents, count how many bind to >1 distinct (normalized) party
    name. A high share confirms document scoping."""
    bindings: dict[str, dict[str, str]] = {}   # term -> doc -> raw name
    for d in docs:
        for raw_name, term in _defined_term_captures(d.text):
            bindings.setdefault(term, {})[d.name] = raw_name
    multi = {t: b for t, b in bindings.items() if len(b) >= 2}
    differing = 0
    examples = []
    for term, b in sorted(multi.items()):
        norms = {normalize_party_name(v) for v in b.values()}
        if len(norms) > 1:
            differing += 1
            if len(examples) < 5:
                examples.append({"term": term, "bindings": sorted(norms)[:4]})
    return {"value": differing, "detail": {
        "terms_total": len(bindings),
        "terms_in_ge2_docs": len(multi),
        "terms_with_differing_bindings": differing,
        "share_differing": round(differing / len(multi), 4) if multi else 0,
        "examples": examples}}


def scan_csv_flag_audit(docs, gold, csv_path, params):
    """Trap probe: in CUAD's master_clauses.csv the '-Answer' cells
    are supposed to be Yes/No presence flags, not answer text."""
    import csv as csv_mod
    if csv_path is None:
        return {"value": None, "detail": {"reason": "no csv found"}}
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv_mod.DictReader(f)
        ans_cols = [c for c in (reader.fieldnames or [])
                    if c.endswith("-Answer")]
        non_empty = yes_no = 0
        other: dict[str, int] = {}
        per_col: dict[str, dict] = {}
        rows = 0
        for row in reader:
            rows += 1
            for c in ans_cols:
                v = (row.get(c) or "").strip()
                if not v:
                    continue
                pc = per_col.setdefault(c, {"non_empty": 0, "yes_no": 0})
                non_empty += 1
                pc["non_empty"] += 1
                if v in ("Yes", "No"):
                    yes_no += 1
                    pc["yes_no"] += 1
                else:
                    other[v] = other.get(v, 0) + 1
    mixed_cols = {c: pc for c, pc in per_col.items()
                  if pc["non_empty"] and pc["yes_no"] < pc["non_empty"]}
    return {"value": round(yes_no / non_empty, 4) if non_empty else 0,
            "detail": {
                "rows": rows, "answer_columns": len(ans_cols),
                "non_empty_cells": non_empty, "yes_no_cells": yes_no,
                "yes_no_share": round(yes_no / non_empty, 4) if non_empty else 0,
                "distinct_other_values": len(other),
                "other_examples": dict(sorted(
                    other.items(), key=lambda kv: -kv[1])[:8]),
                "columns_with_non_flag_values": len(mixed_cols),
                "mixed_column_examples": dict(list(mixed_cols.items())[:6])}}


def scan_party_recurrence(docs, gold, csv_path, params):
    """Normalized party names (gold 'Parties' answers) recurring
    across documents — the cross-contract join material."""
    if gold is None:
        return {"value": None, "detail": {"reason": "no gold annotations"}}
    category = params.get("category", "Parties")
    per_party: dict[str, set] = {}
    for t in gold["data"]:
        for para in t["paragraphs"]:
            for qa in para["qas"]:
                if qa["id"].rsplit("__", 1)[-1] != category:
                    continue
                for ans in qa.get("answers", []):
                    for chunk in re.split(r";\s*", ans["text"]):
                        p = normalize_party_name(chunk)
                        if len(p) >= 3:
                            per_party.setdefault(p, set()).add(t["title"])
    recurring = {p: len(s) for p, s in per_party.items() if len(s) >= 2}
    spot = {name: len(per_party.get(name, set()))
            for name in params.get("spot_check", [])}
    return {"value": len(recurring), "detail": {
        "distinct_parties": len(per_party),
        "parties_in_ge2_docs": len(recurring),
        "top_recurring": dict(sorted(
            recurring.items(), key=lambda kv: -kv[1])[:10]),
        "spot_check": spot}}


_MONTHS = ("January|February|March|April|May|June|July|August|"
           "September|October|November|December")
_DATE_RES = [
    re.compile(rf"\b(?:{_MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b"),
    re.compile(rf"\b(?:the\s+)?\d{{1,2}}(?:st|nd|rd|th)\s+day\s+of\s+"
               rf"(?:{_MONTHS})\s*,?\s*\d{{4}}\b", re.IGNORECASE),
    re.compile(rf"\b\d{{1,2}}\s+(?:{_MONTHS})\s+\d{{4}}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
]


def _date_parseable(span: str) -> bool:
    return any(r.search(span) for r in _DATE_RES)


def scan_date_parse_rate(docs, gold, csv_path, params):
    if gold is None:
        return {"value": None, "detail": {"reason": "no gold annotations"}}
    category = params.get("category", "Expiration Date")
    spans = []
    for t in gold["data"]:
        for para in t["paragraphs"]:
            for qa in para["qas"]:
                if qa["id"].rsplit("__", 1)[-1] == category:
                    spans.extend(a["text"] for a in qa.get("answers", []))
    ok = sum(1 for s in spans if _date_parseable(s))
    return {"value": round(ok / len(spans), 4) if spans else 0, "detail": {
        "category": category, "spans": len(spans), "parseable": ok,
        "parse_rate": round(ok / len(spans), 4) if spans else 0,
        "unparseable_examples": [s for s in spans
                                  if not _date_parseable(s)][:5]}}


def scan_amendment_titles(docs, gold, csv_path, params):
    hits = [d.name for d in docs if "amendment" in d.name.lower()]
    return {"value": len(hits), "detail": {
        "docs_with_amendment_in_title": len(hits), "examples": hits[:5]}}


SCANNERS = {
    "inventory": scan_inventory,
    "annotation_titles": scan_annotation_titles,
    "category_census": scan_category_census,
    "pattern_prevalence": scan_pattern_prevalence,
    "defined_terms": scan_defined_terms,
    "defined_term_scope": scan_defined_term_scope,
    "csv_flag_audit": scan_csv_flag_audit,
    "party_recurrence": scan_party_recurrence,
    "date_parse_rate": scan_date_parse_rate,
    "amendment_titles": scan_amendment_titles,
}


# ------------------------------------------------------------ sampling

def dispersed_sample(docs: list[Doc], n: int) -> list[Doc]:
    """Deterministic dispersed sample: length-quartile strata, picks
    spread evenly inside each stratum (sorted by name). Never
    head-only (playbook Part A / NER sampling rule)."""
    if n >= len(docs):
        return list(docs)
    ordered = sorted(docs, key=lambda d: (d.n_chars, d.name))
    quartiles = [ordered[i * len(ordered) // 4:(i + 1) * len(ordered) // 4]
                 for i in range(4)]
    picks: list[Doc] = []
    per = n // 4
    for q in quartiles:
        if not q:
            continue
        k = min(per, len(q))
        idxs = sorted({round(j * (len(q) - 1) / max(k - 1, 1))
                       for j in range(k)}) if k > 1 else [len(q) // 2]
        picks.extend(q[i] for i in idxs)
    # top up deterministically from the global spread
    if len(picks) < n:
        have = {d.name for d in picks}
        rest = [d for d in ordered if d.name not in have]
        step = max(len(rest) // (n - len(picks)), 1)
        picks.extend(rest[::step][:n - len(picks)])
    return sorted(picks, key=lambda d: d.name)


def _excerpt(doc: Doc, head_chars: int = 1800, ctx_chars: int = 1200) -> str:
    parts = [doc.text[:head_chars]]
    caps = _defined_term_captures(doc.text)
    if caps:
        m = _DEFINED_TERM_RE.search(doc.text)
        if m and m.start() > head_chars:
            parts.append("...[defined-term context]...")
            parts.append(doc.text[max(0, m.start() - 300):
                                  m.end() + ctx_chars])
    else:
        mid = len(doc.text) // 2
        parts.append("...[middle]...")
        parts.append(doc.text[mid:mid + ctx_chars])
    return "\n".join(parts)


# ------------------------------------------------------------ drafting

_DRAFT_SYSTEM = """You are drafting a corpus profile for a knowledge-graph
construction pipeline. You are given corpus inventory statistics and a
dispersed sample of documents from ONE corpus family. Draft candidate
profile claims following this checklist: inventory, labels &
metadata, entity terrain, identity & aliases, relation terrain,
attribution terrain, value formats, document families, text-hygiene
traps, category-to-task mapping.

Rules:
- Output ONLY JSON: {"claims": [ ... ]}.
- Each claim: {"section": <checklist section>, "statement": <one
  precise sentence>, "kind": "fact"|"rule"|"trap"|"mapping",
  "expected": <a number you directly counted in the sample, or null>,
  "verify": {"scanner": <name>, "params": {...}} or null,
  "decision_point": <true if a human must approve this>}.
- Available scanners (they re-measure over the FULL corpus):
  inventory (value = unique doc count), annotation_titles (value =
  gold titles with no matching file after normalization),
  category_census (value = number of label categories with answers),
  pattern_prevalence (params: {"regex": ...}; value = docs matching),
  defined_terms (value = parenthetical definition captures),
  defined_term_scope (value = terms in >=2 docs whose binding party
  differs across docs), csv_flag_audit (value = Yes/No share of
  derived-CSV answer cells), party_recurrence (params may include
  "spot_check": [normalized names]; value = parties in >=2 docs),
  date_parse_rate (params: {"category": ...}; value = deterministic
  parse rate of gold spans), amendment_titles (value = docs with
  'Amendment' in the title).
- Numeric "expected" ONLY when you actually counted it in the sample;
  say the scope in the statement (e.g. "in the 12-doc sample").
- Identity rules, role words that are never identities, traps, and
  category mappings are judgment material: set verify to null and
  decision_point appropriately.
- Be specific to THIS corpus. No generic advice."""

_BOUNCE_SYSTEM = """You drafted corpus-profile claims from a sample.
Deterministic scanners have now re-measured them over the FULL
corpus; some numeric expectations mismatch. For each mismatched
claim, return a revised statement that restates the claim at
full-corpus scope using the measured value (the measured value is
authoritative). Output ONLY JSON:
{"revisions": [{"id": <claim id>, "statement": <revised sentence>}]}"""


def _chat(model: str, system: str, user: str, api_key: str,
          max_tokens: int = 8000, timeout: int = 300) -> tuple[str, dict]:
    _binding = connections.seat("drafting")
    if not api_key:
        raise connections.BindingKeyError(
            f"seat 'drafting' cannot run: its key variable "
            f"{_binding.key_env} is not set")
    import requests
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
    }
    if _binding.is_openrouter_dialect:
        # OpenRouter-dialect fields: emitted only
        # when the drafting seat's dialect declares them.
        # Frontier drafting at low reasoning effort (bench_frontier
        # precedent: default effort burns the cap on hidden tokens).
        payload["reasoning"] = {"effort": "low"}
        payload["provider"] = {"sort": PROVIDER_SORT}
        payload["usage"] = {"include": True}
    r = requests.post(_binding.chat_url,
                      headers={"Authorization": f"Bearer {api_key}"},
                      json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    usage = connections.normalize_usage(data.get("usage"), _binding)
    return data["choices"][0]["message"]["content"], usage


def _parse_json_block(text: str):
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```"
                         else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def draft_claims(family: str, inventory_detail: dict, sample: list[Doc],
                 gold_note: str, csv_note: str, model: str,
                 api_key: str) -> tuple[list[dict], dict]:
    sample_txt = "\n\n".join(
        f"=== SAMPLE DOC: {d.name} ({d.n_chars} chars) ===\n{_excerpt(d)}"
        for d in sample)
    user = (f"Corpus family: {family}\n"
            f"Inventory (deterministic, full corpus): "
            f"{json.dumps(inventory_detail)}\n"
            f"Annotations: {gold_note}\nDerived files: {csv_note}\n"
            f"Dispersed sample: {len(sample)} of the documents.\n\n"
            f"{sample_txt}")
    content, usage = _chat(model, _DRAFT_SYSTEM, user, api_key)
    parsed = _parse_json_block(content)
    claims = parsed.get("claims", []) if isinstance(parsed, dict) else []
    return claims, usage


# --------------------------------------------------------- verification

def _expected_matches(expected, measured_value) -> bool | None:
    """None = nothing to compare. Counts compare exactly; rates get
    a small tolerance (a sample rate is an estimate)."""
    if expected is None or measured_value is None:
        return None
    if isinstance(expected, (int, float)) and \
            isinstance(measured_value, (int, float)):
        if float(measured_value) <= 1.0 and float(expected) <= 1.0 and \
                (float(expected) != int(expected) or
                 float(measured_value) != int(measured_value)):
            return abs(float(expected) - float(measured_value)) <= 0.03
        return float(expected) == float(measured_value)
    return str(expected).strip() == str(measured_value).strip()


def verify_claims(claims: list[dict], docs, gold, csv_path) -> list[dict]:
    """Run each claim's verify spec over the full corpus. Measured
    values are attached; nothing is dropped here."""
    for c in claims:
        spec = c.get("verify") or {}
        scanner = spec.get("scanner")
        c["measured"] = None
        c["verdict"] = "unverifiable"
        if scanner in SCANNERS:
            result = SCANNERS[scanner](docs, gold, csv_path,
                                       spec.get("params") or {})
            c["measured"] = result
            match = _expected_matches(c.get("expected"),
                                      result.get("value"))
            # Match is None when there is
            # nothing to compare (the claim carries no expected
            # value, or the scanner measured none) — the old
            # ternary read that as "confirmed", a verdict no
            # comparison ever produced. "confirmed" only when a
            # comparison actually happened and matched;
            # incomparable claims stay "unverifiable".
            if match is None:
                c["verdict"] = "unverifiable"
            elif match:
                c["verdict"] = "confirmed"
            else:
                c["verdict"] = "mismatch"
    return claims


def _add_scanner_claims(claims: list[dict], docs, gold, csv_path,
                        start_id: int) -> list[dict]:
    """The deterministic census itself enters the profile as
    first-class [FULL] claims (the profile's measured core), whether
    or not the draft mentioned them."""
    census = [
        ("inventory", "inventory", {},
         lambda r: f"Corpus holds {r['detail']['n_files']} document "
                   f"files, {r['detail']['n_unique_content']} unique "
                   f"by content, {r['detail']['total_chars']} chars "
                   f"total."),
        ("labels", "category_census", {}, None),
        ("hygiene", "pattern_prevalence", {"regex": r"\[\*\*\*\]"},
         lambda r: f"Redaction marker [***] appears in "
                   f"{r['detail']['docs_with']} of "
                   f"{r['detail']['docs_total']} documents "
                   f"({r['detail']['total_matches']} occurrences)."),
        ("hygiene", "pattern_prevalence",
         {"regex": r"\[\s\*\s\*\s\*\s\]"},
         lambda r: f"Spaced redaction marker [ * * * ] appears in "
                   f"{r['detail']['docs_with']} documents "
                   f"({r['detail']['total_matches']} occurrences)."),
        ("identity", "defined_terms", {}, None),
        ("identity", "defined_term_scope", {}, None),
        ("labels", "csv_flag_audit", {}, None),
        ("identity", "party_recurrence", {}, None),
        ("values", "date_parse_rate", {"category": "Expiration Date"},
         None),
        ("families", "amendment_titles", {}, None),
        ("labels", "annotation_titles", {}, None),
    ]
    out = []
    for section, scanner, params, stmt_fn in census:
        if scanner == "csv_flag_audit" and csv_path is None:
            continue
        if scanner in ("category_census", "party_recurrence",
                       "date_parse_rate", "annotation_titles") \
                and gold is None:
            continue
        r = SCANNERS[scanner](docs, gold, csv_path, params)
        cid = f"SC-{start_id + len(out):02d}"
        statement = stmt_fn(r) if stmt_fn else None
        out.append({
            "id": cid, "section": section, "kind": "fact",
            "statement": statement, "source": "scanner",
            "verify": {"scanner": scanner, "params": params},
            "expected": None, "measured": r, "verdict": "measured",
            "decision_point": False})
    claims.extend(out)
    return claims


# ------------------------------------------------------------ artifacts

def build_setup_config(family: str, claims: list[dict],
                       profile_hash: str) -> dict:
    cfg = {"family": family, "profile_hash": profile_hash,
           **PLAYBOOK_DEFAULTS}
    cfg["corpus_scope"] = {
        "family": family,
        "registry_namespace": family,
        "cross_corpus_links": "reviewed_edges_only"}
    cfg["c1_whitelist"] = None
    cfg["c1_whitelist_note"] = ("per-corpus: derive from the relation "
                                "census of the first batch (playbook "
                                "Part B #6); setup does not invent it")
    markers = []
    categories = None
    role_words = []
    for c in claims:
        m = c.get("measured") or {}
        det = m.get("detail") or {}
        if c.get("section") == "hygiene" \
                and (c.get("verify") or {}).get("scanner") == \
                "pattern_prevalence" and det.get("docs_with"):
            pat = det.get("pattern")
            if pat and pat not in markers:
                markers.append(pat)
        if (c.get("verify") or {}).get("scanner") == "category_census":
            categories = det.get("categories")
        if c.get("kind") == "rule" and "role" in c.get("statement", "").lower():
            role_words.append(c["statement"])
    cfg["redaction_markers_observed"] = markers
    if categories:
        cfg["label_categories"] = categories
    cfg["role_word_rules_from_profile"] = role_words
    return cfg


def _hash_content(family: str, claims: list[dict], cfg: dict) -> str:
    core = {
        "family": family,
        "claims": [{k: c.get(k) for k in
                    ("id", "section", "statement", "kind", "grade",
                     "measured")} for c in claims],
        "setup_config": {k: v for k, v in cfg.items()
                         if k != "profile_hash"},
    }
    blob = json.dumps(core, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def render_profile_md(profile: dict) -> str:
    lines = [f"# Corpus Profile — {profile['family']} "
             f"(setup service, stage 0)",
             "",
             f"**Generated:** {profile['created_at']} · "
             f"**Service:** {profile['service_version']} · "
             f"**Status:** {profile['status']} · "
             f"**Profile hash:** `{profile['profile_hash']}`",
             f"**Corpus:** `{profile['corpus_dir']}` · "
             f"**Draft model:** {profile.get('draft_model') or 'none'}",
             "",
             "Evidence grades: [FULL] deterministic count over the "
             "whole corpus · [SAMPLE n=…] read from the dispersed "
             "sample · [JUDGMENT] awaiting human approval.",
             ""]
    by_section: dict[str, list] = {}
    for c in profile["claims"]:
        by_section.setdefault(c["section"], []).append(c)
    for section, cs in by_section.items():
        lines.append(f"## {section}")
        lines.append("")
        for c in cs:
            grade = {"FULL": "[FULL]",
                     "JUDGMENT": "[JUDGMENT]"}.get(
                         c["grade"], f"[{c['grade']}]")
            lines.append(f"- {grade} {c['statement']}")
            m = c.get("measured") or {}
            if m.get("value") is not None:
                lines.append(f"  - measured: {m['value']} "
                             f"(verdict: {c['verdict']}, "
                             f"source: {c['source']})")
        lines.append("")
    lines.append("## Decision points (human approval required)")
    lines.append("")
    if profile["decision_points"]:
        for dp in profile["decision_points"]:
            lines.append(f"- {dp}")
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def _fmt_spend(usd) -> str:
    """Drafting-spend display: unmetered (None) reads as such."""
    if usd is None:
        return "not metered (provider reports no cost)"
    return f"${usd:.4f}"


def run_setup(corpus_dir: Path, family: str, out_dir: Path,
              sample_n: int = 12, model: str | None = None,
              draft: bool = True) -> dict:
    docs, excluded = load_documents(corpus_dir)
    if not docs:
        raise RuntimeError(f"no .txt documents under {corpus_dir}")
    gold = find_gold(corpus_dir)
    csv_path = find_csv(corpus_dir)
    sample = dispersed_sample(docs, sample_n)

    inv = SCANNERS["inventory"](docs, gold, csv_path, {})
    gold_note = (f"SQuAD-shaped span gold at {gold['_path']} "
                 f"({len(gold['data'])} titles, "
                 f"{len(gold_categories(gold))} label categories)"
                 if gold else "none found")
    csv_note = (f"{csv_path.name} (derived table — audit before "
                f"trusting)" if csv_path else "none found")

    claims: list[dict] = []
    spend = {"draft_usd": 0.0, "bounce_usd": 0.0, "total_usd": 0.0}
    _draft_binding = connections.seat("drafting")
    draft_key = _draft_binding.key_or_none()

    if draft:
        # The drafting seat ships unbound: the
        # model is the caller's argument or the seat's
        # declared binding, and an unbound seat refuses
        # here — before any request — with the named error.
        draft_model = model or _draft_binding.require_model()
        if not draft_key:
            raise RuntimeError(f"{_draft_binding.key_env} not set "
                               "(drafting needs it; or pass "
                               "--no-draft)")
        raw_claims, usage = draft_claims(
            family, inv["detail"], sample, gold_note, csv_note,
            draft_model, draft_key)
        spend["draft_usd"] = usage["cost"]
        for i, c in enumerate(raw_claims, 1):
            claims.append({
                "id": f"DR-{i:02d}",
                "section": c.get("section", "general"),
                "statement": c.get("statement", ""),
                "kind": c.get("kind", "fact"),
                "source": "draft",
                "verify": c.get("verify"),
                "expected": c.get("expected"),
                "decision_point": bool(c.get("decision_point")),
            })
        claims = verify_claims(claims, docs, gold, csv_path)
        # One bounce for numeric mismatches (design section 2 step 3).
        mismatched = [c for c in claims if c["verdict"] == "mismatch"]
        if mismatched:
            user = json.dumps({"mismatched": [
                {"id": c["id"], "statement": c["statement"],
                 "expected": c.get("expected"),
                 "measured": (c.get("measured") or {}).get("value"),
                 "measured_detail": (c.get("measured") or {}).get("detail")}
                for c in mismatched]}, ensure_ascii=False)
            try:
                content, usage2 = _chat(draft_model, _BOUNCE_SYSTEM,
                                        user, draft_key,
                                        max_tokens=3000)
                spend["bounce_usd"] = usage2["cost"]
                revisions = {r["id"]: r["statement"] for r in
                             _parse_json_block(content).get("revisions", [])}
                for c in mismatched:
                    if c["id"] in revisions:
                        c["statement"] = revisions[c["id"]]
                        c["verdict"] = "revised"
                    else:
                        c["verdict"] = "escalated"
                        c["decision_point"] = True
            except Exception as e:
                for c in mismatched:
                    c["verdict"] = "escalated"
                    c["decision_point"] = True
                spend["bounce_error"] = str(e)[:200]
    _total = connections.add_cost(spend["draft_usd"],
                                  spend["bounce_usd"])
    spend["total_usd"] = None if _total is None else round(_total, 4)

    claims = _add_scanner_claims(claims, docs, gold, csv_path,
                                 start_id=1)

    # Grades grade the CLAIM, not the presence of a nearby number:
    # a fact with a full-corpus measurement is [FULL]; a rule /
    # trap / mapping stays [JUDGMENT] (human-approval material)
    # even when a supporting measurement is attached as evidence.
    for c in claims:
        if c["kind"] == "fact" and c.get("measured"):
            c["grade"] = "FULL"
        elif c["source"] == "scanner":
            c["grade"] = "FULL"
        elif c["kind"] == "fact":
            c["grade"] = f"SAMPLE n={len(sample)}"
        else:
            c["grade"] = "JUDGMENT"
        if not c.get("statement"):
            m = c.get("measured") or {}
            det = m.get("detail") or {}
            c["statement"] = (f"[{c['section']}] "
                              f"{(c.get('verify') or {}).get('scanner')}: "
                              f"{json.dumps(det)[:300]}")

    decision_points = [c["statement"] for c in claims
                       if c.get("decision_point")]
    profile = {
        "family": family,
        "corpus_dir": str(corpus_dir),
        "service_version": SERVICE_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "status": ("PENDING_APPROVAL" if decision_points else "COMPLETE"),
        "draft_model": draft_model if draft else None,
        "sampling": {"n_sampled": len(sample),
                     "rule": "length-quartile strata, even spread",
                     "sample_docs": [d.name for d in sample]},
        "inventory": inv["detail"],
        "excluded_meta_files": excluded,
        "gold": gold_note, "derived_files": csv_note,
        "claims": claims,
        "decision_points": decision_points,
        "spend": spend,
    }
    cfg = build_setup_config(family, claims, "")
    profile["profile_hash"] = _hash_content(family, claims, cfg)
    cfg = build_setup_config(family, claims, profile["profile_hash"])
    profile["setup_config"] = cfg

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "profile.json").write_text(
        json.dumps(profile, indent=1, ensure_ascii=False))
    (out_dir / "setup_config.json").write_text(
        json.dumps(cfg, indent=1, ensure_ascii=False))
    (out_dir / "profile.md").write_text(render_profile_md(profile))
    return profile


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline.setup_service")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("profile", help="Run stage-0 setup for a corpus")
    p.add_argument("corpus_dir")
    p.add_argument("--family", required=True)
    p.add_argument("--sample-n", type=int, default=12)
    p.add_argument("--model", default=None,
                   help="drafting model (default: the drafting "
                        "seat's bound model — the seat ships "
                        "unbound, so bind one via "
                        "ONTOSMITH_DRAFTING_MODEL or this flag)")
    p.add_argument("--out", default=None,
                   help="artifact dir (default profiles/<family>)")
    p.add_argument("--no-draft", action="store_true",
                   help="mechanical census only, no frontier drafting")
    args = ap.parse_args(argv)

    if args.cmd == "profile":
        out = Path(args.out) if args.out else ROOT / "profiles" / args.family
        profile = run_setup(Path(args.corpus_dir), args.family, out,
                            sample_n=args.sample_n, model=args.model,
                            draft=not args.no_draft)
        print(f"profile written: {out}")
        print(f"  family={profile['family']} "
              f"status={profile['status']} "
              f"hash={profile['profile_hash']}")
        print(f"  claims={len(profile['claims'])} "
              f"decision_points={len(profile['decision_points'])}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
