"""SqliteEntityRegistry — the registry as a durable source of truth.

The registry is the
system of record for entities, aliases, and ER decisions, and the
Neo4j graph is a rebuildable projection of it. Until now the only
registry implementation lived *inside* Neo4j (`Neo4jEntityRegistry`),
so dropping the projection would destroy the source of truth.

Backend choice — SQLite (one file, WAL mode): it is the store the
codebase already operates for exactly this shape of data (the stage
queue, pipeline/queue.py — transactional claims, crash-safe, stdlib
only), it gives the registry real transactions and a uniqueness
PRIMARY KEY (which is what makes minted-ID collision checks, G-12,
enforceable rather than advisory), and it keeps the deployable
SQLite/PostgreSQL seam of §4 honest: the class speaks only the
EntityRegistry interface, so the backend can be swapped without
touching the batch pipeline. Files live in the workspace
(default `<repo>/data/registry.db`), never inside Neo4j.

Identity: create() mints `ent:<uuid4hex>` IDs, collision-checked
against the PRIMARY KEY — IDs are never derived from names, so two
entities whose names slugify identically stay distinct. Candidate
lookup runs over a normalized-name index (index_normalize: case /
diacritic folding, honorific stripping) plus per-token rows, so
form variants surface as candidates. The index proposes; the ER gate
(or an exact-name shortcut) decides — the index never merges.

Namespaces: every entity carries a corpus. find_candidates /
resolve with a corpus argument return only that corpus unless the
caller explicitly passes cross_corpus=True.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from .registry import (
    Entity,
    EntityRegistry,
    index_normalize,
    mint_entity_id,
    normalize_name,
    pick_survivor,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
  entity_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  corpus TEXT NOT NULL DEFAULT '',
  aliases TEXT NOT NULL DEFAULT '[]',
  batch_id TEXT NOT NULL DEFAULT '',
  run_id TEXT NOT NULL DEFAULT '',
  doc_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS name_index (
  norm_key TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  surface TEXT NOT NULL,
  kind TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_name_index_key ON name_index(norm_key);
CREATE INDEX IF NOT EXISTS idx_name_index_entity ON name_index(entity_id);
CREATE TABLE IF NOT EXISTS er_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id TEXT NOT NULL DEFAULT '',
  run_id TEXT NOT NULL DEFAULT '',
  merged_name TEXT NOT NULL,
  surviving_id TEXT NOT NULL,
  canonical_name TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '',
  p_same REAL,
  reason TEXT NOT NULL DEFAULT '',
  decided_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS id_mapping (
  old_id TEXT PRIMARY KEY,
  new_id TEXT NOT NULL,
  migrated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SqliteEntityRegistry(EntityRegistry):
    """EntityRegistry backed by a SQLite file (source of truth)."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # RLock: interface methods compose (merge/resolve call get).
        self._lock = threading.RLock()
        self._con = sqlite3.connect(str(self.db_path),
                                    check_same_thread=False)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=NORMAL")
        self._con.executescript(_SCHEMA)
        # er_decisions.outcome: rows are 'merged'
        # (an actual merge) or 'review' (pair recorded, NOT merged).
        # Pre-existing DBs predate the column — add it guarded.
        cols = {r[1] for r in self._con.execute(
            "PRAGMA table_info(er_decisions)")}
        if "outcome" not in cols:
            self._con.execute(
                "ALTER TABLE er_decisions ADD COLUMN outcome TEXT "
                "NOT NULL DEFAULT 'merged'")
        # er_decisions.reason : names the deterministic
        # rule behind a decision when one made the call (a type-pair
        # merge prohibition). Pre-existing DBs predate the column —
        # add it guarded, exactly like outcome above.
        if "reason" not in cols:
            self._con.execute(
                "ALTER TABLE er_decisions ADD COLUMN reason TEXT "
                "NOT NULL DEFAULT ''")
        self._con.commit()

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _exists(self, entity_id: str) -> bool:
        row = self._con.execute(
            "SELECT 1 FROM entities WHERE entity_id = ?",
            (entity_id,)).fetchone()
        return row is not None

    def _row_to_entity(self, row) -> Entity:
        return Entity(entity_id=row[0], name=row[1], label=row[2] or "",
                      corpus=row[3] or "",
                      aliases=json.loads(row[4] or "[]"),
                      batch_id=row[5] or "", run_id=row[6] or "",
                      doc_id=row[7] or "")

    _COLS = ("entity_id, name, label, corpus, aliases, batch_id, "
             "run_id, doc_id")

    def _index_rows(self, ent: Entity) -> list[tuple]:
        rows = []
        surfaces = [(ent.name, "name")]
        surfaces += [(a, "alias") for a in ent.aliases]
        for surface, kind in surfaces:
            key = index_normalize(surface)
            if not key:
                continue
            rows.append((key, ent.entity_id, surface, kind))
            for tok in set(key.split(" ")):
                rows.append((tok, ent.entity_id, surface, "token"))
        return rows

    def _reindex(self, ent: Entity) -> None:
        self._con.execute("DELETE FROM name_index WHERE entity_id = ?",
                          (ent.entity_id,))
        self._con.executemany(
            "INSERT INTO name_index (norm_key, entity_id, surface, kind) "
            "VALUES (?, ?, ?, ?)", self._index_rows(ent))

    def _scoped(self, ent: Entity, corpus: str | None,
                cross_corpus: bool) -> bool:
        return (corpus is None or cross_corpus or ent.corpus == corpus)

    # ------------------------------------------------------------------
    # EntityRegistry interface
    # ------------------------------------------------------------------
    def get(self, entity_id: str) -> Entity | None:
        with self._lock:
            row = self._con.execute(
                f"SELECT {self._COLS} FROM entities "
                "WHERE entity_id = ?", (entity_id,)).fetchone()
        return self._row_to_entity(row) if row else None

    def create(self, entity: Entity) -> str:
        with self._lock:
            eid = entity.entity_id or mint_entity_id(self._exists)
            if self._exists(eid):
                return eid  # idempotent: caller-assigned id already stored
            ent = Entity(entity_id=eid, name=entity.name,
                         label=entity.label, aliases=list(entity.aliases),
                         batch_id=entity.batch_id, run_id=entity.run_id,
                         doc_id=entity.doc_id, corpus=entity.corpus)
            self._con.execute(
                "INSERT INTO entities (entity_id, name, label, corpus, "
                "aliases, batch_id, run_id, doc_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ent.entity_id, ent.name, ent.label, ent.corpus,
                 json.dumps(ent.aliases), ent.batch_id, ent.run_id,
                 ent.doc_id, _now()))
            self._reindex(ent)
            self._con.commit()
            return eid

    def _decision_exists(self, batch_id: str, merged_name: str,
                         surviving_id: str, outcome: str) -> bool:
        """True iff this (pair, batch, outcome)
        decision is already recorded. This is the idempotency key
        that keeps a WRITE-stage resume from appending duplicate
        er_decisions rows for pairs already decided in that batch
        (a replayed run re-derives the same pairs under a new
        run_id). Caller holds the lock."""
        row = self._con.execute(
            "SELECT 1 FROM er_decisions WHERE batch_id = ? "
            "AND merged_name = ? AND surviving_id = ? "
            "AND outcome = ? LIMIT 1",
            (batch_id, merged_name, surviving_id,
             outcome)).fetchone()
        return row is not None

    def record_er_decision(self, batch_id: str, run_id: str,
                           merged_name: str, surviving_id: str,
                           canonical_name: str, model: str = "",
                           p_same: float | None = None,
                           outcome: str = "review",
                           reason: str = "") -> None:
        """Append an er_decisions row. outcome='merged' rows are
        written by merge(); outcome='review' rows record a pair that
        was considered and deliberately NOT merged (G-10 review
        band), with the gate's p_same/model when a gate was
        consulted and `reason` when a deterministic rule (e.g. an
        type-pair merge prohibition) made the call.
        Idempotent per (batch_id, merged_name, surviving_id,
        outcome): a repeat call for a pair already
        decided in this batch records nothing."""
        with self._lock:
            if self._decision_exists(batch_id, merged_name,
                                     surviving_id, outcome):
                return
            self._con.execute(
                "INSERT INTO er_decisions (batch_id, run_id, "
                "merged_name, surviving_id, canonical_name, model, "
                "p_same, outcome, reason, decided_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (batch_id, run_id, merged_name, surviving_id,
                 canonical_name, model, p_same, outcome, reason,
                 _now()))
            self._con.commit()

    def merge(self, existing_id: str, new: Entity,
              model: str = "", p_same: float | None = None) -> str:
        with self._lock:
            cur = self.get(existing_id)
            if cur is None:
                # Match the legacy registry: merging into an unknown id
                # materializes the entity under that id.
                ent = Entity(entity_id=existing_id, name=new.name,
                             label=new.label, aliases=[],
                             batch_id=new.batch_id, run_id=new.run_id,
                             doc_id=new.doc_id, corpus=new.corpus)
                self._con.execute(
                    "INSERT INTO entities (entity_id, name, label, "
                    "corpus, aliases, batch_id, run_id, doc_id, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ent.entity_id, ent.name, ent.label, ent.corpus,
                     "[]", ent.batch_id, ent.run_id, ent.doc_id, _now()))
                self._reindex(ent)
                self._con.commit()
                return existing_id
            best = pick_survivor([cur.name, new.name])
            aliases = list(cur.aliases)
            # The incoming entity's own aliases are retained on
            # the survivor alongside both names — a merge unions
            # the surface sets, it never drops an attested form.
            for form in (cur.name, new.name, *new.aliases):
                if form != best and form not in aliases:
                    aliases.append(form)
            self._con.execute(
                "UPDATE entities SET name = ?, aliases = ? "
                "WHERE entity_id = ?",
                (best, json.dumps(aliases), existing_id))
            # The entity update above is idempotent
            # (alias dedupe); the decision row is written only if
            # this (pair, batch) merge is not already recorded, so
            # a WRITE-stage resume does not duplicate it.
            if not self._decision_exists(new.batch_id, new.name,
                                         existing_id, "merged"):
                self._con.execute(
                    "INSERT INTO er_decisions (batch_id, run_id, "
                    "merged_name, surviving_id, canonical_name, "
                    "model, p_same, outcome, decided_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'merged', ?)",
                    (new.batch_id, new.run_id, new.name, existing_id,
                     best, model, p_same, _now()))
            ent = Entity(entity_id=existing_id, name=best,
                         label=cur.label, corpus=cur.corpus,
                         aliases=aliases, batch_id=cur.batch_id,
                         run_id=cur.run_id, doc_id=cur.doc_id)
            self._reindex(ent)
            self._con.commit()
            return existing_id

    def find_candidates(self, name: str, label: str = "",
                        limit: int = 5, corpus: str | None = None,
                        cross_corpus: bool = False) -> list[Entity]:
        qkey = index_normalize(name)
        qtoks = set(qkey.split(" ")) if qkey else set()
        with self._lock:
            ids: list[str] = []
            seen: set[str] = set()
            keys = [qkey, *sorted(qtoks)]
            for key in keys:
                if not key:
                    continue
                for (eid,) in self._con.execute(
                        "SELECT DISTINCT entity_id FROM name_index "
                        "WHERE norm_key = ?", (key,)):
                    if eid not in seen:
                        seen.add(eid)
                        ids.append(eid)
            ents = []
            for eid in ids:
                row = self._con.execute(
                    f"SELECT {self._COLS} FROM entities "
                    "WHERE entity_id = ?", (eid,)).fetchone()
                if row:
                    ents.append(self._row_to_entity(row))
        scored: list[tuple[float, str, str, Entity]] = []
        qnorm = normalize_name(name)
        for ent in ents:
            if label and ent.label != label:
                continue
            if not self._scoped(ent, corpus, cross_corpus):
                continue
            scored.append((self._score(ent, qkey, qtoks, qnorm),
                           ent.name.lower(), ent.entity_id, ent))
        scored.sort(key=lambda t: (-t[0], t[1], t[2]))
        return [t[3] for t in scored[:limit]]

    @staticmethod
    def _score(ent: Entity, qkey: str, qtoks: set,
               qnorm: str) -> float:
        """Deterministic candidate score. Ranking only — the ER gate
        decides; the index never merges."""
        name_key = index_normalize(ent.name)
        if name_key == qkey and qkey:
            return 100.0
        if any(index_normalize(a) == qkey for a in ent.aliases):
            return 90.0
        ntoks = set(name_key.split(" ")) if name_key else set()
        if ntoks and qtoks and (ntoks <= qtoks or qtoks <= ntoks):
            return 60.0 + len(ntoks & qtoks)
        overlap = len(ntoks & qtoks)
        if overlap:
            return 30.0 + 5 * overlap
        enorm = normalize_name(ent.name)
        if qnorm and (qnorm in enorm or enorm in qnorm):
            return 20.0
        return 0.0

    def resolve(self, name: str, corpus: str | None = None) -> Entity | None:
        """Exact normalized match over names + aliases (index keys)."""
        qkey = index_normalize(name)
        if not qkey:
            return None
        with self._lock:
            rows = self._con.execute(
                "SELECT entity_id, kind FROM name_index "
                "WHERE norm_key = ? AND kind IN ('name', 'alias') "
                "ORDER BY (kind = 'name') DESC, entity_id",
                (qkey,)).fetchall()
            for eid, _kind in rows:
                ent = self.get(eid)
                if ent is not None and self._scoped(ent, corpus, False):
                    return ent
        return None

    def split_entity(self, entity_id: str,
                     partition: dict[str, list[str]],
                     batch_id: str = "", run_id: str = "",
                     model: str = "") -> dict:
        """Split surfaces off an entity into new entities.

        partition maps a new canonical name -> the surfaces that
        move to a newly minted entity under that name. The source
        entity keeps its own name surface and every unlisted
        surface. Surfaces listed but not currently on the source
        are reported, never invented; a surface that is the
        source's name cannot move. Each moved surface is recorded
        as an er_decisions row with outcome='split' (provenance:
        the re-key is auditable and reversible from the decision
        log + the pre-split snapshot). Returns a report dict.
        """
        with self._lock:
            src = self.get(entity_id)
            if src is None:
                raise KeyError(f"no such entity: {entity_id}")
            owned = list(src.aliases)
            moved: dict[str, str] = {}
            created: dict[str, list[str]] = {}
            unknown: list[str] = []
            for canonical, surfaces in partition.items():
                new_surfaces = []
                for s in surfaces:
                    if s == src.name:
                        continue  # the name surface never moves
                    if s not in owned:
                        unknown.append(s)
                        continue
                    if s in moved:
                        continue  # first group wins; never double-move
                    moved[s] = canonical
                    new_surfaces.append(s)
                if not new_surfaces:
                    continue
                new_ent = Entity(entity_id="", name=canonical,
                                 label=src.label, corpus=src.corpus,
                                 aliases=[s for s in new_surfaces
                                          if s != canonical],
                                 batch_id=batch_id, run_id=run_id,
                                 doc_id="")
                new_id = self.create(new_ent)
                created[new_id] = new_surfaces
                for s in new_surfaces:
                    self._con.execute(
                        "INSERT INTO er_decisions (batch_id, run_id, "
                        "merged_name, surviving_id, canonical_name, "
                        "model, p_same, outcome, decided_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, NULL, 'split', ?)",
                        (batch_id, run_id, s, new_id, canonical,
                         model, _now()))
            remaining = [a for a in src.aliases if a not in moved]
            self._con.execute(
                "UPDATE entities SET aliases = ? WHERE entity_id = ?",
                (json.dumps(remaining), entity_id))
            ent = Entity(entity_id=entity_id, name=src.name,
                         label=src.label, corpus=src.corpus,
                         aliases=remaining, batch_id=src.batch_id,
                         run_id=src.run_id, doc_id=src.doc_id)
            self._reindex(ent)
            self._con.commit()
            return {"source_id": entity_id, "source_name": src.name,
                    "created": created, "moved": len(moved),
                    "remaining_aliases": len(remaining),
                    "unknown_surfaces": unknown}

    def detach_surface(self, entity_id: str, surface: str,
                       batch_id: str = "", run_id: str = "",
                       model: str = "", reason: str = "") -> dict:
        """Detach one alias residence from an entity.

        The owned-elsewhere repair: a surface adjudicated to NOT
        belong on this entity, while the same surface also resides
        on another entity (its home — which this operation never
        touches and whose existence the CALLER verifies beforehand,
        as the DP-3 executor verified split eligibility). Removes
        exactly the (entity, surface) residence — every occurrence
        of the surface in the entity's alias list — and records an
        er_decisions row with outcome='detach' via
        record_er_decision, so the removal is auditable and the
        (batch, pair, outcome) idempotency rule applies: a
        repeat call for a pair already recorded under this batch
        is a no-op reporting already_detached=True. No entity is
        created or deleted; the precedent shape is the 2026-10-16
        ruling that detached `Steering Committee` from the Joint
        Commercial Committee entity.

        Refusals (ValueError; nothing is written): the surface is
        the entity's canonical name (a name surface never detaches
        — the name-resident class is a ruling-level repair, not a
        detach); the surface does not reside on the entity; or the
        detach would leave the entity with zero surfaces. An
        unknown entity_id raises KeyError (split_entity's
        convention). Returns a report dict.
        """
        with self._lock:
            src = self.get(entity_id)
            if src is None:
                raise KeyError(f"no such entity: {entity_id}")
            if surface == src.name:
                raise ValueError(
                    f"cannot detach the canonical name surface: "
                    f"{surface!r} on {entity_id}")
            if surface not in src.aliases:
                if self._decision_exists(batch_id, surface,
                                         entity_id, "detach"):
                    return {"source_id": entity_id,
                            "source_name": src.name,
                            "surface": surface, "detached": False,
                            "already_detached": True,
                            "occurrences_removed": 0,
                            "remaining_aliases": len(src.aliases)}
                raise ValueError(
                    f"surface not resident on entity: {surface!r} "
                    f"on {entity_id}")
            occurrences = src.aliases.count(surface)
            remaining = [a for a in src.aliases if a != surface]
            if not src.name and not remaining:
                raise ValueError(
                    f"detach would leave entity with zero surfaces: "
                    f"{entity_id}")
            self._con.execute(
                "UPDATE entities SET aliases = ? WHERE entity_id = ?",
                (json.dumps(remaining), entity_id))
            ent = Entity(entity_id=entity_id, name=src.name,
                         label=src.label, corpus=src.corpus,
                         aliases=remaining, batch_id=src.batch_id,
                         run_id=src.run_id, doc_id=src.doc_id)
            self._reindex(ent)
            # The decision insert commits the whole detach (entity
            # update + reindex + row) as one transaction.
            self.record_er_decision(batch_id, run_id, surface,
                                    entity_id, src.name, model=model,
                                    outcome="detach", reason=reason)
            self._con.commit()
            return {"source_id": entity_id, "source_name": src.name,
                    "surface": surface, "detached": True,
                    "already_detached": False,
                    "occurrences_removed": occurrences,
                    "remaining_aliases": len(remaining)}

    # ------------------------------------------------------------------
    # ER review-band settlement
    # ------------------------------------------------------------------
    # A held pair is an er_decisions row with outcome='review'.
    # A HUMAN settlement of that pair is a LATER row for the
    # same (batch_id, merged_name, surviving_id) whose outcome
    # is 'merged' or 'kept_separate' and whose reason carries
    # the settlement marker below — the marker is what tells a
    # person's decision apart from a WRITE-time machine row
    # for the same pair key (a pair can be machine-merged for
    # one mention and held for another). Decision rows stay
    # append-only: settling never edits or deletes the held
    # row, so the audit trail keeps both the hold and its
    # settlement.
    _SETTLEMENT_MARKER = "human settlement"

    def _er_rows(self) -> list[dict]:
        """All er_decisions rows WITH ids, in record order
        (er_decisions() keeps its id-less public shape)."""
        with self._lock:
            rows = self._con.execute(
                "SELECT id, batch_id, run_id, merged_name, "
                "surviving_id, canonical_name, model, p_same, "
                "outcome, reason, decided_at "
                "FROM er_decisions ORDER BY id").fetchall()
        return [{"id": r[0], "batch_id": r[1], "run_id": r[2],
                 "merged_name": r[3], "surviving_id": r[4],
                 "canonical_name": r[5], "model": r[6],
                 "p_same": r[7], "outcome": r[8], "reason": r[9],
                 "decided_at": r[10]} for r in rows]

    @classmethod
    def _is_human_settlement(cls, row: dict) -> bool:
        return (row["outcome"] in ("merged", "kept_separate")
                and (row["reason"] or "").startswith(
                    cls._SETTLEMENT_MARKER))

    def er_review_pairs(self, batch_id: str) -> list[dict]:
        """The batch's held ER pairs (outcome='review' rows),
        each with its human settlement when one exists:
        ``settled`` is None or {id, outcome, model, reason,
        decided_at} of the settlement row."""
        rows = self._er_rows()
        out = []
        for r in rows:
            if r["batch_id"] != batch_id or r["outcome"] != "review":
                continue
            settled = None
            for s in rows:
                if (s["id"] > r["id"]
                        and s["batch_id"] == r["batch_id"]
                        and s["merged_name"] == r["merged_name"]
                        and s["surviving_id"] == r["surviving_id"]
                        and self._is_human_settlement(s)):
                    settled = s
            out.append({**r, "settled": (
                None if settled is None else {
                    "id": settled["id"],
                    "outcome": settled["outcome"],
                    "model": settled["model"],
                    "reason": settled["reason"],
                    "decided_at": settled["decided_at"]})})
        return out

    def settled_er_decision(self, merged_name: str,
                            surviving_id: str) -> dict | None:
        """The latest human settlement row for a pair, across
        batches (a pair's identity is registry-global: the
        mention surface + the surviving entity id). None when
        no person has settled the pair."""
        found = None
        for r in self._er_rows():
            if (r["merged_name"] == merged_name
                    and r["surviving_id"] == surviving_id
                    and self._is_human_settlement(r)):
                found = r
        return found

    def merge_entities(self, surviving_id: str, merged_id: str,
                       batch_id: str = "", run_id: str = "",
                       model: str = "", p_same: float | None = None,
                       reason: str = "") -> dict:
        """Fold one EXISTING entity into another.

        The settlement-time counterpart of merge() (which folds
        a not-yet-registered mention at WRITE time): both rows
        already exist because a held pair's two sides were both
        created. The survivor keeps its id; the canonical name
        follows merge()'s pick_survivor convention; every
        surface of both entities (names + aliases, minus the
        canonical) becomes an alias of the survivor, so the
        folded name keeps resolving. The merged-away row is
        deleted, the fold is recorded as an er_decisions row
        with outcome='merged' (written directly, split_entity's
        precedent — the record of a settlement must not be
        droppable by the replay guard), and the
        id_mapping table records merged_id -> surviving_id:
        the remap the graph re-projection consumes. Triples
        are NOT touched here — the registry is the source of
        truth; the graph follows by re-projection
        (`batch er-apply`).

        Refusals: ValueError when the ids are equal; KeyError
        for an unknown id (split_entity's convention)."""
        with self._lock:
            if surviving_id == merged_id:
                raise ValueError(
                    f"cannot merge an entity into itself: "
                    f"{surviving_id}")
            survivor = self.get(surviving_id)
            if survivor is None:
                raise KeyError(f"no such entity: {surviving_id}")
            loser = self.get(merged_id)
            if loser is None:
                raise KeyError(f"no such entity: {merged_id}")
            canonical = pick_survivor([survivor.name, loser.name])
            aliases = list(survivor.aliases)
            for form in (survivor.name, loser.name,
                         *loser.aliases):
                if form != canonical and form not in aliases:
                    aliases.append(form)
            self._con.execute(
                "UPDATE entities SET name = ?, aliases = ? "
                "WHERE entity_id = ?",
                (canonical, json.dumps(aliases), surviving_id))
            self._con.execute(
                "DELETE FROM name_index WHERE entity_id = ?",
                (merged_id,))
            self._con.execute(
                "DELETE FROM entities WHERE entity_id = ?",
                (merged_id,))
            ent = Entity(entity_id=surviving_id, name=canonical,
                         label=survivor.label,
                         corpus=survivor.corpus, aliases=aliases,
                         batch_id=survivor.batch_id,
                         run_id=survivor.run_id,
                         doc_id=survivor.doc_id)
            self._reindex(ent)
            self._con.execute(
                "INSERT OR REPLACE INTO id_mapping "
                "(old_id, new_id, migrated_at) VALUES (?, ?, ?)",
                (merged_id, surviving_id, _now()))
            self._con.execute(
                "INSERT INTO er_decisions (batch_id, run_id, "
                "merged_name, surviving_id, canonical_name, "
                "model, p_same, outcome, reason, decided_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'merged', ?, ?)",
                (batch_id, run_id, loser.name, surviving_id,
                 canonical, model, p_same, reason, _now()))
            self._con.commit()
            return {"surviving_id": surviving_id,
                    "merged_id": merged_id,
                    "canonical_name": canonical,
                    "aliases": aliases}

    # ------------------------------------------------------------------
    # bookkeeping (migration, audits, tests)
    # ------------------------------------------------------------------
    def count(self) -> int:
        with self._lock:
            return int(self._con.execute(
                "SELECT count(*) FROM entities").fetchone()[0])

    def all_entities(self) -> list[Entity]:
        with self._lock:
            rows = self._con.execute(
                f"SELECT {self._COLS} FROM entities "
                "ORDER BY entity_id").fetchall()
        return [self._row_to_entity(r) for r in rows]

    def er_decisions(self) -> list[dict]:
        with self._lock:
            rows = self._con.execute(
                "SELECT batch_id, run_id, merged_name, surviving_id, "
                "canonical_name, model, p_same, outcome, reason, "
                "decided_at "
                "FROM er_decisions ORDER BY id").fetchall()
        return [{"batch_id": r[0], "run_id": r[1], "merged_name": r[2],
                 "surviving_id": r[3], "canonical_name": r[4],
                 "model": r[5], "p_same": r[6], "outcome": r[7],
                 "reason": r[8], "decided_at": r[9]}
                for r in rows]

    def set_mapping(self, old_id: str, new_id: str) -> None:
        with self._lock:
            self._con.execute(
                "INSERT OR REPLACE INTO id_mapping "
                "(old_id, new_id, migrated_at) VALUES (?, ?, ?)",
                (old_id, new_id, _now()))
            self._con.commit()

    def mapping(self) -> dict[str, str]:
        with self._lock:
            return dict(self._con.execute(
                "SELECT old_id, new_id FROM id_mapping").fetchall())

    def close(self) -> None:
        with self._lock:
            self._con.close()

    def __enter__(self) -> SqliteEntityRegistry:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
