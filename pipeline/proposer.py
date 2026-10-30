"""LLM proposer — first-class candidate source for CANDIDATES (E4).

Integration of the two proven standalone harnesses into the batch
pipeline (gap G-03): cuad_llm_triples.py (free triples) and
cuad_clause_extract.py (clause slots). The recipes are carried over
unchanged — 20K-char chunks (19.5K step), at most 10 triples per
chunk, shortest-verbatim-quote instruction, Haiku 5.5 as the
default proposer model — because Step 1A measured THIS recipe's
output through the Jev gates (Tier 1 yields: legal triples 97.4%,
clause slots 91.5%, novels triples 95.0%).

The proposer only PROPOSES. Every kept item becomes a candidate in
the existing queue -> GATES (Jev) -> ROUTE flow; no score is ever
assigned here (the standalone harnesses' hard-coded p_true 0.9 is
exactly what G-03 abolished). Between proposal and candidacy sit
the deterministic span/attestation guards (§5, re-scoped to LLM
proposals for G-04/G-05):

  g1_quote       the quote (whitespace-normalized) is not a
                 verbatim substring of the document text.
  l0_attest      L0 document-level attestation: subject or object
                 surface is not attested anywhere in the document
                 (the §5 attestation window is the document; the
                 level actually reached — quote / chunk / document —
                 is recorded on the candidate as `attest`).
  l0_role_word   an endpoint is a bare role word the document
                 leaves undefined (the corpus profile's rule:
                 role nouns are not identities); only when the
                 profile declares role-word rules. A term the
                 document itself defines is an identity inside
                 it and passes (upstream program, 2026-10-29).
  theme_missing  the relation is a bare prepositional phrase —
                 no predicate, hence no theme.
  duplicate      the item's (subject, relation, object) key —
                 after identity resolution — equals an
                 already-kept item's; recorded, never silently
                 dropped.

Attestation is tiered (upstream program): verbatim, through
the document's own stipulated aliases (defined terms,
acronym pairs — the registry), under punctuation-spacing
collapse, or through a proposer-declared derivation surface;
the tier that carried an endpoint is recorded as
`attest_kind`. A verb+preposition relation whose object
attests in the document but not in the quote no longer
blocks (the former theme_presence): the item is kept with
its evidence grade recorded as `theme_grade` for the judge.

Blocked items land in candidates_blocked.jsonl with the reason code,
the same sidecar the Arm B structural constraints use. Slot fills
pass the span gate (g1_quote) and per-(doc, category, value)
dedupe; their subject is the document's own Document Name fill
(fallback: source file stem), mirroring cuad_clause_tograph.py.

Profile wiring: the proposer takes an optional
`guidance` string rendered from the corpus profile's setup_config
(pipeline/profile_config.derive_guidance — the channel). No
guidance -> the prompt is byte-identical to the recipe above. With
guidance, the model is asked for typed endpoints (subject_type /
object_type); guard_triples validates the tags against
the profile's endpoint-type vocabulary.

Domain framing: the prompt templates are domain-neutral;
the framing line (and the slot rules block) that specialize them
to a family are profile data threaded through the `framing` /
`rules` parameters (pipeline/profile_config.proposer_framing /
slot_framing / slot_rules). The standalone harnesses' contract
text survives as the CUAD profile's declared framing — the
prompt CUAD builds is byte-identical to the recipe above.

Mention layer (issue #54, profile-gated by the
`mention_layer` setup_config key; absent = off and every
behavior below is inert): entities register and link by
noun surface forms, so a fact the text states through a
pronoun, a definite description, or a sentence other than
the one naming the entity has no citable path to its
endpoint — the proposer either resolves it silently or
skips it, and a dropped candidate records only a score.
With the layer on, the proposer additionally emits, per
endpoint, the mention span as it appears in the quote
(verbatim), its type (named / pronominal / descriptive),
and — when the mention is not the entity's name — a
resolution proposal: the antecedent span, verbatim from
the document, that names the entity. guard_triples
verifies every DECLARED span with the same verbatim
discipline as the quote itself: a mention that is
present but unverifiable blocks the item by name
(mention_missing / mention_span / mention_antecedent),
never a silent drop. An endpoint whose mention data is
absent — the proposer did not answer for it — is not
blocked: it falls back to the legacy evidence path and
is judged on its quote exactly as without the layer.
(Cut 2: the first cut blocked absence too, and the
measured Phase 2 A/B showed the proposer's unreliable
mention-field compliance culling a quarter of all
proposals before judging. The layer is additive by
policy — only a declared-but-invalid mention can
block — and every fallback is counted by endpoint in
the batch accounting, so the fallback rate is always
visible.) The quote is never rewritten — the resolution
travels as annotation, and GATES judges the resolution
as its own decision before the fact (stage_gates), so
the proposer proposes and Jev decides, as everywhere
else in the pipeline.

Entity sweep (issue #58, profile-gated by the
`entity_sweep` setup_config key; absent = off and every
behavior below is inert): the chunk pass proposes at
document density, and an entity the text names but the
pass never builds a triple around has no second chance —
the gold re-examination measured that population at
~30% of all verified labels (every one attested by name
in the text; reference-only presence was zero). The
sweep is an ADDITIVE second pass: a deterministic code
census extracts the document's candidate name surfaces
(maximal capitalized sequences, attested under norm_ws
containment or dropped and counted), the surfaces that
appear as no endpoint of any chunk-pass item become
anchors, and each anchor is re-asked in its own view —
its mention sentences ± 1, capped — under the chunk
prompt plus one rule (an endpoint of every triple must
be the anchor). Sweep items pass the same guards and
merge behind the chunk channel: a sweep item whose key
equals a chunk-pass item's is a counted duplicate, never
a replacement, and an item touching neither endpoint of
its anchor is counted off-anchor and dropped, never
blocked into the record. The chunk pass runs first,
untouched, with its own prompt — the pass can only add
proposal mass, which is the structural form of the
mention layer's measured lesson (a REPLACING variant
proposed less, and differently, and the draw fell).

Cut 2 (issue #58) makes the item's quote — its
evidence span — load-bearing in this channel only.
The first pilot's adjudicated precision was 0.676
against a 0.90 bar: assertions the text does not
state, proposed from a narrow anchor view, passed the
gates. The sweep prompt now states the span as a
verified term of the contract, and
propose_sweep_for_doc voids BY CODE any sweep
proposal whose span is missing (field absent, empty,
or non-string) or fails norm_ws containment in the
FULL document text — before the anchor test and
before the guards, never blocked into the record —
counted as `evidence_missing` in the stage metrics
and the finish record's restatement, so the void is
visible accounting, not the cut-1 parse filter's
silence (a span-less sweep item there vanished
uncounted). The accounting identity is
proposed = items + off_anchor + evidence_missing.
The void verifies presence, not sufficiency — a
verbatim span can still be over-read — which is why
the redesign is measured, not assumed. The chunk
channel is untouched: its items keep the legacy
discipline (a parse-required quote, guard-verified).
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

from . import connections
from .reference_profile import CHAT_URL as CHAT_URL  # preset route (re-export)
from .reference_profile import PROPOSER_MODEL, PROVIDER_SORT

DEFAULT_MODEL = PROPOSER_MODEL
CHUNK = 20000
STEP = 19500
MAX_PER_CHUNK = 10
WINDOW = 500  # judge context = quote ± 500 chars (audit standard)

# The base templates below are
# domain-neutral core text. A family's domain framing — the
# opening line of each prompt, and for slots the rules block —
# is profile data (pipeline/profile_config.py: proposer_framing /
# slot_framing / slot_rules), threaded in by stage_candidates.
# Before this fix, CUAD's contract framing was compiled into
# the core templates and every family ran under it (fiction,
# which declares no guidance, was told its documents were
# commercial contract excerpts). The defaults below are the
# neutral core framing a family gets when it declares none.
TRIPLE_FRAMING_DEFAULT = \
    "Extract factual relations from the excerpt below."

TRIPLE_PROMPT = """{framing}

Return ONLY a JSON array of at most {maxn} objects:
{{"subject": ..., "relation": ..., "object": ..., "quote": ...}}

Rules:
- subject / object: named entities only — parties, companies,
  people, defined terms, documents, dates, amounts, jurisdictions.
  Never pronouns, never bare role words alone ("the party").
- relation: the short verb phrase as stated in the text
  (e.g. "grants", "shall pay", "is located in", "terminates").
- quote: the SHORTEST verbatim phrase from the excerpt that states
  the fact (a clause, not a whole paragraph). Character-for-
  character; never paraphrase.
- Only facts the excerpt states. If none, return [].

Excerpt:
---
{text}
---"""

# Mention types the proposer may declare for an endpoint's
# mention span (issue #54). "named" = the mention is the
# entity's name or a form of it; "pronominal" = a pronoun;
# "descriptive" = a definite description that is not the
# name ("the company", "the German racer").
MENTION_TYPES = ("named", "pronominal", "descriptive")

# The mention-layer variant of the triple prompt (issue
# #54): identical rules for the triple itself, plus the
# per-endpoint mention fields. Used only when the profile
# declares the mention layer; the base template above is
# untouched so undeclared profiles build byte-identical
# prompts.
TRIPLE_PROMPT_MENTION = """{framing}

Return ONLY a JSON array of at most {maxn} objects:
{{"subject": ..., "relation": ..., "object": ..., "quote": ...,
  "subject_mention": ..., "subject_mention_type": ...,
  "subject_antecedent": ..., "object_mention": ...,
  "object_mention_type": ..., "object_antecedent": ...}}

Rules:
- subject / object: named entities only — parties, companies,
  people, defined terms, documents, dates, amounts, jurisdictions.
  Never pronouns, never bare role words alone ("the party").
- relation: the short verb phrase as stated in the text
  (e.g. "grants", "shall pay", "is located in", "terminates").
- quote: the SHORTEST verbatim phrase from the excerpt that states
  the fact (a clause, not a whole paragraph). Character-for-
  character; never paraphrase.
- subject_mention / object_mention: the exact words IN THE
  QUOTE that refer to the subject / object — the name itself,
  a pronoun ("He", "They", "it"), or a description
  ("the company"). Copy them character-for-character from
  the quote.
- subject_mention_type / object_mention_type: "named" when
  the mention is the entity's name or a form of it;
  "pronominal" for a pronoun; "descriptive" for a
  description that is not the name.
- subject_antecedent / object_antecedent: when the mention
  type is not "named", the SHORTEST verbatim phrase from
  the excerpt that names the entity the mention refers to
  (character-for-character; it may sit outside the quote).
  When the type is "named", use "".
- Only facts the excerpt states. If none, return [].

Excerpt:
---
{text}
---"""

# The declared-surface variant of the triple prompt
# (upstream attestation program, 2026-10-29): identical
# rules for the triple itself, plus per-endpoint
# `*_attested` fields — the exact words in the excerpt
# the endpoint was derived FROM when the endpoint itself
# is stated in a normalized form (a country name for a
# nationality adjective: "Germany" from "German"). The
# guard checks the declared surface's attestation
# (tier T3); whether the derivation is RIGHT is the
# judge's call under the R1 seam. Used only when the
# profile declares the layer; the base template above
# is untouched so undeclared profiles build
# byte-identical prompts.
TRIPLE_PROMPT_DECLARED = """{framing}

Return ONLY a JSON array of at most {maxn} objects:
{{"subject": ..., "relation": ..., "object": ..., "quote": ...,
  "subject_attested": ..., "object_attested": ...}}

Rules:
- subject / object: named entities only — parties, companies,
  people, defined terms, documents, dates, amounts, jurisdictions.
  Never pronouns, never bare role words alone ("the party").
- relation: the short verb phrase as stated in the text
  (e.g. "grants", "shall pay", "is located in", "terminates").
- quote: the SHORTEST verbatim phrase from the excerpt that states
  the fact (a clause, not a whole paragraph). Character-for-
  character; never paraphrase.
- subject_attested / object_attested: when the subject /
  object is stated in a normalized form that does NOT
  itself appear in the excerpt (a country named for a
  nationality adjective — "Germany" for "German"; a full
  name expanded from an abbreviation), copy here the
  exact words from the excerpt it was derived from,
  character-for-character. When the endpoint appears in
  the excerpt as stated, use "".
- Only facts the excerpt states. If none, return [].

Excerpt:
---
{text}
---"""

SLOT_FRAMING_DEFAULT = \
    "You extract clause facts from the excerpt below."
# Neutral core rules: value/quote discipline only. Category
# semantics (what a category's value looks like, yes/no-style
# categories, party handling) are the family's domain content,
# declared as slot_rules in its profile — CUAD's rules
# block, carried verbatim in profiles/cuad/setup_config.json,
# names its clause categories and is not core text.
SLOT_RULES_DEFAULT = """- "value": the shortest complete value, as written in the
  excerpt. For dates, as written.
- "quote": copy the EXACT verbatim text from the excerpt that states
  the fact (a phrase or one sentence). It must match the excerpt
  character-for-character. Never paraphrase the quote."""

SLOT_PROMPT = """{framing}

For each category below, if the excerpt STATES the fact, return one
entry. If a category is not stated in this excerpt, omit it.

Categories: {cats}

Rules:
{rules}
- Return ONLY a JSON array of {{"category": ..., "value": ...,
  "quote": ...}}. No other text.

Excerpt:
---
{text}
---"""

# Prepositions for the theme-presence guard.
PREPS = {"in", "on", "of", "for", "to", "with", "by", "at", "from",
         "under", "over", "between", "into", "upon", "against",
         "within", "without", "through", "per", "via", "as", "about"}

# The role-word guard's vocabulary is NOT core data ():
# role nouns are one domain's content, so the set lives in the
# declaring pack (packs/legal-contract/role_words.json) and
# reaches guard_triples as a parameter, resolved by
# profile_config from the run's config / binding pack. Core
# carries the guard MECHANISM (an endpoint that is a bare role
# word is not an identity), never the word list.


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _is_haiku55(model: str) -> bool:
    # The Haiku behavior switch is a capability declaration
    # now: the rule table lives once, in the
    # connection layer (connections.derive_capabilities).
    return connections.CAP_NO_TEMPERATURE in \
        connections.derive_capabilities(model)


def _chunks(text: str) -> list[tuple[int, str]]:
    return [(i // STEP, text[i:i + CHUNK])
            for i in range(0, len(text), STEP)]


def _chat(content: str, model: str, max_tokens: int,
          key: str) -> tuple[str, dict]:
    binding = connections.seat("proposer")
    if not key:
        raise connections.BindingKeyError(
            f"seat 'proposer' cannot run: its key variable "
            f"{binding.key_env} is not set")
    import requests
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
    }
    # provider.sort is an OpenRouter-dialect field (issue
    # #41): emitted only when the seat's dialect declares it.
    if binding.is_openrouter_dialect:
        payload["provider"] = {"sort": PROVIDER_SORT}
    if not binding.has_capability(
            connections.CAP_NO_TEMPERATURE, model):
        payload["temperature"] = 0.0
    r = requests.post(
        binding.chat_url,
        headers={"Authorization": f"Bearer {key}"},
        json=payload, timeout=180)
    r.raise_for_status()
    data = r.json()
    text = data["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```"
                         else lines[1:])
    return text, connections.normalize_usage(
        data.get("usage"), binding)


def _parse_array(content: str, salvage: bool) -> list:
    try:
        items = json.loads(content)
    except Exception:
        if not salvage:
            return []
        # Salvage a truncated array: keep complete objects only.
        cut = content.rfind("},")
        if cut <= 0:
            return []
        try:
            items = json.loads(content[:cut + 1] + "]")
        except Exception:
            return []
    return items if isinstance(items, list) else []


def _build_triple_prompt(chunk: str, guidance: str = "",
                         framing: str | None = None,
                         maxn: int | None = None,
                         mentions: bool = False,
                         declared: bool = False) -> str:
    """The proposer prompt for one chunk .

    The base is the domain-neutral TRIPLE_PROMPT template filled
    with the family's declared framing (profile data;
    pipeline/profile_config.proposer_framing) or, absent a
    declaration, TRIPLE_FRAMING_DEFAULT. With the framing the
    CUAD profile declares, the construction is byte-identical to
    the pre-framing prompt — pinned by
    the profile-wiring tests against the golden string
    captured before the guidance channel existed, and per family
    by the domain-boundary tests. With guidance (rendered
    from the corpus profile by
    pipeline/profile_config.derive_guidance), the guidance block is
    appended after the base prompt.

    `mentions` (the profile's mention-layer declaration,
    issue #54) selects TRIPLE_PROMPT_MENTION instead of the
    base template; the default path is untouched.
    """
    template = TRIPLE_PROMPT_MENTION if mentions else \
        TRIPLE_PROMPT_DECLARED if declared else TRIPLE_PROMPT
    base = template.format(
        framing=framing or TRIPLE_FRAMING_DEFAULT,
        maxn=MAX_PER_CHUNK if maxn is None else maxn, text=chunk)
    if not guidance:
        return base
    return base + "\n\n" + guidance


def _mention_from_item(it: dict, ep: str) -> dict:
    """One endpoint's mention record from a raw proposal
    item (issue #54): the flat subject_mention /
    subject_mention_type / subject_antecedent fields the
    mention prompt asks for, gathered into one dict.
    Missing or malformed fields stay missing — guard_triples
    decides what that means (an absent mention falls back
    to the legacy evidence path for that endpoint; a
    present-but-invalid one blocks by name), the parse
    layer invents nothing."""
    m: dict = {}
    span = it.get(f"{ep}_mention")
    if isinstance(span, str) and span.strip():
        m["span"] = span.strip()
    mtype = it.get(f"{ep}_mention_type")
    if isinstance(mtype, str) and mtype.strip():
        m["type"] = mtype.strip().lower()
    ant = it.get(f"{ep}_antecedent")
    if isinstance(ant, str) and ant.strip():
        m["antecedent"] = ant.strip()
    return m


def propose_triples_for_doc(doc_id: str, text: str, key: str,
                            model: str = DEFAULT_MODEL,
                            guidance: str = "",
                            framing: str | None = None,
                            max_per_chunk: int | None = None,
                            mentions: bool = False,
                            declared: bool = False) -> dict:
    """Raw triple proposals for one document (unguarded).

    Returns {items, cost, calls, errors, chunks, chunks_at_cap};
    each item carries the chunk it came from (+ its normalized
    form) for the guards. When the profile's guidance asks for
    typed endpoints, items also carry subject_type / object_type
    as proposed (validated against the profile's type
    vocabulary in guard_triples).

    max_per_chunk is the prompt's per-chunk proposal ceiling:
    None selects the core default (MAX_PER_CHUNK — the CUAD
    standalone recipe's value); a profile may declare its own
    (profile_config.proposer_max_per_chunk). chunks_at_cap
    counts chunks whose parsed proposal count reached the
    ceiling — the saturation signal: a chunk at the cap is
    indistinguishable from a chunk that merely had that many
    facts, unless it is counted (issue #52).

    mentions (the profile's mention-layer declaration,
    issue #54) selects the mention prompt variant; items then
    also carry a `mentions` dict ({subject, object} ->
    {span, type, antecedent?}) as proposed — span
    verification is guard_triples' job, as for typed
    endpoints above.
    """
    cap = MAX_PER_CHUNK if max_per_chunk is None else max_per_chunk
    tasks = _chunks(text)

    def work(task):
        idx, chunk = task
        try:
            content, u = _chat(
                _build_triple_prompt(chunk, guidance, framing,
                                     maxn=cap, mentions=mentions,
                                     declared=declared),
                model, 12000 if _is_haiku55(model) else 6000, key)
            items = _parse_array(content, salvage=True)
            err = None
        except Exception as e:
            items, u, err = [], {}, str(e)[:200]
        out = []
        for it in items:
            if isinstance(it, dict) and all(
                    it.get(k) for k in
                    ("subject", "relation", "object", "quote")):
                rec = {
                    "subject": str(it["subject"]).strip(),
                    "relation": str(it["relation"]).strip(),
                    "object": str(it["object"]).strip(),
                    "quote": str(it["quote"]).strip(),
                    "chunk_idx": idx, "chunk_norm": norm_ws(chunk)}
                # Typed endpoints, when the proposer emits
                # them (guidance asks for them). Carried verbatim
                # here; guard_triples validates them against the
                # profile's endpoint-type vocabulary.
                for tk in ("subject_type", "object_type"):
                    tv = it.get(tk)
                    if isinstance(tv, str) and tv.strip():
                        rec[tk] = tv.strip()
                if mentions:
                    rec["mentions"] = {
                        ep: _mention_from_item(it, ep)
                        for ep in ("subject", "object")}
                if declared:
                    # Declared derivation surfaces (tier T3):
                    # carried verbatim; guard_triples checks
                    # their attestation, the judge decides
                    # the derivation. Absent/empty stays
                    # absent — the parse layer invents
                    # nothing.
                    for ep in ("subject", "object"):
                        av = it.get(f"{ep}_attested")
                        if isinstance(av, str) and av.strip():
                            rec[f"{ep}_attested"] = av.strip()
                out.append(rec)
        return out, u, err

    items, cost, calls, errors = [], 0.0, 0, 0
    chunks_at_cap = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for out, u, err in ex.map(work, tasks):
            items.extend(out)
            if len(out) >= cap:
                chunks_at_cap += 1
            cost = connections.add_cost(cost, u.get("cost"))
            calls += 1
            if err:
                errors += 1
    return {"items": items,
            "cost": None if cost is None else round(cost, 4),
            "calls": calls,
            "errors": errors, "proposed": len(items),
            "chunks": len(tasks), "chunks_at_cap": chunks_at_cap}


# ------------------------------------------------------------------
# Entity sweep (issue #58): deterministic census + per-anchor pass
# ------------------------------------------------------------------
# The per-anchor view's character ceiling: the mention
# sentences of one anchor, ± 1 neighbouring sentence,
# in document order. Windows beyond the ceiling are
# counted, never silently truncated.
SWEEP_CONTEXT_MAX = 3000

# Lowercase function words that may sit INSIDE a name
# surface, between two capitalized tokens ("Bank of
# America", "University of the Philippines", "Ludwig van
# Beethoven"). Language mechanics, not domain vocabulary:
# a connector never starts or ends a surface, and a
# surface containing one still has to attest verbatim
# (norm_ws containment) before it joins the roster.
_SWEEP_CONNECTORS = frozenset(
    {"of", "the", "and", "de", "del", "van", "von", "der"})

# Sentence-boundary discipline for the census and the
# anchor views: the pair census's abbreviation rule
# (pair_proposer.split_sentences_r3's guard, applied here
# unconditionally — a mention spanning a false boundary
# is exactly what the sweep exists to find). A token
# whose final period belongs to a dotted initialism
# ("U.S.") or a declared abbreviation word ("St.", "Dr.")
# does NOT end a sentence; any other token ending in
# [.!?] does, and a name sequence never crosses it.
_INITIALISM_RX = re.compile(r"(?<![A-Za-z])(?:[A-Za-z]\.)+")
_ABBREV_WORD_RX = re.compile(
    r"(?i)(?<![A-Za-z])(?:st|mt|dr|mr|mrs|ms|no|fig|vs|jr|sr)\.")
_ABBREV_MASK = "\x00"


def _mask_abbrev_periods(text: str) -> str:
    chars = list(text)
    for rx in (_INITIALISM_RX, _ABBREV_WORD_RX):
        for m in rx.finditer(text):
            for i in range(m.start(), m.end()):
                if chars[i] == ".":
                    chars[i] = _ABBREV_MASK
    return "".join(chars)


# Trailing closers that may follow a sentence mark
# (quotes, parens, brackets) — stripped before the mark
# is read. Typographic quotes written as escapes, per
# the lint standard for ambiguous punctuation.
_TRAILING_CLOSERS = frozenset(
    "\"'\u201d\u2019\u00bb)]}")


def _sentence_final(raw_tok: str) -> bool:
    """True when a raw whitespace token ends a sentence:
    it closes with [.!?] (after trailing closers) and the
    mark is not an abbreviation period."""
    t = raw_tok
    while t and t[-1] in _TRAILING_CLOSERS:
        t = t[:-1]
    if not t or t[-1] not in ".!?":
        return False
    if t[-1] != ".":
        return True
    return not any(m.end() == len(t)
                   for rx in (_INITIALISM_RX, _ABBREV_WORD_RX)
                   for m in rx.finditer(t))


def _token_core(tok: str) -> str:
    """A whitespace token stripped of its edge punctuation
    (internal apostrophes / hyphens / periods survive:
    "O'Brien", "Smith-Jones", "U.S"). Pure-punctuation
    tokens core to "" — they break name sequences, which
    is what separates "Paris. London" into two surfaces
    and keeps a comma-joined list from gluing."""
    i, j = 0, len(tok)
    while i < j and not tok[i].isalnum():
        i += 1
    while j > i and not tok[j - 1].isalnum():
        j -= 1
    return tok[i:j]


def _starts_capital(core: str) -> bool:
    """True when the token's first letter is uppercase."""
    for ch in core:
        if ch.isalpha():
            return ch.isupper()
    return False


_QUOTE_CHARS = frozenset("\"'\u201c\u201d\u2018\u2019\u00ab\u00bb")

# The double-quote family: the marks that open and
# close a quoted span. Single quotes are apostrophes
# as often as quotes and never move span state.
_DQUOTE_CHARS = frozenset("\"\u201c\u201d\u00ab\u00bb")

# Sentence-initial words that are not name material: at a
# sentence or paragraph boundary a capitalised token from
# this set is prose syntax, not the start of a name
# ("During Alger 's term" censuses Alger, not a person
# called During Alger).
_INITIAL_DROP = frozenset(
    {"during", "after", "before", "when", "while", "as",
     "in", "on", "at", "by", "for", "from", "with"})


def _is_quote_token(raw: str) -> bool:
    """True for a whitespace token made only of quote
    characters (the corpus tokenises quotes standalone:
    'Wilfried " Willi " Schneider')."""
    return bool(raw) and all(ch in _QUOTE_CHARS
                             for ch in raw)


def _bridged_form(text: str) -> str:
    """The document's bridged form, for attesting a name
    a nickname's quotes interrupt: built from token
    CORES, not raw tokens. Dropping only standalone
    quote tokens left the marks embedded whenever the
    text attaches them to the words they wrap — raw
    text's convention ('Michael John "Mike" Padden') —
    and the bridged form of exactly the names it exists
    for never matched, so the nickname branch could not
    fire and the full name censused nowhere."""
    return norm_ws(" ".join(
        c for c in (_token_core(t) for t in text.split())
        if c))


def census_entities(text: str) -> dict:
    """The deterministic census roster for one document.

    Extracts maximal capitalized-token sequences (a
    lowercase connector run may bridge two capitalized
    tokens; a sequence never crosses a sentence boundary
    — abbreviation periods excepted, per the boundary
    rule above — and never crosses a paragraph break: a
    title line and the body are different mention sites,
    and gluing them manufactures names that occur
    nowhere). Three surface disciplines keep the roster
    to names the document actually uses:

    - a leading article (The / A / An) is dropped when a
      two-or-more-token name follows it, so a
      sentence-initial "The Bank of America" censuses as
      "Bank of America", while a short name keeps its
      article: "The Hague"; a leading sentence-initial
      prose word (During / After / In / ...) at a
      sentence or paragraph boundary is dropped the
      same way.
    - a quote-wrapped token inside a sequence is a
      nickname insertion (Wilfried "Willi" Schneider):
      quote tokens bridge the sequence instead of
      breaking it, the wrapped token is excluded from
      the surface when two or more unwrapped tokens
      remain, and its normalized form is recorded as an
      alias of the surface. The entity is attested by
      its full bridged form, which the alias list also
      carries. The wrapped token must be interior: a
      quote that opens the sequence marks a quoted
      title ("Suffer the Children"), whose first token
      is part of the name and is never excluded.
    - a surface that differs from another roster
      surface only by one token, where that token is a
      recorded nickname alias of the other surface
      (Willi Schneider vs Wilfried Schneider), folds
      into the other surface as an alias form.

    Each surface is kept only if attested in the
    document (norm_ws containment, or the bridged-form
    attestation above); a surface the normalization
    cannot find (a name whose internal punctuation the
    joining flattened, e.g. "U.S. Army" -> "u.s army")
    is dropped AND counted, never silently lost.
    Dedupes by normalized surface, first surface form
    wins. Pure function of the text: identical run to
    run, no API, so the roster's recall against any
    gold name set is measurable offline (the sweep's
    pre-flight gate).

    Returns {entities: [{surface, norm, aliases}],
    extracted, unattested}: `extracted` counts every
    sequence the scan produced, `unattested` the ones
    the containment test dropped. `aliases` are
    normalized alternate mention forms of the entity
    (nickname tokens, bridged full forms, folded alias
    forms) — mention forms, never separate entities.
    """
    doc_norm = norm_ws(text)
    doc_bridged = _bridged_form(text)
    surfaces: list[tuple[str, list[str], bool]] = []
    for par in re.split(r"\n\s*\n", text):
        raw_tokens = par.split()
        merged: list[str] = []
        ti = 0
        while ti < len(raw_tokens):
            if ti + 2 < len(raw_tokens) + 1 \
                    and ti + 2 <= len(raw_tokens) - 1 \
                    and raw_tokens[ti + 1] in ("-", "\u2013") \
                    and _token_core(raw_tokens[ti]) \
                    and _starts_capital(
                        _token_core(raw_tokens[ti])) \
                    and _token_core(raw_tokens[ti + 2]) \
                    and _starts_capital(
                        _token_core(raw_tokens[ti + 2])):
                merged.append(
                    _token_core(raw_tokens[ti]) + "-"
                    + _token_core(raw_tokens[ti + 2]))
                ti += 3
                continue
            merged.append(raw_tokens[ti])
            ti += 1
        raw_tokens = merged
        tokens = [_token_core(t) for t in raw_tokens]
        quoted: list[bool] = []
        quote_open = False
        for rt in raw_tokens:
            if _is_quote_token(rt):
                quote_open = not quote_open
                quoted.append(False)
                continue
            # Attached quote marks: raw text glues them
            # to the words they wrap ('"Mike" Padden'),
            # and tokenized-then-joined text can glue a
            # closing mark to the NEXT word ('"Mike
            # "Padden'). A token is quoted when its
            # first letter sits inside a double-quote
            # span: a head mark opens the span when it
            # is closed and closes it when it is open,
            # and the state flips on every double-quote
            # mark the token carries.
            marks = sum(1 for ch in rt
                        if ch in _DQUOTE_CHARS)
            head_mark = bool(rt) and rt[0] in _DQUOTE_CHARS
            quoted.append(not quote_open if head_mark
                          else quote_open)
            if marks % 2 == 1:
                quote_open = not quote_open
        # Sentence-boundary map: a token starts at a
        # boundary at the paragraph start or right after
        # a sentence-final raw token (quote tokens are
        # transparent to the look-back).
        boundary: list[bool] = []
        last_raw: str | None = None
        for rt in raw_tokens:
            boundary.append(
                last_raw is None or _sentence_final(last_raw))
            if not _is_quote_token(rt):
                last_raw = rt
        i, n = 0, len(tokens)
        while i < n:
            if _is_quote_token(raw_tokens[i]):
                i += 1
                continue
            if tokens[i] and _starts_capital(tokens[i]):
                seq: list[int] = [i]
                j = i + 1
                while j < n:
                    if _is_quote_token(raw_tokens[j]):
                        j += 1
                        continue
                    # A name sequence never crosses a
                    # sentence boundary: the token before
                    # the next one must not be
                    # sentence-final.
                    prev = j - 1
                    while _is_quote_token(raw_tokens[prev]):
                        prev -= 1
                    if _sentence_final(raw_tokens[prev]):
                        break
                    t = tokens[j]
                    if t and _starts_capital(t):
                        seq.append(j)
                        j += 1
                        continue
                    k = j
                    while k < n and tokens[k] \
                            and tokens[k].lower() \
                            in _SWEEP_CONNECTORS:
                        k += 1
                    if k > j and k < n and tokens[k] \
                            and _starts_capital(tokens[k]):
                        seq.extend(range(j, k + 1))
                        j = k + 1
                        continue
                    break
                cores = [tokens[x] for x in seq]
                if len(cores) >= 3 \
                        and cores[0].lower() in ("the", "a", "an"):
                    seq = seq[1:]
                    cores = cores[1:]
                elif len(cores) >= 2 and boundary[i] \
                        and cores[0].lower() in _INITIAL_DROP:
                    seq = seq[1:]
                    cores = cores[1:]
                plain = [x for x in seq if not quoted[x]]
                aliases: list[str] = []
                attested = False
                # The nickname exclusion requires a plain
                # name token BEFORE the quoted token: the
                # nickname pattern is Name "Nick" Name. A
                # quote that OPENs the sequence marks a
                # quoted title ("Suffer the Children",
                # "El Regalo Más Grande"), whose first
                # token is part of the name — excluding it
                # censused the title as a fragment ("the
                # Children") and demoted the full title to
                # an alias, and resolution then rewrote
                # correct endpoints down to the fragment
                # (scale-test pilot, 2026-10-29).
                if 2 <= len(plain) < len(seq) \
                        and not quoted[seq[0]]:
                    aliases = [norm_ws(tokens[x])
                               for x in seq if quoted[x]]
                    full = " ".join(cores)
                    if norm_ws(full) in doc_bridged:
                        aliases.append(norm_ws(full))
                        seq = plain
                        cores = [tokens[x] for x in seq]
                        attested = True
                surface = " ".join(cores)
                if "-" in surface:
                    spaced = norm_ws(
                        surface.replace("-", " - "))
                    if spaced in doc_norm:
                        aliases.append(spaced)
                        attested = True
                surfaces.append(
                    (surface, aliases, attested))
                i = j
            else:
                i += 1
    entities, seen, unattested = [], set(), 0
    for s, aliases, attested in surfaces:
        sn = norm_ws(s)
        if not attested and sn not in doc_norm:
            unattested += 1
            continue
        if sn in seen:
            continue
        seen.add(sn)
        entities.append({"surface": s, "norm": sn,
                         "aliases": sorted(set(aliases))})
    # Fold alias forms: a surface differing from another
    # in exactly one token, where its token there is a
    # nickname alias of the other surface, is a mention
    # form of that entity, not an entity of its own.
    alias_of: dict[str, str] = {}
    for e in entities:
        for a in e["aliases"]:
            alias_of.setdefault(a, e["norm"])
    by_norm = {e["norm"]: e for e in entities}
    folded: set[str] = set()
    for e in entities:
        toks = e["norm"].split()
        for pos, t in enumerate(toks):
            canon = alias_of.get(t)
            if canon is None or canon == e["norm"] \
                    or canon not in by_norm:
                continue
            ctoks = canon.split()
            if len(ctoks) != len(toks):
                continue
            cand = [*toks[:pos], ctoks[pos], *toks[pos + 1:]]
            if " ".join(cand) == canon:
                by_norm[canon]["aliases"] = sorted(set(
                    by_norm[canon]["aliases"])
                    | set(e["aliases"]) | {e["norm"]})
                folded.add(e["norm"])
                break
    entities = [e for e in entities
                if e["norm"] not in folded]
    return {"entities": entities,
            "extracted": len(surfaces),
            "unattested": unattested}


# Absorption may complete a name with name material
# only. An extra token that is a function word, a prose
# opener, or a role/title word marks the longer surface
# as a phrase that merely CONTAINS the fragment — a
# title phrase ("Attorney General of Afghanistan"), a
# work title containing a place ("Battle of Los
# Angeles"), a coordination ("South Plainfield and the
# Cedar Brook"), a prose-glued surface ("Similarly
# Amdír", "... Education To") — never the fragment's
# own entity under a fuller name. Every measured
# absorption whose extra tokens contained such a word
# was damage (scale-test pilot, 2026-10-29); the
# completions that survive the veto — given names,
# compound parts, generational suffixes — carry none.
_ABSORB_VETO = frozenset({
    # function words
    "the", "a", "an", "and", "or", "to", "of", "in",
    "on", "at", "by", "for", "with", "from", "as",
    "into", "upon", "per", "via",
    # prose openers / connectives
    "similarly", "through", "however", "meanwhile",
    "thus", "hence", "then", "also", "once",
    # role / title words
    "attorney", "general", "prime", "minister",
    "president", "king", "queen", "prince", "princess",
    "secretary", "director", "chairman", "chairwoman",
    "governor", "mayor", "chief", "justice", "captain",
    "professor", "doctor", "bishop", "pope", "emperor",
    "empress", "duke", "duchess", "lord", "lady",
    "judge", "senator", "ambassador", "chancellor",
    "premier",
}) | _INITIAL_DROP


# --- Structural absorption vetoes -------------------
# Rule 4 below rewrites a mention to a longer census
# surface that merely CONTAINS it. Containment alone
# does not make the longer surface the same entity:
# it may be a typed entity built around the mention
# (a university in Chicago, a country's senate), two
# mentions glued across a lost boundary (an ALL-CAPS
# contract title fused to its subtitle, a verse line's
# first word), a designation phrase, or a distinct
# entity sharing a name token (the company 'Cimatti'
# is not its founder). The vetoes in _absorption_veto
# refuse such targets on structural and evidentiary
# grounds — the shape of the completion, and whether
# the document itself supplies identity evidence for
# the rewrite — never on a list of known-bad surfaces
# or of type-denoting words: a hand-written word list
# is a per-language artifact, and this layer's rules
# must be statements about names, not about English.

_GEN_SUFFIXES = frozenset(
    {"jr", "sr", "ii", "iii", "iv", "esq"})


def _seq_of(norm: str) -> list:
    seq = [t for t in re.split(r"[\s-]+", norm) if t]
    if seq and seq[0] in ("the", "a", "an"):
        seq = seq[1:]
    return seq


def _align(ep_seq: list, cand_seq: list):
    """Subsequence alignment of the fragment's tokens
    in the candidate's: returns (prepend, infix,
    trailing) extra tokens, or None when the fragment's
    tokens do not appear in order."""
    idx: list[int] = []
    i = 0
    for j, t in enumerate(cand_seq):
        if i < len(ep_seq) and t == ep_seq[i]:
            idx.append(j)
            i += 1
    if i < len(ep_seq) or not idx:
        return None
    first, last = idx[0], idx[-1]
    matched = set(idx)
    return (cand_seq[:first],
            [cand_seq[k] for k in range(first + 1, last)
             if k not in matched],
            cand_seq[last + 1:])


def _contains_seq(hay: list, needle: list) -> int:
    """Contiguous occurrences of needle in hay."""
    if not needle or len(needle) > len(hay):
        return 0
    return sum(1 for i in range(len(hay) - len(needle) + 1)
               if hay[i:i + len(needle)] == needle)


def _absorption_veto(ep_norm: str, cand: str,
                     index: tuple,
                     ep_initialism: bool) -> bool:
    """True when a structural veto refuses the candidate
    as an absorption target for the fragment. Each veto
    reads the SHAPE of the completion, not the identity
    of the names involved."""
    by_norm = index[0]
    # An initialism is a complete referring expression,
    # not a fragment of a longer name: 'ASCAP' does not
    # absorb into a phrase that happens to contain it
    # ('Joining ASCAP').
    if ep_initialism:
        return True
    ep_seq = _seq_of(ep_norm)
    cand_seq = _seq_of(cand)
    extras = set(cand_seq) - set(ep_seq)
    # An extra token the document writes in all caps is
    # a designator (an initialism or a defined-term
    # shout), not name material: 'FK Fyllingsdalen' is
    # the club designation, and an ALL-CAPS contract
    # heading fused to its own first line
    # ('THIS COOPERATION AGREEMENT' above 'Cooperation
    # Agreement') is a layout artifact, not a party.
    raw_toks = [t for t in re.split(
        r"[\s-]+", by_norm[cand]["surface"]) if t]
    for raw in raw_toks:
        if raw.lower() in extras and len(raw) >= 2 \
                and raw.isupper():
            return True
    # A second, complete entity name sitting in the
    # candidate's infix/trailing region means the
    # surface is two mentions glued across a lost
    # boundary ('United States Husain Haqqani' — the
    # country and its ambassador), and a candidate that
    # contains the fragment twice is a coordination or
    # list surface, not a name.
    if _contains_seq(cand_seq, ep_seq) >= 2:
        return True
    al = _align(ep_seq, cand_seq)
    if al is not None:
        first = len(al[0])
        last = len(cand_seq) - len(al[2])
        for other in by_norm:
            if other in (cand, ep_norm):
                continue
            o_seq = _seq_of(other)
            if len(o_seq) < 2:
                continue
            for i in range(
                    len(cand_seq) - len(o_seq) + 1):
                if cand_seq[i:i + len(o_seq)] == o_seq \
                        and i >= first \
                        and i + len(o_seq) > last:
                    return True
    # A mention may be rewritten to a longer name only
    # on document-supplied identity evidence. Bare
    # containment is not evidence — the longer surface
    # may be a typed entity built around the mention
    # (a university in Chicago, a country's senate, a
    # park around a river), a distinct entity sharing
    # a name token (the company 'Cimatti' is not its
    # founder 'Marco Cimatti'), or prose glued to the
    # name at a lost boundary (a verse line's
    # 'Methinks Ulysses', a show's name around a
    # country in 'Voice Romania'). The one exception
    # is the mention's only anchor (see _anchored):
    # an infix completion of a mention that occurs
    # nowhere outside the candidate, where refusing
    # would protect nothing and cost the fact.
    if len(cand_seq) > len(ep_seq) \
            and not _rewrite_evidence(
                ep_seq, cand, index) \
            and not _anchored(
                ep_norm, ep_seq, cand, cand_seq, index):
        return True
    return False


def _anchored(ep_norm: str, ep_seq: list, cand: str,
              cand_seq: list, index: tuple) -> bool:
    """The mention's only anchor: the fragment never
    occurs in the document outside the candidate, and
    the completion is INFIX only — the extra tokens
    sit strictly between the fragment's own first and
    last tokens (a seat-written 'Jared Allen' for
    'Jared Scot Allen': the middle name is the name's
    own material). Leading completions are where glue
    and specifiers live ('Voice Romania' is a show,
    not the country; 'Joining SC Freiburg' is a verb
    phrase), and trailing completions are where
    derived entities live (a country's senate) — those
    always need evidence. Refusing an infix rewrite
    of an anchorless mention would not protect a
    competing attested name — there is none — it
    would only leave the mention unattested and cost
    the fact."""
    al = _align(ep_seq, cand_seq)
    if al is None or al[0] or al[2] or not al[1]:
        return False
    return _outside_count(index[3], ep_norm, cand) == 0


def _rewrite_evidence(ep_seq: list, cand: str,
                      index: tuple) -> bool:
    """Document-supplied evidence that the longer
    candidate is the mention's own fuller name: the
    completion is a bare generational suffix, the
    fragment is a hyphen component of the target, the
    target's entity records a longer alias form
    containing the fragment (a nickname-bridged full
    form), or the document attests the longer name
    more than once."""
    by_norm, alias_of, bridged = index[0], index[1], \
        index[3]
    cand_seq = _seq_of(cand)
    al = _align(ep_seq, cand_seq)
    if al is not None and al[0] == [] and al[1] == [] \
            and al[2] and all(
                t in _GEN_SUFFIXES for t in al[2]):
        return True
    surf = by_norm[cand]["surface"]
    if any(len(tok.split("-")) >= 2
           and ep_seq[-1] in tok.split("-")
           for tok in surf.lower().split()):
        return True
    for a, canon in alias_of.items():
        if canon != cand:
            continue
        a_seq = _seq_of(a)
        if len(a_seq) > len(ep_seq) and _contains_seq(
                a_seq, ep_seq):
            return True
    return len(_phrase_spans(bridged, cand)) >= 2


def _identity_index(doc_text: str) -> tuple[dict, dict, set, str]:
    """The identity index guard_triples resolves against:
    (by_norm, alias_of, maximals, bridged) from the
    document's census. by_norm maps a census norm to its
    entity; alias_of maps an alias norm (nickname,
    bridged full form, folded alias form) to the
    canonical entity norm; maximals are the entity norms
    whose token set is not a proper subset of another
    entity's — the only surfaces a fragment may resolve
    INTO. bridged is the document's bridged form, kept
    so absorption can check where mentions actually
    occur (see _resolve_endpoint rule 4)."""
    cen = census_entities(doc_text)
    by_norm = {e["norm"]: e for e in cen["entities"]}
    alias_of: dict[str, str] = {}
    for e in cen["entities"]:
        for a in e["aliases"]:
            alias_of.setdefault(a, e["norm"])
    toks = {n: _identity_tokens(n) for n in by_norm}
    maximals = {n for n in by_norm
                if not any(n != m and toks[n] < toks[m]
                           for m in by_norm)}
    # A surface made entirely of words the document
    # itself uses as ordinary lowercase prose, and
    # occurring only inside title-case paragraphs
    # (headings), is a prose run, not a name: headings
    # mint them wholesale ('Rectal Cancer Patients
    # Treated' — every content word a common word the
    # body uses lowercase). A name keeps at least one
    # token the document never uses as prose
    # ('Malagasy', 'Bosáková', 'Angeles'), or recurs in
    # ordinary paragraphs. Such surfaces stay in the
    # census but are demoted from maximals: never
    # absorption targets.
    prose = {c.lower() for raw in doc_text.split()
             if (c := _token_core(raw)).isalpha()
             and c.islower() and len(c) >= 2}
    paragraphs = re.split(r"\n\s*\n", doc_text)

    def _title_case(par: str) -> bool:
        # Title case capitalizes content words; function
        # words stay lowercase ('Analysis of INT 0144
        # Trial: Association of Polymorphisms with
        # Survival...').
        caps = [c for c in (_token_core(t)
                            for t in par.split())
                if c.isalpha()
                and c.lower() not in _ABSORB_VETO
                and c.lower() not in ("the", "a", "an")]
        return len(caps) >= 3 and all(
            c[:1].isupper() for c in caps)

    title_pars = [p for p in paragraphs if _title_case(p)]
    body_pars = [p for p in paragraphs if not _title_case(p)]

    def _is_prose_surface(norm: str) -> bool:
        content = [t for t in re.split(r"[\s-]+", norm)
                   if t and t not in _ABSORB_VETO
                   and t not in ("the", "a", "an")]
        if not content or not all(t in prose
                                  for t in content):
            return False
        surface = by_norm[norm]["surface"]
        return (any(surface in p for p in title_pars)
                and not any(surface in p
                            for p in body_pars))

    maximals = {n for n in maximals
                if not _is_prose_surface(n)}
    return by_norm, alias_of, maximals, _bridged_form(doc_text)


def _phrase_spans(bridged: str, phrase: str) -> list:
    """Token-boundary occurrences of a norm phrase in
    the bridged document text."""
    pat = re.compile(r"(?<![a-z0-9])" + re.escape(phrase)
                     + r"(?![a-z0-9])")
    return [(m.start(), m.end()) for m in pat.finditer(bridged)]


def _outside_count(bridged: str, ep_norm: str,
                   cand_norm: str) -> int:
    """How often the fragment occurs in the document at
    a position no occurrence of the candidate surface
    covers: its independent textual life — the document
    referring with the fragment on its own, outside the
    longer surface that contains it."""
    cand_spans = _phrase_spans(bridged, cand_norm)
    return sum(1 for s, e in _phrase_spans(bridged, ep_norm)
               if not any(cs <= s and e <= ce
                          for cs, ce in cand_spans))


def _is_code_fragment(tok: str) -> bool:
    """True for a token that is a designation code, not
    name material: it starts with a digit ('251a/a') or
    is a slash pair of single characters ('q/q', a
    genotype zygosity code). A name completing with
    such a token is a designation phrase, not a longer
    name."""
    if tok[:1].isdigit():
        return True
    return bool(re.fullmatch(r".?/.?", tok))


def _name_tokens(norm: str) -> set:
    """The token set identity resolution compares:
    whitespace- and hyphen-separated parts. A hyphenated
    compound (Bernheimer-Haus) is one census token but
    two name parts — a fragment may name either part."""
    return {t for t in re.split(r"[\s-]+", norm) if t}


def _identity_tokens(norm: str) -> set:
    """_name_tokens with a leading article removed:
    the / a / an at the head of a surface is syntax,
    not identity ("The Hague" is the entity an endpoint
    "Hague" names; "The Haus" must not outrank the
    compound it abbreviates)."""
    toks = _name_tokens(norm)
    first = norm.split(" ", 1)[0] if norm else ""
    if first in ("the", "a", "an"):
        toks.discard(first)
    return toks


def _resolve_endpoint(ep_norm: str, index: tuple,
                      anchor_norm: str | None = None,
                      ep_initialism: bool = False,
                      seat=None
                      ) -> tuple[str | None, list[str]]:
    """Resolve one proposed endpoint (norm_ws form) to
    the document's canonical surface for the entity it
    names. Returns (surface, candidates): surface is the
    canonical surface to substitute, or None when no
    resolution applies; a non-empty candidates list
    means the fragment is ambiguous between those
    canonical surfaces — ambiguity is reported, never
    guessed.

    Resolution order (deterministic, code-only):
    1. an exact census entity stands as-is, UNLESS a
       longer census name completes it in one of the two
       citation directions: prepending (given names
       complete a surname — "Schneider" for "Wilfried
       Schneider"; a trailing part is how a person is
       partially cited) or a generational suffix
       ("Paul Desmarais" → "Paul Desmarais Jr"). Those
       fall through to the fragment rules below. Any
       OTHER trailing extension names a derived, distinct
       entity ("Calgary" → "Calgary City Council": the
       city is not the council, and rewriting merged two
       entities onto one node), so the complete name
       stands;
    2. a recorded alias (nickname / bridged form /
       folded alias form) resolves to its entity;
    2b. a nickname-headed form — the entity's recorded
       nickname heading the entity's name tail (Mike
       Padden for Michael John Padden) — resolves to
       its entity, the same fold the census applies
       among its own surfaces;
    3. a fragment of the item's own anchor (the sweep
       asked about that entity) resolves to the anchor;
    4. a proper token-subset of exactly ONE maximal
       surface resolves to it; of several, it is
       ambiguous; of none, it stands as proposed (the
       proposer may name entities the census missed —
       absence from the roster is not proof of error).
       Absorption completes a name with name material
       only: a candidate whose extra tokens contain a
       function word, a prose opener, or a role/title
       word (_ABSORB_VETO) merely CONTAINS the fragment
       (a title phrase, a work title, a coordination, a
       prose-glued surface) and is never a target, and a
       candidate completed by a designation code
       ('251a/a', 'q/q') is a designation phrase, not a
       longer name. A surviving candidate must then
       pass the structural vetoes (_absorption_veto):
       an initialism never absorbs, an all-caps extra
       token is a designator, a second complete entity
       name inside the candidate marks a glued surface,
       and — the general rule — a rewrite needs
       document-supplied identity evidence (a recorded
       longer alias form, a generational suffix, a
       hyphen compound, or a second attestation of the
       longer name), unless it is the mention's only
       anchor (an infix completion of a mention that
       occurs nowhere outside the candidate — the
       middle name case: 'Jared Allen' for 'Jared Scot
       Allen'). The vetoes refuse only a UNIQUE
       candidate; a contested field stays ambiguous.
    """
    by_norm, alias_of, maximals, bridged = index
    if ep_norm in by_norm:
        ep_seq = [t for t in re.split(r"[\s-]+", ep_norm) if t]

        def _completion(longer: str) -> bool:
            seq = [t for t in re.split(r"[\s-]+", longer)
                   if t]
            if len(ep_seq) >= len(seq):
                return False
            if seq[-len(ep_seq):] == ep_seq:
                return True  # prepended completion
            return seq[:len(ep_seq)] == ep_seq and all(
                t in ("jr", "sr", "ii", "iii", "iv", "esq")
                for t in seq[len(ep_seq):])

        if not any(n != ep_norm and _completion(n)
                   for n in by_norm):
            return None, []
    if ep_norm in alias_of:
        return by_norm[alias_of[ep_norm]]["surface"], []
    # 2b. a nickname-headed form: the endpoint heads the
    # entity's name tail with the entity's recorded
    # nickname (Mike Padden for Michael John Padden) —
    # the same fold the census applies among its own
    # surfaces, extended to endpoints, which otherwise
    # attested nowhere and blocked. The nickname must be
    # this entity's recorded alias and the remaining
    # tokens exactly the entity's tail; anything else
    # falls through untouched.
    ep_seq = [t for t in re.split(r"[\s-]+", ep_norm) if t]
    if len(ep_seq) >= 2 and ep_seq[0] in alias_of:
        target = alias_of[ep_seq[0]]
        t_seq = [t for t in re.split(r"[\s-]+", target)
                 if t]
        if len(t_seq) > len(ep_seq) and \
                t_seq[len(t_seq) - len(ep_seq) + 1:] \
                == ep_seq[1:]:
            return by_norm[target]["surface"], []
    etoks = _identity_tokens(ep_norm)
    if anchor_norm and anchor_norm in by_norm \
            and etoks < _identity_tokens(anchor_norm):
        return by_norm[anchor_norm]["surface"], []
    toks = {n: _identity_tokens(n) for n in by_norm}
    # A coordination surface ("Vietnam and Cambodia") is
    # not an entity and never a resolution target: a
    # fragment naming one side of it must not be
    # rewritten into the pair (the pair asserts a fact
    # about both sides). One further eligibility rule,
    # from the BioRED pilot's damage classes: a
    # candidate whose extra tokens include a
    # designation code ('251a/a', 'q/q') is a
    # designation phrase ('VEGFR2 H472Q Q/Q'), not a
    # longer name. (The title-case prose runs that
    # pilot also produced — 'Rectal Cancer Patients
    # Treated' — never become targets at all: see the
    # prose-surface demotion in _identity_index.)
    def _article_variant(ep: str, cand: str) -> bool:
        # Same identity under two surface forms (a
        # leading article is the usual difference):
        # the form the document actually refers with
        # wins. An endpoint used independently at least
        # twice stands as proposed ('SRY', referred to
        # bare throughout, does not become 'The SRY');
        # a form occurring only inside the longer
        # surface, or once at its own definition,
        # still resolves to the census canonical
        # ('Seller' -> 'The Seller', the defined-term
        # convention; 'The Hague').
        if etoks != toks[cand] or ep == cand:
            return False
        return _outside_count(bridged, ep, cand) >= 2

    cand_norms = []
    for m in maximals:
        if not (etoks < toks[m]
                or (etoks == toks[m] and ep_norm != m)):
            continue
        if (toks[m] - etoks) & _ABSORB_VETO:
            continue
        if any(_is_code_fragment(t)
               for t in toks[m] - etoks):
            continue
        if _article_variant(ep_norm, m):
            continue
        if re.search(r"[A-Z][\w.]*\s+and\s+[A-Z]",
                     by_norm[m]["surface"]):
            continue
        cand_norms.append(m)
    # The rule chain's own outcome. Ambiguity is a
    # property of the document, not of the veto set:
    # the structural gates may only refuse a UNIQUE
    # candidate, never prune a contested field into a
    # false unique answer \u2014 a vetoed rival must not
    # hand the endpoint to the one bad candidate that
    # remains. So the veto runs only on a unique
    # survivor, and a contested field is reported, not
    # decided.
    rule_complete = None
    rule_ambiguous: list[str] = []
    if len(cand_norms) > 1:
        rule_ambiguous = cand_norms
    elif len(cand_norms) == 1:
        m = cand_norms[0]
        if not _absorption_veto(ep_norm, m, index,
                                ep_initialism):
            rule_complete = m
    if seat is None:
        if rule_ambiguous:
            return None, sorted(
                by_norm[m]["surface"]
                for m in rule_ambiguous)
        if rule_complete is not None:
            return by_norm[rule_complete]["surface"], []
        return None, []
    # The identity seat (identity-surgery program,
    # 2026-10-30): an injected judge consulted per
    # subset candidate with the code-computed feature
    # envelope. The seat answers True (complete),
    # False (stand), or None (no opinion \u2014 the rule
    # chain's outcome stands for that candidate). A
    # seat with an opinion on every candidate
    # therefore decides rule 4 outright; a seat
    # carrying frozen verdicts for judged points only
    # (the decisions-file seat) overrides exactly
    # those points and defers the rest. The seat can
    # only choose among census surfaces, never invent
    # one. The contested-field rule is unchanged:
    # more than one approved candidate is ambiguity,
    # reported, never guessed. Everything else in
    # this function (rules 1-3) and in the caller
    # (the invariants) applies exactly as without a
    # seat.
    ep_seq_s = _seq_of(ep_norm)
    approved = []
    for m in maximals:
        if not (etoks < toks[m]
                or (etoks == toks[m] and ep_norm != m)):
            continue
        cand_seq_s = _seq_of(m)
        al_s = _align(ep_seq_s, cand_seq_s)
        feats = {
            "fragment_surface": (
                by_norm[ep_norm]["surface"]
                if ep_norm in by_norm else ep_norm),
            "candidate_surface": by_norm[m]["surface"],
            "outside": _outside_count(bridged, ep_norm, m),
            "cand_attest": len(_phrase_spans(bridged, m)),
            "evidence": _rewrite_evidence(
                ep_seq_s, m, index),
            "anchored": _anchored(
                ep_norm, ep_seq_s, m, cand_seq_s, index),
            "align": (
                {"prepend": al_s[0], "infix": al_s[1],
                 "trailing": al_s[2]} if al_s else None),
        }
        verdict = seat(ep_norm, m, feats)
        if verdict is None:
            if m == rule_complete:
                approved.append(m)
        elif verdict:
            approved.append(m)
    if len(approved) > 1:
        return None, sorted(
            by_norm[m]["surface"] for m in approved)
    if len(approved) == 1:
        return by_norm[approved[0]]["surface"], []
    return None, []


def split_sentences(text: str) -> list[str]:
    """Deterministic sentence split for anchor views:
    boundaries after [.!?] followed by whitespace, with
    abbreviation periods masked first (the pair census's
    discipline, without its title-block special case and
    without any length filter — a short sentence can
    carry a mention)."""
    masked = _mask_abbrev_periods(text)
    return [p.strip().replace(_ABBREV_MASK, ".") for p in
            re.split(r"(?<=[.!?])\s+", masked) if p.strip()]


def anchor_contexts(text: str, anchor: str,
                    aliases: list[str] | None = None) -> dict:
    """One anchor's view: the sentences containing a
    census-attested mention of the anchor (normalized
    substring; any of the anchor's census aliases — a
    nickname or bridged form the canonical surface's
    own substring cannot find — also counts), each
    with ± 1 neighbouring sentence, in document order,
    total ≤ SWEEP_CONTEXT_MAX chars.

    Contiguous kept sentences merge into runs; a run that
    would push the view past the ceiling is left out and
    counted (`windows_dropped`) — never silently
    truncated mid-run. Returns {context, mention_sentences,
    windows, windows_dropped}."""
    sents = split_sentences(text)
    forms = [norm_ws(anchor), *list(aliases or [])]
    hits = [i for i, s in enumerate(sents)
            if any(f in norm_ws(s) for f in forms)]
    keep: set[int] = set()
    for i in hits:
        keep.update((i - 1, i, i + 1))
    runs: list[list[int]] = []
    for i in sorted(x for x in keep if 0 <= x < len(sents)):
        if runs and i == runs[-1][-1] + 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    blocks, dropped, total = [], 0, 0
    for run in runs:
        block = " ".join(sents[x] for x in run)
        add = len(block) + (1 if blocks else 0)
        if total + add > SWEEP_CONTEXT_MAX:
            dropped += 1
            continue
        blocks.append(block)
        total += add
    return {"context": "\n".join(blocks),
            "mention_sentences": len(hits),
            "windows": len(blocks),
            "windows_dropped": dropped}


def _build_sweep_prompt(context: str, anchor: str,
                        guidance: str = "",
                        framing: str | None = None,
                        maxn: int | None = None,
                        mentions: bool = False) -> str:
    """The sweep prompt for one anchor: the chunk prompt
    built by _build_triple_prompt (the base template stays
    untouched), over the anchor's view instead of a chunk,
    plus two added rules: the anchor rule (an endpoint of
    every triple must be the anchor entity, named as it
    appears in the text) and — cut 2 — the evidence rule
    (every triple must carry its evidence span, and the
    span is verified; a triple without a verifying span
    is discarded)."""
    base = _build_triple_prompt(context, guidance, framing,
                                maxn=maxn, mentions=mentions)
    return base + (
        f"\n\nAdditional rule for this pass: one endpoint "
        f"of every triple — the subject or the object — "
        f"must be the entity \"{anchor}\", named exactly "
        f"as it appears in the excerpt."
        f"\n\nEvidence rule for this pass: every triple "
        f"must carry its evidence span in \"quote\" — the "
        f"verbatim phrase from the excerpt that states "
        f"the fact, copied character-for-character. The "
        f"span is verified against the document text: a "
        f"triple whose quote is missing, or whose text "
        f"does not appear in the document, is discarded.")


def propose_sweep_for_doc(doc_id: str, text: str,
                          anchors: list[dict], key: str,
                          model: str = DEFAULT_MODEL,
                          guidance: str = "",
                          framing: str | None = None,
                          max_per_chunk: int | None = None,
                          mentions: bool = False) -> dict:
    """Per-anchor proposals for one document (unguarded).

    One proposer-seat call per anchor over its mention
    view (anchor_contexts), same decoding and per-call
    ceiling as the chunk pass. Items are parsed with the
    chunk pass's discipline and carry the anchor they
    were asked under (`anchor`, `anchor_norm`) plus the
    view as their chunk fields, so guard_triples' chunk
    attestation rung reads the view.

    Cut 2 — the evidence void, checked FIRST, before
    the anchor test: an item whose evidence span (its
    `quote`) is missing — the field absent, empty, or
    not a string — or whose span fails verification
    (`norm_ws(quote)` not a substring of the document's
    `norm_ws` — the FULL document text, strict
    whitespace-normalized verbatim; no
    punctuation-insensitive rung softens it) is VOID:
    counted (`evidence_missing`) and dropped here,
    never returned, never blocked into the record. An
    item failing both the evidence test and the anchor
    test counts as evidence_missing — the order is
    fixed so the accounting is deterministic.

    An item whose NEITHER endpoint is its anchor is
    contract drift: it is counted (`off_anchor`) and
    dropped here — never returned, never blocked into
    the record. An anchor with no view (no mention
    sentence survived the ceiling) is skipped and counted
    (`anchors_skipped`), never called.

    Returns {items, cost, calls, errors, proposed,
    off_anchor, evidence_missing, anchors_asked,
    anchors_skipped, windows_dropped}; `proposed` counts
    every parsed item carrying the triple fields, so the
    pass's attrition is fully accounted by
    proposed = items + off_anchor + evidence_missing.
    (Items lacking a triple field entirely are not
    triples — malformed, invisible, exactly as on the
    chunk path.)
    """
    cap = MAX_PER_CHUNK if max_per_chunk is None \
        else max_per_chunk
    doc_norm = norm_ws(text)

    def work(task):
        _idx, anchor = task
        view = anchor_contexts(text, anchor["surface"],
                               anchor.get("aliases"))
        if not view["context"]:
            return [], {}, None, 0, 0, \
                view["windows_dropped"], True
        try:
            content, u = _chat(
                _build_sweep_prompt(view["context"],
                                    anchor["surface"],
                                    guidance, framing,
                                    maxn=cap, mentions=mentions),
                model, 12000 if _is_haiku55(model) else 6000, key)
            items = _parse_array(content, salvage=True)
            err = None
        except Exception as e:
            items, u, err = [], {}, str(e)[:200]
        out, off, ev = [], 0, 0
        for it in items:
            if not (isinstance(it, dict) and all(
                    it.get(k) for k in
                    ("subject", "relation", "object"))):
                continue
            # The evidence void (cut 2): the span must be
            # present and verify against the full document
            # text. A missing or unverifiable span voids
            # the proposal here — it never reaches the
            # anchor test or the guards.
            q = it.get("quote")
            qn = norm_ws(q) if isinstance(q, str) else ""
            if not qn or qn not in doc_norm:
                ev += 1
                continue
            rec = {
                "subject": str(it["subject"]).strip(),
                "relation": str(it["relation"]).strip(),
                "object": str(it["object"]).strip(),
                "quote": q.strip(),
                "chunk_idx": -1,
                "chunk_norm": norm_ws(view["context"]),
                "anchor": anchor["surface"],
                "anchor_norm": anchor["norm"]}
            for tk in ("subject_type", "object_type"):
                tv = it.get(tk)
                if isinstance(tv, str) and tv.strip():
                    rec[tk] = tv.strip()
            if mentions:
                rec["mentions"] = {
                    ep: _mention_from_item(it, ep)
                    for ep in ("subject", "object")}
            # On-anchor: an endpoint names the anchor —
            # equality or containment either way (the
            # mention layer's naming discipline: a
            # fuller or shorter form of the same name
            # still names it; an unrelated endpoint
            # does not).
            ends = (norm_ws(rec["subject"]),
                    norm_ws(rec["object"]))
            if not any(e == anchor["norm"]
                       or e in anchor["norm"]
                       or anchor["norm"] in e
                       for e in ends):
                off += 1
                continue
            out.append(rec)
        return out, u, err, off, ev, \
            view["windows_dropped"], False

    items, cost, calls, errors = [], 0.0, 0, 0
    off_anchor = evidence_missing = skipped = wdropped = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for out, u, err, off, ev, wd, skip in ex.map(
                work, list(enumerate(anchors))):
            items.extend(out)
            off_anchor += off
            evidence_missing += ev
            wdropped += wd
            if skip:
                skipped += 1
                continue
            cost = connections.add_cost(cost, u.get("cost"))
            calls += 1
            if err:
                errors += 1
    # `proposed` counts every parsed item carrying the
    # triple fields — the items that reach the guards,
    # plus both proposal-time drops, which also carried
    # them: proposed = items + off_anchor +
    # evidence_missing.
    return {"items": items,
            "cost": None if cost is None else round(cost, 4),
            "calls": calls,
            "errors": errors,
            "proposed": len(items) + off_anchor
            + evidence_missing,
            "off_anchor": off_anchor,
            "evidence_missing": evidence_missing,
            "anchors_asked": len(anchors) - skipped,
            "anchors_skipped": skipped,
            "windows_dropped": wdropped}


def propose_slots_for_doc(doc_id: str, text: str,
                          categories: list[str], key: str,
                          model: str = DEFAULT_MODEL,
                          framing: str | None = None,
                          rules: str | None = None) -> dict:
    """Raw clause-slot fills for one document (unguarded).

    The prompt is the domain-neutral SLOT_PROMPT template filled
    with the family's declared framing + rules block (profile data;
    pipeline/profile_config.slot_framing / slot_rules) or the
    neutral core defaults. With the framing and rules the CUAD
    profile declares, the construction is byte-identical to the
    pre-framing prompt (pinned by the domain-boundary tests)."""
    tasks = _chunks(text)
    cats = ", ".join(categories)

    def work(task):
        _idx, chunk = task
        try:
            content, u = _chat(
                SLOT_PROMPT.format(
                    framing=framing or SLOT_FRAMING_DEFAULT,
                    cats=cats, rules=rules or SLOT_RULES_DEFAULT,
                    text=chunk),
                model, 4000 if _is_haiku55(model) else 1500, key)
            items = _parse_array(content, salvage=False)
            err = None
        except Exception as e:
            items, u, err = [], {}, str(e)[:200]
        out = []
        for it in items:
            if isinstance(it, dict) and it.get("category") in categories \
                    and it.get("value") and it.get("quote"):
                out.append({"category": it["category"],
                            "value": str(it["value"]).strip(),
                            "quote": str(it["quote"]).strip()})
        return out, u, err

    fills, cost, calls, errors = [], 0.0, 0, 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for out, u, err in ex.map(work, tasks):
            fills.extend(out)
            cost = connections.add_cost(cost, u.get("cost"))
            calls += 1
            if err:
                errors += 1
    return {"fills": fills,
            "cost": None if cost is None else round(cost, 4),
            "calls": calls,
            "errors": errors, "proposed": len(fills)}


# ------------------------------------------------------------------
# Evidence windows
# ------------------------------------------------------------------
def quote_window(text: str, quote: str, half: int = WINDOW) -> str:
    """quote ± half chars of source text (the audit-standard judge
    context). Locates the quote raw, then via a whitespace-normalized
    index map; falls back to the quote itself."""
    pos = text.find(quote)
    if pos >= 0:
        return text[max(0, pos - half): pos + len(quote) + half]
    norm, idx = [], []
    prev_ws = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if not prev_ws:
                norm.append(" ")
                idx.append(i)
            prev_ws = True
        else:
            norm.append(ch)
            idx.append(i)
            prev_ws = False
    nq = " ".join(quote.split())
    npos = "".join(norm).find(nq)
    if npos < 0 or not idx:
        return quote
    start = idx[npos]
    end = idx[min(npos + len(nq) - 1, len(idx) - 1)] + 1
    return text[max(0, start - half): end + half]


# ------------------------------------------------------------------
# Deterministic guards
# ------------------------------------------------------------------
def _verify_mentions(it: dict, qn: str, doc_norm: str,
                     sn: str, on: str) -> tuple[dict, list] | str:
    """Verify one item's mention records (issue #54).

    Returns (cleaned, fallback) — the verified mention
    records by endpoint, and the endpoints whose mention
    data was ABSENT — or the block reason string when a
    DECLARED mention is invalid. The policy (cut 2) is
    per-endpoint and additive:

    - Absent: the endpoint carries no mention record, or
      the record has no usable span (the proposer did not
      answer for that endpoint — the measured Phase 2
      shape: the mention fields were simply omitted). The
      endpoint falls back to the legacy evidence path:
      the item is judged on its quote exactly as without
      the layer, and the fallback is counted by endpoint
      in the batch accounting.
    - Present but invalid blocks the item by name, with
      the quote's own verbatim discipline:
      - mention_missing: a span was declared but its type
        is missing or outside MENTION_TYPES — a malformed
        answer, not an absent one.
      - mention_span: the mention span is not a verbatim
        (whitespace-normalized) substring of the item's
        quote — the mention must appear in the quote, that
        is where the judge will look for it.
      - mention_antecedent: a non-named mention
        (pronominal / descriptive) without a usable
        resolution: the antecedent is absent, is not a
        verbatim substring of the document, or does not
        name the endpoint (neither surface contains the
        other — "Paul Desmarais" for "Paul Desmarais Jr."
        passes; an unrelated phrase does not).

    A "named" mention needs no antecedent: the span in the
    quote IS the attestation, judged as before.
    """
    raw = it.get("mentions")
    cleaned: dict = {}
    fallback: list = []
    for ep, surf_n in (("subject", sn), ("object", on)):
        m = raw.get(ep) if isinstance(raw, dict) else None
        if not isinstance(m, dict):
            fallback.append(ep)
            continue
        span = m.get("span")
        if not isinstance(span, str) or not span.strip():
            fallback.append(ep)
            continue
        mtype = m.get("type")
        if mtype not in MENTION_TYPES:
            return "mention_missing"
        if norm_ws(span) not in qn:
            return "mention_span"
        rec = {"span": span.strip(), "type": mtype}
        if mtype != "named":
            ant = m.get("antecedent")
            if not isinstance(ant, str) or not ant.strip():
                return "mention_antecedent"
            ant_n = norm_ws(ant)
            if ant_n not in doc_norm:
                return "mention_antecedent"
            if not (ant_n == surf_n or ant_n in surf_n
                    or surf_n in ant_n):
                return "mention_antecedent"
            rec["antecedent"] = ant.strip()
        cleaned[ep] = rec
    return cleaned, fallback


_PUNCT_SPACE_RX = re.compile(r"\s*([^\w\s])\s*")


def punct_collapse(s: str) -> str:
    """Remove whitespace adjacent to punctuation. Tokenized
    corpus texts space their punctuation ('Mediaș ,
    Transylvania'); a quote transcribed with natural
    punctuation ('Mediaș, Transylvania') is the document's
    own text modulo that spacing convention. Used only by
    the opt-in G1 relaxation in guard_triples."""
    return _PUNCT_SPACE_RX.sub(r"\1", s)


# ------------------------------------------------------------------
# Document registry (upstream attestation program, 2026-10-29)
# ------------------------------------------------------------------
# A document stipulates its own aliases: defined terms in
# contracts ("X (the "Term")", ""Term" means …",
# hereinafter), acronym pairs in technical text, Korean
# 이하 "갑" forms. The registry collects those stipulations
# deterministically at guard time. It is genre-blind by
# construction — it reacts to what THIS document stipulates,
# never to a genre label — and document-scoped: nothing in
# it carries into another document.
#
# Measured motivation (provenance audit, 2026-10-29): of 89
# role-word blocks in the CUAD slice, 77 (87%) had their
# endpoint term defined in the very document that proposed
# them — the guard was killing the contract's own defined
# parties. Methods borrowed (never their numbers): DEFT
# definition patterns; Schwartz-Hearst acronym pairing.

_DEF_PAREN_RX = re.compile(
    r"([A-Z][\w&.,'\u2019\-]+(?:\s+(?:[A-Z][\w&.,'\u2019\-]+|of|the|and|&))*)"
    r"\s*\(\s*(?:the\s+)?[\"“]([^\"”]{1,60})[\"”]\s*\)")
_DEF_HEREINAFTER_RX = re.compile(
    r"([A-Z][\w&.,'\u2019\-]+(?:\s+(?:[A-Z][\w&.,'\u2019\-]+|of|the|and|&))*)"
    r"\s*,?\s*hereinafter\s+(?:referred\s+to\s+as\s+)?"
    r"(?:the\s+)?[\"“]([^\"”]{1,60})[\"”]")
_DEF_MEANS_RX = re.compile(
    r"[\"“]([^\"”]{1,60})[\"”]\s+"
    r"(?:means|shall\s+mean|has\s+the\s+meaning)")
_DEF_MEANS_BARE_RX = re.compile(
    r"(?<![\w.])([A-Z][\w'\u2019\-]*(?:\s+[A-Z][\w'\u2019\-]*){0,3})"
    r"\s+(?:means|shall\s+mean|will\s+mean)\b")
_DEF_PAREN_ANY_RX = re.compile(
    r"\(([^()]{0,40}?)"
    r"[\"“]([^\"”]{1,60})[\"”]\s*\)")
_DEF_REFERRED_RX = re.compile(
    r"referred\s+to\s+(?:collectively\s+|individually\s+)?"
    r"(?:herein\s+)?as\s+(?:the\s+|a\s+|an\s+)?"
    r"[\"“]([^\"”]{1,60})[\"”]")
_NAME_RUN_RX = re.compile(
    r"[A-Z][\w&.'\u2019\-]*(?:\s+(?:[A-Z][\w&.'\u2019\-]+|of|the|and|&))*")
_KO_DEF_PAREN_RX = re.compile(
    r"\(이하\s*[\"'“]([^\"'”]{1,20})[\"'”]\)")
_KO_DEF_SAY_RX = re.compile(
    r"이하\s*[\"'“]([^\"'”]{1,20})[\"'”]\s*(?:이라|이라고)")
_ACRONYM_RX = re.compile(
    r"([A-Z][\w&\-]+(?:\s+(?:[A-Z][\w&\-]+|of|the|and|for|&)){1,6})"
    r"\s*\(\s*([A-Z][A-Z0-9]{1,9})\s*\)")


def _document_registry(doc_text: str) -> tuple[dict, set]:
    """The document's stipulated aliases: (canonical_of,
    defined). canonical_of maps an alias's norm_ws form to
    the referent surface the document binds it to (defined
    terms whose referent run is a clean name — no comma,
    no digit, at most six tokens; comma/digit runs are
    addresses and descriptions, not names, and bind
    nothing). defined holds every norm the document
    stipulates as a term, referent or not: a defined term
    IS an identity inside this document (the contract
    rule), even when no clean referent surface exists to
    canonicalize to."""
    canonical_of: dict[str, str] = {}
    defined: set[str] = set()
    for rx in (_DEF_PAREN_RX, _DEF_HEREINAFTER_RX):
        for m in rx.finditer(doc_text):
            referent = m.group(1).strip().rstrip(",")
            term = m.group(2).strip()
            if not term:
                continue
            defined.add(norm_ws(term))
            if (referent and "," not in referent
                    and not any(c.isdigit() for c in referent)
                    and len(referent.split()) <= 6):
                canonical_of.setdefault(norm_ws(term), referent)
    # Any quoted term inside parentheses is stipulated,
    # including forms the adjacent pattern cannot reach:
    # a descriptor clause between the name and the
    # parenthesis ('Audible Inc. a Delaware corporation
    # ("Company")'), or a collective marker ('(collectively
    # the "Parties")'). The referent, when one is wanted,
    # is recovered by looking back from the parenthesis —
    # under tight discipline, because a loose lookback
    # binds terms to whatever name happens to precede
    # them (measured on the CUAD slice: ("ACSI") bound
    # to a lone 'Inc.', ("Vyera") to a lone 'LLC', and
    # ("Development IP") to the previous sentence's
    # 'Development Plan'):
    # - the run has at least two tokens (a lone suffix
    #   token — 'Inc.', 'LLC' — is not a name);
    # - the run ENDS within 90 characters of the
    #   parenthesis, and the gap between run and
    #   parenthesis crosses no sentence boundary and
    #   no other parenthesis.
    for m in _DEF_PAREN_ANY_RX.finditer(doc_text):
        term = m.group(2).strip()
        if not term:
            continue
        defined.add(norm_ws(term))
        if norm_ws(term) in canonical_of:
            continue
        window = doc_text[max(0, m.start() - 200):m.start()]
        best = None
        for r in _NAME_RUN_RX.finditer(window):
            run = r.group(0).strip().rstrip(",")
            if not run or "," in run \
                    or any(c.isdigit() for c in run) \
                    or len(run.split()) < 2:
                continue
            gap = window[r.end():]
            if len(gap) > 90 or "(" in gap \
                    or re.search(r"[.!?]\s", gap):
                continue
            best = run
        if best is not None:
            canonical_of[norm_ws(term)] = best
    for m in _DEF_REFERRED_RX.finditer(doc_text):
        defined.add(norm_ws(m.group(1).strip()))
    for m in _DEF_MEANS_RX.finditer(doc_text):
        defined.add(norm_ws(m.group(1).strip()))
    for m in _DEF_MEANS_BARE_RX.finditer(doc_text):
        phrase = m.group(1).strip()
        if phrase.split()[0] not in (
                "The", "This", "It", "There", "A", "An"):
            defined.add(norm_ws(phrase))
    for rx in (_KO_DEF_PAREN_RX, _KO_DEF_SAY_RX):
        for m in rx.finditer(doc_text):
            defined.add(norm_ws(m.group(1).strip()))
    for m in _ACRONYM_RX.finditer(doc_text):
        long_form, short = m.group(1).strip(), m.group(2).strip()
        if long_form.startswith("The "):
            long_form = long_form[4:]
        initials = "".join(
            w[0] for w in re.findall(r"[A-Z][\w&\-]*", long_form))
        letters = re.sub(r"[^A-Z]", "", short)
        it = iter(initials)
        if letters and all(c in it for c in letters):
            canonical_of.setdefault(norm_ws(short), long_form)
    # Shared-modifier fence, applied to every binding:
    # a clause-defined term that shares the referent's
    # first token but is not that name extended names a
    # NEW, related concept, not the referent — '...
    # resulting from activities under the Development
    # Plan ("Development IP")': the IP is defined by the
    # clause; the Plan is a different entity. The term
    # stays defined (an identity in this document); it
    # just does not canonicalize. A term that extends
    # the FULL referent ('Smith Barney' -> 'Smith Barney
    # Holdings') or a one-token short form (("Vyera")
    # for Vyera Pharmaceuticals) is untouched.
    for tnorm in list(canonical_of):
        ttoks = tnorm.split()
        rtoks = norm_ws(canonical_of[tnorm]).split()
        if len(ttoks) >= 2 and rtoks \
                and ttoks[0] == rtoks[0] \
                and ttoks[:len(rtoks)] != rtoks:
            del canonical_of[tnorm]
    return canonical_of, defined


def identity_doc_key(text: str) -> str:
    """The decisions-file key for a document: the first
    16 hex chars of the sha256 of its bridged form —
    the keying the identity-surgery measurement files
    (and the frozen seat verdicts they carry) use."""
    return hashlib.sha256(
        _bridged_form(text).encode("utf-8")).hexdigest()[:16]


def load_identity_decisions(path) -> dict:
    """Load a frozen identity-seat decisions file:
    JSONL records {"doc", "ep", "cand", "decision"}
    with decision "complete" | "stand", keyed by
    identity_doc_key. Returns {(doc, ep, cand): bool}.
    Any malformed record raises ValueError — a
    declared seat file fails closed, never silently
    judges nothing."""
    decisions: dict[tuple[str, str, str], bool] = {}
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                doc = rec["doc"]
                ep = rec["ep"]
                cand = rec["cand"]
                decision = rec["decision"]
            except Exception as e:
                raise ValueError(
                    f"identity decisions {path}:{lineno}: "
                    f"malformed record: {e}") from e
            if not all(isinstance(v, str) and v
                       for v in (doc, ep, cand)) \
                    or decision not in ("complete", "stand"):
                raise ValueError(
                    f"identity decisions {path}:{lineno}: "
                    "doc/ep/cand must be non-empty strings "
                    "and decision 'complete' or 'stand'")
            decisions[(doc, ep, cand)] = (
                decision == "complete")
    return decisions


def identity_seat_for_doc(decisions: dict, doc_key: str,
                          stats: dict | None = None):
    """The per-document seat over a decisions lookup:
    the frozen verdict for (doc_key, fragment,
    candidate), or None — no opinion, rule 4's chain
    decides — when the point was never judged. When
    `stats` is given it counts consultations and
    opinions under "consulted" / "opinions"."""
    def seat(ep_norm, cand_norm, feats):
        if stats is not None:
            stats["consulted"] = \
                stats.get("consulted", 0) + 1
        verdict = decisions.get(
            (doc_key, ep_norm, cand_norm))
        if verdict is not None and stats is not None:
            stats["opinions"] = \
                stats.get("opinions", 0) + 1
        return verdict
    return seat


def guard_triples(items: list[dict], doc_text: str,
                  role_words: Iterable[str] | None = None,
                  entity_types: set[str] | None = None,
                  theme_attest_key: str | None = None,
                  quote_punct_insensitive: bool = False,
                  theme_attest_chunk: bool = False,
                  mentions: bool = False,
                  identity_seat=None
                  ) -> tuple[list[dict], list[dict]]:
    """Apply G1 + L0 attestation + role-word + theme guards.

    Returns (kept, blocked). Kept items gain `attest` (the level at
    which both endpoints are attested: quote | chunk | document).
    Blocked items gain `reason` (see module docstring).

    `identity_seat` is an optional injected judge for
    rule 4 of identity resolution (see _resolve_endpoint):
    a callable (fragment_norm, candidate_norm, features)
    -> bool | None deciding, per subset candidate,
    whether the fragment completes to the candidate
    (True), stands (False), or defers to the rule chain
    (None). No seat (the default) keeps the deterministic
    rule chain; behavior is unchanged.

    `role_words` is the role-word guard's vocabulary — the
    normalized role nouns, supplied as pack data and resolved
    by profile_config (). An endpoint whose normalized
    surface is in the set is blocked (l0_role_word): a bare
    role word is not an identity. None (the default) disables
    the guard; profiles that declare no role-word rules run
    with it off.

    typing: a proposed subject_type / object_type survives
    into the kept (and blocked) record only when it belongs to the
    profile's endpoint-type vocabulary (`entity_types`); an
    out-of-vocabulary tag is dropped — the triple itself is NOT
    blocked for a bad tag — and the endpoint continues untyped.
    With entity_types=None (no profile vocabulary), proposed tags
    pass through as-is.

    Two opt-in relaxations (both default to
    the legacy behavior, byte-identical):

    - `quote_punct_insensitive`: G1 also accepts a quote that
      is a verbatim substring of the document after
      punctuation-spacing collapse on both sides. The #66
      anatomy showed 15 of 19 g1_quote blocks were the
      document's own words differing only by the corpus's
      punctuation-spacing convention; genuinely edited or
      invented quotes fail at every normalization rung and
      stay blocked.
    - `theme_attest_key`: name of an item field carrying the
      span the item's endpoints were posed from (pair
      enumeration: the pair sentence). When set, the
      theme-presence check accepts endpoint attestation in
      that span as well as in the quote. The C2 pattern the
      guard targets — an endpoint fabricated from an
      unrelated quote fragment — cannot occur for posed
      endpoints: both are attested in the posed span by
      construction (measured 186/186 in the #66 run, where
      the quote-only test blocked pair items whose judge
      cited a subject-side fragment). Free proposals carry
      no such field and keep the quote-only test.
    - `theme_attest_chunk`: the theme-presence check also
      accepts the object attested anywhere in the chunk the
      proposal came from (the item's `chunk_norm`), not only
      inside the shortest quote. For packs whose relations
      are verb+preposition forms ("works at", "diagnosed
      with") whose objects the text typically names across
      the passage rather than inside the minimal quote, the
      quote-only test blocks the relation wholesale (the
      2026-10-23 BYO walk: 10/10 "works at" proposals from a
      hand-authored pack). The relaxation is weaker than
      the quote test — the object need only co-occur in the
      chunk — so it is a declared opt-in, never a default.
      (Subsumed by theme grading below: the grade is
      recorded either way; the flags remain accepted for
      signature compatibility and are no longer consulted.)

    Upstream attestation program (2026-10-29; design and
    decision tests in the launch notes):

    - Attestation is tiered. An endpoint attests verbatim
      (the legacy path), through the document's stipulated
      aliases (the registry: defined terms and acronym
      pairs the document itself binds), under punctuation-
      spacing collapse on both sides (tokenized corpora
      space quotes, apostrophes and hyphens), or — when
      the item carries a declared derivation surface
      (`subject_attested` / `object_attested`, the
      declared-surface proposer layer) — through that
      surface's attestation. The non-legacy tier that
      carried an endpoint is recorded in the kept record's
      `attest_kind`; the derivation's validity is the
      judge's call, not the guard's.
    - A stipulated alias with an attested referent is
      canonicalized to the referent (recorded in
      `canonicalized`, as identity resolutions are).
    - The role-word guard fires only for endpoints the
      document leaves undefined: a term the document
      defines is an identity inside it.
    - theme_presence no longer blocks. An item whose
      object attests in the document but not in its quote
      is kept with `theme_grade` (its `attest` level:
      chunk | document) for the judge.
    - A duplicate of an already-kept item is recorded in
      the blocked sidecar with reason `duplicate` instead
      of vanishing uncounted.
    Identity resolution (always on, no profile switch):
    after the quote gate, each endpoint is resolved
    against the document's census roster
    (_resolve_endpoint) — a fragment that names exactly
    one canonical surface (a nickname alias, an alias
    form, a fragment of the item's own anchor, or a
    proper token-subset of one maximal surface) is
    rewritten to that surface and the rewrite is
    recorded in the kept record's `canonicalized`; a
    fragment naming several surfaces is recorded in
    `identity_ambiguous` and left as proposed.
    Attestation reads the effective endpoint through
    the entity's recorded mention forms, so a canonical
    name that never occurs contiguously (a nickname
    intervenes) is not blocked for occurring nowhere.
    Blocked records always carry the item as proposed:
    the resolution lands on the record only when the
    item is kept.

    - `mentions`: the profile declared the mention layer
      (issue #54). Each item's mention records are verified
      by _verify_mentions after the quote gate and before
      attestation: declared spans verbatim, a non-named
      mention carrying a verbatim, endpoint-naming
      antecedent. The layer is additive (cut 2): an
      endpoint whose mention data is absent falls back to
      the legacy evidence path — kept items name those
      endpoints in `mention_fallback` and carry the
      cleaned `mentions` dict (verified endpoints only;
      the key is absent when every endpoint fell back, so
      the record is exactly the legacy shape). Only a
      declared-but-invalid mention blocks, and blocked
      items carry the records as proposed. Default
      False = the checks do not run and records are
      exactly the legacy shape.
    """
    doc_norm = norm_ws(doc_text)
    doc_punct = punct_collapse(doc_norm) \
        if quote_punct_insensitive else None
    identity = _identity_index(doc_text) if items \
        else None
    doc_bridged = _bridged_form(doc_text) \
        if identity is not None else None
    # The document's stipulated aliases (defined terms,
    # acronym pairs) and its punctuation-collapsed form
    # for the normalized attestation tier.
    registry, defined_terms = _document_registry(doc_text) \
        if items else ({}, set())
    doc_rep = punct_collapse(doc_norm)
    kept, blocked, seen = [], [], set()
    for it in items:
        s, r, o, q = (it["subject"], it["relation"], it["object"],
                      it["quote"])
        base = {"subject": s, "relation": r, "object": o, "quote": q}
        for tk in ("subject_type", "object_type"):
            tv = it.get(tk)
            if isinstance(tv, str) and tv.strip():
                tv = tv.strip()
                if entity_types is None or tv in entity_types:
                    base[tk] = tv
        if mentions and isinstance(it.get("mentions"), dict):
            # As proposed, for the blocked sidecar's audit;
            # replaced by the verified record below.
            base["mentions"] = it["mentions"]
        qn = norm_ws(q)
        if not qn or (qn not in doc_norm
                      and not (doc_punct is not None
                               and punct_collapse(qn) in doc_punct)):
            blocked.append({**base, "reason": "g1_quote"})
            continue
        sn, on = norm_ws(s), norm_ws(o)
        if mentions:
            mres = _verify_mentions(it, qn, doc_norm, sn, on)
            if isinstance(mres, str):
                blocked.append({**base, "reason": mres})
                continue
            cleaned_m, fallback_eps = mres
            if cleaned_m:
                base["mentions"] = cleaned_m
            else:
                # Every endpoint fell back: the kept record
                # is exactly the legacy shape.
                base.pop("mentions", None)
            if fallback_eps:
                base["mention_fallback"] = fallback_eps
        # Identity resolution runs before attestation.
        # A fragment endpoint (Willi / Alger / Paul
        # Desmarais for Paul Desmarais Jr.) names its
        # entity under a mention form, and the graph
        # keys nodes by name string — an unresolved
        # fragment is a separate node and a broken
        # graph, not a stylistic variant. A unique
        # resolution rewrites the endpoint to the
        # canonical surface (recorded in
        # `canonicalized`); an ambiguous one is
        # recorded in `identity_ambiguous` and left as
        # proposed. Attestation then reads the
        # EFFECTIVE endpoint through every mention
        # form the census recorded for its entity: the
        # canonical name may never occur contiguously
        # (Wilfried "Willi" Schneider) while its
        # bridged form does, so a raw substring test
        # would block exactly the correct name.
        forms: dict[str, tuple[list[str], bool]] = {}
        eff = {"subject": base["subject"],
               "object": base["object"]}
        canon: dict[str, list[str]] = {}
        amb: dict[str, list[str]] = {}
        pre_eff = dict(eff)
        pre_canon = dict(canon)
        if identity is not None:
            by_norm = identity[0]
            for ep in ("subject", "object"):
                res, cands = _resolve_endpoint(
                    norm_ws(base[ep]), identity,
                    anchor_norm=it.get("anchor_norm"),
                    ep_initialism=bool(re.fullmatch(
                        r"[A-Z][A-Z0-9]{1,5}",
                        base[ep].strip())),
                    seat=identity_seat)
                if res is not None and res != base[ep]:
                    canon[ep] = [base[ep], res]
                    eff[ep] = res
                elif cands:
                    amb[ep] = cands
                effn = norm_ws(eff[ep])
                ent = by_norm.get(effn)
                forms[ep] = (
                    [effn] + (list(ent["aliases"])
                              if ent else []),
                    ent is not None or ep in canon)
        # Registry canonicalization: an endpoint stated
        # as a stipulated alias (a defined term with a
        # clean referent, an acronym short form) whose
        # referent the document attests resolves to the
        # referent surface — recorded in `canonicalized`
        # exactly like an identity resolution, since the
        # graph keys nodes by name string and "Company"
        # the defined term IS that party inside this
        # document. An alias whose referent does not
        # attest keeps its proposed surface and is
        # attested through the alias form itself
        # (reg_forms), which the document stipulates.
        reg_forms: dict[str, list[str]] = {}
        for ep in ("subject", "object"):
            pnorm = norm_ws(base[ep])
            target = registry.get(pnorm)
            if target is None:
                continue
            tnorm = norm_ws(target)
            if tnorm == norm_ws(eff[ep]):
                reg_forms[ep] = [pnorm]
            elif tnorm in doc_norm \
                    or punct_collapse(tnorm) in doc_rep:
                # The stipulated binding is the strongest
                # signal in the document: the record shows
                # proposed -> stipulated referent, even
                # when identity resolution also rewrote
                # the endpoint on the way.
                canon[ep] = [base[ep], target]
                eff[ep] = target
                reg_forms[ep] = [pnorm, tnorm]
                fs = forms.get(ep, ([], False))[0]
                forms[ep] = ([tnorm, *fs], True)
            else:
                reg_forms[ep] = [pnorm]

        if norm_ws(eff["subject"]) \
                == norm_ws(eff["object"]) \
                and norm_ws(base["subject"]) \
                != norm_ws(base["object"]):
            # Endpoint-merge invariant: canonicalisation
            # exists to give one entity one node; it may
            # never make a triple reflexive. When two
            # distinct proposed endpoints resolve to one
            # surface, the merge itself is the error — a
            # company is not its founder though they share
            # a name token, and a place is not the resort
            # named after it — so both endpoints fall back
            # to their pre-resolution forms.
            eff = pre_eff
            canon = pre_canon
            reg_forms = {}
            forms = {}
            if identity is not None:
                by_norm = identity[0]
                for ep in ("subject", "object"):
                    effn = norm_ws(eff[ep])
                    ent = by_norm.get(effn)
                    forms[ep] = (
                        [effn] + (list(ent["aliases"])
                                  if ent else []),
                        ent is not None)

        def _attested(ep: str, hay: str, hay_rep: str,
                      bridged: bool = False,
                      _forms=forms, _reg=reg_forms,
                      _base=base) -> bool:
            # Attestation is tiered (upstream program):
            # the endpoint's forms verbatim in the
            # haystack, the document's stipulated alias
            # forms, or any form under punctuation-
            # spacing collapse on both sides (the T1
            # repair — tokenized corpora space their
            # quotes, apostrophes and hyphens:
            # 'Healey 's', 'Won - yong'). The bridged-
            # document fallback belongs to L0 alone: it
            # answers "does this entity occur in the
            # document at all". The quote and chunk
            # haystacks must NOT fall back to it — that
            # would widen a quote-level test into a
            # document-level one. (_forms/_reg/_base
            # bind this item's structures at definition
            # time.)
            fs, bridged_ok = _forms.get(
                ep, ([norm_ws(_base[ep])], False))
            rs = _reg.get(ep, [])
            return any(f in hay for f in fs) \
                or any(f in hay for f in rs) \
                or any(punct_collapse(f) in hay_rep
                       for f in fs) \
                or any(punct_collapse(f) in hay_rep
                       for f in rs) \
                or (bridged and bridged_ok
                    and doc_bridged is not None
                    and any(f in doc_bridged for f in fs))

        prop_sn, prop_on = sn, on
        sn, on = norm_ws(eff["subject"]), \
            norm_ws(eff["object"])
        qn_rep = punct_collapse(qn)
        chunk_n = it.get("chunk_norm", "")
        chunk_rep = punct_collapse(chunk_n)
        # L0: attested somewhere in the document at
        # all? The tier that carried each endpoint is
        # recorded in `attest_kind` when it is not the
        # legacy verbatim/alias path: "registered" (a
        # stipulated alias form), "normalized" (the
        # spacing repair), "declared" (the proposer's
        # declared derivation surface attests — the
        # derivation itself is the judge's call, R1).
        kinds: dict[str, str] = {}
        unattested = False
        for ep in ("subject", "object"):
            fs, bridged_ok = forms.get(
                ep, ([norm_ws(base[ep])], False))
            rs = reg_forms.get(ep, [])
            effn = norm_ws(eff[ep])
            if effn in doc_norm or any(
                    f in doc_norm for f in fs) or (
                    bridged_ok and doc_bridged is not None
                    and any(f in doc_bridged for f in fs)):
                continue
            if any(f in doc_norm for f in rs):
                kinds[ep] = "registered"
            elif punct_collapse(effn) in doc_rep or any(
                    punct_collapse(f) in doc_rep
                    for f in fs + rs):
                kinds[ep] = "normalized"
            else:
                decl = it.get(f"{ep}_attested")
                dn = norm_ws(decl) \
                    if isinstance(decl, str) else ""
                if dn and (dn in doc_norm
                           or punct_collapse(dn) in doc_rep):
                    kinds[ep] = "declared"
                else:
                    unattested = True
        if unattested:
            blocked.append({**base, "reason": "l0_attest"})
            continue
        if _attested("subject", qn, qn_rep) \
                and _attested("object", qn, qn_rep):
            attest = "quote"
        elif _attested("subject", chunk_n, chunk_rep) \
                and _attested("object", chunk_n, chunk_rep):
            attest = "chunk"
        else:
            attest = "document"
        if role_words:
            # A term THIS document defines is an
            # identity here, not a bare role word:
            # the guard fires only for endpoints the
            # document leaves undefined (provenance
            # audit: 77 of 89 role blocks in the CUAD
            # slice were the contract's own defined
            # parties).
            def _role_blocked(effn_: str, propn_: str) -> bool:
                if effn_ not in role_words \
                        and propn_ not in role_words:
                    return False
                return effn_ not in defined_terms \
                    and propn_ not in defined_terms \
                    and effn_ not in registry \
                    and propn_ not in registry
            if _role_blocked(sn, prop_sn) \
                    or _role_blocked(on, prop_on):
                blocked.append({**base,
                                "reason": "l0_role_word"})
                continue
        toks = [t for t in re.split(r"[^a-z]+", norm_ws(r)) if t]
        if toks and all(t in PREPS for t in toks):
            blocked.append({**base, "reason": "theme_missing"})
            continue
        if toks and toks[-1] in PREPS \
                and not _attested("object", qn, qn_rep):
            # Theme grading (decision test 1,
            # 2026-10-29): the object is attested in
            # the document (L0 passed) but not inside
            # the quote. The old guard blocked the
            # whole class; a judged sample of the
            # blocked class was overwhelmingly
            # text-true (the parse path states the
            # object across the passage, not inside
            # the minimal quote). The item is KEPT and
            # its evidence grade goes to the judge,
            # whose seams already weigh evidence
            # strength; fabrication control stays
            # with G1 (the quote is verbatim) plus
            # the judge. `theme_attest_key` /
            # `theme_attest_chunk` are subsumed by
            # the grade and no longer consulted.
            base["theme_grade"] = attest
        # The resolution lands on the record only
        # here, once the item is kept: blocked records
        # carry the item exactly as proposed.
        if canon:
            base["subject"] = eff["subject"]
            base["object"] = eff["object"]
            base["canonicalized"] = canon
        if amb:
            base["identity_ambiguous"] = amb
        if kinds:
            base["attest_kind"] = kinds
        k = (sn, norm_ws(r), on)
        if k in seen:
            # A duplicate is a loss like any other and
            # is accounted, never silent: it lands in
            # the blocked sidecar under its own reason,
            # as the record it would have kept.
            blocked.append({**base, "reason": "duplicate"})
            continue
        seen.add(k)
        kept.append({**base, "attest": attest})
    return kept, blocked


def guard_slots(fills: list[dict], doc_text: str) -> tuple[list[dict],
                                                          list[dict]]:
    """Span gate + per-(category, value) dedupe for slot fills."""
    doc_norm = norm_ws(doc_text)
    kept, blocked, seen = [], [], set()
    for f in fills:
        if not norm_ws(f["quote"]) or \
                norm_ws(f["quote"]) not in doc_norm:
            blocked.append({**f, "reason": "g1_quote"})
            continue
        k = (f["category"], norm_ws(f["value"]))
        if k in seen:
            continue
        seen.add(k)
        kept.append(f)
    return kept, blocked


def api_key() -> str:
    """The proposer seat's key, or "" when its binding's
    key variable is unset (callers that require the seat
    refuse with :func:`key_env` named in the message)."""
    return connections.seat("proposer").key_or_none() or ""


def key_env() -> str:
    """The environment variable the proposer binding
    reads its key from (preset: OPENROUTER_API_KEY)."""
    return connections.seat("proposer").key_env
