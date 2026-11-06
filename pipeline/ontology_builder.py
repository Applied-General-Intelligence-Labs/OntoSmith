"""Ontology builder: draft -> validate ->
approve -> emit, productizing the lab's setup-service loop.

The lab proved the loop for *profile claims*: setup_service
(stage 0) samples a corpus, a drafting-tier model drafts claims,
deterministic scanners re-measure them, and unresolved items go
to a human decision-point list (the approval-brief pattern).
What the lab never had is the same loop for the *schema itself*
— the schema_front inventory (entity/relation types,
definitions, boundary examples) that a pack carries. BioRED
pack v0 was bound by a manual design pass. This module is that
missing builder:

  1. DRAFT. From a stage-0 corpus profile plus a domain brief,
     produce a draft schema document (kind
     ``ontology_schema_draft``). The draft context carries the
     profile's measured census as lists (the semantic census:
     recurring defined terms, binding names, clause headings)
     and the profile's document survey (head and tail of each
     sampled document), so the drafting seat decides about
     recurring corpus terrain explicitly instead of never
     seeing it. Drafting assistance is a
     *capability-tier role* (``drafting`` — the escalation
     tier): the caller binds it in the connection layer (the
     reference profile's preset is one such binding) and passes
     a drafter callable in. This module names no model; with no
     drafter it seeds a deterministic skeleton from the brief,
     whose gaps validation then names one by one.
     A draft after the first may be a REVISION of an
     existing pack rather than a fresh composition: the
     caller may pass the previous pack version's
     schema_front as a *seed*, and the draft then carries
     the seed with it (see ``draft_schema``). The drafting
     rules inherit the seed's types, relations and
     signature pairs; removing one, shrinking a signature,
     or rewriting an inherited definition is allowed only
     with a structured decision point recording the reason,
     and validation enforces that (stage 2). A revision may
     also carry a *hole report* — named families that
     processing reported holes for — which the revision
     must address as one bundle (new relation, signature
     extension with its definition revision, definition
     consistency fix) or explicitly leave open with a
     decision point. That bundle is the augmentation loop's
     revision path.
     The CLI names the two situations as modes, because a
     pack id is a technical coincidence, not an intention:
     a plain ``draft`` composes fresh from the brief — even
     when a published pack carries the same id — and
     ``draft --from <pack>`` extends the pack the caller
     names (a pack id resolved in the repo's packs/ tree,
     or a path to a schema document). Extension is the
     only path that inherits: the seed is the existing map,
     the profile's material (claims, semantic census,
     document survey) is the NEW documents the map must
     grow to cover, and the run prints which mode it is in.
     A material guide rides the draft command's output —
     under 10 documents of material a strong warning, at
     10–29 a recommendation note (the measured composition
     trajectory is still growing through that range and
     settles around 30 documents); the guide warns, it
     never blocks.
  2. VALIDATE. Deterministic, fail-closed checks over the
     draft: every type carries a definition and boundary
     examples; no duplicate or colliding type names; no
     overlapping types without a declared boundary (boundary
     declarations are structured decision points, and a
     revision that adds, re-signs or redefines a relation
     must declare its boundary with every relation it
     shares a signature pair with); relation
     endpoint signatures (type names or "TypeA~TypeB" pair
     strings, the shipped shape) stay inside the entity
     inventory;
     definitions live in exactly one place (the schema_front —
     the single-source rule); and the would-be pack payload
     passes the same fail-closed setup_config validation as
     any profile config (the pack contract, PACK_DESIGN §1).
     Every defect is a named finding; nothing is repaired
     silently.
  3. APPROVE. The human decision point is explicit, mirroring
     the lab's approval-record pattern: approval is a separate
     record bound to the draft's schema hash, produced only
     for a draft that validates clean. A draft cannot become
     in_force without one.
  4. EMIT. An approved draft emits a pack skeleton in the
     shipped layout — pack.json (payload pinned by sha256),
     profile/setup_config.json, and a conformance stub that
     records the emission honestly as *unmeasured*: the pack
     exists as approval-bound data; pricing it is a registered
     conformance measurement (PACK_DESIGN §2), not a builder
     claim. Emission refuses without a matching approval, and
     the emitted pack is self-checked through the staged
     loader before emit returns.

Domain content is data throughout: the schema lives in the
draft document and then in the pack payload, never in code —
this module has no domain branch. The core-pack path is the
intended use: a user with no domain pack drafts, validates,
gets approval, and emits their own pack.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import profile_config

BUILDER_VERSION = "ontology-builder/1.0"
DRAFT_KIND = "ontology_schema_draft"

#: The capability tier drafting assistance binds to
#: (domain artifacts declare tiers; the connection layer
#: binds them to models — reference_profile carries the tested
#: preset). The builder itself never names a model.
DRAFTING_TIER = "drafting"

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

_SCHEMA_DRAFT_SYSTEM = """You are drafting an ontology schema for a
knowledge-graph construction pipeline, from a corpus profile and a
domain brief. The schema is a CLOSED inventory: only the types you
declare will ever be extracted.

Rules:
- Output ONLY JSON: {"schema_front": {...},
  "merge_prohibited_type_pairs": [[typeA, typeB], ...],
  "decision_points": [...]}.
- schema_front: {"purpose": <one paragraph: what this schema is
  for>, "entity_inventory": [...], "relation_inventory": [...]}.
- Each entity entry: {"type": <name>, "role": "entity" (or a
  qualifier role for context-only types), "definition": <one
  precise paragraph>, "form_examples": [<typical surface forms>],
  "not_entities": {<category>: [<boundary examples that must NOT
  be extracted as this type>]}}.
- Each relation entry: {"type": <name>, "definition": <one
  precise paragraph>, "endpoint_signature": [<entity type
  names>], "boundary_examples": [<statements or near-miss cases
  that mark this relation's boundary>]}. You may add "direction":
  "directed"|"symmetric" and "inverse_of": <relation name> where
  the corpus supports them.
- Every type needs a definition AND boundary material: boundary
  examples are what separate this type from its neighbours.
- The context carries a semantic census (defined terms that
  recur across documents, the names they bind to, recurring
  clause/section headings) and a document survey — read the
  survey TAILS as well as the heads; closing terrain (governing
  law, venue, term, signatures) lives in tails. Treat every
  recurring census family as terrain the schema must decide
  about explicitly: cover it with a type or relation, or record
  in "decision_points" why it is out of scope. Silence about a
  recurring family is not a decision.
- Signature attestation: every endpoint-signature pair you
  declare must rest on an instance the survey or census
  material attests. A pair with no attested instance is not
  a harmless extra — it promises a structure the corpus
  does not contain; leave it out, or (when a revision adds
  it to an inherited relation) carry it on an
  extend_signature record whose "evidence" states plainly
  that it is declared without an attested instance, and why.
- Boundary declarations: before finishing, walk every pair of
  sibling types (entity-entity and relation-relation) whose
  definitions could both match the same statement. For each
  such pair, declare the boundary as a STRUCTURED decision
  point: {"action": "declare_boundary", "kind":
  "entity"|"relation", "types": [<name A>, <name B>],
  "boundary": "distinct"|"includes"|"included_in",
  "reason": <what each side includes and excludes, and the
  test that separates them>} — "includes" states the first
  type covers the second (a subtype relation),
  "included_in" the reverse, "distinct" that the two are
  mutually exclusive. Free text never settles a boundary.
  In a revision, validation rejects the draft when a
  relation you added, re-signed or redefined shares a
  signature pair with another relation and no declaration
  settles the two. A confusable pair with no declaration is
  an undeclared boundary.
- Definitions stay at the breadth the corpus attests: a
  boundary is won in "decision_points" and boundary
  examples, never by narrowing a definition. Do not add a
  membership condition (a named originator, a form, a
  provenance) that would exclude instances the corpus
  itself treats as the type, unless that condition IS the
  test separating a declared pair — an extra condition is
  over-narrowing, and every instance it excludes becomes
  a silent extraction hole.
- Do not declare a new relation that merely reverses an
  existing one (a "parent of" beside an existing "child
  of"): declare "inverse_of" on one side instead, so the
  pair is extracted once and read in both directions.
- SEED INHERITANCE. When the context carries a
  "seed_schema_front", this draft is a revision of that
  schema, not a fresh composition. The profile, claims,
  semantic census and document survey in the context
  describe the NEW documents this revision extends the
  schema to cover; the seed is the existing map. Terrain
  the seed already covers needs no new declarations —
  the revision's work is what the new material shows
  that the seed does not cover, decided against the
  seed. Inherit every seed
  entity type, relation type and endpoint-signature pair.
  Additions are free. A removal or shrinkage is allowed
  only with a structured decision point recording the
  reason — an object of the form {"action": "remove_type",
  "kind": "entity"|"relation", "type": <name>, "reason":
  <why>} for a dropped type, {"action": "remove_signature",
  "kind": "relation", "type": <relation>, "detail":
  <the dropped signature entry>, "reason": <why>} for a
  dropped signature pair, or {"action": "revise_definition"
  (or "narrow_definition"), "kind": ..., "type": <name>,
  "reason": <why>} for any rewrite of an inherited
  definition. Validation rejects an unjustified removal,
  shrinkage or silent definition rewrite. A signature pair
  ADDITION must ride with the definition revision that
  keeps the definition consistent with the widened
  signature; if the existing definition already covers
  the added pair, say so in an {"action":
  "extend_signature", "kind": "relation", "type": <name>,
  "evidence": <the corpus instance the added pair rests
  on — or an explicit statement that it is declared
  without an attested instance, and why>, "reason":
  <why the definition already covers it>} decision point
  instead. An extend_signature record with no evidence
  justifies nothing.
- HOLE REPORT. When the context carries a "hole_report",
  it names families that processing reported holes for.
  Address each family in this revision as one bundle —
  a new relation, a signature extension with its
  definition revision, or a definition consistency fix —
  or record in "decision_points" why that family stays
  open. A named family left silent is an unmade decision.
- Definitions live ONLY in the schema_front entries — nowhere
  else in your output.
- Be specific to THIS corpus and brief. No generic advice, no
  configuration, no model names, no thresholds."""


# ------------------------------------------------------------------
# small helpers

def _now() -> str:
    return datetime.now(UTC).isoformat()


def _finding(code: str, path: str, message: str) -> dict:
    """One named validation finding (fail-closed: severity is
    always "error" — any finding blocks approval/emission)."""
    return {"code": code, "severity": "error",
            "path": path, "message": message}


def _norm_name(name: str) -> str:
    """Name-collision key: casefolded, non-alphanumerics removed
    ('Gene_Product', 'gene product' and 'GeneProduct' collide)."""
    return re.sub(r"[^a-z0-9]+", "", str(name).casefold())


def _norm_text(text: str) -> str:
    """Definition-identity key: casefolded, punctuation collapsed."""
    return re.sub(r"\W+", " ", str(text).casefold()).strip()


def _nonempty_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _example_strings(values) -> list[str]:
    """Non-empty strings out of an example list (anything else in
    the list is not boundary material)."""
    if not isinstance(values, list):
        return []
    return [v for v in values if _nonempty_str(v)]


def schema_hash(draft: dict) -> str:
    """Content hash over the draft's substantive schema — the
    value an approval record binds to. Any schema edit after
    approval changes the hash and stale-dates the approval."""
    core = {
        "family": draft.get("family"),
        "pack_id": draft.get("pack_id"),
        "schema_front": draft.get("schema_front"),
        "merge_prohibited_type_pairs":
            draft.get("merge_prohibited_type_pairs") or [],
        # the theme-attestation lever is an operating
        # declaration like merge_prohibited_type_pairs — it
        # rides the payload, so it is approval-bound too:
        # toggling it after approval stale-dates the record.
        "theme_attest_chunk":
            bool(draft.get("theme_attest_chunk")),
    }
    blob = json.dumps(core, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _inventory_names(inventory) -> list[str]:
    """Type names of an inventory, in declaration order (entries
    that are not objects with a type name contribute nothing —
    validation reports them separately)."""
    names: list[str] = []
    for entry in inventory or []:
        if isinstance(entry, dict) and _nonempty_str(entry.get("type")):
            names.append(str(entry["type"]))
    return names


def _entity_boundary_material(entry: dict) -> list[str]:
    """An entity type's boundary examples: its typical forms plus
    every not-an-entity example, flattened."""
    out = _example_strings(entry.get("form_examples"))
    not_entities = entry.get("not_entities")
    if isinstance(not_entities, dict):
        for examples in not_entities.values():
            out.extend(_example_strings(examples))
    return out


def _relation_boundary_material(entry: dict) -> list[str]:
    """A relation type's boundary evidence: an explicit
    boundary_examples list, or the shipped-shape pair of a
    signature note plus an extraction intent (the form the
    lab's certified schemas carry)."""
    explicit = _example_strings(entry.get("boundary_examples"))
    if explicit:
        return explicit
    if _nonempty_str(entry.get("signature_note")) and \
            _nonempty_str(entry.get("extraction_intent")):
        return [str(entry["signature_note"])]
    return []


# ------------------------------------------------------------------
# 1. draft

def _brief_names(brief: dict, key: str) -> list[str]:
    """The seed type names of one brief field.

    Entries are type-name strings. Anything else (a dict
    carrying a definition, a number) is refused with a
    named error — silently str()-coercing it would seed
    the draft with a repr as a type name and let
    validation report nonsense findings against it
   ."""
    out = []
    for t in brief.get(key) or []:
        if not isinstance(t, str):
            raise ValueError(
                f"brief {key} entries must be type-name "
                f"strings, got {type(t).__name__}: {t!r}")
        out.append(t)
    return out


def _brief_parts(brief) -> tuple[str, list[str], list[str], str]:
    """Normalize a domain brief to (text, seed entity names,
    seed relation names, purpose). A brief is free text, a
    structured dict ({"text", "purpose", "entity_types",
    "relation_types"}), or None."""
    if brief is None:
        return "", [], [], ""
    if isinstance(brief, str):
        return brief, [], [], ""
    if isinstance(brief, dict):
        return (str(brief.get("text") or ""),
                _brief_names(brief, "entity_types"),
                _brief_names(brief, "relation_types"),
                str(brief.get("purpose") or ""))
    raise ValueError(f"brief must be text, a dict, or None "
                     f"({type(brief).__name__})")


# ------------------------------------------------------------------
# seed lineage (inheritance revisions)

#: Structured decision-point actions that justify changing an
#: inherited (seed) schema: a seed type dropped, a seed
#: signature pair dropped, an inherited definition rewritten,
#: a signature pair added on the strength of the existing
#: definition. Validation matches removals and rewrites
#: against these records; free-text points never justify.
SEED_CHANGE_ACTIONS = ("remove_type", "remove_signature",
                       "revise_definition", "narrow_definition",
                       "extend_signature")

#: The structured decision-point action that declares the
#: boundary between two sibling types, and the boundary
#: values it may state: the two are mutually exclusive
#: ("distinct"), or the first type's coverage includes the
#: second's ("includes", and its reverse "included_in").
#: Validation matches confusable pairs against these
#: records; free-text points never declare a boundary.
BOUNDARY_DECLARATION_ACTION = "declare_boundary"
BOUNDARY_KINDS = ("distinct", "includes", "included_in")


def _published_pack_seed(pack_id: str):
    """The published pack this id names, as a revision
    seed — the resolver behind the CLI's explicit
    extension mode (``--from <pack_id>``).

    Looks for packs/<pack_id>/profile/setup_config.json
    under the repo root, the layout emit writes, and
    returns (document, path); returns None when no such
    pack exists, or when its payload carries no
    schema_front to inherit (a hand-authored profile
    config is a pack, but not an extendable seed) — the
    CLI then refuses, because an explicitly named
    extension target that cannot be extended is an
    error, never a silent fresh composition. An id that
    is not a slug never resolves: the draft's own
    validation reports the id, and a malformed id must
    not become a path."""
    if not _SLUG_RE.match(pack_id or ""):
        return None
    root = Path(__file__).resolve().parent.parent
    published = (root / "packs" / pack_id / "profile"
                 / "setup_config.json")
    if not published.exists():
        return None
    document = _load_json(str(published))
    if not isinstance(document.get("schema_front"), dict):
        return None
    return document, published


def _front_hash(front: dict) -> str:
    """Content hash over one schema_front — the value a
    seeded draft records for the seed it inherited, so the
    lineage stays checkable after the seed pack moves on."""
    blob = json.dumps(front, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------
# material-size guide (warns, never blocks)

#: Material-guide thresholds, from the measured composition
#: trajectory (the builder-measure runs): the drafted
#: inventory keeps growing through the first documents of
#: corpus material and settles around 30 documents, so
#: under 10 the map is still in its fast-growing range,
#: and at 10-29 more material is still likely to deepen
#: it. The guide is a printed warning, never a gate.
MATERIAL_WARN_BELOW = 10
MATERIAL_SETTLES_AROUND = 30


def _material_doc_count(profile: dict) -> int | None:
    """How many documents of material a profile carries —
    the guide's yardstick. Unique content first (a
    duplicated file is not more material), then the raw
    file count, then the surveyed documents. None when the
    profile carries no count at all — the guide stays
    silent rather than guessing."""
    inventory = profile.get("inventory") or {}
    for key in ("n_unique_content", "n_files"):
        value = inventory.get(key)
        if isinstance(value, int) and value >= 0:
            return value
    survey = profile.get("document_survey")
    if isinstance(survey, list) and survey:
        return len(survey)
    return None


def material_guidance(profile: dict) -> str | None:
    """The material-size guide line for a draft run, or
    None when the material is at/above the settling range
    (or uncountable). In extension mode the yardstick is
    the profile being drafted from — the NEW material the
    pack is extended to cover; the seed is an existing
    map, not material."""
    n = _material_doc_count(profile)
    if n is None or n >= MATERIAL_SETTLES_AROUND:
        return None
    if n < MATERIAL_WARN_BELOW:
        return (f"material warning: this draft rests on "
                f"{n} document(s) of material — under "
                f"{MATERIAL_WARN_BELOW} documents the "
                f"schema is still in its fast-growing "
                f"range, so this map will likely be "
                f"shallow; treat it as a first sketch and "
                f"plan an extension pass once more "
                f"material is in")
    return (f"material guidance: this draft rests on {n} "
            f"document(s) of material — in the measured "
            f"composition trajectory the schema is still "
            f"growing through this range and settles "
            f"around {MATERIAL_SETTLES_AROUND} documents; "
            f"more material may still deepen the map")


def _seed_parts(seed) -> tuple[dict, dict]:
    """Normalize a revision seed to (schema_front, source).

    A seed is the previous pack version's schema: a schema
    draft document, an emitted pack payload, or a bare
    schema_front — each carries the front under
    ``schema_front`` except the bare form, which IS the
    front. Anything else is refused by name: inheriting
    from a document that carries no schema would silently
    degrade the revision to a fresh composition."""
    if not isinstance(seed, dict):
        raise ValueError(f"seed must be a schema document "
                         f"(draft, pack payload, or schema_front), "
                         f"got {type(seed).__name__}")
    if isinstance(seed.get("schema_front"), dict):
        front = seed["schema_front"]
        source = {"pack_id": seed.get("pack_id"),
                  "pack_version": seed.get("pack_version"),
                  "kind": seed.get("kind") or "pack_payload"}
    elif isinstance(seed.get("entity_inventory"), list) or \
            isinstance(seed.get("relation_inventory"), list):
        front = seed
        source = {"pack_id": None, "pack_version": None,
                  "kind": "schema_front"}
    else:
        raise ValueError("seed carries no schema_front — a "
                         "revision inherits a schema, not a "
                         "document without one")
    if not isinstance(front.get("entity_inventory"), list) or \
            not isinstance(front.get("relation_inventory"), list):
        raise ValueError("seed schema_front inventories must "
                         "be lists")
    return copy.deepcopy(front), source


def _hole_report_entries(hole_report) -> list[dict]:
    """Normalize a hole report to a list of family records.

    A hole report names the families processing reported
    holes for (the augmentation loop's input). Entries are
    objects ({"family": ..., "kind": ..., "detail": ...});
    a bare string names a family with no further detail.
    Anything else is refused by name rather than silently
    dropped — a dropped family is a hole nobody decided
    about."""
    if hole_report is None:
        return []
    if not isinstance(hole_report, list):
        raise ValueError("hole report must be a list of "
                         "family records")
    out = []
    for entry in hole_report:
        if isinstance(entry, str):
            out.append({"family": entry})
        elif isinstance(entry, dict) and \
                _nonempty_str(entry.get("family")):
            out.append(dict(entry))
        else:
            raise ValueError(
                "hole report entries must be family name "
                f"strings or objects with a 'family', got "
                f"{entry!r}")
    return out


def build_draft_context(profile: dict, brief, *, seed=None,
                        hole_report=None) -> dict:
    """The deterministic context a drafting tier works from:
    the corpus profile's inventory + claim statements and the
    brief. Claims carry their measured detail with them — the
    semantic census (defined-term lists, recurring binding
    names, recurring clause/section headings) reaches the
    drafter as lists, not as the counts the profile statements
    summarize — and the profile's document survey (head AND
    tail of each sampled document, with its heading
    candidates) rides along as its own field. A revision
    additionally carries its seed schema_front (the schema
    being revised — the drafting rules inherit it) and the
    hole report the revision must address. The context names
    its own mode (``draft_mode``): "extension" when a seed
    rides — the profile material then describes the NEW
    documents the extension must cover, against the seed as
    the existing map — and "fresh" otherwise. No model is
    consulted to build it."""
    text, seed_e, seed_r, purpose = _brief_parts(brief)
    claims = []
    census = None
    for c in profile.get("claims") or []:
        entry = {"section": c.get("section"),
                 "statement": c.get("statement"),
                 "kind": c.get("kind"),
                 "grade": c.get("grade")}
        measured = c.get("measured")
        if isinstance(measured, dict):
            entry["measured_value"] = measured.get("value")
            entry["measured_detail"] = measured.get("detail")
            if (c.get("verify") or {}).get("scanner") == \
                    "semantic_census":
                census = measured.get("detail")
        claims.append(entry)
    seed_front = None
    if seed is not None:
        seed_front, _source = _seed_parts(seed)
    return {
        "family": profile.get("family"),
        "inventory": profile.get("inventory") or {},
        "claims": claims,
        "semantic_census": census,
        "document_survey": profile.get("document_survey") or [],
        "brief": text,
        "brief_purpose": purpose,
        "seed_entity_types": seed_e,
        "seed_relation_types": seed_r,
        "draft_mode": "extension" if seed is not None
                      else "fresh",
        "seed_schema_front": seed_front,
        "hole_report": _hole_report_entries(hole_report),
    }


def make_chat_drafter(model: str, api_key: str):
    """Bind the drafting tier to a chat model — the connection
    layer's job (the caller resolves ``model`` from its
    binding; the drafting seat ships unbound, so the model is
    the user's declaration — the builder's ``--model`` option
    or ``ONTOSMITH_DRAFTING_MODEL``). Returns a drafter
    callable: context dict -> drafted dict, transported by
    setup_service's chat helper (the lab flow's own
    transport, reused)."""
    from .setup_service import _chat, _parse_json_block

    def drafter(context: dict) -> dict:
        content, _usage = _chat(
            model, _SCHEMA_DRAFT_SYSTEM,
            json.dumps(context, ensure_ascii=False), api_key)
        parsed = _parse_json_block(content)
        if not isinstance(parsed, dict):
            raise ValueError("drafting tier returned a non-object "
                             "document")
        return parsed

    return drafter


def _seed_schema_front(purpose: str, seed_e: list[str],
                       seed_r: list[str]) -> dict:
    """The deterministic no-drafter schema: brief-declared type
    names as entries with empty definitions. Every gap is left
    visible for validation to name — never filled by invention."""
    return {
        "purpose": purpose,
        "entity_inventory": [
            {"type": name, "role": "entity", "definition": "",
             "form_examples": [], "not_entities": {}}
            for name in seed_e],
        "relation_inventory": [
            {"type": name, "definition": "",
             "endpoint_signature": [], "boundary_examples": []}
            for name in seed_r],
    }


def _normalize_drafted(raw: dict) -> tuple[dict, list, list]:
    """Normalize drafting-tier output to (schema_front,
    merge_prohibited_type_pairs, decision_points). The
    schema_front is carried as authored — unknown entry fields
    are schema data the renderers ignore, and round-tripping a
    hand-authored schema must not strip them."""
    if not isinstance(raw, dict):
        raise ValueError("drafted document is not a JSON object")
    sf = raw.get("schema_front")
    if sf is None and ("entity_inventory" in raw
                       or "relation_inventory" in raw):
        sf = raw  # a bare schema_front document
    if not isinstance(sf, dict):
        raise ValueError("drafted document carries no schema_front "
                         "object")
    sf = copy.deepcopy(sf)
    if not isinstance(sf.get("entity_inventory"), list):
        sf["entity_inventory"] = []
    if not isinstance(sf.get("relation_inventory"), list):
        sf["relation_inventory"] = []
    pairs = raw.get("merge_prohibited_type_pairs") or []
    if not isinstance(pairs, list):
        raise ValueError("drafted merge_prohibited_type_pairs is "
                         "not a list")
    points = raw.get("decision_points") or []
    if not isinstance(points, list):
        raise ValueError("drafted decision_points is not a list")
    # Decision points are free text (strings) or structured
    # records (objects — the seed-change records validation
    # matches on). Both ride as authored; anything else is
    # not a decision point.
    normalized_points = []
    for p in points:
        if isinstance(p, (str, dict)):
            normalized_points.append(p)
        else:
            raise ValueError(
                "drafted decision_points entries must be "
                f"strings or objects, got {type(p).__name__}")
    return sf, pairs, normalized_points


def draft_schema(profile: dict, brief, *, family: str, pack_id: str,
                 display_name: str, drafter=None, seed=None,
                 hole_report=None) -> dict:
    """Stage 1 — produce a draft schema document.

    ``profile`` is a stage-0 corpus profile (setup_service's
    profile dict). ``drafter`` is the drafting tier bound by the
    caller: a callable taking the draft context and returning a
    drafted document. Without one, the draft is the
    deterministic brief-seeded skeleton. Either way the result
    is a working document with status DRAFT_NOT_APPROVED —
    approval is a separate, human step.

    ``seed`` makes the draft a revision: the previous pack
    version's schema (a draft, a pack payload, or a bare
    schema_front). The draft then carries the seed front with
    it (``draft["seed"]``), validation holds the revision to
    the inheritance rules (no unjustified removal, signature
    shrinkage, or silent definition rewrite), and the
    deterministic no-drafter path starts from the seed front
    itself — inheritance is what a skeleton can honestly
    offer for a revision — with any brief-named types the
    seed lacks appended as empty entries for validation to
    name. ``hole_report`` names the families the revision
    must address (see ``_hole_report_entries``); it rides
    the draft context only."""
    text, seed_e, seed_r, purpose = _brief_parts(brief)
    seed_front = seed_source = None
    if seed is not None:
        seed_front, seed_source = _seed_parts(seed)
    if drafter is not None:
        raw = drafter(build_draft_context(
            profile, brief, seed=seed, hole_report=hole_report))
        sf, pairs, points = _normalize_drafted(raw)
        source = "llm"
    elif seed_front is not None:
        sf = copy.deepcopy(seed_front)
        have_e = set(_inventory_names(sf.get("entity_inventory")))
        for name in seed_e:
            if name not in have_e:
                sf["entity_inventory"].append(
                    {"type": name, "role": "entity",
                     "definition": "", "form_examples": [],
                     "not_entities": {}})
        have_r = set(_inventory_names(sf.get("relation_inventory")))
        for name in seed_r:
            if name not in have_r:
                sf["relation_inventory"].append(
                    {"type": name, "definition": "",
                     "endpoint_signature": [],
                     "boundary_examples": []})
        pairs, points = [], []
        source = "deterministic"
    else:
        sf = _seed_schema_front(purpose, seed_e, seed_r)
        pairs, points = [], []
        source = "deterministic"
    draft = {
        "kind": DRAFT_KIND,
        "builder_version": BUILDER_VERSION,
        "created_at": _now(),
        "family": family,
        "pack_id": pack_id,
        "display_name": display_name,
        "status": "DRAFT_NOT_APPROVED",
        "provenance": {
            "profile_hash": profile.get("profile_hash"),
            "corpus_family": profile.get("family"),
            "drafting_tier": DRAFTING_TIER,
            "drafting_source": source,
            "mode": "extension" if seed_front is not None
                    else "fresh",
            "brief": text or None,
        },
        "schema_front": sf,
        "entity_types": _inventory_names(sf.get("entity_inventory")),
        "relation_types": _inventory_names(sf.get("relation_inventory")),
        "merge_prohibited_type_pairs": pairs,
        "decision_points": points,
    }
    if seed_front is not None:
        draft["seed"] = {
            "source": seed_source,
            "schema_hash": _front_hash(seed_front),
            "schema_front": seed_front,
        }
        draft["provenance"]["seed"] = {
            "pack_id": (seed_source or {}).get("pack_id"),
            "schema_hash": draft["seed"]["schema_hash"],
        }
    return draft


# ------------------------------------------------------------------
# 2. validate

def _check_inventory(inventory, kind: str, findings: list[dict]) -> dict:
    """Shared per-inventory checks. ``kind`` is "entity" or
    "relation". Returns {type name: entry} for well-formed
    entries (first occurrence wins; later duplicates are
    findings, not entries)."""
    seen: dict[str, dict] = {}
    seen_norm: dict[str, str] = {}
    for i, entry in enumerate(inventory or []):
        path = f"{kind}_inventory[{i}]"
        if not isinstance(entry, dict):
            findings.append(_finding(
                "ENTRY_NOT_OBJECT", path,
                f"{kind} inventory entry is not an object"))
            continue
        name = entry.get("type")
        if not _nonempty_str(name):
            findings.append(_finding(
                "MISSING_TYPE_NAME", path,
                f"{kind} entry has no non-empty 'type' name"))
            continue
        name = str(name)
        if name in seen:
            findings.append(_finding(
                "DUPLICATE_TYPE", path,
                f"{kind} type {name!r} is declared twice — a type "
                f"is defined in exactly one place"))
            continue
        norm = _norm_name(name)
        if norm in seen_norm:
            findings.append(_finding(
                "TYPE_NAME_COLLISION", path,
                f"{kind} type {name!r} collides with "
                f"{seen_norm[norm]!r} once case/punctuation are "
                f"normalized — two names for one type, with no "
                f"declared boundary between them"))
            continue
        seen[name] = entry
        seen_norm[norm] = name
        if not _nonempty_str(entry.get("definition")):
            findings.append(_finding(
                "MISSING_DEFINITION", f"{path}.definition",
                f"{kind} type {name!r} has no definition"))
    return seen


def _check_overlaps(entries: dict[str, dict], kind: str,
                    findings: list[dict]) -> None:
    """Two types with identical definitions are one type declared
    twice — an overlap with no declared boundary between them."""
    by_definition: dict[str, str] = {}
    for name, entry in entries.items():
        definition = entry.get("definition")
        if not _nonempty_str(definition):
            continue
        key = _norm_text(str(definition))
        if key in by_definition:
            findings.append(_finding(
                "OVERLAP_UNDECLARED", f"{kind}_inventory",
                f"{kind} types {by_definition[key]!r} and {name!r} "
                f"carry identical definitions — overlapping types "
                f"with no declared boundary; merge them or "
                f"differentiate the definitions"))
        else:
            by_definition[key] = name


def _payload_for(draft: dict, pack_version: str) -> dict:
    """The pack payload emission would write (also what
    validation checks against the pack contract). Definitions
    appear only inside the schema_front — the single source."""
    sf = draft.get("schema_front") or {}
    payload = {
        "family": draft.get("family"),
        "profile_version": 1,
        "pack_id": draft.get("pack_id"),
        "pack_version": pack_version,
        "schema_front": sf,
        "entity_types": _inventory_names(sf.get("entity_inventory")),
        "relation_types": _inventory_names(sf.get("relation_inventory")),
        "consumption_note": (
            "Emitted by pipeline.ontology_builder "
            f"({BUILDER_VERSION}) from an approved schema draft; "
            "see the pack's conformance.json for the emission "
            "record. Definitions live in the schema_front only."),
    }
    pairs = draft.get("merge_prohibited_type_pairs") or []
    if pairs:
        payload["merge_prohibited_type_pairs"] = pairs
    # the theme-attestation lever rides the same channel
    # as merge_prohibited_type_pairs — an operating declaration
    # the author states on the draft, carried into the payload
    # the pin binds. Absent/false = the legacy quote-only test,
    # so a draft that never mentions it emits byte-identically.
    if draft.get("theme_attest_chunk"):
        payload["theme_attest_chunk"] = True
    return payload


def _signature_atoms(entry: dict) -> set[str]:
    """A relation's endpoint-signature entries as a set of
    surface atoms (type names and "TypeA~TypeB" pair strings,
    whitespace-trimmed) — the unit a revision may drop or add."""
    signature = entry.get("endpoint_signature")
    if not isinstance(signature, list):
        return set()
    return {str(e).strip() for e in signature
            if _nonempty_str(str(e))}


def _seed_change_index(draft: dict,
                       findings: list[dict]) -> dict:
    """Index a draft's structured seed-change decision points
    by (action, kind, type). A point carrying an "action" key
    claims to be a seed-change record and is held to the
    record's shape (a known action, a kind, a type, a
    non-empty reason) — a malformed record justifies nothing
    and is itself a finding. Free-text points and records
    without an action are ordinary decision points and never
    justify a seed change."""
    index: dict[tuple, list[dict]] = {}
    for i, point in enumerate(draft.get("decision_points") or []):
        if not isinstance(point, dict) or "action" not in point:
            continue
        if point.get("action") == BOUNDARY_DECLARATION_ACTION:
            continue  # the boundary index owns these records
        path = f"decision_points[{i}]"
        action = point.get("action")
        kind = point.get("kind")
        type_name = point.get("type")
        reason = point.get("reason")
        # A pair addition on the strength of the existing
        # definition must rest on evidence: the corpus
        # instance the added pair is attested by, or an
        # explicit statement that it is declared without one.
        evidence_ok = action != "extend_signature" or \
            _nonempty_str(point.get("evidence"))
        if action not in SEED_CHANGE_ACTIONS or \
                kind not in ("entity", "relation") or \
                not _nonempty_str(type_name) or \
                not _nonempty_str(reason) or \
                not evidence_ok:
            findings.append(_finding(
                "BAD_SEED_CHANGE", path,
                f"seed-change record {point!r} is malformed — "
                f"it needs an action in {SEED_CHANGE_ACTIONS}, "
                f"a kind ('entity' or 'relation'), a type name "
                f"and a non-empty reason, and an "
                f"extend_signature record additionally needs a "
                f"non-empty 'evidence' (the corpus instance "
                f"the added pair rests on, or an explicit "
                f"statement that it is declared without one); "
                f"a malformed record justifies nothing"))
            continue
        index.setdefault((action, kind, str(type_name)),
                         []).append(point)
    return index


def _pair_atoms(entry: dict) -> set[str]:
    """A relation's directed pair atoms — its signature
    entries in "TypeA~TypeB" form. Two relations holding the
    same pair atom can state the same typed fact, which is
    what makes them a confusable (competing) pair; bare
    type-name signature entries name no pair and compete
    over nothing specific."""
    return {a for a in _signature_atoms(entry) if "~" in a}


def _boundary_declaration_index(
        draft: dict, entities: dict, relations: dict,
        findings: list[dict]) -> set:
    """Index a draft's structured boundary declarations as
    {(kind, (name_a, name_b))} with the names sorted. A
    record carrying the declare_boundary action is held to
    the record's shape — a kind, exactly two distinct type
    names of that kind present in the inventory, a boundary
    in BOUNDARY_KINDS and a non-empty reason; a malformed
    record settles nothing and is itself a finding. Only
    these records settle a boundary: free text can describe
    one, it cannot declare it (the same principle as
    seed-change records)."""
    declared: set = set()
    inventories = {"entity": entities, "relation": relations}
    for i, point in enumerate(draft.get("decision_points")
                              or []):
        if not isinstance(point, dict) or \
                point.get("action") != \
                BOUNDARY_DECLARATION_ACTION:
            continue
        path = f"decision_points[{i}]"
        kind = point.get("kind")
        types = point.get("types")
        boundary = point.get("boundary")
        reason = point.get("reason")
        ok = kind in inventories and \
            isinstance(types, list) and len(types) == 2 and \
            all(_nonempty_str(t) for t in types) and \
            str(types[0]) != str(types[1]) and \
            boundary in BOUNDARY_KINDS and \
            _nonempty_str(reason)
        if ok:
            inventory = inventories[kind]
            ok = all(str(t) in inventory for t in types)
        if not ok:
            findings.append(_finding(
                "BAD_BOUNDARY_DECLARATION", path,
                f"boundary declaration {point!r} is malformed "
                f"— it needs a kind ('entity' or 'relation'), "
                f"exactly two distinct type names of that "
                f"kind present in the inventory, a boundary "
                f"in {BOUNDARY_KINDS} and a non-empty reason; "
                f"a malformed declaration settles nothing"))
            continue
        declared.add((kind, tuple(sorted(str(t)
                                         for t in types))))
    return declared


def _check_boundary_declarations(
        draft: dict, relations: dict, declared: set,
        findings: list[dict]) -> None:
    """Boundary settlement for seeded revisions. A relation
    the revision adds, re-signs (a signature pair gained)
    or redefines can compete — over every directed pair
    atom it now shares with another relation — only under
    a declared boundary: a declare_boundary decision point
    settling the pair, or an inverse_of link between the
    two (the pair is then one relation read in two
    directions, not two competitors). An unsettled pair is
    a finding naming both relations and the shared pairs.

    The duty arises from change, exactly as the seed
    lineage duties do: a relation the revision leaves
    untouched owes no new declarations. (Consequence,
    stated plainly: a confusable pair between two
    untouched relations is not gated here — it is settled
    the first time either side is next changed, and the
    schema audit remains the net for pairs no revision
    has touched.)"""
    seed = draft.get("seed")
    if not isinstance(seed, dict):
        return
    front = seed.get("schema_front")
    if not isinstance(front, dict):
        return  # the lineage check already reports BAD_SEED
    seed_relations = {str(r["type"]): r
                      for r in front.get("relation_inventory")
                      or []
                      if isinstance(r, dict)
                      and _nonempty_str(r.get("type"))}
    inverse_links = set()
    for name, entry in relations.items():
        inverse = entry.get("inverse_of")
        if _nonempty_str(inverse):
            inverse_links.add(tuple(sorted(
                (name, str(inverse)))))
    reported: set = set()
    for name, entry in relations.items():
        seed_entry = seed_relations.get(name)
        if seed_entry is None:
            touched = True
        else:
            touched = bool(
                _pair_atoms(entry) - _pair_atoms(seed_entry)) \
                or _norm_text(str(entry.get("definition")
                                  or "")) != \
                _norm_text(str(seed_entry.get("definition")
                               or ""))
        if not touched:
            continue
        atoms = _pair_atoms(entry)
        for other, other_entry in relations.items():
            if other == name:
                continue
            shared = atoms & _pair_atoms(other_entry)
            if not shared:
                continue
            key = tuple(sorted((name, other)))
            if key in reported or \
                    ("relation", key) in declared or \
                    key in inverse_links:
                continue
            reported.add(key)
            findings.append(_finding(
                "BOUNDARY_UNDECLARED", "relation_inventory",
                f"relations {name!r} and {other!r} share "
                f"signature pair(s) {sorted(shared)} and can "
                f"state the same fact; this revision adds, "
                f"re-signs or redefines {name!r}, and no "
                f"declare_boundary decision point settles "
                f"the pair (distinct, or one including the "
                f"other) and neither declares the other its "
                f"inverse — an undeclared boundary travels "
                f"with the schema"))


def _check_seed_lineage(draft: dict, entities: dict,
                        relations: dict,
                        findings: list[dict]) -> None:
    """Inheritance discipline for seeded drafts: the revision
    may add freely, but what the seed declared cannot quietly
    disappear or shrink. A dropped seed type, a dropped
    signature pair, or a rewritten inherited definition each
    need the matching structured decision point (see
    SEED_CHANGE_ACTIONS); a signature pair ADDITION needs the
    definition revision that keeps the definition consistent
    with the widened signature, or an extend_signature record
    stating the definition already covers it. Boundary
    material (examples) is drafting surface, not contract —
    revisions may rework it unrecorded."""
    seed = draft.get("seed")
    if not isinstance(seed, dict):
        return
    front = seed.get("schema_front")
    if not isinstance(front, dict):
        findings.append(_finding(
            "BAD_SEED", "seed",
            "the draft declares a seed but carries no seed "
            "schema_front — lineage cannot be checked"))
        return
    index = _seed_change_index(draft, findings)

    def justified(actions, kind, name, detail=None) -> bool:
        for action in actions:
            for point in index.get((action, kind, name), []):
                if detail is None:
                    return True
                recorded = point.get("detail")
                if recorded == detail:
                    return True
                if isinstance(recorded, list) and \
                        detail in [str(d) for d in recorded]:
                    return True
        return False

    seed_entities = {str(e["type"]): e
                     for e in front.get("entity_inventory") or []
                     if isinstance(e, dict)
                     and _nonempty_str(e.get("type"))}
    seed_relations = {str(r["type"]): r
                      for r in front.get("relation_inventory") or []
                      if isinstance(r, dict)
                      and _nonempty_str(r.get("type"))}
    for kind, seed_map, new_map in (
            ("entity", seed_entities, entities),
            ("relation", seed_relations, relations)):
        for name, seed_entry in seed_map.items():
            new_entry = new_map.get(name)
            if new_entry is None:
                if not justified(("remove_type",), kind, name):
                    findings.append(_finding(
                        "SEED_REMOVAL_UNJUSTIFIED",
                        f"{kind}_inventory.{name}",
                        f"seed {kind} type {name!r} is dropped "
                        f"by this revision with no remove_type "
                        f"decision point recording why — an "
                        f"inherited type disappears only on a "
                        f"recorded reason"))
                continue
            seed_def = _norm_text(str(seed_entry.get(
                "definition") or ""))
            new_def = _norm_text(str(new_entry.get(
                "definition") or ""))
            if seed_def != new_def and not justified(
                    ("revise_definition", "narrow_definition"),
                    kind, name):
                findings.append(_finding(
                    "SEED_DEFINITION_UNDECLARED",
                    f"{kind}_inventory.{name}.definition",
                    f"the definition of inherited {kind} type "
                    f"{name!r} is rewritten with no "
                    f"revise_definition decision point "
                    f"recording why — a silent rewrite can "
                    f"narrow the type and strand the facts "
                    f"the old breadth covered"))
        if kind != "relation":
            continue
        for name, seed_entry in seed_map.items():
            new_entry = new_map.get(name)
            if new_entry is None:
                continue  # the drop itself is reported above
            dropped = _signature_atoms(seed_entry) - \
                _signature_atoms(new_entry)
            for atom in sorted(dropped):
                if not justified(("remove_signature",),
                                 "relation", name, detail=atom):
                    findings.append(_finding(
                        "SEED_SIGNATURE_UNJUSTIFIED",
                        f"relation_inventory.{name}."
                        f"endpoint_signature",
                        f"signature pair {atom!r} of inherited "
                        f"relation {name!r} is dropped with no "
                        f"remove_signature decision point "
                        f"recording why"))
            added = _signature_atoms(new_entry) - \
                _signature_atoms(seed_entry)
            if added:
                seed_def = _norm_text(str(seed_entry.get(
                    "definition") or ""))
                new_def = _norm_text(str(new_entry.get(
                    "definition") or ""))
                if seed_def == new_def and not justified(
                        ("extend_signature",), "relation", name):
                    findings.append(_finding(
                        "SEED_SIGNATURE_UNDECLARED",
                        f"relation_inventory.{name}."
                        f"endpoint_signature",
                        f"relation {name!r} gains signature "
                        f"pair(s) {sorted(added)} while its "
                        f"definition stands unchanged and no "
                        f"extend_signature decision point "
                        f"states the definition already covers "
                        f"them — a pair addition rides with "
                        f"its definition revision, or with "
                        f"that declaration"))


def validate_schema_draft(draft: dict) -> list[dict]:
    """Stage 2 — deterministic, fail-closed validation.

    Returns the findings list (empty = valid). Checks: document
    shape and pack-id slug; closed inventories with no duplicate
    or colliding names; a definition and boundary material for
    every type; relation endpoint signatures inside the entity
    inventory; no overlapping types without a declared boundary;
    definitions in a single source (a competing definitions
    carrier, or per-type definitions disagreeing with the
    schema_front, is a fork); merge prohibitions naming real
    types; seed lineage for seeded drafts (no unjustified
    removal, signature shrinkage, or silent definition
    rewrite — see _check_seed_lineage); and full pack-contract
    conformance of the would-be payload via
    profile_config.setup_config_errors."""
    findings: list[dict] = []
    if not isinstance(draft, dict):
        return [_finding("DRAFT_NOT_OBJECT", "",
                         "schema draft is not a JSON object")]
    if draft.get("kind") != DRAFT_KIND:
        findings.append(_finding(
            "WRONG_KIND", "kind",
            f"document kind {draft.get('kind')!r} is not "
            f"{DRAFT_KIND!r}"))
    if not _nonempty_str(draft.get("family")):
        findings.append(_finding(
            "MISSING_FIELD", "family", "draft has no family"))
    pack_id = draft.get("pack_id")
    if not _nonempty_str(pack_id):
        findings.append(_finding(
            "MISSING_FIELD", "pack_id", "draft has no pack_id"))
    elif not _SLUG_RE.match(str(pack_id)):
        findings.append(_finding(
            "PACK_ID_SLUG", "pack_id",
            f"pack_id {pack_id!r} is not a slug (lowercase "
            f"letters/digits joined by single hyphens) — it "
            f"names the pack directory"))
    sf = draft.get("schema_front")
    if not isinstance(sf, dict):
        findings.append(_finding(
            "MISSING_FIELD", "schema_front",
            "draft has no schema_front object"))
        return findings
    entities = _check_inventory(sf.get("entity_inventory"),
                                "entity", findings)
    relations = _check_inventory(sf.get("relation_inventory"),
                                 "relation", findings)
    if not entities:
        findings.append(_finding(
            "EMPTY_ENTITY_INVENTORY", "entity_inventory",
            "the schema declares no entity types"))
    if not relations:
        findings.append(_finding(
            "EMPTY_RELATION_INVENTORY", "relation_inventory",
            "the schema declares no relation types"))
    for name, entry in entities.items():
        if not _entity_boundary_material(entry):
            findings.append(_finding(
                "MISSING_BOUNDARY_EXAMPLES",
                f"entity_inventory.{name}",
                f"entity type {name!r} has no boundary examples "
                f"(form_examples / not_entities) — boundary "
                f"material is what separates it from its "
                f"neighbours"))
        role = entry.get("role", "entity")
        if not _nonempty_str(role):
            findings.append(_finding(
                "BAD_ROLE", f"entity_inventory.{name}.role",
                f"entity type {name!r} has a non-string role"))
    entity_norms = {_norm_name(n) for n in entities}
    for name, entry in relations.items():
        if not _relation_boundary_material(entry):
            findings.append(_finding(
                "MISSING_BOUNDARY_EXAMPLES",
                f"relation_inventory.{name}",
                f"relation type {name!r} has no boundary "
                f"material (boundary_examples, or a signature "
                f"note + extraction intent)"))
        signature = entry.get("endpoint_signature")
        if not isinstance(signature, list) or not signature:
            findings.append(_finding(
                "BAD_ENDPOINT_SIGNATURE",
                f"relation_inventory.{name}.endpoint_signature",
                f"relation type {name!r} has no endpoint "
                f"signature"))
        else:
            # Shipped shape: an entry is a type name, or a
            # "TypeA~TypeB" pair string; every named side must
            # be an inventory type.
            for endpoint in signature:
                for side in str(endpoint).split("~"):
                    if side.strip() not in entities:
                        findings.append(_finding(
                            "UNKNOWN_ENDPOINT_TYPE",
                            f"relation_inventory.{name}."
                            f"endpoint_signature",
                            f"relation type {name!r} names "
                            f"endpoint type {side.strip()!r}, "
                            f"which is not in the entity "
                            f"inventory"))
        inverse = entry.get("inverse_of")
        if inverse is not None and str(inverse) not in relations:
            findings.append(_finding(
                "UNKNOWN_INVERSE",
                f"relation_inventory.{name}.inverse_of",
                f"relation type {name!r} declares inverse_of "
                f"{inverse!r}, which is not in the relation "
                f"inventory"))
        if _norm_name(name) in entity_norms:
            findings.append(_finding(
                "TYPE_NAME_COLLISION",
                f"relation_inventory.{name}",
                f"relation type {name!r} shares its name with "
                f"an entity type — one name, two kinds"))
    _check_overlaps(entities, "entity", findings)
    _check_overlaps(relations, "relation", findings)
    # Definition single-source: definitions live in the
    # schema_front only. A competing top-level definitions
    # carrier, or name-list entries carrying their own
    # definitions that disagree with the schema_front, fork it.
    if "definitions" in draft:
        findings.append(_finding(
            "DEFINITION_FORK", "definitions",
            "the draft carries a top-level 'definitions' object "
            "— definitions live in the schema_front only "
            "(single source)"))
    for key, entries in (("entity_types", entities),
                         ("relation_types", relations)):
        declared = draft.get(key)
        if isinstance(declared, list):
            for item in declared:
                if isinstance(item, dict) and \
                        _nonempty_str(item.get("definition")):
                    front = entries.get(str(item.get("type") or ""))
                    if front is not None and \
                            _norm_text(str(item["definition"])) != \
                            _norm_text(str(front.get("definition")
                                           or "")):
                        findings.append(_finding(
                            "DEFINITION_FORK", key,
                            f"{key} entry {item.get('type')!r} "
                            f"carries a definition that disagrees "
                            f"with the schema_front — definitions "
                            f"live in the schema_front only"))
            declared_names = sorted(
                str(i) if isinstance(i, str)
                else str((i or {}).get("type") or "")
                for i in declared)
            front_names = sorted(entries)
            if declared_names != front_names:
                findings.append(_finding(
                    "INVENTORY_MISMATCH", key,
                    f"the draft's {key} list does not match the "
                    f"schema_front inventory — the inventory is "
                    f"the single source for the type list"))
    for pair in draft.get("merge_prohibited_type_pairs") or []:
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            findings.append(_finding(
                "BAD_MERGE_PAIR", "merge_prohibited_type_pairs",
                f"merge prohibition {pair!r} is not a pair of "
                f"type names"))
            continue
        a, b = str(pair[0]), str(pair[1])
        if a == b:
            findings.append(_finding(
                "BAD_MERGE_PAIR", "merge_prohibited_type_pairs",
                f"merge prohibition names type {a!r} against "
                f"itself"))
        for side in (a, b):
            if side not in entities:
                findings.append(_finding(
                    "UNKNOWN_MERGE_PAIR_TYPE",
                    "merge_prohibited_type_pairs",
                    f"merge prohibition names type {side!r}, "
                    f"which is not in the entity inventory"))
    tac = draft.get("theme_attest_chunk")
    if tac is not None and not isinstance(tac, bool):
        findings.append(_finding(
            "BAD_THEME_ATTEST", "theme_attest_chunk",
            f"theme_attest_chunk {tac!r} is not a boolean — "
            f"the declaration is true (chunk-level theme "
            f"attestation) or absent (the quote-only test)"))
    declared_boundaries = _boundary_declaration_index(
        draft, entities, relations, findings)
    _check_seed_lineage(draft, entities, relations, findings)
    _check_boundary_declarations(draft, relations,
                                 declared_boundaries, findings)
    # Pack-contract conformance: the payload emission would
    # ship must pass the same fail-closed validation as any
    # profile config.
    for err in profile_config.setup_config_errors(
            _payload_for(draft, "0.1.0")):
        findings.append(_finding(
            "PAYLOAD_INVALID", "payload",
            f"the emitted payload would fail setup_config "
            f"validation: {err}"))
    return findings


# ------------------------------------------------------------------
# 2b. drift diff (anchoring check: report only, never a merge)

def _front_maps(front: dict) -> tuple[dict, dict]:
    entities = {str(e["type"]): e
                for e in front.get("entity_inventory") or []
                if isinstance(e, dict)
                and _nonempty_str(e.get("type"))}
    relations = {str(r["type"]): r
                 for r in front.get("relation_inventory") or []
                 if isinstance(r, dict)
                 and _nonempty_str(r.get("type"))}
    return entities, relations


def _pair_sides(atom: str) -> tuple[str, str] | None:
    sides = [s.strip() for s in atom.split("~")]
    if len(sides) == 2 and sides[0] != sides[1]:
        return sides[0], sides[1]
    return None


def schema_front_diff(a_front: dict, b_front: dict) -> dict:
    """The structural difference between two schema fronts —
    the drift report's substance. Compares inventories by
    type name, definitions by normalized text, and relation
    signatures by atom set. Also names *inverse candidates*:
    two relations (one from each front, or both in B) whose
    pair atoms are exact swaps of each other with no
    inverse_of declared between them — the duplicated-
    inverse waste a reviewer should settle by declaring the
    inverse, reported here as review material, never as a
    validation finding and never merged automatically."""
    a_e, a_r = _front_maps(a_front)
    b_e, b_r = _front_maps(b_front)
    definitions_changed = []
    for kind, a_map, b_map in (("entity", a_e, b_e),
                               ("relation", a_r, b_r)):
        for name in sorted(set(a_map) & set(b_map)):
            if _norm_text(str(a_map[name].get("definition")
                              or "")) != \
                    _norm_text(str(b_map[name].get("definition")
                                   or "")):
                definitions_changed.append(
                    {"kind": kind, "type": name})
    signature_changes = []
    for name in sorted(set(a_r) & set(b_r)):
        added = _signature_atoms(b_r[name]) - \
            _signature_atoms(a_r[name])
        removed = _signature_atoms(a_r[name]) - \
            _signature_atoms(b_r[name])
        if added or removed:
            signature_changes.append({
                "type": name,
                "added": sorted(added),
                "removed": sorted(removed)})
    # Inverse candidates are a property of the compared
    # schemas together: index every relation's pair atoms by
    # name across both fronts (a name in both fronts is one
    # relation), then look for two distinct relations holding
    # exact swaps of one pair with no inverse_of declared
    # between them on either side.
    pair_index: dict[tuple, set] = {}
    declared_inverse: set[tuple] = set()
    for front_relations in (a_r, b_r):
        for name, entry in front_relations.items():
            for atom in _signature_atoms(entry):
                sides = _pair_sides(atom)
                if sides:
                    pair_index.setdefault(sides, set()).add(name)
            inverse = entry.get("inverse_of")
            if _nonempty_str(inverse):
                declared_inverse.add((name, str(inverse)))
                declared_inverse.add((str(inverse), name))
    inverse_candidates = []
    seen_pairs = set()
    for (x, y), names in sorted(pair_index.items()):
        for other in sorted(pair_index.get((y, x), set())):
            for name in sorted(names):
                if other == name:
                    continue
                key = tuple(sorted((name, other)))
                if key in seen_pairs or \
                        (name, other) in declared_inverse:
                    continue
                seen_pairs.add(key)
                inverse_candidates.append(
                    {"a_relation": name, "b_relation": other,
                     "pair": f"{x}~{y}"})
    return {
        "entities_added": sorted(set(b_e) - set(a_e)),
        "entities_removed": sorted(set(a_e) - set(b_e)),
        "relations_added": sorted(set(b_r) - set(a_r)),
        "relations_removed": sorted(set(a_r) - set(b_r)),
        "definitions_changed": definitions_changed,
        "signature_changes": signature_changes,
        "inverse_candidates": inverse_candidates,
    }


def render_schema_diff(diff: dict, a_label: str = "A",
                       b_label: str = "B") -> str:
    """The drift report as a human document. A diff is a
    report, never a merge: nothing in this module applies
    one front's content to the other."""
    lines = [
        f"# Schema drift report — {a_label} → {b_label}",
        "",
        "Structural difference only — a report, never a "
        "merge; nothing here is applied. Removals and "
        "signature losses on the "
        "B side are the anchoring check's subject: what a "
        "fresh composition (or a revision) dropped that A "
        "carried.",
        "",
        f"Entity types: +{len(diff['entities_added'])} "
        f"-{len(diff['entities_removed'])}",
    ]
    for name in diff["entities_added"]:
        lines.append(f"- added entity: {name}")
    for name in diff["entities_removed"]:
        lines.append(f"- REMOVED entity: {name}")
    lines.append(f"Relation types: "
                 f"+{len(diff['relations_added'])} "
                 f"-{len(diff['relations_removed'])}")
    for name in diff["relations_added"]:
        lines.append(f"- added relation: {name}")
    for name in diff["relations_removed"]:
        lines.append(f"- REMOVED relation: {name}")
    lines.append(f"Definitions rewritten: "
                 f"{len(diff['definitions_changed'])}")
    for change in diff["definitions_changed"]:
        lines.append(f"- definition changed: "
                     f"{change['kind']} {change['type']!r}")
    lines.append(f"Signature changes: "
                 f"{len(diff['signature_changes'])}")
    for change in diff["signature_changes"]:
        for atom in change["added"]:
            lines.append(f"- signature + {change['type']}: "
                         f"{atom}")
        for atom in change["removed"]:
            lines.append(f"- signature - {change['type']}: "
                         f"{atom}")
    lines.append(f"Inverse candidates (review — declare "
                 f"inverse_of instead of duplicating): "
                 f"{len(diff['inverse_candidates'])}")
    for cand in diff["inverse_candidates"]:
        lines.append(f"- {cand['a_relation']!r} "
                     f"({cand['pair']}) reverses "
                     f"{cand['b_relation']!r} with no "
                     f"inverse_of declared")
    lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------
# 3. approve

def render_approval_brief(draft: dict,
                          findings: list[dict] | None = None) -> str:
    """The human decision document, in the lab approval-brief
    pattern: what the schema is, what approval settles, and —
    while validation findings remain — exactly what blocks."""
    if findings is None:
        findings = validate_schema_draft(draft)
    sf = draft.get("schema_front") or {}
    entities = sf.get("entity_inventory") or []
    relations = sf.get("relation_inventory") or []
    lines = [
        f"# {draft.get('display_name') or draft.get('pack_id')} "
        f"— approval brief (ontology builder)",
        "",
        f"Draft pack: `{draft.get('pack_id')}` · family "
        f"`{draft.get('family')}` · status "
        f"{draft.get('status')} · schema hash "
        f"`{schema_hash(draft)}`",
        f"Drafted by: {draft.get('builder_version')} · drafting "
        f"tier: {(draft.get('provenance') or {}).get('drafting_tier')}"
        f" (source: "
        f"{(draft.get('provenance') or {}).get('drafting_source')})",
    ]
    seed = draft.get("seed")
    if isinstance(seed, dict) and isinstance(
            seed.get("schema_front"), dict):
        lines.append(
            f"Lineage: revision inheriting seed pack "
            f"`{(seed.get('source') or {}).get('pack_id')}` · "
            f"seed schema hash `{seed.get('schema_hash')}`")
    lines += [
        "",
        "## Inventory",
        "",
        f"Entity types ({len(entities)}):",
    ]
    for e in entities:
        if isinstance(e, dict):
            lines.append(f"- {e.get('type')}: "
                         f"{e.get('definition', '')}")
    lines.append("")
    lines.append(f"Relation types ({len(relations)}):")
    for r in relations:
        if isinstance(r, dict):
            lines.append(f"- {r.get('type')}: "
                         f"{r.get('definition', '')}")
    lines.append("")
    lines.append("## Decision points (human approval required)")
    lines.append("")
    if findings:
        lines.append("Validation is NOT clean — approval is "
                     "refused until these are settled:")
        lines.append("")
        for f in findings:
            lines.append(f"- [{f['code']}] {f['path']}: "
                         f"{f['message']}")
        lines.append("")
    declared = draft.get("decision_points") or []
    if declared:
        for point in declared:
            if isinstance(point, dict):
                action = point.get("action")
                if action:
                    detail = point.get("detail")
                    detail_txt = (f" [{detail}]"
                                  if detail is not None else "")
                    lines.append(
                        f"- [seed change: {action}] "
                        f"{point.get('kind')} "
                        f"{point.get('type')!r}{detail_txt} — "
                        f"{point.get('reason')}")
                else:
                    lines.append(
                        "- " + json.dumps(point,
                                          ensure_ascii=False))
            else:
                lines.append(f"- {point}")
        lines.append("")
    if not findings and not declared:
        lines.append("- none outstanding — validation is clean; "
                     "approval settles the schema as drafted.")
        lines.append("")
    lines.append("Approving binds an approval record to the "
                 "schema hash above; any later schema edit "
                 "stale-dates the approval and emission refuses.")
    lines.append("")
    return "\n".join(lines)


def approve_schema_draft(draft: dict, *, approver: str,
                         outcome: str | None = None,
                         issue=None) -> dict:
    """Stage 3 — record the human approval of a draft.

    Mirrors the lab's approval record (profile / profile_hash /
    decision_points / issue / outcome), bound to the draft's
    schema hash. Refuses (ValueError) while validation findings
    remain, or when the recorded outcome is not an approval —
    a rejection is recorded by the absence of an approval, not
    by an approval record that does not approve."""
    findings = validate_schema_draft(draft)
    if findings:
        raise ValueError(
            "approval refused: the draft fails validation with "
            f"{len(findings)} finding(s): "
            + "; ".join(f"[{f['code']}] {f['message']}"
                        for f in findings))
    today = datetime.now(UTC).date().isoformat()
    if outcome is None:
        outcome = f"approved ({approver}, {today})"
    if "approved" not in outcome.casefold():
        raise ValueError(
            "approval refused: the recorded outcome does not "
            f"approve ({outcome!r})")
    return {
        "pack_id": draft.get("pack_id"),
        "profile": draft.get("family"),
        "schema_hash": schema_hash(draft),
        "decision_points": len(draft.get("decision_points") or []),
        "issue": issue,
        "outcome": outcome,
        "approver": approver,
        "approved_at": _now(),
    }


# ------------------------------------------------------------------
# 4. emit

def emit_pack(draft: dict, approval: dict | None, out_root,
              pack_version: str = "0.1.0") -> dict:
    """Stage 4 — emit an approved draft as a pack skeleton.

    Writes packs/<pack_id>/ under ``out_root``: pack.json
    (payload pinned by sha256), profile/setup_config.json (the
    payload), conformance.json (one stub entry recording the
    emission as unmeasured). Refuses — ValueError naming the
    reason — when the draft fails validation, when there is no
    approval record, when the approval is stale (its schema
    hash no longer matches the draft) or names another pack,
    when the recorded outcome is not an approval, or when the
    target pack already exists (a changed pack is a new
    version, never an overwrite). Before returning, the
    emitted pack is loaded back through the staged pack
    loader (pin verification + payload validation) and its
    guidance rendered, so an emit that cannot load cannot
    report success."""
    findings = validate_schema_draft(draft)
    if findings:
        raise ValueError(
            "emission refused: the draft fails validation with "
            f"{len(findings)} finding(s): "
            + "; ".join(f"[{f['code']}] {f['message']}"
                        for f in findings))
    if approval is None:
        raise ValueError(
            "emission refused: no approval record — a draft "
            "cannot become in_force without human approval")
    if approval.get("pack_id") != draft.get("pack_id"):
        raise ValueError(
            "emission refused: the approval record names pack "
            f"{approval.get('pack_id')!r}, not "
            f"{draft.get('pack_id')!r}")
    if approval.get("schema_hash") != schema_hash(draft):
        raise ValueError(
            "emission refused: the approval is stale — its "
            "schema hash does not match the draft as it now "
            "stands; re-approve the current draft")
    if "approved" not in str(approval.get("outcome") or "").casefold():
        raise ValueError(
            "emission refused: the approval record's outcome "
            "does not approve")
    pack_id = str(draft["pack_id"])
    pack_dir = Path(out_root) / "packs" / pack_id
    if (pack_dir / "pack.json").exists():
        raise ValueError(
            f"emission refused: pack {pack_id!r} already "
            f"exists at {pack_dir} — a changed pack is a new "
            f"version, not an overwrite")
    payload = _payload_for(draft, pack_version)
    sf = draft.get("schema_front") or {}
    n_entities = len(_inventory_names(sf.get("entity_inventory")))
    n_relations = len(_inventory_names(sf.get("relation_inventory")))

    profile_dir = pack_dir / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    payload_rel = f"packs/{pack_id}/profile/setup_config.json"
    payload_path = profile_dir / "setup_config.json"
    payload_path.write_text(
        json.dumps(payload, indent=1, ensure_ascii=False))
    pin = hashlib.sha256(payload_path.read_bytes()).hexdigest()

    manifest = {
        "assembled": datetime.now(UTC).date().isoformat(),
        "conformance_record": "conformance.json",
        "contents_index": {
            "schema_front": (
                f"Closed entity inventory ({n_entities} types) "
                f"and relation inventory ({n_relations} types) "
                f"with definitions + boundary examples, drafted "
                f"and approved through the ontology builder; "
                f"rendered into proposer guidance by the "
                f"profile channel (derive_guidance)."),
            "approval": (
                f"Approval record bound to schema hash "
                f"{approval['schema_hash']} (outcome: "
                f"{approval.get('outcome')})."),
        },
        "design": "docs/PACK_DESIGN.md",
        "display_name": draft.get("display_name") or pack_id,
        "emitted_by": BUILDER_VERSION,
        "pack_format": 1,
        "pack_id": pack_id,
        "pack_version": pack_version,
        "payload": {
            "kind": "setup_config",
            "note": ("Emitted by the ontology builder from an "
                     "approved schema draft (approval bound to "
                     "the schema hash in contents_index). The "
                     "pack binds the payload by pin; it never "
                     "forks its content — a changed schema is "
                     "a new pack version."),
            "path": payload_rel,
            "profile_version": 1,
            "sha256": pin,
        },
        "profile_family": draft.get("family"),
        "status": "in_force",
    }
    (pack_dir / "pack.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False,
                   sort_keys=True))
    conformance = {
        "entries": [{
            "id": "OB-1",
            "status": "emitted_unmeasured",
            "date": datetime.now(UTC).date().isoformat(),
            "baseline": ("No pack: the domain had no schema of "
                         "its own (core baseline "
                         "defaults only)."),
            "content": (
                f"Ontology-builder emission: schema_front with "
                f"{n_entities} entity types and {n_relations} "
                f"relation types (definitions + boundary "
                f"examples), approved under schema hash "
                f"{approval['schema_hash']}."),
            "delta": ("The artifact exists; no measurement has "
                      "priced it."),
            "gate": ("Emission gate = builder validation "
                     "(every finding clear) + a human approval "
                     "record bound to the schema hash. Adoption "
                     "as a measured pack requires a registered "
                     "conformance measurement "
                     "(docs/PACK_DESIGN.md §2)."),
            "instrument": ("pipeline/ontology_builder.py "
                           "(draft -> validate -> approve -> "
                           "emit)"),
            "population": ("Unmeasured — no corpus run under "
                           "this pack yet."),
            "treatment": (
                f"Pack {pack_version} skeleton in force as "
                f"data. It declares no thresholds or operating "
                f"points; the core baseline supplies them "
                f"at resolution."),
            "verdict": ("EMITTED — builder-assembled and "
                        "approval-bound; explicitly NOT a "
                        "measured conformance claim."),
        }],
        "pack_id": pack_id,
        "pack_version": pack_version,
        "record_format": 1,
        "spec": "docs/PACK_DESIGN.md §2",
    }
    (pack_dir / "conformance.json").write_text(
        json.dumps(conformance, indent=1, ensure_ascii=False,
                   sort_keys=True))
    # Self-check through the staged loader: pin verification +
    # payload validation + a guidance render. An emit that
    # cannot load cannot report success. The emitted files
    # must also satisfy the pack schemas (packs/
    # pack.schema.json + packs/conformance.schema.json) —
    # the builder emits only what the format defines.
    from . import pack_schema
    defects = pack_schema.validate_pack_dir(pack_dir)
    if defects:
        raise ValueError(
            "emission produced a pack that fails schema "
            "validation: " + "; ".join(defects))
    loaded = profile_config.pack_setup_config(pack_dir)
    profile_config.derive_guidance(loaded)
    return manifest


# ------------------------------------------------------------------
# CLI

def _load_json(path) -> dict:
    try:
        text = Path(path).read_text()
    except FileNotFoundError as e:
        raise ValueError(f"{path} not found: {e}") from e
    except OSError as e:
        raise ValueError(f"{path} cannot be read: {e}") from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _write_json(path, obj) -> None:
    # The builder owns its working directories (the emit path
    # already creates the pack tree): a first-run user following
    # the tracks must not meet a raw FileNotFoundError because
    # drafts/ does not exist yet.
    p = Path(path)
    if p.parent != Path(""):
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, ensure_ascii=False))


def _print_findings(findings: list[dict]) -> None:
    for f in findings:
        print(f"  [{f['code']}] {f['path']}: {f['message']}")


def main(argv=None) -> int:
    """``python -m pipeline ontology <draft|import|validate|
    brief|approve|emit|check|diff> ...`` — the builder's
    command surface."""
    ap = argparse.ArgumentParser(
        prog="ontosmith ontology",
        description="Ontology builder: draft -> validate -> "
                    "approve -> emit a pack")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("draft", help="Draft a schema from a "
                                     "corpus profile + brief "
                                     "(fresh by default; "
                                     "--from PACK extends an "
                                     "existing pack; a "
                                     "material guide warns "
                                     "under 30 documents of "
                                     "material, strongly "
                                     "under 10)")
    p.add_argument("--profile", required=True,
                   help="stage-0 profile.json (setup_service)")
    p.add_argument("--brief", default=None,
                   help="domain brief: a text file, or a JSON "
                        "file with text/purpose/entity_types/"
                        "relation_types (the type fields are "
                        "lists of type-name strings)")
    p.add_argument("--family", default=None,
                   help="corpus family (default: the profile's)")
    p.add_argument("--pack-id", required=True)
    p.add_argument("--display-name", default=None)
    p.add_argument("--out", required=True, help="draft JSON path")
    p.add_argument("--llm", action="store_true",
                   help="bind the drafting tier to a chat model "
                        "(default: deterministic brief-seeded "
                        "skeleton, no spend)")
    p.add_argument("--model", default=None,
                   help="drafting-tier binding (connection "
                        "layer); default: the drafting seat's "
                        "bound model (ONTOSMITH_DRAFTING_MODEL) "
                        "— the seat ships unbound")
    p.add_argument("--from", dest="from_pack", default=None,
                   metavar="PACK",
                   help="extension mode: extend the named "
                        "existing pack — a pack id resolved "
                        "under this repo's packs/ tree, or a "
                        "path to a schema draft / pack payload "
                        "JSON — revising its schema against "
                        "this profile's documents (the new "
                        "material) under the inheritance rules "
                        "(removals and shrinks then need "
                        "recorded reasons). Without --from the "
                        "draft composes fresh, even when a "
                        "published pack carries the same "
                        "--pack-id")
    p.add_argument("--holes", default=None,
                   help="hole report JSON (a list of family "
                        "records, or an object with a "
                        "'families' list) the revision must "
                        "address")

    p = sub.add_parser("import", help="Import an external "
                                      "ontology as a draft "
                                      "(BYO schema): inventory "
                                      "+ scoping spec -> draft "
                                      "+ scoping record")
    p.add_argument("--spec", required=True,
                   help="scoping spec JSON (the human judgment: "
                        "per-type sources, actions, boundary "
                        "material, drops with reasons)")
    p.add_argument("--inventory", required=True, action="append",
                   help="source-ontology inventory JSON "
                        "(repeatable: module file + reference "
                        "files)")
    p.add_argument("--out", required=True, help="draft JSON path")
    p.add_argument("--scoping-out", default=None,
                   help="scoping record path (default: beside "
                        "the draft, .scoping.json)")

    p = sub.add_parser("validate", help="Validate a schema draft")
    p.add_argument("draft")

    p = sub.add_parser("brief", help="Render the approval brief")
    p.add_argument("draft")

    p = sub.add_parser("approve", help="Record human approval "
                                       "of a draft")
    p.add_argument("draft")
    p.add_argument("--approver", required=True)
    p.add_argument("--outcome", default=None,
                   help="outcome text (must approve; default: "
                        "'approved (<approver>, <date>)')")
    p.add_argument("--issue", default=None)
    p.add_argument("--out", default=None,
                   help="approval record path (default: "
                        "approval_<date>.json beside the draft)")

    p = sub.add_parser("emit", help="Emit an approved draft as "
                                    "a pack")
    p.add_argument("draft")
    p.add_argument("--approval", required=True)
    p.add_argument("--out-root", default=None,
                   help="tree root the pack lands under "
                        "(default: this repo's root)")
    p.add_argument("--pack-version", default="0.1.0")

    p = sub.add_parser("check", help="Validate a pack "
                                     "directory against the "
                                     "pack schemas + loader "
                                     "(contributor "
                                     "pre-submission check)")
    p.add_argument("pack_dir",
                   help="the pack directory (packs/<pack_id>)")

    p = sub.add_parser("diff", help="Report the structural "
                                    "difference between two "
                                    "schemas (drafts or pack "
                                    "payloads) — the anchoring "
                                    "check; a report, never a "
                                    "merge")
    p.add_argument("a", help="base schema JSON (draft or "
                             "pack payload)")
    p.add_argument("b", help="compared schema JSON (draft "
                             "or pack payload)")
    p.add_argument("--a-label", default=None)
    p.add_argument("--b-label", default=None)
    p.add_argument("--out", default=None,
                   help="report path (default: stdout)")

    args = ap.parse_args(argv)

    if args.cmd == "draft":
        try:
            profile = _load_json(args.profile)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        brief = None
        if args.brief:
            raw = Path(args.brief).read_text()
            try:
                brief = json.loads(raw)
            except json.JSONDecodeError:
                brief = raw
        drafter = None
        if args.llm:
            from . import connections as _connections
            _binding = _connections.seat("drafting")
            try:
                draft_model = args.model or _binding.require_model()
            except _connections.BindingModelError as e:
                print(str(e), file=sys.stderr)
                return 2
            draft_key = _binding.key_or_none()
            if not draft_key:
                print(f"drafting tier needs {_binding.key_env} "
                      "(or drop --llm for the deterministic "
                      "skeleton)", file=sys.stderr)
                return 2
            drafter = make_chat_drafter(draft_model, draft_key)
        seed = None
        seed_from = None
        if args.from_pack:
            # Extension mode: the caller names the pack to
            # extend — a schema document path, or a pack id
            # resolved in this repo's packs/ tree. A named
            # target that does not resolve refuses loudly:
            # extension intent must never degrade silently
            # into a fresh composition.
            if Path(args.from_pack).exists():
                try:
                    seed = _load_json(args.from_pack)
                except ValueError as e:
                    print(str(e), file=sys.stderr)
                    return 1
                seed_from = args.from_pack
            elif _SLUG_RE.match(args.from_pack):
                try:
                    found = _published_pack_seed(args.from_pack)
                except ValueError as e:
                    print(str(e), file=sys.stderr)
                    return 1
                if found is None:
                    print(f"--from names pack "
                          f"{args.from_pack!r}, but no "
                          f"extendable published pack with "
                          f"that id exists in this repo — the "
                          f"pack is missing, or its payload "
                          f"carries no schema_front to "
                          f"inherit; compose without --from "
                          f"to start a new one",
                          file=sys.stderr)
                    return 1
                seed, published = found
                seed_from = str(published)
            else:
                print(f"--from {args.from_pack!r} is neither "
                      f"a schema document path nor a pack id",
                      file=sys.stderr)
                return 1
        hole_report = None
        if args.holes:
            try:
                holes_doc = _load_json(args.holes)
            except ValueError as e:
                print(str(e), file=sys.stderr)
                return 1
            if isinstance(holes_doc, dict):
                hole_report = holes_doc.get("families") or \
                    holes_doc.get("hole_report")
            else:
                hole_report = holes_doc
        try:
            draft = draft_schema(
                profile, brief,
                family=args.family or profile.get("family") or "",
                pack_id=args.pack_id,
                display_name=args.display_name
                or args.pack_id.replace("-", " ").title(),
                drafter=drafter, seed=seed,
                hole_report=hole_report)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        if seed is not None:
            seed_block = draft.get("seed") or {}
            seed_name = (seed_block.get("source") or {}).get(
                "pack_id") or args.from_pack
            print(f"mode: extension — extends pack "
                  f"{seed_name!r} (inherits "
                  f"{seed_block.get('schema_hash')} from "
                  f"{seed_from})")
        else:
            print(f"mode: fresh composition — no seed; "
                  f"pack {args.pack_id!r} starts from a "
                  f"blank schema")
        guide = material_guidance(profile)
        if guide is not None:
            print(guide)
        _write_json(args.out, draft)
        findings = validate_schema_draft(draft)
        print(f"draft written: {args.out} "
              f"(status {draft['status']}, "
              f"{len(draft['entity_types'])} entity types, "
              f"{len(draft['relation_types'])} relation types, "
              f"schema hash {schema_hash(draft)})")
        if findings:
            print(f"validation findings ({len(findings)}) — the "
                  f"decision-point list approval must settle:")
            _print_findings(findings)
        else:
            print("validation: clean")
        return 0

    if args.cmd == "import":
        from . import ontology_import
        try:
            spec = ontology_import.load_json_file(args.spec)
            inventories = [ontology_import.load_json_file(p)
                           for p in args.inventory]
            draft, record = ontology_import.build_import(
                spec, inventories)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        _write_json(args.out, draft)
        scoping_out = args.scoping_out
        if scoping_out is None:
            stem = Path(args.out)
            if stem.suffix == ".json":
                stem = stem.with_suffix("")
                if stem.suffix == ".draft":
                    stem = stem.with_suffix("")
            scoping_out = str(stem) + ".scoping.json"
        _write_json(scoping_out, record)
        counts = record["counts"]
        print(f"draft written: {args.out} "
              f"(status {draft['status']}, "
              f"{len(draft['entity_types'])} entity types, "
              f"{len(draft['relation_types'])} relation types, "
              f"schema hash {schema_hash(draft)})")
        print(f"scoping record: {scoping_out} "
              f"({counts['scoped_concepts']} scoped concepts: "
              f"{counts['kept']} kept, {counts['merged']} merged, "
              f"{counts['flattened']} flattened, "
              f"{counts['role_converted']} role-converted, "
              f"{counts['demoted']} demoted, "
              f"{counts['dropped']} dropped; "
              f"{counts['external_entities']} external entities)")
        findings = validate_schema_draft(draft)
        if findings:
            print(f"validation findings ({len(findings)}) — the "
                  f"decision-point list approval must settle:")
            _print_findings(findings)
        else:
            print("validation: clean")
        return 0

    if args.cmd == "validate":
        try:
            draft = _load_json(args.draft)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        findings = validate_schema_draft(draft)
        if findings:
            print(f"validation findings ({len(findings)}):")
            _print_findings(findings)
            return 1
        print("validation: clean")
        return 0

    if args.cmd == "brief":
        try:
            draft = _load_json(args.draft)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        print(render_approval_brief(draft))
        return 0

    if args.cmd == "approve":
        try:
            draft = _load_json(args.draft)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        try:
            record = approve_schema_draft(
                draft, approver=args.approver,
                outcome=args.outcome, issue=args.issue)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        out = args.out or str(
            Path(args.draft).parent
            / f"approval_{datetime.now(UTC).date().isoformat()}.json")
        _write_json(out, record)
        print(f"approval recorded: {out}")
        print(f"  schema hash {record['schema_hash']}")
        return 0

    if args.cmd == "emit":
        try:
            draft = _load_json(args.draft)
            approval = _load_json(args.approval)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        root = Path(args.out_root) if args.out_root else \
            Path(__file__).resolve().parent.parent
        try:
            manifest = emit_pack(draft, approval, root,
                                 pack_version=args.pack_version)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        print(f"pack emitted: {root / 'packs' / manifest['pack_id']}")
        print(f"  payload pin sha256:"
              f"{manifest['payload']['sha256'][:16]}…")
        return 0

    if args.cmd == "check":
        from . import pack_schema
        pack_dir = Path(args.pack_dir)
        defects = pack_schema.validate_pack_dir(pack_dir)
        try:
            profile_config.pack_setup_config(pack_dir)
        except ValueError as e:
            message = str(e)
            if not any(message in d or d in message
                       for d in defects):
                defects.append(message)
        if defects:
            print(f"pack check: {len(defects)} defect(s) in "
                  f"{pack_dir}:")
            for d in defects:
                print(f"  - {d}")
            return 1
        manifest = profile_config.load_pack_manifest(pack_dir)
        print(f"pack check: clean — {manifest['pack_id']} "
              f"{manifest['pack_version']} (manifest + "
              f"conformance record satisfy the pack schemas; "
              f"payload pin verifies)")
        return 0

    if args.cmd == "diff":
        try:
            a_doc = _load_json(args.a)
            b_doc = _load_json(args.b)
            a_front, _ = _seed_parts(a_doc)
            b_front, _ = _seed_parts(b_doc)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        report = render_schema_diff(
            schema_front_diff(a_front, b_front),
            args.a_label or args.a, args.b_label or args.b)
        if args.out:
            Path(args.out).parent.mkdir(parents=True,
                                        exist_ok=True)
            Path(args.out).write_text(report)
            print(f"drift report written: {args.out}")
        else:
            print(report)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
