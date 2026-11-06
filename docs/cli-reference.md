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

The lab's measurement commands (review, evaluate, the
gate runners, and friends) are not part of this
surface; they run from their module entries, e.g.
`python -m pipeline.gate_cli triple`.

## Core

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

### `ontosmith profile` — Corpus profiler: stage-0 profile of a corpus (deterministic, no LLM)

`ontosmith profile --help`:

```text
usage: ontosmith profile [-h] --family FAMILY [--out OUT] [--json] corpus_dir

Corpus profiler: stage-0 profile of a corpus directory — deterministic, no LLM
calls, no spend

positional arguments:
  corpus_dir       directory of .txt documents (read recursively)

options:
  -h, --help       show this help message and exit
  --family FAMILY  corpus family name (names the profile and its setup_config)
  --out OUT        artefact directory (default: profiles/<family> under the
                   current directory)
  --json           print the machine summary as JSON instead of the report
                   card
```

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
```

#### `ontosmith batch run`

`ontosmith batch run --help`:

```text
usage: ontosmith batch run [-h] batch_id

positional arguments:
  batch_id

options:
  -h, --help  show this help message and exit
```

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

#### `ontosmith batch er-apply`

`ontosmith batch er-apply --help`:

```text
usage: ontosmith batch er-apply [-h] batch_id

positional arguments:
  batch_id

options:
  -h, --help  show this help message and exit
```

#### `ontosmith batch list`

`ontosmith batch list --help`:

```text
usage: ontosmith batch list [-h]

options:
  -h, --help  show this help message and exit
```

### `ontosmith inspect` — Evidence/provenance viewer: one triple, its evidence + provenance

`ontosmith inspect --help`:

```text
usage: ontosmith inspect [-h] [--index INDEX] [--subject SUBJECT]
                         [--relation RELATION] [--object OBJECT] [--json]
                         artefact

Evidence/provenance viewer: print one triple with its evidence spans and
provenance chain

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

### `ontosmith verify` — Verification cores: score/audit-sets/project/report

`ontosmith verify --help`:

```text
usage: ontosmith verify [-h] {score,audit-sets,project,report} ...

Command-line surface for the verification cores.

positional arguments:
  {score,audit-sets,project,report}
    score               raw exact-match scores
    audit-sets          build the A/B/C judgment sets
    project             counted workload projection
    report              full evaluation report

options:
  -h, --help            show this help message and exit
```

#### `ontosmith verify score`

`ontosmith verify score --help`:

```text
usage: ontosmith verify score [-h] --predicted PREDICTED --gold GOLD
                              [--out OUT]

options:
  -h, --help            show this help message and exit
  --predicted PREDICTED
  --gold GOLD
  --out OUT
```

#### `ontosmith verify audit-sets`

`ontosmith verify audit-sets --help`:

```text
usage: ontosmith verify audit-sets [-h] --predicted PREDICTED --gold GOLD
                                   --out-dir OUT_DIR

options:
  -h, --help            show this help message and exit
  --predicted PREDICTED
  --gold GOLD
  --out-dir OUT_DIR
```

#### `ontosmith verify project`

`ontosmith verify project --help`:

```text
usage: ontosmith verify project [-h] --counts COUNTS [--scenario SCENARIO]

options:
  -h, --help           show this help message and exit
  --counts COUNTS      unit=count,... for the base scenario
  --scenario SCENARIO  name:unit=count,... (extra scenario)
```

#### `ontosmith verify report`

`ontosmith verify report --help`:

```text
usage: ontosmith verify report [-h] --predicted PREDICTED --gold GOLD
                               [--out OUT]

options:
  -h, --help            show this help message and exit
  --predicted PREDICTED
  --gold GOLD
  --out OUT
```

## Authoring

### `ontosmith ontology` — Ontology builder: draft/validate/approve/emit a pack; check validates a pack directory

`ontosmith ontology --help`:

```text
usage: ontosmith ontology [-h]
                          {draft,import,validate,brief,approve,emit,check,diff}
                          ...

Ontology builder: draft -> validate -> approve -> emit a pack

positional arguments:
  {draft,import,validate,brief,approve,emit,check,diff}
    draft               Draft a schema from a corpus profile + brief (fresh by
                        default; --from PACK extends an existing pack; a
                        material guide warns under 30 documents of material,
                        strongly under 10)
    import              Import an external ontology as a draft (BYO schema):
                        inventory + scoping spec -> draft + scoping record
    validate            Validate a schema draft
    brief               Render the approval brief
    approve             Record human approval of a draft
    emit                Emit an approved draft as a pack
    check               Validate a pack directory against the pack schemas +
                        loader (contributor pre-submission check)
    diff                Report the structural difference between two schemas
                        (drafts or pack payloads) — the anchoring check; a
                        report, never a merge

options:
  -h, --help            show this help message and exit
```

#### `ontosmith ontology draft`

`ontosmith ontology draft --help`:

```text
usage: ontosmith ontology draft [-h] --profile PROFILE [--brief BRIEF]
                                [--family FAMILY] --pack-id PACK_ID
                                [--display-name DISPLAY_NAME] --out OUT
                                [--llm] [--model MODEL] [--from PACK]
                                [--holes HOLES]

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
  --from PACK           extension mode: extend the named existing pack — a
                        pack id resolved under this repo's packs/ tree, or a
                        path to a schema draft / pack payload JSON — revising
                        its schema against this profile's documents (the new
                        material) under the inheritance rules (removals and
                        shrinks then need recorded reasons). Without --from
                        the draft composes fresh, even when a published pack
                        carries the same --pack-id
  --holes HOLES         hole report JSON (a list of family records, or an
                        object with a 'families' list) the revision must
                        address
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

#### `ontosmith identity-freeze doc`

`ontosmith identity-freeze doc --help`:

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

#### `ontosmith identity-freeze ep`

`ontosmith identity-freeze ep --help`:

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

#### `ontosmith identity-freeze cand`

`ontosmith identity-freeze cand --help`:

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

#### `ontosmith identity-freeze decision`

`ontosmith identity-freeze decision --help`:

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

## Serve

### `ontosmith search` — Hybrid search over a built graph (chunks + triples)

`ontosmith search --help`:

```text
usage: ontosmith search [-h] [--source SOURCE] [--batch BATCH]
                        [--store {auto,file,neo4j}] [--k K] [--embed] [--json]
                        query

Hybrid search over a built graph : lexical + vector + graph retrieval over the
build's chunks and triples

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

#### `ontosmith search auto`

`ontosmith search auto --help`:

```text
usage: ontosmith search [-h] [--source SOURCE] [--batch BATCH]
                        [--store {auto,file,neo4j}] [--k K] [--embed] [--json]
                        query

Hybrid search over a built graph : lexical + vector + graph retrieval over the
build's chunks and triples

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

#### `ontosmith search file`

`ontosmith search file --help`:

```text
usage: ontosmith search [-h] [--source SOURCE] [--batch BATCH]
                        [--store {auto,file,neo4j}] [--k K] [--embed] [--json]
                        query

Hybrid search over a built graph : lexical + vector + graph retrieval over the
build's chunks and triples

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

#### `ontosmith search neo4j`

`ontosmith search neo4j --help`:

```text
usage: ontosmith search [-h] [--source SOURCE] [--batch BATCH]
                        [--store {auto,file,neo4j}] [--k K] [--embed] [--json]
                        query

Hybrid search over a built graph : lexical + vector + graph retrieval over the
build's chunks and triples

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

### `ontosmith serve` — Sample API server: search / entity / neighborhood / triple endpoints

`ontosmith serve --help`:

```text
usage: ontosmith serve [-h] [--source SOURCE] [--batch BATCH]
                       [--store {auto,file,neo4j}] [--host HOST] [--port PORT]
                       [--library DIR] [--pack PACK] [--embed]

Sample API server over the search/graph surface: /search, /entity,
/neighborhood, /triple (with evidence)

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
  --library DIR         manage a folder of .txt documents from the Documents
                        page at /documents and rebuild the graph from it
                        (localhost only; each rebuild spends, under a cap)
  --pack PACK           the pack library rebuilds use (default: open-domain)
  --embed               activate the search vector arm via the connection
                        layer's default embedder (refuses if the embedder
                        seat's key is unset)
```

#### `ontosmith serve auto`

`ontosmith serve auto --help`:

```text
usage: ontosmith serve [-h] [--source SOURCE] [--batch BATCH]
                       [--store {auto,file,neo4j}] [--host HOST] [--port PORT]
                       [--library DIR] [--pack PACK] [--embed]

Sample API server over the search/graph surface: /search, /entity,
/neighborhood, /triple (with evidence)

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
  --library DIR         manage a folder of .txt documents from the Documents
                        page at /documents and rebuild the graph from it
                        (localhost only; each rebuild spends, under a cap)
  --pack PACK           the pack library rebuilds use (default: open-domain)
  --embed               activate the search vector arm via the connection
                        layer's default embedder (refuses if the embedder
                        seat's key is unset)
```

#### `ontosmith serve file`

`ontosmith serve file --help`:

```text
usage: ontosmith serve [-h] [--source SOURCE] [--batch BATCH]
                       [--store {auto,file,neo4j}] [--host HOST] [--port PORT]
                       [--library DIR] [--pack PACK] [--embed]

Sample API server over the search/graph surface: /search, /entity,
/neighborhood, /triple (with evidence)

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
  --library DIR         manage a folder of .txt documents from the Documents
                        page at /documents and rebuild the graph from it
                        (localhost only; each rebuild spends, under a cap)
  --pack PACK           the pack library rebuilds use (default: open-domain)
  --embed               activate the search vector arm via the connection
                        layer's default embedder (refuses if the embedder
                        seat's key is unset)
```

#### `ontosmith serve neo4j`

`ontosmith serve neo4j --help`:

```text
usage: ontosmith serve [-h] [--source SOURCE] [--batch BATCH]
                       [--store {auto,file,neo4j}] [--host HOST] [--port PORT]
                       [--library DIR] [--pack PACK] [--embed]

Sample API server over the search/graph surface: /search, /entity,
/neighborhood, /triple (with evidence)

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
  --library DIR         manage a folder of .txt documents from the Documents
                        page at /documents and rebuild the graph from it
                        (localhost only; each rebuild spends, under a cap)
  --pack PACK           the pack library rebuilds use (default: open-domain)
  --embed               activate the search vector arm via the connection
                        layer's default embedder (refuses if the embedder
                        seat's key is unset)
```

### `ontosmith quickstart` — First run: build a graph from one text file and serve it

`ontosmith quickstart --help`:

```text
usage: ontosmith quickstart [-h] [--batch-id BATCH_ID] [--port PORT]
                            [--no-serve]
                            file

Build a knowledge graph from one text document and serve it: batch create +
run (open-domain pack), then serve.

positional arguments:
  file                 the text document to build from

options:
  -h, --help           show this help message and exit
  --batch-id BATCH_ID  batch id (default: quickstart-<file stem>)
  --port PORT          serve port (default: 8765)
  --no-serve           build only; print the serve command instead of starting
                       it
```
