# Appendix track — reproduce the reference build

The [hands-on guide](hands-on.md)'s four tracks are
how you use OntoSmith; this appendix track is how
you **check its published record**: the flagship
build's whole loop — profile → counted projection →
gold audit → capped live run → corrected scoring —
replayed and re-run against shipped artifacts and a
fetched dataset subset. Steps A1–A5 spend nothing
and need no key.

### A1. Profile the smoke corpus

Three hand-sized documents ship in `examples/demo/smoke/`:

```console
$ ontosmith profile examples/demo/smoke --family smoke
Corpus profile
  family          smoke
  status          COMPLETE
  documents       3
  claims          6
  decision points 0
  profile hash    sha256:560f304c6651e3a6
  spend           $0.0000
  written         profiles/smoke (profile.json, setup_config.json, profile.md)
```

Check yourself: the same corpus always yields the same
profile hash. That determinism is what makes a profile
a *record* rather than a log.

### A2. Inspect a triple

The smoke set's build output ships as a recorded
artifact, so inspection runs offline:

```console
$ ontosmith inspect examples/demo/smoke/triples.jsonl
Triple  (1 of 3 in examples/demo/smoke/triples.jsonl)
  subject   North Loop Cycles
  relation  founded by
  object    Dana Whitfield
Evidence
  [quote] North Loop Cycles is a community bicycle workshop founded in 2016 by Dana Whitfield and Rob Okafor.
Provenance
  doc_id               north-loop-history
  tier                 1
  p_true               0.97
```

This is the records-file form; the batch-directory form
of Track 1 §4 is the richer view. Try `--object
"Priya Raman"` and `--json`.

### A3. Count a build's workload before running it

A counted projection reports the workload a build
will move through its stages, per scenario, and
names the governing (largest) one. This is the
flagship build's own counted input — 26,938 candidate
pairs:

```console
$ ontosmith verify project --counts candidate_pairs=26938
{
 "scenarios": {
  "base": {
   "candidate_pairs": 26938
  }
 },
 "total_units": {
  "base": 26938
 },
 "governing_scenario": "base"
}
```

What the run spends is recorded in its own ledger
as it goes; pricing that workload is the caller's
arithmetic, at their own provider's rates. In a
live batch the ledger is the discipline: a unit
that would cross the recorded line is
never started, and the run resumes from its ledger
instead of re-spending.

### A4. Audit the gold; score corrected

Benchmarks' gold is incomplete, so a raw score grades
your build against a partially wrong answer key. The
audit measures how incomplete: blind samples of your
unmatched predictions (Direction A) and of the gold
itself (Direction B), judged without gold status, with
verbatim quote verification and embedded controls —
then a stratified correction with intervals. The
flagship record ([whitepaper](whitepaper.md) §5; in-tree
pointer [`benchmarks/redocred.md`](../benchmarks/redocred.md))
is the worked instance, and its samples + verdicts ship
in [`benchmarks/appendix/`](../benchmarks/appendix/).

### A5. Replay the corrected scoring

Recompute the flagship's headlines from the appendix
alone, through the shipped estimator cores:

```console
$ python examples/demo/replay_scores.py
Corrected-scoring reproduction — from benchmarks/appendix/
Ruled adjudication contract (the record's headlines, whitepaper §5):
  Direction A support estimate  p_A = 0.7919 [0.7472, 0.8305]
  Direction B gold support       g_B = 0.5244 [0.4754, 0.573]
  Blind controls                 37/40 supported
  Quote-verification failures    11
  Corrected precision  FULL-500  0.911 [0.8919, 0.9275]
  Corrected precision  CONF-440  0.9121 [0.8897, 0.931]
  Ceiling-adj. recall  FULL-500  0.2028 [0.1856, 0.2237]
  Ceiling-adj. recall  CONF-440  0.2036 [0.1852, 0.2262]
Original adjudication protocol (provenance — the estimate as first published):
  Direction A support estimate  p_A = 0.945 [0.9161, 0.9643]
  Direction B gold support       g_B = 0.841 [0.8008, 0.8743]
  Blind controls                 40/40 supported
  Quote-verification failures    32
  Corrected precision  FULL-500  0.9765 [0.9641, 0.9847]
  Corrected precision  CONF-440  0.9753 [0.9603, 0.9849]
  Ceiling-adj. recall  FULL-500  0.1265 [0.1216, 0.1328]
  Ceiling-adj. recall  CONF-440  0.1263 [0.1212, 0.1332]
All headlines reproduce the published record (whitepaper §5), both verdict sets.
```

The script exits non-zero if any reproduced value leaves
the published figure — this is the failure-anatomy lesson
in executable form. The figures themselves are the
whitepaper's §5 record (the original protocol's estimate
stands there as provenance, §5.4); the record's anatomy
(§Failure anatomy; chart shipped as the whitepaper asset
`benchmarks/assets/anatomy.png`) shows where the recall
went.

On your own build, the same tooling runs as
`ontosmith verify audit-sets --predicted <triples.jsonl>
--gold <gold.jsonl> --out-dir <dir>` (builds the blind
A/B/C judgment sets) and `ontosmith verify report
--predicted <triples.jsonl> --gold <gold.jsonl>` (raw
scores, corrected estimates, and the failure anatomy as
one report card).

### A6. Fetch the demo corpus

One command pulls a Re-DocRED dev subset from the
dataset's own repository and writes it as plain text
beside your work, with a `SOURCE.md` carrying the
attribution and terms (dataset: MIT; underlying text:
Wikipedia, CC BY-SA):

```console
$ python examples/demo/fetch_demo.py --out demo-data --docs 40
...
Wrote 40 documents (48,011 bytes) to demo-data/
```

`--dry-run` prints the source, terms, and the expected
cost basis without downloading. First 40 documents in
file order — deterministic, so your run and this
guide's agree. This is the corpus Track 2 profiles.

### A7. Profile it, create the batch

```console
$ ontosmith profile demo-data --family redocred
Corpus profile
  family          redocred
  status          COMPLETE
  documents       40
  claims          6
  decision points 0
  profile hash    sha256:aceac6fb15f59e38
  spend           $0.0000
  written         profiles/redocred (profile.json, setup_config.json, profile.md)
```

The create is Track 2's command, verbatim — same
command, same output (profile line, pin-verified pack
line, `created batch redocred-dev-subset-40docs with
40 docs`), closing with the run line:

```console
run: python -m pipeline batch run redocred-dev-subset-40docs
```

Creation froze the executed configuration into the
batch (core baseline + pack overlay, pin-verified) and
Nothing has been spent.

> **Note — creating under a custom binding.** This
> appendix runs under the reference preset, whose
> provider reports cost, so every seat's spend is
> metered in the batch's ledger. Under a custom
> binding whose seats cannot meter cost (cost basis
> `not-provided` — see
> [connections.md](connections.md)), the ledger
> records those seats' spend as unmetered rather
> than estimating it. Bindings themselves are
> declared with `ontosmith configure`.

### A8. Build — LIVE (reference preset)

The export names the preset's key variable — the
reference preset is an OpenRouter binding, so its key
variable is the one below; the product needs a
connection, not this provider
([connections.md](connections.md)):

```console
$ export OPENROUTER_API_KEY=...        # the reference preset's key variable, env only
$ ontosmith batch run redocred-dev-subset-40docs
```

This is the one step that spends. It runs the staged
pipeline (ingest → NER/roster → candidates → gates →
route → write → index → verify) under the reference
preset. Bind a different
endpoint or model set — declared with `ontosmith
configure` and loaded from the `ontosmith-bindings.env`
it writes, as in Track 1 §3 — and the same step runs
under your custom binding instead.

The run's spend is recorded in the batch's ledger
as it goes. A stopped run resumes with
`ontosmith batch run redocred-dev-subset-40docs`,
never by silently re-spending. Watch it with
`ontosmith batch status redocred-dev-subset-40docs`.

### A9. Score and inspect your build

```console
$ ontosmith verify score --predicted batches/redocred-dev-subset-40docs/written_triples.jsonl \
    --gold <your gold file>
$ ontosmith verify report --predicted batches/redocred-dev-subset-40docs/written_triples.jsonl \
    --gold <your gold file>
$ ontosmith inspect batches/redocred-dev-subset-40docs
```

`verify report` ends in the report card: raw scores,
corrected estimates with intervals (once you have run
the audit sets of step A4 over your own output), and
the failure anatomy of *your* build — the same readout
this project's own flagship record publishes, produced
by the same code. `inspect` here is the batch-directory
form — the rich view of Track 1 §4.
