"""The live identity seat: orchestration for the batch path.

Wraps the frozen completion process (pipeline/identity_seat.py)
into the CANDIDATES stage's guard calls. Per document:

  1. a collecting pass runs the guard on a deep copy of the
     items with a seat that only records the rule-4 decision
     points it is consulted about and defers on every one —
     so the document's full consulted-pair set is known
     before anything is judged (a dossier's pairing lists
     are computed over that whole set);
  2. verdicts for the set are built read-through the ledger
     (pipeline.identity_seat.build_verdict_map): judged
     points are answered from the ledger, new points are
     scored concurrently and recorded;
  3. the real guard pass runs on the original items with a
     seat that answers from the verdict map.

Channels that guard outside this flow (the entity sweep's
per-anchor groups) use the seat callable directly: it
answers from the document's map and judges a missed point
on demand, with the pair set known at that moment. The
deep-copy collecting pass never touches the caller's
items; the apply pass is the only mutation path, exactly
as with the frozen decisions-file seat.
"""
from __future__ import annotations

import copy

from . import proposer as _prop
from .identity_dossier import assemble_dossiers
from .identity_ledger import VerdictLedger
from .identity_seat import build_verdict_map


class LiveIdentitySeat:
    """One seat for a whole batch run: holds the ledger and
    the run's judging statistics, and tracks the document
    currently being guarded."""

    def __init__(self, ledger_path):
        self.ledger = VerdictLedger(ledger_path)
        self.stats = {"consulted": 0, "opinions": 0,
                      "judge_calls": 0, "ledger_hits": 0,
                      "judge_errors": 0, "cost_usd": 0.0}
        self._text: str | None = None
        self._doc_key: str | None = None
        self._index = None
        self._pairs: list[tuple[str, str]] = []
        self._norm_of: dict[tuple[str, str], tuple[str, str]] = {}
        self._rules: dict[tuple[str, str], bool] = {}
        self._verdicts: dict[tuple[str, str], bool | None] = {}
        self._built: set[tuple[str, str]] = set()

    # -- per-document lifecycle -----------------------------

    def begin_doc(self, text: str) -> None:
        """Reset the per-document state for `text`. Called by
        the batch loop before the document's guard calls."""
        self._text = text
        self._doc_key = _prop.identity_doc_key(text)
        self._index = _prop._identity_index(text)
        self._pairs = []
        self._norm_of = {}
        self._rules = {}
        self._verdicts = {}
        self._built = set()

    def _register(self, ep_norm, cand_norm, feats):
        """Record one consulted point (norms + surfaces)."""
        key = (ep_norm, cand_norm)
        if key not in self._norm_of:
            pair = (feats["fragment_surface"],
                    feats["candidate_surface"])
            self._norm_of[key] = pair
            self._pairs.append(pair)
        return key

    def _rules_completed(self, pair) -> bool:
        """The deterministic chain's no-seat disposition for
        one surface pair: resolve the fragment with no seat
        and compare against the candidate surface (cached
        per fragment)."""
        if pair not in self._rules:
            frag, cand = pair
            surf, _cands = _prop._resolve_endpoint(
                _prop.norm_ws(frag), self._index)
            self._rules[pair] = (surf == cand)
        return self._rules[pair]

    def _build_pending(self) -> None:
        """Judge every registered pair not yet built, over
        dossiers assembled from the document's full known
        pair set, and fold the judging stats in."""
        pending = [p for p in self._pairs if p not in self._built]
        if not pending:
            return
        dossiers = assemble_dossiers(self._text, self._pairs)
        rules = {p: self._rules_completed(p) for p in pending}
        stats: dict = {}
        verdicts = build_verdict_map(
            self._doc_key, pending, dossiers, rules,
            self.ledger, stats=stats)
        for key in ("judge_calls", "ledger_hits",
                    "judge_errors", "cost_usd"):
            self.stats[key] += stats.get(key, 0)
        for pair, verdict in verdicts.items():
            norm_key = (_prop.norm_ws(pair[0]),
                        _prop.norm_ws(pair[1]))
            self._verdicts[norm_key] = verdict
            self._built.add(pair)

    # -- guard integration ----------------------------------

    def guard(self, items, text, **guard_kwargs):
        """guard_triples with the live seat (main channels):
        collecting pass on a deep copy, verdict build, then
        the apply pass on the caller's items."""
        if self._doc_key != _prop.identity_doc_key(text):
            self.begin_doc(text)

        def collecting(ep_norm, cand_norm, feats):
            self._register(ep_norm, cand_norm, feats)
            return None

        _prop.guard_triples(copy.deepcopy(items), text,
                            identity_seat=collecting,
                            **guard_kwargs)
        self._build_pending()
        return _prop.guard_triples(items, text,
                                   identity_seat=self.seat,
                                   **guard_kwargs)

    def seat(self, ep_norm, cand_norm, feats):
        """The seat callable: answers from the document's
        verdict map, judging a missed point on demand."""
        self.stats["consulted"] += 1
        key = self._register(ep_norm, cand_norm, feats)
        if key not in self._verdicts:
            self._build_pending()
        verdict = self._verdicts.get(key)
        if verdict is not None:
            self.stats["opinions"] += 1
        return verdict
