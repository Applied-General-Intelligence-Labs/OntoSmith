"""EntityRegistry and GraphStore interfaces.

Design decision (2026-10-10):
- EntityRegistry = system of record (source of truth for entities)
- GraphStore = derived view optimized for RAG traversals
- Interfaces are separate NOW; physical separation LATER.
- Today: Neo4j implements GraphStore. Registry starts simple.

Write ordering (registry-first):
1. EntityRegistry: resolve + merge entities → on failure: nothing happened
2. GraphStore: write triples referencing registry IDs
   → on failure: orphan entities in registry (harmless)
Never reverse: graph-first risks edges pointing to nothing.
"""
from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


def attestation_id(subject_id: str, relation: str, object_id: str,
                   doc_id: str, surface_s: str = "",
                   surface_o: str = "", quote: str = "",
                   model: str = "") -> str:
    """Content-derived identity of ONE attestation of a fact
   : the same fact attested in N documents, or twice in one
    document under different surfaces/quotes/methods, yields N
    distinct ids, while re-writing the identical attestation is
    idempotent. Writers that carry no surface/quote material
    (pipeline Triple rows) pass empty strings, in which case the id
    reduces to the (fact, doc) pair — the pre-G-28 collapse for
    exact duplicates, which those writers dedupe upstream anyway.
    The id is scoped by (batch_id, run_id) through the edge key it
    joins; writers of the same run must pass the same material."""
    material = "|".join([subject_id, relation, object_id, doc_id,
                         surface_s, surface_o, quote, model])
    return hashlib.sha256(material.encode()).hexdigest()[:24]


def normalize_name(name: str) -> str:
    """Normalize for entity matching.

    Handles: case, underscores, extra whitespace, OCR newlines.
    'LEE_HARVEY_OSWALD' → 'lee harvey oswald'
    'Carlos\\nBlalock' → 'carlos blalock'
    """
    n = name.lower()
    n = n.replace("_", " ")
    n = re.sub(r"\s+", " ", n)  # newlines, tabs → single space
    n = n.strip(" .,;:-")
    return n.strip()


# Honorifics stripped by index_normalize when they lead a name. These
# are form variants of the name itself (the ER guardrail's safe class),
# so they belong in index keys — never in merge decisions.
_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "prof", "professor",
               "st", "sir", "madam", "rev", "hon"}


def index_normalize(name: str) -> str:
    """Normalize a name into a registry index key.

    The form-variant rules: case folding, diacritic folding
    ('Léon' → 'leon'), punctuation/underscore cleanup (via
    normalize_name), the spaced-hyphen fold ('Charles -
    Édouard' → 'charles-édouard'), and stripping of leading
    honorific tokens ('Dr. Smith' → 'smith'). Index keys drive
    candidate LOOKUP only; they are never an auto-merge decision.
    """
    import unicodedata
    n = unicodedata.normalize("NFKD", name)
    n = "".join(ch for ch in n if not unicodedata.combining(ch))
    n = normalize_name(n)
    # Per-token punctuation strip: normalize_name only trims the
    # string's ends, so 'Dr. Smith' would otherwise keep 'dr.'.
    toks = [t.strip(".,;:") for t in n.split(" ")]
    toks = [t for t in toks if t]
    # Spaced-hyphen fold: tokenized corpora space their hyphens
    # ('Charles - Édouard', 'Kang Won - yong') — the same
    # punctuation-spacing convention the guard's attestation
    # tier collapses (proposer.punct_collapse). A standalone
    # hyphen token joins its neighbours, so the spaced and
    # closed forms share one index key and resolve as form
    # variants of one name. The fold lives in the index key
    # only: stored surfaces are never rewritten, and a merge
    # retains BOTH attested forms (one as the name, the other
    # as an alias) — alias retention, not replacement (the
    # replacement form measured net-negative in the density
    # program: gold itself mixes the two conventions).
    folded: list[str] = []
    for tok in toks:
        if tok in ("-", "\u2013", "\u2014") and folded:
            folded[-1] += "-"
        elif folded and folded[-1].endswith("-"):
            folded[-1] += tok
        else:
            folded.append(tok)
    toks = folded
    while len(toks) > 1 and toks[0] in _HONORIFICS:
        toks = toks[1:]
    return " ".join(toks)


# Generic tokens that never make a token-subset pair a form variant on
# their own ("Biotech" ⊂ "Biotech Holdings" is a variant; "Inc" ⊂
# anything is not evidence of anything).
_GENERIC_TOKENS = {"inc", "llc", "corp", "corporation", "ltd", "co",
                   "company", "the", "of", "and", "group", "holdings"}


def is_form_variant(a: str, b: str) -> bool:
    """Deterministic form-variant test.

    True when two surface forms are variants of the SAME name itself
    under the established rules — and only then may WRITE auto-merge
    without a gate call or a human:
      - index-normalized equality (case / diacritic folding,
        honorific stripping, punctuation cleanup), or
      - strict token subset in either direction ('Aduro' ⊂
        'Aduro Biotech'), unless the smaller side is a single generic
        token, or
      - initialism: one side is a single token whose letters are the
        initials of the other side's tokens ('IBM' ~
        'International Business Machines').
    Semantic similarity is NEVER a form variant: pairs failing this
    test go to the ER review band, they are not merged.
    """
    na, nb = index_normalize(a), index_normalize(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta, tb = na.split(" "), nb.split(" ")
    sa, sb = set(ta), set(tb)
    if sa < sb or sb < sa:
        smaller = sa if sa < sb else sb
        if len(smaller) == 1 and next(iter(smaller)) in _GENERIC_TOKENS:
            return False
        return True
    # Initialism (corpus-attested abbreviation form).
    for short, long_ in ((ta, tb), (tb, ta)):
        if len(short) == 1 and len(long_) >= 2:
            tok = short[0]
            if len(tok) >= 2 and tok == "".join(t[0] for t in long_ if t):
                return True
    return False


def mint_entity_id(exists) -> str:
    """Mint a collision-checked unique entity ID.

    `exists(entity_id) -> bool` is the backend's uniqueness check;
    minting loops until the candidate is unused, so a collision can
    never silently merge two entities (the legacy name-slug failure).
    """
    import uuid
    while True:
        eid = "ent:" + uuid.uuid4().hex
        if not exists(eid):
            return eid


def name_quality(name: str) -> float:
    """Score for survivor selection in entity merges.

    The survivor should be the cleanest, most complete name.
    OCR fragments ('0.\\nLive in New York') score low;
    complete names ('LEE HARVEY OSWALD') score high.
    """
    score = 0.0
    # Penalties: OCR garbage
    if "\n" in name or "\t" in name or "\r" in name:
        score -= 10
    if re.match(r"^[\d\.\-\s•·]+", name):
        score -= 10  # leading numbers/bullets: "0. Live in..."
    if re.search(r"[\\/|]{2,}", name):
        score -= 5  # OCR artifacts
    if len(name.strip()) < 3:
        score -= 5
    if len(name) > 100:
        score -= 5  # probably a sentence fragment
    # Bonuses: completeness
    score += min(len(name.strip()), 30) / 10
    words = name.split()
    if len(words) >= 2:
        score += 2  # multi-word names are more complete
    if len(words) >= 3:
        score += 1
    return score


def pick_survivor(names: list[str]) -> str:
    """Pick the cleanest name. Deterministic tie-break."""
    return max(sorted(names), key=name_quality)


@dataclass
class Entity:
    entity_id: str          # registry-assigned, stable
    name: str               # canonical display name
    label: str              # person | organization | location | ...
    aliases: list[str] = field(default_factory=list)
    batch_id: str = ""
    run_id: str = ""
    doc_id: str = ""
    corpus: str = ""        # corpus namespace; "" = unscoped legacy


class EntityRegistry(ABC):
    """System of record for entities."""

    @abstractmethod
    def find_candidates(self, name: str, label: str = "",
                        limit: int = 5, corpus: str | None = None,
                        cross_corpus: bool = False) -> list[Entity]:
        """Find existing entities that might match (fuzzy).

        Corpus scoping: when `corpus` is given and
        `cross_corpus` is False, only entities of that corpus are
        returned — resolution never crosses namespaces by default.
        `cross_corpus=True` is the explicit call-site opt-in."""

    @abstractmethod
    def get(self, entity_id: str) -> Entity | None:
        ...

    @abstractmethod
    def create(self, entity: Entity) -> str:
        """Create a new entity. Returns entity_id."""

    @abstractmethod
    def merge(self, existing_id: str, new: Entity,
              model: str = "", p_same: float | None = None) -> str:
        """Merge new entity into existing. Returns surviving entity_id.
        The new name is added as an alias, and any aliases the new
        entity carries are retained on the survivor (unioned, never
        dropped); provenance is preserved.
        model/p_same record the deciding gate when a gate made the
        call (deterministic form-variant merges pass neither)."""

    def record_er_decision(self, batch_id: str, run_id: str,
                           merged_name: str, surviving_id: str,
                           canonical_name: str, model: str = "",
                           p_same: float | None = None,
                           outcome: str = "review",
                           reason: str = "") -> None:
        """Record an ER decision that did NOT merge (review band).

        Default: no-op (backends without a decision store). The
        file registry overrides this — review-band pairs land in
        er_decisions with outcome='review' so the pair, the gate's
        p_same and the model stay auditable. `reason` names
        a deterministic rule behind the decision when one made the
        call (e.g. an type-pair merge prohibition);
        gate-driven rows leave it empty."""

    def settled_er_decision(self, merged_name: str,
                            surviving_id: str) -> dict | None:
        """The latest HUMAN settlement of an ER pair, if any.

        Default: None (backends without a decision store settle
        nothing). The file registry overrides this:
        a pair a person settled — merged, or kept separate — is
        a recorded decision in er_decisions, and WRITE consults
        this so a keep-separate pair is never re-held and its
        gate never re-consulted."""
        return None

    def resolve(self, name: str, corpus: str | None = None) -> Entity | None:
        """Resolve a surface-form name to an entity (normalized match
        on names + aliases). Returns None if not found. Override for
        efficiency; default scans (fine for prototype scale)."""
        return None


@dataclass
class Triple:
    subject_id: str
    relation: str
    object_id: str
    p_true: float
    tier: int               # 1 = VERIFIED, 2 = UNVERIFIED
    tag: str                # VERIFIED | UNVERIFIED
    batch_id: str
    run_id: str
    doc_id: str
    model: str = ""
    gate: str = "triple"


class GraphStore(ABC):
    """Derived view for RAG traversal queries."""

    @abstractmethod
    def write_triples(self, triples: list[Triple]) -> int:
        """Write triples. Returns count written."""

    @abstractmethod
    def delete_run(self, batch_id: str, run_id: str) -> int:
        """Rollback: delete everything from a run. Returns count deleted."""

    @abstractmethod
    def traverse(self, entity_id: str, hops: int = 2,
                 limit: int = 50) -> list[dict]:
        """Multi-hop traversal from an entity. Returns paths."""
