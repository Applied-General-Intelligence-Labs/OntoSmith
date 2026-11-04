# Packs — the pack contract

This is the pack-format contract for the shipped product:
what a pack is, what its files mean, and the rules a pack
is loaded, versioned, and measured under. It is the
document the shipped `pack.json` manifests cite as their
`design` source. The user-facing walkthrough that authors
a pack end to end is the hands-on guide's Track 3
([hands-on.md](hands-on.md)); this page is the contract
that walkthrough operates under.

**Terms.**

- *Core* = the domain-invariant implementation:
  stage machinery, prompt-construction mechanisms, the gate,
  the registry, the measurement instruments. The core
  carries meta-structure and schema-behavior rules only; it
  never says what exists in a domain.
- *Pack* = one domain's
  content as data: type sets, definitions, boundary
  contrasts, convention notes, fitted artefacts, evaluation
  hooks — plus its identity, version, pin, and conformance
  record (what "conformance" means here is defined in §2.4).
  Do not confuse *Core* with the **core pack**: the core
  pack (`packs/core`) is baseline *content* — the
  domain-neutral type inventory and gates every domain
  pack composes over — that runs on Core, the
  implementation.
- *Payload* = the `setup_config` document a pack
  binds and the profile channel consumes.
- *Candidate* = pack
  content that exists and is pinned but is **not in force**:
  measured (or awaiting measurement) under §2, adoptable
  only as a new pack version after a passing registered
  gate.

## 1. The contract

### 1.1 What a pack contains

A pack directory `packs/<pack_id>/` holds a manifest
(`pack.json`, format 1) and a conformance record
(`conformance.json`). The manifest declares:

| Section | Content | Rule |
|---|---|---|
| Identity | `pack_id`, `pack_version`, `status`, `profile_family` | §1.2 |
| Payload | `kind: setup_config`, repo-relative `path`, `sha256` pin, `profile_version` | §1.3 — exactly one payload in format 1 |
| Contents index | Human-auditable map of what the payload carries (inventories, prohibitions, switches, declarations) | descriptive; the payload is normative |
| Convention notes | Corpus-convention observations, each with a status (`observed_not_in_force` unless adopted into the payload by a passing measurement) | fitted from fitting-side data only; aggregate form (§2.3) |
| Candidates | Artefacts pinned by path + sha256, each with `status` and a conformance pointer | never in force at the version that records them (§1.4) |
| Conformance record | Pointer to `conformance.json` | §2 |

Content rules, applying to every section: **(a)** type
sets and definitions live ONLY in the payload
(schema_front / vocabularies) — the manifest indexes
them, never restates them normatively, so there is one
definition of each type per pack version (definition
fixing). **(b)** Boundary contrasts and convention notes
are fitted from the fitting side of the corpus
(Train-side gold, corpus documentation) and recorded in
aggregate form — never authored from a held-out item
(§2.3). **(c)** A fitted artefact (a trained model file,
a fitted table) is pack content of the same standing as
a prompt text: pinned, versioned, and adoptable only
through §2.

**Auxiliary documents (optional).** A pack directory
may additionally carry human-readable auxiliary
documents under `profile/` — a corpus census, a
corpus profile narrative. They are documentation
only: non-normative, never loaded, and no part of
the contract above; a pack without them is complete.
Two ship today: the Bio Medical pack's corpus census
(`packs/bio-medical/profile/CENSUS.md`) and the Legal
Contract pack's corpus profile
(`packs/legal-contract/profile/profile.md`).

### 1.2 Identity, versioning, pinning

- `pack_id` is the domain's stable name (`legal-contract`).
  It never changes across versions.
- `pack_version` is a semantic version of the pack's
  IN-FORCE content. Any change to the payload — a
  definition, a switch, a prohibition — is a version
  bump, never an in-place edit: the previous version's
  payload file stays in the tree (`setup_config_v3.json`
  beside `setup_config_v4.json`).
- The manifest pins the payload by sha256. A payload
  whose bytes no longer match the pin is a different
  version that has not been taken, and the loader refuses
  it (§1.4). Candidate artefacts are pinned the same way.
- The profile's own `profile_hash` / `profile_version`
  continue to exist inside the payload lineage; the pack
  pin is over the payload FILE, so pack identity +
  payload identity are both checkable from the manifest
  alone.

### 1.3 Resolution order and precedence

**The generic core is the default; a specified pack wins;
undeclared is core-only.**

Per surface, concretely:

1. A batch names a profile and, through `--pack`, a pack.
   The pack's payload is the family's setup_config for
   every channel the payload covers.
2. For each key the payload declares, the pack's value
   governs. For every key the payload does NOT declare,
   the core default governs — no guidance, for example,
   means the core prompt, byte-identical. Batch creation
   freezes the resolution into the batch directory
   (`setup_config.resolved.json`) and stamps it into the
   batch record.

   **The framing exception.** The domain-framing keys
   (`proposer_framing`, `proposer_draw_framings`,
   `slot_framing`, `slot_rules`,
   `pair_judge_generic_relation`) are profile data: the
   family's own generated profile —
   `profiles/<family>/setup_config.json` in the state
   root, written by `ontosmith profile` for the user's
   corpus — may redeclare any of them over the binding
   pack's payload at batch creation, and the profile's
   value governs. No other key crosses: a generated
   profile's thresholds, inventories, and guards never
   overlay a pack. A profile that declares no framing
   leaves the resolution exactly the pack's — the
   measured path stays byte-identical. Batch creation
   prints the effective `proposer_framing` with the
   resolution, so the genre in force is visible before
   any spend.
3. Partial packs are legal: a pack that declares only an
   inventory is a vocabulary pack and nothing more.
4. One active pack per family in format 1. Composition of
   several packs over one corpus is a format-2 question,
   deferred deliberately — with the overlap rule stated
   now so it is not invented later under pressure: **two
   packs whose in-force content defines the same surface
   (a type, a relation, a prohibition) differently may
   not both be active over one corpus until an overlap
   measurement has priced the conflict and a precedence
   is registered.**

### 1.4 Fail-closed semantics

Loading a pack refuses, naming the defect, when:

- the manifest is missing or malformed;
- a required field is absent;
- a field is present that the pack schema does not
  define (§1.6);
- the pack_format is unsupported;
- the payload file is missing;
- **the payload's bytes do not match the pin**; or
- the payload fails setup_config validation (an
  unconsumed key is a silent no-op, and a pack may not
  smuggle one in).

There is no degraded mode: a run that
cannot load its named pack does not fall back to
core-only under the pack's name — that would be a
provenance break, not a quality nit. The same refusal
applies at batch creation when a named pack's
`profile_family` does not match the batch's profile.

### 1.5 Per-run provenance

A run under a pack records, in its batch manifest beside
the profile hash: the `pack_id`, `pack_version`, and the
payload sha256 (visible in `ontosmith batch create`
output as the `pack:` line). Because the payload is the
file the profile lineage pins, pack identity + payload
identity are checkable from the run record alone. A run
whose executed configuration is byte-identical to the
pack bundle it named is stamped accordingly; a run under
no named pack runs the core baseline, and its record
says so.

### 1.6 The field-level schema

Sections 1.1–1.5 state the contract in prose; the field-level
definition of both files is a pair of JSON Schema (draft
2020-12) documents shipped beside the packs —
`packs/pack.schema.json` and `packs/conformance.schema.json`.
The loader validates every manifest against the pack schema
before any field is trusted, and a field the schema does not
define is refused: adding a field is a format change, made
by changing the schema, never by smuggling a key into a
manifest. `ontosmith ontology check <pack-dir>` runs the
whole check a contributor needs before submitting a pack —
manifest schema, conformance-record schema, and the loader's
pin + payload validation.

The tables below are the human view of the two schemas.
They are kept honest mechanically: a test flattens both
schema documents to field paths and asserts the tables
carry exactly those paths, no more and no fewer.

#### pack.json fields

| Field | Type | Required | Read by | Meaning |
|---|---|---|---|---|
| `pack_format` | integer (= 1) | yes | loader | Manifest format version; format 1 carries exactly one setup_config payload |
| `pack_id` | string (slug) | yes | loader, provenance, batch CLI | The domain's stable name; never changes across versions |
| `pack_version` | string (semver) | yes | provenance | Version of the pack's in-force content (§1.2) |
| `status` | enum: `in_force`, `superseded`, `draft` | yes | — (record) | Lifecycle of this version; `in_force` is the version a resolution loads |
| `display_name` | string | yes | — (record) | Human-facing pack name |
| `profile_family` | string | yes | loader, batch CLI | The profile lineage this pack binds; lookup matches on this, never on directory names |
| `assembled` | string (date) | yes | — (record) | Date this manifest version was assembled |
| `conformance_record` | string | yes | `ontology check` | Pack-relative filename of the conformance record |
| `design` | string | no | — (record) | Design source; shipped packs cite only documents in this tree |
| `scope` | string | no | — (record) | One-line corpus/domain scope statement, where one exists |
| `validated_on` | string | no | — (record) | The single dataset the in-force content was validated on, where one is named; the conformance record is normative |
| `emitted_by` | string | no | — (record) | Emitter stamp on builder-emitted packs |
| `payload` | object | yes | loader | The one payload this pack binds |
| `payload.kind` | string (= `setup_config`) | yes | loader | Payload kind |
| `payload.path` | string | yes | loader | Repo-relative path of the payload file |
| `payload.sha256` | string (sha256 hex) | yes | loader (pin) | The pin over the payload's bytes; drift refuses (§1.4) |
| `payload.profile_version` | integer | no | provenance | The payload's own profile-lineage version |
| `payload.note` | string | no | — (record) | What this payload is and why these are its bytes |
| `contents_index` | object (string → string) | no | — (record) | Human-auditable map of what the payload carries; descriptive, the payload is normative |
| `convention_notes` | array | no | — (record) | Corpus-convention observations (§2.3) |
| `convention_notes[].id` | string (`CN-<n>`) | yes | — (record) | Note identifier within the pack |
| `convention_notes[].note` | string | yes | — (record) | The observation, in aggregate form |
| `convention_notes[].status` | enum: `in_force`, `observed_not_in_force` | yes | — (record) | `in_force`: states a rule the in-force payload follows; `observed_not_in_force`: recorded observation, governs nothing |
| `artefacts` | array | no | loader | In-force pack data files beyond the payload, each pinned |
| `artefacts[].kind` | string | yes | loader | Artefact kind; the loader consumes `role_words` and `relation_declarations` (the latter named by the payload's `relation_declarations_source` key), other kinds ride as data through the payload's declarations |
| `artefacts[].path` | string | yes | loader | Repo-relative path of the artefact file |
| `artefacts[].sha256` | string (sha256 hex) | yes | loader (pin) | The pin over the artefact's bytes |
| `artefacts[].note` | string | no | — (record) | What the artefact is and how it is consumed |
| `artefacts[].status` | enum: `in_force`, `in_force_emission_data_only` | no | — (record) | `in_force_emission_data_only`: in force as emission data only, not a prompt surface |
| `candidates` | array | no | — (record) | Pinned content NOT in force at this version (§1.1) |
| `candidates[].id` | string | no | — (record) | Candidate identifier, when one of a named series |
| `candidates[].kind` | string | yes | — (record) | Candidate kind (`setup_config` or a content-variant kind) |
| `candidates[].status` | enum: `candidate_not_in_force`, `measured_not_adopted`, `closed` | yes | — (record) | Pinned awaiting a gate / priced and not adopted / priced and the route closed |
| `candidates[].note` | string | yes | — (record) | What the candidate is and the standing of its measurement |
| `candidates[].conformance` | string | no | — (record) | The conformance entry id that prices this candidate |
| `candidates[].path` | string | no | — (record) | Repo-relative path, when the candidate is a single pinned file |
| `candidates[].sha256` | string (sha256 hex) | no | — (record) | The pin over the candidate file's bytes (single-file shape) |
| `candidates[].profile_version` | integer | no | — (record) | The candidate payload's lineage version (single-file shape) |
| `candidates[].artefacts` | array | no | — (record) | The candidate's pinned files, when it is an artefact set |
| `candidates[].artefacts[].path` | string | yes | — (record) | Path of one of the candidate's files, as recorded when measured |
| `candidates[].artefacts[].sha256` | string (sha256 hex) | yes | — (record) | The pin over that file's bytes |

#### conformance.json fields

| Field | Type | Required | Read by | Meaning |
|---|---|---|---|---|
| `record_format` | integer (= 1) | yes | `ontology check` | Conformance record format version |
| `pack_id` | string (slug) | yes | `ontology check` | The pack this record belongs to; must match the manifest |
| `pack_version` | string (semver) | yes | — (record) | The pack version the record was opened under; entries accumulate across versions |
| `spec` | string | yes | — (record) | The measurement spec the record follows (this document, §2) |
| `entries` | array (≥ 1) | yes | — (record) | The priced items, in record order |
| `entries[].id` | string | yes | — (record) | Entry identifier within the record (`GC-1`, `CR-3`, `DC-2`, `CC-4`, `OB-1`, …) |
| `entries[].date` | string (date) | yes | — (record) | Date of the measurement |
| `entries[].status` | enum: `adopted`, `measured_adopted`, `measured_baseline`, `measured_pilot`, `measured_not_adopted`, `closed`, `emitted_unmeasured` | yes | — (record) | Priced and in force / the control reading / pilot-scale pricing / priced and not adopted / route closed / builder-emitted, unmeasured |
| `entries[].content` | string | yes | — (record) | What the item is — the content whose place is priced |
| `entries[].treatment` | string | yes | — (record) | The item as applied for the measurement |
| `entries[].baseline` | string | yes | — (record) | The control the delta was measured against |
| `entries[].population` | string | yes | — (record) | Corpus population and its fitting/evaluation split relationship |
| `entries[].instrument` | string | yes | — (record) | The run, harness, or analysis that produced the numbers |
| `entries[].delta` | string | yes | — (record) | The measured numbers, both directions where shown |
| `entries[].gate` | string | yes | — (record) | The pre-registered yardstick and gate legs |
| `entries[].verdict` | string | yes | — (record) | The outcome in words, carrying the gate result |
| `entries[].decision` | string | no | — (record) | The human decision the measurement fed, when one is recorded |

## 2. Conformance measurement — how pack content earns its place

A component keeps its place only by measured conformance.
The pack carries the record; this section is the spec
the record follows.

### 2.1 The measurement

For each content item proposed for adoption (a guidance
revision, a definition change, a switch, a fitted
artefact):

- **Pack-on vs pack-off** on the SAME population: the
  item's Δ against the in-force version as control, same
  instrument, same split, paired where the instrument
  supports it.
- **Held-out gold only.** The evaluation split is never
  the fitting split. Content fitted on Train is priced
  on Dev; content priced on Dev needs a fresher split
  before it can be re-priced (the one-registered-evaluation
  discipline).
- **Pre-registered readings and gates.** The yardstick,
  the population, the gate legs, and the caps are written
  before the run — never chosen from the results. Every
  stage report carries the frozen readings as
  non-regression guards beside the primary yardstick.
- **Per-item records, not pack-global claims.** A pack
  version is never "better" in the abstract; each item's
  entry stands or falls on its own gate.

### 2.2 The record

`conformance.json` entries carry: `id`, `date`, `content`,
`treatment`, `baseline`, `population` (with its
fitting/evaluation split relationship), `instrument`,
`delta` (the measured numbers, both directions where the
instrument shows both), `gate`, and `verdict` + `status`
∈ {`adopted`, `measured_adopted`, `measured_baseline`,
`measured_pilot`, `measured_not_adopted`, `closed`,
`emitted_unmeasured`} (the schema's enum, §1.6).

A
negative result is recorded with the same standing as a
positive one — the record is the pack's memory of what
NOT to re-propose without new evidence. A pack emitted
by the ontology builder starts with a single entry whose
status is `emitted_unmeasured`: the artifact exists, no
measurement has priced it, and the entry says exactly
that.

### 2.3 Fitting-side discipline

Convention notes and fitted artefacts are built from
fitting-side material only: Train-side gold, the corpus's
published documentation, aggregate anatomy of failures
(counts and shares over the held-out side, never its
items). A note that cannot be stated in aggregate form
does not enter the pack.

### 2.4 What conformance is not

Not assumed from authority (a definition being "official"
does not price it), not inherited across domains (a Δ
measured on one pack's corpus says nothing about
another's), and not a one-time ceremony (a model-tier
change re-opens the price of every instruction-shaped
item, because the consumer changed).

## 3. The core / pack boundary

Every element of a build belongs to exactly one class:

| Class | What it is | Examples |
|---|---|---|
| **CORE** | Domain-invariant mechanism or policy; identical for every domain | The stage machine, queue, registry, graph projection; the fail-closed key registry and guidance renderer; the Jev gate and its call shape; the cross-document merge guard |
| **PACK** | Domain content; rides as data in the payload, never as a code branch | Type inventories with definitions and boundary examples; merge prohibitions; clause label categories; surface→canonical relation maps; the domain's measured judge-variant selection |
| **RUN** | A per-run parameter — the operator's choice for one run, not the domain's and not the core's | Thresholds and the review band; the enumeration window; the spend stop-line; model bindings (capability-tier assignments made in the connection layer) |

Two standing consequences:

- **Model names are never pack content.** A payload may
  declare that a seat is exercised (and mirror the
  operating declarations it was authored under), but the
  binding itself lives in the connection layer
  ([connections.md](connections.md)); the values a batch
  actually obeys are its run configuration, not the
  payload's declarations.
- **A domain needing a different core value needs a core
  decision, not pack content.** Safety guards pinned in
  the core (the cross-document merge minimum is the
  standing example) do not become per-domain settings by
  being copied into a payload.

## 4. The schema draft — the builder's working document

A pack begins as a **schema draft**: the JSON document
`ontosmith ontology draft` writes, the author edits,
`validate` checks, `approve` binds (to the schema
hash), and `emit` turns into the pack of §1. The
hands-on guide's Track 3 walks the loop; this section
is the document's field-level definition — every key
the builder reads or writes, so a draft can be read,
edited, or written by hand with no guesswork.

| Field | Type | Written by | Meaning |
|---|---|---|---|
| `kind` | string (= `ontology_schema_draft`) | draft | Document kind; validation refuses anything else |
| `builder_version` | string | draft | Version of the builder that produced the draft |
| `created_at` | string (timestamp) | draft | When the draft was produced |
| `family` | string | draft (from `--family` / the profile) | The corpus family the schema belongs to; becomes the pack's `profile_family` |
| `pack_id` | string (slug) | draft (from `--pack-id`) | The pack's stable name; names the emitted pack directory |
| `display_name` | string | draft | Human-facing pack name |
| `status` | string (= `DRAFT_NOT_APPROVED`) | draft | Lifecycle stamp; approval is a separate record, never a status edit |
| `provenance` | object | draft | Where the draft came from (below) |
| `provenance.profile_hash` | string | draft | Hash of the stage-0 profile the draft was made from |
| `provenance.corpus_family` | string | draft | The profile's family, as recorded at draft time |
| `provenance.drafting_tier` | string (= `drafting`) | draft | The seat that drafted |
| `provenance.drafting_source` | enum: `deterministic`, `llm` | draft | `deterministic`: the brief-seeded skeleton (no model). `llm`: the bound drafting seat wrote the schema front |
| `provenance.brief` | string \| null | draft | The brief text the draft was seeded from |
| `schema_front` | object | draft, then the author | The schema itself — the single source of every definition (below) |
| `entity_types` | array of string | draft (derived) | Mirror of the entity inventory's type names, in declaration order; the schema front is normative |
| `relation_types` | array of string | draft (derived) | Mirror of the relation inventory's type names |
| `merge_prohibited_type_pairs` | array of [string, string] | brief / drafting / author | Operating declaration: entity type pairs the build's entity resolution must never merge (a prohibited pair is recorded to the ER review band instead). Rides the emitted payload; bound by the approval hash |
| `theme_attest_chunk` | boolean | author | Operating declaration (opt-in, default off): formerly widened the candidate guard's theme-presence check from the quote to the proposal's chunk. Theme grading has subsumed it — a verb+preposition proposal whose object attests in the document but not in its quote is now kept with its evidence grade (`theme_grade`) for the judge, so the declaration no longer changes guard behavior; it remains accepted and still rides the emitted payload, bound by the approval hash. The guard itself is documented in the hands-on guide, Track 3 §5 |
| `decision_points` | array of string | drafting | Open questions the drafter surfaced for the human approver |

The `schema_front` object:

| Field | Type | Meaning |
|---|---|---|
| `purpose` | string | One paragraph: what this schema is for |
| `entity_inventory` | array | The entity types (entries below) |
| `relation_inventory` | array | The relation types (entries below) |

Each `entity_inventory` entry:

| Field | Type | Meaning |
|---|---|---|
| `type` | string | The type name |
| `role` | string (= `entity`) | `entity`, or a qualifier role for context-only types |
| `definition` | string | One precise paragraph — what this type is |
| `form_examples` | array of string | Typical surface forms of the type |
| `not_entities` | object (category → array of string) | Boundary examples that must NOT be extracted as this type, grouped by category |

Each `relation_inventory` entry:

| Field | Type | Meaning |
|---|---|---|
| `type` | string | The relation name (surface form, e.g. `works at`) |
| `definition` | string | One precise paragraph — when this relation holds |
| `endpoint_signature` | array of 2 string | The entity types the relation runs between, subject first |
| `boundary_examples` | array of string | Statements or near-miss cases that mark the relation's boundary |
| `direction` | enum: `directed`, `symmetric` | Optional; defaults to directed |
| `inverse_of` | string | Optional; the relation name this one inverts |

Definitions live **only** in the schema front: a
competing definitions carrier anywhere else in the
draft — or a type-list entry carrying a definition
that disagrees with the front — is a `DEFINITION_FORK`
finding, and any finding blocks approval and emission.
