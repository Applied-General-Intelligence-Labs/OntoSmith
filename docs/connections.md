# Connections — what OntoSmith connects to

Building a graph takes two things beyond your documents:
an **LLM connection** (the seats below) and **Neo4j** —
the graph store the build writes into. They are the
product's two named pillars, one per kind of work:
**Jev**, the decision model, judges what is true enough
to write; **Neo4j** stores it — native graph traversal,
a vector index, and full-text search in one store,
which is why it was chosen (alternatives were reviewed
and declined on exactly this point).

Neither is optional
for a build. Everything else is local. This page says
which seats the LLM connection plugs into, how a
binding is declared, which models we recommend for each
seat (and why), how to set Neo4j up, and what runs with
neither in place.

## Seats: capability tiers, not model names

The pipeline never asks for "a model". It asks for a
**seat** — a capability tier with a defined job — and
your connection fills the seats:

| Seat | Job | Plugs into |
|---|---|---|
| **Proposer** | Candidate extraction: proposes triples for enumerated pairs (free triples and the fitted pair call). The roster/census is deterministic code in the proposer's own module, not a model stage | Chat endpoint |
| **Judge** | The Jev decision gate: decides which candidates are true enough to write | Decision endpoint |
| **Escalation** | Adjudication escalation for shaky-band items; the gate-evaluation judges (the decisions-route gates' default model, and the text judge) | Chat / decision endpoint |
| **Drafting** | Schema and profile drafting — an authoring aid behind the human approve gate. **Ships unbound**: no model is named for it until you declare one. **Never judges** | Chat endpoint |
| **Embedder** | Indexing and question embedding for candidate search and retrieval | Embeddings endpoint |

NER is not a seat: the batch NER stage is a contract
stage that writes empty mention rows (the local
GLiNER spotter was retired with the pipeline
candidate source, 2026-10-29) and binds nothing.

Domain packs (a domain's ontology as data) declare
only these tiers. Model names,
endpoints, and keys live in the connection layer and
nowhere else — a pack can be shared without sharing
anyone's provider account.

## How a binding is declared

Each seat binds three things:

- an **OpenAI-compatible base URL**,
- a **model name** for that seat,
- a **key variable name** — the key's *value* is read
  from that environment variable, never from a settings
  file. Config names the variable; it never holds the
  secret.

Bindings are resolved by the connection layer
(`pipeline/connections.py`). With nothing declared, the
**reference preset** applies: the values in
`pipeline/reference_profile.py` (the OpenRouter base URL
plus the measured seats' model constants) — the measured
configuration, pre-filled so a default install
reproduces the published record. The drafting seat is
the one deliberate gap: the preset declares its endpoint
coordinates but **no model**, because drafting is not
part of the measured pipeline (see Recommended models,
below).

To bind your own provider, gateway, or local
OpenAI-compatible server, declare the binding with
**`ontosmith configure`**. It asks per seat — base
URL, model, key variable name, dialect — starting
from the preset's values, checks the result with
zero-spend probes (key presence, `GET {base}/models`,
and the decisions route's presence for the judge and
escalation seats), and writes
`ontosmith-bindings.env`: the full per-seat
declaration, stamped with the binding's name
(`reference` when every answer equals the preset,
`custom` otherwise).

Prefer files to prompts?
`ontosmith configure --emit-template` prints the
shipped template
([bindings-template.env](bindings-template.env));
fill a copy in and validate it with
`ontosmith configure --from-file <file>` — both doors
write the identical file, and the worked examples in
the template are the canonical examples.

Load the
file into your environment
(`set -a; . ./ontosmith-bindings.env; set +a`) and
the declarations below take effect; setting the
variables directly in the environment is the same
contract without the file.

Two global variables cover the common case:

| Variable | Effect |
|---|---|
| `ONTOSMITH_BASE_URL` | Base URL for every seat |
| `ONTOSMITH_KEY_ENV` | Name of the environment variable holding the key, for every seat |
| `ONTOSMITH_DIALECT` | Provider dialect for every seat (below) |

Per-seat variables override the globals, which override
the preset:

| Variable pattern | Effect |
|---|---|
| `ONTOSMITH_<SEAT>_BASE_URL` | Base URL for one seat |
| `ONTOSMITH_<SEAT>_MODEL` | Model for one seat |
| `ONTOSMITH_<SEAT>_KEY_ENV` | Key variable name for one seat |
| `ONTOSMITH_<SEAT>_DIALECT` | Provider dialect for one seat |
| `ONTOSMITH_<SEAT>_DECISIONS_URL` | Decisions route for the judge/escalation seats |
| `ONTOSMITH_<SEAT>_PRICE_IN_PER_MTOK` / `_PRICE_OUT_PER_MTOK` | Declared rates (USD per million tokens), both or neither |
| `ONTOSMITH_<SEAT>_CAPABILITIES` | Capability declarations (below) |

`<SEAT>` is one of `PROPOSER`, `JUDGE`, `ESCALATION`,
`DRAFTING`, `EMBEDDER`. Routes derive from the
base URL — `{base}/chat/completions`,
`{base}/embeddings`, `{base}/systemone`, and
`{base}/decisions`. The decisions route is the one
non-OpenAI-standard path: under the preset, the judge
and escalation seats use the measured gateway's alpha
decisions route; rebinding a base URL re-derives the
route from the new base, and
`ONTOSMITH_JUDGE_DECISIONS_URL` /
`ONTOSMITH_ESCALATION_DECISIONS_URL` override it outright
when your gateway serves decisions somewhere else.
Individual gate and ontology commands also accept a
`--model` override for one-off runs.

Refusals are loud, never silent: if a seat's binding is
declared but its key variable is unset, the run stops
with a named error (`BindingKeyError`) identifying the
seat and the missing variable. Nothing quietly falls
back to the preset provider.

The drafting seat adds the
mirror-image refusal: used with no model bound — no
`ONTOSMITH_DRAFTING_MODEL`, no `--model` — it stops
with a named error (`BindingModelError`) identifying
the seat and the declaration that binds one, before
any request is made.

Any change from the shipped preset is a **custom
binding**, and run records state which binding produced
a run: batch creation stamps the resolved binding into
the batch record and the run manifest as non-secret
provenance — the binding's name (`reference` or
`custom`) plus, per seat, the base URL's host, the
model, the dialect, and the seat's cost basis (next
section). Key variable names and values are never
recorded. That is the whole contract: bind anything you
like, and the record still says what actually ran.

### Dialect, cost, and capabilities

A binding also declares a **dialect** per seat —
which provider dialect the route speaks:

- `openrouter` (the shipped preset): the route may be
  sent OpenRouter-only request fields (provider
  routing hints, reasoning controls) and reports a
  per-response cost in its usage block.
- `openai` (the default for custom bindings): the
  portable OpenAI-compatible subset only — no
  provider-only fields are ever sent to the route.

Undeclared, the preset's dialect is inherited while a
seat rides the preset's route; a seat rebound to
another base URL defaults to `openai` unless a dialect
is declared.

**Cost is metered honestly, or not at all.** Every
provider response's usage is normalized at one point
and stamped with a **cost basis**:

- `provider-reported` — the response carried a cost
  (the `openrouter` dialect);
- `estimated` — no reported cost, but the binding
  declares per-seat rates (`PRICE_IN/OUT_PER_MTOK`),
  so spend is computed from token counts;
- `not-provided` — neither: token counts are still
  recorded everywhere, but the spend is **unknown**,
  never a fabricated zero, and the batch ledger records
  it as such.

The USD stop-line follows from that: a batch whose
`max_cost_usd` cap is set refuses — at creation, and
again before a run — when any seat it will use is
`not-provided`, naming the seats and the remedies
(bind a cost-reporting route, declare rates, or run
uncapped). The cap's only home is the
`ONTOSMITH_MAX_COST_USD` environment variable —
unset, empty or `none` means no cap (anything else
must be a finite, non-negative USD amount); the ledger
still records what a run spent. `quickstart` caps a
first run at $2 when every seat meters cost and the
variable is unset. A cap that could never
trip is never silently recorded.

**Capabilities** replace model-name pattern matching.
Two are declared today — `no-temperature` (the route
rejects a temperature field for this model) and
`reasoning-tokens` (the model bills reasoning into
its completion budget, so it is given
`max_completion_tokens` instead of `max_tokens`).
Undeclared, they derive from the model id;
`ONTOSMITH_<SEAT>_CAPABILITIES` declares the set
outright, or adjusts the derived set with `+`/`-`
entries.

## Optimized for Jev

OntoSmith is **optimized for Jev**, the decision model
in the judge seat — and the seats come in two kinds.

**The judge seat is a pinned product component.** It
ships bound to a fixed model and version
(`typesafe/jev-1.13`) reached through its decisions
route, and the product's calibration — the gate
thresholds and the review band — is fitted to that
model, per gate. Changing the *shipped* binding is a
product release decision, taken only with
re-measurement. The connection layer applies the same
per-seat overrides to the judge seat as to any other
seat — nothing in code stops you — but a rebound judge
seat is explicitly **not** the optimized
configuration: the run is recorded as a custom
binding, and neither the shipped calibration nor the
published quality record carries over to it.

**Every other seat is yours to bind.** Proposer,
escalation, drafting, and embedder
each take whatever model,
endpoint, and provider you declare, per seat (above).
Several of the shipped seats are held by other
vendors' models; that is the norm this layer is built
for, not an exception.

The shipped assignments were not picked by
preference. Where seats were chosen by measurement,
they were **selected by head-to-head comparative
measurement** — candidate models run against the
incumbents on the same corpora, adopted only where the
measured outcomes supported the change — and the
[white paper](whitepaper.md) is the record of those
measurements (the extraction bake-off and the
judge-configuration gates).

The judge seat is the
exception to that sentence: Jev is a pinned product
component, not a bake-off winner, and no
Jev-versus-frontier judgment comparison is in the
published record.

And what a different binding does
not inherit is the calibration: figures measured under
one configuration are statements about that
configuration, and a custom binding's numbers are its
owner's to measure.

## The identity seat

One question inside the build is not a gate: when a
document mentions a name in a shortened form, does it
complete to a longer name in the same document, or
stand as written? A deterministic rule chain answers
most of these; the contested remainder is judged by
the **identity seat**, which is on by default — the
core baseline declares `identity_seat_live` in its
setup_config, the way it declares the gate
thresholds. Judging is the product's mechanism, not
an opt-in.

The live form runs a frozen completion process. For
each contested pair the pipeline assembles an
evidence dossier from the document — the fragment's
sentences, the longer name's definition and
mentions, sibling names — scores one fixed claim
against it with the pinned Jev component, and
combines the score with the rule chain: a high score
sustains a completion the rules reached, a low score
holds the fragment as written, and the band between
defers to the rule chain. Every verdict is recorded
in the batch's `identity_verdicts.jsonl`, keyed by
the dossier's fingerprint and the process's
calibration id, and a re-run reads the ledger
instead of re-judging — a rebuild reproduces the
same completions even though the scores themselves
are not bit-stable.

A pack can take the seat out of the live form in two
ways: declare `identity_seat_live` false to run the
rule chain alone, or declare
`identity_seat_decisions` — a frozen JSONL file of
verdicts produced offline for the corpus — which
supersedes the live default. One payload declaring
both forms is refused at validation, and a live seat
whose key is missing when a point is scored fails
the stage closed.

The frozen file has a second, corpus-level form:
`identity_seat_decisions_global` names a global
freeze, produced from per-point verdicts by
`ontosmith identity-freeze`. The freeze keys each
verdict by the (fragment, candidate) pair instead of
by document — one verdict for the pair everywhere —
and promotes pairs the offline judge decided
differently in different documents to ambiguous
records. An ambiguous pair carries no verdict: the
seat defers to the rule chain, and the batch lists
the ambiguous pairs it actually met in its
`identity_ambiguous.jsonl` for the review band.
Declaring both file forms is refused; a declared
freeze that cannot be loaded fails the stage closed.

## Recommended models

The recommended binding for each seat — for the
measured seats, the exact set the published quality
record was measured with:

| Seat | Recommended |
|---|---|
| Proposer | `anthropic/claude-haiku-5.5` |
| Judge (Jev gate) | `typesafe/jev-1.13` |
| Escalation | `anthropic/claude-sonnet-5.5` |
| Drafting | — (bind your own) |
| Embedder | `openai/text-embedding-3-large` |

NER is not a seat (see the seat table above): the
local GLiNER spotter was retired on 2026-10-29, and
the LLM proposer is the only candidate source.

Two honest qualifiers:

- **Recommendation is not a requirement.** Bind any
  models you prefer — that is a custom binding (above),
  recorded as such. The published figures are the
  *recommended configuration's* figures: reproduce them
  with the recommended set, and re-measure on your own
  account with anything else.
- The judge seat is the one exception to "swap freely":
  its thresholds are calibrated to the pinned gate, so
  changing it without re-measurement invalidates the
  operating points, not just the provenance line.

## The reference preset: our measurement environment

The shipped preset fills the generic binding with
**OpenRouter** (`https://openrouter.ai/api/v1`) and the
recommended models above. That is because OpenRouter is
the environment *we measured in* — the preset is the
reproduction path for the published record, pre-filled
so a default install runs the measured configuration.

It is not a product premise. OntoSmith does not require
an OpenRouter account; it requires one LLM connection,
and the preset is simply the first binding of it.

**Reaching the judge takes nothing extra.** Under the
preset, the judge seat is served by the same provider
as every other seat: OpenRouter serves the pinned model
`typesafe/jev-1.13` — `typesafe` is the model-id
namespace under which Jev is served there — and Jev's
calls go to OpenRouter's own routes (the systemone
route for batch gates, the alpha decisions route for
the gate CLI) using the **same `OPENROUTER_API_KEY`
and the same account** as the chat seats. A default
install with that one key set reaches every seat, the
judge included. There is no separate Jev account, key,
or signup.

One route note, for the record — the route is part of
the provenance, and this is its only home in the docs.
The published measurements ran with the chat and
embedding seats routed over OpenRouter, and in the
strict sense an operating point includes its route:
the same models reached over a different route are a
different configuration, whose figures are that
configuration's to measure.

The judge seat is the
exception to the *chat* route only: it does not use the
chat route or any provider routing — its calls go to
the pinned decisions/systemone routes on the preset
provider named above, whatever route the other seats
take.

*Roadmap, stated plainly:* the generic binding speaks
the OpenAI-compatible API shape, which covers most
hosted gateways and local servers today. Presets for
providers' **native** dialects (non-OpenAI request
shapes — direct Anthropic/OpenAI APIs, Azure, local
runtimes) are planned additions, not shipped ones; until
they land, those providers are reachable through any
OpenAI-compatible route.

## Neo4j — the graph store (required for builds)

A build writes into Neo4j: WRITE stores the accepted
triples and entities, INDEX stores chunk embeddings in
its vector index, VERIFY reconciles the graph against
the write manifest, and `search` / `serve` read the
graph from it. The registry (SQLite, local) remains the
source of truth for records and decisions; Neo4j is
where the graph itself lives. There is no file-based
build path — the file store exists to read recorded
artifacts back (the keyless demo and replay path in
[serving.md](serving.md)), not to build into.

**Setup.** OntoSmith needs a **running Neo4j 5.x**,
reachable at `NEO4J_URL` (below), with a password
set for the `neo4j` user. How it was installed is
not OntoSmith's concern, and **Docker is not
required**. Follow [Neo4j's installation
guide](https://neo4j.com/docs/operations-manual/current/installation/)
— the authoritative procedure, maintained per
version and OS (its Linux instructions are the
WSL2 route as well). The shape of it, as examples:

```console
# standalone server (the guide's Linux tarball) —
# install a Java 17/21 runtime first, then:
$ neo4j-admin dbms set-initial-password <password>   # before the first start
$ neo4j start

# …or, if you already run Docker, the one-line alternative:
$ docker run -d --name ontosmith-neo4j -p 7474:7474 -p 7687:7687 \
    -e NEO4J_AUTH=neo4j/<password> neo4j:5
```

Community Edition is enough (see the licensing
note below). What is ours is the contract around
that link: export the password you set (below),
and **verify the store answers** before a build —
it takes a few seconds to boot; `200` means
serving:

```console
$ curl -s -o /dev/null -w "%{http_code}\n" http://localhost:7474
200
```

`000` means not up yet — wait and retry. (The
browser UI at the same address, logged in as
`neo4j` with your password, is the same check.)

**Aura (hosted)** skips the install entirely:
create the instance, keep the password issued at
creation (it is shown once), and set `NEO4J_URL`
to the instance's endpoint and `NEO4J_USER` as
Aura presents it — Aura hosts are detected
automatically and use the Query API (see the
table).

The connection is environment-only, like
everything else:

| Variable | Used for | Default |
|---|---|---|
| `NEO4J_URL` | HTTP endpoint the client talks to (a local server's transactional API; Aura hosts are detected and use the Query API automatically) | `http://localhost:7474` |
| `NEO4J_USER` | User | `neo4j` |
| `NEO4J_PASSWORD` | Password | — (no default; env only) |
| `ONTOSMITH_FULLTEXT_ANALYZER` | Lucene analyzer for the serving full-text indexes (`chunk_text_ft`, `entity_name_ft`) — set it (e.g. `cjk`) for non-Latin corpora when the server bundles a suitable analyzer. Applies when an index is first created; changing it on an existing store requires dropping those two indexes and re-running a build's INDEX/VERIFY. An invalid name fails the schema ensure by name | — (Neo4j's stock `standard`) |

With a local default install, `NEO4J_PASSWORD` is the
only variable you must set. Before a build spends
anything, `batch run` probes the configured store: if
it is unreachable or rejects the credentials, the run
refuses with a named error that says what is wrong and
points here — it never runs the paid stages and then
dies at WRITE. The unreachable case reads:

```text
error: Neo4j at http://localhost:7474 is unreachable (…). A build requires a running Neo4j (the graph store) — start one and check NEO4J_URL / NEO4J_USER / NEO4J_PASSWORD; setup is documented in docs/connections.md (Neo4j — the graph store).
```

(The probe fires only when a graph
stage — WRITE, INDEX, or VERIFY — is still pending.)

**One graph per store.** Ontology building completes
first; the graph is then constructed from that one
completed ontology — and the store holds exactly that
one graph. Every WRITE stamps the store with the
graph's identity (the pack and version, or the profile
lineage, that produced it, and the batch that built
it). A build under a changed ontology **replaces** the
store's graph rather than accumulating a second one
beside it: the run names the graph it is replacing —
in the preflight output, before anything is spent,
and again at WRITE — and per-version stores are not a
thing to manage.

If the store holds content no stamp
identifies (written before stamping, or by another
tool), the build stops by name instead of overwriting
it. The registry keeps every replaced graph's full
history — decisions, manifests, artifacts — so nothing
is lost when the store moves on; the store itself is
current-state only, and serving ([serving.md](serving.md))
answers from the one graph that is there, with no
scope to specify.

*Licensing.* OntoSmith does not bundle Neo4j; you run
your own instance under Neo4j's own terms. The
Community Edition (GPLv3) is sufficient — OntoSmith
requires no Enterprise-only feature. The official
Python driver is Apache 2.0.

## Embeddings need a hosted connection

There is no local embedding fallback. Where an embedder
is actually required — `search --embed`, `serve
--embed`, candidate search over a Neo4j registry, a
build's INDEX stage — an embedder seat whose key is
unset stops with the named `BindingKeyError` refusal
(above), declared or not, rather than quietly embedding
with a weaker local model: a local model was measured
against the hosted embedder and missed candidates it
catches, and the record was measured with the hosted
one.

Surfaces that need no embedder — lexical + graph
search without `--embed`, and everything under "What
needs no key at all" below — run keyless exactly as
before.

## What needs no key at all

A good part of OntoSmith runs before any connection
exists:

- `ontosmith profile` — the corpus census is
  deterministic; no model is called.
- `ontosmith inspect` — evidence and provenance viewing
  over recorded output.
- `ontosmith ontology validate` (and brief / approve) —
  deterministic checks and the human decision; only
  `draft` calls the drafting seat.
- `ontosmith verify` — score / project / report and the
  appendix replay are local computation over your files.
- `ontosmith batch create` — freezes the configuration
  and the stop-line; `batch run` is the step that spends
  — and the step that needs Neo4j running (above).

Start there. Connect a provider when you are ready to
build — on the [hands-on guide's](hands-on.md) Track 1,
or your own corpus.
