"""Batch abstraction: the unit of work for the KG pipeline.

A Batch binds input documents, config, outputs, and metrics together.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from .reference_profile import (
    EMBED_MODEL,
    JUDGE_MODEL,
    NER_MODEL,
    PROPOSER_MODEL,
)


class BatchStatus(str, Enum):
    CREATED = "CREATED"
    INGESTED = "INGESTED"
    NER_DONE = "NER_DONE"
    CANDIDATES_DONE = "CANDIDATES_DONE"
    GATED = "GATED"
    ROUTED = "ROUTED"
    WRITTEN = "WRITTEN"
    INDEXED = "INDEXED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


# Ordered stage progression (FAILED can happen from any stage)
STAGE_ORDER = [
    BatchStatus.CREATED,
    BatchStatus.INGESTED,
    BatchStatus.NER_DONE,
    BatchStatus.CANDIDATES_DONE,
    BatchStatus.GATED,
    BatchStatus.ROUTED,
    BatchStatus.WRITTEN,
    BatchStatus.INDEXED,
    BatchStatus.VERIFIED,
]


@dataclass(frozen=True)
class BatchConfig:
    # Model fields: the constants are only the no-binding
    # defaults. `batch create` resolves gate_model /
    # embed_model / llm_model from the seat bindings in
    # force and freezes them here, so a batch's
    # config states the models that actually run — the
    # same models its manifest's binding provenance names.
    # (ner_model named the local GLiNER model of the
    # retired pipeline source; the field is inert since
    # 2026-10-29 and kept so stored configs still load.)
    ner_model: str = NER_MODEL
    gate_model: str = JUDGE_MODEL
    triple_threshold: float = 0.25
    review_band_lo: float = 0.25
    review_band_hi: float = 0.50
    er_threshold: float = 0.40
    embed_model: str = EMBED_MODEL
    # The batch spend stop-line. The cost ledger
    # enforces this as a hard cap (pre-stage check + per-entry
    # accounting in stages). The 10.0 here is the core pack's
    # declared batch_cost_cap_usd mirror, NOT what a created
    # batch gets: `batch create` always sets this field from
    # ONTOSMITH_MAX_COST_USD (batch_cli.cap_from_env) — unset
    # means None, no cap — and quickstart supplies a $2
    # default on metered bindings. A cap set on a cost-less
    # binding refuses by name at create/run, because it could
    # never trip (connections.require_metered).
    max_cost_usd: float | None = 10.0
    # GATES claim lease (decided 2026-10-16): seconds a claimed
    # queue item stays claimed before a resumed run may reclaim it.
    # Default 300 = the pre-decision hard-coded constant in
    # pipeline/queue.py; now a policy value set per batch/run.
    # Heartbeat-based claims are deferred to the multi-instance
    # distribution step (SETUP.md distribution note).
    queue_lease_seconds: int = 300
    audit_sample_n: int = 50
    code_version: str = ""
    # Proposer model for the candidate stage. (There is no
    # candidate-source setting: the LLM proposer is the
    # only source — the GLiNER+spaCy pipeline source and
    # its candidate_source field were removed 2026-10-29.)
    llm_model: str = PROPOSER_MODEL

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> BatchConfig:
        # A stored config from the pipeline era may still
        # carry candidate_source: "llm" (or its absence)
        # loads silently; a retired value refuses by name
        # rather than silently running a different builder
        # than the stored config states.
        legacy = d.get("candidate_source")
        if legacy is not None and str(legacy).lower() != "llm":
            raise ValueError(
                f"candidate_source {legacy!r} was retired "
                "2026-10-29: the LLM proposer is the only "
                "candidate source (the GLiNER+spaCy pipeline "
                "source is removed)")
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Batch:
    batch_id: str
    run_id: str
    source: str
    collection: str
    doc_ids: list[str]
    config: BatchConfig
    status: BatchStatus = BatchStatus.CREATED
    metrics: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    # Provenance: one entry per run that stamped a (new) code version.
    code_history: list = field(default_factory=list)
    # Stage-0 setup profile this batch runs under: the content
    # hash from profiles/<family>/profile.json, recorded into the
    # manifest at INGEST. "" = no profile (legacy batches unchanged).
    profile_hash: str = ""
    # The profile's corpus family; locates profiles/<family>/
    # setup_config.json for the stages that consume it (CANDIDATES
    # proposer categories, ROUTE C1 whitelist, WRITE relation map).
    profile_family: str = ""
    # Industry-pack provenance (docs/PACK_DESIGN
    # .md §1.5): the pack_provenance() triple, stamped at creation
    # when a pack pins exactly the setup_config bytes this batch
    # executes with (pipeline.profile_config.pack_for_setup_config).
    # {} = no pack binds the run — legacy batches are unchanged.
    pack_provenance: dict = field(default_factory=dict)
    # Connection-layer provenance: the Bindings
    # .provenance() map stamped at creation — binding name
    # ("reference" | "custom") + per-seat host/model, never
    # a key name or value. {} = not recorded (batches
    # created before binding provenance was recorded are unchanged).
    binding_provenance: dict = field(default_factory=dict)

    def __post_init__(self):
        now = datetime.now(UTC).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        d["config"] = self.config.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Batch:
        d = dict(d)
        d["status"] = BatchStatus(d["status"])
        d["config"] = BatchConfig.from_dict(d["config"])
        return cls(**d)


def new_run_id() -> str:
    return str(uuid.uuid4())


def git_code_version(root: Path) -> str:
    """Short git hash of the code tree at root ('' when unavailable).

    Recorded on each batch so a result can be traced to the exact code
    that produced it (config.code_version + Batch.code_history)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=root,
            capture_output=True, text=True, timeout=5)
        return out.stdout.strip()
    except Exception:
        return ""


def doc_id_for_text(text: str) -> str:
    """Content hash = document identity. Same content = same doc_id."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def batch_dir(root: Path, batch_id: str) -> Path:
    return root / "batches" / batch_id


def write_manifest(b: Batch, sources: list[dict], root: Path) -> dict:
    """Write immutable manifest.json (bill of materials)."""
    d = batch_dir(root, b.batch_id)
    d.mkdir(parents=True, exist_ok=True)
    # tamper-evident hash over sorted doc_ids
    h = hashlib.sha256(
        "\n".join(sorted(b.doc_ids)).encode()).hexdigest()[:16]
    manifest = {
        "batch_id": b.batch_id,
        "run_id": b.run_id,
        "collection": b.collection,
        "snapshot_at": datetime.now(UTC).isoformat(),
        "sources": sources,
        "source_count": len(sources),
        "manifest_hash": "sha256:" + h,
    }
    if b.profile_hash:
        # Pin the stage-0 profile this run executed under, so the
        # determinism premise ("same profile hash") is checkable.
        manifest["profile_hash"] = b.profile_hash
    if b.pack_provenance:
        # The pack identity triple beside the
        # profile pin (docs/PACK_DESIGN.md §1.5).
        manifest["pack_provenance"] = b.pack_provenance
    if b.binding_provenance:
        # The connection binding this batch was
        # created under (host + model per seat; no secrets).
        manifest["binding"] = b.binding_provenance
    (d / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def save_batch(b: Batch, root: Path) -> None:
    d = batch_dir(root, b.batch_id)
    d.mkdir(parents=True, exist_ok=True)
    b.updated_at = datetime.now(UTC).isoformat()
    (d / "batch.json").write_text(json.dumps(b.to_dict(), indent=1))


def load_batch(root: Path, batch_id: str) -> Batch:
    p = batch_dir(root, batch_id) / "batch.json"
    return Batch.from_dict(json.loads(p.read_text()))


def save_state(b: Batch, root: Path, stages_completed: list[str],
               last_error: str | None = None) -> None:
    d = batch_dir(root, b.batch_id)
    state = {
        "batch_id": b.batch_id,
        "run_id": b.run_id,
        # The corpus name rides along: drift_check read
        # state.json's "source" for years while save_state never
        # wrote it, so the read silently yielded "". batch.json
        # stays the authoritative copy; this one is written so
        # the state file carries what its readers look for.
        "source": b.source,
        "status": b.status.value,
        "stages_completed": stages_completed,
        "last_error": last_error,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    (d / "state.json").write_text(json.dumps(state, indent=1))


def advance(b: Batch, root: Path, stages_completed: list[str]) -> None:
    """Move to the next stage in STAGE_ORDER and checkpoint."""
    idx = STAGE_ORDER.index(b.status)
    if idx + 1 < len(STAGE_ORDER):
        b.status = STAGE_ORDER[idx + 1]
    save_batch(b, root)
    save_state(b, root, stages_completed)


def mark_failed(b: Batch, root: Path, stages_completed: list[str],
                error: str) -> None:
    b.status = BatchStatus.FAILED
    save_batch(b, root)
    save_state(b, root, stages_completed, last_error=error)
