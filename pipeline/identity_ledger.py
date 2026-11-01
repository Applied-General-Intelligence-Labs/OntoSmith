"""The identity verdict ledger: judged points stay judged.

One JSONL record per judged decision point, keyed by
(document key, fragment, candidate, dossier hash,
calibration id). The dossier hash and the calibration id
are both in the key on purpose: change the evidence
assembly, the claim, the thresholds or the judge model
and old verdicts simply stop matching — invalidation is
structural, never a cleanup job.

A record carries the score and the zone verdict so any
run can be audited point by point: which model, under
which calibration, saw which evidence and decided what.

Loading fails closed: a malformed record raises, exactly
like a malformed decisions file — a ledger that cannot be
read must never silently judge nothing.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .identity_seat import calibration_id, dossier_hash
from .reference_profile import JUDGE_MODEL

RECORD_VERSION = 1


def _key(doc_key: str, frag: str, cand: str,
         dossier_sha256: str, calibration: str):
    return (doc_key, frag, cand, dossier_sha256, calibration)


class VerdictLedger:
    """Append-only verdict store over one JSONL file."""

    def __init__(self, path):
        self.path = Path(path)
        self._records: dict[tuple, dict] = {}
        if self.path.exists():
            self._load()

    def _load(self):
        with open(self.path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    key = _key(rec["doc"], rec["frag"], rec["cand"],
                               rec["dossier_sha256"],
                               rec["calibration"])
                    assert rec["verdict"] in (True, False, None)
                    assert isinstance(rec["p"], (int, float))
                except Exception as e:
                    raise ValueError(
                        f"identity verdict ledger {self.path}:"
                        f"{lineno}: malformed record: {e}") from e
                self._records[key] = rec

    def get(self, doc_key: str, frag: str, cand: str,
            dossier: str) -> dict | None:
        """The recorded verdict for this exact point +
        evidence + calibration, or None if never judged."""
        key = _key(doc_key, frag, cand, dossier_hash(dossier),
                   calibration_id())
        return self._records.get(key)

    def put(self, doc_key: str, frag: str, cand: str,
            dossier: str, p: float,
            verdict: bool | None) -> dict:
        """Record a fresh verdict (append + in-memory). The
        caller scores first; this never calls the judge."""
        rec = {
            "v": RECORD_VERSION,
            "doc": doc_key,
            "frag": frag,
            "cand": cand,
            "dossier_sha256": dossier_hash(dossier),
            "calibration": calibration_id(),
            "model": JUDGE_MODEL,
            "p": p,
            "verdict": verdict,
            "judged_at": datetime.now(timezone.utc).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._records[_key(doc_key, frag, cand,
                           rec["dossier_sha256"],
                           rec["calibration"])] = rec
        return rec

    def __len__(self):
        return len(self._records)
