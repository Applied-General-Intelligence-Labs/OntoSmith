"""Batch CLI: create, run, status, rollback batches.

The batch path is the product's build engine. Every run moves
through the stage contract

    INGEST -> NER -> CANDIDATES -> GATES -> ROUTE -> WRITE
           -> INDEX -> VERIFY

with per-stage state persisted under the batch directory, so a
run can resume after failure without redoing completed stages.

Batch state (the batches/ tree, the registry) lives under
state_root() — see its docstring. Packs resolve through
the pack candidates anchored at state_root(): a ``packs/``
tree in the state root shadows the shipped trees, so a
pack placed beside the user's data is the pack a batch
binds; absent one, the installed data-files tree and then
the source tree beside the code supply the shipped packs.

Usage:
    python -m pipeline batch create --source <name> --collection <coll> --docs <glob>
    python -m pipeline batch run <batch_id>
    python -m pipeline batch status <batch_id>
    python -m pipeline batch rollback <batch_id> --run <run_id>
    python -m pipeline batch list
    python -m pipeline batch er-review <batch_id>
    python -m pipeline batch er-decide <batch_id> --pair <id> --merge|--keep-separate
    python -m pipeline batch er-apply <batch_id>
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

from . import connections
from .batch import (
    Batch,
    BatchConfig,
    BatchStatus,
    batch_dir,
    git_code_version,
    load_batch,
    new_run_id,
    save_batch,
)
from .batch_run import _er_terminal_account, run_batch
from .neo4j_store import Neo4jEntityRegistry, Neo4jGraphStore
from .reference_profile import JUDGE_MODEL
from .reference_profile import SYSTEMONE_URL as SYSTEMONE_URL

ROOT = Path(__file__).resolve().parent.parent


def state_root() -> Path:
    """Where batch state lives: the batches/ tree and the
    registry's data/ directory.

    $ONTOSMITH_ROOT when set. Otherwise the tree beside
    the code when it is a source checkout (it carries
    packs/ — the checkout is the project, and the shipped
    guides run from its root), and the current working
    directory for an installed layout: a wheel install's
    code tree is site-packages, and state written there
    is both misplaced (the guides' batches/… paths are
    working-directory-relative) and fragile (a read-only
    site-packages refuses the write)."""
    env = os.environ.get("ONTOSMITH_ROOT")
    if env:
        return Path(env)
    if (ROOT / "packs").is_dir():
        return ROOT
    return Path.cwd()


def cmd_create(args) -> None:
    """Create a batch: resolve doc globs, resolve the effective
    setup_config through the pack resolver (core baseline +
    the batch's pack, fail-closed), stamp pack provenance,
    persist the batch.

    Resolution: when a pack
    binds the batch — named with --pack, or found through the
    profile's family — the executed setup_config is the
    RESOLVED config (``resolve_setup_config``), written into
    the batch directory as ``setup_config.resolved.json`` so
    the run step executes exactly the bytes creation resolved,
    never a re-read of mutable pack files. A resolution failure
    refuses the whole creation — no fallback to the baseline
    under a named pack. Batches with no binding pack keep the
    legacy behavior (profile-file validation + exact-bytes
    provenance)."""
    paths = []
    for pattern in args.docs:
        paths.extend(glob.glob(pattern, recursive=True))
    paths = sorted(set(paths))
    if not paths:
        print("no documents matched", file=sys.stderr)
        sys.exit(1)
    batch_id = args.batch_id or f"{args.collection}-{len(paths)}docs"
    # Issue #46: create is FRESH-ONLY. Creating over an existing
    # batch id used to rewrite batch.json (new run_id, status
    # CREATED, new resolved config) while state.json and the
    # stage artifacts of the previous run survived — a record
    # `batch status` read as incoherent, and the next run would
    # start from CREATED against a directory full of another
    # run's state. The product idiom (pack emission: a changed
    # pack is a new version, not an overwrite) is to refuse BY
    # NAME before anything is written: the existing batch is
    # run/resumed, or the new build gets a new id. A blind full
    # reset was rejected — the registry holds the old batch's
    # entities and ER decisions, so no reset stays inside the
    # batch directory.
    _existing_dir = batch_dir(state_root(), batch_id)
    if (_existing_dir / "batch.json").exists():
        try:
            _existing = load_batch(state_root(), batch_id)
            _desc = (f"status {_existing.status.value}, "
                     f"run {_existing.run_id}")
        except Exception:
            _desc = "its batch.json cannot be read"
        print(f"error: batch {batch_id!r} already exists "
              f"({_desc}); create makes a new batch and never "
              f"overwrites one. Run or resume the existing "
              f"batch (`ontosmith batch run {batch_id}`), or "
              f"create the new build under a different "
              f"--batch-id.", file=sys.stderr)
        sys.exit(2)
    profile_hash = ""
    profile_family = ""
    if getattr(args, "profile", None):
        prof = json.loads(Path(args.profile).read_text())
        profile_hash = prof.get("profile_hash", "")
        profile_family = prof.get("family", "")
        if not profile_hash:
            print(f"profile {args.profile} has no profile_hash",
                  file=sys.stderr)
            sys.exit(1)

    # --- pack resolution (fail-closed) --------------------------
    from .profile_config import (
        find_pack_dir,
        load_pack_manifest,
        pack_dir_for_family,
        pack_provenance,
        resolve_setup_config,
    )
    pack_dir = None
    if getattr(args, "pack", None):
        try:
            pack_dir = find_pack_dir(state_root(), args.pack)
        except ValueError as e:
            print(f"pack resolution failed: {e}", file=sys.stderr)
            sys.exit(1)
    elif profile_family:
        pack_dir = pack_dir_for_family(state_root(),
                                       profile_family)
    resolved_cfg: dict | None = None
    prov: dict | None = None
    if pack_dir is not None:
        manifest = load_pack_manifest(pack_dir)
        pack_family = manifest.get("profile_family", "")
        if profile_family and pack_family != profile_family:
            print(f"pack {manifest.get('pack_id')!r} binds family "
                  f"{pack_family!r}, not the profile's family "
                  f"{profile_family!r}; refusing to create the "
                  f"batch under a mismatched pack",
                  file=sys.stderr)
            sys.exit(1)
        profile_family = profile_family or pack_family
        try:
            resolved_cfg = resolve_setup_config(state_root(),
                                                pack_dir)
        except ValueError as e:
            print(f"setup_config resolution failed: {e}",
                  file=sys.stderr)
            sys.exit(1)
        # Framing exception (docs/PACK_DESIGN.md §1.3): the
        # family's own generated profile — the one written
        # for the user's corpus — may redeclare the
        # domain-framing keys over the pack's payload.
        # Framing is profile data (profile_config §5), and a
        # corpus whose genre differs from the pack's
        # measured one (a novel under the encyclopedia pack)
        # otherwise has no surface to say so: the run would
        # silently propose against the wrong genre. ONLY the
        # framing keys overlay — every other key keeps the
        # §1.3 precedence, and a profile that declares no
        # framing leaves the resolution byte-identical.
        from .profile_config import (
            framing_declarations,
            generated_profile_setup_config,
            setup_config_errors,
        )
        try:
            _prof_cfg = generated_profile_setup_config(
                state_root(), profile_family)
        except ValueError as e:
            print(f"setup_config validation failed: {e}",
                  file=sys.stderr)
            sys.exit(1)
        _framing = framing_declarations(_prof_cfg)
        if _framing:
            resolved_cfg = {**resolved_cfg, **_framing}
            _errs = setup_config_errors(resolved_cfg,
                                         merged=True)
            if _errs:
                for e in _errs:
                    print(f"setup_config validation failed: "
                          f"{e}", file=sys.stderr)
                sys.exit(1)
        prov = pack_provenance(pack_dir)
    elif profile_family:
        # No binding pack: the core baseline still applies by
        # default (PACK_DESIGN §1.3 — the hands-on Track 1
        # promise), with the family's profile setup_config
        # layered over it: profile keys govern where declared,
        # baseline declarations govern the rest, and the merged
        # config is frozen into the batch exactly as the pack
        # path freezes its resolution. (Before this, a packless
        # family froze nothing and the run read the profile file
        # alone, so baseline declarations — the identity seat's
        # live default among them — silently never reached it.)
        # Both layers are validated fail-closed first: every
        # profile key must have a registered consumer, the
        # batch is NOT created when validation fails, and the
        # offending key is named.
        from .profile_config import (
            profile_setup_config_path,
            setup_config_errors,
        )
        # The family's layer, in the run step's own candidate
        # order: the generated profile's file in the state
        # root first, then the pack-layout profile.
        legacy_sc = state_root() / "profiles" / profile_family \
            / "setup_config.json"
        sc_path = legacy_sc if legacy_sc.exists() else \
            profile_setup_config_path(ROOT, profile_family)
        scfg = None
        if sc_path is not None:
            try:
                scfg = json.loads(sc_path.read_text())
            except Exception as e:
                print(f"setup_config {sc_path} is not valid "
                      f"JSON: {e}", file=sys.stderr)
                sys.exit(1)
            errs = setup_config_errors(scfg)
            if errs:
                for e in errs:
                    print(f"setup_config validation failed: {e}",
                          file=sys.stderr)
                sys.exit(1)
        try:
            resolved_cfg = resolve_setup_config(state_root(),
                                                None)
        except ValueError as e:
            print(f"setup_config resolution failed: {e}",
                  file=sys.stderr)
            sys.exit(1)
        if scfg is not None:
            resolved_cfg = {**resolved_cfg, **scfg}
            errs = setup_config_errors(resolved_cfg, merged=True)
            if errs:
                for e in errs:
                    print(f"setup_config validation failed: {e}",
                          file=sys.stderr)
                sys.exit(1)

    # The config's model fields resolve from the seat
    # bindings in force at create — the same resolution whose
    # provenance is stamped below — never straight from the
    # reference-profile constants. BatchConfig's defaults ARE
    # those constants, so without this the wire ran the preset
    # models while the manifest recorded the bound ones. The
    # frozen config then states exactly the models that run.
    _bindings = connections.resolve_bindings()
    cfg_kwargs: dict = {}
    for _field, _seat in (("llm_model", "proposer"),
                          ("gate_model", "judge"),
                          ("embed_model", "embedder")):
        _model = _bindings.seat(_seat).model
        if _model is not None:
            cfg_kwargs[_field] = _model
    if getattr(args, "no_cost_cap", False):
        cfg_kwargs["max_cost_usd"] = None
    elif getattr(args, "max_cost_usd", None) is not None:
        cfg_kwargs["max_cost_usd"] = float(args.max_cost_usd)
    if getattr(args, "frontier_escalation", False):
        cfg_kwargs["frontier_escalation_enabled"] = True
        if getattr(args, "escalation_band_lo", None) is not None:
            cfg_kwargs["frontier_escalation_band_lo"] = float(
                args.escalation_band_lo)
        if getattr(args, "escalation_band_hi", None) is not None:
            cfg_kwargs["frontier_escalation_band_hi"] = float(
                args.escalation_band_hi)
    config = BatchConfig(
        code_version=git_code_version(ROOT),
        **cfg_kwargs)
    # Stop-line rule: a USD cap set on a cost-less
    # binding could never trip — refuse the creation by name
    # (BindingCostError) instead of recording a guarded-looking
    # batch that is not guarded. Seats a batch run uses:
    # proposer, judge, embedder (+ escalation when frontier
    # escalation is enabled).
    _cap_seats = ["proposer", "judge", "embedder"]
    if config.frontier_escalation_enabled:
        _cap_seats.append("escalation")
    connections.require_metered(
        _bindings, config.max_cost_usd, _cap_seats)
    b = Batch(batch_id=batch_id, run_id=new_run_id(),
              source=args.source, collection=args.collection,
              doc_ids=[],  # filled at INGEST
              config=config,
              profile_hash=profile_hash,
              profile_family=profile_family)
    if profile_hash:
        print(f"profile: {profile_family} {profile_hash}")
    if prov is not None:
        # The resolved config descends from this pack's
        # pinned payload — stamp the provenance triple so the
        # INGEST manifest records it beside the profile pin.
        b.pack_provenance = prov
        print(f"pack: {prov['pack_id']} "
              f"{prov['pack_version']} (payload "
              f"sha256:{prov['pack_payload_sha256'][:16]}…)")
    elif profile_family:
        # Legacy path (docs/PACK_DESIGN.md §1.5):
        # stamp the provenance triple only when a pack
        # pins exactly the setup_config bytes this batch
        # executes with. No binding pack -> the batch carries
        # no pack identity (never a guessed one).
        from .profile_config import (
            pack_for_setup_config,
            profile_setup_config_path,
        )
        sc_path = profile_setup_config_path(ROOT, profile_family)
        if sc_path is not None:
            prov2 = pack_for_setup_config(
                ROOT, profile_family, sc_path.read_bytes())
            if prov2:
                b.pack_provenance = prov2
                print(f"pack: {prov2['pack_id']} "
                      f"{prov2['pack_version']} (payload "
                      f"sha256:{prov2['pack_payload_sha256'][:16]}…)")
    # Stamp the connection binding this batch was
    # created under (provenance: name + per-seat host/model;
    # keys are never recorded). A custom binding is named
    # as such in the record.
    b.binding_provenance = _bindings.provenance()
    print(f"binding: {b.binding_provenance['name']}")
    sroot = state_root()
    save_batch(b, sroot)
    # stash doc paths for the run step
    d = batch_dir(sroot, batch_id)
    (d / "doc_paths.json").write_text(json.dumps(paths))
    if resolved_cfg is not None:
        # The executed configuration, frozen at creation: the
        # run step reads this file, not the pack files.
        (d / "setup_config.resolved.json").write_text(
            json.dumps(resolved_cfg, indent=1, sort_keys=True))
        _over = prov['pack_id'] if prov else \
            f"profile '{profile_family}'"
        print(f"setup_config: resolved (core baseline + "
              f"{_over}, {len(resolved_cfg)} keys) -> "
              f"setup_config.resolved.json")
        # The effective proposer framing, printed before
        # any spend: the genre the run will propose against
        # is a first-run fact, never a discovery made from
        # an empty graph afterwards.
        from .profile_config import proposer_framing
        _effective_framing = proposer_framing(resolved_cfg)
        if _effective_framing is not None:
            print(f"proposer_framing: {_effective_framing}")
    print(f"created batch {batch_id} with {len(paths)} docs")
    print(f"run: python -m pipeline batch run {batch_id}")


def _decide_fn(context: str, subject: str, relation: str, obj: str,
               model: str = JUDGE_MODEL):
    """Jev noul via the judge seat's systemone route. The
    binding supplies the endpoint and key; a missing key
    refuses with BindingKeyError naming seat + variable."""
    import urllib.request
    binding = connections.seat("judge")
    key = binding.require_key()
    state = (f"Document excerpt:\n{context}\n\n"
             f"Candidate triple: ({subject} | {relation} | {obj})")
    payload = {
        "model": model, "state": state,
        "questions": {"q1": {
            "type": "noul",
            "instructions": (
                f"Does the document support that the relation '{relation}' "
                f"holds between '{subject}' and '{obj}'? Answer true only if "
                "the document states or clearly implies it."),
            "criteria": {
                "true": "The document states or clearly implies the relation holds.",
                "false": "The document does not support the relation."}}}}
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        binding.systemone_url, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}",
                 "X-Title": "ontosmith"},
        method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
    ans = result.get("answers", {}).get("q1", {})
    # A gate response missing the score field is a gate
    # FAILURE, never a judged negative. Defaulting to 0.0 silently
    # recorded unjudged candidates as p_true=0.0 (dropped at ROUTE
    # with no trace); raising routes the item through the queue's
    # fail → retry → DLQ path, where the failure stays visible.
    if not isinstance(ans, dict) or "noul" not in ans:
        raise RuntimeError(
            "gate response missing 'noul' score field "
            f"(answer keys: {sorted(ans) if isinstance(ans, dict) else type(ans).__name__}); "
            "refusing to record an unjudged candidate as p_true=0.0")
    return float(ans["noul"]), connections.normalize_usage(
        result.get("usage"), binding)


def _er_decide_fn(name_a: str, name_b: str, label: str, context: str,
                 model: str = JUDGE_MODEL):
    """Jev ER gate: are these two mentions the same
    real-world entity? Endpoint + key from the judge
    seat's binding (connection layer)."""
    import urllib.request
    binding = connections.seat("judge")
    key = binding.require_key()
    state = (f"Context: {context}\n\n"
             f"Mention A: '{name_a}' (label: {label or 'unknown'})\n"
             f"Mention B: '{name_b}'\n\n"
             f"Do A and B refer to the same real-world entity?")
    payload = {
        "model": model, "state": state,
        "questions": {"q1": {
            "type": "noul",
            "instructions": (
                "Decide whether the two mentions refer to the same "
                "real-world entity (person, organization, or place). "
                "Consider spelling variants, abbreviations, and OCR noise "
                "(e.g. 'OSWALD' vs 'OSW.\\LD'). Answer true only if they "
                "are clearly the same entity."),
            "criteria": {
                "true": "Both mentions refer to the same real-world entity.",
                "false": ("They are different entities, or it is ambiguous.")}}}}
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        binding.systemone_url, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}",
                 "X-Title": "ontosmith"},
        method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
    ans = result.get("answers", {}).get("q1", {})
    # Same rule as _decide_fn — a missing score field is
    # a gate failure (raised, recorded by the caller), never a silent
    # p_same=0.0 that reads as "judged different".
    if not isinstance(ans, dict) or "noul" not in ans:
        raise RuntimeError(
            "ER gate response missing 'noul' score field "
            f"(answer keys: {sorted(ans) if isinstance(ans, dict) else type(ans).__name__}); "
            "refusing to record an unscored pair as p_same=0.0")
    return float(ans["noul"]), connections.normalize_usage(
        result.get("usage"), binding)


def _resolution_decide_fn(context: str, mention: str, antecedent: str,
                          entity: str, model: str = JUDGE_MODEL):
    """Jev noul via the judge seat (issue #54): does the
    mention in the excerpt refer to the claimed entity, on
    the evidence of the proposed antecedent?

    The proposer proposed the resolution (mention span +
    verbatim antecedent); this gate decides it, exactly as
    _decide_fn decides facts. A response missing the score
    field is a gate FAILURE (raised), never a silent 0.0.
    """
    import urllib.request
    binding = connections.seat("judge")
    key = binding.require_key()
    state = (f"Document excerpt:\n{context}\n\n"
             f"Mention in the text: '{mention}'\n"
             f"Proposed antecedent (the text said to name who or "
             f"what the mention refers to): '{antecedent}'\n"
             f"Claimed referent: '{entity}'")
    payload = {
        "model": model, "state": state,
        "questions": {"q1": {
            "type": "noul",
            "instructions": (
                f"Does the mention '{mention}' in the document refer "
                f"to '{entity}'? The proposed antecedent "
                f"'{antecedent}' is the evidence offered for that "
                "reference. Answer true only if the document makes "
                "clear that the mention refers to that entity — "
                "if the antecedent names a different entity, or the "
                "reference is ambiguous, answer false."),
            "criteria": {
                "true": "The mention clearly refers to the claimed entity.",
                "false": ("The mention refers to something else, or "
                          "the reference is ambiguous.")}}}}
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        binding.systemone_url, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}",
                 "X-Title": "ontosmith"},
        method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
    ans = result.get("answers", {}).get("q1", {})
    # Same rule as _decide_fn — a missing score field is a
    # gate failure, never a silent judged negative.
    if not isinstance(ans, dict) or "noul" not in ans:
        raise RuntimeError(
            "resolution gate response missing 'noul' score field "
            f"(answer keys: {sorted(ans) if isinstance(ans, dict) else type(ans).__name__}); "
            "refusing to record an unjudged resolution as 0.0")
    return float(ans["noul"]), connections.normalize_usage(
        result.get("usage"), binding)


def _load_batch_or_exit(root: Path, batch_id: str) -> Batch:
    """Load a batch or exit with a named error: a
    typo'd id is a user error, not a traceback."""
    try:
        return load_batch(root, batch_id)
    except FileNotFoundError:
        print(f"error: no such batch {batch_id!r} "
              f"(no {batch_dir(root, batch_id) / 'batch.json'})",
              file=sys.stderr)
        sys.exit(2)


def cmd_run(args) -> None:
    """Run (or resume) a batch through the stage contract."""
    sroot = state_root()
    b = _load_batch_or_exit(sroot, args.batch_id)
    # The resume run_id is minted (or kept) by run_batch —
    # the single minter; --reuse-run rides through
    # as keep_run_id. This command never mints its own.
    d = batch_dir(sroot, b.batch_id)
    doc_texts = None
    # INGEST needs the documents: a fresh batch, or a FAILED
    # batch that died before INGEST completed (no stage in
    # state.json) and will resume from CREATED.
    needs_docs = b.status == BatchStatus.CREATED
    if b.status == BatchStatus.FAILED:
        state_p = d / "state.json"
        done = (json.loads(state_p.read_text())
                .get("stages_completed", [])
                if state_p.exists() else [])
        needs_docs = not done
    if needs_docs:
        paths = json.loads((d / "doc_paths.json").read_text())
        doc_texts = {}
        for p in paths:
            try:
                doc_texts[p] = Path(p).read_text(errors="replace")
            except Exception as e:
                print(f"  skip {p}: {e}", file=sys.stderr)
    # Registry backend: the file-backed SQLite registry is the
    # source of truth; Neo4j is the projection. JEV_REGISTRY=neo4j
    # selects the legacy Neo4j-resident registry (fallback only).
    # JEV_REGISTRY_DB overrides the registry file location.
    backend = os.environ.get("JEV_REGISTRY", "sqlite").lower()
    if backend == "neo4j":
        registry = Neo4jEntityRegistry(
            cache_path=sroot / "data" / "emb_cache.pkl")
        print("registry backend: neo4j (legacy fallback)")
    else:
        from .file_registry import SqliteEntityRegistry
        db_path = Path(os.environ.get(
            "JEV_REGISTRY_DB", sroot / "data" / "registry.db"))
        registry = SqliteEntityRegistry(db_path)
        print(f"registry backend: sqlite ({db_path})")
    graph = Neo4jGraphStore()
    run_batch(b, sroot, doc_texts, decide_fn=_decide_fn,
              er_decide_fn=_er_decide_fn,
              resolution_decide_fn=_resolution_decide_fn,
              registry=registry, graph=graph, live_decide=True,
              keep_run_id=args.reuse_run)


def _status_metrics(b: Batch, sroot: Path) -> dict:
    """The metrics `batch status` presents: as stored, except
    the finish ER account, which is recomputed from the
    decisions ledger at read time.

    metrics.finish.er in batch.json is the account as it
    stood when the run finished (written by the finish
    summary). ER settlement is a post-finish act by design:
    er-decide appends the settlement to the registry's
    er_decisions ledger and nothing rewrites the stored
    snapshot, so presenting the snapshot as current froze
    the finish-time counts forever — status reported 11
    unactioned after er-review showed all 11 settled
    (0.1.4 hands-on gate). Status is a read of current
    state, so the ER account is derived from the same
    ledger er-review reads, with the same counting
    (_er_terminal_account).

    The stored batch.json is NOT rewritten: its finish
    block stays the at-finish record for every other
    reader; only this presentation is refreshed. Falls
    back to the stored metrics when the ledger cannot
    account — the legacy backend keeps no decision store,
    or the registry file is absent (in which case opening
    it would manufacture an empty ledger whose zero counts
    would clobber a real snapshot)."""
    backend = os.environ.get("JEV_REGISTRY", "sqlite").lower()
    if backend != "sqlite":
        return b.metrics
    db_path = Path(os.environ.get(
        "JEV_REGISTRY_DB", sroot / "data" / "registry.db"))
    if not db_path.exists():
        return b.metrics
    account = None
    registry = None
    try:
        registry, _label = _open_registry(sroot)
        account = _er_terminal_account(registry, b.batch_id)
    except Exception:
        account = None
    finally:
        close = getattr(registry, "close", None)
        if close is not None:
            close()
    if account is None:
        return b.metrics
    metrics = dict(b.metrics)
    finish = dict(metrics.get("finish") or {})
    finish["er"] = account
    metrics["finish"] = finish
    return metrics


def cmd_status(args) -> None:
    """Print a batch's status, stage state, and metrics.

    The metrics are the stored record, with one refresh:
    the finish ER account is recomputed from the decisions
    ledger (see _status_metrics), because settlements
    postdate the finish that snapshotted it."""
    sroot = state_root()
    b = _load_batch_or_exit(sroot, args.batch_id)
    d = batch_dir(sroot, b.batch_id)
    state_p = d / "state.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else {}
    print(json.dumps({
        "batch_id": b.batch_id, "run_id": b.run_id,
        "status": b.status.value,
        "stages_completed": state.get("stages_completed", []),
        "metrics": _status_metrics(b, sroot),
        "last_error": state.get("last_error"),
    }, indent=1))


def cmd_rollback(args) -> None:
    """Delete one run's graph edges and record the rollback."""
    # Manual approval required: --yes flag
    if not args.yes:
        print("Rollback deletes data. Pass --yes to confirm.", file=sys.stderr)
        print(f"Would delete: batch_id={args.batch_id} run_id={args.run}",
              file=sys.stderr)
        sys.exit(2)
    graph = Neo4jGraphStore()
    n = graph.delete_run(args.batch_id, args.run)
    # Log the rollback
    d = batch_dir(state_root(), args.batch_id)
    log_p = d / "rollbacks.jsonl"
    import datetime
    with open(log_p, "a") as f:
        f.write(json.dumps({
            "batch_id": args.batch_id, "run_id": args.run,
            "deleted_edges": n,
            "at": datetime.datetime.now(
                datetime.UTC).isoformat(),
            "reason": args.reason or "",
        }) + "\n")
    print(f"rolled back run {args.run}: {n} edges deleted "
          f"(entities kept — registry owns them)")


def _open_registry(sroot: Path):
    """Open the batch registry exactly as `batch run` does
    (sqlite file registry by default; JEV_REGISTRY=neo4j
    selects the legacy Neo4j-resident backend). Returns
    (registry, backend_label)."""
    backend = os.environ.get("JEV_REGISTRY", "sqlite").lower()
    if backend == "neo4j":
        return (Neo4jEntityRegistry(
            cache_path=sroot / "data" / "emb_cache.pkl"), "neo4j")
    from .file_registry import SqliteEntityRegistry
    db_path = Path(os.environ.get(
        "JEV_REGISTRY_DB", sroot / "data" / "registry.db"))
    return SqliteEntityRegistry(db_path), "sqlite"


def _settlement_registry(sroot: Path):
    """The registry for the ER settlement commands. Settlement
    records live in the file registry's er_decisions; the
    legacy Neo4j registry keeps no decision store (its
    record_er_decision is the interface no-op), so held pairs
    cannot exist there — refuse by name instead of reporting
    an empty list that would read as 'nothing held'."""
    registry, backend = _open_registry(sroot)
    if backend != "sqlite":
        print("error: ER settlement needs the file registry "
              "(its er_decisions table is the decision record); "
              "the legacy Neo4j registry keeps no decision "
              "store. Unset JEV_REGISTRY (or point "
              "JEV_REGISTRY_DB at the file registry) and retry.",
              file=sys.stderr)
        sys.exit(2)
    return registry


def _resolve_pair_entities(registry, batch: Batch, pair: dict):
    """Resolve a held pair's two sides to registry entities.

    The survivor side is the record's surviving_id. The
    mention side is the entity the held mention became when
    WRITE created it: the entity named merged_name — this
    batch's own first, then one in this batch's corpus, then
    a unique name match. Returns (mention_entity | None,
    survivor_entity | None); None means the side cannot be
    resolved uniquely and a merge cannot be applied."""
    survivor = registry.get(pair["surviving_id"])
    mention = None
    cands = [e for e in registry.all_entities()
             if e.name == pair["merged_name"]
             and e.entity_id != pair["surviving_id"]]
    for e in cands:
        if e.batch_id == batch.batch_id:
            mention = e
            break
    if mention is None:
        for e in cands:
            if e.corpus == batch.source:
                mention = e
                break
    if mention is None and len(cands) == 1:
        mention = cands[0]
    return mention, survivor


def cmd_er_review(args) -> None:
    """List a batch's held ER pairs with the evidence a
    person needs to settle them: both sides as
    registry entities, the gate's p_same and model, the
    recorded reason where one applies, each side's triple
    count in the batch's graph, and sample contexts from the
    routed candidates. Settled pairs are hidden unless
    --all."""
    sroot = state_root()
    b = _load_batch_or_exit(sroot, args.batch_id)
    registry = _settlement_registry(sroot)
    pairs = registry.er_review_pairs(b.batch_id)
    unsettled = [p for p in pairs if p["settled"] is None]
    shown = pairs if args.all else unsettled
    from .registry_store import RegistryStore
    rs = RegistryStore(sroot)
    manifest = rs.manifest_triples(b.batch_id) or []
    triple_counts: dict[str, int] = {}
    for t in manifest:
        for k in ("subject_id", "object_id"):
            triple_counts[t[k]] = triple_counts.get(t[k], 0) + 1
    try:
        routed = rs.routed_rows(b.batch_id)
    except FileNotFoundError:
        routed = []

    def contexts_for(pair: dict) -> list[str]:
        names = {pair["merged_name"], pair["canonical_name"]}
        out: list[str] = []
        for r in routed:
            if r.get("subject") in names or r.get("object") in names:
                ctx = (r.get("context") or "").strip()
                if ctx and ctx not in out:
                    out.append(ctx)
            if len(out) >= 2:
                break
        return out

    if not shown:
        print(f"no {'held' if args.all else 'unsettled held'} "
              f"ER pairs for batch {b.batch_id}"
              + ("" if args.all else
                 f" ({len(pairs) - len(unsettled)} settled; "
                 f"--all to show)"))
        return
    for p in shown:
        mention, survivor = _resolve_pair_entities(registry, b, p)
        if p["settled"] is None:
            state = "HELD"
        else:
            state = (f"SETTLED "
                     f"{p['settled']['outcome']} by "
                     f"{p['settled']['model']} "
                     f"({p['settled']['decided_at']})")
        print(f"pair #{p['id']} — {state}")
        print(f"  '{p['merged_name']}'"
              + (f" [{mention.entity_id}, {mention.label or 'untyped'}]"
                 if mention else " [entity not resolvable]"))
        print(f"  vs '{p['canonical_name']}'"
              + (f" [{survivor.entity_id}, "
                 f"{survivor.label or 'untyped'}]"
                 if survivor else " [entity not resolvable]"))
        score = ("unscored" if p["p_same"] is None
                 else f"{p['p_same']:.2f}")
        print(f"  gate p_same={score}"
              + (f" ({p['model']})" if p["model"] else "")
              + f"; held {p['decided_at']}"
              + (f"; reason: {p['reason']}" if p["reason"] else ""))
        if mention is not None or survivor is not None:
            print(f"  triples in graph: mention side "
                  f"{triple_counts.get(mention.entity_id, 0) if mention else 0}"
                  f" · survivor side "
                  f"{triple_counts.get(survivor.entity_id, 0) if survivor else 0}")
        for ctx in contexts_for(p):
            snippet = ctx if len(ctx) <= 240 else ctx[:237] + "..."
            print(f"  context: {snippet}")
        if p["settled"] is None:
            print(f"  settle: ontosmith batch er-decide "
                  f"{b.batch_id} --pair {p['id']} "
                  f"--merge | --keep-separate")
        print()


def cmd_er_decide(args) -> None:
    """Record a human settlement of one held ER pair
    and, for a merge, apply it to the registry
    (the source of truth) at once.

    The decision row is append-only, like every er_decisions
    row: the hold stays on record and the settlement lands
    beside it (outcome 'merged' via the fold, or
    'kept_separate'), stamped with who decided (--by), when,
    and the gate's p_same. A merge folds the pair in the
    registry (merge_entities); the GRAPH follows by
    re-projection — `batch er-apply`. A keep-separate pair
    is never re-held by later runs."""
    sroot = state_root()
    b = _load_batch_or_exit(sroot, args.batch_id)
    registry = _settlement_registry(sroot)
    pairs = registry.er_review_pairs(b.batch_id)
    pair = next((p for p in pairs if p["id"] == args.pair), None)
    if pair is None:
        print(f"error: no held pair #{args.pair} for batch "
              f"{b.batch_id!r} (see `ontosmith batch er-review "
              f"{b.batch_id}`)", file=sys.stderr)
        sys.exit(2)
    if pair["settled"] is not None:
        s = pair["settled"]
        print(f"error: pair #{pair['id']} for batch "
              f"{b.batch_id!r} is already settled "
              f"({s['outcome']} by {s['model']}, "
              f"{s['decided_at']}); a settlement is a record, "
              f"not an edit — the pair stays as decided",
              file=sys.stderr)
        sys.exit(2)
    by = args.by or "human"
    reason = f"human settlement of review pair #{pair['id']}"
    if args.reason:
        reason += f": {args.reason}"
    if args.keep_separate:
        registry.record_er_decision(
            b.batch_id, b.run_id, pair["merged_name"],
            pair["surviving_id"], pair["canonical_name"],
            model=by, p_same=pair["p_same"],
            outcome="kept_separate", reason=reason)
        print(f"pair #{pair['id']} settled keep-separate by "
              f"{by}: '{pair['merged_name']}' and "
              f"'{pair['canonical_name']}' stay separate "
              f"entities; later runs will not re-hold this pair")
        return
    mention, survivor = _resolve_pair_entities(registry, b, pair)
    if mention is None or survivor is None:
        missing = ("the mention-side entity "
                   f"'{pair['merged_name']}'" if mention is None
                   else "the survivor-side entity "
                        f"'{pair['canonical_name']}'")
        print(f"error: cannot apply a merge for pair "
              f"#{pair['id']}: {missing} cannot be resolved "
              f"uniquely in the registry; the decision was NOT "
              f"recorded", file=sys.stderr)
        sys.exit(2)
    report = registry.merge_entities(
        pair["surviving_id"], mention.entity_id,
        batch_id=b.batch_id, run_id=b.run_id, model=by,
        p_same=pair["p_same"], reason=reason)
    print(f"pair #{pair['id']} settled merge by {by}: "
          f"'{mention.name}' folded into "
          f"'{report['canonical_name']}' "
          f"[{report['surviving_id']}]"
          f" (aliases now: {', '.join(report['aliases']) or '—'})")
    print(f"the registry (source of truth) is settled; the "
          f"graph still shows both nodes — re-project with: "
          f"ontosmith batch er-apply {b.batch_id}")


def cmd_er_apply(args) -> None:
    """Apply a batch's recorded ER merges to its graph:
    amend the WRITE manifest
    (written_triples.jsonl) through the registry's
    id_mapping, then rebuild the batch's projection with the
    existing machinery (graph_store.project_from_registry),
    and remove a merged-away entity node once no REL edge
    touches it. Idempotent: with no recorded merge touching
    the batch's graph there is nothing to apply."""
    sroot = state_root()
    b = _load_batch_or_exit(sroot, args.batch_id)
    registry = _settlement_registry(sroot)
    from .registry_store import RegistryStore
    rs = RegistryStore(sroot)
    manifest = rs.manifest_triples(b.batch_id)
    if manifest is None:
        print(f"error: batch {b.batch_id!r} has no "
              f"written_triples.jsonl (it predates the WRITE "
              f"manifest); its projection cannot be amended",
              file=sys.stderr)
        sys.exit(2)
    mapping = registry.mapping()
    present = ({t["subject_id"] for t in manifest}
               | {t["object_id"] for t in manifest})
    remap = {old: new for old, new in mapping.items()
             if old in present}
    if not remap:
        print(f"nothing to apply for batch {b.batch_id}: no "
              f"recorded ER merge touches its graph")
        return

    def _rid(eid: str) -> str:
        seen = set()
        while eid in mapping and eid not in seen:
            seen.add(eid)
            eid = mapping[eid]
        return eid

    from .graph_store import GraphStore
    from .neo4j_store import probe
    gs = GraphStore(registry_store=rs, entity_registry=registry)
    # Preflight before amending anything: with the store down,
    # nothing is touched and the rerun applies in full — the
    # manifest rewrite alone would make a later er-apply read
    # as 'nothing to apply' over a stale projection.
    problem = probe(gs.c)
    if problem is not None:
        print(f"error: {problem} — nothing was amended; the "
              f"registry settlement stands and er-apply can "
              f"be rerun when the store is reachable",
              file=sys.stderr)
        sys.exit(2)
    rows, changed = [], 0
    for t in manifest:
        nt = dict(t)
        nt["subject_id"] = _rid(t["subject_id"])
        nt["object_id"] = _rid(t["object_id"])
        changed += nt != t
        rows.append(nt)
    if changed:
        mp = batch_dir(sroot, b.batch_id) / "written_triples.jsonl"
        with open(mp, "w") as f:
            for t in rows:
                f.write(json.dumps(t) + "\n")
    report = gs.project_from_registry(b.batch_id, id_remap=remap)
    removed = []
    for old in remap:
        n = gs.c.one(
            "MATCH (e:Entity {entity_id: $id})-[r:REL]-() "
            "RETURN count(r)", {"id": old}) or 0
        if n == 0:
            gs.c.run([{"statement":
                       "MATCH (e:Entity {entity_id: $id}) "
                       "DETACH DELETE e",
                       "parameters": {"id": old}}])
            removed.append(old)
    print(f"applied ER merges to batch {b.batch_id}: "
          f"{changed} manifest triple(s) re-pointed, "
          f"projection rebuilt ({report['entities']} entities, "
          f"{report['edges']} edges, p_true carried "
          f"{report['p_true_carried']}, unset "
          f"{report['p_true_unset']})"
          + (f"; removed merged-away node(s): "
             f"{', '.join(removed)}" if removed else "")
          + (f"; the store held batch "
             f"{report['replaced']['previous_batch']}'s graph "
             f"({report['replaced']['previous_triples']} "
             f"triples) — this projection replaced it (the "
             f"store holds one graph; the registry keeps the "
             f"replaced graph's history)"
             if report.get("replaced") else ""))


def cmd_list(args) -> None:
    """List all batches under the tree's batches/ directory."""
    root = state_root() / "batches"
    if not root.exists():
        print("(no batches)")
        return
    for d in sorted(root.iterdir()):
        if (d / "batch.json").exists():
            b = load_batch(state_root(), d.name)
            print(f"{b.batch_id:<30} {b.status.value:<12} "
                  f"run={b.run_id[:8]} docs={len(b.doc_ids)}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="ontosmith batch")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="Create a batch from documents")
    c.add_argument("--source", required=True)
    c.add_argument("--collection", required=True)
    c.add_argument("--docs", nargs="+", required=True,
                   help="glob patterns for input documents")
    c.add_argument("--batch-id", default=None)
    c.add_argument("--profile", default=None,
                   help="path to a stage-0 profile.json (setup service); "
                        "its profile_hash is recorded on the batch and "
                        "into the manifest at INGEST")

    c.add_argument("--pack", default=None,
                   help="domain pack binding this batch (pack_id, "
                        "family, or pack directory); the executed "
                        "setup_config is resolved as core baseline "
                        "+ this pack and frozen into the batch "
                        "directory (default: the pack bound to the "
                        "--profile family, if any)")
    c.add_argument("--max-cost-usd", type=float, default=None,
                   help="stop-line: hard spend cap for the batch "
                        "(default: the BatchConfig default)")
    c.add_argument("--no-cost-cap", action="store_true",
                   help="run with no USD spend cap (the explicit "
                        "way to run on a cost-less binding; the "
                        "ledger then records tokens and marks "
                        "cost not-provided)")
    c.add_argument("--frontier-escalation", action="store_true",
                   help="enable frontier escalation for "
                        "non-judging seats (default OFF; the judging "
                        "seat never escalates)")
    c.add_argument("--escalation-band-lo", type=float, default=None,
                   help="shaky-band lower bound (with "
                        "--frontier-escalation)")
    c.add_argument("--escalation-band-hi", type=float, default=None,
                   help="shaky-band upper bound (with "
                        "--frontier-escalation)")
    c.set_defaults(fn=cmd_create)

    r = sub.add_parser("run", help="Run (or resume) a batch")
    r.add_argument("batch_id")
    r.add_argument("--reuse-run", action="store_true",
                   help="keep run_id on retry (default: new run_id)")
    r.set_defaults(fn=cmd_run)

    s = sub.add_parser("status", help="Show batch status")
    s.add_argument("batch_id")
    s.set_defaults(fn=cmd_status)

    rb = sub.add_parser("rollback", help="Delete a run's outputs (manual)")
    rb.add_argument("batch_id")
    rb.add_argument("--run", required=True)
    rb.add_argument("--yes", action="store_true")
    rb.add_argument("--reason", default="")
    rb.set_defaults(fn=cmd_rollback)

    er = sub.add_parser(
        "er-review",
        help="List a batch's held ER review-band pairs, "
             "with the evidence to settle them")
    er.add_argument("batch_id")
    er.add_argument("--all", action="store_true",
                    help="include pairs already settled")
    er.set_defaults(fn=cmd_er_review)

    ed = sub.add_parser(
        "er-decide",
        help="Record a human settlement of one held ER pair "
             "(a merge folds the pair in the registry at once)")
    ed.add_argument("batch_id")
    ed.add_argument("--pair", type=int, required=True,
                    help="held pair id (from er-review)")
    grp = ed.add_mutually_exclusive_group(required=True)
    grp.add_argument("--merge", action="store_true",
                     help="fold the pair into one entity")
    grp.add_argument("--keep-separate", action="store_true",
                     help="record that the pair stays two "
                          "entities; later runs never re-hold it")
    ed.add_argument("--by", default="human",
                    help="who is deciding (recorded on the "
                         "decision row; default 'human')")
    ed.add_argument("--reason", default="",
                    help="why (appended to the decision record)")
    ed.set_defaults(fn=cmd_er_decide)

    ea = sub.add_parser(
        "er-apply",
        help="Apply recorded ER merges to the graph: amend "
             "the WRITE manifest and re-project")
    ea.add_argument("batch_id")
    ea.set_defaults(fn=cmd_er_apply)

    ls = sub.add_parser("list", help="List batches")
    ls.set_defaults(fn=cmd_list)

    args = ap.parse_args()
    args.fn(args)
