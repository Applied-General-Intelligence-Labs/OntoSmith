"""Batch runner: executes the 8-stage pipeline for a Batch.

Stages: INGEST → NER → CANDIDATES → GATES → ROUTE → WRITE → INDEX → VERIFY
Each stage checkpoints; a crash resumes from the last completed stage.
"""
from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from .batch import (
    Batch,
    BatchStatus,
    advance,
    batch_dir,
    doc_id_for_text,
    git_code_version,
    mark_failed,
    new_run_id,
    save_batch,
    save_state,
    write_manifest,
)
from .heartbeat import beat as _heartbeat_beat


def _log(batch_id: str, msg: str) -> None:
    print(f"[batch {batch_id}] {msg}", flush=True)


def _beat(b: Batch, root: Path, stage: str,
          progress: int | None = None, total: int | None = None) -> None:
    """Liveness heartbeat (§3): rewritten at stage transitions and at
    CANDIDATES/GATES checkpoints. Never raises (see heartbeat.py)."""
    _heartbeat_beat(batch_dir(root, b.batch_id), b.batch_id, b.run_id,
                    stage, progress, total)


def _conn_add_cost(total, add):
    # None-aware spend accumulation (connections.add_cost).
    from . import connections as _conn
    return _conn.add_cost(total, add)


def _round_cost(usd):
    # Round a possibly-unmetered (None) spend figure.
    return None if usd is None else round(usd, 4)


def _fmt_cost(usd) -> str:
    # Log-line spend: unmetered reads as such, not $0.0000.
    return "unmetered" if usd is None else f"${usd:.4f}"


def _record_cost(b: Batch, stage: str, usd: float | None) -> None:
    """Cost ledger (§3): per-stage API spend in batch metrics.

    b.metrics["cost_ledger"] maps stage -> USD (assignment semantics:
    a resumed stage re-records its recomputed total, so the ledger
    never double-counts) plus "total_usd", "cap_usd" and
    "cost_basis" (the bindings' aggregate basis —
    provider-reported / estimated / not-provided / mixed).
    Enforces config.max_cost_usd as a hard cap: recording a stage
    that pushes the cumulative total over the cap raises, which
    aborts the run (run_batch marks the batch FAILED with this
    error in state).

    `usd` may be None — the stage's spend is unmetered (a
    cost-less binding). The ledger records None (unknown),
    never a folded 0.0. While any recorded stage is
    unmetered the run's total is likewise unknown:
    "total_usd" is None and the metered stages' sum rides
    in "metered_usd" (a bare numeric total beside
    a null stage told JSON consumers the run cost that
    much, or nothing, when the truth is "partly unknown").
    With a cap set, an unmetered stage refuses by name
    instead: a cap cannot guard spend it cannot meter
    (create/run preflights refuse this combination
    earlier; this is the same rule at the point of record).
    """
    from . import connections as _conn
    cap = b.config.max_cost_usd
    if usd is None and cap is not None:
        raise _conn.BindingCostError(
            f"stage {stage} recorded unmetered spend (the "
            f"binding reports no cost) while max_cost_usd "
            f"${cap:.2f} is set; a USD cap cannot guard spend "
            f"it cannot meter — bind a cost-reporting route, "
            f"declare per-seat rates, or remove the cap "
            f"(--no-cost-cap)")
    ledger = dict(b.metrics.get("cost_ledger") or {})
    ledger[stage] = None if usd is None else round(float(usd), 4)
    stage_values = [v for k, v in ledger.items()
                    if k not in ("total_usd", "metered_usd",
                                 "cap_usd", "cost_basis")]
    metered = round(sum(v for v in stage_values
                        if isinstance(v, (int, float))), 4)
    ledger["metered_usd"] = metered
    total = None if any(v is None for v in stage_values) \
        else metered
    ledger["total_usd"] = total
    ledger["cap_usd"] = cap
    ledger["cost_basis"] = _conn.resolve_bindings().cost_basis()
    b.metrics["cost_ledger"] = ledger
    if cap is not None and total > cap:
        raise RuntimeError(
            f"cost cap exceeded: ledger total ${total:.4f} > "
            f"max_cost_usd ${cap:.2f} "
            f"(stage {stage} recorded ${usd:.4f})")


def _check_cost_cap(b: Batch) -> None:
    """Pre-stage cap guard: refuse to start a stage when the ledger
    already sits at/over the hard cap. No cap (None) never
    refuses."""
    cap = b.config.max_cost_usd
    if cap is None:
        return
    ledger = b.metrics.get("cost_ledger") or {}
    total = ledger.get("total_usd") or 0.0
    if total >= cap:
        raise RuntimeError(
            f"cost cap exceeded: ledger total ${total:.4f} >= "
            f"max_cost_usd ${cap:.2f} before stage start")


def _load_setup_config(b: Batch, root: Path) -> dict | None:
    """Stage-0 setup_config for the batch's profile family.

    Consumed by CANDIDATES (proposer categories, role-word
    rules, guidance, endpoint types), ROUTE
    (C1 whitelist) and WRITE (relation map, merge
    prohibitions). The registered consumer set lives in
    pipeline/profile_config.py; batch creation fails closed on
    any key outside it. None when the batch runs without a
    profile; stages then use their no-profile defaults.

    Sources, in priority order:
    1. the batch directory's ``setup_config.resolved.json`` —
       the resolver's output frozen at creation (core
       baseline + the binding pack); a batch executes exactly
       the configuration its creation resolved;
    2. the legacy ``profiles/<family>/setup_config.json``;
    3. the family's profile file in the pack layout
       (``profile_setup_config_path``).
    """
    if not b.profile_family:
        return None
    from .profile_config import profile_setup_config_path
    resolved = batch_dir(root, b.batch_id) / "setup_config.resolved.json"
    legacy = root / "profiles" / b.profile_family / "setup_config.json"
    candidates = [resolved, legacy]
    pack_sc = profile_setup_config_path(root, b.profile_family)
    if pack_sc is not None:
        candidates.append(pack_sc)
    p = next((c for c in candidates if c.exists()), None)
    # The batch DECLARES a profile family, so a missing
    # or unreadable setup_config is a hard failure, not a silent
    # no-profile fallback — running under defaults while batch.json
    # names a profile makes the run's semantics differ from its
    # record with no trace. Fail loud; the stage runner records the
    # error on the batch (FAILED + error) like any other stage fault.
    if p is None:
        raise FileNotFoundError(
            f"batch declares profile_family={b.profile_family!r} but "
            f"no setup_config exists at any of "
            f"{[str(c) for c in candidates]}; refusing to run with "
            "no-profile defaults under a named profile")
    try:
        return json.loads(p.read_text())
    except Exception as e:
        raise RuntimeError(
            f"setup_config for profile_family={b.profile_family!r} "
            f"at {p} is unreadable/corrupt: {e}; refusing to run "
            "with no-profile defaults under a named profile") from e


def stage_ingest(b: Batch, root: Path, doc_texts: dict[str, str],
                 stages: list[str]) -> None:
    """INGEST: texts → texts.jsonl with content-hash doc_ids. Dedupe."""
    _log(b.batch_id, f"INGEST: {len(doc_texts)} input docs")
    d = batch_dir(root, b.batch_id)
    seen: dict[str, dict] = {}
    sources = []
    with open(d / "texts.jsonl", "w") as f:
        for path, text in doc_texts.items():
            doc_id = doc_id_for_text(text)
            if doc_id in seen:
                continue
            seen[doc_id] = {"path": path}
            f.write(json.dumps({
                "doc_id": doc_id, "source_path": path,
                "text": text, "char_count": len(text)}) + "\n")
            sources.append({"doc_id": doc_id, "source_path": path,
                            "char_count": len(text)})
    b.doc_ids = sorted(seen.keys())
    b.metrics["ingest"] = {"docs_in": len(doc_texts),
                           "docs_unique": len(seen),
                           "docs_deduped": len(doc_texts) - len(seen)}
    _record_cost(b, "INGEST", 0.0)  # local only, no API spend
    write_manifest(b, sources, root)
    save_batch(b, root)
    advance(b, root, [*stages, "INGEST"])
    _log(b.batch_id, f"INGEST done: {len(seen)} unique docs")


def stage_ner(b: Batch, root: Path, stages: list[str]) -> None:
    """NER stage — contract writer only since 2026-10-29.

    The GLiNER NER stage fed the retired pipeline candidate
    source. The LLM proposer's guards attest by deterministic
    text containment and never consult NER mentions, and the
    document list comes from texts.jsonl — so the stage now
    emits one empty-entity row per document, keeping the
    ner.jsonl contract (registry/graph consumers read it
    tolerantly) and resume semantics unchanged, and never
    loads a local model. The stage name stays NER because
    batch checkpoints record it."""
    d = batch_dir(root, b.batch_id)
    texts = {}
    with open(d / "texts.jsonl") as f:
        for line in f:
            r = json.loads(line)
            texts[r["doc_id"]] = r["text"]
    ner_path = d / "ner.jsonl"
    have = set()
    if ner_path.exists():
        for line in ner_path.read_text().splitlines():
            try:
                have.add(json.loads(line)["doc_id"])
            except Exception:
                break
    with open(ner_path, "a") as out:
        for doc_id in texts:
            if doc_id not in have:
                out.write(json.dumps({"doc_id": doc_id,
                                      "entities": []}) + "\n")
    b.metrics["ner"] = {"entities": 0, "entities_per_doc": 0.0,
                        "seconds": 0.0, "workers": 0,
                        "gliner_retired": True}
    _record_cost(b, "NER", 0.0)
    save_batch(b, root)
    advance(b, root, [*stages, "NER"])
    _log(b.batch_id, "NER: contract rows written (GLiNER retired "
                     "2026-10-29; the LLM proposer is the only "
                     "candidate source)")


def _per_doc_distribution(candidates: list[dict],
                          doc_ids) -> dict:
    """Candidate counts per document, over EVERY built
    document (a document with no candidates counts 0).
    Reported by CANDIDATES so extraction sparsity is a
    printed fact, not a hidden one (issue #52)."""
    import statistics
    counts = {doc_id: 0 for doc_id in doc_ids}
    for c in candidates:
        if c.get("doc_id") in counts:
            counts[c["doc_id"]] += 1
    vals = sorted(counts.values())
    if not vals:
        return {"documents": 0, "min": 0, "median": 0, "max": 0}
    return {"documents": len(vals), "min": vals[0],
            "median": statistics.median(vals), "max": vals[-1]}


def stage_candidates(b: Batch, root: Path, stages: list[str]) -> None:
    """CANDIDATES: LLM proposer → candidates.

    The LLM proposer (pipeline/proposer.py) is the only
    candidate source: free triples plus clause slots where
    the profile declares categories, with the profile's
    pair-enumeration / entity-sweep / mention channels as
    declared — every channel through the one guard funnel
    (deterministic span/attestation/identity guards at
    proposal time), Jev gates judging downstream as usual.

    The GLiNER+spaCy pipeline source was retired
    2026-10-29 (unmeasured, English-bound, separate
    identity discipline) and the candidate_source
    setting was removed with it — there is one source,
    so there is nothing to select.
    """
    setup_cfg = _load_setup_config(b, root)
    slot_categories = list((setup_cfg or {}).get(
        "label_categories") or [])
    # The role-word guard vocabulary is pack data,
    # resolved by profile_config — frozen into the resolved
    # config at batch creation, or read from the binding
    # pack's role_words artefact. None = the profile declares
    # no role-word rules (guard off); a declaration with no
    # resolvable vocabulary refuses here (fail closed).
    from .profile_config import effective_role_words
    role_words = effective_role_words(root, setup_cfg)
    # profile wiring: the guidance
    # rendered from the profile's setup_config is threaded into the
    # proposer prompt, and proposed endpoint types are validated
    # against the profile's endpoint-type vocabulary. A profile
    # that configures no guidance yields "" here, and the prompt
    # construction is then byte-identical to the pre-channel one.
    from .profile_config import (
        derive_guidance,
        endpoint_type_names,
        entity_sweep_enabled,
        mention_layer_enabled,
        pair_judge_generic_relation,
        proposer_framing,
        proposer_max_per_chunk,
        slot_framing,
        slot_rules,
    )
    guidance = derive_guidance(setup_cfg)
    endpoint_types = endpoint_type_names(setup_cfg)
    # The free proposer's per-chunk proposal ceiling: the
    # profile may declare its own (proposer_max_per_chunk);
    # absent a declaration the core default applies and the
    # prompt is byte-identical to the pre-#52 recipe.
    proposer_cap = proposer_max_per_chunk(setup_cfg)
    # The mention layer (issue #54): the profile's
    # declaration switches the free proposer to the mention
    # prompt variant and the guards to span verification;
    # absent = off and this stage is byte-identical to the
    # pre-layer behavior.
    mention_on = mention_layer_enabled(setup_cfg)
    if mention_on:
        _log(b.batch_id,
             "CANDIDATES: mention layer ON (mention spans + "
             "antecedent resolutions proposed, spans verified "
             "by the guards, resolutions judged at GATES)")
    # The entity sweep (issue #58): the profile's
    # declaration adds an entity-centered second proposal
    # pass inside the free-proposer branch below — the
    # chunk pass runs first, untouched; the sweep only
    # appends candidates for entities the chunk pass never
    # proposed an endpoint for. Absent = off and this stage
    # is byte-identical to the pre-sweep behavior.
    sweep_on = entity_sweep_enabled(setup_cfg)
    if sweep_on:
        _log(b.batch_id,
             "CANDIDATES: entity sweep ON (census anchors "
             "re-asked per anchor after the chunk pass; "
             "sweep candidates merge behind the chunk "
             "channel)")
    # Domain framing: the family's declared framing
    # for the proposer / pair-judge base templates, threaded as
    # data; None (profile declares none) selects the builders'
    # domain-neutral core defaults.
    triple_framing = proposer_framing(setup_cfg)
    clause_framing = slot_framing(setup_cfg)
    clause_rules = slot_rules(setup_cfg)
    pair_generic_relation = pair_judge_generic_relation(
        setup_cfg)
    _log(b.batch_id,
         f"CANDIDATES: LLM proposer (model={b.config.llm_model}, "
         f"slot categories={len(slot_categories)}, "
         f"role-word guard={'on' if role_words else 'off'})")
    if guidance:
        _log(b.batch_id,
             f"CANDIDATES: profile guidance active "
             f"({len(guidance)} chars); endpoint types "
             f"validated against {sorted(endpoint_types)}")

    d = batch_dir(root, b.batch_id)
    texts = {}
    source_paths = {}
    with open(d / "texts.jsonl") as f:
        for line in f:
            r = json.loads(line)
            texts[r["doc_id"]] = r["text"]
            source_paths[r["doc_id"]] = r.get("source_path", "")
    # Pair-enumeration branch: with the profile's
    # pair_enumeration switch on, the proposer phase below
    # replaces the free generative proposer with the roster pair
    # census (pipeline/pair_proposer.py). The roster is a frozen
    # batch-dir input: roster_v2.jsonl when the profile declares
    # roster_pass (the mention-first pass product),
    # roster.jsonl otherwise. Switch absent/off -> this
    # block is inert and candidate construction is byte-identical
    # to the pre-branch behavior (pinned by the wiring tests).
    pair_enum = bool(setup_cfg
                     and setup_cfg.get("pair_enumeration"))
    pair_window = (setup_cfg or {}).get("pair_window") or "sentence"
    # Pair guard relaxations: profile-selected,
    # absent = the frozen legacy behavior (pinned by
    # tests/test_emission_guards.py). theme attestation for
    # pair items uses the pair sentence the endpoints were
    # posed from; the quote check additionally accepts
    # punctuation-spacing-equivalent quotes.
    pair_abbrev_guard = bool((setup_cfg or {}).get(
        "pair_abbrev_guard"))
    pair_theme_attest = bool((setup_cfg or {}).get(
        "pair_theme_attest_sentence"))
    quote_punct_insensitive = bool((setup_cfg or {}).get(
        "quote_punct_insensitive"))
    # theme attestation scope for free proposals —
    # profile/pack-selected, absent = the quote-only test.
    theme_attest_chunk = bool((setup_cfg or {}).get(
        "theme_attest_chunk"))
    # Identity seat (identity-surgery program,
    # 2026-10-30): the profile may name a frozen
    # decisions file carrying an offline judge's
    # verdicts for rule 4 of identity resolution.
    # Declared -> loaded once here and consulted per
    # document in the guard calls below; a point the
    # file never judged defers to the rule chain.
    # Absent = no seat: the deterministic chain alone,
    # byte-identical. A declared file that cannot be
    # loaded fails the stage closed.
    identity_decisions = None
    identity_seat_src = None
    identity_seat_stats = {"consulted": 0, "opinions": 0}
    _seat_decl = (setup_cfg or {}).get(
        "identity_seat_decisions")
    if _seat_decl is not None:
        if not (isinstance(_seat_decl, str)
                and _seat_decl.strip()):
            raise ValueError(
                "identity_seat_decisions must name a "
                "decisions file (fail closed)")
        from . import proposer as _prop
        _seat_path = Path(_seat_decl)
        if not _seat_path.is_absolute():
            _seat_path = root / _seat_path
        try:
            identity_decisions = \
                _prop.load_identity_decisions(_seat_path)
        except Exception as e:
            raise ValueError(
                f"identity seat decisions {_seat_path} "
                f"are unreadable or malformed: {e} "
                "(fail closed)") from e
        identity_seat_src = {
            "path": _seat_decl,
            "sha256": hashlib.sha256(
                _seat_path.read_bytes()).hexdigest(),
            "n_decisions": len(identity_decisions)}
        _log(b.batch_id,
             f"CANDIDATES: identity seat ON "
             f"({identity_seat_src['n_decisions']} frozen "
             f"verdicts from {_seat_decl}, sha256 "
             f"{identity_seat_src['sha256'][:12]}…; "
             "unjudged points defer to the rule chain)")
    # Pair-judge variant: profile-selected;
    # absent = the default judge (sentence x standard).
    pair_judge_context = (setup_cfg or {}).get(
        "pair_judge_context") or "sentence"
    pair_judge_instruction = (setup_cfg or {}).get(
        "pair_judge_instruction") or "standard"
    # JF-F1 direction slot on the production path:
    # profile-selected via the registered pair_judge_format key.
    # Absent = the legacy answer format — the proposer is then
    # called with jf1=False and a None symmetric set, exactly the
    # pre-existing call, and prompts / parsing / emission / verdict
    # records are byte-identical (pinned by the wiring tests). The symmetric set
    # is DATA: read from the pinned pack artefact the profile
    # names (pair_judge_symmetric_source), never a core literal;
    # any failure to resolve it fails the stage closed.
    pair_judge_format = (setup_cfg or {}).get("pair_judge_format")
    if pair_judge_format is not None and pair_judge_format != "jf1":
        raise ValueError(
            f"pair_judge_format {pair_judge_format!r} is not a "
            "registered format (fail closed; registered: 'jf1', "
            "or omit the key for the legacy format)")
    jf1_on = bool(pair_enum and pair_judge_format == "jf1")
    symmetric_set: set[str] | None = None
    symmetric_src: dict | None = None
    if jf1_on:
        src = (setup_cfg or {}).get("pair_judge_symmetric_source")
        if not (isinstance(src, str) and src.strip()):
            raise ValueError(
                "pair_judge_format 'jf1' requires "
                "'pair_judge_symmetric_source' (fail closed)")
        spath = Path(src)
        if not spath.is_absolute():
            # Pack-declared artefact paths are workspace-relative:
            # they resolve against the batch state root (the tree
            # beside the code in a checkout; $ONTOSMITH_ROOT / the
            # working directory for an installed copy), never
            # against the package's own tree.
            spath = root / spath
        try:
            artefact = json.loads(spath.read_text())
            sym = artefact["symmetric_relations"]
            if not (isinstance(sym, list) and sym and all(
                    isinstance(s, str) and s for s in sym)):
                raise ValueError(
                    "symmetric_relations is not a non-empty "
                    "string list")
        except Exception as e:
            raise ValueError(
                f"JF-F1 symmetric-set artefact {spath} is "
                f"unreadable or malformed: {e} (fail closed)"
            ) from e
        symmetric_set = set(sym)
        symmetric_src = {
            "path": src,
            "sha256": hashlib.sha256(spath.read_bytes()).hexdigest(),
            "n_symmetric": len(symmetric_set)}
    relation_inventory: set[str] = set()
    roster_by_doc: dict[str, list[dict]] = {}
    roster_name = ""
    if pair_enum:
        sf = (setup_cfg or {}).get("schema_front") or {}
        relation_inventory = {
            str(r["type"]) for r in sf.get("relation_inventory") or []
            if isinstance(r, dict) and r.get("type")} or {
            str(x) for x in (setup_cfg or {}).get("relation_types")
            or []}
        roster_name = "roster_v2.jsonl" if (setup_cfg or {}).get(
            "roster_pass") else "roster.jsonl"
        roster_path = d / roster_name
        if not roster_path.exists():
            raise RuntimeError(
                f"pair_enumeration is on but the batch roster "
                f"{roster_name} is missing from {d} — materialize "
                f"the frozen roster into the batch dir first")
        for line in open(roster_path):
            if line.strip():
                rr = json.loads(line)
                roster_by_doc[rr["doc_id"]] = rr["mentions"]
        _log(b.batch_id,
             f"CANDIDATES: pair enumeration ON (window="
             f"{pair_window}, roster={roster_name}, judge="
             f"{pair_judge_context}x{pair_judge_instruction}: "
             f"{len(roster_by_doc)} docs, "
             f"{sum(len(v) for v in roster_by_doc.values())} "
             f"mentions, inventory={len(relation_inventory)} "
             f"relations)")
        if jf1_on:
            _log(b.batch_id,
                 f"CANDIDATES: JF-F1 direction slot ON (symmetric "
                 f"set: {symmetric_src['n_symmetric']} relations "
                 f"from {symmetric_src['path']}, sha256 "
                 f"{symmetric_src['sha256'][:12]}…)")
    # The document list comes from texts.jsonl; the LLM
    # proposer never consults NER mentions (ner.jsonl is a
    # contract artifact since the pipeline source retired).
    candidates = []
    blocked_all = []  # Arm-B C3-blocked records (killed-set audit sidecar)
    # Verdict sidecar:
    # every pair verdict the pair stage produces, persisted
    # pre-guard so the verdict -> blocked -> deduped -> kept
    # accounting is reproducible from artifacts alone.
    pair_verdicts: list[dict] = []
    pair_verdicts_consistent = True
    # Proposer spend/stats. Persisted in the checkpoint so a
    # resumed run keeps the true totals (unlike the fallback above).
    proposer_cost = 0.0
    proposer_stats = {"proposed": 0, "calls": 0, "errors": 0,
                      "slots_proposed": 0, "typed_kept": 0,
                      "types_dropped_unknown": 0,
                      "chunks": 0, "chunks_at_cap": 0,
                      "max_per_chunk": 0,
                      "mention_candidates": 0,
                      "mention_resolutions": 0,
                      "mention_fallback_subject": 0,
                      "mention_fallback_object": 0,
                      "identity_seat_consulted": 0,
                      "identity_seat_opinions": 0}

    def _sync_identity_seat_stats():
        proposer_stats["identity_seat_consulted"] = \
            identity_seat_stats["consulted"]
        proposer_stats["identity_seat_opinions"] = \
            identity_seat_stats["opinions"]
    pair_stats = {"enumerated": 0, "answered": 0, "calls": 0,
                  "outcomes": {}, "alias_emitted": 0}
    # Entity-sweep accounting (issue #58), checkpointed
    # with the proposer stats so a resumed run keeps the
    # true totals. All counters stay 0 — and no metrics
    # block is emitted — when the profile does not declare
    # the sweep.
    sweep_cost = 0.0
    sweep_stats = {"anchors": 0, "anchors_asked": 0,
                   "anchors_skipped": 0, "proposed": 0,
                   "off_anchor": 0, "evidence_missing": 0,
                   "duplicates": 0,
                   "merged": 0, "blocked": 0,
                   "blocked_reasons": {},
                   "calls": 0, "errors": 0,
                   "windows_dropped": 0,
                   "census_entities": 0, "census_extracted": 0,
                   "census_unattested": 0}
    # A corrupted-checkpoint reset is stamped into the stage
    # metrics below (ckpt_reset) — never silent.
    ckpt_reset: dict | None = None
    # Checkpoint for resume: track processed doc_ids
    ckpt_path = d / "candidates.ckpt.json"
    done_docs = set()
    if ckpt_path.exists():
        try:
            ckpt = json.loads(ckpt_path.read_text())
            done_docs = set(ckpt.get("done_docs", []))
            _ckpt_cost = ckpt.get("proposer_cost_usd", 0.0)
            proposer_cost = (None if _ckpt_cost is None
                             else float(_ckpt_cost))
            for k in proposer_stats:
                proposer_stats[k] = int(ckpt.get("proposer_stats", {})
                                        .get(k, 0))
            for k in pair_stats:
                pair_stats[k] = ckpt.get("pair_stats", {}).get(
                    k, pair_stats[k])
            _ckpt_sweep_cost = ckpt.get("sweep_cost_usd", 0.0)
            sweep_cost = (None if _ckpt_sweep_cost is None
                          else float(_ckpt_sweep_cost))
            for k in sweep_stats:
                if k == "blocked_reasons":
                    sweep_stats[k] = dict(
                        ckpt.get("sweep_stats", {}).get(k, {}))
                else:
                    sweep_stats[k] = int(
                        ckpt.get("sweep_stats", {}).get(
                            k, sweep_stats[k]))
            # Load existing candidates
            if (d / "candidates.jsonl").exists():
                with open(d / "candidates.jsonl") as f:
                    for line in f:
                        candidates.append(json.loads(line))
                _log(b.batch_id, f"CANDIDATES: resumed with {len(done_docs)} docs done, "
                    f"{len(candidates)} candidates")
            if (d / "candidates_blocked.jsonl").exists():
                with open(d / "candidates_blocked.jsonl") as f:
                    for line in f:
                        blocked_all.append(json.loads(line))
            if pair_enum and (d / "pair_verdicts.jsonl").exists():
                with open(d / "pair_verdicts.jsonl") as f:
                    for line in f:
                        if line.strip():
                            pair_verdicts.append(json.loads(line))
            elif pair_enum and pair_stats.get("outcomes"):
                # Resumed from a pre-sidecar checkpoint: the metrics
                # carry verdicts the sidecar never saw. The sidecar
                # then covers only docs judged by the wired code,
                # and the outcome-count assert below is skipped.
                pair_verdicts_consistent = False
        except Exception as e:
            # A checkpoint that cannot be read resets the stage's
            # resume state (done-docs, proposer/pair stats, loaded
            # candidates). That reset used to be silent:
            # log it and stamp it into the metrics.
            ckpt_reset = {"error": f"{type(e).__name__}: {e}"}
            _log(b.batch_id,
                 f"CANDIDATES: checkpoint {ckpt_path.name} "
                 f"unreadable ({ckpt_reset['error']}); resume "
                 f"state reset — stage restarts from scratch")

    doc_ids = list(texts.keys())
    _beat(b, root, "CANDIDATES", len(done_docs), len(doc_ids))
    for idx, doc_id in enumerate(doc_ids):
        if doc_id in done_docs:
            continue
        text = texts[doc_id]
        doc_blocked = []
        # The per-document identity seat over the frozen
        # decisions (None when the profile declares no
        # seat file): verdicts keyed by the document's
        # bridged-form hash, unjudged points defer.
        doc_seat = None
        if identity_decisions is not None:
            from . import proposer as _prop
            doc_seat = _prop.identity_seat_for_doc(
                identity_decisions,
                _prop.identity_doc_key(text),
                stats=identity_seat_stats)
        # LLM proposer — free triples + clause slots.
        # Proposals pass the deterministic guards in pipeline/proposer.py;
        # survivors become candidates judged by Jev at GATES (no score
        # is assigned here). Proposer candidates are bounded by the
        # recipe's per-chunk cap, as in the measured standalone path.
        doc_llm = []
        if pair_enum:
            # Pair-enumeration: the roster
            # pair census replaces the free proposer. Same guard,
            # same candidate-record discipline (the guard output
            # IS the candidate set — no parallel second pass);
            # records carry method "pair-enum".
            from . import connections as _conn
            from . import pair_proposer as _pp
            from . import proposer as _prop
            # Named refusal (BindingKeyError) when the proposer
            # seat's key variable is unset — never a bare
            # RuntimeError, and run_batch's preflight normally
            # catches this before any stage runs.
            key = _conn.seat("proposer").require_key()
            res = _pp.propose_pairs_for_doc(
                doc_id, text, roster_by_doc.get(doc_id, []), key,
                model=b.config.llm_model, guidance=guidance,
                relation_inventory=relation_inventory,
                window=pair_window,
                judge_context=pair_judge_context,
                judge_instruction=pair_judge_instruction,
                generic_relation=pair_generic_relation,
                jf1=jf1_on, symmetric_relations=symmetric_set,
                abbrev_guard=pair_abbrev_guard,
                evidence_sentence_in_items=pair_theme_attest)
            proposer_cost = _conn_add_cost(
                proposer_cost, res["cost"])
            proposer_stats["calls"] += res["calls"]
            proposer_stats["errors"] += res["errors"]
            pair_stats["enumerated"] += res["enumerated"]
            pair_stats["answered"] += res["answered"]
            pair_stats["calls"] += res["calls"]
            for ok_, ov in res["outcomes"].items():
                pair_stats["outcomes"][ok_] = pair_stats[
                    "outcomes"].get(ok_, 0) + ov
            # The doc's verdicts join the sidecar pool
            # (they were computed pre-guard, inside the proposer;
            # the guard outcome never alters a verdict record).
            pair_verdicts.extend(res.get("verdicts") or [])
            pair_stats["alias_emitted"] += sum(
                1 for it in res["items"]
                if it["subject"] != it["subject_canonical"]
                or it["object"] != it["object_canonical"])
            kept, blocked = _prop.guard_triples(
                res["items"], text, role_words=role_words,
                entity_types=endpoint_types or None,
                theme_attest_key=("evidence_sentence"
                                  if pair_theme_attest else None),
                quote_punct_insensitive=quote_punct_insensitive,
                identity_seat=doc_seat)
            for it in kept:
                rec = {"doc_id": doc_id, "subject": it["subject"],
                       "relation": it["relation"],
                       "object": it["object"],
                       "context": _prop.quote_window(text, it["quote"]),
                       "method": "pair-enum", "quote": it["quote"],
                       "attest": it["attest"]}
                for tk in ("subject_type", "object_type"):
                    if it.get(tk):
                        rec[tk] = it[tk]
                doc_llm.append(rec)
            for bl in blocked:
                rec = {"doc_id": doc_id, "subject": bl["subject"],
                       "relation": bl["relation"],
                       "object": bl["object"],
                       "context": _prop.quote_window(text, bl["quote"]),
                       "method": "pair-enum", "quote": bl["quote"],
                       "reason": bl["reason"]}
                for tk in ("subject_type", "object_type"):
                    if bl.get(tk):
                        rec[tk] = bl[tk]
                doc_blocked.append(rec)
        if not pair_enum:
            from . import connections as _conn
            from . import proposer as _prop
            # Named refusal (BindingKeyError) — see the pair-enum
            # branch above.
            key = _conn.seat("proposer").require_key()
            res = _prop.propose_triples_for_doc(
                doc_id, text, key, model=b.config.llm_model,
                guidance=guidance, framing=triple_framing,
                max_per_chunk=proposer_cap,
                mentions=mention_on)
            proposer_cost = _conn_add_cost(
                proposer_cost, res["cost"])
            proposer_stats["proposed"] += res["proposed"]
            proposer_stats["calls"] += res["calls"]
            proposer_stats["errors"] += res["errors"]
            proposer_stats["chunks"] += res["chunks"]
            proposer_stats["chunks_at_cap"] += res["chunks_at_cap"]
            proposer_stats["max_per_chunk"] = (
                proposer_cap if proposer_cap is not None
                else _prop.MAX_PER_CHUNK)
            if endpoint_types:
                proposer_stats["types_dropped_unknown"] += sum(
                    1 for it in res["items"]
                    for tk in ("subject_type", "object_type")
                    if isinstance(it.get(tk), str)
                    and it[tk].strip()
                    and it[tk].strip() not in endpoint_types)
            kept, blocked = _prop.guard_triples(
                res["items"], text, role_words=role_words,
                entity_types=endpoint_types or None,
                quote_punct_insensitive=quote_punct_insensitive,
                theme_attest_chunk=theme_attest_chunk,
                mentions=mention_on,
                identity_seat=doc_seat)
            proposer_stats["typed_kept"] += sum(
                1 for it in kept
                if it.get("subject_type") or it.get("object_type"))
            for it in kept:
                rec = {
                    "doc_id": doc_id, "subject": it["subject"],
                    "relation": it["relation"], "object": it["object"],
                    "context": _prop.quote_window(text, it["quote"]),
                    "method": "llm", "quote": it["quote"],
                    "attest": it["attest"]}
                # Validated endpoint types ride the
                # candidate through GATES/ROUTE into WRITE. Absent
                # keys = untyped candidate (legacy record shape).
                for tk in ("subject_type", "object_type"):
                    if it.get(tk):
                        rec[tk] = it[tk]
                # Verified mention records ride along the
                # same way (issue #54): GATES judges any
                # resolution they carry before the fact.
                if it.get("mentions"):
                    rec["mentions"] = it["mentions"]
                    proposer_stats["mention_candidates"] += 1
                    if any(m.get("antecedent")
                           for m in it["mentions"].values()):
                        proposer_stats["mention_resolutions"] += 1
                # Endpoints whose mention data was absent
                # fell back to the legacy evidence path
                # (issue #54, cut 2): the fallback rides the
                # candidate record and is counted by
                # endpoint, so the fallback rate is visible
                # in the stage metrics and at the finish.
                if it.get("mention_fallback"):
                    rec["mention_fallback"] = \
                        it["mention_fallback"]
                    for ep in it["mention_fallback"]:
                        proposer_stats[
                            f"mention_fallback_{ep}"] += 1
                doc_llm.append(rec)
            for bl in blocked:
                rec = {
                    "doc_id": doc_id, "subject": bl["subject"],
                    "relation": bl["relation"], "object": bl["object"],
                    "context": _prop.quote_window(text, bl["quote"]),
                    "method": "llm", "quote": bl["quote"],
                    "reason": bl["reason"]}
                for tk in ("subject_type", "object_type"):
                    if bl.get(tk):
                        rec[tk] = bl[tk]
                if bl.get("mentions"):
                    rec["mentions"] = bl["mentions"]
                doc_blocked.append(rec)
            if sweep_on:
                # Entity sweep (issue #58): an additive
                # second pass over the chunk pass's output.
                # The census is deterministic code; anchors
                # are the census entities that appear as NO
                # endpoint of any chunk-pass item for this
                # document (kept + blocked — the presence
                # accounting that defined the target
                # population). Sweep items pass the SAME
                # guard call (same flags) and merge behind
                # the chunk channel: an item whose key
                # equals a chunk-pass item's key is a
                # counted duplicate and the chunk record
                # wins. Candidate identity here is the
                # in-pipeline key — norm_ws endpoints,
                # relation verbatim (TN canonicalization
                # remains the scorer's, pack-supplied,
                # concern at read time).
                census = _prop.census_entities(text)
                sweep_stats["census_entities"] += len(
                    census["entities"])
                sweep_stats["census_extracted"] += \
                    census["extracted"]
                sweep_stats["census_unattested"] += \
                    census["unattested"]
                chunk_endpoints = set()
                chunk_keys = set()
                for it in kept + blocked:
                    chunk_endpoints.add(
                        _prop.norm_ws(it["subject"]))
                    chunk_endpoints.add(
                        _prop.norm_ws(it["object"]))
                    chunk_keys.add((
                        _prop.norm_ws(it["subject"]),
                        it["relation"],
                        _prop.norm_ws(it["object"])))
                anchors = [e for e in census["entities"]
                           if e["norm"] not in chunk_endpoints]
                sweep_stats["anchors"] += len(anchors)
                if anchors:
                    sres = _prop.propose_sweep_for_doc(
                        doc_id, text, anchors, key,
                        model=b.config.llm_model,
                        guidance=guidance,
                        framing=triple_framing,
                        max_per_chunk=proposer_cap,
                        mentions=mention_on)
                    proposer_cost = _conn_add_cost(
                        proposer_cost, sres["cost"])
                    sweep_cost = _conn_add_cost(
                        sweep_cost, sres["cost"])
                    sweep_stats["calls"] += sres["calls"]
                    sweep_stats["errors"] += sres["errors"]
                    sweep_stats["proposed"] += sres["proposed"]
                    sweep_stats["off_anchor"] += \
                        sres["off_anchor"]
                    sweep_stats["evidence_missing"] += \
                        sres["evidence_missing"]
                    sweep_stats["anchors_asked"] += \
                        sres["anchors_asked"]
                    sweep_stats["anchors_skipped"] += \
                        sres["anchors_skipped"]
                    sweep_stats["windows_dropped"] += \
                        sres["windows_dropped"]
                    # The guard runs per anchor group (same
                    # flags — the same call discipline), so
                    # the merge below, not the guard's own
                    # in-call dedupe, decides duplicates: a
                    # fact between two anchors is proposed
                    # under both, and the second arrival must
                    # be a COUNTED duplicate. The standing
                    # key set starts with the chunk channel's
                    # keys — chunk provenance always wins —
                    # and gains each merged sweep key.
                    by_anchor: dict[str, list[dict]] = {}
                    for it in sres["items"]:
                        by_anchor.setdefault(
                            it["anchor_norm"], []).append(it)
                    seen_keys = set(chunk_keys)
                    skept, sblocked = [], []
                    for e in anchors:
                        group = by_anchor.get(e["norm"])
                        if not group:
                            continue
                        gk, gb = _prop.guard_triples(
                            group, text,
                            role_words=role_words,
                            entity_types=endpoint_types or None,
                            quote_punct_insensitive=
                            quote_punct_insensitive,
                            theme_attest_chunk=theme_attest_chunk,
                            mentions=mention_on,
                            identity_seat=doc_seat)
                        skept.extend(gk)
                        sblocked.extend(gb)
                    for it in skept:
                        k = (_prop.norm_ws(it["subject"]),
                             it["relation"],
                             _prop.norm_ws(it["object"]))
                        if k in seen_keys:
                            sweep_stats["duplicates"] += 1
                            continue
                        seen_keys.add(k)
                        rec = {
                            "doc_id": doc_id,
                            "subject": it["subject"],
                            "relation": it["relation"],
                            "object": it["object"],
                            "context": _prop.quote_window(
                                text, it["quote"]),
                            "method": "llm-sweep",
                            "quote": it["quote"],
                            "attest": it["attest"]}
                        for tk in ("subject_type", "object_type"):
                            if it.get(tk):
                                rec[tk] = it[tk]
                        if it.get("mentions"):
                            rec["mentions"] = it["mentions"]
                        if it.get("mention_fallback"):
                            rec["mention_fallback"] = \
                                it["mention_fallback"]
                        doc_llm.append(rec)
                        sweep_stats["merged"] += 1
                    for bl in sblocked:
                        rec = {
                            "doc_id": doc_id,
                            "subject": bl["subject"],
                            "relation": bl["relation"],
                            "object": bl["object"],
                            "context": _prop.quote_window(
                                text, bl["quote"]),
                            "method": "llm-sweep",
                            "quote": bl["quote"],
                            "reason": bl["reason"]}
                        for tk in ("subject_type", "object_type"):
                            if bl.get(tk):
                                rec[tk] = bl[tk]
                        if bl.get("mentions"):
                            rec["mentions"] = bl["mentions"]
                        doc_blocked.append(rec)
                        sweep_stats["blocked"] += 1
                        sweep_stats["blocked_reasons"][
                            bl["reason"]] = sweep_stats[
                                "blocked_reasons"].get(
                                    bl["reason"], 0) + 1
            if slot_categories:
                sres = _prop.propose_slots_for_doc(
                    doc_id, text, slot_categories, key,
                    model=b.config.llm_model,
                    framing=clause_framing, rules=clause_rules)
                proposer_cost = _conn_add_cost(
                    proposer_cost, sres["cost"])
                proposer_stats["slots_proposed"] += sres["proposed"]
                proposer_stats["calls"] += sres["calls"]
                proposer_stats["errors"] += sres["errors"]
                fills, sblocked = _prop.guard_slots(sres["fills"], text)
                doc_name = next(
                    (f["value"] for f in fills
                     if f["category"] == "Document Name"), None)
                if not doc_name:
                    stem = Path(source_paths.get(doc_id, "")).name
                    doc_name = stem[:-4] if stem.endswith(".txt") \
                        else (stem or doc_id)
                for f in fills:
                    if f["category"] == "Document Name":
                        continue
                    doc_llm.append({
                        "doc_id": doc_id, "subject": doc_name,
                        "relation": f["category"],
                        "object": f["value"],
                        "context": _prop.quote_window(text, f["quote"]),
                        "method": "clause-llm", "quote": f["quote"]})
                for bl in sblocked:
                    doc_blocked.append({
                        "doc_id": doc_id, "subject": doc_name,
                        "relation": bl["category"],
                        "object": bl["value"],
                        "context": _prop.quote_window(text, bl["quote"]),
                        "method": "clause-llm", "quote": bl["quote"],
                        "reason": bl["reason"]})
        candidates.extend(doc_llm)
        blocked_all.extend(doc_blocked)
        # Checkpoint: save progress after each doc
        done_docs.add(doc_id)
        if (idx + 1) % 10 == 0 or (idx + 1) == len(doc_ids):
            with open(d / "candidates.jsonl", "w") as f:
                for c in candidates:
                    f.write(json.dumps(c) + "\n")
            if blocked_all:
                with open(d / "candidates_blocked.jsonl", "w") as f:
                    for bl in blocked_all:
                        f.write(json.dumps(bl) + "\n")
            if pair_verdicts:
                with open(d / "pair_verdicts.jsonl", "w") as f:
                    for v in pair_verdicts:
                        f.write(json.dumps(v) + "\n")
            _sync_identity_seat_stats()
            ckpt_path.write_text(json.dumps({
                "done_docs": sorted(done_docs),
                "proposer_cost_usd": _round_cost(proposer_cost),
                "proposer_stats": proposer_stats,
                "pair_stats": pair_stats,
                "sweep_stats": sweep_stats,
                "sweep_cost_usd": _round_cost(sweep_cost)}))
            _log(b.batch_id, f"CANDIDATES: {idx+1}/{len(doc_ids)} docs, "
                    f"{len(candidates)} candidates")
            _beat(b, root, "CANDIDATES", len(done_docs), len(doc_ids))
    with open(d / "candidates.jsonl", "w") as f:
        for c in candidates:
            f.write(json.dumps(c) + "\n")
    if blocked_all:
        with open(d / "candidates_blocked.jsonl", "w") as f:
            for bl in blocked_all:
                f.write(json.dumps(bl) + "\n")
    if pair_verdicts:
        with open(d / "pair_verdicts.jsonl", "w") as f:
            for v in pair_verdicts:
                f.write(json.dumps(v) + "\n")
    if pair_enum:
        # Sidecar assert: the persisted sidecar
        # must account for every pair judgment the stage metrics
        # record. The sidecar counts verdicts (pre-guard); the
        # metrics count the same outcomes — any drift means the
        # bookkeeping, not the candidates, is broken, and the
        # stage fails loudly rather than recording it silently.
        if pair_verdicts_consistent:
            from collections import Counter
            sidecar_outcomes = dict(Counter(
                v["outcome"] for v in pair_verdicts))
            if sidecar_outcomes != pair_stats["outcomes"]:
                raise RuntimeError(
                    "pair_verdicts sidecar outcome counts "
                    f"{sidecar_outcomes} != pair_enum outcomes "
                    f"{pair_stats['outcomes']}")
        else:
            _log(b.batch_id,
                 "CANDIDATES: pair_verdicts sidecar is partial "
                 "(resumed from a pre-sidecar checkpoint); "
                 "outcome-count assert skipped")
    # Clean up checkpoint
    if ckpt_path.exists():
        ckpt_path.unlink()
    # Metrics by method
    by_method: dict[str, int] = {}
    for c in candidates:
        by_method[c.get("method", "?")] = \
            by_method.get(c.get("method", "?"), 0) + 1
    blocked_reasons: dict[str, int] = {}
    for bl in blocked_all:
        blocked_reasons[bl.get("reason", "?")] = \
            blocked_reasons.get(bl.get("reason", "?"), 0) + 1
    _sync_identity_seat_stats()
    b.metrics["candidates"] = {
        "total": len(candidates),
        "by_method": by_method,
        "blocked": len(blocked_all), "blocked_reasons": blocked_reasons,
        "proposer": {**proposer_stats,
                     "cost_usd": _round_cost(proposer_cost),
                     "model": b.config.llm_model}}
    if ckpt_reset is not None:
        b.metrics["candidates"]["checkpoint_reset"] = ckpt_reset
    if identity_seat_src is not None:
        b.metrics["candidates"]["identity_seat"] = {
            **identity_seat_src,
            "consulted": identity_seat_stats["consulted"],
            "opinions": identity_seat_stats["opinions"]}
    if pair_enum:
        b.metrics["candidates"]["pair_enum"] = {
            **pair_stats, "window": pair_window,
            "roster": roster_name,
            "judge_context": pair_judge_context,
            "judge_instruction": pair_judge_instruction,
            "judge_format": pair_judge_format or "legacy"}
        if symmetric_src is not None:
            b.metrics["candidates"]["pair_enum"][
                "symmetric_source"] = symmetric_src
    if sweep_on:
        # The sweep's own accounting block (issue #58):
        # what the pass was asked, what it proposed, and
        # where the proposals died — evidence voids at
        # proposal time (cut 2), duplicates behind the
        # chunk channel, off-anchor drops, guard blocks by
        # reason. Its spend is folded into the proposer
        # ledger above; the block also carries it separately
        # so the pass is priced on its own.
        b.metrics["candidates"]["sweep"] = {
            **sweep_stats,
            "cost_usd": _round_cost(sweep_cost)}
    # Ledger: the stage's actual API spend is the proposer's
    # (usage returned per call and summed above). Proposer
    # spend IS checkpointed (above), so a resumed run keeps
    # the true total.
    _record_cost(b, "CANDIDATES",
                 _round_cost(_conn_add_cost(0.0, proposer_cost)))
    # Extraction density, reported (issue #52): the
    # per-document distribution over every built document,
    # and — for the free proposer — how many chunks reached
    # the per-chunk proposal ceiling. Before this, totals
    # were all the stage printed: a cap-bound run and a
    # naturally sparse run were indistinguishable.
    per_doc = _per_doc_distribution(candidates, texts.keys())
    b.metrics["candidates"]["per_doc"] = per_doc
    save_batch(b, root)
    advance(b, root, [*stages, "CANDIDATES"])
    _log(b.batch_id, f"CANDIDATES done: {len(candidates)} "
                     f"(by method: {by_method})")
    _log(b.batch_id,
         f"CANDIDATES density: per-doc candidates min "
         f"{per_doc['min']} / median {per_doc['median']} / "
         f"max {per_doc['max']} over {per_doc['documents']} "
         f"documents")
    if proposer_stats.get("chunks"):
        _log(b.batch_id,
             f"CANDIDATES density: proposer chunks at cap "
             f"{proposer_stats['chunks_at_cap']}/"
             f"{proposer_stats['chunks']} "
             f"(cap {proposer_stats['max_per_chunk']} per chunk)")
    fb_total = (proposer_stats.get("mention_fallback_subject", 0)
                + proposer_stats.get("mention_fallback_object", 0))
    if fb_total:
        # Mention-layer fallbacks (issue #54, cut 2):
        # endpoints whose mention data was absent were
        # judged on the quote under the legacy evidence
        # path. Named on the run output like guard blocks:
        # the fallback rate is the layer's compliance
        # signal, and it must not be silent either.
        _log(b.batch_id,
             f"CANDIDATES mention fallback: {fb_total} "
             f"endpoint(s) (subject "
             f"{proposer_stats['mention_fallback_subject']}, "
             f"object "
             f"{proposer_stats['mention_fallback_object']}) "
             f"judged on the quote under the legacy "
             f"evidence path")
    if sweep_on:
        _log(b.batch_id,
             f"CANDIDATES entity sweep: "
             f"{sweep_stats['anchors']} anchor(s), "
             f"{sweep_stats['proposed']} proposed, "
             f"{sweep_stats['merged']} merged candidates, "
             f"{sweep_stats['duplicates']} duplicate(s) "
             f"behind the chunk channel, "
             f"{sweep_stats['off_anchor']} off-anchor "
             f"dropped, {sweep_stats['evidence_missing']} "
             f"evidence-missing voided, "
             f"{sweep_stats['blocked']} blocked "
             f"(by reason: {sweep_stats['blocked_reasons']})")
    if blocked_all:
        # Guard blocks are reported BY NAME in the run
        # output, not only in the metrics record — a relation
        # class blocked wholesale (theme_presence) must be
        # visible at the moment it happens, with the sidecar
        # that carries the blocked items.
        _log(b.batch_id,
             f"CANDIDATES blocked: {len(blocked_all)} "
             f"(by reason: {blocked_reasons}) — see "
             f"candidates_blocked.jsonl")


def _resolution_needs(c: dict) -> list:
    """The endpoint resolutions a candidate carries (issue
    #54): [(endpoint, mention record)] for each endpoint
    whose verified mention record proposes an antecedent
    resolution. Empty for legacy candidates and for
    candidates whose mentions are all named."""
    m = c.get("mentions") or {}
    return [(ep, m[ep]) for ep in ("subject", "object")
            if isinstance(m.get(ep), dict)
            and m[ep].get("antecedent")]


def _fold_costs(costs: list):
    """Fold one item's per-call costs into its recorded
    cost: the first call's cost as-is (so a single-call
    item records exactly what the pre-mention code
    recorded, None included), later calls folded in with
    the connection layer's None-aware accumulation (any
    unmetered call makes the item's cost unknown)."""
    total = costs[0]
    for extra in costs[1:]:
        total = _conn_add_cost(total, extra)
    return total


def stage_gates(b: Batch, root: Path, stages: list[str],
                decide_fn, resolution_decide_fn=None) -> None:
    """GATES: Jev noul per candidate (queue-based, resumable).

    Candidates are enqueued as work items. Workers claim, judge, complete.
    A crash mid-stage loses nothing — unclaimed items stay queued.

    Mention layer (issue #54): a candidate carrying
    antecedent resolutions has each resolution judged as
    its own Jev decision BEFORE the fact (the proposer
    proposes, Jev decides). The candidate's resolution
    score is the weaker endpoint's. At/above the review
    band's high edge the resolution is clear and the fact
    is judged as usual; inside the band the resolution is
    ambiguous — the fact is judged but ROUTE caps the
    candidate at Tier-2 review; below the triple threshold
    the resolution is rejected, the fact is never judged,
    and the judgment record carries drop_reason
    "resolution_rejected" so the drop accounting can tell
    a rejected resolution from a rejected fact.
    Candidates carrying resolutions while no resolution
    judge is wired refuse the stage by name (fail closed).
    """
    from .queue import WorkQueue
    d = batch_dir(root, b.batch_id)
    candidates = [json.loads(l) for l in open(d / "candidates.jsonl")]
    if resolution_decide_fn is None and any(
            _resolution_needs(c) for c in candidates):
        raise RuntimeError(
            "GATES: candidates carry mention resolutions "
            "(issue #54) but no resolution judge was wired — "
            "refusing to judge facts on unjudged resolutions "
            "(fail closed)")
    # The $ projection is a planning heuristic, printed only
    # when the judge seat can meter spend at all: on
    # a cost-less binding a dollar figure would fabricate
    # knowledge the product's own doctrine says is unknown —
    # the banner then projects a judgment COUNT instead, and
    # the cap pre-check is skipped (a cap over unmetered
    # spend refuses by name at create/preflight/record).
    from . import connections as _conn_mod
    if _conn_mod.seat("judge").reports_cost:
        projected = len(candidates) * 0.00003  # conservative per-judgment estimate
        if (b.config.max_cost_usd is not None
                and projected > b.config.max_cost_usd):
            raise RuntimeError(
                f"budget cap: projected ${projected:.2f} > max ${b.config.max_cost_usd}")
        projection_note = f"budget ok, projected ${projected:.4f}"
    else:
        projection_note = (f"projected {len(candidates)} judgments "
                           f"(unmetered binding — no $ projection)")

    q = WorkQueue(d / "queue.db")
    worker_id = f"gates-{b.run_id[:8]}"
    try:
        # Enqueue (idempotent — resume won't duplicate)
        items = [{"item_id": f"cand-{i}",
                  "index": i, **c} for i, c in enumerate(candidates)]
        total = q.enqueue(b.batch_id, b.run_id, "GATES", items)
        depth = q.depth(b.batch_id, b.run_id, "GATES")
        pending = depth.get("pending", 0) + depth.get("claimed", 0)
        _log(b.batch_id, f"GATES: {total} queued, {pending} remaining "
                         f"({projection_note}, "
                         f"claim lease {b.config.queue_lease_seconds}s)")
        if pending == 0:
            _log(b.batch_id, "GATES: nothing to do (already complete)")

        def judge(c):
            result: dict = {}
            costs: list = []
            needs = _resolution_needs(c)
            if needs:
                p_res = None
                for ep, m in needs:
                    p_i, u_i = resolution_decide_fn(
                        c["context"], m["span"], m["antecedent"],
                        c["subject"] if ep == "subject"
                        else c["object"],
                        model=b.config.gate_model)
                    costs.append(u_i.get("cost"))
                    p_res = p_i if p_res is None else min(p_res, p_i)
                result["p_resolution"] = p_res
                if p_res >= b.config.review_band_hi:
                    result["resolution_status"] = "clear"
                elif p_res >= b.config.triple_threshold:
                    result["resolution_status"] = "review"
                else:
                    # Not a judged-negative fact: p_true 0.0
                    # is a routing sentinel, and the record
                    # carries the reason class that makes the
                    # distinction everywhere downstream.
                    result.update({
                        "resolution_status": "rejected",
                        "drop_reason": "resolution_rejected",
                        "fact_judged": False})
                    return {**result, "p_true": 0.0,
                            "cost": _fold_costs(costs)}
            p_true, usage = decide_fn(
                c["context"], c["subject"], c["relation"],
                c["object"], model=b.config.gate_model)
            costs.append(usage.get("cost"))
            return {**result, "p_true": p_true,
                    "cost": _fold_costs(costs)}

        # Spend already banked by a previous (crashed) session of this
        # stage, so the mid-loop cap check below is resume-correct.
        # The final total is recomputed from all results regardless.
        spent_before = 0.0
        with q._conn:
            cur = q._conn.execute(
                "SELECT result FROM queue_items WHERE batch_id=? "
                "AND run_id=? AND stage='GATES' AND status='done'",
                (b.batch_id, b.run_id))
            for (result_s,) in cur.fetchall():
                if result_s:
                    spent_before = _conn_add_cost(
                        spent_before,
                        json.loads(result_s).get("cost", 0))
        prior_total = (b.metrics.get("cost_ledger") or {}).get(
            "total_usd") or 0.0

        total_cost, errs, done = 0.0, 0, depth.get("done", 0)
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=8) as ex:
            while True:
                claimed = q.claim(b.batch_id, b.run_id, "GATES",
                                  worker_id, limit=32,
                                  lease_seconds=b.config.queue_lease_seconds)
                if not claimed:
                    # Crash residue: items still 'claimed' by a dead
                    # worker become claimable only after the queue's
                    # configured lease lapses. Breaking here (the previous
                    # behavior) finalized them as error judgments
                    # (p_true 0.0); wait out the lease instead (§3
                    # resume contract).
                    depth_now = q.depth(b.batch_id, b.run_id, "GATES")
                    if depth_now.get("claimed", 0) \
                            and not depth_now.get("pending", 0):
                        _log(b.batch_id,
                             f"GATES: waiting for "
                             f"{depth_now['claimed']} stale claims to "
                             f"lapse (crash recovery)")
                        _beat(b, root, "GATES", done, total)
                        time.sleep(10)
                        continue
                    break
                futs = {ex.submit(judge, it.payload): it for it in claimed}
                for fut in as_completed(futs):
                    it = futs[fut]
                    try:
                        result = fut.result()
                        cost = result.get("cost")
                        total_cost = _conn_add_cost(total_cost, cost)
                        q.complete(it.item_id, result,
                                   batch_id=b.batch_id, run_id=b.run_id)
                    except Exception as e:
                        errs += 1
                        st = q.fail(it.item_id, str(e)[:200],
                                    batch_id=b.batch_id, run_id=b.run_id)
                        _log(b.batch_id,
                             f"GATES: item {it.item_id} failed → {st}")
                    done += 1
                    if done % 200 == 0:
                        _log(b.batch_id, f"GATES: {done}/{total}")
                        _beat(b, root, "GATES", done, total)
                # Hard cap (§3): abort the stage rather than silently
                # continue past max_cost_usd. Unmetered spend under
                # a set cap refuses by name — the cap
                # cannot guard what it cannot meter.
                if (b.config.max_cost_usd is not None
                        and (spent_before is None
                             or total_cost is None)):
                    from . import connections as _conn
                    raise _conn.BindingCostError(
                        "GATES recorded unmetered spend (the judge "
                        "binding reports no cost) while max_cost_usd "
                        f"${b.config.max_cost_usd:.2f} is set; a USD "
                        "cap cannot guard spend it cannot meter — "
                        "bind a cost-reporting route, declare "
                        "per-seat rates, or remove the cap "
                        "(--no-cost-cap)")
                if (b.config.max_cost_usd is not None
                        and prior_total + spent_before + total_cost
                        > b.config.max_cost_usd):
                    raise RuntimeError(
                        f"cost cap exceeded in GATES: prior stages "
                        f"${prior_total:.4f} + gates "
                        f"${spent_before + total_cost:.4f} > "
                        f"max_cost_usd ${b.config.max_cost_usd:.2f}")
        # Collect results in index order
        judgments = []
        with q._conn:
            cur = q._conn.execute(
                """SELECT payload, result, status FROM queue_items
                   WHERE batch_id=? AND run_id=? AND stage='GATES'
                   ORDER BY CAST(json_extract(payload, '$.index') AS INTEGER)""",
                (b.batch_id, b.run_id))
            for payload_s, result_s, status in cur.fetchall():
                c = json.loads(payload_s)
                if status == "done" and result_s:
                    r = json.loads(result_s)
                    j = {**c, "p_true": r["p_true"],
                         "cost": r["cost"], "error": None}
                    # Mention-layer fields ride the judgment
                    # when the result carries them (issue
                    # #54); absent = the legacy record shape.
                    for k in ("p_resolution", "resolution_status",
                              "drop_reason", "fact_judged"):
                        if k in r:
                            j[k] = r[k]
                    judgments.append(j)
                else:
                    judgments.append({**c, "p_true": 0.0, "cost": 0,
                                      "error": f"status={status}"})
        # Recompute cost from results (avoids double-count on resume)
        total_cost = 0.0
        for j in judgments:
            total_cost = _conn_add_cost(total_cost, j.get("cost", 0))
        errs = sum(1 for j in judgments if j.get("error"))
        dlq = q.dlq_items(b.batch_id, "GATES")
        if dlq:
            _log(b.batch_id, f"GATES: {len(dlq)} items in DLQ")
        with open(d / "judgments.jsonl", "w") as f:
            for j in judgments:
                f.write(json.dumps(j) + "\n")
    finally:
        q.close()
    dt = time.perf_counter() - t0
    b.metrics["gates"] = {"judged": len(judgments), "errors": errs,
                          "cost_usd": _round_cost(total_cost),
                          "seconds": round(dt, 1)}
    res_counts: dict[str, int] = {}
    for j in judgments:
        st = j.get("resolution_status")
        if st:
            res_counts[st] = res_counts.get(st, 0) + 1
    if res_counts:
        b.metrics["gates"]["resolutions"] = res_counts
        _log(b.batch_id,
             f"GATES: mention resolutions judged — {res_counts}")
    _record_cost(b, "GATES", _round_cost(total_cost))
    save_batch(b, root)
    advance(b, root, [*stages, "GATES"])
    _log(b.batch_id, f"GATES done: {len(judgments) - errs} ok, "
                     f"cost={_fmt_cost(total_cost)}, {errs} errors")


def _c1_whitelist(setup_cfg: dict | None) -> set | None:
    """C1 (verb-class, preposition) whitelist from the corpus
    profile's setup_config.

    Returns None when the profile declares no whitelist (CUAD:
    c1_whitelist is null with a derivation note) — the caller then
    applies NO C1 demotions and logs that fact; a whitelist is never
    invented at route time (playbook: derive per corpus from the
    first batch's relation census). Accepted shapes: a list of
    [verb, prep] pairs, or c1_profile-style rules
    [{verbs: [...], preps: [...]}]."""
    if not setup_cfg:
        return None
    raw = setup_cfg.get("c1_whitelist")
    if not raw:
        return None
    allow: set = set()
    if isinstance(raw, dict):
        raw = raw.get("rules") or []
    for item in raw:
        if isinstance(item, dict):
            for v in item.get("verbs", []):
                for p in item.get("preps", []):
                    allow.add((v, p))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            allow.add((item[0], item[1]))
    return allow


def stage_route(b: Batch, root: Path, stages: list[str]) -> None:
    """ROUTE: tier decision + review queue, Arm C baseline (§5).

    Design (2026-10-11):
    - Dropped candidates are saved to dropped_queue.jsonl for audit.
    - Human can review false negatives. Nothing is truly lost.

    Arm C demotions (the standalone Arm C route logic,
    wired in): a would-be Tier 1 item carrying C7 fields is demoted
    to Tier 2 — never dropped — when
      C4  asserted is False (negated/modal extraction), or
      C1  its object binding is prep:<lemma> and the (verb, prep)
          pair is not in the corpus profile's C1 whitelist.
    Items without C7 fields (LLM-proposed candidates) carry no
    asserted/path, so neither rule fires on them — their guards ran
    at proposal time (pipeline/proposer.py). C2 theme-presence likewise
    lives at extraction level (§5), not here.

    Mention layer (issue #54): a judgment whose resolution
    landed in the review band (resolution_status "review")
    is capped at Tier 2 — the ambiguous resolution goes to
    the human review queue with resolution_review set,
    never a silent Tier-1 accept. Dropped rows gain a
    reason_class when the judgment carries mention-layer
    fields: the drop_reason itself ("resolution_rejected")
    or "fact_rejected" — the two drop classes the pre-layer
    accounting could not tell apart.
    """
    d = batch_dir(root, b.batch_id)
    judgments = [json.loads(l) for l in open(d / "judgments.jsonl")]
    cfg = b.config
    setup_cfg = _load_setup_config(b, root)
    c1_allow = _c1_whitelist(setup_cfg)
    if c1_allow is None:
        _log(b.batch_id, "ROUTE: no C1 whitelist in profile "
                         "(c1_whitelist null/absent) — Arm C C1 "
                         "demotions skipped, none invented")
    counts = {"tier1": 0, "tier2": 0, "dropped": 0}
    demotes = {"C4": 0, "C1": 0}
    res_caps = 0
    with open(d / "routed.jsonl", "w") as rf, \
            open(d / "review_queue.jsonl", "w") as qf, \
            open(d / "dropped_queue.jsonl", "w") as df:
        for j in judgments:
            p = j.get("p_true", 0.0) or 0.0
            if p >= cfg.review_band_hi:
                tier = 1
            elif p >= cfg.triple_threshold:
                tier = 2
            else:
                tier = 0
            reason = None
            if tier == 1:
                path = j.get("path") or ""
                if j.get("asserted") is False:
                    reason = "C4"
                elif c1_allow is not None and \
                        path.startswith("prep:"):
                    prep = path[5:]
                    if (j.get("relation"), prep) not in c1_allow:
                        reason = "C1"
                if reason:
                    tier = 2
                    demotes[reason] += 1
                    j["armc_demote"] = reason
            if tier == 1 and j.get("resolution_status") == "review":
                tier = 2
                res_caps += 1
                j["resolution_capped"] = True
            if tier == 1:
                j["tier"], j["tag"] = 1, "VERIFIED"
                counts["tier1"] += 1
            elif tier == 2:
                j["tier"], j["tag"] = 2, "UNVERIFIED"
                counts["tier2"] += 1
                qrow = {
                    "batch_id": b.batch_id, "run_id": b.run_id,
                    "doc_id": j["doc_id"], "subject": j["subject"],
                    "relation": j["relation"], "object": j["object"],
                    "p_true": p, "status": "TO_BE_REVIEWED"}
                if j.get("resolution_capped"):
                    qrow["resolution_review"] = True
                qf.write(json.dumps(qrow) + "\n")
            else:
                j["tier"], j["tag"] = 0, "DROPPED"
                counts["dropped"] += 1
                drow = {
                    "batch_id": b.batch_id, "run_id": b.run_id,
                    "doc_id": j["doc_id"], "subject": j["subject"],
                    "relation": j["relation"], "object": j["object"],
                    "p_true": p, "status": "DROPPED",
                    # Carry the gate error through, so a
                    # dropped row whose gate call FAILED (judgments
                    # row error, e.g. DLQ) is distinguishable from a
                    # judged negative everywhere downstream.
                    "error": j.get("error")}
                # Mention-layer reason classes (issue #54):
                # a rejected resolution is not a rejected
                # fact, and the drop record says which.
                if j.get("drop_reason"):
                    drow["reason_class"] = j["drop_reason"]
                elif j.get("resolution_status"):
                    drow["reason_class"] = "fact_rejected"
                df.write(json.dumps(drow) + "\n")
            rf.write(json.dumps(j) + "\n")
    b.metrics["route"] = {**counts, "armc_demotes": demotes,
                          "resolution_review_caps": res_caps,
                          "route_baseline": "arm_c"}
    _record_cost(b, "ROUTE", 0.0)  # deterministic local routing
    save_batch(b, root)
    advance(b, root, [*stages, "ROUTE"])
    _log(b.batch_id, f"ROUTE done: {counts}, Arm C demotes: {demotes}")


# Cross-document merge guard (option B, decided 2026-10-16): a
# cross-document form-variant pair may auto-merge only when the Jev
# ER gate scores it at or above this p_same. Below it — or unscored —
# the pair is recorded to the review band and never merged.
ER_XDOC_MERGE_MIN = 0.85


def stage_write(b: Batch, root: Path, stages: list[str],
                registry, graph, er_decide_fn=None) -> None:
    """WRITE: registry-first, then graph. (Consistency §10.1)

    Entity resolution (the form-variant guardrail + the
    cross-document merge guard, option B — decided 2026-10-16):
    form variants of the name
    itself merge deterministically WITHIN a document; a cross-
    document form-variant pair may merge only when the Jev ER gate
    scores it p_same >= 0.85 (score recorded on the merge row);
    every other pair goes to the review band — recorded in
    er_decisions with the gate's p_same when consulted, never
    auto-merged (see er_match).
    Relations are normalized at this stage.

    Typed mentions: a candidate may carry
    subject_type / object_type (proposed under the profile's schema
    guidance, validated at CANDIDATES). The mention's type becomes
    its entity's registry label, and the profile's configured
    merge_prohibited_type_pairs bind BOTH ER channels — a
    prohibited pair never merges, within-doc or cross-doc, and is
    recorded to the review band with the prohibition as the
    reason. Untyped mentions / no configured prohibitions =
    the pre-existing behavior exactly.
    """
    from .registry import Entity, Triple
    d = batch_dir(root, b.batch_id)
    tier1 = []
    with open(d / "routed.jsonl") as f:
        for line in f:
            j = json.loads(line)
            if j.get("tier") == 1:
                tier1.append(j)
    _log(b.batch_id, f"WRITE: {len(tier1)} Tier 1 triples")
    # Corpus namespace: the batch's source is its corpus family
    # (§4; same rule RegistryStore.corpus applies to batch.json).
    corpus = b.source
    setup_cfg = _load_setup_config(b, root)
    from .profile_config import prohibited_type_pairs, type_pair_prohibited
    merge_prohibitions = prohibited_type_pairs(setup_cfg)
    if merge_prohibitions:
        _log(b.batch_id,
             f"WRITE: {len(merge_prohibitions)} type-pair merge "
             f"prohibitions active")

    er_cost = 0.0  # Jev ER gate spend, surfaced into the ledger below
    er_review = 0  # pairs recorded to the ER review band (no merge)
    er_gate_merges = 0  # cross-doc merges via the ER gate (>= 0.85)
    er_xdoc_blocked = 0  # cross-doc form-variant pairs held to review
    er_prohibited_blocked = 0  # pairs held by a type prohibition

    def er_match(name: str, label: str, context: str,
                 doc_id: str, mention_type: str = ""
                 ) -> tuple[str, float | None] | None:
        """Resolve a mention to (entity_id, gate_p_same), or None.

        gate_p_same is None for a deterministic within-document
        merge (no gate consulted) and the recorded Jev ER score for
        a cross-document merge the merge guard admitted.

        ER guardrail (§5 — replaces the earlier mechanism whose
        merges measured 0.283 precision) + the cross-document
        merge guard (option B, decided 2026-10-16) + type
        prohibitions:
        0. Type-pair prohibition: when the mention carries a type,
           the candidate entity's stored type (its label) differs,
           and the profile configures that pair in
           merge_prohibited_type_pairs, the pair NEVER merges in
           either channel below — it is recorded to the review band
           with the prohibition as the reason, and scanning
           continues with the next candidate.
        1. Form-variant merge, same document: a candidate whose
           name (or an alias) is a form variant of the mention under
           is_form_variant AND whose home doc (entity.doc_id) is the
           mention's doc merges deterministically — no gate call.
        2. Form-variant, cross-document (or unknown provenance —
           either doc id missing): the Jev ER gate MUST score the
           pair first. p_same >= ER_XDOC_MERGE_MIN auto-merges with
           the score recorded on the merge's er_decisions row;
           below that (or unscored / gate failure) the pair is NEVER
           merged — it is recorded to the review band with its score.
           (The census behind the guard measured unguarded cross-document
           form-variant merges at 0.057 precision.)
        3. Everything else goes to the REVIEW BAND: the pair is
           never auto-merged on similarity. For plausible pairs
           (shared non-generic token; at most 2 per mention) the
           Jev ER gate is consulted and the pair + p_same + model
           are recorded in er_decisions (outcome='review') for
           human adjudication; the mention becomes its own entity.

        Human settlements bind every channel: a pair
        settled keep-separate (`batch er-decide`) is checked
        before the gate in channels (2) and (3) and before the
        within-doc merge in (1) — it is never re-held, never
        re-consulted, and never merged.
        """
        nonlocal er_cost, er_review, er_gate_merges, er_xdoc_blocked
        nonlocal er_prohibited_blocked
        from .registry import _GENERIC_TOKENS, index_normalize, is_form_variant

        def settled_kept_separate(candidate_id: str) -> bool:
            """A pair a human settled keep-separate
            is never re-held — the gate is not re-consulted and
            no new review row is recorded, in either channel
            below. Registries without a decision store settle
            nothing (the interface default)."""
            fn = getattr(registry, "settled_er_decision", None)
            if fn is None:
                return False
            row = fn(name, candidate_id)
            return bool(row) and row.get("outcome") == "kept_separate"
        # Corpus-scoped by default: resolution never crosses namespaces
        # (the cross-corpus name-contamination class). Candidates are
        # looked up UNTYPED (label=""): the registry filters on a
        # non-empty label, which would hide exactly the cross-type
        # candidates the prohibition below must see.
        cands = registry.find_candidates(name, label=label, limit=5,
                                         corpus=corpus)
        if not cands:
            return None
        fv_handled: set[str] = set()  # pairs step (2) already decided
        for c in cands:
            if not (is_form_variant(name, c.name) or any(
                    is_form_variant(name, a) for a in c.aliases)):
                continue
            fv_handled.add(c.entity_id)
            # type-pair prohibition: binds BOTH channels —
            # checked before the within-doc / cross-doc split, the
            # pair never merges and the gate is never consulted.
            # The pair is recorded to the review band with the
            # prohibition as the recorded reason. Either side
            # untyped (empty) -> the prohibition cannot fire.
            if type_pair_prohibited(mention_type, c.label,
                                    merge_prohibitions):
                registry.record_er_decision(
                    b.batch_id, b.run_id, name, c.entity_id, c.name,
                    outcome="review",
                    reason=(f"merge prohibited by profile "
                            f"(merge_prohibited_type_pairs): "
                            f"{mention_type} x {c.label}"))
                er_review += 1
                er_prohibited_blocked += 1
                _log(b.batch_id,
                     f"  ER merge prohibited by profile: '{name}' "
                     f"({mention_type}) vs '{c.name}' ({c.label}) "
                     f"— recorded to review, NOT merged")
                continue
            if settled_kept_separate(c.entity_id):
                _log(b.batch_id,
                     f"  ER pair settled keep-separate (human): "
                     f"'{name}' vs '{c.name}' — not re-held, "
                     f"NOT merged")
                continue
            if doc_id and c.doc_id and doc_id == c.doc_id:
                return c.entity_id, None  # (1) within-doc: as before
            # (2) cross-document / unknown provenance: gate first.
            p_same = None
            if er_decide_fn is not None:
                try:
                    p_same, usage = er_decide_fn(
                        name, c.name, label, context,
                        model=b.config.gate_model)
                    er_cost = _conn_add_cost(
                        er_cost, (usage or {}).get("cost", 0))
                except Exception as e:
                    _log(b.batch_id,
                         f"  ER gate error for cross-doc pair "
                         f"'{name}' vs '{c.name}': {e}")
                    p_same = None
            if p_same is not None and p_same >= ER_XDOC_MERGE_MIN:
                er_gate_merges += 1
                _log(b.batch_id,
                     f"  ER cross-doc gate merge: '{name}' -> "
                     f"'{c.name}' (gate p_same={p_same:.2f} "
                     f">= {ER_XDOC_MERGE_MIN})")
                return c.entity_id, p_same
            registry.record_er_decision(
                b.batch_id, b.run_id, name, c.entity_id, c.name,
                model=b.config.gate_model if p_same is not None else "",
                p_same=p_same, outcome="review")
            er_review += 1
            er_xdoc_blocked += 1
            _log(b.batch_id,
                 f"  ER cross-doc held to review: '{name}' vs "
                 f"'{c.name}' (gate p_same="
                 f"{'unscored' if p_same is None else f'{p_same:.2f}'}, "
                 f"NOT merged)")
        if er_decide_fn is None:
            return None
        name_toks = set(index_normalize(name).split()) - _GENERIC_TOKENS
        consulted = 0
        for c in cands:
            if consulted >= 2:
                break
            if c.entity_id in fv_handled:
                continue  # step (2) scored + recorded this pair already
            cand_toks = set(index_normalize(c.name).split())
            if not (name_toks & cand_toks):
                continue  # index noise, not a plausible pair
            if settled_kept_separate(c.entity_id):
                _log(b.batch_id,
                     f"  ER pair settled keep-separate (human): "
                     f"'{name}' vs '{c.name}' — not re-held, "
                     f"NOT merged")
                continue
            consulted += 1
            try:
                p_same, usage = er_decide_fn(
                    name, c.name, label, context,
                    model=b.config.gate_model)
                er_cost = _conn_add_cost(
                    er_cost, (usage or {}).get("cost", 0))
            except Exception as e:
                _log(b.batch_id, f"  ER review error for '{name}': {e}")
                continue
            registry.record_er_decision(
                b.batch_id, b.run_id, name, c.entity_id, c.name,
                model=b.config.gate_model, p_same=p_same,
                outcome="review")
            er_review += 1
            _log(b.batch_id,
                 f"  ER review band: '{name}' vs '{c.name}' "
                 f"(gate p_same={p_same:.2f}, NOT merged)")
        return None

    # 1. Registry first (source of truth)
    ent_ids: dict[str, str] = {}
    er_merges = 0
    for j in tier1:
        for name, type_key in ((j["subject"], "subject_type"),
                               (j["object"], "object_type")):
            key = name.lower()
            if key in ent_ids:
                continue
            # The mention's validated type (when the
            # candidate carries one) is stored as the entity's
            # registry label and is er_match's prohibition operand.
            # er_match still looks candidates up untyped (label="")
            # exactly as before. Untyped mention -> label "" ->
            # legacy behavior.
            label = j.get(type_key) or ""
            hit = er_match(name, "", j.get("context", "")[:500],
                           j["doc_id"], mention_type=label)
            if hit:
                existing, gate_p = hit
                new_ent = Entity(entity_id="", name=name, label=label,
                                 batch_id=b.batch_id, run_id=b.run_id,
                                 doc_id=j["doc_id"], corpus=corpus)
                if gate_p is None:
                    ent_ids[key] = registry.merge(existing, new_ent)
                else:
                    # Gate-admitted cross-doc merge: the score
                    # is an input of the decision — record it on the
                    # merge's er_decisions row.
                    ent_ids[key] = registry.merge(
                        existing, new_ent, model=b.config.gate_model,
                        p_same=gate_p)
                er_merges += 1
            else:
                ent_ids[key] = registry.create(
                    Entity(entity_id="", name=name, label=label,
                           batch_id=b.batch_id, run_id=b.run_id,
                           doc_id=j["doc_id"], corpus=corpus))
    # 1.4. Store identity: the store holds ONE
    # graph — this build's. When the store's stamp names a
    # different graph, clear it (named, counted) before
    # projecting this one; content no stamp identifies stops
    # the stage by name (the run preflight refuses first —
    # this is the same check at the point of writing). The
    # registry keeps the replaced graph's full history; only
    # the store's current state is replaced.
    store_client = None
    store_replaced = None
    _client = getattr(graph, "c", None)
    if _client is not None:
        from .neo4j_store import Neo4jClient
        if isinstance(_client, Neo4jClient):
            store_client = _client
            from .neo4j_store import (
                batch_graph_identity,
                check_store_write,
                clear_store_graph,
                describe_graph_identity,
            )
            _action, _current = check_store_write(
                store_client, batch_graph_identity(b))
            if _action == "replace" and _current is not None:
                _removed = clear_store_graph(
                    store_client, keep_batch_id=b.batch_id)
                _log(b.batch_id,
                     f"WRITE: replacing the store's current "
                     f"graph ({describe_graph_identity(_current)}) "
                     f"— removed {_removed['rel_edges']} edges, "
                     f"{_removed['entities']} entities, "
                     f"{_removed['chunks']} chunks; the registry "
                     f"keeps the replaced graph's history")
                store_replaced = {
                    "previous_batch": _current["batch_id"],
                    "previous_pack": _current["pack_id"],
                    "previous_pack_version":
                        _current["pack_version"],
                    "previous_triples": _current["triples"],
                    "removed": _removed}
    # 1.5. Project registry entities into the graph. The graph is a
    # projection of the registry (§4): with a file-backed registry the
    # nodes do not exist until projected; with the legacy Neo4j
    # registry create() already wrote them and this idempotent upsert
    # just backfills name/label/aliases/corpus.
    if hasattr(graph, "upsert_entities"):
        records = []
        for eid in sorted(set(ent_ids.values())):
            ent = registry.get(eid)
            if ent is not None:
                records.append({
                    "entity_id": ent.entity_id, "name": ent.name,
                    "label": ent.label, "aliases": ent.aliases,
                    "corpus": ent.corpus or corpus})
        if records:
            graph.upsert_entities(records)
    # 2. Graph second (derived view). Relations are normalized at
    # WRITE: profile vocabulary / explicit map / deterministic
    # canonical form; unmapped surfaces pass through and are counted
    # in relation_map.json for the per-batch report.
    from .relation_map import RelationNormalizer
    normalizer = RelationNormalizer(
        profile_map=(setup_cfg or {}).get("relation_map"),
        vocab=(setup_cfg or {}).get("label_categories") or [])
    triples = []
    for j in tier1:
        stored_rel, _layer = normalizer.normalize(j["relation"])
        triples.append(Triple(
            subject_id=ent_ids[j["subject"].lower()],
            object_id=ent_ids[j["object"].lower()],
            relation=stored_rel, p_true=j["p_true"],
            tier=1, tag="VERIFIED",
            batch_id=b.batch_id, run_id=b.run_id,
            doc_id=j["doc_id"], model=b.config.gate_model))
    n = graph.write_triples(triples)
    # Projection manifest: the exact canonical triples this stage put
    # into the graph. VERIFY reconciles the graph against this file —
    # the WRITE-time ER mapping cannot be reconstructed afterwards
    # from surface names (resolution is order/decision-dependent).
    with open(d / "written_triples.jsonl", "w") as f:
        for t, j in zip(triples, tier1):
            f.write(json.dumps({
                "subject_id": t.subject_id, "relation": t.relation,
                "surface_relation": j["relation"],
                "object_id": t.object_id, "doc_id": t.doc_id}) + "\n")
    rel_report = normalizer.report()
    with open(d / "relation_map.json", "w") as f:
        json.dump(rel_report, f, indent=1)
    # The graph write is complete: stamp the store with this
    # graph's identity — the stamp is what a later
    # build names before replacing this graph, and what
    # serving resolves the store's one graph from.
    if store_client is not None:
        from .neo4j_store import store_inventory, write_graph_stamp
        _inv = store_inventory(store_client)
        write_graph_stamp(store_client, {
            **batch_graph_identity(b),
            "triples": _inv["rel_edges"],
            "entities": _inv["entities"],
            "chunks": _inv["chunks"],
            "stamped_at": datetime.now(UTC).isoformat()})
    b.metrics["write"] = {"triples": n, "entities": len(ent_ids),
                          "er_merges": er_merges,
                          "er_form_variant_merges": er_merges,
                          "er_gate_merges": er_gate_merges,
                          "er_cross_doc_blocked": er_xdoc_blocked,
                          "er_prohibited_blocked": er_prohibited_blocked,
                          "er_review_pairs": er_review,
                          "er_cost_usd": _round_cost(er_cost),
                          "relation_layers": rel_report["layers"],
                          "relations_unmapped":
                              sum(rel_report["unmapped_surfaces"]
                                  .values())}
    if store_replaced is not None:
        b.metrics["write"]["store_replaced"] = store_replaced
    _record_cost(b, "WRITE", _round_cost(er_cost))
    save_batch(b, root)
    advance(b, root, [*stages, "WRITE"])
    _log(b.batch_id, f"WRITE done: {n} triples, {len(ent_ids)} entities, "
                     f"{er_merges} ER merges")
    if er_review:
        # a hold names its remedy on the line that reports
        # it — the settlement flow (er-review prints the decide
        # command in turn), so the reader standing at the WRITE
        # output is never left to hunt for the next step.
        _log(b.batch_id,
             f"WRITE: {er_review} ER pair(s) held for human "
             f"settlement — settle them with: ontosmith batch "
             f"er-review {b.batch_id}")


def _index_embedder(model_name: str):
    """Pick the embedder for INDEX by model name.

    Only hosted provider/model ids (e.g.
    openai/text-embedding-3-large) resolve, through the
    embedder seat's binding. There is no local embedding
    path: the local model measured
    against the large one lost (2026-10-12, measured:
    single-hop recall@10 0.500 -> 0.617 vs MiniLM; see
    rag_eval.py), so a config naming anything else
    refuses here instead of silently embedding with a
    weaker model.
    """
    from .embedder import SeatEmbedder
    if "/" in model_name:
        return SeatEmbedder(model_name)
    raise ValueError(
        f"embed_model {model_name!r} is not a hosted "
        f"provider/model id; the embedder seat binds "
        f"hosted models only (no local embedding) — name "
        f"a provider/model id such as "
        f"openai/text-embedding-3-large")


def _index_serving(b: Batch, root: Path, graph) -> dict | None:
    """INDEX's serving half: ensure the store's serving
    schema and project this batch's chunks into it (§4
    GraphStore.ensure_serving), over the same Neo4j client
    the run writes through — so a completed build answers
    `search` / `serve` with no manual step. Returns the
    ensure report, or None when this run carries no
    production store: stub graph objects (tests, replay
    doubles) expose no Neo4j client, the _preflight_store
    precedent, and are untouched."""
    client = getattr(graph, "c", None)
    if client is None:
        return None
    from .neo4j_store import Neo4jClient
    if not isinstance(client, Neo4jClient):
        return None
    from .graph_store import GraphStore
    from .registry_store import RegistryStore
    gs = GraphStore(client=client,
                    registry_store=RegistryStore(root))
    return gs.ensure_serving(b.batch_id)


def _chunk_documents(texts: dict[str, str],
                     doc_best_tier: dict[str, int],
                     batch_id: str) -> tuple[list[str], list[dict]]:
    """The serving chunk universe of a build: EVERY document,
    in sorted doc order, cut into 1000-char windows (a window
    is kept when its stripped length exceeds 100 chars).

    Each chunk is tagged by its document's best routed tier —
    tier 1 → VERIFIED, otherwise UNVERIFIED; a document with
    no routed rows defaults to tier 2 / UNVERIFIED. This is
    the tagging query_stack.project_serving applies to the
    flagship corpus; stage_index and the VERIFY coverage
    check share this one implementation so the stage and its
    invariant can never disagree about what coverage means.

    History: before issue #50 the stage chunked only
    documents holding a Tier-2-routed candidate, so a
    Tier-1-heavy build's serving index silently covered a
    subset of the corpus (multilingual pilot, 2026-10-27:
    1 of 16 documents). Sorted iteration keeps the index a
    deterministic function of the inputs."""
    chunks, meta = [], []
    for doc_id in sorted(texts):
        text = texts[doc_id]
        tier = doc_best_tier.get(doc_id, 2)
        tag = "VERIFIED" if tier == 1 else "UNVERIFIED"
        for i in range(0, len(text), 1000):
            ch = text[i:i + 1000]
            if len(ch.strip()) > 100:
                chunks.append(ch)
                meta.append({"doc_id": doc_id, "chunk_idx": i // 1000,
                             "tag": tag, "tier": tier,
                             "batch_id": batch_id})
    return chunks, meta


def stage_index(b: Batch, root: Path, stages: list[str],
                graph=None) -> None:
    """INDEX: document chunks → embeddings → vector index —
    and the serving projection: the stage ensures the
    store's serving schema and projects the chunks it just
    embedded into the store, so the build's finish line
    (search / serve) works on a fresh store with no step in
    between.

    The chunk universe is the whole build (every document,
    tagged by its best routed tier) — see _chunk_documents.
    The artifact keeps its historical name
    (tier2_index.pkl): stored batches and the serving
    readers resolve it by that name; its scope is no longer
    Tier 2 only."""
    import pickle
    _log(b.batch_id, "INDEX: building vector index over all documents")
    d = batch_dir(root, b.batch_id)
    texts = {}
    with open(d / "texts.jsonl") as f:
        for line in f:
            r = json.loads(line)
            texts[r["doc_id"]] = r["text"]
    doc_best_tier: dict[str, int] = {}
    with open(d / "routed.jsonl") as f:
        for line in f:
            j = json.loads(line)
            t = j.get("tier")
            if t in (1, 2):
                cur = doc_best_tier.get(j["doc_id"])
                if cur is None or t < cur:
                    doc_best_tier[j["doc_id"]] = t
    chunks, meta = _chunk_documents(texts, doc_best_tier, b.batch_id)
    if not chunks:
        _log(b.batch_id, "INDEX: no chunks (no document window "
                         "passed the length rule), skipping")
        b.metrics["index"] = {"chunks": 0, "documents": 0,
                              "documents_total": len(texts)}
        # The schema is still this stage's to ensure: chunk
        # search against a store with no full-text index
        # refuses by name, chunks or no chunks.
        serving = _index_serving(b, root, graph)
        if serving is not None:
            b.metrics["index"]["store_chunks"] = serving["chunks"]
            _log(b.batch_id, "INDEX: serving schema ensured "
                             "(no chunks to project)")
        _record_cost(b, "INDEX", 0.0)
        save_batch(b, root)
        advance(b, root, [*stages, "INDEX"])
        return
    embedder = _index_embedder(b.config.embed_model)
    embs = embedder.embed(chunks)
    with open(d / "tier2_index.pkl", "wb") as f:
        pickle.dump({"embeddings": embs, "meta": meta,
                     "chunks": chunks,
                     "model": b.config.embed_model}, f)
    emb_usage = embedder.usage
    n_docs = len({m["doc_id"] for m in meta})
    b.metrics["index"] = {"chunks": len(chunks),
                         "documents": n_docs,
                         "documents_total": len(texts),
                         "dims": int(embs.shape[1]),
                         "model": b.config.embed_model,
                         "input_tokens": emb_usage["input_tokens"]}
    # Serving projection: schema + this batch's chunks into
    # the store, over the run's own client. A projection
    # failure fails the stage here — a build that cannot be
    # served must not sail into VERIFY silent.
    serving = _index_serving(b, root, graph)
    if serving is not None:
        b.metrics["index"]["store_chunks"] = serving["chunks"]
        if serving["projected"] is not None:
            b.metrics["index"]["store_chunks_projected"] = \
                serving["projected"]["chunks"]
        _log(b.batch_id,
             f"INDEX: serving schema ensured; "
             f"{serving['chunks']} chunks in store for this "
             f"batch")
    # Ledger: the embedder's actual spend (SeatEmbedder
    # accumulates usage from each response; token count is recorded
    # in metrics above either way). Cost may be None (unmetered
    # binding) — the ledger records it as unknown, not 0.0.
    _record_cost(b, "INDEX",
                 None if emb_usage["cost"] is None
                 else round(emb_usage["cost"], 4))
    save_batch(b, root)
    advance(b, root, [*stages, "INDEX"])
    _log(b.batch_id, f"INDEX done: {len(chunks)} chunks over "
                     f"{n_docs} of {len(texts)} documents")


def stage_verify(b: Batch, root: Path, stages: list[str],
                 registry, graph) -> None:
    """VERIFY: consistency checks across registry, graph, and files.

    Checks:
    1. Every graph edge references an existing registry entity.
    2. Tier counts match routed.jsonl.
    3. All Tier 1 edges are tagged VERIFIED.
    4. Review queue count matches Tier 2 count.
    5. Graph projection matches the WRITE manifest exactly
       (batch-wide): the set of (subject_id, relation, object_id)
       edges in the graph equals the set recorded in
       written_triples.jsonl at WRITE time. Catches partial WRITE
       runs and residue from abandoned runs. Batches written before
       the manifest existed skip this check (pass=None).
    6. Store drift spot audit: a deterministic sample of
       manifest attestations at (subject, relation, object, doc_id)
       granularity compared against the store at property level
       (p_true, tier) — sees per-document attestation loss and
       property drift that set equality cannot. pass=None without
       a manifest; with a manifest, a property join of 0
       attestations FAILS the check —
       VERIFIED is never recorded on key existence alone.
    7. Index artifact match: tier2_index.pkl is opened and
       its chunk count and embedding model are compared against
       the INDEX metrics; when INDEX recorded 0 chunks the file
       must not exist. pass=None when no INDEX metrics exist.
    8. Serving coverage: the distinct documents in the batch's
       chunk index must equal the chunkable documents in
       texts.jsonl (expected coverage re-derived with the
       shared chunking rule, not read from INDEX metrics);
       with a production store attached, the store's chunk
       documents for the batch must match as well. Partial
       coverage FAILS — a green VERIFY means the serving
       index covers the whole build (issue #50).

    Graph checks are scoped by batch_id, NOT run_id: edges legitimately
    carry the id of the run that minted them, so run-scoping made the
    checks vacuous whenever VERIFY ran under a fresh run.
    """
    _log(b.batch_id, "VERIFY: running consistency checks")
    d = batch_dir(root, b.batch_id)
    checks: dict[str, dict] = {}

    # 1. Graph edges → registry entities: every endpoint
    # of every REL edge carrying this batch_id must resolve in the
    # REGISTRY, the source of truth. The previous form compared the
    # graph against its own nodes — an endpoint trivially "exists" as
    # the node its edge is attached to — so the check could not fail
    # even when the registry had never heard of the entity, and the
    # id-less-endpoint count it computed was discarded. Now the
    # endpoints are enumerated from the graph and looked up in the
    # registry one by one; an endpoint with no entity_id, or no
    # registry row, counts as dangling.
    if hasattr(graph, "c"):  # Neo4jGraphStore
        if registry is not None and hasattr(registry, "get"):
            rows = graph.c.run([{"statement":
                "MATCH (a)-[r:REL {batch_id: $b}]->(bb) "
                "RETURN a.entity_id, bb.entity_id",
                "parameters": {"b": b.batch_id}}])[0]
            endpoints = [r["row"] for r in rows]
            seen_ids = {eid for pair in endpoints for eid in pair}
            missing = sorted(eid for eid in seen_ids
                             if eid is not None
                             and registry.get(eid) is None)
            missing_set = set(missing)
            if None in seen_ids:
                missing_set.add(None)
            dangling = sum(1 for s, o in endpoints
                           if s in missing_set or o in missing_set)
            checks["no_dangling_edges"] = {
                "pass": dangling == 0, "dangling": dangling,
                "edges_checked": len(endpoints),
                "missing_endpoints": missing[:10]}
        else:
            checks["no_dangling_edges"] = {
                "pass": None,
                "note": "no registry handle; registry-vs-graph "
                        "comparison not possible"}
    else:
        checks["no_dangling_edges"] = {"pass": None,
                                       "note": "not a Neo4j store"}

    # 2. Tier counts match
    tier_counts = {"tier1": 0, "tier2": 0, "dropped": 0}
    with open(d / "routed.jsonl") as f:
        for line in f:
            t = json.loads(line).get("tier", 0)
            if t == 1:
                tier_counts["tier1"] += 1
            elif t == 2:
                tier_counts["tier2"] += 1
            else:
                tier_counts["dropped"] += 1
    m = b.metrics.get("route", {})
    checks["tier_counts_match"] = {
        "pass": (m.get("tier1") == tier_counts["tier1"]
                 and m.get("tier2") == tier_counts["tier2"]
                 and m.get("dropped") == tier_counts["dropped"]),
        "routed_file": tier_counts, "metrics": m}

    # 3. All Tier 1 edges tagged VERIFIED (batch-wide)
    if hasattr(graph, "c"):
        bad_tags = graph.c.one(
            "MATCH ()-[r:REL {batch_id: $b, tier: 1}]->() "
            "WHERE r.tag <> 'VERIFIED' RETURN count(r)",
            {"b": b.batch_id}) or 0
        checks["tier1_all_verified"] = {"pass": bad_tags == 0,
                                        "bad_tags": bad_tags}

    # 4. Review queue matches Tier 2
    q_count = sum(1 for _ in open(d / "review_queue.jsonl"))
    checks["review_queue_match"] = {
        "pass": q_count == tier_counts["tier2"],
        "queue": q_count, "tier2": tier_counts["tier2"]}

    # 5. Graph projection matches the WRITE manifest (batch-wide).
    manifest_p = d / "written_triples.jsonl"
    if hasattr(graph, "c") and manifest_p.exists():
        expected = set()
        with open(manifest_p) as f:
            for line in f:
                j = json.loads(line)
                expected.add((j["subject_id"], j["relation"],
                              j["object_id"]))
        edge_rows = graph.c.run([{"statement":
            "MATCH (a:Entity)-[r:REL {batch_id: $b}]->(bb:Entity) "
            "RETURN a.entity_id, r.type, bb.entity_id",
            "parameters": {"b": b.batch_id}}])[0]
        actual = {(r["row"][0], r["row"][1], r["row"][2])
                  for r in edge_rows}
        missing = expected - actual
        extra = actual - expected
        checks["graph_projection_match"] = {
            "pass": not missing and not extra,
            "graph_edges": len(actual), "expected_edges": len(expected),
            "missing": len(missing), "extra": len(extra),
            "missing_sample": sorted(missing)[:5],
            "extra_sample": sorted(extra)[:5]}
    elif hasattr(graph, "c"):
        checks["graph_projection_match"] = {
            "pass": None,
            "note": "no written_triples.jsonl (batch predates the "
                    "WRITE manifest); projection not reconciled"}
    else:
        checks["graph_projection_match"] = {
            "pass": None, "note": "not a Neo4j store"}

    # 6. Store drift spot audit. Check 5 compares edge SETS
    #    at (subject, relation, object) granularity; it cannot see
    #    per-document attestation loss (an attestation collapse
    #    that hid 1,478 attestations under near-equal counts) or per-edge property
    #    drift (p_true / tier mutated after WRITE). This check
    #    re-derives expected (subject, type, object, doc_id) rows
    #    from the batch manifest + routed rows and compares a
    #    deterministic sample against the store at property level,
    #    using pipeline/drift_check.py as the single implementation.
    #    pass=None when no manifest exists (same semantics as 5).
    if hasattr(graph, "c") and manifest_p.exists():
        from . import drift_check as dc
        exp, join = dc.expected_from_manifest(
            b.batch_id, root=root, registry=registry)
        act = dc.actual_from_store(b.batch_id, client=graph.c)
        rep = dc.audit(exp, act, sample_n=200)
        # Guard: a property audit that joined
        # 0 attestations compared nothing — the check FAILS and
        # VERIFIED is blocked; it never degrades to a silent pass
        # on key existence alone. (The pre-fix audit resolved the
        # wrong registry + a corpus field save_state never wrote,
        # joined 0 on a real batch, and still recorded VERIFIED.)
        prop_joined = join.get("joined", 0)
        drift_entry = {
            "pass": bool(rep["drift_free"]) and prop_joined > 0,
            "sample_checked": rep["sampled_expected"],
            "missing": rep["missing"],
            "extra": rep["extra"],
            "property_drift": rep["property_drift"],
            "expected_total": rep["expected_total"],
            "actual_total": rep["actual_total"],
            "join": join}
        if prop_joined == 0:
            drift_entry["note"] = (
                "property audit joined 0 attestations — no "
                "p_true/tier comparison was performed, so the "
                "drift check fails rather than passing on key "
                "existence alone")
        checks["drift_spot_audit"] = drift_entry
    elif hasattr(graph, "c"):
        checks["drift_spot_audit"] = {
            "pass": None,
            "note": "no written_triples.jsonl (batch predates "
                    "the WRITE manifest); the property audit did "
                    "not run — no attestation properties were "
                    "compared"}
    else:
        checks["drift_spot_audit"] = {
            "pass": None, "note": "not a Neo4j store"}

    # 7. Tier-2 index artifact vs INDEX metrics: the
    # pickle is opened and its chunk count and embedding model are
    # compared against what INDEX recorded in metrics. Existence of
    # the file alone proved nothing — a stale or foreign index would
    # have passed a stage that never looked inside the artifact.
    idx_m = b.metrics.get("index") or {}
    n_chunks = idx_m.get("chunks")
    pkl_p = d / "tier2_index.pkl"
    if n_chunks is None:
        checks["tier2_index_match"] = {
            "pass": None, "note": "no INDEX metrics recorded"}
    elif n_chunks == 0:
        checks["tier2_index_match"] = {
            "pass": not pkl_p.exists(), "chunks": 0,
            "note": "INDEX recorded 0 chunks; no index file may exist"}
    elif not pkl_p.exists():
        checks["tier2_index_match"] = {
            "pass": False, "chunks": n_chunks,
            "note": "INDEX recorded chunks but tier2_index.pkl "
                    "is missing"}
    else:
        import pickle
        try:
            with open(pkl_p, "rb") as f:
                idx_data = pickle.load(f)
            pkl_chunks = len(idx_data.get("chunks") or [])
            pkl_model = idx_data.get("model")
            checks["tier2_index_match"] = {
                "pass": pkl_chunks == n_chunks
                        and pkl_model == idx_m.get("model"),
                "chunks": n_chunks, "pkl_chunks": pkl_chunks,
                "model": idx_m.get("model"), "pkl_model": pkl_model}
        except Exception as e:
            checks["tier2_index_match"] = {
                "pass": False, "chunks": n_chunks,
                "note": f"tier2_index.pkl unreadable: {e}"}

    # 8. Serving coverage: the chunk index must cover every
    #    chunkable document in the build. Checks 1-7 assert
    #    the index artifact is consistent with INDEX's own
    #    metrics; none of them compared the index against the
    #    corpus — which is how a build whose serving index
    #    held 1 of 16 documents recorded VERIFIED (the
    #    Tier-2-only chunking of issue #50, measured by the
    #    multilingual pilot, 2026-10-27). Expected coverage
    #    is re-derived here from texts.jsonl with the shared
    #    chunking rule (_chunk_documents), never from the
    #    stage's metrics. When the graph is a production
    #    Neo4j store, the store-side chunk documents for this
    #    batch must match too — the projection is what
    #    search actually reads.
    if n_chunks is None:
        checks["serving_coverage"] = {
            "pass": None, "note": "no INDEX metrics recorded"}
    else:
        texts_v: dict[str, str] = {}
        with open(d / "texts.jsonl") as f:
            for line in f:
                r = json.loads(line)
                texts_v[r["doc_id"]] = r["text"]
        _, want_meta = _chunk_documents(texts_v, {}, b.batch_id)
        want_docs = {m["doc_id"] for m in want_meta}
        have_docs: set[str] = set()
        if pkl_p.exists():
            import pickle
            try:
                with open(pkl_p, "rb") as f:
                    have_docs = {m["doc_id"] for m in
                                 (pickle.load(f).get("meta") or [])}
            except Exception:
                have_docs = set()
        missing_docs = sorted(want_docs - have_docs)
        extra_docs = sorted(have_docs - want_docs)
        cov = {"pass": not missing_docs and not extra_docs,
               "documents_expected": len(want_docs),
               "documents_indexed": len(have_docs),
               "missing_documents": missing_docs[:10],
               "extra_documents": extra_docs[:10]}
        client = getattr(graph, "c", None)
        if client is not None:
            from .neo4j_store import Neo4jClient
            if isinstance(client, Neo4jClient):
                try:
                    rows = client.run([{"statement":
                        "MATCH (c:Chunk {batch_id: $b}) "
                        "RETURN DISTINCT c.doc_id",
                        "parameters": {"b": b.batch_id}}])[0]
                    store_docs = {r["row"][0] for r in rows}
                    cov["documents_in_store"] = len(store_docs)
                    store_missing = sorted(want_docs - store_docs)
                    if store_missing:
                        cov["pass"] = False
                        cov["missing_from_store"] = store_missing[:10]
                except Exception as e:
                    cov["store_note"] = (
                        f"store-side coverage unreadable: {e}")
        if not cov["pass"]:
            cov["note"] = (
                "serving index coverage is partial: search "
                "would silently cover only part of this build")
        checks["serving_coverage"] = cov

    all_pass = all(c.get("pass") for c in checks.values()
                   if c.get("pass") is not None)
    b.metrics["verify"] = {"checks": checks, "all_pass": all_pass}
    _record_cost(b, "VERIFY", 0.0)  # local consistency checks
    save_batch(b, root)
    with open(d / "verify_report.json", "w") as f:
        json.dump({"batch_id": b.batch_id, "run_id": b.run_id,
                   "checks": checks, "all_pass": all_pass}, f, indent=1)
    if not all_pass:
        failed = [k for k, v in checks.items() if v.get("pass") is False]
        raise RuntimeError(f"VERIFY failed: {failed}")
    advance(b, root, [*stages, "VERIFY"])
    _log(b.batch_id, f"VERIFY done: all {len(checks)} checks passed")


def _batch_edge_count(graph, batch_id: str) -> int:
    """REL edges in the graph carrying this batch_id (0 when the
    store cannot be queried — purge decisions then stay no-ops)."""
    if graph is None or not hasattr(graph, "c"):
        return 0
    try:
        return graph.c.one(
            "MATCH ()-[r:REL {batch_id: $b}]->() RETURN count(r)",
            {"b": batch_id}) or 0
    except Exception:
        return 0


def purge_batch_attempt(b: Batch, root: Path, graph) -> dict:
    """Retry semantics (decided 2026-10-19): retrying a
    batch that already ran WRITE first DELETES that batch's own
    prior-attempt outputs, then rewrites ("erase, then write again").

    Scoped strictly to this batch_id:
    - graph REL edges carrying this batch_id (all runs — a retry
      mints a fresh run_id, so run-scoped deletion cannot reach
      the prior attempt's edges);
    - this batch's own written_triples.jsonl manifest and
      tier2_index.pkl (a stale manifest/index must never outlive
      the edges it describes).

    Another batch's edges and files are never touched. Entity nodes
    stay: the registry owns them (the rollback
    invariant — rollback deletes edges, not entities). Returns a
    record of what was removed, for batch metrics."""
    d = batch_dir(root, b.batch_id)
    info: dict = {"edges_deleted": 0, "files_removed": []}
    if graph is not None:
        if hasattr(graph, "delete_batch"):
            info["edges_deleted"] = int(
                graph.delete_batch(b.batch_id) or 0)
        elif hasattr(graph, "delete_run"):
            info["edges_deleted"] = int(
                graph.delete_run(b.batch_id, b.run_id) or 0)
        elif hasattr(graph, "_delete_batch_rels"):
            graph._delete_batch_rels(b.batch_id)
            info["edges_deleted"] = -1  # count not reported by store
    for name in ("written_triples.jsonl", "tier2_index.pkl"):
        p = d / name
        if p.exists():
            p.unlink()
            info["files_removed"].append(name)
    return info


def _preflight_bindings(b: Batch, stages: list[str],
                          live_decide: bool) -> None:
    """Refuse before any stage runs when a pending stage needs a
    seat whose key variable is unset.

    The refusal is the connection layer's documented
    :class:`~pipeline.connections.BindingKeyError`, naming the
    seat and the variable — raised here, before INGEST spends
    anything, instead of surfacing mid-run (at CANDIDATES, after
    earlier stages have run, or at the first GATES call) as a
    bare RuntimeError or a late failure. Only seats a *pending*
    stage will actually call are checked: a resumed batch past
    CANDIDATES never re-checks the proposer, and the judge is
    checked only when the caller drives GATES with the live CLI
    decide function (``live_decide``) — stub-driven runs (tests,
    replay) are untouched. The embedder seat is deliberately not
    preflighted: INDEX calls it only when chunks exist,
    and its own call site raises the same named error.
    """
    from . import connections as _conn
    # The proposer is the only candidate source (the pipeline
    # source was retired 2026-10-29), so a pending CANDIDATES
    # always needs its key.
    if "CANDIDATES" not in stages:
        _conn.seat("proposer").require_key()
    if live_decide and "GATES" not in stages:
        _conn.seat("judge").require_key()


def _preflight_cost_cap(b: Batch, stages: list[str],
                        live_decide: bool) -> None:
    """Stop-line preflight: when a USD cap is set,
    every seat this run will use must be able to meter its
    spend — otherwise the cap could never trip and the run
    refuses by name BEFORE any paid call (the ledger's
    _record_cost applies the same rule at record time).

    Seats by pending stage: CANDIDATES → proposer; GATES →
    judge (live decide only — stub decide functions in
    tests/replays make no provider calls); WRITE → judge via
    the ER decide function (same live gate); INDEX →
    embedder."""
    if b.config.max_cost_usd is None:
        return
    from . import connections as _conn
    seats = []
    if "CANDIDATES" not in stages:
        seats.append("proposer")
    if live_decide and ("GATES" not in stages
                        or "WRITE" not in stages):
        seats.append("judge")
    if "INDEX" not in stages:
        seats.append("embedder")
    _conn.require_metered(_conn.resolve_bindings(),
                          b.config.max_cost_usd, seats)


def _preflight_store(b: Batch, stages: list[str], graph) -> None:
    """Store preflight: a build writes into Neo4j —
    a required part of the stack (native graph + vector
    index + full-text in one store), not an optional backend.
    When a graph stage (WRITE / INDEX / VERIFY) is still
    pending and the graph object is the production Neo4j
    store, verify reachability and auth BEFORE anything
    runs — including the retry purge, which also
    touches the store — so an absent or misconfigured store
    refuses by name here instead of killing the batch at
    WRITE after the paid stages have run. Stub graph objects (tests, replay doubles)
    expose no Neo4j client and are untouched.

    Identity: the store holds ONE
    graph, stamped with its identity at WRITE. When this
    build will replace a different graph, the preflight
    names that graph NOW — before the paid stages — and
    when the store holds content no stamp identifies, the
    run stops by name (StoreIdentityError): a build never
    overwrites a graph it cannot name."""
    if graph is None:
        return
    if not [s for s in ("WRITE", "INDEX", "VERIFY")
            if s not in stages]:
        return
    client = getattr(graph, "c", None)
    if client is None:
        return
    from .neo4j_store import StoreUnreachableError, probe
    problem = probe(client)
    if problem is not None:
        raise StoreUnreachableError(
            f"{problem}. A build requires a running Neo4j "
            f"(the graph store) — start one and check "
            f"NEO4J_URL / NEO4J_USER / NEO4J_PASSWORD; "
            f"setup is documented in docs/connections.md "
            f"(Neo4j — the graph store).")
    from .neo4j_store import Neo4jClient
    if isinstance(client, Neo4jClient):
        from .neo4j_store import (
            batch_graph_identity,
            check_store_write,
            describe_graph_identity,
        )
        action, current = check_store_write(
            client, batch_graph_identity(b))
        if action == "replace" and current is not None:
            _log(b.batch_id,
                 f"store: currently holds "
                 f"{describe_graph_identity(current)} — this "
                 f"build replaces it at WRITE (the store "
                 f"holds one graph; the registry keeps the "
                 f"replaced graph's full history)")


def _er_terminal_account(registry, batch_id: str) -> dict | None:
    """ER decisions for one batch, counted by terminal state
    (issue #51): merges recorded, pairs held for settlement,
    and of those held, how many a human has settled (merge /
    keep-separate) and how many are still unactioned.

    The counts ride on the registry's own settlement
    semantics (er_review_pairs pairs each review row with its
    settlement row). Returns None when the registry cannot
    account — registries without a decision store hold
    nothing visible (the interface default)."""
    pairs_fn = getattr(registry, "er_review_pairs", None)
    decisions_fn = getattr(registry, "er_decisions", None)
    if pairs_fn is None or decisions_fn is None:
        return None
    try:
        pairs = pairs_fn(batch_id)
        merged = sum(1 for r in decisions_fn()
                     if r.get("batch_id") == batch_id
                     and r.get("outcome") == "merged")
    except Exception:
        return None
    settled_merge = sum(
        1 for p in pairs
        if (p.get("settled") or {}).get("outcome") == "merged")
    settled_separate = sum(
        1 for p in pairs
        if (p.get("settled") or {}).get("outcome")
        == "kept_separate")
    unactioned = sum(1 for p in pairs if p.get("settled") is None)
    return {"merged": merged, "held_total": len(pairs),
            "held_settled_merge": settled_merge,
            "held_settled_keep_separate": settled_separate,
            "held_unactioned": unactioned}


def _finish_summary(b: Batch, root: Path, registry) -> None:
    """The batch finish line (issue #51): a completed build
    accounts for its ER decisions by terminal state in its
    own report — the metrics block and the run log — and a
    build ending with unactioned settlement holds says so
    loudly, naming the remedy.

    Before this, WRITE recorded er_review_pairs in its stage
    metrics and named the remedy on its log line, but the
    finish (the summary a reader signs off on) carried no ER
    accounting at all: the multilingual pilot's build
    finished VERIFIED with 44 of 49 ER decisions held and
    nothing at the finish line said the graph shipped with
    those pairs unmerged. The hold itself is the product's
    measured design (a human decision, never an auto-merge);
    what was missing is that the build must state it.

    The same rule covers CANDIDATES guard blocks (issue
    #54, post-verdict): the stage totals its blocks by
    reason in its own metrics and names them on its log
    line, but the finish — the record a reader signs off
    on — restated none of it, so a guard cliff (the
    mention layer's measured mention_missing cull: 69 of
    257 proposals in the Phase 2 A/B) was indistinguishable
    at the finish from an ordinary sparse run. When the
    stage blocked anything, the finish carries the
    by-reason counts in its metrics and names them on its
    log line.

    And the same rule covers mention-layer fallbacks
    (issue #54, cut 2): endpoints whose mention data was
    absent were judged on the quote under the legacy
    evidence path. The fallback rate is the layer's
    compliance signal — if it is not restated at the
    finish, a run whose proposer never answered the
    mention fields reads as an ordinary mention run. When
    any endpoint fell back, the finish carries the counts
    by endpoint in its metrics and names them on its log
    line.

    And the same rule covers the entity sweep (issue
    #58): the pass's yield and attrition — anchors asked,
    proposals made, duplicates dropped behind the chunk
    channel, off-anchor drops, guard blocks by reason —
    and its shipped Tier-1 count, attributed from the
    routed records, are restated here whenever the sweep
    ran. Without the restatement, a sweep that proposed
    into a void (every anchor call answered off-anchor,
    say) reads at the finish as an ordinary run."""
    account = (_er_terminal_account(registry, b.batch_id)
               if registry is not None else None)
    cand = b.metrics.get("candidates") or {}
    blocked_total = cand.get("blocked") or 0
    blocked_reasons = dict(cand.get("blocked_reasons") or {})
    prop = cand.get("proposer") or {}
    fb_by_endpoint = {
        "subject": prop.get("mention_fallback_subject") or 0,
        "object": prop.get("mention_fallback_object") or 0}
    fb_total = fb_by_endpoint["subject"] + fb_by_endpoint["object"]
    sweep_blk = cand.get("sweep") or {}
    if account is None and not blocked_total and not fb_total \
            and not sweep_blk:
        return
    finish: dict = {}
    if account is not None:
        finish["er"] = account
    if blocked_total:
        finish["candidates_blocked"] = {
            "total": blocked_total,
            "by_reason": blocked_reasons}
    if fb_total:
        finish["candidates_fallback"] = {
            "endpoints": fb_total,
            "by_endpoint": fb_by_endpoint}
    if sweep_blk:
        # Shipped attribution from the routed records:
        # the sweep's candidates are indistinguishable
        # downstream, but their method survives into
        # routed.jsonl, so the finish can say how many of
        # them reached Tier 1.
        shipped_t1 = 0
        routed_p = batch_dir(root, b.batch_id) / "routed.jsonl"
        if routed_p.exists():
            with open(routed_p) as f:
                for line in f:
                    if not line.strip():
                        continue
                    r = json.loads(line)
                    if r.get("method") == "llm-sweep" \
                            and r.get("tier") == 1:
                        shipped_t1 += 1
        finish["candidates_sweep"] = {
            "anchors": sweep_blk.get("anchors", 0),
            "proposed": sweep_blk.get("proposed", 0),
            "duplicates": sweep_blk.get("duplicates", 0),
            "off_anchor": sweep_blk.get("off_anchor", 0),
            "evidence_missing": sweep_blk.get(
                "evidence_missing", 0),
            "blocked": {
                "total": sweep_blk.get("blocked", 0),
                "by_reason": dict(
                    sweep_blk.get("blocked_reasons") or {})},
            "shipped_tier1": shipped_t1}
    b.metrics["finish"] = finish
    save_batch(b, root)
    if account is not None:
        _log(b.batch_id,
             f"finish: ER decisions — {account['merged']} merged, "
             f"{account['held_total']} held for settlement "
             f"({account['held_settled_merge']} settled merge, "
             f"{account['held_settled_keep_separate']} settled "
             f"keep-separate, {account['held_unactioned']} "
             f"unactioned)")
        if account["held_unactioned"]:
            _log(b.batch_id,
                 f"ER SETTLEMENT PENDING: "
                 f"{account['held_unactioned']} held pair(s) are "
                 f"unactioned — this graph ships with those "
                 f"entities unmerged until a human settles them: "
                 f"ontosmith batch er-review {b.batch_id}")
    if blocked_total:
        _log(b.batch_id,
             f"finish: CANDIDATES blocked {blocked_total} "
             f"(by reason: {blocked_reasons}) — see "
             f"candidates_blocked.jsonl")
    if fb_total:
        _log(b.batch_id,
             f"finish: CANDIDATES mention fallback "
             f"{fb_total} endpoint(s) (by endpoint: "
             f"{fb_by_endpoint}) — judged on the quote "
             f"under the legacy evidence path")
    if sweep_blk:
        cs = finish["candidates_sweep"]
        _log(b.batch_id,
             f"finish: CANDIDATES entity sweep "
             f"{cs['anchors']} anchor(s), "
             f"{cs['proposed']} proposed, "
             f"{cs['duplicates']} duplicate(s), "
             f"{cs['off_anchor']} off-anchor, "
             f"{cs['evidence_missing']} evidence-missing, "
             f"{cs['blocked']['total']} blocked "
             f"(by reason: {cs['blocked']['by_reason']}), "
             f"{cs['shipped_tier1']} shipped at Tier 1")


def run_batch(b: Batch, root: Path, doc_texts: dict[str, str] | None,
              decide_fn=None, er_decide_fn=None,
              resolution_decide_fn=None,
              registry=None, graph=None,
              live_decide: bool = False,
              keep_run_id: bool = False) -> Batch:
    """Run all remaining stages. Resumes from checkpoint.

    ``live_decide`` marks that ``decide_fn`` is the CLI's live
    judge function (as opposed to a test/replay stub); it
    enables the binding preflight for the judge seat.

    ``keep_run_id`` is the CLI's --reuse-run: a FAILED batch
    resumes under its existing run_id. This function is the
    single place a resume run_id is minted — callers
    never mint their own, so one resume names exactly one
    run_id."""
    d = batch_dir(root, b.batch_id)
    state_p = d / "state.json"
    stages: list[str] = []
    if state_p.exists():
        stages = json.loads(state_p.read_text()).get("stages_completed", [])
    # Retry rule: a FAILED batch whose failure sits at WRITE or
    # later — WRITE/INDEX in stages_completed, or the stage after
    # the last completed one is WRITE (partial WRITE before the
    # failure was recorded) — is retried by PURGE-THEN-REWRITE:
    # delete this batch's prior-attempt edges and artifacts, roll
    # the checkpoint back to ROUTE, and let WRITE run again from
    # the recorded routed rows. Without the purge, the retry's
    # fresh run_id writes a second edge set beside the first
    # attempt's, and VERIFY (batch-scoped by design) fails on the
    # residue with no self-repair path.
    if b.status == BatchStatus.FAILED and stages and graph is not None \
            and (any(s in stages for s in ("WRITE", "INDEX"))
                 or stages[-1] == "ROUTE"):
        info = purge_batch_attempt(b, root, graph)
        info["purged_run_id"] = b.run_id
        stages = [s for s in stages
                  if s in ("INGEST", "NER", "CANDIDATES", "GATES",
                           "ROUTE")]
        b.metrics["retry_purge"] = info
        save_state(b, root, stages)
        _log(b.batch_id, f"retry purge: {info['edges_deleted']} "
                         f"edges deleted, files {info['files_removed']}; "
                         "checkpoint rolled back to ROUTE")
    # Kill -9 case: the batch sits at ROUTED (checkpoint
    # advanced) but a previous attempt died inside WRITE before any
    # failure could be recorded, leaving partial edges behind.
    # Same rule: purge this batch's edges before WRITE re-enters.
    if b.status == BatchStatus.ROUTED and graph is not None \
            and _batch_edge_count(graph, b.batch_id):
        info = purge_batch_attempt(b, root, graph)
        b.metrics["retry_purge"] = info
        save_batch(b, root)
        _log(b.batch_id, f"retry purge (residue at ROUTED): "
                         f"{info['edges_deleted']} edges deleted, "
                         f"files {info['files_removed']}")
    # Resume from FAILED: map back to the last completed stage's status
    if b.status == BatchStatus.FAILED:
        resume_map = {
            "INGEST": BatchStatus.INGESTED,
            "NER": BatchStatus.NER_DONE,
            "CANDIDATES": BatchStatus.CANDIDATES_DONE,
            "GATES": BatchStatus.GATED,
            "ROUTE": BatchStatus.ROUTED,
            "WRITE": BatchStatus.WRITTEN,
            "INDEX": BatchStatus.INDEXED,
            "VERIFY": BatchStatus.VERIFIED,
        }
        b.status = (resume_map.get(stages[-1], BatchStatus.CREATED)
                    if stages else BatchStatus.CREATED)
        if keep_run_id:
            _log(b.batch_id,
                 f"resuming FAILED batch from {b.status.value} "
                 f"(run_id kept: {b.run_id})")
        else:
            b.run_id = new_run_id()  # failed run is abandoned; new run_id
            _log(b.batch_id,
                 f"resuming FAILED batch from {b.status.value} "
                 f"(new run_id {b.run_id})")
        save_batch(b, root)
    _log(b.batch_id, f"resuming from {b.status.value} "
                     f"(done: {stages})")
    # Provenance: stamp the code version this run executes with. A batch
    # can span several runs (and code fixes between them), so keep the
    # full history, not just the latest hash.
    ver = git_code_version(root)
    if ver:
        if ver != b.config.code_version:
            object.__setattr__(b.config, "code_version", ver)
        if (not b.code_history
                or b.code_history[-1].get("code_version") != ver):
            b.code_history.append({
                "run_id": b.run_id, "code_version": ver,
                "at": datetime.now(UTC).isoformat()})
        save_batch(b, root)
    # Preflight (before the try, so a refusal leaves the batch
    # un-failed and un-started): pending stages' seats must have
    # their keys, or the run refuses with BindingKeyError here.
    _preflight_bindings(b, stages, live_decide and decide_fn is not None)
    # Stop-line preflight, same placement and for
    # the same reason: a USD cap over cost-less seats refuses
    # by name before any stage starts.
    _preflight_cost_cap(b, stages,
                        live_decide and decide_fn is not None)
    # Store preflight, same placement: a build needs
    # Neo4j serving before the first stage runs.
    _preflight_store(b, stages, graph)
    try:
        # Lazy imports for heavy deps
        if b.status == BatchStatus.CREATED:
            assert doc_texts is not None, "doc_texts required for INGEST"
            _check_cost_cap(b)
            _beat(b, root, "INGEST")
            stage_ingest(b, root, doc_texts, stages)
            stages.append("INGEST")
        if b.status == BatchStatus.INGESTED:
            _check_cost_cap(b)
            _beat(b, root, "NER")
            stage_ner(b, root, stages)
            stages.append("NER")
        if b.status == BatchStatus.NER_DONE:
            _check_cost_cap(b)
            _beat(b, root, "CANDIDATES")
            stage_candidates(b, root, stages)
            stages.append("CANDIDATES")
        if b.status == BatchStatus.CANDIDATES_DONE:
            assert decide_fn is not None, "decide_fn required for GATES"
            _check_cost_cap(b)
            _beat(b, root, "GATES")
            stage_gates(b, root, stages, decide_fn,
                        resolution_decide_fn=resolution_decide_fn)
            stages.append("GATES")
        if b.status == BatchStatus.GATED:
            _check_cost_cap(b)
            _beat(b, root, "ROUTE")
            stage_route(b, root, stages)
            stages.append("ROUTE")
        if b.status == BatchStatus.ROUTED:
            assert registry is not None and graph is not None
            _check_cost_cap(b)
            _beat(b, root, "WRITE")
            stage_write(b, root, stages, registry, graph,
                        er_decide_fn=er_decide_fn)
            stages.append("WRITE")
        if b.status == BatchStatus.WRITTEN:
            _check_cost_cap(b)
            _beat(b, root, "INDEX")
            stage_index(b, root, stages, graph=graph)
            stages.append("INDEX")
        if b.status == BatchStatus.INDEXED:
            _check_cost_cap(b)
            _beat(b, root, "VERIFY")
            stage_verify(b, root, stages, registry, graph)
            stages.append("VERIFY")
        _finish_summary(b, root, registry)
        _log(b.batch_id, f"batch run complete at {b.status.value}")
    except Exception as e:
        mark_failed(b, root, stages, str(e)[:500])
        _log(b.batch_id, f"FAILED: {e}")
        raise
    return b
