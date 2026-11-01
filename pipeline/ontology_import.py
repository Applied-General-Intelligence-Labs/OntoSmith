"""Ontology import: an external OWL/RDF ontology -> a builder
draft, through an explicit scoping spec.

Track 3's builder drafts a schema from a corpus profile and a
brief. This module is the BYO-schema door: a user who already
has an ontology — a public standard like FIBO, or their own —
imports it instead. The import is two artifacts, both data:

  1. An INVENTORY of the source ontology: every declared
     class and property with its IRI, label, definition,
     parents, and (for properties) domain/range, extracted
     mechanically from the source files. Producing it is a
     small extractor's job (the FIBO one is stdlib-only); this
     module consumes the JSON shape, whatever produced it.

  2. A SCOPING SPEC: the human judgment, declared per type.
     An OWL ontology is a hierarchy with axioms; a pack is a
     flat extraction contract. The spec says, per target type,
     which source concepts fold into it (kept / merged /
     flattened), which source concept's definition the type
     carries, and the boundary material the source does not
     have. Everything in the scoped inventory that no target
     claims must be accounted for — demoted (datatype
     properties have no channel in a flat contract) or dropped
     with a stated reason, party-role classes converted to the
     relations that carry them. Unaccounted concepts are an
     error, never a silent omission: the scoping record the
     import emits is the complete account.

The output is an ordinary builder draft (kind
``ontology_schema_draft``) plus the scoping record. The draft
goes through the same validate -> approve -> emit gates as
any drafted schema — importing buys no exemption from the
human approval the builder exists to record. Definitions
the source states are carried verbatim (with the source's
license and attribution recorded in the spec and the record);
definitions the source does not state — boundary material
everywhere, and the types grounded in a library the source
only references — are authored in the spec and recorded as
authored, never dressed as the source's words.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .ontology_builder import DRAFT_KIND

IMPORTER_VERSION = "ontology-import/1.0"
SPEC_KIND = "ontology_import_spec"
RECORD_KIND = "ontology_scoping_record"

_ACTIONS = ("kept", "merged", "flattened")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _local(iri: str) -> str:
    return iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def merge_inventories(inventories: list[dict]) -> dict:
    """Flatten inventory documents to {iri: entry}.

    Real ontologies restate concepts: FIBO's Contracts file,
    for instance, re-declares three People classes to attach
    restrictions — the same IRI, declared in two files. Which
    declaration the import carries must never be a coin flip,
    so input order is the rule: the first declaration wins,
    verbatim, and the rest are named on the kept entry as
    ``also_declared_in``. The spec author controls the order
    (module files first, reference files after), and the
    scoping record shows every declaration site."""
    by_iri: dict[str, dict] = {}
    for doc in inventories:
        entries = doc if isinstance(doc, list) else doc.get("entries")
        if not isinstance(entries, list):
            raise ValueError("inventory document is not a list "
                             "of entries (nor an object with an "
                             "'entries' list)")
        for entry in entries:
            iri = entry.get("iri")
            if not iri:
                raise ValueError(f"inventory entry has no iri: "
                                 f"{entry!r}")
            if iri in by_iri:
                kept = by_iri[iri]
                also = kept.setdefault("also_declared_in", [])
                site = entry.get("source_file")
                if site and site not in also:
                    also.append(site)
                continue
            by_iri[iri] = dict(entry)
    return by_iri


def _ancestors(iri: str, by_iri: dict) -> list[str]:
    """Transitive parents of a concept, restricted to concepts
    the inventory itself declares (external parents are
    recorded separately by the caller from the raw parent
    list). Breadth-first, declaration order preserved."""
    seen: list[str] = []
    queue = list(by_iri[iri].get("parents") or [])
    known = set()
    while queue:
        parent = queue.pop(0)
        if parent in known:
            continue
        known.add(parent)
        if parent in by_iri:
            seen.append(parent)
            queue.extend(by_iri[parent].get("parents") or [])
    return seen


def _check_spec_shape(spec: dict) -> None:
    if not isinstance(spec, dict):
        raise ValueError("scoping spec is not a JSON object")
    if spec.get("kind") not in (None, SPEC_KIND):
        raise ValueError(f"spec kind {spec.get('kind')!r} is "
                         f"not {SPEC_KIND!r}")
    for field in ("family", "pack_id", "purpose"):
        if not str(spec.get(field) or "").strip():
            raise ValueError(f"scoping spec has no {field!r}")
    source = spec.get("source")
    if not isinstance(source, dict) or not source.get("name"):
        raise ValueError("scoping spec has no source object "
                         "with a name")
    if not source.get("scoped_files"):
        raise ValueError("scoping spec declares no scoped_files "
                         "— the module boundary coverage is "
                         "enforced over")


def build_import(spec: dict, inventories: list[dict]) -> tuple[dict, dict]:
    """Build (draft, scoping_record) from a scoping spec and
    one or more inventory documents. Fail-closed throughout:
    every malformed claim raises ValueError naming it, and
    every concept in the scoped files must be accounted for
    exactly once before a draft exists."""
    _check_spec_shape(spec)
    by_iri = merge_inventories(inventories)
    scoped_files = set(spec["source"]["scoped_files"])
    # iri -> (disposition, target type or None, reason or note)
    accounted: dict[str, tuple[str, str | None, str | None]] = {}

    def claim(iri: str, disposition: str, target: str | None,
              note: str | None) -> dict:
        entry = by_iri.get(iri)
        if entry is None:
            raise ValueError(
                f"spec names source concept {iri}, which is "
                f"not in the inventory")
        if iri in accounted:
            raise ValueError(
                f"source concept {iri} is accounted for twice "
                f"({accounted[iri][0]} and {disposition}) — "
                f"the scoping account must be exact")
        accounted[iri] = (disposition, target, note)
        return entry

    entities: list[dict] = []
    type_records: list[dict] = []

    def add_entity(target: dict, *, external: bool) -> None:
        name = target.get("type")
        if not str(name or "").strip():
            raise ValueError("spec entity target has no type name")
        action = target.get("action") or ("external" if external
                                          else None)
        sources = list(target.get("sources") or [])
        if external:
            if sources:
                raise ValueError(
                    f"external entity {name!r} declares "
                    f"inventory sources — it is external "
                    f"precisely because the source only "
                    f"references it")
            if not target.get("external_source"):
                raise ValueError(
                    f"external entity {name!r} names no "
                    f"external_source IRI")
            definition = target.get("definition")
            if not str(definition or "").strip():
                raise ValueError(
                    f"external entity {name!r} carries no "
                    f"authored definition — nothing else can "
                    f"supply one")
            definition_from = None
            provenance = "authored"
        else:
            if action not in _ACTIONS:
                raise ValueError(
                    f"entity {name!r} action {action!r} is not "
                    f"one of {_ACTIONS}")
            if action == "kept" and len(sources) != 1:
                raise ValueError(
                    f"entity {name!r} is 'kept' but names "
                    f"{len(sources)} sources — kept means one "
                    f"source concept, carried whole")
            if action in ("merged", "flattened") and len(sources) < 2:
                raise ValueError(
                    f"entity {name!r} is {action!r} but names "
                    f"fewer than two sources")
            definition_from = target.get("definition_from")
            if definition_from not in sources:
                raise ValueError(
                    f"entity {name!r} definition_from "
                    f"{definition_from!r} is not one of its "
                    f"sources")
            for iri in sources:
                entry = claim(iri, action, name,
                              target.get("note"))
                if entry.get("kind") != "class":
                    raise ValueError(
                        f"entity {name!r} source {iri} is a "
                        f"{entry.get('kind')}, not a class")
            definition = (by_iri[definition_from].get("definition")
                          or target.get("definition"))
            if not str(definition or "").strip():
                raise ValueError(
                    f"entity {name!r}: the source states no "
                    f"definition for {definition_from} and the "
                    f"spec authors none — a type may not ship "
                    f"definitionless")
            provenance = ("source" if by_iri[definition_from]
                          .get("definition") else "authored")
        entities.append({
            "type": name, "role": "entity",
            "definition": definition,
            "form_examples": target.get("form_examples") or [],
            "not_entities": target.get("not_entities") or {},
        })
        type_records.append({
            "type": name, "kind": "entity", "action": action,
            "source_iris": sources,
            "external_source": target.get("external_source"),
            "definition_from": definition_from,
            "definition_provenance": provenance,
            "note": target.get("note"),
        })

    for target in spec.get("entities") or []:
        add_entity(target, external=False)
    for target in spec.get("external_entities") or []:
        add_entity(target, external=True)

    relations: list[dict] = []
    for target in spec.get("relations") or []:
        name = target.get("type")
        if not str(name or "").strip():
            raise ValueError("spec relation target has no type name")
        action = target.get("action")
        sources = list(target.get("sources") or [])
        if action not in _ACTIONS:
            raise ValueError(
                f"relation {name!r} action {action!r} is not "
                f"one of {_ACTIONS}")
        if action == "kept" and len(sources) != 1:
            raise ValueError(
                f"relation {name!r} is 'kept' but names "
                f"{len(sources)} sources")
        if action in ("merged", "flattened") and len(sources) < 2:
            raise ValueError(
                f"relation {name!r} is {action!r} but names "
                f"fewer than two sources")
        definition_from = target.get("definition_from")
        if definition_from not in sources:
            raise ValueError(
                f"relation {name!r} definition_from "
                f"{definition_from!r} is not one of its "
                f"sources")
        for iri in sources:
            entry = claim(iri, action, name, target.get("note"))
            if entry.get("kind") != "object_property":
                raise ValueError(
                    f"relation {name!r} source {iri} is a "
                    f"{entry.get('kind')}, not an object "
                    f"property — datatype properties demote, "
                    f"they do not become relations")
        definition = (by_iri[definition_from].get("definition")
                      or target.get("definition"))
        if not str(definition or "").strip():
            raise ValueError(
                f"relation {name!r}: the source states no "
                f"definition for {definition_from} and the "
                f"spec authors none")
        entry_out = {
            "type": name,
            "definition": definition,
            "endpoint_signature":
                target.get("endpoint_signature") or [],
            "boundary_examples":
                target.get("boundary_examples") or [],
        }
        if target.get("inverse_of"):
            entry_out["inverse_of"] = target["inverse_of"]
        if target.get("direction"):
            entry_out["direction"] = target["direction"]
        relations.append(entry_out)
        type_records.append({
            "type": name, "kind": "relation", "action": action,
            "source_iris": sources,
            "external_source": None,
            "definition_from": definition_from,
            "definition_provenance": (
                "source" if by_iri[definition_from]
                .get("definition") else "authored"),
            "note": target.get("note"),
        })

    for conv in spec.get("role_conversions") or []:
        entry = claim(conv["iri"], "role_converted",
                      conv.get("into_relation"), conv.get("note"))
        if entry.get("kind") != "class":
            raise ValueError(
                f"role conversion {conv['iri']} is a "
                f"{entry.get('kind')}, not a class")
        if conv.get("into_relation") not in {
                r["type"] for r in relations}:
            raise ValueError(
                f"role conversion {conv['iri']} names relation "
                f"{conv.get('into_relation')!r}, which the spec "
                f"does not declare")

    for dem in spec.get("demoted") or []:
        claim(dem["iri"], "demoted", dem.get("into"),
              dem.get("note"))
    for drop in spec.get("dropped") or []:
        if not str(drop.get("reason") or "").strip():
            raise ValueError(
                f"dropped concept {drop.get('iri')} states no "
                f"reason — a drop without a reason is an "
                f"omission, not a scoping decision")
        claim(drop["iri"], "dropped", None, drop["reason"])

    unaccounted = [
        iri for iri, entry in by_iri.items()
        if entry.get("source_file") in scoped_files
        and iri not in accounted]
    if unaccounted:
        raise ValueError(
            "scoping is incomplete — "
            f"{len(unaccounted)} concept(s) in the scoped "
            f"files are accounted for nowhere: "
            + ", ".join(sorted(unaccounted)))

    concepts: list[dict] = []
    counts = {"kept": 0, "merged": 0, "flattened": 0,
              "role_converted": 0, "demoted": 0, "dropped": 0}
    for iri, entry in by_iri.items():
        if entry.get("source_file") not in scoped_files \
                and iri not in accounted:
            continue  # reference-file concept never drawn on
        disposition, target_name, note = accounted.get(
            iri, (None, None, None))
        if disposition in counts:
            counts[disposition] += 1
        parents = list(entry.get("parents") or [])
        concepts.append({
            "iri": iri,
            "label": entry.get("label"),
            "kind": entry.get("kind"),
            "source_file": entry.get("source_file"),
            "also_declared_in": entry.get("also_declared_in")
            or [],
            "action": disposition,
            "target": target_name,
            "reason": note,
            "parents": parents,
            "ancestors_in_source": _ancestors(iri, by_iri),
            "external_parents": [p for p in parents
                                 if p not in by_iri],
        })

    source = spec["source"]
    schema_front = {
        "purpose": spec["purpose"],
        "entity_inventory": entities,
        "relation_inventory": relations,
    }
    draft = {
        "kind": DRAFT_KIND,
        "builder_version": IMPORTER_VERSION,
        "created_at": _now(),
        "family": spec["family"],
        "pack_id": spec["pack_id"],
        "display_name": spec.get("display_name") or spec["pack_id"],
        "status": "DRAFT_NOT_APPROVED",
        "provenance": {
            "profile_hash": None,
            "corpus_family": None,
            "drafting_tier": "import",
            "drafting_source": f"ontology-import ({source['name']})",
            "brief": spec.get("brief"),
            "import_source": {
                "name": source["name"],
                "publisher": source.get("publisher"),
                "version": source.get("version"),
                "license": source.get("license"),
                "url": source.get("url"),
                "files": source.get("files"),
            },
        },
        "schema_front": schema_front,
        "entity_types": [e["type"] for e in entities],
        "relation_types": [r["type"] for r in relations],
        "merge_prohibited_type_pairs":
            spec.get("merge_prohibited_type_pairs") or [],
        "decision_points": spec.get("decision_points") or [],
    }
    from .ontology_builder import schema_hash
    record = {
        "kind": RECORD_KIND,
        "importer_version": IMPORTER_VERSION,
        "generated_at": _now(),
        "source": source,
        "family": spec["family"],
        "pack_id": spec["pack_id"],
        "schema_hash": schema_hash(draft),
        "counts": {
            **counts,
            "scoped_concepts": sum(counts.values()),
            "external_entities": len(
                spec.get("external_entities") or []),
            "entity_types": len(entities),
            "relation_types": len(relations),
        },
        "types": type_records,
        "concepts": concepts,
    }
    return draft, record


def load_json_file(path) -> object:
    try:
        text = Path(path).read_text()
    except FileNotFoundError as e:
        raise ValueError(f"{path} not found: {e}") from e
    except OSError as e:
        raise ValueError(f"{path} cannot be read: {e}") from e
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not valid JSON: {e}") from e
