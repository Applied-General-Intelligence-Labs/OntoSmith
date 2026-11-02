# CLI reference

Every `ontosmith` command and its options, captured
from the live `--help` surface of the unified CLI
(`pipeline/__main__.py` dispatches each command to its
module). This file is maintained verbatim from the live
`--help` surface, not generated: refresh it by hand
after any CLI change (each block below is the verbatim
output of the shown invocation, ANSI escapes stripped).

Invoke as `ontosmith <command> ...` from an install,
or `python -m pipeline <command> ...` from a clone.

## Label

### `ontosmith review` — Interactive review-band labeling (human)

`ontosmith review --help`:

```text
usage: ontosmith review [-h] --input INPUT [--out OUT] [--p-lo P_LO]
                        [--p-hi P_HI] [--limit LIMIT]

options:
  -h, --help     show this help message and exit
  --input INPUT  scored run JSON
  --out OUT
  --p-lo P_LO
  --p-hi P_HI
  --limit LIMIT
```

## Evaluate

### `ontosmith evaluate` — Run gate evaluation with bootstrap CI

`ontosmith evaluate --help`:

```text
usage: ontosmith evaluate [-h] --result RESULT [--compare COMPARE]
                          [--p-field P_FIELD] [--label-field LABEL_FIELD]
                          [--doc-field DOC_FIELD] [--seed SEED]
                          [--iters ITERS] [--stability] [--calibration]
                          [--out OUT]

options:
  -h, --help            show this help message and exit
  --result RESULT
  --compare COMPARE     second result file for paired McNemar
  --p-field P_FIELD
  --label-field LABEL_FIELD
  --doc-field DOC_FIELD
  --seed SEED
  --iters ITERS
  --stability
  --calibration
  --out OUT
```

### `ontosmith recalibrate` — Re-tune thresholds; DRIFT alert on change

`ontosmith recalibrate --help`:

```text
usage: ontosmith recalibrate [-h] --history HISTORY --deployed-threshold
                             DEPLOYED_THRESHOLD [--window WINDOW] [--out OUT]

options:
  -h, --help            show this help message and exit
  --history HISTORY
  --deployed-threshold DEPLOYED_THRESHOLD
  --window WINDOW
  --out OUT
```

### `ontosmith calibration` — Calibration / ECE report

`ontosmith calibration --help`:

```text
usage: ontosmith calibration [-h] [--out OUT]

options:
  -h, --help  show this help message and exit
  --out OUT
```

## Models

### `ontosmith llm` — Judge candidates via the judge seat's model

`ontosmith llm --help`:

```text
usage: ontosmith llm [-h] [--candidates CANDIDATES] [--out OUT]
                     [--max-candidates MAX_CANDIDATES] --model MODEL
                     [--max-tokens MAX_TOKENS]

options:
  -h, --help            show this help message and exit
  --candidates CANDIDATES
  --out OUT
  --max-candidates MAX_CANDIDATES
  --model MODEL
  --max-tokens MAX_TOKENS
                        max completion tokens (raise for reasoning models)
```

### `ontosmith ollama` — Judge candidates via local Ollama

`ontosmith ollama --help`:

```text
usage: ontosmith ollama [-h] [--model MODEL] [--candidates CANDIDATES]
                        [--out OUT] [--limit LIMIT]

options:
  -h, --help            show this help message and exit
  --model MODEL
  --candidates CANDIDATES
  --out OUT
  --limit LIMIT
```

## Gates

### `ontosmith triple` — Triple validation gate (Jev)

`ontosmith triple --help`:

```text
usage: ontosmith triple [-h] [--n-docs N_DOCS] [--seed SEED] [--model MODEL]
                        [--max-candidates MAX_CANDIDATES]
                        [--negatives {random,hard}]
                        [--gate {noul,noul_v2,choice,score}]
                        [--dump-candidates DUMP_CANDIDATES]
                        [--load-candidates LOAD_CANDIDATES] [--out OUT]

options:
  -h, --help            show this help message and exit
  --n-docs N_DOCS
  --seed SEED
  --model MODEL
  --max-candidates MAX_CANDIDATES
  --negatives {random,hard}
                        random: corrupt with a random relation (v0); hard:
                        realistic error types (v0-hard)
  --gate {noul,noul_v2,choice,score}
                        noul: binary 'does R hold?' gate; noul_v2: noul with
                        direction emphasis; choice: 'which relation holds?'
                        gate; score: 5-point support strength gate
  --dump-candidates DUMP_CANDIDATES
                        write the candidate list to this JSON file and exit
  --load-candidates LOAD_CANDIDATES
                        load candidates from a JSON file instead of building
  --out OUT
```

### `ontosmith er` — Entity resolution gate (Jev)

`ontosmith er --help`:

```text
usage: ontosmith er [-h] [--candidates CANDIDATES] [--out OUT]
                    [--max-candidates MAX_CANDIDATES] [--model MODEL]

options:
  -h, --help            show this help message and exit
  --candidates CANDIDATES
  --out OUT
  --max-candidates MAX_CANDIDATES
  --model MODEL
```

### `ontosmith conflict` — Conflict detection gate (Jev)

`ontosmith conflict --help`:

```text
usage: ontosmith conflict [-h] [--candidates CANDIDATES] [--out OUT]
                          [--max-candidates MAX_CANDIDATES] [--model MODEL]

options:
  -h, --help            show this help message and exit
  --candidates CANDIDATES
  --out OUT
  --max-candidates MAX_CANDIDATES
  --model MODEL
```

### `ontosmith onto` — Ontology gate (Jev)

`ontosmith onto --help`:

```text
usage: ontosmith onto [-h] [--candidates CANDIDATES] [--out OUT]
                      [--max-candidates MAX_CANDIDATES] [--model MODEL]

options:
  -h, --help            show this help message and exit
  --candidates CANDIDATES
  --out OUT
  --max-candidates MAX_CANDIDATES
  --model MODEL
```

### `ontosmith cascade` — Choice-cascade dense graph builder

`ontosmith cascade --help`:

```text
usage: ontosmith cascade [-h] [--n-docs N_DOCS] [--seed SEED]
                         [--workers WORKERS] [--threshold THRESHOLD]
                         [--out OUT] [--smoke SMOKE]

options:
  -h, --help            show this help message and exit
  --n-docs N_DOCS
  --seed SEED
  --workers WORKERS
  --threshold THRESHOLD
  --out OUT
  --smoke SMOKE         only run first N pairs (mechanics check)
```

## Pipeline

### `ontosmith configure` — Declare provider bindings for the seats (interactive)

`ontosmith configure --help`:

```text
usage: ontosmith configure [-h] [--out OUT] [--from-file PATH]
                           [--emit-template] [--no-probe]

Declare the provider bindings for OntoSmith's model seats and write them to an
ontosmith-bindings.env file (keys by variable name only).

options:
  -h, --help        show this help message and exit
  --out OUT         output file (default: ontosmith-bindings.env; with --from-
                    file, the input file itself)
  --from-file PATH  validate a filled bindings file (e.g. a copy of
                    docs/bindings-template.env) instead of prompting
  --emit-template   print the shipped bindings template and exit
  --no-probe        skip the zero-spend probes (the file is stamped 'not
                    probed')
```

### `ontosmith identity-freeze` — Freeze per-point identity-seat decisions into a global decisions file

`ontosmith identity-freeze --help`:

```text
usage: ontosmith identity-freeze [-h] points freeze

Freeze per-point identity-seat decisions into a global decisions file
(unanimous pairs freeze; disagreeing pairs become ambiguous records).

positional arguments:
  points      per-point decisions JSONL ({doc, ep, cand, decision})
  freeze      output global freeze JSONL

options:
  -h, --help  show this help message and exit
```

The freeze is the corpus-level form of the identity
seat's frozen decisions: a profile declares the output
file as `identity_seat_decisions_global`, and pairs the
freeze promoted to ambiguous defer to the rule chain
and surface in the batch's `identity_ambiguous.jsonl`
([connections.md](connections.md#the-identity-seat)).

### `ontosmith batch` — Batch pipeline: create/run/status/rollback + ER settlement

`ontosmith batch --help`:

```text
usage: ontosmith batch [-h]
                       {create,run,status,rollback,er-review,er-decide,er-apply,list}
                       ...

positional arguments:
  {create,run,status,rollback,er-review,er-decide,er-apply,list}
    create              Create a batch from documents
    run                 Run (or resume) a batch
    status              Show batch status
    rollback            Delete a run's outputs (manual)
    er-review           List a batch's held ER review-band pairs, with the
                        evidence to settle them
    er-decide           Record a human settlement of one held ER pair (a merge
                        folds the pair in the registry at once)
    er-apply            Apply recorded ER merges to the graph: amend the WRITE
                        manifest and re-project
    list                List batches

options:
  -h, --help            show this help message and exit
```

#### `ontosmith batch create`

`ontosmith batch create --help`:

```text
usage: ontosmith batch create [-h] --source SOURCE --collection COLLECTION
                              --docs DOCS [DOCS ...] [--batch-id BATCH_ID]
                              [--profile PROFILE] [--pack PACK]
                              [--max-cost-usd MAX_COST_USD] [--no-cost-cap]
                              [--frontier-escalation]
                              [--escalation-band-lo ESCALATION_BAND_LO]
                              [--escalation-band-hi ESCALATION_BAND_HI]

options:
  -h, --help            show this help message and exit
  --source SOURCE
  --collection COLLECTION
  --docs DOCS [DOCS ...]
                        glob patterns for input documents
  --batch-id BATCH_ID
  --profile PROFILE     path to a stage-0 profile.json (setup service); its
                        profile_hash is recorded on the batch and into the
                        manifest at INGEST
  --pack PACK           domain pack binding this batch (pack_id, family, or
                        pack directory); the executed setup_config is resolved
                        as core baseline + this pack and frozen into the batch
                        directory (default: the pack bound to the --profile
                        family, if any)
  --max-cost-usd MAX_COST_USD
                        stop-line: hard spend cap for the batch (default: the
                        BatchConfig default)
  --no-cost-cap         run with no USD spend cap (the explicit way to run on
                        a cost-less binding; the ledger then records tokens
                        and marks cost not-provided)
  --frontier-escalation
                        enable frontier escalation for non-judging seats
                        (default OFF; the judging seat never escalates)
  --escalation-band-lo ESCALATION_BAND_LO
                        shaky-band lower bound (with --frontier-escalation)
  --escalation-band-hi ESCALATION_BAND_HI
                        shaky-band upper bound (with --frontier-escalation)
```

> **Recorded, not wired (v1):** `--frontier-escalation`
> and its band bounds are written into the batch config
> at creation, but no v1 pipeline stage consumes them.
> Review-band items route to the Tier-2 review queue;
> frontier adjudication and promotion live in the
> verification harness, not the product (whitepaper
> §3.2). Setting the flag changes nothing in a v1 run —
> it is documented here so the config record is never
> mistaken for a working switch.

> **Create is fresh-only.** A batch id names one build.
> Creating over an id that already has a batch refuses
> by name — the existing batch's status and run are
> printed, and nothing is written. (The alternative it
> replaces was worse than either clean option: the new
> `batch.json` landed over the old run's untouched
> `state.json` and stage artifacts, and `batch status`
> read the mix as one incoherent record.) To iterate —
> after re-emitting a pack with a changed declaration,
> say — create the new build under a new `--batch-id`;
> to continue the existing build, `batch run` resumes
> it. A changed thing is a new identity, never an
> overwrite — the same rule pack emission follows.

#### `ontosmith batch run`

`ontosmith batch run --help`:

```text
usage: ontosmith batch run [-h] [--reuse-run] batch_id

positional arguments:
  batch_id

options:
  -h, --help   show this help message and exit
  --reuse-run  keep run_id on retry (default: new run_id)
```

> **Before the first stage:** the run preflights its
> bindings (a pending stage's seat with an unset key
> variable refuses by name), its cost cap, and — when a
> graph stage (WRITE / INDEX / VERIFY) is still pending
> — the **Neo4j store**: unreachable, or rejecting the
> configured credentials, refuses with a named error
> before anything runs or spends
> ([connections.md](connections.md#neo4j--the-graph-store-required-for-builds)).

> **Reading the WRITE metrics.** The batch record's
> `metrics.write` carries the write stage's counts. Two
> fields name things defined nowhere else:
>
> - **`relations_unmapped`** — the number of written
>   triples whose surface relation matched nothing at
>   WRITE: not the profile's `label_categories`
>   vocabulary, not its explicit `relation_map`, not
>   the WRITE view of its pack's relation declarations
>   (when the profile declares
>   `relation_declarations_source`: the declared
>   canonicalizations — a flipped entry renames the
>   relation and swaps the endpoints — and the declared
>   vocabulary, each surface its own canonical form),
>   and unchanged by the deterministic canonicalization
>   (case/whitespace folding, auxiliary stripping,
>   `_`-joining). An unmapped relation is not dropped:
>   it is stored **unchanged** and counted here. The
>   per-surface breakdown is the batch directory's
>   `relation_map.json` (`unmapped_surfaces`) — the
>   report the map grows from. **`relation_layers`**
>   beside the count gives the written relations per
>   normalization layer: `vocab` / `map` / `normalized`
>   / `surface`.
> - **`er_review_pairs`** — entity-resolution pairs the
>   WRITE stage held in the review band instead of
>   merging: cross-document pairs the ER gate scored
>   below the merge threshold, second-opinion flags,
>   and pairs the pack's `merge_prohibited_type_pairs`
>   forbids. Each held pair is **recorded** — in the
>   registry's `er_decisions` table with
>   `outcome='review'` (the pair, the gate's `p_same`,
>   the model, and the reason where one applies), in
>   the run log as it is held, and in this count — and
>   both entities stay in the graph as separate nodes.
>   A held pair is settled by a human through
>   `batch er-review` (the pairs, with their evidence)
>   → `batch er-decide` (a recorded merge /
>   keep-separate decision) → `batch er-apply` (the
>   merges re-projected into the graph) — see the three
>   commands below. The decision is append-only: the
>   hold stays on record and the settlement lands
>   beside it in the same table.

#### `ontosmith batch status`

`ontosmith batch status --help`:

```text
usage: ontosmith batch status [-h] batch_id

positional arguments:
  batch_id

options:
  -h, --help  show this help message and exit
```

#### `ontosmith batch rollback`

`ontosmith batch rollback --help`:

```text
usage: ontosmith batch rollback [-h] --run RUN [--yes] [--reason REASON]
                                batch_id

positional arguments:
  batch_id

options:
  -h, --help       show this help message and exit
  --run RUN
  --yes
  --reason REASON
```

#### `ontosmith batch er-review`

`ontosmith batch er-review --help`:

```text
usage: ontosmith batch er-review [-h] [--all] batch_id

positional arguments:
  batch_id

options:
  -h, --help  show this help message and exit
  --all       include pairs already settled
```

> Lists the batch's held ER pairs (the
> `er_review_pairs` above) with what a person needs to
> settle one: both sides as registry entities (ids,
> labels), the gate's `p_same` and model, the recorded
> reason where one applies, each side's triple count in
> the batch's graph, and sample contexts from the
> routed candidates. Settled pairs are hidden unless
> `--all`; a settled pair prints its outcome, who
> decided, and when.
>
> Pair ids are `er_decisions` row ids — one
> registry-wide sequence shared with the machine
> decision rows (WRITE-time merges, splits, detaches)
> and with every other batch's rows. **Ids therefore
> skip**: a missing number is a decision row that is
> not a held pair of this batch, not a pair that was
> lost.

#### `ontosmith batch er-decide`

`ontosmith batch er-decide --help`:

```text
usage: ontosmith batch er-decide [-h] --pair PAIR (--merge | --keep-separate)
                                 [--by BY] [--reason REASON]
                                 batch_id

positional arguments:
  batch_id

options:
  -h, --help       show this help message and exit
  --pair PAIR      held pair id (from er-review)
  --merge          fold the pair into one entity
  --keep-separate  record that the pair stays two entities; later runs never
                   re-hold it
  --by BY          who is deciding (recorded on the decision row; default
                   'human')
  --reason REASON  why (appended to the decision record)
```

> Records the human settlement of one held pair, as an
> append-only `er_decisions` row beside the hold —
> who (`--by`), when, the gate's `p_same`, and the
> reason. Deciding an unknown pair, or one already
> settled, refuses by name; a settlement is a record,
> not an edit.
>
> **`--merge`** also applies the fold to the registry
> (the source of truth) at once: the mention-side
> entity folds into the record's surviving entity —
> the canonical name follows the registry's
> survivor-picking convention, every surface of both
> entities is preserved as an alias of the survivor,
> and the fold is recorded in the registry's
> `id_mapping`. The graph still shows both nodes
> until `er-apply` re-projects it. **`--keep-separate`**
> records the decision only; later runs consult it and
> never re-hold the pair (the ER gate is not
> re-consulted for it either).

#### `ontosmith batch er-apply`

`ontosmith batch er-apply --help`:

```text
usage: ontosmith batch er-apply [-h] batch_id

positional arguments:
  batch_id

options:
  -h, --help  show this help message and exit
```

> Applies the batch's recorded ER merges to its
> graph, in one command: the WRITE manifest
> (`written_triples.jsonl`) is amended through the
> registry's `id_mapping` (the manifest remains the
> exact set of canonical triples the graph must hold,
> now as amended by the recorded settlements — the
> settlements themselves are the audit trail, in
> `er_decisions`), the batch's projection is rebuilt
> with the existing projection machinery, and a
> merged-away entity node is removed once no REL edge
> touches it. A re-pointed edge keeps its `p_true` —
> the score is part of the attestation's evidence
> chain. The store is preflighted before anything is
> amended: unreachable, nothing is touched and the
> command can simply be rerun. With no recorded merge
> touching the batch's graph, there is nothing to
> apply. The projection asserts this batch's graph as
> the store's one graph (see `search` below): if the
> store currently holds a different build's graph, the
> apply replaces it and the output names the graph it
> replaced.

#### `ontosmith batch list`

`ontosmith batch list --help`:

```text
usage: ontosmith batch list [-h]

options:
  -h, --help  show this help message and exit
```

### `ontosmith profile` — Corpus profiler: stage-0 profile of a corpus (deterministic, no LLM)

`ontosmith profile --help`:

```text
usage: ontosmith profile [-h] --family FAMILY [--out OUT]
                         [--sample-n SAMPLE_N] [--json]
                         corpus_dir

Corpus profiler: stage-0 profile of a corpus directory —
deterministic, no LLM calls, no spend

positional arguments:
  corpus_dir           directory of .txt documents (read recursively)

options:
  -h, --help           show this help message and exit
  --family FAMILY      corpus family name (names the profile and its
                       setup_config)
  --out OUT            artefact directory (default: profiles/<family> under
                       the current directory)
  --sample-n SAMPLE_N  dispersed sample size for sample-graded claims (default
                       12)
  --json               print the machine summary as JSON instead of the report
                       card
```

### `ontosmith ontology` — Ontology builder: draft/import/validate/approve/emit a pack; check validates a pack directory

`ontosmith ontology --help`:

```text
usage: ontosmith ontology [-h]
                          {draft,import,validate,brief,approve,emit,check} ...

Ontology builder: draft -> validate -> approve -> emit a pack

positional arguments:
  {draft,import,validate,brief,approve,emit,check}
    draft               Draft a schema from a corpus profile + brief
    import              Import an external ontology as a draft (BYO schema):
                        inventory + scoping spec -> draft + scoping record
    validate            Validate a schema draft
    brief               Render the approval brief
    approve             Record human approval of a draft
    emit                Emit an approved draft as a pack
    check               Validate a pack directory against the pack schemas +
                        loader (contributor pre-submission check)

options:
  -h, --help            show this help message and exit
```

#### `ontosmith ontology draft`

`ontosmith ontology draft --help`:

```text
usage: ontosmith ontology draft [-h] --profile PROFILE [--brief BRIEF]
                                [--family FAMILY] --pack-id PACK_ID
                                [--display-name DISPLAY_NAME] --out OUT
                                [--llm] [--model MODEL]

options:
  -h, --help            show this help message and exit
  --profile PROFILE     stage-0 profile.json (setup_service)
  --brief BRIEF         domain brief: a text file, or a JSON file with
                        text/purpose/entity_types/relation_types (the type
                        fields are lists of type-name strings)
  --family FAMILY       corpus family (default: the profile's)
  --pack-id PACK_ID
  --display-name DISPLAY_NAME
  --out OUT             draft JSON path
  --llm                 bind the drafting tier to a chat model (default:
                        deterministic brief-seeded skeleton, no spend)
  --model MODEL         drafting-tier binding (connection layer); default: the
                        drafting seat's bound model (ONTOSMITH_DRAFTING_MODEL)
                        — the seat ships unbound
```

#### `ontosmith ontology import`

`ontosmith ontology import --help`:

```text
usage: ontosmith ontology import [-h] --spec SPEC --inventory INVENTORY --out
                                 OUT [--scoping-out SCOPING_OUT]

options:
  -h, --help            show this help message and exit
  --spec SPEC           scoping spec JSON (the human judgment: per-type
                        sources, actions, boundary material, drops with
                        reasons)
  --inventory INVENTORY
                        source-ontology inventory JSON (repeatable: module
                        file + reference files)
  --out OUT             draft JSON path
  --scoping-out SCOPING_OUT
                        scoping record path (default: beside the draft,
                        .scoping.json)
```

The BYO-schema door (hands-on Track 4): consumes a
mechanically extracted inventory of an external
OWL/RDF ontology plus a human-authored scoping spec,
and emits an ordinary builder draft plus the scoping
record — the complete per-concept account (kept /
merged / flattened / role-converted / demoted /
dropped with reasons). The draft then passes the
same validate → approve → emit gates as a drafted
schema; the import is fail-closed (every concept in
the spec's scoped files must be accounted for exactly
once).

#### `ontosmith ontology validate`

`ontosmith ontology validate --help`:

```text
usage: ontosmith ontology validate [-h] draft

positional arguments:
  draft

options:
  -h, --help  show this help message and exit
```

#### `ontosmith ontology brief`

`ontosmith ontology brief --help`:

```text
usage: ontosmith ontology brief [-h] draft

positional arguments:
  draft

options:
  -h, --help  show this help message and exit
```

#### `ontosmith ontology approve`

`ontosmith ontology approve --help`:

```text
usage: ontosmith ontology approve [-h] --approver APPROVER [--outcome OUTCOME]
                                  [--issue ISSUE] [--out OUT]
                                  draft

positional arguments:
  draft

options:
  -h, --help           show this help message and exit
  --approver APPROVER
  --outcome OUTCOME    outcome text (must approve; default: 'approved
                       (<approver>, <date>)')
  --issue ISSUE
  --out OUT            approval record path (default: approval_<date>.json
                       beside the draft)
```

#### `ontosmith ontology emit`

`ontosmith ontology emit --help`:

```text
usage: ontosmith ontology emit [-h] --approval APPROVAL [--out-root OUT_ROOT]
                               [--pack-version PACK_VERSION]
                               draft

positional arguments:
  draft

options:
  -h, --help            show this help message and exit
  --approval APPROVAL
  --out-root OUT_ROOT   tree root the pack lands under (default: this repo's
                        root)
  --pack-version PACK_VERSION
```

#### `ontosmith ontology check`

`ontosmith ontology check --help`:

```text
usage: ontosmith ontology check [-h] pack_dir

positional arguments:
  pack_dir    the pack directory (packs/<pack_id>)

options:
  -h, --help  show this help message and exit
```

### `ontosmith inspect` — Evidence/provenance viewer: one triple, its evidence + provenance

`ontosmith inspect --help`:

```text
usage: ontosmith inspect [-h] [--index INDEX] [--subject SUBJECT]
                         [--relation RELATION] [--object OBJECT] [--json]
                         artefact

Evidence/provenance viewer: print one triple with its evidence
spans and provenance chain

positional arguments:
  artefact             triple-records JSONL/.json file, or a batch directory

options:
  -h, --help           show this help message and exit
  --index INDEX        index among matching triples (default: first match)
  --subject SUBJECT    subject substring filter
  --relation RELATION  relation substring filter
  --object OBJECT      object substring filter
  --json               print the inspection report as JSON instead of the
                       report card
```

Point `inspect` at a **batch directory** for the full
view — resolved names, quoted evidence, provenance.
Pointed at a bare records file it degrades (`ent:` IDs,
"(none recorded)" evidence, a Gaps section): a file alone
carries no registry context. One Gaps line — `no
batch/status provenance recorded` — can appear even in
the directory form; it reports that the triple record
itself carries no batch/status fields (batch provenance
lives in the batch manifest and status), not that
evidence is missing.

## Serve

### `ontosmith search` — Hybrid search over a built graph (chunks + triples)

`ontosmith search --help`:

```text
usage: ontosmith search [-h] [--source SOURCE] [--batch BATCH]
                        [--store {auto,file,neo4j}] [--k K] [--embed] [--json]
                        query

Hybrid search over a built graph: lexical + vector + graph
retrieval over the build's chunks and triples

positional arguments:
  query                 the search question

options:
  -h, --help            show this help message and exit
  --source SOURCE       batch directory, records directory, or records file
                        (default: the shipped example build)
  --batch BATCH         batch id under the state root's batches/ (alternative
                        to --source)
  --store {auto,file,neo4j}
                        serving store: auto = Neo4j when NEO4J_URL is
                        configured, else the file store (default: auto)
  --k K                 max documents / triples returned (default: 10)
  --embed               activate the vector arm via the connection layer's
                        default embedder (refuses if the embedder seat's key
                        is unset)
  --json                print the result as JSON instead of the report card
```

> **Which graph a Neo4j search answers from.** The
> Neo4j store holds exactly one graph — the current
> build's, stamped at WRITE with its ontology identity
> ([connections.md](connections.md)) — and a Neo4j
> search answers from that graph; there is no scope
> option because there is nothing to scope between.
> `--batch` / `--source` beside `--store neo4j` (or an
> auto-selected Neo4j) names the build you *expect*:
> if the store's current graph is a different build's,
> the search refuses by name (build the named one to
> make its graph the store's, or drop the name). A
> store with no stamped graph refuses the same way,
> saying whether it is empty or holds unstamped
> content. With `--store file`, `--batch` / `--source`
> select the artifact read-back instead, exactly as
> before. `serve` follows the same rules.

### `ontosmith serve` — Sample API server: search / entity / neighborhood / triple endpoints

`ontosmith serve --help`:

```text
usage: ontosmith serve [-h] [--source SOURCE] [--batch BATCH]
                       [--store {auto,file,neo4j}] [--host HOST] [--port PORT]
                       [--embed]

Sample API server over the search/graph surface: /search,
/entity, /neighborhood, /triple (with evidence)

options:
  -h, --help            show this help message and exit
  --source SOURCE       batch directory, records directory, or records file
                        (default: the shipped example build)
  --batch BATCH         batch id under the state root's batches/ (alternative
                        to --source)
  --store {auto,file,neo4j}
                        serving store: auto = Neo4j when NEO4J_URL is
                        configured, else the file store (default: auto)
  --host HOST           bind host (default: 127.0.0.1)
  --port PORT           bind port (default: 8765)
  --embed               activate the search vector arm via the connection
                        layer's default embedder (refuses if the embedder
                        seat's key is unset)
```

### `ontosmith verify` — Verification cores: score/audit-sets/project/report

`ontosmith verify --help`:

```text
usage: ontosmith verify [-h] {score,audit-sets,project,report} ...

Command-line surface for the verification cores.

positional arguments:
  {score,audit-sets,project,report}
    score               raw exact-match scores
    audit-sets          build the A/B/C judgment sets
    project             counted cost projection vs a stop-line
    report              full evaluation report

options:
  -h, --help            show this help message and exit
```

#### `ontosmith verify score`

`ontosmith verify score --help`:

```text
usage: ontosmith verify score [-h] --predicted PREDICTED --gold GOLD
                              [--normalization NORMALIZATION] [--out OUT]

options:
  -h, --help            show this help message and exit
  --predicted PREDICTED
  --gold GOLD
  --normalization NORMALIZATION
  --out OUT
```

#### `ontosmith verify audit-sets`

`ontosmith verify audit-sets --help`:

```text
usage: ontosmith verify audit-sets [-h] --predicted PREDICTED --gold GOLD
                                   [--normalization NORMALIZATION] --out-dir
                                   OUT_DIR [--seed SEED] [--a-n A_N]
                                   [--b-n B_N] [--c-n C_N]

options:
  -h, --help            show this help message and exit
  --predicted PREDICTED
  --gold GOLD
  --normalization NORMALIZATION
  --out-dir OUT_DIR
  --seed SEED
  --a-n A_N
  --b-n B_N
  --c-n C_N
```

#### `ontosmith verify project`

`ontosmith verify project --help`:

```text
usage: ontosmith verify project [-h] --counts COUNTS [--scenario SCENARIO]
                                --unit-cost UNIT_COST --cap CAP
                                [--stop-line STOP_LINE]

options:
  -h, --help            show this help message and exit
  --counts COUNTS       unit=count,... for the base scenario
  --scenario SCENARIO   name:unit=count,... (extra scenario)
  --unit-cost UNIT_COST
                        unit=usd,... caller-supplied prices
  --cap CAP
  --stop-line STOP_LINE
```

#### `ontosmith verify report`

`ontosmith verify report --help`:

```text
usage: ontosmith verify report [-h] --predicted PREDICTED --gold GOLD
                               [--normalization NORMALIZATION] [--out OUT]
                               [--title TITLE]

options:
  -h, --help            show this help message and exit
  --predicted PREDICTED
  --gold GOLD
  --normalization NORMALIZATION
  --out OUT
  --title TITLE
```
