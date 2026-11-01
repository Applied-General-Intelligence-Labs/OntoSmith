"""Profile setup_config layer.

The stage-0 corpus profile's setup_config.json is the config layer
that carries a corpus's ontology into the pipeline. Three jobs:

1. FAIL-CLOSED VALIDATION. Every key in a setup_config must have a
   registered consumer (CONSUMED_KEYS) or be declared metadata
   (METADATA_KEYS / any ``*_note`` annotation). Batch creation
   refuses a config with an unregistered key and names it. Before
   this registry existed, a key could sit in the profile forever
   with no reader: BioRED v1's entity_types / relation_types were
   consumed by NOTHING (the wiring
   audit) — the profile looked wired
   and was decoration. That state is now impossible at creation.

2. THE GUIDANCE CHANNEL. derive_guidance() renders the
   profile's schema into the guidance text stage_candidates threads
   into the proposer prompt: an explicit ``proposer_guidance``
   string wins verbatim; otherwise the ``schema_front`` inventory
   (entity types with definitions + boundary examples, relation
   inventory with endpoint signatures and extraction intents,
   merge prohibitions) is rendered deterministically. A profile
   with neither key yields "" — and with empty guidance the
   constructed proposer prompt is byte-identical to the
   pre-channel construction (pinned by
   scripts/test_profile_wiring.py).

3. TYPED MENTIONS. The helpers below define the type
   vocabulary candidates are validated against
   (endpoint_type_names) and the merge prohibitions WRITE's ER
   consults in BOTH channels (prohibited_type_pairs /
   type_pair_prohibited): config key
   ``merge_prohibited_type_pairs`` = a list of [typeA, typeB]
   unordered pairs that must never merge.

4. INDUSTRY PACKS (docs/PACK_DESIGN.md). The pack
   loader at the bottom of this module binds a versioned pack
   manifest (packs/<id>/pack.json) to the profile payload it
   pins by sha256 and returns the payload as a setup_config dict
   through this module's own fail-closed validation — the pack
   layer adds identity, versioning, and a conformance record
   around the channel, never a second content path.
   resolve_setup_config() layers the core/default pack
   under a specified pack (§1.3 resolution: the pack's
   declared keys govern, undeclared keys keep the core
   defaults the core pack carries).

5. DOMAIN FRAMING. The proposer and pair-judge
   base templates are domain-neutral core text; a family's
   domain framing (proposer_framing / slot_framing / slot_rules
   / pair_judge_generic_relation) is profile data, accessed via
   the helpers at the bottom of this module and threaded by
   stage_candidates. Before this, the core templates carried
   one family's (CUAD's) contract text as the compiled-in
   default, and the pair judge's evidence template named
   BioRED's "Association" in core code.
"""
from __future__ import annotations

from pathlib import Path

# Keys a pipeline stage actually reads. Value = the consumer, for
# humans auditing this registry.
CONSUMED_KEYS: dict[str, str] = {
    # CANDIDATES (stage_candidates)
    "label_categories": "CANDIDATES slot categories "
                        "(proposer.propose_slots_for_doc)",
    "role_word_rules_from_profile": "CANDIDATES role-word guard "
                                     "switch (proposer.guard_triples)",
    "role_words": "CANDIDATES role-word guard vocabulary "
                  "(proposer.guard_triples), resolved from the "
                  "declaring pack's role_words artefact "
                  "(pack_role_words; frozen into the resolved "
                  "config by resolve_setup_config)",
    "proposer_guidance": "CANDIDATES -> proposer prompt guidance "
                         "(derive_guidance)",
    "schema_front": "CANDIDATES -> proposer prompt guidance + "
                    "endpoint type vocabulary "
                    "(derive_guidance / endpoint_type_names)",
    "entity_types": "CANDIDATES endpoint-type validation vocabulary "
                    "(endpoint_type_names) and guidance "
                    "vocabulary when no schema_front is declared "
                    "(derive_guidance fallback rendering)",
    "relation_types": "CANDIDATES guidance vocabulary when no "
                      "schema_front is declared (derive_guidance "
                      "fallback rendering: closed relation list)",
    # CANDIDATES pair-enumeration branch: with
    # pair_enumeration on, stage_candidates replaces
    # the free generative proposer with the roster pair census
    # (pipeline/pair_proposer.py); pair_window selects the enumeration
    # window; roster_pass declares that the batch's roster file is
    # the mention-first roster-pass product (roster_v2.jsonl).
    "pair_enumeration": "CANDIDATES pair-enumeration branch switch "
                        "(stage_candidates)",
    "pair_window": "CANDIDATES pair-enumeration window: "
                   "'sentence' | 'adjacent' (stage_candidates)",
    "roster_pass": "CANDIDATES roster source declaration: true = "
                   "the batch roster is roster_v2.jsonl, built by "
                   "the mention-first roster pass "
                   "(pipeline/roster_pass.py; stage_candidates)",
    # CANDIDATES pair-judge variant: the
    # factorial's winning judge (abstract context x evidence
    # instruction) selected by setup_config; absent = the
    # the default judge (sentence x standard), byte-identical.
    "pair_judge_context": "CANDIDATES pair-enumeration judge "
                          "context: 'sentence' | 'abstract' "
                          "(stage_candidates -> "
                          "pair_proposer.build_pair_prompt_judge)",
    "pair_judge_instruction": "CANDIDATES pair-enumeration judge "
                              "instruction: 'standard' | 'evidence' "
                              "(stage_candidates -> "
                              "pair_proposer.build_pair_prompt_judge)",
    "pair_judge_format": "CANDIDATES pair-enumeration judge "
                         "answer format: 'jf1' = the JF-F1 "
                         "direction slot (stage_candidates -> "
                         "pair_proposer.propose_pairs_for_doc "
                         "jf1=True). Absent = "
                         "the legacy answer format, byte-identical.",
    "pair_judge_symmetric_source": "CANDIDATES pair-enumeration "
                                   "judge: path (repo-root relative "
                                   "or absolute) of the pinned "
                                   "direction-declarations artefact "
                                   "the JF-F1 symmetric relation set "
                                   "is read from (stage_candidates; "
                                   "the family pack's declarations "
                                   "artefact)",
    # CANDIDATES emission-guard relaxations: registered so a
    # pack payload may declare them; absent = the frozen legacy
    # behavior, so registering the keys changes no run that does
    # not declare them).
    "pair_abbrev_guard": "CANDIDATES pair-enumeration sentence "
                         "splitter abbreviation guard "
                         "(stage_candidates -> "
                         "pair_proposer.split_sentences_r3 "
                         "abbrev_guard)",
    "pair_theme_attest_sentence": "CANDIDATES pair-item theme "
                                  "attestation reads the posed "
                                  "pair sentence instead of the "
                                  "item span (stage_candidates "
                                  "guards)",
    "quote_punct_insensitive": "CANDIDATES quote verification "
                               "accepts punctuation-spacing-"
                               "equivalent quotes (stage_candidates "
                               "guards)",
    "identity_seat_decisions": "CANDIDATES identity seat "
                               "decisions file: path (batch-root "
                               "relative or absolute) of the frozen "
                               "JSONL verdicts an offline judge "
                               "produced for rule 4 of identity "
                               "resolution (stage_candidates -> "
                               "proposer.guard_triples "
                               "identity_seat); absent = the "
                               "deterministic rule chain alone",
    "theme_attest_chunk": "CANDIDATES theme-presence guard "
                          "accepts a verb+preposition relation's "
                          "object attested anywhere in the "
                          "proposal's chunk, not only inside the "
                          "shortest quote (stage_candidates -> "
                          "proposer.guard_triples, for packs "
                          "whose relations are verb+preposition "
                          "forms — absent = the quote-only test)",
    # Domain framing for the proposer base prompts: the core
    # templates in
    # pipeline/proposer.py and pipeline/pair_proposer.py are
    # domain-neutral; a family's domain framing is profile data,
    # never a core literal. Absent = the neutral core default.
    "proposer_framing": "CANDIDATES free-proposer triple prompt "
                        "opening line (stage_candidates -> "
                        "proposer.propose_triples_for_doc)",
    "proposer_max_per_chunk": "CANDIDATES free-proposer "
                              "per-chunk proposal ceiling "
                              "(stage_candidates -> "
                              "proposer.propose_triples_for_doc "
                              "max_per_chunk; absent = the core "
                              "default MAX_PER_CHUNK)",
    "mention_layer": "CANDIDATES->GATES mention layer "
                     "(issue #54): the free proposer emits "
                     "per-endpoint mention spans + types + "
                     "verbatim antecedent resolutions, the "
                     "guards verify both spans, and GATES "
                     "judges each resolution before the fact "
                     "(stage_candidates / stage_gates -> "
                     "proposer.mention_layer_enabled; absent "
                     "= off, byte-identical legacy path)",
    "entity_sweep": "CANDIDATES entity sweep (issue #58): "
                    "an additive, entity-centered second "
                    "proposal pass inside the free-proposer "
                    "branch — a deterministic code census "
                    "names the document's entities, the ones "
                    "that appear as no endpoint of any "
                    "chunk-pass item become anchors, and each "
                    "anchor is re-asked in its own mention "
                    "context under the chunk prompt plus one "
                    "anchor rule; sweep items pass the same "
                    "guards and merge behind the chunk "
                    "channel, which is never shadowed "
                    "(stage_candidates -> "
                    "proposer.entity_sweep_enabled / "
                    "proposer.propose_sweep_for_doc; absent "
                    "= off, byte-identical legacy path)",
    "slot_framing": "CANDIDATES clause-slot prompt opening line "
                    "(stage_candidates -> "
                    "proposer.propose_slots_for_doc)",
    "slot_rules": "CANDIDATES clause-slot prompt rules block "
                  "(stage_candidates -> "
                  "proposer.propose_slots_for_doc)",
    "pair_judge_generic_relation": "CANDIDATES pair-judge evidence "
                                   "instruction: the inventory's "
                                   "generic-member relation name "
                                   "(stage_candidates -> "
                                   "pair_proposer."
                                   "build_pair_prompt_judge)",
    # ROUTE (stage_route)
    "c1_whitelist": "ROUTE Arm C C1 demotions (_c1_whitelist)",
    # WRITE (stage_write)
    "relation_map": "WRITE relation normalization "
                    "(RelationNormalizer profile_map)",
    "merge_prohibited_type_pairs": "WRITE ER merge prohibitions, "
                                   "both channels "
                                   "(prohibited_type_pairs)",
}

# Declared metadata: provenance and batch-parameter declarations.
# These keys carry no pipeline behavior promise — batch parameters
# are BatchConfig values; the profile records the declared values
# the batch was authored under — but they are legitimate profile
# content, so validation accepts them explicitly rather than by
# silence. Any OTHER unregistered key fails validation.
METADATA_KEYS: set[str] = {
    "family", "profile_version", "profile_hash", "corpus_scope",
    "consumption_note",
    # industry-pack declaration (docs/PACK_DESIGN.md):
    # a setup_config may declare the pack it is the payload of;
    # the declaration is provenance metadata in v0 — the pack
    # manifest (packs/<id>/pack.json) is the identity record.
    "pack_id", "pack_version",
    # batch-parameter declarations (mirrored into BatchConfig)
    "triple_threshold", "review_band", "er_threshold",
    "er_cross_doc_min_p_same", "chunk_window_chars",
    "chunk_min_chars", "route_baseline", "er_auto_merge",
    "er_alias_sources", "batch_cost_cap_usd",
    # LEGACY declarations: inert
    # mirrors that NAME connection-layer bindings — the live
    # embed binding is BatchConfig.embed_model (defaulted from
    # the reference profile) and the live provider routing is
    # the transports' reference-profile preset. Accepted only
    # so the shipped hash-pinned payloads keep validating;
    # dropped from payloads at their next version bump, after
    # which these two entries leave the registry too.
    "embed_model", "provider_sort",
    # observed-corpus annotations (setup-service records)
    "redaction_markers_observed",
}


def _is_registered(key: str) -> bool:
    return (key in CONSUMED_KEYS or key in METADATA_KEYS
            or key.endswith("_note"))


def setup_config_errors(cfg: dict) -> list[str]:
    """Fail-closed validation of one setup_config dict.

    Returns a list of human-readable errors (empty = valid). Every
    unregistered key is named — an unconsumed key is exactly the
    silent-no-op failure these keys were declared for.
    """
    errors: list[str] = []
    if not isinstance(cfg, dict):
        return [f"setup_config is not a JSON object "
                f"({type(cfg).__name__})"]
    for key in cfg:
        if not _is_registered(key):
            errors.append(
                f"setup_config key {key!r} has no registered "
                f"consumer (pipeline/profile_config.py CONSUMED_KEYS / "
                f"METADATA_KEYS) — register it, declare it metadata, "
                f"or remove it; an unconsumed key is a silent no-op")
    g = cfg.get("proposer_guidance")
    if g is not None and not isinstance(g, str):
        errors.append("setup_config key 'proposer_guidance' must be "
                      "a string")
    sf = cfg.get("schema_front")
    if sf is not None and not isinstance(sf, dict):
        errors.append("setup_config key 'schema_front' must be an "
                      "object with entity_inventory / "
                      "relation_inventory")
    if isinstance(sf, dict):
        # Directionality declarations (schema data consumed
        # by _render_schema_front): values are a closed vocabulary,
        # validated here so a typo fails closed at creation rather
        # than silently rendering the default statement.
        rd = sf.get("relation_directionality")
        if rd is not None and rd not in ("directed", "undirected"):
            errors.append(
                "schema_front key 'relation_directionality' must "
                "be 'directed' or 'undirected'")
        for r in sf.get("relation_inventory") or []:
            if not isinstance(r, dict):
                continue
            d = r.get("direction")
            if d is not None and d not in ("directed", "symmetric"):
                errors.append(
                    f"schema_front relation {r.get('type')!r}: "
                    f"'direction' must be 'directed' or "
                    f"'symmetric'")
            inv = r.get("inverse_of")
            if inv is not None and not (isinstance(inv, str)
                                        and inv.strip()):
                errors.append(
                    f"schema_front relation {r.get('type')!r}: "
                    f"'inverse_of' must be a non-empty relation "
                    f"name")
    for bkey in ("pair_enumeration", "roster_pass",
                 "pair_abbrev_guard", "pair_theme_attest_sentence",
                 "quote_punct_insensitive", "theme_attest_chunk"):
        bv = cfg.get(bkey)
        if bv is not None and not isinstance(bv, bool):
            errors.append(
                f"setup_config key {bkey!r} must be a boolean")
    rw = cfg.get("role_words")
    if rw is not None and not (
            isinstance(rw, (list, tuple)) and rw and all(
                isinstance(w, str) and w.strip() for w in rw)):
        errors.append(
            "setup_config key 'role_words' must be a non-empty "
            "list of non-empty strings (the role-word guard "
            "vocabulary, resolved from pack data)")
    pw = cfg.get("pair_window")
    if pw is not None and pw not in ("sentence", "adjacent"):
        errors.append(
            "setup_config key 'pair_window' must be 'sentence' or "
            "'adjacent'")
    pjc = cfg.get("pair_judge_context")
    if pjc is not None and pjc not in ("sentence", "abstract"):
        errors.append(
            "setup_config key 'pair_judge_context' must be "
            "'sentence' or 'abstract'")
    pji = cfg.get("pair_judge_instruction")
    if pji is not None and pji not in ("standard", "evidence"):
        errors.append(
            "setup_config key 'pair_judge_instruction' must be "
            "'standard' or 'evidence'")
    pjf = cfg.get("pair_judge_format")
    if pjf is not None and pjf != "jf1":
        errors.append(
            "setup_config key 'pair_judge_format' must be 'jf1' "
            "(the only registered format; omit the key for the "
            "legacy format)")
    pss = cfg.get("pair_judge_symmetric_source")
    if pss is not None and not (isinstance(pss, str) and pss.strip()):
        errors.append(
            "setup_config key 'pair_judge_symmetric_source' must "
            "be a non-empty string (path to the pinned direction-"
            "declarations artefact)")
    if pjf == "jf1" and not (isinstance(pss, str) and pss.strip()):
        errors.append(
            "setup_config key 'pair_judge_format' is 'jf1' but "
            "'pair_judge_symmetric_source' is not declared — the "
            "JF-F1 symmetric set must come from a pinned pack "
            "artefact, never from core code (fail closed)")
    for fkey in ("proposer_framing", "slot_framing", "slot_rules",
                 "pair_judge_generic_relation"):
        fv = cfg.get(fkey)
        if fv is not None and not (isinstance(fv, str) and fv.strip()):
            errors.append(
                f"setup_config key {fkey!r} must be a non-empty "
                f"string (domain framing carried as profile data)")
    pairs = cfg.get("merge_prohibited_type_pairs")
    if pairs is not None:
        ok = isinstance(pairs, list) and all(
            isinstance(p, (list, tuple)) and len(p) == 2
            and all(isinstance(t, str) and t for t in p)
            and p[0] != p[1]
            for p in pairs)
        if not ok:
            errors.append(
                "setup_config key 'merge_prohibited_type_pairs' "
                "must be a list of [typeA, typeB] string pairs with "
                "typeA != typeB")
    return errors


def entity_type_names(cfg: dict | None) -> set[str]:
    """The profile's declared entity-type vocabulary.

    Accepts both authored shapes: a list of type-name strings, or a
    list of {"type": name, ...} objects.
    """
    out: set[str] = set()
    for e in (cfg or {}).get("entity_types") or []:
        if isinstance(e, str) and e:
            out.add(e)
        elif isinstance(e, dict) and e.get("type"):
            out.add(str(e["type"]))
    return out


def endpoint_type_names(cfg: dict | None) -> set[str]:
    """Types a relation endpoint may carry (the validation set).

    When the profile carries a schema_front, its entity inventory
    is authoritative: entries whose role is not "entity" (e.g.
    OrganismTaxon, role qualifier_never_entity) are NOT
    endpoint types — a proposer tagging an endpoint with one has
    the tag dropped at the guards, exactly like an unknown type.
    Without a schema_front, the declared entity_types vocabulary
    is the set.
    """
    cfg = cfg or {}
    sf = cfg.get("schema_front")
    if isinstance(sf, dict) and sf.get("entity_inventory"):
        return {str(e["type"]) for e in sf["entity_inventory"]
                if isinstance(e, dict) and e.get("type")
                and e.get("role", "entity") == "entity"}
    return entity_type_names(cfg)


def prohibited_type_pairs(cfg: dict | None) -> set[frozenset]:
    """Configured merge prohibitions as a set of unordered pairs."""
    out: set[frozenset] = set()
    for p in (cfg or {}).get("merge_prohibited_type_pairs") or []:
        if isinstance(p, (list, tuple)) and len(p) == 2:
            out.add(frozenset((str(p[0]), str(p[1]))))
    return out


def type_pair_prohibited(type_a: str, type_b: str,
                         pairs: set[frozenset]) -> bool:
    """True iff both types are present, differ, and the unordered
    pair is configured prohibited. An untyped side (empty string)
    is never prohibited — untyped pairs keep legacy behavior."""
    if not type_a or not type_b or type_a == type_b or not pairs:
        return False
    return frozenset((type_a, type_b)) in pairs


def _render_schema_front(sf: dict) -> str:
    """Deterministic rendering of a schema_front inventory into
    proposer guidance text. Data-driven: every string below comes
    from the profile except the fixed framing/instructions."""
    lines: list[str] = []
    lines.append(
        "## Corpus schema (from this corpus's profile — binding)")
    lines.append(
        "The excerpt is NOT a commercial contract excerpt; it is a "
        "document from the corpus this schema describes. Where the "
        "generic instructions above conflict with this schema, this "
        "schema wins.")
    entities = [e for e in sf.get("entity_inventory") or []
                if isinstance(e, dict) and e.get("type")]
    if entities:
        lines.append("")
        lines.append("### Entity types (closed inventory)")
        lines.append(
            "Extract ONLY entities of the types marked [entity]. "
            "Report each endpoint's type as \"subject_type\" / "
            "\"object_type\" using the exact type name.")
        not_entities: dict[str, list[str]] = {}
        for e in entities:
            role = e.get("role", "entity")
            head = f"- {e['type']} [{role}]: {e.get('definition', '')}"
            lines.append(head)
            forms = [str(x) for x in e.get("form_examples") or []]
            if forms:
                lines.append(f"  Typical forms: {'; '.join(forms)}")
            if role != "entity":
                lines.append(
                    "  NEVER an entity and NEVER a relation "
                    "endpoint; do not extract it.")
            for cat, examples in (e.get("not_entities")
                                  or {}).items():
                bucket = not_entities.setdefault(str(cat), [])
                for ex in examples or []:
                    if str(ex) not in bucket:
                        bucket.append(str(ex))
        if not_entities:
            lines.append("")
            lines.append("### Never entities (any type)")
            lines.append(
                "The following are NOT entities and must never "
                "appear as a subject or object:")
            for cat, examples in not_entities.items():
                lines.append(
                    f"- {cat.replace('_', ' ')}: "
                    f"{'; '.join(examples)}")
    relations = [r for r in sf.get("relation_inventory") or []
                 if isinstance(r, dict) and r.get("type")]
    if relations:
        lines.append("")
        lines.append("### Relation inventory (closed)")
        # The directionality statement is schema DATA, not
        # a core constant. A schema_front declaring
        # relation_directionality="directed" gets the directed
        # statement; an undeclared schema_front (and one declaring
        # "undirected") gets the historical sentence byte-for-byte,
        # so frozen guidance (BioRED's, whose profile records the
        # undirected decision) is unchanged.
        if sf.get("relation_directionality") == "directed":
            dir_stmt = (
                "Relations are directed: report the subject and "
                "object in the direction the relation holds — do "
                "not swap endpoints; the inventory marks the "
                "symmetric exceptions.")
        else:
            dir_stmt = ("Relations are undirected: endpoint order "
                        "does not matter.")
        lines.append(
            "Extract ONLY these relations, and report \"relation\" "
            "as the exact inventory name (not a verb phrase from "
            f"the text). {dir_stmt}")
        for r in relations:
            lines.append(f"- {r['type']}: {r.get('definition', '')}")
            sig = r.get("endpoint_signature") or []
            if sig:
                lines.append(
                    f"  Endpoint signature: {'; '.join(str(s) for s in sig)}")
            if r.get("signature_note"):
                lines.append(f"  {r['signature_note']}")
            # Per-relation direction declarations render as
            # note lines when the payload declares them; payloads
            # declaring neither field render exactly as before.
            if r.get("direction") == "symmetric":
                lines.append(
                    "  Direction: symmetric — endpoint order does "
                    "not matter for this relation.")
            if r.get("inverse_of"):
                lines.append(
                    f"  Inverse: {r['inverse_of']} — the same fact "
                    "in the opposite direction is stated with that "
                    "relation and the endpoints swapped.")
            if r.get("extraction_intent"):
                lines.append(
                    f"  Extraction intent: {r['extraction_intent']}")
    prohibitions = [p for p in sf.get("merge_prohibitions") or []
                    if isinstance(p, dict) and p.get("rule")]
    if prohibitions:
        lines.append("")
        lines.append("### Merge prohibitions (identity rules)")
        for p in prohibitions:
            pid = f"{p['id']}: " if p.get("id") else ""
            lines.append(f"- {pid}{p['rule']}")
        lines.append(
            "These bind extraction too: never report one surface "
            "under two different types.")
    lines.append("")
    lines.append(
        "Output shape per triple: {\"subject\": ..., "
        "\"subject_type\": ..., \"relation\": <exact inventory "
        "name>, \"object\": ..., \"object_type\": ..., "
        "\"quote\": ...}. The quote rules above are unchanged: "
        "shortest verbatim phrase, character-for-character.")
    return "\n".join(lines)


def _render_vocabularies(cfg: dict) -> str:
    """Fallback guidance for a profile that declares type/relation
    vocabularies but no schema_front: the closed inventories alone,
    without definitions. Keeps entity_types / relation_types real
    consumers of the guidance channel for such profiles."""
    lines = ["## Corpus schema (from this corpus's profile — "
             "binding)",
             "The excerpt is NOT a commercial contract excerpt; "
             "it is a document from the corpus this profile "
             "describes. Where the generic instructions above "
             "conflict with this schema, this schema wins."]
    etypes = sorted(entity_type_names(cfg))
    if etypes:
        lines.append("")
        lines.append("### Entity types (closed inventory)")
        lines.append(
            "Extract ONLY entities of these types. Report each "
            "endpoint's type as \"subject_type\" / \"object_type\" "
            "using the exact type name.")
        for t in etypes:
            lines.append(f"- {t}")
    rtypes = [str(r) for r in cfg.get("relation_types") or []]
    if rtypes:
        lines.append("")
        lines.append("### Relation inventory (closed)")
        lines.append(
            "Extract ONLY these relations, and report \"relation\" "
            "as the exact inventory name (not a verb phrase from "
            "the text).")
        for t in rtypes:
            lines.append(f"- {t}")
    lines.append("")
    lines.append(
        "Output shape per triple: {\"subject\": ..., "
        "\"subject_type\": ..., \"relation\": <exact inventory "
        "name>, \"object\": ..., \"object_type\": ..., "
        "\"quote\": ...}. The quote rules above are unchanged: "
        "shortest verbatim phrase, character-for-character.")
    return "\n".join(lines)


# ------------------------------------------------------------------
# Industry packs (docs/PACK_DESIGN.md).
#
# A pack is the versioned home of one domain's content. Its
# manifest (packs/<pack_id>/pack.json) names the profile payload
# it binds — a setup_config file, pinned by sha256 — so the pack
# never forks content: the payload stays the single source the
# profile channel consumes, and the pin is the definition-fixing
# rule made executable. Loading is fail-closed in the #51 sense:
# a malformed manifest, a payload whose bytes no longer match
# the pin, or a payload that fails setup_config validation all
# refuse to load, naming the reason. A pack whose pin verifies
# and whose payload validates loads to exactly the setup_config
# dict the channel already consumes — byte-identical behavior is
# proven by scripts/test_pack_v0.py.
#
# RESOLUTION. resolve_setup_config()
# layers the core/default pack (packs/core/) under a
# specified domain pack: the core payload is the baseline,
# and for each key the specified pack declares, the pack's
# value governs — for every key it does not declare, the
# core default governs (docs/PACK_DESIGN.md §1.3).
# Both layers load through pack_setup_config, so both pins
# verify and both payloads validate before the merge; the
# merged dict is validated again, and any failure refuses the
# whole resolution — there is no fallback to the baseline
# under a specified pack's name (§1.4).
# ------------------------------------------------------------------

_PACK_REQUIRED_FIELDS = ("pack_format", "pack_id", "pack_version",
                         "profile_family", "payload")
_PACK_PAYLOAD_REQUIRED = ("kind", "path", "sha256")


def _pack_root(pack_dir) -> Path:
    return Path(pack_dir).resolve().parent.parent


def load_pack_manifest(pack_dir) -> dict:
    """Parse + structurally validate a pack manifest.

    Fail-closed: a missing file, invalid JSON, a missing required
    field, or an unsupported pack_format raises ValueError naming
    the defect. The manifest is then validated against the pack
    schema (packs/pack.schema.json, via pipeline.pack_schema —
    the field-level definition of pack_format 1): any schema
    defect — a bad enum value, a malformed pin, a field the
    format does not define — also refuses, naming each defect.
    The payload pin is verified by pack_setup_config, not here
    (manifest-level checks only)."""
    import json
    from pathlib import Path
    pack_dir = Path(pack_dir)
    mpath = pack_dir / "pack.json"
    if not mpath.exists():
        raise ValueError(f"pack manifest not found: {mpath}")
    try:
        manifest = json.loads(mpath.read_text())
    except Exception as e:
        raise ValueError(f"pack manifest {mpath} is not valid "
                         f"JSON: {e}") from e
    if not isinstance(manifest, dict):
        raise ValueError(f"pack manifest {mpath} is not a JSON "
                         f"object")
    for f in _PACK_REQUIRED_FIELDS:
        if f not in manifest:
            raise ValueError(f"pack manifest {mpath} is missing "
                             f"required field {f!r}")
    if manifest["pack_format"] != 1:
        raise ValueError(f"pack {manifest.get('pack_id')!r}: "
                         f"unsupported pack_format "
                         f"{manifest['pack_format']!r} (this loader "
                         f"reads format 1)")
    payload = manifest["payload"]
    if not isinstance(payload, dict):
        raise ValueError(f"pack {manifest['pack_id']!r}: payload "
                         f"is not an object")
    for f in _PACK_PAYLOAD_REQUIRED:
        if f not in payload:
            raise ValueError(f"pack {manifest['pack_id']!r}: "
                             f"payload is missing {f!r}")
    if payload["kind"] != "setup_config":
        raise ValueError(f"pack {manifest['pack_id']!r}: payload "
                         f"kind {payload['kind']!r} is not "
                         f"'setup_config' (format 1 carries one "
                         f"setup_config payload)")
    from . import pack_schema
    defects = pack_schema.validate_pack_manifest(
        manifest, hint_dirs=[mpath.parent.parent])
    if defects:
        raise ValueError(
            f"pack manifest {mpath} fails the pack schema "
            f"(packs/pack.schema.json): "
            + "; ".join(defects))
    return manifest


def pack_setup_config(pack_dir) -> dict:
    """The pack's payload as a setup_config dict, pin-verified.

    The payload bytes must hash to the manifest's pin (a drifted
    payload is a different pack version, never a silent edit) and
    must pass the same fail-closed setup_config validation as any
    profile config. Any failure raises ValueError naming it."""
    import hashlib
    import json
    manifest = load_pack_manifest(pack_dir)
    root = _pack_root(pack_dir)
    ppath = root / manifest["payload"]["path"]
    if not ppath.exists():
        raise ValueError(f"pack {manifest['pack_id']!r}: payload "
                         f"file not found: {ppath}")
    digest = hashlib.sha256(ppath.read_bytes()).hexdigest()
    if digest != manifest["payload"]["sha256"]:
        raise ValueError(
            f"pack {manifest['pack_id']!r}: payload pin mismatch "
            f"— manifest pins sha256:{manifest['payload']['sha256'][:16]}… "
            f"but {ppath} hashes to sha256:{digest[:16]}…; a "
            f"changed payload is a new pack version, not an edit")
    try:
        payload_text = ppath.read_text()
    except FileNotFoundError as e:
        raise ValueError(f"pack {manifest['pack_id']!r}: payload "
                         f"file not found: {ppath}") from e
    try:
        cfg = json.loads(payload_text)
    except Exception as e:
        raise ValueError(f"pack {manifest['pack_id']!r}: payload "
                         f"{ppath} is not valid JSON: {e}") from e
    errs = setup_config_errors(cfg)
    if errs:
        raise ValueError(f"pack {manifest['pack_id']!r}: payload "
                         f"fails setup_config validation: "
                         + "; ".join(errs))
    return cfg


def pack_role_words(pack_dir) -> frozenset | None:
    """The pack's role-word guard vocabulary, or None.

    The vocabulary is pack DATA: a pack whose payload
    declares ``role_word_rules_from_profile`` ships the word
    set as a ``role_words`` artefact in its manifest, sha256
    -pinned like the payload (the fitted-qualifiers route).
    Returns None when the manifest lists no such
    artefact. Fail-closed: a listed artefact that is missing,
    malformed, or pin-drifted raises ValueError naming the
    defect — a declared guard never runs with a guessed
    vocabulary.
    """
    import hashlib
    import json
    manifest = load_pack_manifest(pack_dir)
    root = _pack_root(pack_dir)
    for art in manifest.get("artefacts") or []:
        if not isinstance(art, dict) \
                or art.get("kind") != "role_words":
            continue
        apath = root / art["path"]
        if not apath.exists():
            raise ValueError(
                f"pack {manifest['pack_id']!r}: role_words "
                f"artefact not found: {apath}")
        digest = hashlib.sha256(apath.read_bytes()).hexdigest()
        if art.get("sha256") and digest != art["sha256"]:
            raise ValueError(
                f"pack {manifest['pack_id']!r}: role_words "
                f"artefact pin mismatch — manifest pins "
                f"sha256:{art['sha256'][:16]}… but {apath} "
                f"hashes to sha256:{digest[:16]}…")
        try:
            artefact_text = apath.read_text()
        except FileNotFoundError as e:
            raise ValueError(
                f"pack {manifest['pack_id']!r}: role_words "
                f"artefact not found: {apath}") from e
        try:
            data = json.loads(artefact_text)
        except Exception as e:
            raise ValueError(
                f"pack {manifest['pack_id']!r}: role_words "
                f"artefact {apath} is not valid JSON: {e}") from e
        words = data.get("words") if isinstance(data, dict) \
            else None
        if not isinstance(words, list) or not words or not all(
                isinstance(w, str) and w.strip() for w in words):
            raise ValueError(
                f"pack {manifest['pack_id']!r}: role_words "
                f"artefact {apath} must carry a non-empty "
                f"'words' list of non-empty strings")
        return frozenset(" ".join(w.split()).lower()
                         for w in words)
    return None


def effective_role_words(root, cfg) -> frozenset | None:
    """The role-word vocabulary a run executes with, or None.

    Resolution order: (1) the run's setup_config already
    carries the resolved vocabulary — the frozen
    ``setup_config.resolved.json`` batch creation writes;
    (2) the binding pack's ``role_words`` artefact, found by
    the config's declared family. None when the config
    declares no role-word rules (the guard is off). A config
    that declares the rules with no resolvable vocabulary
    raises — fail-closed: the guard never silently runs
    empty.
    """
    if not cfg or not cfg.get("role_word_rules_from_profile"):
        return None
    declared = cfg.get("role_words")
    if declared:
        return frozenset(declared)
    family = cfg.get("family")
    pdir = pack_dir_for_family(root, family) if family else None
    vocab = pack_role_words(pdir) if pdir is not None else None
    if vocab is None:
        raise ValueError(
            "setup_config declares role_word_rules_from_profile "
            f"but no role_words vocabulary resolves (family "
            f"{family!r}); the declaring pack must ship a "
            "role_words artefact")
    return vocab


def installed_packs_dir() -> Path:
    """The pack tree of an installed (wheel) layout.

    The packaging ships the packs as data files under
    ``share/ontosmith/packs`` (pyproject data-files); pip
    places them at the interpreter's sysconfig ``data``
    path. A source checkout never needs this — its own
    ``packs/`` resolves first (packs_dir_candidates) —
    but a wheel install has no ``packs/`` beside the code
    at all, so this is the only tree it can resolve from."""
    import sysconfig
    from pathlib import Path
    return Path(sysconfig.get_path("data")) / "share" / \
        "ontosmith" / "packs"


def packs_dir_candidates(root) -> list:
    """Every ``packs/`` directory a lookup may consult, in
    resolution order: ``root/packs`` first (a source tree
    or explicit root always wins), then the installed
    data-files tree when it is a different directory.

    Missing directories are simply absent from the list —
    a lookup that finds no candidate behaves exactly as
    before (fail-closed refusals, None returns)."""
    from pathlib import Path
    candidates = []
    local = Path(root) / "packs"
    if local.is_dir():
        candidates.append(local)
    installed = installed_packs_dir()
    if installed.is_dir() and \
            installed.resolve() != local.resolve():
        candidates.append(installed)
    return candidates


def _core_pack_dir(root, pack_dir=None) -> Path:
    """Locate the core (baseline) pack directory.

    The baseline that governs a resolution is the core
    pack of the same tree the specified pack came from,
    when that tree carries one (an installed run resolves
    entirely inside its installed tree); otherwise the
    first candidate tree carrying a core pack, in
    packs_dir_candidates order. When no tree carries one,
    the ``root/packs/core`` path is
    returned so the loader's refusal names the expected
    location."""
    from pathlib import Path
    if pack_dir is not None:
        sibling = Path(pack_dir).parent / "core"
        if (sibling / "pack.json").exists():
            return sibling
    for packs_dir in packs_dir_candidates(root):
        core = packs_dir / "core"
        if (core / "pack.json").exists():
            return core
    return Path(root) / "packs" / "core"


def resolve_setup_config(root, pack_dir=None) -> dict:
    """Resolve a run's effective setup_config: core baseline
    overlaid by a specified pack (docs/PACK_DESIGN.md §1.3).

    The core/default pack (``packs/core/`` — under
    ``root``, or in the installed data-files tree for a
    wheel install; _core_pack_dir) is the baseline
    layer. With no ``pack_dir``, the baseline
    payload is the resolution — the domain-neutral defaults a
    new user starts with. With a specified pack, that pack's
    payload overlays the baseline per key: every key it
    declares governs, and every key it does not declare keeps
    the core default, so partial packs stay legal.

    Fail-closed (§1.4): both layers load through
    pack_setup_config (pin verification + setup_config
    validation), and the merged dict must pass
    setup_config_errors — any failure raises ValueError and
    the resolution is refused as a whole; a specified pack
    that cannot load never degrades to the baseline under its
    own name.

    Role words: when the merged config declares
    ``role_word_rules_from_profile``, the declaring pack's
    ``role_words`` artefact is resolved and frozen into the
    merged dict under ``role_words`` — the vocabulary is pack
    data, never a core literal.
    """
    from pathlib import Path
    core_dir = _core_pack_dir(root, pack_dir)
    resolved = pack_setup_config(core_dir)
    specified = pack_dir is not None and \
        Path(pack_dir).resolve() != core_dir.resolve()
    if specified:
        overlay = pack_setup_config(pack_dir)
        resolved = {**resolved, **overlay}
    # A payload declaring the role-word guard carries
    # its vocabulary as pack data; resolve it into the merged
    # config so the frozen resolved config is self-contained.
    # A declaration without the artefact refuses the whole
    # resolution (§1.4) — the guard never runs empty.
    if resolved.get("role_word_rules_from_profile"):
        declaring_dir = Path(pack_dir) if specified \
            else core_dir
        vocab = pack_role_words(declaring_dir)
        if vocab is None:
            raise ValueError(
                "resolved setup_config declares "
                "role_word_rules_from_profile but pack "
                f"{declaring_dir.name!r} ships no role_words "
                "artefact; the guard vocabulary is pack data "
                "and a declared guard never runs empty")
        resolved["role_words"] = sorted(vocab)
    if specified:
        errs = setup_config_errors(resolved)
        if errs:
            raise ValueError(
                "resolved setup_config (core baseline + "
                f"{Path(pack_dir).name}) fails validation: "
                + "; ".join(errs))
    return resolved


def pack_dir_for_family(root, family: str) -> Path | None:
    """The staged pack directory whose manifest binds ``family``.

    The match is on the manifest's ``profile_family`` field —
    never on directory names, which carry the pack's public
    (domain) name. Every candidate tree is searched
    (packs_dir_candidates: the root's own tree first, then
    the installed data-files tree). Returns None when no
    staged pack binds the family. A manifest that cannot be
    parsed raises (fail closed): a broken pack must not
    silently unbind a run.
    """
    for packs_dir in packs_dir_candidates(root):
        for pdir in sorted(packs_dir.iterdir()):
            mpath = pdir / "pack.json"
            if not mpath.exists():
                continue
            manifest = load_pack_manifest(pdir)
            if manifest.get("profile_family") == family:
                return pdir
    return None


def find_pack_dir(root, pack_ref) -> Path:
    """Resolve a user pack reference to a pack directory.

    ``pack_ref`` may be a path to a pack directory, a path to a
    pack.json, or a pack_id / profile_family to look up in the
    candidate trees (packs_dir_candidates: ``root/packs``
    first, then the installed data-files tree). Raises
    ValueError naming the reference when nothing matches
    (fail closed — a named pack that cannot be found never
    degrades to another pack or to no pack).
    """
    from pathlib import Path
    ref = Path(pack_ref)
    if ref.is_dir() and (ref / "pack.json").exists():
        return ref
    if ref.is_file() and ref.name == "pack.json":
        return ref.parent
    for packs_dir in packs_dir_candidates(root):
        by_id = packs_dir / str(pack_ref)
        if (by_id / "pack.json").exists():
            return by_id
        for pdir in sorted(packs_dir.iterdir()):
            mpath = pdir / "pack.json"
            if not mpath.exists():
                continue
            manifest = load_pack_manifest(pdir)
            if manifest.get("pack_id") == str(pack_ref) or \
                    manifest.get("profile_family") == str(pack_ref):
                return pdir
    raise ValueError(f"pack reference {pack_ref!r} does not "
                     f"resolve to a pack directory")


def pack_provenance(pack_dir) -> dict:
    """The provenance triple a run under this pack records
    (docs/PACK_DESIGN.md §1.5): pack identity + version + the
    payload pin, alongside the profile_family / profile_version
    the payload itself declares."""
    manifest = load_pack_manifest(pack_dir)
    return {
        "pack_id": manifest["pack_id"],
        "pack_version": manifest["pack_version"],
        "pack_payload_sha256": manifest["payload"]["sha256"],
        "profile_family": manifest["profile_family"],
        "profile_version": manifest["payload"].get("profile_version"),
    }


def pack_for_setup_config(root, family: str,
                          setup_config_bytes: bytes) -> dict | None:
    """The pack provenance triple binding a run, or None.

    Provenance (docs/PACK_DESIGN.md §1.5): a run executes
    under a pack iff some pack manifest names the run's family
    AND pins exactly the setup_config bytes the run executes
    with — byte equality with the pinned payload, the same
    identity the loader itself enforces. (The payload IS the
    file the profile_hash lineage pins; the two hash lineages —
    the stage-0 profile document's and the setup_config's own —
    do not coincide for every family, so bytes are the binding
    key, never a hash-field comparison.) The binding is exact
    or absent, never approximate. Fail-closed in the loader's
    sense: a pack manifest that cannot be read, or a
    same-family pack whose payload fails its pin / validation,
    raises rather than silently unbinding the run from its
    record."""
    import hashlib
    import json
    candidates = packs_dir_candidates(root)
    if not candidates:
        return None
    digest = hashlib.sha256(setup_config_bytes).hexdigest()
    for packs_dir in candidates:
        for pdir in sorted(packs_dir.iterdir()):
            mpath = pdir / "pack.json"
            if not mpath.exists():
                continue
            try:
                raw = json.loads(mpath.read_text())
            except Exception as e:
                raise ValueError(f"pack manifest {mpath} is not "
                                 f"valid JSON: {e}") from e
            if not isinstance(raw, dict) \
                    or raw.get("profile_family") != family:
                continue
            manifest = load_pack_manifest(pdir)
            if manifest["payload"]["sha256"] != digest:
                continue
            pack_setup_config(pdir)  # pin + validation, fail-closed
            return pack_provenance(pdir)
    return None


def profile_setup_config_path(root, family: str):
    """Locate a family's in-force setup_config in the pack layout.

    Public layout: each pack directory carries its profile at
    ``packs/<pack>/profile/`` (profile.json + setup_config.json).
    The family is matched on the profile document's ``family``
    field — never on directory names, which carry the pack's
    public (domain) name, not the profile lineage.

    Returns the setup_config Path, or None when no staged pack
    carries the family or the file is absent. Every candidate
    tree is searched (packs_dir_candidates)."""
    import json
    for packs_dir in packs_dir_candidates(root):
        for pdir in sorted(packs_dir.iterdir()):
            prof_path = pdir / "profile" / "profile.json"
            if not prof_path.exists():
                continue
            try:
                fam = json.loads(prof_path.read_text()).get("family")
            except Exception:
                continue
            if fam != family:
                continue
            sc_path = pdir / "profile" / "setup_config.json"
            if sc_path.exists():
                return sc_path
    return None


def derive_guidance(cfg: dict | None) -> str:
    """The guidance channel: profile -> proposer guidance.

    An explicit ``proposer_guidance`` string is used verbatim.
    Otherwise a ``schema_front`` inventory is rendered; failing
    that, declared entity_types / relation_types vocabularies are
    rendered as closed inventories. None of them present -> ""
    (no guidance; the proposer prompt construction is then
    byte-identical to the pre-channel behavior).
    """
    cfg = cfg or {}
    g = cfg.get("proposer_guidance")
    if isinstance(g, str) and g.strip():
        return g
    sf = cfg.get("schema_front")
    if isinstance(sf, dict) and (sf.get("entity_inventory")
                                 or sf.get("relation_inventory")):
        return _render_schema_front(sf)
    if entity_type_names(cfg) or cfg.get("relation_types"):
        return _render_vocabularies(cfg)
    return ""


# ------------------------------------------------------------------
# Domain framing accessors.
#
# The proposer and pair-judge base templates in pipeline/proposer.py
# and pipeline/pair_proposer.py are domain-neutral core text. A
# family's domain framing (the contract opening line for CUAD,
# the slot rules block, the pair inventory's generic-member name
# for BioRED) is profile DATA, declared in the family's
# setup_config under the keys registered in CONSUMED_KEYS above
# and threaded by stage_candidates. Each accessor returns None
# when the profile declares nothing — the builders then use
# their neutral core defaults, and no domain text appears.
# ------------------------------------------------------------------

def _framing_value(cfg: dict | None, key: str) -> str | None:
    v = (cfg or {}).get(key)
    return v if isinstance(v, str) and v.strip() else None


def proposer_framing(cfg: dict | None) -> str | None:
    """The family's declared opening line for the free-proposer
    triple prompt; None = neutral core default."""
    return _framing_value(cfg, "proposer_framing")


def proposer_max_per_chunk(cfg: dict | None) -> int | None:
    """The family's declared per-chunk proposal ceiling for
    the free proposer (issue #52); None = the core default
    (proposer.MAX_PER_CHUNK, the CUAD standalone recipe's
    value). A declared value must be a positive integer —
    anything else fails closed, like the other recipe
    declarations."""
    v = (cfg or {}).get("proposer_max_per_chunk")
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise ValueError(
            f"proposer_max_per_chunk must be a positive "
            f"integer, got {v!r} (fail closed)")
    return v


def mention_layer_enabled(cfg: dict | None) -> bool:
    """The family's mention-layer declaration (issue #54).

    Absent = off: the free proposer runs the legacy prompt
    and the CANDIDATES->GATES path is byte-identical to the
    pre-layer behavior. A declared value must be a boolean —
    anything else fails closed, like the other recipe
    declarations."""
    v = (cfg or {}).get("mention_layer")
    if v is None:
        return False
    if not isinstance(v, bool):
        raise ValueError(
            f"mention_layer must be a boolean, got {v!r} "
            "(fail closed)")
    return v


def entity_sweep_enabled(cfg: dict | None) -> bool:
    """The family's entity-sweep declaration (issue #58).

    Absent = off: the free proposer runs only its chunk
    pass and CANDIDATES is byte-identical to the pre-sweep
    behavior. A declared value must be a boolean — anything
    else fails closed, like the other recipe declarations."""
    v = (cfg or {}).get("entity_sweep")
    if v is None:
        return False
    if not isinstance(v, bool):
        raise ValueError(
            f"entity_sweep must be a boolean, got {v!r} "
            "(fail closed)")
    return v


def slot_framing(cfg: dict | None) -> str | None:
    """The family's declared opening line for the clause-slot
    prompt; None = neutral core default."""
    return _framing_value(cfg, "slot_framing")


def slot_rules(cfg: dict | None) -> str | None:
    """The family's declared rules block for the clause-slot
    prompt; None = neutral core default rules."""
    return _framing_value(cfg, "slot_rules")


def pair_judge_generic_relation(cfg: dict | None) -> str | None:
    """The relation inventory's generic-member name for the
    pair judge's evidence instruction — e.g. BioRED's
    "Association", declared in its profile; None = the neutral
    fallback wording in pair_proposer."""
    return _framing_value(cfg, "pair_judge_generic_relation")
