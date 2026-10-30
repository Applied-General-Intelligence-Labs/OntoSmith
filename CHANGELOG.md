# Changelog

All notable changes to OntoSmith are recorded here. The
format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Identity seat seam** — `guard_triples` accepts an
  optional `identity_seat`: an injected judge that
  decides rule 4's completion question (whether a
  fragment mention completes to a longer census name)
  per candidate, from a code-computed feature envelope.
  The seat answers complete / stand / no opinion; no
  opinion defers that candidate to the rule chain. The
  default is no seat — the deterministic rule chain,
  unchanged. The batch path wires the seat from a
  profile key, `identity_seat_decisions`: a frozen
  JSONL file of verdicts an offline judge produced for
  the corpus, keyed by document hash. A declared file
  that cannot be loaded fails the stage closed, and
  the run metrics record the file's hash and how often
  the seat was consulted and ruled. The seam is the
  measured outcome of the identity-surgery program
  (2026-10-30): on a frozen 245-point gold the seat
  matched the rules' accuracy with zero
  referent-changing completions (the rules made two),
  and on fresh documents it completed the legitimate
  full names the rule chain refused (4/4 against 0/4)
  at the cost of one title misnaming. The probe replay
  through this wiring reproduces the measured seat
  outcomes byte for byte.

### Changed

- **Upstream attestation program** — the candidate
  guard now reads a document the way a reader does:
  against the entities the document itself establishes,
  not raw strings alone. Attestation is tiered
  (verbatim; the document's stipulated aliases —
  defined terms and acronym pairs collected into a
  per-document registry; punctuation-spacing collapse
  for tokenized corpora; a proposer-declared derivation
  surface, behind the profile-gated declared-surface
  prompt variant). A defined term is an identity
  inside its document: the role-word guard fires only
  for undefined role nouns, and a defined term with a
  stated referent canonicalizes to it. The
  theme-presence block is retired in favor of grading:
  a verb+preposition proposal whose object attests in
  the document but not in its quote is kept with a
  `theme_grade` for the judge instead of being blocked
  wholesale (`theme_attest_chunk` declarations remain
  accepted and are subsumed). Identity resolution no
  longer rewrites a complete attested name into a
  longer name that merely contains it (a city is not
  its city council), never resolves into a coordination
  surface, and still completes surnames and generational
  suffixes. Duplicates are recorded in the blocked
  sidecar (`duplicate`) instead of vanishing uncounted.
  Measured on recorded corpora replayed offline
  (current guard vs fixed guard, identical inputs):
  contract-slice role-word blocks 89 → 19, kept
  candidates 4,965 → 5,193; novels theme blocks 626 → 0
  (491 graded, the rest attesting in their evidence
  window); wiki seat kept-stage recall 0.186 → 0.210.
- **Documentation polish** — the README gains a
  worked-examples section (three edges from the
  project's own records: a head of government named
  inside another article, a novel's character entering
  by relation, a contract's defined term as an
  identity), and the project site is rebuilt around
  the same examples, with the hero graph drawn as an
  animated SVG. The Connections recommended-models
  table no longer lists the retired GLiNER spotter as
  a current seat. No measured claim changes.

### Added

- **Open Domain pack 0.5.0: TN-3/4/5 data adopted (RE-TN-1)** —
  `packs/open-domain/tn_table.json` goes to version 2: the
  TN-3 relation groups, TN-4 inverse expansions and TN-5
  subsumptions whose code support landed with the scorer
  change below are now adopted as data, under registered
  evaluation RE-TN-1 (conformance entry DC-6), for the
  EXTENDED figure only. Dual reporting is mandatory: the
  frozen TN-1/TN-2 figure is always reported beside the
  extended one, and the delta is labeled an instrument
  change, never a quality gain (on the frozen cap-b records
  against gold v1.1.0: kept predictions 273 → 335, proposal
  union 301 → 364; baseline hits lost 0, unverified new
  hits 0). The pack manifest pins the table as an
  `in_force` artefact, and `verify_cli.load_table` now
  reads the pack-native tn_table format directly, so the
  shipped file works with `--normalization` as-is. The
  extraction payload is unchanged (same sha256 pin).

- **Scorer: alias-aware matching + opt-in TN-3/4/5 layers** —
  `match_records` now also matches a predicted record under
  the attested alias surfaces in its guard-emitted
  `canonicalized` field (the guard's identity resolution
  rewrites endpoints to one canonical surface; the frozen
  scorer matched only the emitted surface, which read the
  upstream-attestation fix as a recall regression on the
  cap-b replay: 262 vs the old guard's 273 — with alias keys
  the same output scores 301). Alias keys are the cross
  product of attested subject x object surfaces, mirroring
  gold_keys, so a record canonicalized at BOTH endpoints
  still matches its gold pair. `NormalizationTable` gains
  three caller-data layers, all default-empty (byte-identical
  to the TN-1/TN-2 instrument): TN-3 relation groups, TN-4
  inverse expansions, TN-5 subsumptions. Grouping ADDS keys
  on both sides — verbatim keys are always retained, so a
  group can never break a TN-2 pairing (verified on the
  frozen cap-b corpus: baseline hits lost 0; hits beyond the
  frozen instrument plus the hand-verified variant set 0).
  `verify_cli.load_table` reads the new keys. Enabling the
  layers as a pack's table is a new registered evaluation
  (the open-domain table is frozen; dual reporting per its
  effect_reporting rule) — measured on cap-b/gold v1.1.0, the
  full stack would read 366 candidate-level hits vs 273
  frozen (union 364 vs 301); the table data itself is NOT
  adopted here.

- **Mention layer, first cut (issue #54)** — profile-gated
  (`mention_layer` in setup_config; absent = off, and the
  undeclared path is byte-identical). Entities register by
  noun surface forms, so facts stated via pronouns, definite
  descriptions, or cross-sentence evidence had no citable
  path to an entity and drops recorded only a score. With
  the layer declared, the free proposer emits per-endpoint
  mention spans (verbatim, as they appear in the quote),
  mention types (named / pronominal / descriptive), and —
  for non-named mentions — a resolution proposal: an
  antecedent span, verbatim from the document. The guards
  verify both spans with the same discipline as quotes;
  an unverifiable span blocks the candidate by name
  (`mention_missing` / `mention_span` /
  `mention_antecedent`), never silently. GATES judges each
  resolution as its own Jev decision before the fact:
  clear proceeds, ambiguous (review band) is capped at
  Tier-2 review, rejected drops with reason class
  `resolution_rejected` — distinguished from
  `fact_rejected` in the drop accounting. The quote is
  never rewritten; resolution travels as annotation.
  **Measured (Re-DocRED 40-doc A/B vs the cap-25 recipe,
  pre-registered criteria): the first cut does not ship
  as a recall feature.** Candidate recall was flat
  (0.1189 vs 0.1183, with a genuinely different proposed
  slice — 46 gold proposed only with the layer on), and
  resolution judging behaved as designed (58 clear /
  3 rejected / 1 review of 62), but Tier-1 recall fell
  to 0.0772 from 0.1066 (paired net −43): the guards
  blocked 69 of 257 proposals as `mention_missing`
  because the proposer's mention-field compliance is
  unreliable, culling candidates before judging. Raw
  Tier-1 precision rose (0.4912 → 0.6038) and cost held
  ($0.00268/doc vs a $0.00312 ceiling). The layer stays
  an experimental opt-in with that measured trade-off;
  the full record is in the cap-recall measurement
  (Phase 2 section) and issue #54.
- **Entity sweep (issue #58)** — profile-gated
  (`entity_sweep` in setup_config; absent = off, and
  the undeclared path is byte-identical; a non-boolean
  value fails closed). An additive second proposal
  pass inside CANDIDATES' free-proposer branch: a
  deterministic code census names a document's
  entities, the ones appearing as no endpoint of any
  chunk-pass item become anchors, and each anchor is
  re-asked in its own mention view under the chunk
  prompt plus one anchor rule and one evidence rule:
  every sweep triple must carry a verbatim evidence
  span copied from the text, verified by code against
  the full document at proposal time — a missing or
  unverifiable span voids the proposal before the
  guards, counted in the finish record as
  `evidence_missing`. Sweep items pass the
  same guards and merge behind the chunk channel —
  duplicates and off-anchor items are counted, never
  shadowing or replacing chunk provenance — and the
  finish record restates the pass (`candidates_sweep`:
  anchors, proposed, duplicates, off-anchor,
  evidence_missing, blocked
  by reason, shipped Tier-1). Measured on the
  pre-registered 8-document density pilot against lab
  gold v1.1.0 (issue #58): the pass recovered 33 of
  139 never-proposed gold labels at Tier-1 (paired
  net +61 Tier-1 over the pilot docs), but the pooled
  new-Tier-1 adjudicated precision was 0.676 against
  a 0.90 bar and the arm cost $0.02423/doc against a
  $0.00375 ceiling — the pilot failed on precision
  and cost, the full-corpus run was not licensed, and
  the pass stays an experimental opt-in pending a
  redesign decision. A pre-registered cut 2
  (2026-10-29) made the evidence span load-bearing —
  the void fired zero times on the re-run (the
  proposer attached a verifying span to all 272
  proposals) and precision measured 0.657 against
  the same 0.90 bar, so the pilot failed on
  precision again; yield and cost passed (P1
  35/139, paired net +67, $0.0240/doc against a
  re-registered $0.0323 bar), and the sweep line
  returns to a close-or-redesign decision. The full
  record is in the
  cap-recall measurement (Entity sweep sections) and
  issue #58.

### Fixed

- **The cost-cap preflight checked the wrong stage set.**
  `_preflight_cost_cap` selected its seats by testing
  `stage in stages` (completed stages) where its own
  docstring and both sibling preflights use pending-stage
  semantics (`not in`). On a fresh run under a USD cap with
  an unmetered proposer binding, the preflight therefore
  examined nothing, and the refusal arrived only from
  `_record_cost` — after CANDIDATES had already spent.
  The three conditions now use pending semantics, with
  regression tests pinning which seats a fresh run, a
  live-decide run, and a resumed run must check. Found in
  the 2026-10-29 implementation audit.
- **Review-band cases were keyed by document alone.**
  The interactive reviewer's resume set and undo jump
  used doc_id as a case's identity, so a document's
  second band case was silently retired by its first
  case's label (never shown, never recorded) and undo
  could land on a different triple from the same
  document. Cases are now keyed by the full triple
  (doc_id, subject, relation, object); label rows carry
  the key, and legacy doc_id-only rows still suppress
  their whole document (the data cannot say which case
  they covered).
- **Stale install and diagram text after the pipeline
  retirement.** The README's install section still
  offered the removed `[nlp]` extra ("the pipeline
  candidate path's local NER") in the present tense —
  inherited by the PyPI variant, whose generator anchor
  pinned the stale paragraph, which is why the doc
  guards stayed green. The paragraph and the anchor now
  name only the surviving extras ([viz], [neo4j]). The
  pipeline-flow diagram's NER subtitle ("entity roster
  (mentions)") and CANDIDATES subtitle ("pairs
  enumerated") likewise described retired mechanics;
  they now read "contract rows only (local NER
  retired)" and "LLM proposer (the only source)", and
  the figure is re-rendered.
- **Queue claim success read a connection cumulative.**
  `Queue.claim` tested `total_changes` (cumulative for
  the connection, effectively always true) instead of
  the statement's rowcount; the UPDATE was correctly
  guarded, so no duplicate processing was observed, but
  a racing second claimer could have double-processed
  an item. Now `cursor.rowcount`. Also from the audit:
  the `ontology_judge` return annotation claimed a
  3-tuple; it returns (and callers unpack) two values.

- **Fragment endpoints became separate graph nodes.**
  The proposer types endpoints in the surface the local
  text uses — a first name alone, a nickname, a surname
  alone, a name missing its suffix — and the graph keys
  nodes by name string, so one entity fragmented into
  several nodes (and a bare surname could wire a
  relation to the wrong bearer). The census roster
  itself fed the problem: it glued a title line to the
  body across a paragraph break, glued sentence-initial
  prose words onto names ("During Alger"), split a
  quoted nickname out of its name ("Wilfried" /
  "Willi" / "Schneider" as three surfaces for
  Wilfried "Willi" Schneider), and split spaced-hyphen
  compounds ("Bernheimer - Haus"). The census now
  keeps sequences inside paragraphs, drops
  sentence-initial prose words, bridges quoted
  nicknames (recording them, and the title form, as
  aliases of the full name), and bridges spaced
  hyphens; and `guard_triples` — the one funnel every
  proposal channel passes — resolves each endpoint
  against the roster before attestation: a fragment
  naming exactly one canonical surface is rewritten
  to it (recorded as `canonicalized`), one naming
  several is flagged (`identity_ambiguous`) and never
  guessed, and attestation reads the effective
  endpoint through the entity's recorded mention
  forms so the canonical name is not blocked for
  never occurring contiguously. Blocked records still
  carry the item exactly as proposed. Deterministic
  code only; no model is consulted on identity.
  Replaying the sweep pilot's six adjudicated
  identity failures through the fixed guard: three
  now resolve to the canonical entity, three are
  flagged ambiguous (their documents genuinely carry
  several bearers of the name) instead of shipping
  silently. Issue #58.

- **The serving index silently covered only part of a
  build's corpus.** INDEX projected chunks only for
  documents that had produced Tier-2-routed candidates,
  so a build whose candidates landed mostly Tier 1 served
  a subset of its documents — in the multilingual pilot's
  build, 1 of 16 — while VERIFY passed. INDEX now chunks
  every built document (each chunk tagged with its
  document's best tier), and VERIFY carries a
  `serving_coverage` check that re-derives the expected
  coverage from the batch's texts and fails by name when
  it is partial. A green VERIFY now means coverage is
  complete. Found by the multilingual pilot (issue #50);
  the flagship build's published recall figures were
  re-checked against its serving projection and stand
  (509/509 documents).

- **ER pairs held for human settlement were invisible in
  the batch's own accounting.** WRITE noted the held
  count, but the finish line said nothing, and a build
  could ship with every held pair unactioned in silence
  (the pilot: 44 of 49 ER decisions). The finish line now
  accounts for ER decisions by terminal state (merged /
  settled merge / settled keep-separate / unactioned),
  records the account in the batch metrics, and prints
  an `ER SETTLEMENT PENDING` line naming the settle
  command when unactioned holds remain. The settlement
  policy itself is unchanged — the review band still
  never auto-merges (issue #51).

- **CANDIDATES guard blocks were invisible in the
  batch's finish record.** The stage totals its blocks
  by reason in its own metrics and names them on its
  log line, but the finish — the record a reader signs
  off on — restated only the ER account, so a guard
  cliff was indistinguishable at the finish from an
  ordinary sparse run (the mention layer's measured
  Phase 2 cull: 69 of 257 proposals blocked as
  `mention_missing`). The finish now carries the
  by-reason block counts in its metrics
  (`finish.candidates_blocked`) and names them on its
  log line, alongside the ER account. Batch-level tests
  pin the surfacing, characterize the cliff under a
  scripted partial-compliance proposer at the measured
  27% mention-field omission rate, and pin equal
  candidate survival for compliant input with the layer
  on and off. The guard policy itself is unchanged
  (issue #54).

- **Extraction density on long documents was capped and
  unreported.** The free proposer's per-chunk ceiling
  (10 proposals per 20,000-char chunk, a CUAD-recipe
  constant) bound every pack and document shape, and
  nothing reported it: batch metrics now carry the
  per-document candidate distribution (min / median /
  max over every built document) and the CANDIDATES log
  prints it plus the count of chunks that reached the
  ceiling. The ceiling is now recipe data a profile may
  declare (`proposer_max_per_chunk`, registered and
  validated like every setup_config key); undeclared
  keeps the core default, byte-identical prompts
  (issue #52).

- **Non-Latin queries retrieved nothing.** The query
  tokenizer was `[a-z0-9]+`, so a Korean-only query
  produced zero tokens — the lexical arm never fired and
  the graph arm's overlap sets were empty — and the
  arms' length filter also dropped dense two-character
  Hangul content words. Tokenization is now script-aware
  (Unicode word tokens; byte-identical streams on ASCII
  input, so the flagship evaluation mirror is unchanged),
  the content-token rule counts two-character non-ASCII
  tokens, and the full-text indexes accept a per-deployment
  analyzer via `ONTOSMITH_FULLTEXT_ANALYZER` (unset keeps
  the stock analyzer and the pre-fix schema). Whitespace
  tokenization is not morphology; the limit is stated in
  the serving guide (issue #53).

- **Hands-on Track 1 §4 named the wrong store.** The
  finish line printed the file-store serve banner and
  promised ranked documents unconditionally; a reader
  arriving from §3 with the store variables still
  exported is served by the graph store instead, and
  its Documents section depends on whether the build
  projected Tier-2 chunks. The section now shows the
  graph-store banner, states the environment rule that
  picks the store, and conditions the ranked-documents
  promise on chunks being present. Found by the
  retroactive vanilla-VM verification of Tracks 1–3.

- **Hands-on Track 3 could not be followed verbatim.**
  §1 printed a stale skeleton schema hash (the draft
  tool's deterministic output has moved since the
  capture); §2's validate block dropped the field-path
  prefixes the tool prints; §4's emit command named an
  approval file dated from the capture, which a reader
  running the track today does not have. The hash is
  refreshed, §2's block matches the tool's output, §3
  now states that the approval file's name and the
  schema hash are the reader's own, and §4 takes the
  approval file by placeholder. Found by the same
  retroactive verification.

### Changed

- **Mention layer: absence now falls back instead of
  blocking (issue #54, cut 2).** The first cut's guard
  treated an endpoint whose mention data was absent the
  same as an invalid mention, and the Phase 2 A/B
  measured the cost: 69 of 257 proposals culled as
  `mention_missing` on the proposer's unreliable field
  compliance, Tier-1 recall 0.1066 -> 0.0772. The layer
  is now additive by policy: an endpoint with no mention
  data proceeds under the legacy evidence path — judged
  on its quote exactly as without the layer — while a
  mention that is declared but invalid (span outside
  the quote, antecedent absent / not verbatim / naming
  another entity, malformed type) still blocks by name.
  Declared, valid resolutions are judged by GATES
  exactly as before. Every fallback is counted by
  endpoint in the stage metrics and restated in the
  finish record (`candidates_fallback`), and candidates
  name their fallback endpoints on the record, so the
  fallback rate is always visible. **Measured (the
  same pre-registered A/B as Phase 2, criteria
  unchanged): the fallback policy removes the first
  cut's failure point but still does not ship as a
  recall feature.** `mention_missing` blocks fell
  69 → 0, the 18 endpoints without mention data were
  judged on their quotes, and judged-file candidate
  recall recovered (0.0793 → 0.0943); raw Tier-1
  precision held (0.5588) and cost held ($0.00291/doc
  vs a $0.00312 ceiling). But Tier-1 recall reached
  0.0895 against the baseline's 0.1066 (paired net
  −25): this draw's proposal channel was thinner
  (249 proposals vs the baseline draw's 327; candidate
  paired net already −23 before judging), and
  resolution judging cost 7 rejected + 4 review-capped
  of 81. The guard semantics stay (absence is not
  invalidity, and the fallback accounting stands);
  the layer remains an experimental opt-in with the
  measured trade-off. Full record: the cap-recall
  measurement (cut-2 section) and issue #55.
  **Precision claim, measured and closed (issue #56).**
  The raw-precision edge above (0.5588 vs 0.4912) was
  tested under a separate pre-registration with blinded
  adjudication (the flagship correction's instrument):
  adjudicated Tier-1 precision is 0.9562 with the layer
  and 0.9561 without — a gain of +0.0001 against a
  registered +0.05 bar. The raw gap was gold's error
  distribution, not the layer (~90% of both arms'
  disagreements are in fact supported). The layer does
  not ship as a precision feature either; the record is
  the cap-recall measurement (precision program
  section) and issue #56.

- **Hands-on guide restructured for a lighter first
  read.** The appendix track (reproduce the reference
  build, steps A1–A9) moved to its own document,
  `docs/hands-on-appendix.md`; the front matter was
  compressed (the four tracks now open in a compact
  table, Track 1 starts at the top of the guide, and
  the profile/pack glossary moved into Track 1's
  opening); the pipeline-path install block moved to
  the end of the guide as an advanced note; and the
  example-document-sets table was slimmed and moved
  ahead of Track 2. Layout only — no step text
  changed: every command, quoted output, and
  checkpoint is the VM-verified text, relocated
  verbatim. README, llms.txt, and the benchmarks
  appendix now point at the appendix document.

### Removed

- **The legacy scale1k graph writer
  (`pipeline/graph_write.py`) is removed.** It wrote
  name-keyed entities with per-relation edge types
  directly from a gate-results file — the pre-product
  identity discipline the shipped store contract
  replaced (entity_id keys, one REL type, a
  batch-stamped single graph, replace-at-WRITE) — and
  derived its model/gate provenance from the results
  filename. Nothing referenced it: no import, test,
  doc, or CLI dispatch. The batch WRITE stage through
  the graph store is the only write path.
- **The GLiNER+spaCy pipeline candidate source is
  retired, and the `candidate_source` setting is
  removed with it; the LLM proposer is the only
  candidate source.** The pipeline path was the
  product default but was never measured at the
  standard the LLM path carries, its structural
  (RER) pass was English-bound by construction
  (hand-written dependency patterns — each new
  language would have multiplied channels), and it
  matched extracted spans to NER mentions with its
  own order-dependent substring rule, a second
  identity discipline beside the guard funnel's (the
  same span could land on different people in
  different documents, and full names could collapse
  to a fragment listed earlier). Rather than grow a
  third implementation of the identity law for it,
  the channel is removed: `pipeline/rer.py` is
  deleted, the NER stage writes empty contract rows
  (its `ner.jsonl` consumers read tolerantly), the
  `ontosmith[nlp]` extra is removed from the package,
  and the `--candidate-source` flag and the
  `candidate_source` config field are gone — with
  one source there is nothing to select. A stored
  config still naming a retired value refuses by
  name at load; one carrying `"llm"` (or nothing)
  loads silently. All measured builds were already
  LLM-path builds; serving is unchanged.

## [0.1.1] - 2026-10-27

Release notes: [v0.1.1 on GitHub](https://github.com/Dongbumlee/ontosmith/releases/tag/v0.1.1).

### Added

- **Ontology import — the BYO-schema door.**
  `ontosmith ontology import` consumes a mechanically
  extracted inventory of an external OWL/RDF ontology
  plus a human-authored scoping spec (per-type kept /
  merged / flattened, role classes converted to
  relations, datatype properties demoted, drops only
  with stated reasons) and emits an ordinary builder
  draft plus a complete scoping record. Fail-closed:
  every concept in the spec's scoped files must be
  accounted for exactly once. The draft passes the
  same validate → approve → emit gates as a drafted
  schema.
- **Pack: `fibo-contracts` 0.1.0** — FIBO's FND
  Agreements module (EDM Council, MIT; release
  `master_2026Q3`) imported through the builder: 88
  scoped concepts → 15 entity + 13 relation types.
  Working papers in `examples/fibo-import/` (extractor,
  inventories, spec, scoping record, coverage account:
  7 of CUAD's 41 clause categories covered by a
  dedicated type, 34 partial, 0 invisible). Emitted
  `emitted_unmeasured`, like every builder pack.
- **Hands-on Track 4** — the import walked end to end
  (fetch → inventory → scope → import → gates →
  build) over a 100-contract CUAD subset.

### Changed

- **Hands-on instruction detail.** The candidate-path
  choice (tracks' `llm` vs the product default
  `pipeline`) is now stated before any install
  command, with what each path installs; Neo4j setup
  no longer implies Docker — a compact example block
  (standalone-tarball key commands plus a one-line
  Docker alternative) with a link to Neo4j's
  official installation guide, and the OntoSmith
  contract (password export, HTTP-200 verify
  checkpoint, the named store refusal) stated as
  ours, in the guide and in connections.md;
  checkpoints and the predictable refusals are
  quoted at the steps where they occur.
- **Whitepaper corrections.** Reference citations
  corrected to the canonical records (Re-DocRED,
  BioRED); cover status line updated to the shipped
  release line; §5.7 names the populations of its two
  corrected precisions and declares its Tier-1-wide
  figure record data; §3.3–§3.4 describe the fourth
  pack and the import door.

### Fixed

- **First-run serving gap (issue #48).** A build's
  first `search` against a fresh store refused by
  name — no full-text index `chunk_text_ft` —
  because nothing in the run created the serving
  schema or projected the build's chunks into the
  store; both existed only behind the
  `pipeline.graph_store` module CLI, which a store
  carried only if someone had run it by hand. The
  INDEX stage now ensures the serving schema
  (constraints + the vector index + both full-text
  indexes) and projects the batch's chunks as part
  of the stage, and `search` / `serve` heal a
  pre-fix store at open: schema ensured, the
  stamped batch's chunks projected from its
  recorded index, no provider spend. Idempotent
  throughout; a store that is down still refuses
  by name.

## [0.1.0] - 2026-10-24

Release notes: [v0.1.0 on GitHub](https://github.com/Dongbumlee/ontosmith/releases/tag/v0.1.0).

### Changed

- **The store holds one graph (RR program).** Neo4j
  now holds exactly one graph — the current build's,
  written under a completed ontology. WRITE stamps
  the store with the graph's identity (pack + version
  or profile lineage, batch, triple count). A build
  under a changed ontology **replaces** the previous
  graph instead of accumulating beside it: the
  preflight names the graph in the store before
  anything is spent, the WRITE stage names the
  replacement and records what it removed, and
  content no stamp identifies stops the run by name
  rather than being overwritten. Same-build rewrites
  stay idempotent (VERIFY unchanged). Search (CLI and
  `serve`) is scope-free by construction over Neo4j:
  it resolves the store's one graph from the stamp —
  an unstamped or empty store refuses by name, and a
  `--batch`/`--source` naming a different build is an
  expectation check that refuses by name, not a
  scope. The file read-back path is unchanged. The
  registry still keeps every replaced graph's full
  history — the store is current-state only. The
  `er-apply` projection asserts the same one-graph
  rule and names any graph it replaces. Docs aligned
  to the settled store story everywhere it is told
  (release notes, serving, connections), including a
  stated scope for the schema itself — a flat
  extraction contract, not a formal ontology — on the
  README, Pages, and `llms.txt`.
- The WRITE hold line in `batch run` output now names
  its remedy — `ontosmith batch er-review <batch>` —
  and Track 3 §5 of the hands-on signposts the ER
  settlement flow where a hold under an authored pack
  can occur.
- The CLI reference explains the ER pair-id sequence:
  ids are one registry-wide `er_decisions` sequence
  shared with machine rows, so held-pair listings
  skip numbers by design.

### Added

- An ER review-band settlement surface (issue #45):
  `ontosmith batch er-review` lists a batch's held
  entity-resolution pairs with the evidence to decide
  them (both registry entities, the gate's `p_same`,
  triple counts, sample contexts); `batch er-decide`
  records a human merge / keep-separate decision as an
  append-only `er_decisions` row — a merge also folds
  the pair in the registry (the source of truth) at
  once, preserving every surface as an alias and the
  fold in `id_mapping`; `batch er-apply` amends the
  WRITE manifest through that mapping and re-projects
  the graph (re-pointed edges keep their `p_true`).
  A keep-separate pair is never re-held by later runs.
- The connection layer generalized beyond its original
  provider, and a front door for declaring bindings
  (issue #41). Seats now declare a **dialect**
  (`openrouter` for the shipped preset; a custom binding
  defaults to the portable `openai` subset), and
  provider-specific request fields are sent only under
  the dialect that declares them. Cost accounting is
  stamped with its basis — `provider-reported`,
  `estimated` from user-declared per-seat rates, or
  `not-provided` — an unknown cost is recorded as
  unknown (the former built-in rate fallback is gone),
  and a USD cap over a seat that cannot meter cost
  refuses by name unless the run is explicitly created
  with `--no-cost-cap`. **`ontosmith configure`** is the
  declaration path: an interactive wizard that walks
  the seats from the preset's values and validates the
  result with zero-spend probes, with a non-interactive
  twin — fill in `docs/bindings-template.env` and run
  `configure --from-file`. Both write the same
  `ontosmith-bindings.env`, stamped with the binding's
  provenance. The reference preset itself is unchanged,
  byte for byte.
- A photograph of the product's output in the README:
  `docs/assets/built-graph.png`, a rendering of a real
  built graph — the Bellicum × Miltenyi neighborhood of
  one CUAD supply agreement, as built by the Legal
  Contract pack (42 entities, 77 Tier-1 triples, all from
  the shipped build's Neo4j projection; no mock data).
  The caption carries the corpus credit (CUAD, The
  Atticus Project, CC BY 4.0).
- A badge row under the README banner: PyPI version,
  PyPI Python versions, and the GitHub Actions CI badge
  (all dynamic; the PyPI badges resolve once the package
  publishes at launch).
- `docs/PACK_DESIGN.md` — the pack-format contract
  (manifest, pinning, resolution, fail-closed loading,
  conformance measurement, the core/pack boundary)
  ported into the product tree, so the shipped pack
  manifests' `design` citations resolve in-tree.
- The pack format's field-level schemas:
  `packs/pack.schema.json` and
  `packs/conformance.schema.json` (JSON Schema draft
  2020-12), shipped in the wheel. The loader validates
  every manifest at load and refuses — naming the
  defect — any field the schema does not define, any
  bad enum value, any malformed pin. pack_format stays
  1: the schemas formalize the format, they do not
  replace it. `docs/PACK_DESIGN.md` §1.6 carries the
  human field tables, kept in set-equality with the
  schemas by a test.
- `ontosmith ontology check <pack-dir>` — the
  contributor validation command: schema-validates a
  pack's manifest and conformance record and runs the
  loader's pin + payload validation; exit 0 when
  clean, 1 with each defect named.

### Changed

- The chat embedder class is renamed
  `OpenRouterEmbedder` → `SeatEmbedder`: the embedder is
  a seat binding, not a provider class (the old name
  survives as a code alias). In the same pass,
  model-name pattern matching for request shape is
  replaced by per-seat capability declarations
  (`ONTOSMITH_<SEAT>_CAPABILITIES`); a seat that
  declares none still derives its capabilities from
  the model id, as before.
- The flagship quality record (whitepaper §5) is
  restructured around the **ruled adjudication
  contract**. After the record was frozen, its 740
  verdicts were stress-tested under a pre-registered
  cross-family blind recheck (agreement 0.815 against a
  0.95 bar — a registered FAIL); the disagreement
  localized to six surface situations the as-typed
  contract had never defined, those situations were
  settled by explicit ruling (new standing spec:
  `docs/ADJUDICATION.md`), and all 565 flagged items
  were re-judged blind under the ruled contract.
  Re-priced through the identical estimator, the
  record's headlines are now corrected precision
  **0.9110** [0.8919–0.9275] (FULL-500) and
  ceiling-adjusted recall **0.2028**; ĝ_B is 0.5244 —
  under the ruled contract only about half of the
  sampled gold survives as typed. The original-protocol
  estimate (0.9765) remains in the record as a single
  provenance passage (§5.4) and in the replay. The ruled
  verdict set ships in the benchmark appendix
  (`judgments_ruled/` + `RULING.md`), and
  `examples/demo/replay_scores.py` now computes both
  estimates — the ruled set is the headline, the
  original set reproduces beside it. Core pack GC-4
  restated to the ruled figures (pack 0.3.0 → 0.4.0).
- The method figure (`docs/assets/method.png`, generated
  by `harness/render_charts.py`): the Verify box below the
  flow is gone — the figure tells one story, how a graph
  is built and served (Verify lives in-flow as pipeline
  stage 8; the measurement discipline stays as the
  figure's footer line). Serving is drawn, not written:
  Graph projects into a "Serving store / Neo4j
  projection" element feeding Search, API, and App boxes
  in place of the old gray "served from Neo4j" text note.
  Emphasis rebalanced — the proposer is a muted clay,
  the Jev gates + judge box carries the figure's
  strongest navy, the graph steps down a shade — and
  Profile's subline drops its "· $0" badge: a box says
  what the step is, not what it costs.
- The connection layer's seat cast now states exactly
  the models tested in their roles. A new **escalation**
  seat carries `anthropic/claude-sonnet-5.5` — the
  measured occupant of the lab's adjudication-escalation
  record — and the gate-evaluation judges
  (`pipeline.decisions` defaults, the `gate triple`
  default model) resolve through it. The **drafting**
  seat ships **unbound**: the preset declares its
  endpoint but no model, and a drafting call without a
  user-declared model (`ONTOSMITH_DRAFTING_MODEL` or the
  command's `--model`) refuses with a named error before
  any request. Drafting is an authoring aid behind the
  human approve gate; it was exercised once in the lab
  and is not part of the measured pipeline, so no model
  is presented as its default.
- The user documentation now teaches the concepts where
  a new user first meets them (2026-10-22). The hands-on
  guide is rebuilt as three tracks over the reader's own
  documents — your documents; your documents + an
  industry pack; your documents → your own ontology —
  each ending at the same serve / search / inspect
  finish line, with the former reference-build walkthrough
  demoted to an appendix track. The README gains a
  Requirements block at the head of its install section
  (Linux tested; Windows under WSL2), plain profile-vs-
  pack paragraphs, and a five-seat reference-profile
  table — the **Provider route row is removed**: the
  table states bindings, and route provenance now lives
  only in `docs/connections.md`, which records that the
  measured record ran over the OpenRouter route for the
  routed seats and that the judge's calls go to Jev's
  pinned decisions endpoint instead. The live-build
  export of the preset key variable is framed at the
  command itself, `inspect`'s directory form leads
  (with its Gaps line explained), `binding: reference`
  is glossed at batch creation, and
  `--frontier-escalation` is documented as recorded in
  the batch config but not wired in v1.
- A documentation audit pass (2026-10-22) corrected the
  shipped documents against the code and the source
  records. Decorative "$0" badges came off the step
  headings (README, hands-on, release notes, site):
  cost is stated as a run's own record, not as
  decoration. Internal decision IDs and lab shorthand
  were rewritten as their substance throughout the
  benchmarks documents, the adjudication appendix, the
  release notes, and `CONTRIBUTING.md`. The whitepaper's
  §4.1 binding table now lists all five seats of the
  shipped preset (the stray provider-route row is gone
  there too). The flagship corrected-precision interval
  now reads [0.9641–0.9847] everywhere it is published —
  the value the shipped replay recomputes from the
  appendix (after the ruled re-pricing recorded above,
  that interval survives as labelled provenance, §5.4) — and the stage enumerations in the README
  and hands-on match the pipeline's eight stages.
- The CLI dispatcher now honors the exit codes its
  command mains return (`ontology`, `profile`,
  `inspect`, `search`, `serve`, `verify` all documented
  process exit codes that were previously dropped, so
  every invocation exited 0).

### Removed

- The `decision` connection seat. The seat named the
  judge bake-off's candidate slot; after Jev took the
  judge seat, its remaining consumers moved off it —
  the gate-evaluation judges to the **escalation** seat
  (recorded above), and the `llm` evaluation judge,
  which had resolved its endpoint through the decision
  seat, now resolves through the **judge** seat. The
  connection layer ships five seats — proposer, judge,
  escalation, drafting, embedder — and
  `ONTOSMITH_DECISION_*` variables are inert. (Earlier
  entries below that name the decision seat describe
  the cast as it stood when they were written.)
- The local-embedding fallback and the `[embed]` extra.
  The embedder is the embedder seat's hosted binding
  only: the one local model measured against it
  (all-MiniLM-L6-v2) missed candidates the hosted model
  catches, and no published figure was ever produced
  with it. Where an embedder is required — `search
  --embed`, `serve --embed`, candidate search over a
  Neo4j registry, a build's INDEX stage — a seat whose
  key cannot be resolved now refuses by name
  (`BindingKeyError`; INDEX refuses a config naming a
  non-hosted model) instead of silently embedding with
  the weaker local model. Surfaces that need no embedder
  (lexical + graph search without `--embed`) are
  unchanged.
- Lab-provenance fields from the shipped packs: the
  `issue` and `lab_codename` fields leave all four
  manifests (and the two conformance records that
  carried the codename), `design` citations point only
  at documents in this tree, and the Bio Medical pack's
  status joins the common vocabulary (`active` →
  `in_force`). Manifest-only changes; every pack's
  payload bytes and pins are untouched, and each
  changed pack's version bumps (Core 0.3.0, Open
  Domain 0.4.0, Bio Medical 0.3.0, Legal Contract
  0.2.0). The Core conformance record's GC-5 entry is
  corrected to the shipped seat cast (escalation
  Sonnet, drafting unbound) — it previously claimed a
  GPT-5 escalation/drafting combination the preset
  never carried.
- The `decision` seat and the preset's
  `DECISION_MODEL` / `DRAFT_MODEL` defaults.
  `microsoft/microsoft-decision-1` was the bake-off
  candidate that lost the judge seat to Jev; it survives
  only as the historical record of that comparison and
  can still be named explicitly per evaluation run, but
  it is no longer any default. `ONTOSMITH_DECISION_*`
  variables are now inert. Binding provenance stored by
  earlier batches is historical data and still loads
  unchanged.
- The `sllm` connection seat and its gate variant: the
  connection layer now ships five seats (proposer, judge,
  decision, drafting, embedder); `decisions.sllm_judge`,
  the `triple --gate sllm` variant (with `--sllm-model`),
  and the `llm --logprobs` mode are gone, and the preset
  no longer binds a small-LLM model. The seat was an
  evaluation-only baseline — nothing in the build path
  ever called it; the gate-evaluation record that
  measured it stands in the white paper. The `llm`
  evaluation judge (text Yes/No) is unchanged apart from
  resolving its endpoint through the decision seat.
  Binding provenance stored by earlier batches is
  historical data and still loads unchanged.

### Fixed

- `batch create` over an existing batch id no longer
  leaves an incoherent record (issue #46): it used to
  rewrite `batch.json` (new run_id, status CREATED)
  over the previous run's untouched `state.json` and
  stage artifacts. Create is now fresh-only and
  refuses by name — run/resume the existing batch, or
  create the new build under a different `--batch-id`.
- Hardening (2026-10-22): the silent paths the code audit
  found are now counted, marked, or blocking. The query
  stack's attestation join keeps each attestation's own
  recorded score (the previous dict join overwrote
  duplicate keys and defaulted misses to (0.3, 2)); the
  counts of unmatched/unscored rows ride in the eval
  output. The drift audit resolves the batch's own
  registry file and the corpus from `batch.json`, and a
  property join of 0 attestations now fails VERIFY
  instead of passing on key existence alone. Batch
  metrics count RER-LLM fallback failures, Phase-2
  worker errors, NER document/chunk failures, per-doc
  candidate-cap hits, and checkpoint resets, and stamp
  the RER env toggles when active; INDEX emits Tier-2
  doc ids in sorted order. Gate-eval error records carry
  an explicit `error` field and every summary reports
  the error count; the eval harness excludes error
  records rather than scoring them as negatives. An
  unparseable text-judge answer is marked
  `parse_failed` instead of silently taking the last
  yes/no token as the verdict. The promotion gate
  compares the profile hash against the batch record
  instead of echoing it, and profile-claim verification
  says "unverifiable" when no comparison happened
  instead of "confirmed".
- The appendix replay's pinned interval for the flagship
  corrected precision read [0.9642, 0.9847]; the true
  lower bound is 0.9641 (a documentation pass corrected
  the whitepaper
  prose but missed this constant — the replay passed
  only inside its ±0.002 tolerance). The pin now reads
  [0.9641, 0.9847] and the replay reproduces it exactly.
- Install surfaces now cover what the build loads: the
  `gliner` package joins the `[nlp]` extra (the batch
  NER stage hard-imports it on the default candidate
  path, and no extra installed it), and the spaCy
  English-model download step is stated where a reader
  needs it — CONTRIBUTING's setup and the hands-on
  guide's prerequisites, whose LIVE build tracks also
  name the `[nlp]` extra they require.
- CONTRIBUTING's printed quality gates match CI exactly
  (byte-compile includes `api`; coverage spans
  pipeline + harness + api with the 45% floor), and its
  cost-claims rule records the one sanctioned exception:
  the whitepaper §5.6 reference-run per-stage table.
- The whitepaper's front matter renders as a table
  instead of one run-on paragraph, and §8 References is
  now purely external sources: the internal release-plan
  citation is deleted, the in-body decision-number
  citations are rewritten as the paper's own
  statements, and the product repository's artifacts
  moved to an unnumbered Artifacts note (2026-10-22).
- VERIFY's store drift spot audit now ships inside the
  package (`pipeline.drift_check`): an installed copy no
  longer dies at VERIFY looking for a `scripts/` tree the
  wheel never contained. Workspace-anchored paths that
  assumed the source tree (the registry's default root,
  the DocRED data cache, gate-eval results, a JF-F1 pack
  artefact, pack-data loading in the harness) now resolve
  against the state root / the shipped-data candidates.

### Added

- The batch pipeline: nine stages (create → fetch →
  roster → enumerate → judge → gate → entities → write →
  report) with per-stage state persisted under the batch —
  a failed run resumes where it stopped and never
  re-spends — and calibrated gate routing into Tier 1
  (verified), Tier 2 (human review band), and a recorded
  Tier 0 dropped queue.
- Three domain packs — Open Domain, Bio Medical,
  Legal Contract — over the Core baseline, each a pinned
  manifest plus payload (ontology, extraction framing,
  gate and entity-resolution defaults) and a conformance
  record of the measurements that adopted it, rejections
  included.
- The setup service (`ontosmith profile`): a corpus
  census whose claims are scanner-verified and whose
  decision points are a human's to approve; the approved
  profile compiles into the pack payload a build runs
  under.
- The ontology builder (`ontosmith ontology`): draft →
  validate → approve → emit, with fail-closed validation,
  human approval bound to the schema hash, and
  load-verified pack emission.
- The verification tooling (`ontosmith verify`): raw and
  audit-corrected precision, ceiling-adjusted recall, a
  failure-anatomy census that assigns every gold item
  exactly one fate, judge-fitting cells with
  pre-registered gates, and counted cost projections that
  price a build before it runs.
- Serving: a Neo4j projection of the Tier-1 graph
  (rebuildable from the registry, never the source of
  truth), hybrid retrieval (vector + full-text + graph),
  an HTTP API, and a demo app.
- The demo app (`app/`): a static,
  build-free page served by `ontosmith serve` at `/`
  over the sample API — search box, triple/evidence
  inspector, entity report, and a click-to-walk
  neighborhood view. Neo4j-backed graphs link out to
  Neo4j Browser for rendered exploration; no custom
  canvas.
- The project [whitepaper](docs/whitepaper.md): the
  long-form technical report and the canonical text of
  the quality records (§5), with its figures wired to
  the shipped chart assets.
- A $0 hands-on path: a small demo corpus fetched from
  its source, and a replay that recomputes the flagship
  quality record's headlines from the shipped adjudication
  appendix — no API key, no model calls.
- The quality records' evidence in-tree: the adjudication
  appendix (samples, key files, verdicts — machine
  judgments, labeled as such) and the claim-level index
  in `benchmarks/README.md`. The records' canonical text
  is the project [whitepaper](docs/whitepaper.md).
- Brand identity: the Forged Triad logo —
  mark, tile, and lockups in `assets/brand/`, with the
  1280×640 title banner heading the README, the Pages
  hero, and the PyPI page, and the tile serving as the
  demo app's and the site's favicon.

### Changed

- Pack names, pre-release (2026-10-22): the baseline pack
  `general` ("General") is now `core` ("Core"), and the
  domain pack `general-documents` ("General Documents") is
  now `open-domain` ("Open Domain"). The old names collided
  — a baseline layer and a domain pack are different kinds
  of thing and now read differently. Both packs were
  version-bumped and re-pinned per pack discipline; no
  operating content changed.
