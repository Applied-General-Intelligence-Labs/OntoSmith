"""Identity-seat dossier assembly (frozen process spec).

The dossier is the whole evidence a Decision-type judge
scores for one rule-4 decision point: how often each mention
is attested, how often the fragment occurs outside the
candidate, the other candidates this fragment was paired
with in the same document (and the other fragments this
candidate was paired with), the other names sharing the
fragment's last token, and a spread of attesting sentences
for both mentions. It carries NO gold and NO rule outcomes.

The assembly is deterministic code and is part of the
measured process: its output text is pinned by the
equivalence tests against the lab builder that produced
the frozen calibration. Any change here changes every
dossier hash and therefore invalidates the verdict ledger
— bump DOSSIER_ASSEMBLY_VERSION when it happens on purpose.

Language boundary: the assembly contract (counts, pairings,
spreads) is language-blind; the surface work it stands on
(sentence splitting, normalization, the identity index) is
the English pack's, imported from pipeline.proposer.
"""
from __future__ import annotations

from collections import defaultdict

from .proposer import (
    _identity_index,
    _outside_count,
    _phrase_spans,
    norm_ws,
    split_sentences,
)

#: Assembly version, stamped into the dossier hash and the
#: verdict-ledger key: a deliberate assembly change mints a
#: new version and old verdicts stop matching.
DOSSIER_ASSEMBLY_VERSION = "dossier-1"


def _occurrences(text: str, surface: str) -> list[str]:
    """Sentences of the document that attest the surface
    (normalized containment), in document order."""
    target = norm_ws(surface)
    if not target:
        return []
    return [s for s in split_sentences(text)
            if target in norm_ws(s)]


def _spread(rows: list[str], k: int = 6) -> list[str]:
    """Up to k rows spread across the list (first, last, and
    evenly spaced between) — position diversity without
    flooding the envelope."""
    if len(rows) <= k:
        return rows
    idx = sorted({round(i * (len(rows) - 1) / (k - 1))
                  for i in range(k)})
    return [rows[i] for i in idx]


def assemble_dossiers(
        text: str,
        pairs: list[tuple[str, str]],
        ) -> dict[tuple[str, str], str]:
    """One dossier per (fragment, candidate) pair of ONE
    document. `pairs` is the document's full consulted-pair
    set (surfaces): the pairing lists inside each dossier
    are computed over the whole set, so a pair's dossier
    depends on its document siblings, never on other
    documents. Duplicate pairs collapse."""
    unique = sorted(set(pairs))
    frag_pairs: dict[str, set] = defaultdict(set)
    cand_pairs: dict[str, set] = defaultdict(set)
    for frag, cand in unique:
        frag_pairs[frag].add(cand)
        cand_pairs[cand].add(frag)
    by_norm, _alias_of, _maximals, bridged = _identity_index(text)
    out: dict[tuple[str, str], str] = {}
    for frag, cand in unique:
        fo = _occurrences(text, frag)
        co = _occurrences(text, cand)
        ep, cn = norm_ws(frag), norm_ws(cand)
        surname = ep.split()[-1] if ep.split() else ""
        siblings = sorted(
            {by_norm[n]["surface"] for n in by_norm
             if n.split() and n.split()[-1] == surname
             and by_norm[n]["surface"] not in (frag, cand)})[:12]
        out[(frag, cand)] = (
            f"Document: {len(text.split()):,} words.\n"
            f"Mention A '{frag}' appears "
            f"{len(_phrase_spans(bridged, ep))} times; "
            f"mention B '{cand}' appears "
            f"{len(_phrase_spans(bridged, cn))} times; "
            f"A occurs {_outside_count(bridged, ep, cn)} times "
            f"outside B's occurrences.\n"
            f"Other candidates paired with A in this document: "
            f"{sorted(frag_pairs[frag] - {cand}) or 'none'}.\n"
            f"Other fragments paired with B: "
            f"{sorted(cand_pairs[cand] - {frag}) or 'none'}.\n"
            f"Other names in this document sharing A's last "
            f"token: {siblings or 'none'}.\n\n"
            "Mention A sentences (spread across the document):\n" +
            "\n".join(_spread(fo)) +
            "\n\nMention B sentences (spread across the document):\n" +
            "\n".join(_spread(co)))
    return out
