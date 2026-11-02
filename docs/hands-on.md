# Hands-on — four tracks over your own documents

*I have documents — what do I run to search them?*
Four tracks, all over **your** documents, each
changing one thing:

| Track | What changes |
|---|---|
| **1 · Your documents** | The quickstart: install → connect → profile → build → search |
| **2 · + an ontology domain pack** | The same documents, with a domain pack bound — what the pack changes is the lesson |
| **3 · → your own ontology** | No shipped pack fits: draft one with the ontology builder, approve it, build under it |
| **4 · + an ontology you already have** | Import a public ontology (worked example: FIBO over the CUAD contracts) through the same builder gates |

**Start at Track 1.** Every track ends at the same
finish line — the build served, one question searched,
one fact opened to its evidence with `inspect` — so
stop after any track and you hold a working graph;
continue and you change exactly one variable.

Worked examples run on two small corpora whose
outputs were captured from the real tools: the
shipped **Alder & Finch** example (`examples/corpus`,
four short documents) and the fetched **Re-DocRED**
subset of the appendix track. Substitute your own
folder of documents anywhere a corpus path appears.

## Prerequisites

- **Linux** — developed and tested on Linux. Windows
  users: run under **WSL2** (native Windows is not a
  tested platform).
- **Python ≥ 3.11**.
- An **LLM connection** — only for the steps that
  spend (each track marks them **LIVE**). Profiling,
  batch creation, validation, and inspection all run
  with no key at all; connect when you are ready to
  build.
- **Neo4j** (5.x) running — for the **LIVE** builds
  only: it is the graph store the build writes into.
  Any running instance works — Docker is not
  required; installation follows Neo4j's own guide,
  linked at the point of first need (Track 1 §3,
  "the store") and in
  [connections.md](connections.md) (Neo4j — the graph
  store). With a local install, exporting
  `NEO4J_PASSWORD` is the whole configuration. A run
  checks the store before it spends and refuses by
  name if it is not serving; the keyless steps never
  touch it.

**There is exactly one candidate path.** A build
gets its candidate triples from the proposer seat
(a model call) — there is no candidate-source
setting to choose. Every channel (free triples,
clause slots, and the profile-declared
pair-enumeration, sweep and mention passes)
proposes through the one guard funnel in
`pipeline/proposer.py`. **Install nothing extra:**
no local NER/RER models are involved. (A local
GLiNER+spaCy pipeline source existed until
2026-10-29 and was retired — unmeasured and
English-bound; the NER stage now writes empty
contract rows and the build log says so.)

Install the package from PyPI — in a virtual
environment, so the install is yours to discard
(`python3 -m venv venv`, then activate it; on
Debian/Ubuntu the venv may first need the
`python3-venv` system package):

```console
$ pip install ontosmith
```

The tracks run against files in this repository —
the example corpora and demo tooling — so clone it
as well and work from the clone's root:

```console
$ git clone https://github.com/Dongbumlee/ontosmith.git
$ cd ontosmith
```

Checkpoint: `ontosmith --help` answers, and you are
at the clone's root. Everything in the tracks up to
each **LIVE** build now runs — profiling, batch
creation, validation, inspection all work with no
key and no store.

## Track 1 — Your documents

Two words first — you meet them in the first three
commands, and they are not the same thing. A
**profile** is a description of **your corpus**: a
deterministic census (document inventory,
conventions, the decision points a human should
settle), produced by `ontosmith profile` with no
model calls — the same documents always yield the
same profile hash. A **pack** is a **domain's
ontology as data**: the type inventory, definitions,
boundary examples, and operating declarations a
build runs under — versioned, pinned by sha256, and
carrying the conformance record of what its content
measured. A batch binds one of each; the full pack
contract is [PACK_DESIGN.md](PACK_DESIGN.md).

### 1. Profile the corpus (no key)

```console
$ ontosmith profile examples/corpus --family demo
Corpus profile
  family          demo
  status          COMPLETE
  documents       4
  claims          6
  decision points 0
  profile hash    sha256:7ae51d31fa9120ec
  spend           $0.0000
  written         profiles/demo (profile.json, setup_config.json, profile.md)
```

The `--family` name is yours to choose for your own
documents — it names the profile, and later binds the
pack whose family matches it. Read
`profiles/<family>/profile.md`: the claims are
scanner-verified statements about your corpus, and any
**decision points** it lists are the questions to settle
before a build spends anything.

### 2. Create the batch (no key)

```console
$ ontosmith batch create --source examples/corpus --collection demo \
    --docs "examples/corpus/*.txt" \
    --profile profiles/demo/profile.json \
    --max-cost-usd 2
profile: demo sha256:7ae51d31fa9120ec
binding: reference
created batch demo-4docs with 4 docs
run: python -m pipeline batch run demo-4docs
```

Three lines deserve a word:

- **No `--pack`** — an undifferentiated corpus runs the
  **Core baseline**, which applies by default. `--pack`
  names a *domain* pack (Track 2), and creation refuses
  a pack whose family does not match the profile rather
  than degrading silently.
- **`binding: reference`** — this is the good state,
  not a warning: it stamps that the run's connection
  resolved exactly to the shipped reference preset (the
  measured bindings). Re-point any seat (a seat is one
  capability slot of the connection — proposer, judge,
  escalation, drafting, embedder) and the stamp
  becomes `custom`, and the record says what ran
  ([connections.md](connections.md)).
- **`--max-cost-usd 2`** — the stop-line, frozen into
  the batch. Nothing has been spent; creation is
  keyless.

Checkpoint: the batch id ends in the document count
(`demo-4docs`). If that count is not your corpus's,
the `--docs` glob did not match what you meant —
patterns are matched relative to your working
directory, and an unquoted `*` may be expanded by
your shell before the tool sees it. Quote the
pattern, as above. Zero matches refuse by name
(`no documents matched`) and create nothing.

### 3. Build — LIVE (the one step that spends)

First, the key. The export names **the preset's** key
variable — because the reference preset is an OpenRouter
binding — not a product requirement:

```console
$ export OPENROUTER_API_KEY=...   # the reference preset's key variable, env only
$ ontosmith batch run demo-4docs
```

If the key is not set, the run refuses before any
stage runs — the error names the seat and the
variable, and nothing is spent:

```text
error: proposer seat: API key environment variable OPENROUTER_API_KEY is not set (binding host openrouter.ai, model anthropic/claude-haiku-5.5); set OPENROUTER_API_KEY or rebind the proposer seat
```

OntoSmith needs *a* connection, not *this* one.
Bindings are declared with **`ontosmith configure`**:
it asks per seat — base URL, model, key variable name,
dialect — starting from the preset's values, checks the
result with zero-spend probes, and writes
`ontosmith-bindings.env` — the full per-seat
declaration, as per-seat `ONTOSMITH_<SEAT>_…`
variables. Load the file before running:

```console
$ set -a; . ./ontosmith-bindings.env; set +a
```

and the same step runs under your custom binding.
Prefer files to prompts? `ontosmith configure
--emit-template` prints the shipped template
([bindings-template.env](bindings-template.env)); fill
a copy in and validate it with `ontosmith configure
--from-file <file>` — both doors write the identical
file. The file holds nothing but those variables:
setting them directly in your environment is the same
contract without the file — the file is what
`configure` writes, and you can write it by hand. The
full contract is in [connections.md](connections.md).

Second, the store. A build writes into **Neo4j**
(5.x), and this is the point of first need — set it
up now. Any running instance reachable from here
will do; **Docker is not required**. Install and
start one by [Neo4j's installation
guide](https://neo4j.com/docs/operations-manual/current/installation/)
— the authoritative procedure, kept current per
version and OS by Neo4j (its Linux instructions
are the WSL2 instructions too). The shape of it,
as examples:

```console
# standalone server (the guide's Linux tarball) —
# install a Java 17/21 runtime first, then:
$ neo4j-admin dbms set-initial-password <password>   # before the first start
$ neo4j start

# …or, if you already run Docker, the one-line alternative:
$ docker run -d --name ontosmith-neo4j -p 7474:7474 -p 7687:7687 \
    -e NEO4J_AUTH=neo4j/<password> neo4j:5
```

(A hosted **Aura** instance skips the install
entirely — its connection variables differ; see
[connections.md](connections.md#neo4j--the-graph-store-required-for-builds).)

Two contract lines are ours. Export **the password
you set** for the `neo4j` user — with a local store
at the default address, this variable is the whole
configuration (the full variable set, `NEO4J_URL` /
`NEO4J_USER` / `NEO4J_PASSWORD`, is in
[connections.md](connections.md#neo4j--the-graph-store-required-for-builds)):

```console
$ export NEO4J_PASSWORD=<the password you set>
```

And **verify before you proceed** — the store
takes a few seconds to boot; when this prints
`200`, it is serving:

```console
$ curl -s -o /dev/null -w "%{http_code}\n" http://localhost:7474
200
```

(`000` means not up yet — wait a few seconds and
retry. Opening http://localhost:7474 in a browser
and logging in as `neo4j` with your password is
the same check, with a face.)

Skip the store and the run tells you, before the
first stage and with nothing spent — it never runs
the paid stages and dies at WRITE:

```text
error: Neo4j at http://localhost:7474 is unreachable (…). A build requires a running Neo4j (the graph store) — start one and check NEO4J_URL / NEO4J_USER / NEO4J_PASSWORD; setup is documented in docs/connections.md (Neo4j — the graph store).
```

The run
executes the staged pipeline (ingest → NER/roster →
candidates → gates → route → write → index → verify)
under the batch's
frozen configuration; the ledger enforces the stop-line
— a unit that would cross the cap is never started, and
the run resumes from its ledger instead of
re-spending. Watch it with `ontosmith batch status
demo-4docs`.

A corpus this size — four documents —
prices in cents of model spend (the README's $1–2
figure for the same build counts the fixed overhead
around it);
`ontosmith verify project` counts and prices a larger
run before it starts (appendix track).

### 4. The finish line: serve, search, inspect one fact

```console
$ ontosmith serve --batch demo-4docs
ontosmith serve: neo4j store (15 triples, 4 documents) at http://127.0.0.1:8765 — /search /entity /neighborhood /triple /health
ontosmith serve: demo app at http://127.0.0.1:8765/

$ ontosmith search --batch demo-4docs "Who founded Alder & Finch?"
$ ontosmith inspect batches/demo-4docs
```

Which store serves depends on the environment you
carry into this step. With §3's store variables still
exported, `serve` and `search` read the graph store
the build wrote — the banner names it (`neo4j store`,
as above). Without them, the same commands read the
batch's **file store** instead (banner: `file
store`), which needs no running Neo4j at all. The
endpoints and the answer shape are the same either
way.

`search` answers with linked entities and
evidence-carrying triples, and ranked documents as
well. Both stores carry the document texts: the
file store from the batch's own index, and the
graph store because INDEX projects **every built
document's** chunks into it — each chunk tagged
with its document's best tier, so a build whose
candidates all landed Tier 1 serves its whole
corpus too (VERIFY checks the coverage and fails
by name if any built document is missing). No
generated prose: what you see is what the graph
holds. The demo app at `/` is the same surface in a
browser.

`inspect` is pointed at the batch **directory** — that
is the form with the full view (resolved names, quoted
evidence, provenance). Pointed at a bare records file
(`written_triples.jsonl`) it degrades: `ent:` IDs,
"(none recorded)" evidence, and a Gaps section, because
a file alone carries no registry context.

One Gaps line
can appear even in the directory form —
`no batch/status provenance recorded`. Read it
precisely: it says the *triple record itself* carries
no batch/status fields (batch provenance lives in the
batch manifest and status, not on the triple); it is
not a missing-evidence warning, and the evidence and
judgment provenance printed above it are unaffected.
The full viewer contract is in
[cli-reference.md](cli-reference.md).

**Held pairs, settled.** A build can hold
entity-resolution pairs it would not merge on its own
— the WRITE metrics' `er_review_pairs` count names
how many. Held is a decision waiting, not a dead end:
list the pairs with the evidence the gate saw, settle
each one as a recorded human decision, and apply the
merges to the graph:

```console
$ ontosmith batch er-review demo-4docs
$ ontosmith batch er-decide demo-4docs --pair 1 --merge
$ ontosmith batch er-apply demo-4docs
```

`--keep-separate` is the other settlement: the pair
stays two entities, the decision is recorded, and
later runs never re-hold it. The batch's finish line
accounts for every ER decision by its terminal state
(merged / settled / still unactioned), and a build
that ends with unactioned holds says so in its own
finish line — `ER SETTLEMENT PENDING` — rather than
shipping the graph quietly.

## Example document sets

The tracks work on any folder of plain-text documents —
yours. If you want the exact corpora the packs were
measured on, fetch them from their sources (this
repository bundles no dataset text):

| Set | Pack | Source | License / terms |
|---|---|---|---|
| **Open Domain** — Re-DocRED dev subset (general documents) | `open-domain` (family `redocred`) | [Re-DocRED dataset repository](https://raw.githubusercontent.com/tonytan48/Re-DocRED/main/data/dev_revised.json) — fetched by `examples/demo/fetch_demo.py`, which writes a `SOURCE.md` beside the documents | Dataset: MIT. Document text derives from Wikipedia — upstream **CC BY-SA** |
| **Legal Contract** — CUAD v1 (510 commercial contracts, 41 clause categories) | `legal-contract` (family `cuad`) | [The Atticus Project — CUAD](https://github.com/TheAtticusProject/cuad); the v1 archive is on [Zenodo (record 4595826)](https://zenodo.org/records/4595826) | **CC BY 4.0** (source contracts are SEC EDGAR filings, public domain) |
| **Bio Medical** — BioRED (600 PubMed abstracts, document-level relations) | `bio-medical` (family `biored`) | [NLM — BioRED download](https://ftp.ncbi.nlm.nih.gov/pub/lu/BioRED/) | Freely available from NLM for research use; do not re-host |
| **Legal Contract, imported ontology** — the same CUAD contracts as above, under an imported schema | `fibo-contracts` (family `fibo-cuad`) | Documents: CUAD as above. Schema: [FIBO](https://github.com/edmcouncil/fibo) (EDM Council), pinned release `master_2026Q3`, fetched in Track 4 | Documents **CC BY 4.0**; FIBO **MIT**-licensed, credited — never rebranded |

The tracks carry their own fetch steps (Track 4 §6
for the CUAD layout; appendix step A6 for Re-DocRED).

## Track 2 — Your documents + an ontology domain pack

Same documents, one change: bind the pack whose family
matches the profile. With the Open Domain example set
(appendix track fetches it):

```console
$ ontosmith profile demo-data --family redocred
...
  profile hash    sha256:aceac6fb15f59e38
...

$ ontosmith batch create --source demo --collection redocred-dev-subset \
    --docs "demo-data/doc_*.txt" --profile profiles/redocred/profile.json \
    --pack open-domain --max-cost-usd 2
profile: redocred sha256:aceac6fb15f59e38
pack: open-domain 0.6.0 (payload sha256:ac14ac0483ed444b…)
binding: reference
setup_config: resolved (core baseline + open-domain, 36 keys) -> setup_config.resolved.json
created batch redocred-dev-subset-40docs with 40 docs
```

What the pack bought, mechanically: its payload was
**pin-verified** against the manifest, **overlaid per
key** on the Core baseline (the pack governs every key
it declares; undeclared keys keep the core defaults —
the resolution order is the contract,
[PACK_DESIGN.md](PACK_DESIGN.md) §1.3), and the resolved
configuration was **frozen into the batch** as
`setup_config.resolved.json`.

What it bought in content
is the domain's schema: the type inventory,
definitions, boundary examples, and fitted declarations
the proposer and gates now run under — itemized in the
pack's contents index (`packs/open-domain/pack.json`)
and priced in its conformance record.

Run it and take the same finish line as Track 1 (build
→ serve → search → inspect one fact). The honest
comparison is Track 1 vs Track 2 at that finish line:
same documents, same seats, one variable — the pack.

The shipped pairings:

| Your documents | Profile family | Pack |
|---|---|---|
| General documents (Re-DocRED set) | `redocred` | `open-domain` |
| Contracts (CUAD set) | `cuad` | `legal-contract` |
| Contracts (CUAD set), under an imported ontology | `fibo-cuad` | `fibo-contracts` (Track 4) |
| Biomedical abstracts (BioRED set) | `biored` | `bio-medical` |
| Anything else | your name | Core baseline (Track 1), or your own pack (Track 3) |

## Track 3 — Your documents → your own ontology

No shipped pack fits your domain? Draft one. This is
the pack-authoring loop — `draft → validate → brief →
approve → emit` — walked on the Alder & Finch profile
from Track 1. Everything here is keyless until the
optional `--llm` drafting variant.

The loop consumes a **profile** of your corpus. If you
skipped Track 1, make one first — it is a single
keyless command, and every step below reads the file
it writes:

```console
$ ontosmith profile <your corpus dir> --family <family>
# writes profiles/<family>/profile.json
```

(What a profile is, and how it differs from a pack:
"Two words first", above; the full walk is Track 1 §1.)

### 1. Draft

A **brief** tells the builder what the domain is. It is
a small JSON file — purpose plus the type names you
expect:

```console
$ cat brief.json
{"text": "A small ceramics studio: its people, products, and workshops.",
 "purpose": "Track who founded and works at the studio, what it makes, and what it offers.",
 "entity_types": ["Organization", "Person", "Product", "Event"],
 "relation_types": ["founded by", "works at", "makes", "offers"]}

$ ontosmith ontology draft --profile profiles/demo/profile.json \
    --brief brief.json --pack-id demo-ceramics \
    --display-name "Demo Ceramics" --out drafts/demo-ceramics.json
draft written: drafts/demo-ceramics.json (status DRAFT_NOT_APPROVED, 4 entity types, 4 relation types, schema hash sha256:98eb8b92a3b41be1…)
validation findings (20) — the decision-point list approval must settle:
  [MISSING_DEFINITION] entity_inventory[0].definition: entity type 'Organization' has no definition
  …
```

The brief's type names land as a skeleton — names
filled in, definitions deliberately empty, and the 20
findings are exactly the work of step 2.

Omit the
brief and the skeleton is empty instead: two
`EMPTY_…_INVENTORY` findings and no types at all. With
`--llm`, the drafting seat writes a fuller draft. The
drafting seat **ships unbound** — bind a model first
with `ontosmith configure` (the declaration it writes
is `ONTOSMITH_DRAFTING_MODEL`). Its output is still
only a draft: drafting never judges, and nothing emits
without the approval below.

### 2. Validate — the findings are the work list

```console
$ ontosmith ontology validate drafts/demo-ceramics.json
validation findings (20):
  [MISSING_DEFINITION] entity_inventory[0].definition: entity type 'Organization' has no definition
  [MISSING_BOUNDARY_EXAMPLES] entity_inventory.Organization: entity type 'Organization' has no boundary examples (form_examples / not_entities) — boundary material is what separates it from its neighbours
  [BAD_ENDPOINT_SIGNATURE] relation_inventory.founded by.endpoint_signature: relation type 'founded by' has no endpoint signature
  …
```

Every type needs three things before the pack may
exist: a **definition**, **boundary material** (examples
of what it is, and near-misses that are *not* it), and —
for relations — an **endpoint signature** (which types
it runs between). Edit them into the draft JSON
(`schema_front`), and re-validate until:

(The draft document's full field list — every key,
its type, and which fields are yours to edit — is
[PACK_DESIGN.md](PACK_DESIGN.md) §4, including the
two operating declarations a draft may carry:
`merge_prohibited_type_pairs` and `theme_attest_chunk`
(the second one matters at build time — §5 below).)

```console
$ ontosmith ontology validate drafts/demo-ceramics.json
validation: clean
```

### 3. Approve — a human decision, bound to the bytes

```console
$ ontosmith ontology brief drafts/demo-ceramics.json   # the approval brief: inventory + decision points
$ ontosmith ontology approve drafts/demo-ceramics.json --approver "A. Reader"
approval recorded: drafts/approval_2026-10-22.json
  schema hash sha256:eb92888135464743…
```

Approval binds to the schema hash: edit the schema
afterwards and the approval stale-dates and emission
refuses. Approving a draft that fails validation is
refused outright — the findings from step 2 are the
decision points the approver is settling. Two details
in the capture above are yours, not the tool's: the
approval file's name carries the date *you* approve
on, and the hash is the hash of the draft *you*
authored in step 2 — expect both to differ from the
values shown, and pass step 4 the file your run
actually wrote.

### 4. Emit — the pack exists

```console
$ ontosmith ontology emit drafts/demo-ceramics.json \
    --approval drafts/approval_<date>.json --out-root .
pack emitted: ./packs/demo-ceramics
  payload pin sha256:0e521c59d46b0db6…
```

```text
packs/demo-ceramics/
  pack.json                 manifest: identity, pin, contents index
  conformance.json          the record (starts as one entry: emitted_unmeasured)
  profile/setup_config.json the payload the pin binds
```

Emission never overwrites: emitting into a tree that
already holds this pack is refused by name. A changed
draft — a fixed definition, a newly declared operating
lever (§5) — is a **new version**, not an edit: approve
the changed draft and emit it to a fresh `--out-root`
with the new `--pack-version` (e.g. `0.2.0`), then
bind the batch to that tree's pack directory.

The first conformance entry states the honest position:
`emitted_unmeasured` — the artifact exists; no
measurement has priced it. Content earns its place
later, by the conformance measurement of
[PACK_DESIGN.md](PACK_DESIGN.md) §2 — per item, held-out
gold, pre-registered gates — exactly the discipline the
shipped packs' records show.

### 5. Build under your pack — same finish line

```console
$ ontosmith batch create --source examples/corpus --collection demo-own \
    --docs "examples/corpus/*.txt" --profile profiles/demo/profile.json \
    --pack ./packs/demo-ceramics --max-cost-usd 2
profile: demo sha256:7ae51d31fa9120ec
pack: demo-ceramics 0.1.0 (payload sha256:0e521c59d46b0db6…)
binding: reference
setup_config: resolved (core baseline + demo-ceramics, 25 keys) -> setup_config.resolved.json
created batch demo-own-4docs with 4 docs
```

`--pack` also accepts a pack directory, as here. Build
(**LIVE**, same key step as Track 1 — and the same
Neo4j prerequisite: the store must be running, Track 1
§3), then serve, search, and inspect one fact — the
same finish line, now under an ontology that is yours.

**Candidate guards — read this before your first build
under a pack you authored.** Between proposal and
judgment, every candidate triple passes deterministic
guards, and a blocked candidate never reaches the
judge. The guards are:

- `g1_quote` — the evidence quote must occur in the
  document.
- `l0_attest` — both endpoints must occur in the
  document.
- `l0_role_word` — an endpoint that is only a role
  noun the document leaves undefined — "the manager"
  — is not an entity. A term the document itself
  defines (a contract's `("Company")`, a stipulated
  "Party") is an identity inside that document and
  passes; a defined term with a stated referent is
  resolved to the referent and recorded like any
  canonicalization.
- endpoint identity — an endpoint typed under a
  mention form (a first name alone, a nickname, a
  surname alone, a name missing its suffix) is
  resolved to the document's canonical entity name
  before judgment, and the rewrite is recorded on the
  candidate (`canonicalized`). A form that names
  several entities in the document is flagged
  (`identity_ambiguous`) and left as proposed rather
  than guessed — one name string is one graph node,
  so an unresolved fragment would fragment the
  entity.
- `theme_missing` — the relation is nothing but
  prepositions.
- `duplicate` — the proposal's key, after identity
  resolution, equals an already-kept proposal's;
  recorded in the sidecar like any other loss rather
  than dropped silently.

Blocks
are never silent: the CANDIDATES stage's completion
line names the blocked count by reason, the batch
metrics carry the same counts, and every blocked item
is written to `candidates_blocked.jsonl` in the batch
directory with its reason and its evidence quote.

One former block deserves a paragraph of its own,
because it used to remove relations **wholesale**.
For a relation whose surface form ends in a
preposition — *works at*, *diagnosed with*,
*located in* — the guard once required the object to
be attested inside the proposal's shortest evidence
quote, not merely somewhere in the document (the
former `theme_presence` block). The reason was
fabrication control: without it, a model can pair any
subject with any object the document mentions,
anchored to an unrelated quote fragment. But natural
text often names the object across the passage rather
than inside the minimal quote — and then every
proposal for that relation blocked, and the relation
was simply absent from the graph. A judged sample of
the blocked class showed it was overwhelmingly
text-true, so the guard no longer blocks it: the
proposal is kept with a `theme_grade` field recording
its evidence level (`chunk` or `document`), and the
judge — which sees the quote and its context, and
whose rules already weigh evidence strength — makes
the call. Fabrication control rests on the verbatim
quote gate plus the judge, where it is exercised per
fact rather than per relation class. The older pack
declaration `theme_attest_chunk` is still accepted
(and still rides the emitted payload, bound by the
approval hash), but grading has subsumed it: it no
longer changes guard behavior. The field is defined
in [PACK_DESIGN.md](PACK_DESIGN.md) §4.

**Held pairs, under your pack too.** A build under
your pack can hold entity-resolution pairs it would
not merge on its own — the WRITE output's
`er_review_pairs` count names how many, and the WRITE
log line names the remedy. The settlement flow is
Track 1 §4's *Held pairs, settled*
(`batch er-review` → `er-decide` → `er-apply`); it
works unchanged under a pack you authored.

## Track 4 — Your documents + an ontology you already have

Track 3 assumed no schema exists and drafted one.
Often one does: an industry standard, a consortium
vocabulary, a public ontology your domain already
maintains. Track 4 is the **BYO-schema** path — bring
your own ontology, scope it honestly into a flat
extraction contract, and run it through the same
builder gates. Nothing about the gates changes: an
imported draft validates, is approved, and emits
exactly like a drafted one. What the import buys is
that the schema's content — its inventory, its
definitions — comes from the ontology, not from a
drafting pass.

The worked example is real, and its artifacts ship:
**FIBO** (the Financial Industry Business Ontology,
maintained by the EDM Council) imported over the
**CUAD** contracts of Track 2. The pack is
`packs/fibo-contracts`; the import's working papers —
extractor, inventories, scoping spec, scoping record,
and the coverage account this track ends with — are
in [`examples/fibo-import/`](../examples/fibo-import/).

### 1. Fetch the ontology (pinned)

```console
$ git clone https://github.com/edmcouncil/fibo.git examples/fibo-import/fibo
$ cd examples/fibo-import/fibo
$ git checkout master_2026Q3
$ cd ../../..
```

Pin the release: an ontology is a moving document,
and the scoping record below names the exact version
it scopes (here, release `master_2026Q3`, commit
`2d73435a`). FIBO is MIT-licensed; the contracts
slice is the **FND Agreements** module —
`FND/Agreements/Agreements.rdf` and
`FND/Agreements/Contracts.rdf` — plus
`FND/AgentsAndPeople/People.rdf`, the one file
Contracts imports for party concepts. ("FIBO" is an
EDM Council trademark: credit the source, as the
pack does — never rebrand it.)

### 2. Extract the inventory

An OWL file is not yet something a scoping decision
can be made over. The extractor flattens it to an
**inventory**: every declared class and property
with its IRI, label, definition, parents, and
domain/range. It is a small stdlib-only script —
parsing RDF/XML needs no product dependency:

```console
$ python examples/fibo-import/extract_fibo.py
wrote examples/fibo-import/fibo-agreements-inventory.json (87 entries)
wrote examples/fibo-import/fibo-people-inventory.json (53 entries)
```

Checkpoint: 87 concepts in the scoped module (55
classes, 29 object properties, 3 datatype
properties). Read a few entries: each carries FIBO's
own `skos:definition` where FIBO states one — those
definitions are what the import will carry into the
pack verbatim. (The two inventory files also ship
pinned in `examples/fibo-import/`, so every step
after this one runs without the fetch; the fetch is
the provenance path, and how you would inventory an
ontology of your own.)

### 3. Scope it — the spec is the judgment

An ontology is a hierarchy with axioms; a pack is a
flat extraction contract. The distance between them
is closed by a **scoping spec**: a JSON document
that declares, per target type, which source
concepts fold into it and which of them supplies the
definition — and accounts for everything else. The
shipped spec is
[`examples/fibo-import/fibo-contracts.spec.json`](../examples/fibo-import/fibo-contracts.spec.json);
its calls, in summary:

- **Merge the kind taxonomy.** FIBO's fifteen
  agreement/contract classes (written/verbal,
  uni/bi/multilateral, master, transferable and its
  negations, collateral…) collapse to one `Contract`
  type — the kinds are classification, not extraction
  signal in contract text.
- **Roles become relations.** FIBO models
  `ContractParty`, `Counterparty`, `Beneficiary` and
  kin as *classes* (roles a party plays). A flat
  contract extracts the party itself — an
  `Organization` or a `Person` — and carries the role
  in the linking relation (`HasContractParty`,
  `HasCounterparty`, `HasBeneficiary`, …).
- **Flatten subtrees that out-split the corpus.**
  FIBO's seven netting classes, written for
  derivatives documentation, become one
  `NettingProvision`.
- **Demote datatype properties.** Durations,
  timestamps, booleans (`isAssignable`), legal
  descriptions: a flat extraction contract has no
  datatype channel, so each is demoted to a recorded
  note on its type — visible in the record, not
  silently lost.
- **Drop with reasons, or not at all.** Occurrence
  classes (breach is an event, not a contract
  entity), pre-contractual forms (term sheets), the
  abstract commitment family, properties FIBO
  declares with neither domain nor range — each drop
  in the spec states its reason.

Boundary material — form examples, not-entities,
relation boundary examples — is **authored in the
spec**: FIBO states definitions, not extraction
boundaries. So are three definitions. `Organization`,
`Jurisdiction`, and `Date` are grounded in **OMG
Commons** classes FIBO references but does not define
in these files; the spec authors their definitions
and the scoping record marks them `authored`, never
dressed as FIBO's words.

The accounting is exact, and the import enforces it:
every concept in the scoped files must be claimed
exactly once — as a type source, a role conversion,
a demotion, or a reasoned drop — or the import
refuses and names the unaccounted concepts.

### 4. Import

```console
$ ontosmith ontology import \
    --spec examples/fibo-import/fibo-contracts.spec.json \
    --inventory examples/fibo-import/fibo-agreements-inventory.json \
    --inventory examples/fibo-import/fibo-people-inventory.json \
    --out drafts/fibo-contracts.json \
    --scoping-out examples/fibo-import/fibo-contracts.scoping.json
draft written: drafts/fibo-contracts.json (status DRAFT_NOT_APPROVED, 15 entity types, 13 relation types, schema hash sha256:72db0ede0a392262e8a04170f5d77c7405a90258f2643f9ccaef38ea2a5372d1)
scoping record: examples/fibo-import/fibo-contracts.scoping.json (88 scoped concepts: 19 kept, 21 merged, 14 flattened, 5 role-converted, 8 demoted, 21 dropped; 3 external entities)
validation: clean
```

Two outputs: an ordinary builder **draft** (the same
document kind Track 3 produces), and the **scoping
record** — the complete account, per concept: what
it became, or why it did not, with its FIBO parent
chain. The draft arrives with validation already
clean, because the spec had to be complete to
produce it.

### 5. The builder gates — unchanged

```console
$ ontosmith ontology brief drafts/fibo-contracts.json
$ ontosmith ontology approve drafts/fibo-contracts.json --approver "A. Reader"
approval recorded: drafts/approval_2026-10-26.json
  schema hash sha256:72db0ede0a392262e8a04170f5d77c7405a90258f2643f9ccaef38ea2a5372d1

$ ontosmith ontology emit drafts/fibo-contracts.json \
    --approval drafts/approval_2026-10-26.json --out-root my-packs
pack emitted: my-packs/packs/fibo-contracts
  payload pin sha256:0d77115fb79acf07…

$ ontosmith ontology check packs/fibo-contracts
pack check: clean — fibo-contracts 0.1.0 (manifest + conformance record satisfy the pack schemas; payload pin verifies)
```

The emit lands in a fresh root (`my-packs/`) on
purpose: emission never overwrites, and the clone
already holds the shipped `packs/fibo-contracts` —
emitting into `.` refuses by name, exactly as Track
3 describes. The `check` above runs against the
shipped pack; run it against
`my-packs/packs/fibo-contracts` and it verifies
yours, pin for pin identical.

The approval brief is where the scoping decisions
are settled: the draft's decision points restate the
judgment calls of step 3, and approving binds the
record to the schema hash exactly as in Track 3. The
shipped pack is this flow's output, unmodified — its
conformance record opens, honestly, at
`emitted_unmeasured`.

### 6. Profile and create over the corpus

The documents are Track 2's CUAD set (example sets,
row 4). For a first build, take a priced subset —
the first 100 contracts in sorted filename order
from the unpacked archive (which unpacks to
`CUAD_v1/`, texts in `full_contract_txt/`):

```console
$ mkdir cuad-subset-100
$ ls CUAD_v1/full_contract_txt/*.txt | sort | head -100 | \
    while read f; do cp "$f" cuad-subset-100/; done

$ ontosmith profile cuad-subset-100 --family fibo-cuad
Corpus profile
  family          fibo-cuad
  status          COMPLETE
  documents       100
  claims          6
  decision points 0
  profile hash    sha256:b54c32666bb16ea7
  spend           $0.0000
  written         profiles/fibo-cuad (profile.json, setup_config.json, profile.md)
```

(The profiler's census counts 99 unique contents —
the corpus holds one duplicate pair; it names it in
the profile.) The family is `fibo-cuad`, **not**
Track 2's `cuad`: the profile's family is what binds
the pack, and binding the imported pack to a profile
of the fitted pack's family is refused by name —
that refusal is the two experiments staying
separate.

```console
$ ontosmith batch create --source cuad-subset-100 \
    --collection fibo-cuad-100 \
    --docs "cuad-subset-100/*.txt" \
    --profile profiles/fibo-cuad/profile.json \
    --pack fibo-contracts \
    --max-cost-usd 10
profile: fibo-cuad sha256:b54c32666bb16ea7
pack: fibo-contracts 0.1.0 (payload sha256:0d77115fb79acf07…)
binding: reference
setup_config: resolved (core baseline + fibo-contracts, 27 keys) -> setup_config.resolved.json
created batch fibo-cuad-100-100docs with 100 docs
```

The price discipline is the appendix track's: a
hard cap, frozen at creation. For scale, the fitted
Legal build's recorded governing total is 3.8¢ per
document — at that rate 100 documents price near
$3.80, inside the $10 cap with room. Treat that as a
planning expectation, not a quote. The imported
pack's own rate is now measured (the completed
build is §7's record): **$0.85 for the 100
documents provider-counted — 0.85¢ per document**,
roughly a fifth of the planning figure. The cost is
proposer-dominated; the decision model's judging of
1,326 candidates cost three cents. One build, one
subset: a measured rate for this pack and profile,
not a universal one.

### 7. Build — LIVE, and the finish line

```console
$ export OPENROUTER_API_KEY=...   # as Track 1 §3
$ export NEO4J_PASSWORD=...       # the store from Track 1 §3, running
$ ontosmith batch run fibo-cuad-100-100docs
```

With the key set and the store serving, the build
runs the staged pipeline under the imported pack,
and the finish line is the same as every track's:
`ontosmith serve --batch fibo-cuad-100-100docs`,
one `search`, one fact opened with `inspect`.

Stated plainly, because this guide does not
fabricate transcripts: **the recorded run of this
walkthrough completed, in two legs.** In the
environment it was recorded in, the run first
refused — by name, before any stage, with $0
spent — because no provider key was set, exactly
as Track 1 §3 shows:

```text
error: proposer seat: API key environment variable OPENROUTER_API_KEY is not set (binding host openrouter.ai, model anthropic/claude-haiku-5.5); set OPENROUTER_API_KEY or rebind the proposer seat
```

With a key supplied, the same batch then ran
INGEST → WRITE in that environment: 1,201 Tier-1
triples projected — 1,155 edges over 958 entity
nodes. There INDEX failed three times (the
recording sandbox's egress proxy cut the
embeddings responses mid-stream, at 0.72–0.88 MB),
and VERIFY, which follows INDEX, did not run. 69
of the 1,326 candidates (5.2%) were also lost
unjudged to transport errors in that environment
and excluded fail-closed.

The finish was a resume, not a rebuild. The batch
state moved to a clean VM, WRITE re-projected
onto a fresh store from the recorded judgments —
the edge count held at exactly 1,155; entity
resolution settled a few more merges (955 entity
nodes) — INDEX completed (1,749 Tier-2 chunks),
and **VERIFY passed all 7 checks**. Final spend:
$0.8545 provider-counted for the 100 documents
(batch counted ledger $0.7611), against the $10
cap frozen at creation.

One serving note the finish exposed, and the fix
it produced. As first recorded, a build's chunks
reached the store only through a module-CLI
projection no run performed — against a fresh
store, the first `search --store neo4j` refused
by name (no such full-text index: chunk_text_ft),
and the searches above were run after that manual
step. The run owns it now: the INDEX stage ensures
the serving schema and projects the batch's chunks
as part of the stage, and `search` / `serve` heal
a store a pre-fix build left behind — schema
ensured, chunks projected from the batch's
recorded index — when they open it. Re-verified
zero-base on a clean VM (fresh store, this batch
resumed through the product path): INDEX
projected the 1,749 chunks itself, VERIFY passed
all 7 checks, and the concept queries the
pre-INDEX check missed answered with no step in
between — 'termination for convenience' returns
the convenience-termination clauses, 'cap on
liability' the limitation-of-liability passages,
and 'governing law and jurisdiction' its triples
as before. A precision audit and a pinned-QA
recall for this build have not been run; what
follows is therefore still a coverage account,
not a quality claim.

### What the imported schema gives you — and where its ceiling is

What FIBO gave, concretely: a principled inventory
(88 concepts, each with a stated definition, carried
verbatim), a party/role structure that survived
flattening as relations, and a schema that went from
clone to approved pack in one working session. That
is the honest case for importing: weeks of schema
design, done by a consortium, with the definitions
already written.

The ceiling is equally concrete, and it is measured,
not asserted. CUAD's own annotation scheme has 41
clause categories; the shipped account
([`examples/fibo-import/coverage.md`](../examples/fibo-import/coverage.md))
maps every one to the imported pack:

- **7 of 41 covered** by a dedicated type or
  relation — the dates, governing law, renewal,
  termination for convenience, notice periods,
  third-party beneficiaries.
- **34 of 41 partial** — the content extracts, but
  into the generic provision buckets: the graph
  records that a clause is there, not that it is a
  non-compete, a liability cap, or a license grant.
- **0 of 41 invisible** — the catch-all provision
  type receives every clause. Nothing is lost; most
  of it is unlabeled.

That is the BYO lesson. A public ontology models its
domain as its authors understood it; a corpus's
clause semantics are a *different* artifact — the
fitted Legal pack's 41 categories exist because CUAD
annotates them, not because contract law does.
Import when the ontology's distinctions are the ones
you need (this pack types provisions, roles,
supersession and subordination that CUAD never
annotates — questions the fitted graph cannot
answer). Fit, or draft, when the corpus's own
categories are the point. And read the scoping
record before you build: it is the one place the
import tells you, concept by concept, what you
actually bought.

## Appendix track — reproduce the reference build

How you **check the published record**: the flagship
build's whole loop, replayed against shipped
artifacts and a fetched dataset subset — now in its
own document, [hands-on-appendix.md](hands-on-appendix.md)
(steps A1–A9; A1–A5 spend nothing and need no key).

## Advanced: the pipeline candidate path

There is no local-extraction install: the
`ontosmith[nlp]` extra (GLiNER + spaCy) was removed
with the pipeline candidate source's retirement
(2026-10-29). Every build proposes candidates
through the proposer seat; nothing in a build loads
a local NLP model.

## Where to go next

- The method and the evidence: [`README.md`](../README.md)
  and the quality record ([whitepaper](whitepaper.md) §5; index:
  [`benchmarks/README.md`](../benchmarks/README.md)).
- Your own provider, your own models:
  [`connections.md`](connections.md) — seats, recommended
  models, and the custom-binding contract.
- The pack contract — manifest, pins, candidates,
  conformance: [`PACK_DESIGN.md`](PACK_DESIGN.md).
- The worked example, slowly: [`examples/`](../examples/).
- Serving in depth: [`serving.md`](serving.md).
