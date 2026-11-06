"""The connection layer: per-seat provider bindings.

OntoSmith's pipeline never names a provider. It names a
**seat** — a capability tier with a defined job (docs/
connections.md) — and this layer binds each seat to a
concrete endpoint: an OpenAI-compatible base URL, a model
name, and an API key read from an environment variable.

The shipped **reference preset** (``pipeline/
reference_profile.py``) fills the measured seats with the
bindings the published quality record was measured with,
so a default install reproduces the published
measurements. (The drafting seat is the one exception:
it ships unbound — see the declaration surface below.)
Any user override makes the run a **custom binding**;
batch records state which binding produced a run
(``Bindings.provenance()`` — host + model per seat, never
a key).

Declaration surface: environment variables.
Connection settings across this codebase are env-only
(``OPENROUTER_API_KEY``, ``NEO4J_*``), and keys must
never land in a file: an override names the key's
environment variable, never its value. Per seat ``S``
(one of PROPOSER, JUDGE, ESCALATION, DRAFTING, EMBEDDER;
upper-case in the variable names):

  ONTOSMITH_<S>_BASE_URL      base URL for the seat
  ONTOSMITH_<S>_MODEL         model name for the seat
  ONTOSMITH_<S>_KEY_ENV       env var holding the key
  ONTOSMITH_<S>_DECISIONS_URL decisions-route override
                              (judge / escalation seats)
  ONTOSMITH_<S>_DIALECT       provider dialect (below)
  ONTOSMITH_<S>_PRICE_IN_PER_MTOK / _PRICE_OUT_PER_MTOK
                              optional user-declared rates
                              (below)
  ONTOSMITH_<S>_CAPABILITIES  capability declarations
                              (below)

Provider dialects (provider generalization):
a binding declares which request/response dialect its
route speaks. ``openrouter`` (the shipped preset) may add
OpenRouter-only request fields (``provider.sort``,
``usage.include``, the ``reasoning`` block) and its
responses carry a provider-reported ``usage.cost``.
``openai`` (the default for custom bindings) is the
portable OpenAI-compatible subset only: no provider-only
fields are emitted, and responses are not expected to
carry a cost. A seat's **cost basis** follows from its
declaration: ``provider-reported`` (openrouter dialect),
``estimated`` (the user declared per-seat rates, below),
or ``not-provided``. ``ONTOSMITH_DIALECT`` sets the
dialect globally, overridden per seat.

User-declared rates: a cost-less route can still be
metered if the user declares what they pay —
``ONTOSMITH_<S>_PRICE_IN_PER_MTOK`` and
``ONTOSMITH_<S>_PRICE_OUT_PER_MTOK`` (USD per million
tokens; both or neither). Costs computed from them are
stamped ``cost_basis: estimated`` — config data declared
by the user, never a product price table. Without rates
(or a cost-reporting dialect), cost is ``None``
(``not-provided``): a USD spend cap cannot guard spend
it cannot meter, so batch create/run refuse by name
(:class:`BindingCostError`) when a cap is set on a
cost-less binding.

Capabilities: model-behavior switches (a model rejecting
non-default temperature; a reasoning model needing the
completion-token parameter) are declared per binding,
not guessed from name substrings at call sites.
``ONTOSMITH_<S>_CAPABILITIES`` is a comma-separated list
(``no-temperature``, ``reasoning-tokens``; an empty value
declares none). Undeclared, the capabilities derive from
the seat's model id by the connection layer's derivation
rules — the same rules, stated once, that the call sites
used to apply ad hoc.

The drafting seat ships **unbound**: the preset
declares its endpoint coordinates but no model — drafting
is an authoring aid behind the human approve gate, not
part of the measured pipeline, so no model is presented
as its default. A drafting call resolves its model from
the user's declaration only (``ONTOSMITH_DRAFTING_MODEL``
or the caller's model option) and refuses with a named
error (:class:`BindingModelError`) before any request
when none is bound.

Globals, overridden per seat:
  ONTOSMITH_BASE_URL, ONTOSMITH_KEY_ENV

Resolution order per field: per-seat variable, then the
global variable (base URL / key env only), then the
reference preset. Resolution reads the environment at
call time; nothing is cached.

Refusal rule: a seat that is *used* resolves its key
through :meth:`SeatBinding.require_key`, which raises
:class:`BindingKeyError` naming the seat and the key
variable when that variable is unset or empty — a
declared-but-broken binding never quietly substitutes
the preset's key or endpoint. The embedder seat has no
fallback at all: an embedder use whose key cannot be
resolved refuses the same way, declared or not.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from . import reference_profile as _rp

#: The seats that take a provider binding, in doc order.
SEATS = ("proposer", "judge", "escalation",
         "drafting", "embedder")

#: The preset key variable every shipped seat reads.
PRESET_KEY_ENV = "OPENROUTER_API_KEY"

#: Provider dialects a binding may declare.
DIALECT_OPENAI = "openai"
DIALECT_OPENROUTER = "openrouter"
DIALECTS = (DIALECT_OPENAI, DIALECT_OPENROUTER)

#: Cost-basis stamps: how a seat's spend figures are known.
COST_BASIS_PROVIDER = "provider-reported"
COST_BASIS_ESTIMATED = "estimated"
COST_BASIS_NOT_PROVIDED = "not-provided"

#: Capability declarations: model-behavior
#: switches, declared per binding instead of guessed from
#: name substrings at call sites.
CAP_NO_TEMPERATURE = "no-temperature"
CAP_REASONING_TOKENS = "reasoning-tokens"
CAPABILITIES = (CAP_NO_TEMPERATURE, CAP_REASONING_TOKENS)

# Derivation rules for undeclared capabilities — the
# connection layer's single statement of them (call sites
# used to apply these substrings ad hoc). They derive a
# binding's defaults from its model id only; an explicit
# ONTOSMITH_<S>_CAPABILITIES declaration always wins.
_NO_TEMPERATURE_SUBSTRINGS = ("claude-haiku-5",)
_REASONING_SUBSTRINGS = ("gpt-5", "/o1", "/o3", "spark",
                         "reasoner", "r1")


def derive_capabilities(model: str | None) -> frozenset:
    """Default capabilities for a model id, per the
    derivation rules above."""
    caps: set[str] = set()
    if model:
        m = model.lower()
        if any(s in m for s in _NO_TEMPERATURE_SUBSTRINGS):
            caps.add(CAP_NO_TEMPERATURE)
        if any(s in m for s in _REASONING_SUBSTRINGS):
            caps.add(CAP_REASONING_TOKENS)
    return frozenset(caps)


class BindingKeyError(RuntimeError):
    """A seat's binding names a key variable that is unset
    or empty. The message names the seat and the variable —
    never a key value."""


class BindingModelError(RuntimeError):
    """A seat that ships unbound (drafting) was used with
    no model declared. The message names the seat and how
    to bind a model — never a key value."""


class BindingCostError(RuntimeError):
    """A USD spend cap is set on a binding whose cost
    basis is not-provided: the cap could never trip, so
    the run refuses by name instead of pretending to be
    guarded. The message names the seats, the cap, and
    the remedies — never a key value."""


@dataclass(frozen=True)
class SeatBinding:
    """One seat's concrete binding (connection layer)."""

    seat: str
    base_url: str
    #: The bound model, or None for a seat that ships
    #: unbound (drafting — resolve it through
    #: :meth:`require_model` before use).
    model: str | None
    key_env: str
    #: Explicit decisions-route URL. The preset pins
    #: OpenRouter's alpha decisions route, which does not
    #: sit under the seat's base URL; a custom binding
    #: leaves this None and derives ``<base>/decisions``.
    decisions_url: str | None = None
    #: Provider dialect the route speaks:
    #: ``openrouter`` may emit provider-only request fields
    #: and reports ``usage.cost``; ``openai`` is the
    #: portable subset. Custom bindings default to
    #: ``openai``; the preset declares ``openrouter``.
    dialect: str = DIALECT_OPENAI
    #: Optional user-declared rates (USD per million
    #: tokens) — both or neither. When set on a seat whose
    #: provider does not report cost, spend is computed
    #: from token counts and stamped ``estimated``.
    price_in_per_mtok: float | None = None
    price_out_per_mtok: float | None = None
    #: Explicit capability declaration, or None to derive
    #: from the model id (see :func:`derive_capabilities`).
    capabilities: frozenset | None = None

    # -- derived routes (OpenAI-compatible shapes) --------
    @property
    def chat_url(self) -> str:
        return self.base_url + "/chat/completions"

    @property
    def embeddings_url(self) -> str:
        return self.base_url + "/embeddings"

    @property
    def systemone_url(self) -> str:
        return self.base_url + "/systemone"

    @property
    def decisions_endpoint(self) -> str:
        return self.decisions_url or (self.base_url + "/decisions")

    @property
    def host(self) -> str:
        """Provenance-safe host label (netloc of base_url)."""
        return urlparse(self.base_url).netloc or self.base_url

    # -- model resolution ----------------------------------
    def require_model(self) -> str:
        """The seat's bound model. A seat that ships
        unbound (drafting) raises :class:`BindingModelError`
        naming the seat and the declaration that binds one
        — a drafting call never quietly substitutes a
        model the user did not choose."""
        if not self.model:
            raise BindingModelError(
                f"{self.seat} seat: no model is bound to "
                f"this seat (the {self.seat} seat ships "
                f"unbound); set "
                f"ONTOSMITH_{self.seat.upper()}_MODEL or "
                f"pass the caller's model option to bind one")
        return self.model

    # -- dialect / cost basis / capabilities -------------
    @property
    def is_openrouter_dialect(self) -> bool:
        return self.dialect == DIALECT_OPENROUTER

    @property
    def cost_basis(self) -> str:
        """How this seat's spend is known: the openrouter
        dialect reports cost per response; a user-declared
        price table estimates it; anything else does not
        provide it."""
        if self.is_openrouter_dialect:
            return COST_BASIS_PROVIDER
        if self.price_in_per_mtok is not None:
            return COST_BASIS_ESTIMATED
        return COST_BASIS_NOT_PROVIDED

    @property
    def reports_cost(self) -> bool:
        """True when spend on this seat can be metered
        (provider-reported or user-estimated)."""
        return self.cost_basis != COST_BASIS_NOT_PROVIDED

    def capabilities_for(self, model: str | None = None) -> frozenset:
        """The capabilities in force for a call: the
        binding's explicit declaration when present, else
        derived from the call's model (default: the
        seat's bound model)."""
        if self.capabilities is not None:
            return self.capabilities
        return derive_capabilities(
            self.model if model is None else model)

    def has_capability(self, capability: str,
                       model: str | None = None) -> bool:
        return capability in self.capabilities_for(model)

    # -- key resolution ------------------------------------
    def key_or_none(self, env: dict | None = None) -> str | None:
        env = os.environ if env is None else env
        return env.get(self.key_env) or None

    def require_key(self, env: dict | None = None) -> str:
        key = self.key_or_none(env)
        if not key:
            model_label = (self.model if self.model is not None
                           else "none bound")
            raise BindingKeyError(
                f"{self.seat} seat: API key environment variable "
                f"{self.key_env} is not set (binding host "
                f"{self.host}, model {model_label}); set "
                f"{self.key_env} or rebind the {self.seat} seat")
        return key


@dataclass(frozen=True)
class Bindings:
    """A fully resolved seat map plus its provenance."""

    seats: dict[str, SeatBinding]
    #: Seats the user declared at least one override for.
    declared: frozenset[str] = frozenset()

    def seat(self, name: str) -> SeatBinding:
        return self.seats[name]

    @property
    def is_custom(self) -> bool:
        preset = preset_bindings()
        return any(self.seats[s] != preset.seats[s] for s in SEATS)

    @property
    def name(self) -> str:
        return "custom" if self.is_custom else _rp.REFERENCE_PROFILE_NAME

    def provenance(self) -> dict:
        """Non-secret run-record shape: the binding's name
        and, per seat, the host and model that ran, the
        declared dialect, and the seat's cost basis. Hosts
        and model names only — key variable names and
        values never appear."""
        return {
            "name": self.name,
            "seats": {
                s: {"host": b.host, "model": b.model,
                    "dialect": b.dialect,
                    "cost_basis": b.cost_basis}
                for s, b in self.seats.items()
            },
        }

    def cost_basis(self) -> str:
        """Aggregate cost basis over the seats that carry
        a bound model (an unbound seat spends nothing):
        the single basis when all metering agrees, else
        ``mixed``. When NO seat carries a bound model the
        basis is ``not-provided``: nothing is
        metered because nothing can spend — ``mixed``
        would claim a disagreement that does not exist."""
        bases = {b.cost_basis for b in self.seats.values()
                 if b.model is not None}
        if len(bases) == 1:
            return bases.pop()
        if not bases:
            return COST_BASIS_NOT_PROVIDED
        return "mixed"

    def cost_less_seats(self, seats) -> list[str]:
        """Of `seats`, those with a bound model whose cost
        basis is not-provided (a USD cap cannot meter
        them)."""
        return [s for s in seats
                if self.seats[s].model is not None
                and not self.seats[s].reports_cost]


def preset_bindings() -> Bindings:
    """The reference preset: exactly ``reference_profile``."""
    base = _rp.PROVIDER_BASE_URL
    seats = {
        "proposer": SeatBinding(
            "proposer", base, _rp.PROPOSER_MODEL, PRESET_KEY_ENV,
            dialect=DIALECT_OPENROUTER),
        "judge": SeatBinding(
            "judge", base, _rp.JUDGE_MODEL, PRESET_KEY_ENV,
            decisions_url=_rp.DECISIONS_URL,
            dialect=DIALECT_OPENROUTER),
        "escalation": SeatBinding(
            "escalation", base, _rp.ESCALATION_MODEL,
            PRESET_KEY_ENV, decisions_url=_rp.DECISIONS_URL,
            dialect=DIALECT_OPENROUTER),
        "drafting": SeatBinding(
            "drafting", base, None, PRESET_KEY_ENV,
            dialect=DIALECT_OPENROUTER),
        "embedder": SeatBinding(
            "embedder", base, _rp.EMBED_MODEL, PRESET_KEY_ENV,
            dialect=DIALECT_OPENROUTER),
    }
    return Bindings(seats=seats)


def _resolve_dialect(env: dict, tag: str, preset_dialect: str,
                     seat_name: str, route_rebound: bool) -> str:
    """Dialect resolution: an explicit declaration (per-seat,
    then global) wins. Undeclared, the preset's dialect is
    inherited only while the seat still rides the preset's
    route — a seat rebound to another base URL defaults to
    the portable ``openai`` dialect (design §6: openai is
    the default for custom bindings), so provider-only
    fields are never sprayed at a route that did not ask
    for them."""
    raw = (env.get(f"ONTOSMITH_{tag}_DIALECT")
           or env.get("ONTOSMITH_DIALECT"))
    if raw is None:
        return DIALECT_OPENAI if route_rebound else preset_dialect
    dialect = raw.strip().lower()
    if dialect not in DIALECTS:
        raise ValueError(
            f"{seat_name} seat: unknown provider dialect "
            f"{raw!r}; expected one of {', '.join(DIALECTS)} "
            f"(ONTOSMITH_{tag}_DIALECT)")
    return dialect


def _resolve_prices(env: dict, tag: str,
                    seat_name: str) -> tuple[float | None,
                                              float | None]:
    """The seat's optional user-declared rates: both
    variables or neither — a half-declared table is a
    named error, never a silent half-price."""
    raw_in = env.get(f"ONTOSMITH_{tag}_PRICE_IN_PER_MTOK")
    raw_out = env.get(f"ONTOSMITH_{tag}_PRICE_OUT_PER_MTOK")
    if raw_in is None and raw_out is None:
        return None, None
    if raw_in is None or raw_out is None:
        missing = ("PRICE_IN_PER_MTOK" if raw_in is None
                   else "PRICE_OUT_PER_MTOK")
        raise ValueError(
            f"{seat_name} seat: a declared price table needs "
            f"both rates; ONTOSMITH_{tag}_{missing} is not set")
    try:
        return float(raw_in), float(raw_out)
    except ValueError:
        raise ValueError(
            f"{seat_name} seat: declared rates must be "
            f"numbers (USD per million tokens); got "
            f"ONTOSMITH_{tag}_PRICE_IN_PER_MTOK={raw_in!r}, "
            f"ONTOSMITH_{tag}_PRICE_OUT_PER_MTOK={raw_out!r}"
        ) from None


def _resolve_capabilities(env: dict, tag: str, seat_name: str,
                          model: str | None) -> frozenset | None:
    """The seat's explicit capability declaration, or
    None when undeclared (derive from the model id). An
    explicitly empty value declares no capabilities. A
    list containing +/- entries ADJUSTS the derived set
    instead of replacing it."""
    raw = env.get(f"ONTOSMITH_{tag}_CAPABILITIES")
    if raw is None:
        return None
    caps = frozenset(t.strip() for t in raw.split(",")
                     if t.strip())
    unknown = (frozenset(t.lstrip("+-") for t in caps)
               - frozenset(CAPABILITIES))
    if unknown:
        raise ValueError(
            f"{seat_name} seat: unknown capabilities "
            f"{sorted(unknown)}; expected a comma-separated "
            f"list from {', '.join(CAPABILITIES)} "
            f"(ONTOSMITH_{tag}_CAPABILITIES)")
    if any(t.startswith(("+", "-")) for t in caps):
        adjusted = set(derive_capabilities(model))
        for t in caps:
            if t.startswith("-"):
                adjusted.discard(t[1:])
            else:
                adjusted.add(t.lstrip("+"))
        return frozenset(adjusted)
    return caps


def resolve_bindings(env: dict | None = None) -> Bindings:
    """Resolve the effective bindings: per-seat env
    overrides, then global env overrides, then the
    reference preset (see module docstring)."""
    env = os.environ if env is None else env
    preset = preset_bindings()
    seats: dict[str, SeatBinding] = {}
    declared: set[str] = set()
    for name in SEATS:
        p = preset.seats[name]
        tag = name.upper()
        base_url = (env.get(f"ONTOSMITH_{tag}_BASE_URL")
                    or env.get("ONTOSMITH_BASE_URL")
                    or p.base_url)
        model = env.get(f"ONTOSMITH_{tag}_MODEL") or p.model
        key_env = (env.get(f"ONTOSMITH_{tag}_KEY_ENV")
                   or env.get("ONTOSMITH_KEY_ENV")
                   or p.key_env)
        decisions_url = (env.get(f"ONTOSMITH_{tag}_DECISIONS_URL")
                         or p.decisions_url)
        # A user who rebinds the seat to a DIFFERENT base
        # URL but says nothing about the decisions route
        # does not mean the preset's provider-specific
        # alpha route: the route derives from their base
        # URL instead. (Declaring the preset's own base URL
        # is not a rebind — the alpha route stays.)
        if base_url.rstrip("/") != p.base_url \
                and not env.get(f"ONTOSMITH_{tag}_DECISIONS_URL"):
            decisions_url = None
        price_in, price_out = _resolve_prices(env, tag, name)
        binding = SeatBinding(
            name, base_url.rstrip("/"), model, key_env,
            decisions_url=decisions_url,
            dialect=_resolve_dialect(
                env, tag, p.dialect, name,
                route_rebound=(base_url.rstrip("/") != p.base_url)),
            price_in_per_mtok=price_in,
            price_out_per_mtok=price_out,
            capabilities=_resolve_capabilities(env, tag, name,
                                               model))
        if binding != p:
            declared.add(name)
        seats[name] = binding
    return Bindings(seats=seats, declared=frozenset(declared))


def seat(name: str, env: dict | None = None) -> SeatBinding:
    """Resolve one seat's effective binding."""
    return resolve_bindings(env).seat(name)


# ----------------------------------------------------------------------
# Usage normalization + the stop-line rule
# ----------------------------------------------------------------------

def normalize_usage(raw: dict | None,
                    binding: SeatBinding | None = None) -> dict:
    """The single usage-normalization point: a provider's
    raw usage block becomes the internal shape
    ``{input_tokens, output_tokens, cost, cost_basis}``.

    Token counts are always recorded (either token-key
    spelling is accepted). ``cost`` is the provider's own
    figure when the response carries one
    (``provider-reported``); else it is computed from the
    seat's user-declared rates when the binding declares
    them (``estimated``); else it is ``None``
    (``not-provided``) — never a silently invented 0.0,
    and never priced from a built-in table.
    """
    raw = raw or {}
    in_tok = raw.get("prompt_tokens", raw.get("input_tokens", 0))
    out_tok = raw.get("completion_tokens",
                      raw.get("output_tokens", 0))
    in_tok = int(in_tok or 0)
    out_tok = int(out_tok or 0)
    cost_raw = raw.get("cost")
    if cost_raw is not None:
        return {"input_tokens": in_tok, "output_tokens": out_tok,
                "cost": float(cost_raw),
                "cost_basis": COST_BASIS_PROVIDER}
    if binding is not None \
            and binding.price_in_per_mtok is not None \
            and binding.price_out_per_mtok is not None:
        est = (in_tok * binding.price_in_per_mtok
               + out_tok * binding.price_out_per_mtok) / 1_000_000
        return {"input_tokens": in_tok, "output_tokens": out_tok,
                "cost": est, "cost_basis": COST_BASIS_ESTIMATED}
    return {"input_tokens": in_tok, "output_tokens": out_tok,
            "cost": None, "cost_basis": COST_BASIS_NOT_PROVIDED}


def add_cost(total: float | None,
             add: float | None) -> float | None:
    """None-aware spend accumulation: an unmetered (None)
    addend makes the total unknown (None) — an unmetered
    call's spend is never silently folded in as 0.0. A
    *missing* cost key is the caller's concern (stub / no-
    usage records pass 0.0 explicitly); producers that went
    through :func:`normalize_usage` always set the key."""
    if total is None or add is None:
        return None
    return total + add


def require_metered(bindings: Bindings, cap_usd: float | None,
                    seats) -> None:
    """The stop-line rule: a USD cap can only guard spend
    it can meter. When `cap_usd` is set and any of `seats`
    resolves to a cost-less binding, raise
    :class:`BindingCostError` naming the seats, the cap,
    and the remedies. No cap (None) never refuses."""
    if cap_usd is None:
        return
    bad = bindings.cost_less_seats(seats)
    if not bad:
        return
    named = ", ".join(bad)
    raise BindingCostError(
        f"max_cost_usd ${cap_usd:.2f} is set, but the "
        f"following seat(s) cannot meter cost "
        f"(cost_basis: {COST_BASIS_NOT_PROVIDED}): {named}. "
        f"A USD cap cannot guard spend it cannot meter. "
        f"Either bind those seats to a cost-reporting "
        f"route (dialect: {DIALECT_OPENROUTER}), declare "
        f"per-seat rates (ONTOSMITH_<SEAT>_PRICE_IN_PER_MTOK "
        f"and ONTOSMITH_<SEAT>_PRICE_OUT_PER_MTOK, USD per "
        f"million tokens), or remove the cap "
        f"(unset ONTOSMITH_MAX_COST_USD).")
