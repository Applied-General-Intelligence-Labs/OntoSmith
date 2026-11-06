# Reproduce — every headline figure, and the command that recomputes it

The published record makes claims; this page maps each
headline figure to the exact command that recomputes it,
or says plainly when a figure is record data rather than
something a reader can recompute. Three kinds of rows:

- **Recompute (no key)** — runs offline against shipped
  artefacts and fails loudly if a figure drifts.
- **Recompute (your run)** — the shipped tooling that
  produces the analogous figure for *your* corpus.
- **Record** — measured once, under the registered
  protocol; the data and the readout ship, the run
  itself is history.

## The flagship record (whitepaper §5)

| Figure | Value | Recompute |
|---|---|---|
| Corrected precision, FULL-500 | **0.9110** [0.8919–0.9275] | `python examples/demo/replay_scores.py` (no key) |
| Corrected precision, CONF-440 | **0.9121** [0.8897–0.9310] | same replay |
| Ceiling-adjusted recall, FULL-500 | **0.2028** [0.1856–0.2237] | same replay |
| Support estimate p̂_A / gold support ĝ_B | 0.7919 / 0.5244 | same replay |
| Blind controls under the ruled contract | 37 / 40 supported | same replay |
| Quote-verification failures | 11 of 740 verdicts | same replay |
| First-pricing figures (original protocol) | 0.9765 [0.9641–0.9847], recall 0.1265 | same replay — it recomputes **both** verdict sets and checks both against their published values |
| Raw census scores (precision 0.5723 …) | whitepaper §5.2 table | Record — frozen-build census; the values ship as data in `benchmarks/appendix/raw_scores.json` and the replay checks the corrected estimates against them |
| Failure anatomy (§5.3 fates) | per-fate counts + support rates | `python examples/demo/replay_scores.py` (the fate table is part of what it checks) |
| Cost (§5.6): run ledger $18.6802 ($0.03736/doc); key-observed governing total $18.7980 ($0.0376/doc) | flagship run, 500 documents | Record — the run's ledger; for your own corpus the analogous tool is `ontosmith verify project` (counted projection before a run) and the batch stop-line during it |

The replay reads only shipped files — the adjudication
appendix in [`benchmarks/appendix/`](../benchmarks/appendix/README.md)
(samples, key files, both verdict sets, the ruling text) —
and exits non-zero if any recomputed figure drifts from
the published value. Start to finish it needs no API key
and makes no model calls. The full record behind every
row is the [whitepaper](../docs/whitepaper.md) (§5); the
claim-level index is [`benchmarks/README.md`](../benchmarks/README.md).

## The supporting records

| Figure | Value | Where it lives |
|---|---|---|
| BioRED pilot readouts (§5.7) | conversion, grounding, per-type recall | Record — pack conformance entries in [`packs/bio-medical/`](../packs/bio-medical/); index in [`benchmarks/biored-cuad.md`](../benchmarks/biored-cuad.md) |
| BioRED Tier-1-wide corrected precision (§5.7) | 0.9321 over all 2,058 Tier-1 rows | Record — the Stage D audit's finals are stated in the whitepaper; the audit's stratum-level record is not shipped in this tree, so the figure is not reader-recomputable from shipped artefacts |
| CUAD retrieval delta (§5.8) | +0.080 recall@20 (adding the graph arm to hybrid retrieval: 0.6314 → 0.7115; 116 pinned questions) | Record — [`benchmarks/biored-cuad.md`](../benchmarks/biored-cuad.md); measured at zero on short abstracts (BioRED), stated wherever cited |
| Demo profile hash | `sha256:28ede2ab1b3e4fd2…` | Recompute (no key): `ontosmith profile examples/corpus --family demo` — the profiler is deterministic, so the hash is the check |

## What is *not* reproducible from this tree

- **The flagship build itself.** Re-running the 500-
  document build needs the corpus (fetchable under its
  own licence — see NOTICE), a live connection, and the
  measured spend. The *scoring* of that build is what
  ships for replay; the build is a run, not an artefact.
- **The seat-selection comparisons** (§5.5, §5.8) are
  gate records: their readouts and verdicts ship in the
  whitepaper and the pack conformance records; the
  underlying lab runs are not re-runnable from this
  tree.
- **Custom bindings.** Any figure measured under the
  reference profile is a statement about that
  configuration; a custom binding's numbers are its
  owner's to measure with the same tooling
  (`ontosmith verify`).
