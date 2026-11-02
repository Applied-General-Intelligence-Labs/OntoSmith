"""Relation normalization at WRITE.

Relation strings enter the graph from heterogeneous sources:
free-text verb phrases (LLM proposer), roster-pair relation
names (pair enumeration), and profile category names
(clause-slot proposer). Without a
normalization step the graph is not addressable by a canonical
relation vocabulary and retrieval falls back to string token
overlap (§10b named this a top gap).

Deterministic layers, applied in order at WRITE; the layer that
fired is counted per batch and the per-surface report is written
to the batch dir (relation_map.json):

  vocab       the relation is a member of the corpus profile's
              declared label vocabulary (setup_config
              label_categories) — already canonical, stored verbatim.
  map         the profile/setup config carries an explicit
              relation_map {surface: canonical}; an exact hit on the
              normalized surface stores the mapped canonical form.
  normalized  a built-in deterministic canonicalization fired:
              case/whitespace folding, leading auxiliary/modal
              stripping ("shall pay" -> "pay", "is located in" ->
              "located in"), then space -> "_" joining
              ("located in" -> "located_in").
  surface     nothing fired — the surface form is stored unchanged.
              These are COUNTED and reported per batch (top
              surfaces), never silently dropped: the map grows from
              these reports, per corpus family.

A profile may also declare its relation coverage as a pack
artefact (relation_declarations, named by the profile's
relation_declarations_source key): per canonical relation, the
emission surfaces that denote it and the orientation each is
stated in. That one declaration set feeds two views:

  acceptance    canonical -> [(surface, flipped)] — the (name,
                orientation) coverage a stated triple is checked
                against (recall measurement, verification).
  write map     surface -> {canonical, flipped} — the entries the
                declaration marks as WRITE canonicalizations,
                plus its declared vocabulary (each vocabulary
                surface is its own canonical form). Fed to
                RelationNormalizer as profile_map entries, so a
                flipped declaration renames AND swaps endpoints
                at WRITE (normalize_triple).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

_AUX_PREFIX = {"shall", "will", "must", "may", "might", "is", "are",
               "was", "were", "has", "have", "had", "does", "do",
               "did", "can", "could", "should", "would", "be", "been"}


def _surface_key(relation: str) -> str:
    """Normalized lookup key for map/vocab matching."""
    return re.sub(r"\s+", " ", relation.strip().lower())


def load_relation_declarations(path) -> dict:
    """Load and validate a relation_declarations artefact.

    Fail-closed in the loader's sense: a missing file, malformed
    JSON, or a structurally invalid declaration raises ValueError
    naming the defect — a declared mapping never runs on a
    guessed table.
    """
    p = Path(path)
    try:
        raw = p.read_text()
    except OSError as e:
        raise ValueError(
            f"relation declarations artefact not found: {p}") from e
    try:
        data = json.loads(raw)
    except Exception as e:
        raise ValueError(
            f"relation declarations artefact {p} is not valid "
            f"JSON: {e}") from e
    if not isinstance(data, dict) \
            or data.get("kind") != "relation_declarations":
        raise ValueError(
            f"relation declarations artefact {p} must be an "
            "object with kind 'relation_declarations'")
    decls = data.get("declarations")
    if not isinstance(decls, list) or not decls:
        raise ValueError(
            f"relation declarations artefact {p}: 'declarations' "
            "must be a non-empty list")
    for i, d in enumerate(decls):
        if not isinstance(d, dict) \
                or not (isinstance(d.get("canonical"), str)
                        and d["canonical"]) \
                or not (isinstance(d.get("surface"), str)
                        and d["surface"]) \
                or not isinstance(d.get("flipped"), bool):
            raise ValueError(
                f"relation declarations artefact {p}: entry {i} "
                "must carry non-empty 'canonical' / 'surface' "
                "strings and a boolean 'flipped'")
    for key in ("vocabulary", "no_accepted_surface"):
        v = data.get(key) or []
        if not isinstance(v, list) or not all(
                isinstance(x, str) and x for x in v):
            raise ValueError(
                f"relation declarations artefact {p}: {key!r} "
                "must be a list of non-empty strings")
    for i, w in enumerate(data.get("write_canonicalizations")
                           or []):
        if not isinstance(w, dict) \
                or not (isinstance(w.get("surface"), str)
                        and w["surface"]) \
                or not (isinstance(w.get("canonical"), str)
                        and w["canonical"]) \
                or not isinstance(w.get("flipped"), bool):
            raise ValueError(
                f"relation declarations artefact {p}: "
                f"write_canonicalizations entry {i} must carry "
                "non-empty 'surface' / 'canonical' strings and a "
                "boolean 'flipped'")
    return data


def acceptance_mapping(declarations: dict) -> dict:
    """The acceptance view of a declaration set:
    canonical -> [(surface, flipped)] in declaration order.

    Canonicals the declaration names as having no accepted
    emission surface are present with an empty list (declared
    but not statable in this vocabulary).
    """
    out: dict[str, list] = {}
    for d in declarations.get("declarations") or []:
        out.setdefault(d["canonical"], []).append(
            (d["surface"], bool(d["flipped"])))
    for canon in declarations.get("no_accepted_surface") or []:
        out.setdefault(canon, [])
    return out


def write_map(declarations: dict) -> dict:
    """The WRITE view of a declaration set, as RelationNormalizer
    profile_map entries: surface -> {"canonical", "flipped"}.

    Two entry kinds: the declared WRITE canonicalizations (a
    stated surface is stored under its canonical relation,
    endpoints swapped when flipped), and the declared vocabulary
    (each surface is its own canonical form, so it is stored
    verbatim and counted as declared, never as an unmapped
    surface). A surface declared with two different write
    targets is a declaration defect and raises.
    """
    out: dict[str, dict] = {}

    def put(surface: str, canonical: str, flipped: bool) -> None:
        entry = {"canonical": canonical, "flipped": bool(flipped)}
        key = _surface_key(surface)
        if key in out and out[key] != entry:
            raise ValueError(
                f"relation declarations: surface {surface!r} has "
                f"conflicting write targets {out[key]} vs {entry}")
        out[key] = entry

    for surf in declarations.get("vocabulary") or []:
        put(surf, surf, False)
    for w in declarations.get("write_canonicalizations") or []:
        put(w["surface"], w["canonical"], w["flipped"])
    return out


def canonical_form(relation: str) -> str:
    """Built-in deterministic canonicalization (layer 'normalized')."""
    toks = _surface_key(relation).strip(" .").split(" ")
    toks = [t for t in toks if t]
    while len(toks) > 1 and toks[0] in _AUX_PREFIX:
        toks = toks[1:]
    return "_".join(toks)


class RelationNormalizer:
    """Stateful per-batch normalizer: counts layers + surfaces.

    profile_map values are either a canonical relation string
    (stored as-is) or a declaration object {"canonical": str,
    "flipped": bool} — the relation_declarations write view. A
    flipped entry additionally swaps the triple's endpoints;
    that orientation is applied by normalize_triple (normalize
    alone has no endpoints to swap and returns the canonical
    name).
    """

    def __init__(self, profile_map: dict | None = None,
                 vocab: set | list | None = None):
        self.profile_map = {_surface_key(k): v
                            for k, v in (profile_map or {}).items()}
        self.vocab = set(vocab or [])
        self.layer_counts: Counter = Counter()
        self.unmapped_surfaces: Counter = Counter()
        self.mapped_pairs: Counter = Counter()

    def _map_entry(self, relation: str):
        """The profile_map entry for a surface, or None."""
        return self.profile_map.get(_surface_key(relation))

    @staticmethod
    def _entry_canonical(entry) -> str:
        return entry["canonical"] if isinstance(entry, dict) \
            else entry

    def normalize(self, relation: str) -> tuple[str, str]:
        """Return (stored_relation, layer)."""
        if relation in self.vocab:
            self.layer_counts["vocab"] += 1
            return relation, "vocab"
        entry = self._map_entry(relation)
        if entry is not None:
            stored = self._entry_canonical(entry)
            self.layer_counts["map"] += 1
            self.mapped_pairs[f"{relation} -> {stored}"] += 1
            return stored, "map"
        canon = canonical_form(relation)
        if canon and canon != relation:
            self.layer_counts["normalized"] += 1
            self.mapped_pairs[f"{relation} -> {canon}"] += 1
            return canon, "normalized"
        self.layer_counts["surface"] += 1
        self.unmapped_surfaces[relation] += 1
        return relation, "surface"

    def normalize_triple(self, subject: str, relation: str,
                         object_: str) -> tuple[str, str, str, str]:
        """Return (subject, stored_relation, object, layer).

        Like normalize, plus orientation: when the matched map
        entry is a flipped declaration, the endpoints are swapped
        so the stored triple reads in the canonical direction.
        """
        entry = None if relation in self.vocab \
            else self._map_entry(relation)
        if isinstance(entry, dict) and entry.get("flipped"):
            stored = self._entry_canonical(entry)
            self.layer_counts["map"] += 1
            self.mapped_pairs[
                f"{relation} -> {stored} (flipped)"] += 1
            return object_, stored, subject, "map"
        stored, layer = self.normalize(relation)
        return subject, stored, object_, layer

    def report(self) -> dict:
        return {
            "layers": dict(self.layer_counts),
            "mapped_pairs": dict(self.mapped_pairs.most_common()),
            "unmapped_surfaces": dict(
                self.unmapped_surfaces.most_common()),
        }
