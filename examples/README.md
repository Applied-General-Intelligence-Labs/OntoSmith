# The shipped demo build

`triples.jsonl` in this directory is the graph
`ontosmith serve` opens when no source is named: a real
pipeline build of **Pride and Prejudice** (Jane Austen,
Project Gutenberg 1342), all 58 chapters, built with the
open-domain pack (0.8.0) under the reference profile —
737 Tier-1 triples over 303 entities, every triple
carrying its evidence quote, batch VERIFIED (all
consistency checks passed). Two
records quoting the edition's own 1894 illustration
apparatus were dropped at freeze time; nothing else was
edited. Serve it with no key and no database:

```console
$ ontosmith serve
```

and for your own document:

```console
$ ontosmith quickstart your-file.txt
```

# Worked example — profile, build, inspect

A tiny end-to-end pass over a four-document corpus, in the
order a real build runs. Steps 1 and 3 run offline, with no
API key and $0 spend; step 2 is the one step that needs a
model connection, so its output ships here as a recorded
artefact (marked as such) and the step itself is described,
not faked.

The corpus is **Alder & Finch**, a fictional ceramics
studio, in `corpus/` — four short documents (history,
people, products, workshops), written for this example.
No dataset text is redistributed.

## Step 1 — Profile the corpus (offline, deterministic)

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

The profiler censuses the corpus before any build:
inventory, hygiene, identity/defined-term structure, and
corpus conventions stated as scanner-verified claims in
`profiles/demo/profile.md`. It makes no LLM calls — the
same corpus yields the same `profile hash` on every run —
and its `setup_config.json` is the stage-0 configuration
the build consumes. On a real corpus this is also where
decision points surface for a human (here: none).

## Step 2 — Build (needs a connection; output recorded)

A build runs the staged pipeline under a pack — for an
undifferentiated corpus like this one, the **Core**
baseline pack (no `--pack` flag — the Core baseline
applies by default). First, the binding: declare it with
`ontosmith configure` — it walks the seats from the
preset's values, probes the result with zero spend, and
writes the bindings file you load before running. The
run below uses the shipped **reference preset**, so
accepting the defaults is the whole declaration; its key
variable is the one the export names:

```console
$ ontosmith configure
$ export OPENROUTER_API_KEY=…        # the preset's key, env only
$ ontosmith batch create --source examples/corpus --collection demo \
      --docs 'examples/corpus/*.txt' \
      --profile profiles/demo/profile.json
$ ontosmith batch run demo-4docs
```

The run is capped by a stop-line (`--max-cost-usd`, or
the pack's declared cap) enforced from the spend ledger,
and `ontosmith verify project` prices a counted candidate
set against that line before you commit to a run. The
build used the **reference profile**
(the tested default preset: proposer/pair judge
`anthropic/claude-haiku-5.5`, Jev gate `typesafe/jev-1.13`,
embedder `openai/text-embedding-3-large`, via OpenRouter).

**Recorded output:** `alder-finch/triples.jsonl` in this directory is
the build artefact for this corpus — seven Tier-1 triples
in the pipeline's judged-record shape, each carrying its
evidence quote and gate provenance — shipped so step 3
runs offline. It is a recording, not a live run: your own
build writes the same record shape into its batch
directory (`routed.jsonl`).

## Step 3 — Inspect a triple (offline)

```console
$ ontosmith inspect examples/alder-finch/triples.jsonl
Triple  (1 of 7 in examples/alder-finch/triples.jsonl)
  subject   Alder & Finch
  relation  founded by
  object    Mara Alder
Evidence
  [quote] Alder & Finch is a ceramics studio founded in 2011 by Mara Alder and Tomas Finch.
Provenance
  doc_id               alder-finch-history
  tier                 1
  p_true               0.98
```

Select by content instead of position:

```console
$ ontosmith inspect examples/alder-finch/triples.jsonl --subject "Priya Nair"
Triple  (6 of 7 in examples/alder-finch/triples.jsonl)
  subject   Priya Nair
  relation  joined
  object    Alder & Finch
Evidence
  [quote] Priya Nair joined Alder & Finch in 2019 as its glaze chemist.
Provenance
  doc_id               alder-finch-people
  tier                 1
  p_true               0.95
```

Every evidence quote is a verbatim substring of its
source document — the example's test suite checks this,
because an inspect view is only worth what its evidence
is worth. A record with no evidence span says so under
**Gaps** rather than rendering clean: absence is a
finding, not a formatting choice. `ontosmith inspect`
also takes a batch directory directly (it resolves
`judgments.jsonl` → `routed.jsonl` →
`written_triples.jsonl`) and `--json` for machines.

## Where to go next

- Design a schema for your own domain from a profile:
  `ontosmith ontology` (draft → validate → approve →
  emit a pack).
- Score a build of your own, raw and corrected:
  `ontosmith verify` — the tooling behind the quality
  record ([whitepaper](../docs/whitepaper.md) §5; index:
  [`benchmarks/README.md`](../benchmarks/README.md)).
