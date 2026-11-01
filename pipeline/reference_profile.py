"""The reference profile: OntoSmith's tested model bindings.

This module is the single home of the model/endpoint bindings
that ship as the product's **default connection preset**.
The published quality record was
measured with exactly these bindings, so a default install
reproduces the published measurements; a user who edits the
connection layer runs a *custom binding* and re-measures on
their own account (runs record which binding was used).

Every binding declared here is the measured occupant of its
seat: the cast is exactly the models tested in
their roles — no name is presented as a default on any
other basis. The one seat that declares **no** model here
is drafting: an authoring aid behind the human approve
gate, exercised once in the lab and never part of the
measured pipeline, so the preset ships it unbound and the
user binds it explicitly (``pipeline/connections.py``).

Layer discipline:
  - Domain config (packs / profiles) declares only *capability
    tiers* (proposer / judge / escalation) — never these names.
  - The names live here (the shipped preset) and in the user's
    connection config, nowhere else in the pipeline.
  - Secrets are not settings: API keys are read from the
    environment by name only (e.g. OPENROUTER_API_KEY) and
    never appear in any config layer.

The Jev gate binding is a *pinned product component*, not a
casual setting: its thresholds are calibrated per gate+model,
so JUDGE_MODEL changes only with a re-measured pack release.
"""
from __future__ import annotations

# --- Provider route (connection preset base) -------------------
PROVIDER_BASE_URL = "https://openrouter.ai/api/v1"
CHAT_URL = PROVIDER_BASE_URL + "/chat/completions"
EMBEDDINGS_URL = PROVIDER_BASE_URL + "/embeddings"
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
#: Route the batch path's gate calls take (Jev decision model).
SYSTEMONE_URL = PROVIDER_BASE_URL + "/systemone"
#: Provider-routing preference sent with chat/embedding calls
#: (the OpenRouter "provider.sort" request field): a
#: connection-layer routing knob. Shipped payloads may still
#: declare it as legacy metadata, but the
#: live value is this preset, never a domain-layer setting.
PROVIDER_SORT = "throughput"

# --- Tier bindings (the tested set) -----------------------------
#: Proposer tier: candidate extraction / pair proposing / roster.
PROPOSER_MODEL = "anthropic/claude-haiku-5.5"
#: Judge tier: the Jev decision gate (pinned; see module docstring).
JUDGE_MODEL = "typesafe/jev-1.13"
#: Identity-seat calibration (the frozen completion process,
#: pipeline/identity_seat.py): the completion-claim wording
#: version and the fitted zone thresholds — auto-complete
#: requires the rule chain's completion AND p >= TAU_HI;
#: p <= TAU_LO auto-stands; between them is the review band.
#: Fitted on the frozen calibration split; like JUDGE_MODEL,
#: these change only with a re-measured pack release.
IDENTITY_CLAIM_VERSION = "completion-b1"
IDENTITY_TAU_HI = 0.79
IDENTITY_TAU_LO = 0.10
#: Escalation tier: adjudication escalation for shaky-band
#: items and the gate-evaluation judges. The lab's measured
#: escalation record ran on this seat (merge-adjudication
#: confirmations, judge fitting).
ESCALATION_MODEL = "anthropic/claude-sonnet-5.5"
#: Embedding binding (indexing + question embedding).
EMBED_MODEL = "openai/text-embedding-3-large"
#: Output dimensionality of EMBED_MODEL (index schema constant).
EMBED_DIMS = 3072
#: NER binding for the pipeline candidate path.
NER_MODEL = "urchade/gliner_medium-v2.1"

#: Human-readable name recorded in run provenance.
REFERENCE_PROFILE_NAME = "reference"
