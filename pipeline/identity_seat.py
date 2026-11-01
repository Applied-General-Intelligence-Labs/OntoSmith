"""The identity seat: a Decision-type judge over dossiers.

The frozen completion process, as calibrated (see
pipeline/reference_profile.py for the pins): deterministic
code assembles an evidence dossier per rule-4 decision
point (pipeline/identity_dossier.py); the judge seat's
decision model scores the dossier against the frozen
completion claim; the zone rule combines the score with
the deterministic chain's own disposition —

  - rules completed the point AND p >= IDENTITY_TAU_HI
    -> complete (True)
  - p <= IDENTITY_TAU_LO -> stand (False)
  - otherwise -> review band (None: the rule chain's
    outcome stands and the point is surfaced for review)

Verdicts are never taken from a bare score: the judge is
consulted only through build_verdict_map, which reads and
writes the verdict ledger (pipeline/identity_ledger.py),
so a judged point stays judged — rebuilds, stage resumes
and reruns all see the first verdict, because the decision
model's score is not bit-stable across calls.

Failure policy: a per-point call failure (the decisions
client exhausted its retries) demotes that point to the
review band and is counted in stats; a binding failure
(missing key, unbound seat) is global and propagates —
the stage fails closed rather than judging silently.
"""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor

from . import decisions
from .connections import BindingKeyError
from .identity_dossier import DOSSIER_ASSEMBLY_VERSION
from .reference_profile import (
    IDENTITY_CLAIM_VERSION,
    IDENTITY_TAU_HI,
    IDENTITY_TAU_LO,
    JUDGE_MODEL,
)

#: The frozen completion claim (wording version
#: IDENTITY_CLAIM_VERSION in the reference profile). This
#: is the claim the dossier measurement was made with;
#: the lab's contract-leg process script used a shorter
#: paraphrase, and the W4 equivalence run caught the
#: divergence (novel-leg zone agreement 51% under the
#: paraphrase, 97.5% on the leg whose claim matched).
#: The claim is unified on this text; the wording is
#: pinned verbatim by the test suite.
INSTR_B = ("Is '{b}' the complete, full name of the same entity that the "
           "mention '{a}' refers to in the text? Answer true only if the "
           "text shows that '{a}' is a shortened or partial form of "
           "exactly this entity whose full name is '{b}'. If '{b}' is "
           "itself a shortened, generic, or partial form, or the "
           "mentions refer to different entities, answer false.")


def calibration_id() -> str:
    """The identity of this calibration: judge model, claim
    version, zone thresholds, dossier assembly version. A
    verdict recorded under another calibration never
    answers for this one (it is part of the ledger key)."""
    return (f"jev-identity|{JUDGE_MODEL}|{IDENTITY_CLAIM_VERSION}"
            f"|tau{IDENTITY_TAU_HI}/{IDENTITY_TAU_LO}"
            f"|{DOSSIER_ASSEMBLY_VERSION}")


def dossier_hash(dossier: str) -> str:
    return hashlib.sha256(dossier.encode("utf-8")).hexdigest()


def score_dossier(dossier: str, fragment: str,
                  candidate: str) -> tuple[float, dict]:
    """One judge call: the dossier is the state, the frozen
    completion claim is the question. Returns (p, usage)."""
    answers, usage = decisions.decide(
        dossier,
        {"q": {"type": "noul",
               "instructions": INSTR_B.format(a=fragment,
                                              b=candidate)}},
        model=JUDGE_MODEL, seat="judge")
    return float(answers["q"]["noul"]), usage


def zone_verdict(p: float, rules_completed: bool) -> bool | None:
    """The frozen zone rule (see module docstring)."""
    if rules_completed and p >= IDENTITY_TAU_HI:
        return True
    if p <= IDENTITY_TAU_LO:
        return False
    return None


def build_verdict_map(doc_key, points, dossiers, rules_completed,
                      ledger, stats=None, max_workers=8):
    """Verdicts for one document's decision points.

    `doc_key`: pipeline.proposer.identity_doc_key of the
    document. `points`: (fragment, candidate) surface pairs.
    `dossiers`: {(fragment, candidate): dossier text} from
    pipeline.identity_dossier.assemble_dossiers.
    `rules_completed`: {(fragment, candidate): bool} — the
    deterministic chain's no-seat disposition per point.
    `ledger`: pipeline.identity_ledger.VerdictLedger.

    Returns {(fragment, candidate): True | False | None}.
    Ledger hits never re-call the judge; misses are scored
    concurrently and recorded before returning, so the map
    a caller builds on is exactly what the ledger holds.
    """
    if stats is not None:
        stats.setdefault("consulted", 0)
        stats.setdefault("ledger_hits", 0)
        stats.setdefault("judge_calls", 0)
        stats.setdefault("judge_errors", 0)
        stats.setdefault("cost_usd", 0.0)
    verdicts: dict[tuple[str, str], bool | None] = {}
    misses: list[tuple[str, str]] = []
    for frag, cand in points:
        if stats is not None:
            stats["consulted"] += 1
        hit = ledger.get(doc_key, frag, cand, dossiers[(frag, cand)])
        if hit is not None:
            verdicts[(frag, cand)] = hit["verdict"]
            if stats is not None:
                stats["ledger_hits"] += 1
        else:
            misses.append((frag, cand))

    def _score(pair):
        frag, cand = pair
        try:
            p, usage = score_dossier(dossiers[pair], frag, cand)
        except BindingKeyError:
            # A binding failure is not a per-point failure:
            # the seat is misconfigured, and quietly
            # deferring every point would hide it. Fail
            # closed. (BindingKeyError is a RuntimeError
            # subclass, so it is re-raised before the
            # catch below.)
            raise
        except RuntimeError:
            return pair, None, None
        return pair, p, usage

    if misses:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            scored = list(ex.map(_score, misses))
        for pair, p, usage in scored:
            frag, cand = pair
            if p is None:
                verdicts[pair] = None
                if stats is not None:
                    stats["judge_errors"] += 1
                continue
            verdict = zone_verdict(p, rules_completed[pair])
            ledger.put(doc_key, frag, cand, dossiers[pair], p, verdict)
            verdicts[pair] = verdict
            if stats is not None:
                stats["judge_calls"] += 1
                stats["cost_usd"] += float(usage.get("cost") or 0.0)
    return verdicts
