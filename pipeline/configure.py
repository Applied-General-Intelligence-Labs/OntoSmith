"""``ontosmith configure`` — the bindings front door.

One mechanism, two doors, one artifact: an
``ontosmith-bindings.env`` file in the full per-seat
canonical form (see :func:`render_bindings`). The
interactive door walks the seats with the shipped preset's
values as the starting point; the non-interactive door
validates a hand-filled copy of the shipped template
(``docs/bindings-template.env``) through the same writer,
so both doors produce byte-identical files for identical
answers.

Keys are referenced by variable NAME only — a key value is
never asked for, never written, and never printed. Probes
are zero-spend: key presence, ``GET {base}/models``, and a
decisions-route presence check (an empty body whose only
job is to prove the route exists). A probe failure still
writes the file — stamped not-validated in its header —
and exits non-zero.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import urllib.error
import urllib.request
from importlib import resources
from pathlib import Path

from . import connections
from .connections import SeatBinding

DEFAULT_OUT = "ontosmith-bindings.env"
#: The template ships as package data so
#: --emit-template resolves from an installed wheel, not
#: only from a source tree's docs/ directory. The docs/
#: copy is the documented twin; a test pins them equal.
TEMPLATE_RESOURCE = "bindings-template.env"


def template_text() -> str:
    """The shipped bindings template, read from the
    installed package (importlib.resources)."""
    return resources.files("pipeline").joinpath(
        TEMPLATE_RESOURCE).read_text(encoding="utf-8")

_HEADER = """\
# OntoSmith seat bindings — where each model seat points.
# Written by `ontosmith configure`; safe to edit by hand and
# re-validate with `ontosmith configure --from-file <file>`.
# Keys are referenced by variable NAME only — no key value
# ever appears in this file. Load it before running:
#   set -a; . ./ontosmith-bindings.env; set +a
"""


# ----------------------------------------------------------------------
# Canonical rendering
# ----------------------------------------------------------------------

def _q(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace(
        '"', '\\"') + '"'


def render_bindings(bindings: connections.Bindings,
                    validation: str) -> str:
    """The canonical bindings file for a resolved Bindings.

    Every bound seat is written in full (base URL, model,
    key variable name, dialect; decisions URL, declared
    rates and declared capabilities when they are in force).
    An unbound drafting seat is written as a comment and the
    ABSENCE of its MODEL line — the state the resolver
    already represents. Deterministic: same bindings in,
    same bytes out.
    """
    lines = [_HEADER.rstrip("\n"),
             f"# Binding: {bindings.provenance()['name']}",
             f"# Validation: {validation}"]
    for name in connections.SEATS:
        seat = bindings.seat(name)
        tag = name.upper()
        lines.append("")
        if seat.model is None:
            lines.append(
                f"# {name} seat: unbound — no model declared "
                f"(drafting is disabled under this binding)")
            continue
        lines.append(f"ONTOSMITH_{tag}_BASE_URL={_q(seat.base_url)}")
        lines.append(f"ONTOSMITH_{tag}_MODEL={_q(seat.model)}")
        lines.append(f"ONTOSMITH_{tag}_KEY_ENV={_q(seat.key_env)}")
        lines.append(f"ONTOSMITH_{tag}_DIALECT={_q(seat.dialect)}")
        if name in ("judge", "escalation"):
            # The effective decisions route, declared or
            # derived — written explicitly so the file alone
            # determines the binding (and the interactive
            # door, which lets it derive, renders the same
            # bytes as a file that declares it).
            lines.append(
                f"ONTOSMITH_{tag}_DECISIONS_URL="
                f"{_q(seat.decisions_endpoint)}")
        if seat.price_in_per_mtok is not None:
            lines.append(
                f"ONTOSMITH_{tag}_PRICE_IN_PER_MTOK="
                f"{_q(repr(seat.price_in_per_mtok))}")
            lines.append(
                f"ONTOSMITH_{tag}_PRICE_OUT_PER_MTOK="
                f"{_q(repr(seat.price_out_per_mtok))}")
        if seat.capabilities is not None:
            lines.append(
                f"ONTOSMITH_{tag}_CAPABILITIES="
                f"{_q(','.join(sorted(seat.capabilities)))}")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
# Env-file parsing (the --from-file door)
# ----------------------------------------------------------------------

def parse_bindings_file(text: str) -> dict[str, str]:
    """Parse an env-style bindings file into a dict.

    Accepts KEY=value and KEY="value" (with \\" and \\\\
    escapes), blank lines and # comments. A leading
    ``export `` (the shell style a hand-written file
    naturally takes) is accepted as the same declaration:
    silently dropping those lines made a
    shell-style file resolve to the preset and stamp a
    false `reference`. Keys that are not ONTOSMITH_*
    declarations are ignored.
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value.startswith('"') and value.endswith('"') \
                and len(value) >= 2:
            value = value[1:-1].replace('\\"', '"') \
                               .replace("\\\\", "\\")
        elif value.startswith("'") and value.endswith("'") \
                and len(value) >= 2:
            value = value[1:-1]
        if key.startswith("ONTOSMITH_"):
            out[key] = value
    return out


def unfilled_placeholders(env: dict[str, str]) -> list[str]:
    """Keys whose value still holds a template <…> marker."""
    import re
    return sorted(k for k, v in env.items()
                  if re.search(r"<[^>\n]+>", v))


def resolve_from(env_map: dict[str, str]) -> connections.Bindings:
    """Resolve bindings from a file's declarations alone:
    ambient ONTOSMITH_* variables never leak into a file's
    resolution (the file is the whole declaration)."""
    scrubbed = {k: v for k, v in os.environ.items()
                if not k.startswith("ONTOSMITH_")}
    scrubbed.update(env_map)
    return connections.resolve_bindings(scrubbed)


# ----------------------------------------------------------------------
# Zero-spend probes
# ----------------------------------------------------------------------

def _plain_transport_error(e: BaseException) -> str:
    """A transport failure in plain words: the
    probe report (and the artifact header built from it)
    names what happened — 'connection refused', 'timed
    out' — never urllib's raw '<urlopen error [Errno …]>'
    plumbing."""
    reason = getattr(e, "reason", e)
    if isinstance(reason, OSError):
        return reason.strerror or str(reason)
    return str(reason)


def _http(method: str, url: str, key: str | None,
          body: bytes | None = None, timeout: int = 10):
    """One probe request; returns (status, parsed_json|None).
    Raises urllib.error.URLError / OSError on transport
    failure. HTTP error statuses are returned, not raised."""
    req = urllib.request.Request(url, data=body, method=method)
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw or b"null")
            except json.JSONDecodeError:
                return resp.status, None
    except urllib.error.HTTPError as e:
        return e.code, None


def probe_bindings(bindings: connections.Bindings,
                   key_source: dict | None = None) -> tuple[
                       list[str], list[str]]:
    """Run the §7 probes; returns (failures, warnings).

    - key presence: each bound seat's key variable is set
      (presence only — values are used transiently as the
      probe's bearer token and never printed or stored);
    - GET {base}/models per distinct base URL: 200 means
      reachable and authenticated; a declared model missing
      from the returned list is a warning, not a failure;
    - judge/escalation seats with a decisions URL: an
      empty-body POST — any structural response proves the
      route exists; 404 or a transport failure means it
      does not. The embedder seat is probed via /models
      only (never an embeddings call).
    """
    keys = os.environ if key_source is None else key_source
    failures: list[str] = []
    warnings: list[str] = []
    bound = [bindings.seat(n) for n in connections.SEATS
             if bindings.seat(n).model is not None]

    for seat in bound:
        if not keys.get(seat.key_env):
            failures.append(
                f"{seat.seat} seat: key variable {seat.key_env} "
                f"is not set in this environment")

    models_by_base: dict[str, list[str] | None] = {}
    for seat in bound:
        if seat.base_url in models_by_base:
            continue
        key = keys.get(seat.key_env) or ""
        try:
            status, payload = _http(
                "GET", f"{seat.base_url}/models", key)
        except (urllib.error.URLError, OSError,
                http.client.HTTPException) as e:
            failures.append(
                f"GET {seat.base_url}/models unreachable: "
                f"{_plain_transport_error(e)}")
            models_by_base[seat.base_url] = None
            continue
        if status != 200:
            failures.append(
                f"GET {seat.base_url}/models returned HTTP "
                f"{status} (route unreachable or key rejected)")
            models_by_base[seat.base_url] = None
            continue
        ids = [d.get("id") for d in (payload or {}).get("data", [])
               if isinstance(d, dict)]
        models_by_base[seat.base_url] = ids

    for seat in bound:
        ids = models_by_base.get(seat.base_url)
        if ids and seat.model not in ids:
            warnings.append(
                f"{seat.seat} seat: model {seat.model} is not "
                f"in {seat.base_url}/models (the route may "
                f"still serve it — warning only)")

    for seat in bound:
        if seat.seat not in ("judge", "escalation"):
            continue
        route = seat.decisions_endpoint
        key = keys.get(seat.key_env) or ""
        try:
            status, _ = _http("POST", route, key, body=b"{}")
        except (urllib.error.URLError, OSError,
                http.client.HTTPException) as e:
            failures.append(
                f"{seat.seat} seat: decisions route "
                f"{route} unreachable: "
                f"{_plain_transport_error(e)}")
            continue
        if status == 404:
            failures.append(
                f"{seat.seat} seat: decisions route "
                f"{route} returned 404 (route not present "
                f"at the declared base URL)")
    return failures, warnings


# ----------------------------------------------------------------------
# Stamp + notes printing
# ----------------------------------------------------------------------

def print_stamp(bindings: connections.Bindings) -> None:
    prov = bindings.provenance()
    print(f"binding stamp: {prov['name']}")
    for name in connections.SEATS:
        s = prov["seats"][name]
        if s["model"] is None:
            print(f"  {name:<11} unbound")
            continue
        print(f"  {name:<11} {s['model']} @ {s['host']} "
              f"(dialect {s['dialect']}, cost {s['cost_basis']})")


def print_cost_notes(bindings: connections.Bindings) -> None:
    less = bindings.cost_less_seats(list(connections.SEATS))
    if less:
        print("note: seat(s) " + ", ".join(less) + " cannot "
              "meter cost (cost_basis: not-provided). A batch "
              "run under a USD cap (max_cost_usd) will refuse "
              "by name on this binding — declare per-seat "
              "rates (ONTOSMITH_<SEAT>_PRICE_IN_PER_MTOK / "
              "_OUT) or run without a cap (leave "
              "ONTOSMITH_MAX_COST_USD unset).")


# ----------------------------------------------------------------------
# Interactive door
# ----------------------------------------------------------------------

class _Prompter:
    def __init__(self, input_fn=input):
        self._input = input_fn

    def ask(self, label: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        try:
            raw = self._input(f"{label}{suffix}: ")
        except EOFError:
            raise KeyboardInterrupt from None
        raw = raw.strip()
        return raw if raw else default

    def ask_yes_no(self, label: str, default: bool = False) -> bool:
        hint = "Y/n" if default else "y/N"
        try:
            raw = self._input(f"{label} [{hint}]: ")
        except EOFError:
            raise KeyboardInterrupt from None
        raw = raw.strip().lower()
        if not raw:
            return default
        return raw.startswith("y")


def interactive_env(input_fn=input) -> dict[str, str]:
    """Walk the seats; returns the declaration dict (the
    same shape --from-file parses). Starting point: the
    shipped preset's values."""
    preset = connections.preset_bindings()
    preset_base = preset.seat("proposer").base_url
    p = _Prompter(input_fn)
    print("OntoSmith seat bindings — answer per seat; Enter "
          "keeps the shown value. Keys are referenced by "
          "variable NAME only; no key value is ever asked "
          "for or written.")
    base = p.ask("Provider base URL (shared)", preset_base)
    default_key = ("OPENROUTER_API_KEY" if base == preset_base
                   else "LLM_API_KEY")
    key_env = p.ask("Key variable name (shared)", default_key)
    default_dialect = ("openrouter" if base == preset_base
                       else "openai")
    dialect = p.ask("Provider dialect (shared: openai | "
                    "openrouter)", default_dialect)
    if dialect not in connections.DIALECTS:
        raise ValueError(
            f"unknown dialect {dialect!r}; expected one of "
            f"{', '.join(connections.DIALECTS)}")

    env: dict[str, str] = {}
    for name in connections.SEATS:
        tag = name.upper()
        p_seat: SeatBinding = preset.seat(name)
        print(f"\n— {name} seat —")
        if name == "drafting":
            if not p.ask_yes_no(
                    "Bind a drafting model? (the preset "
                    "leaves drafting unbound)", default=False):
                continue  # unbound: no lines written at all
            model = p.ask("  drafting model", "")
            if not model:
                print("  no model named — drafting stays "
                      "unbound")
                continue
        else:
            model = p.ask(f"  {name} model", p_seat.model or "")
        s_base, s_key, s_dialect = base, key_env, dialect
        s_decisions = None
        if p.ask_yes_no("  override this seat's connection?",
                        default=False):
            s_base = p.ask("    base URL", s_base)
            s_key = p.ask("    key variable name", s_key)
            s_dialect = p.ask("    dialect", s_dialect)
            if s_dialect not in connections.DIALECTS:
                raise ValueError(
                    f"unknown dialect {s_dialect!r}; expected "
                    f"one of {', '.join(connections.DIALECTS)}")
        if name in ("judge", "escalation") and s_base != preset_base:
            s_decisions = p.ask(
                "    decisions URL (blank = derive from the "
                "base URL)", "")
        env[f"ONTOSMITH_{tag}_BASE_URL"] = s_base
        env[f"ONTOSMITH_{tag}_MODEL"] = model
        env[f"ONTOSMITH_{tag}_KEY_ENV"] = s_key
        env[f"ONTOSMITH_{tag}_DIALECT"] = s_dialect
        if s_decisions:
            env[f"ONTOSMITH_{tag}_DECISIONS_URL"] = s_decisions

    if p.ask_yes_no("\nDeclare per-seat prices (for cost "
                    "estimates) or capabilities?",
                    default=False):
        for name in connections.SEATS:
            tag = name.upper()
            if f"ONTOSMITH_{tag}_MODEL" not in env:
                continue
            pin = p.ask(f"  {name} price in (USD per Mtok, "
                        f"blank = none)", "")
            if pin:
                pout = p.ask(f"  {name} price out (USD per "
                             f"Mtok)", "")
                if not pout:
                    raise ValueError(
                        f"{name}: a price pair must declare "
                        f"both in and out rates")
                env[f"ONTOSMITH_{tag}_PRICE_IN_PER_MTOK"] = pin
                env[f"ONTOSMITH_{tag}_PRICE_OUT_PER_MTOK"] = pout
            caps = p.ask(f"  {name} capabilities (comma "
                         f"separated, blank = derive)", "")
            if caps:
                env[f"ONTOSMITH_{tag}_CAPABILITIES"] = caps
    return env


# ----------------------------------------------------------------------
# Shared finish: resolve, probe, write, report
# ----------------------------------------------------------------------

def finish(env_map: dict[str, str], out_path: Path,
           probe: bool) -> int:
    try:
        bindings = resolve_from(env_map)
    except (ValueError, connections.BindingModelError) as e:
        print(f"configure: {e}", file=sys.stderr)
        return 2
    failures: list[str] = []
    warnings: list[str] = []
    if probe:
        failures, warnings = probe_bindings(bindings)
        validation = ("validated" if not failures
                      else "NOT VALIDATED — probe failed: "
                      + "; ".join(failures))
    else:
        validation = "not probed (--no-probe)"
    out_path.write_text(render_bindings(bindings, validation))
    print(f"wrote {out_path}")
    print_stamp(bindings)
    for w in warnings:
        print(f"warning: {w}")
    for f in failures:
        print(f"probe failure: {f}", file=sys.stderr)
    print_cost_notes(bindings)
    if failures:
        print("the file is written but stamped NOT "
              "VALIDATED; fix the probe failures and re-run",
              file=sys.stderr)
        return 2
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="ontosmith configure",
        description="Declare the provider bindings for "
                    "OntoSmith's model seats and write them "
                    "to an ontosmith-bindings.env file "
                    "(keys by variable name only).")
    ap.add_argument("--out", default=None,
                    help="output file (default: "
                         "ontosmith-bindings.env; with "
                         "--from-file, the input file itself)")
    ap.add_argument("--from-file", default=None,
                    metavar="PATH",
                    help="validate a filled bindings file "
                         "(e.g. a copy of "
                         "docs/bindings-template.env) instead "
                         "of prompting")
    ap.add_argument("--emit-template", action="store_true",
                    help="print the shipped bindings "
                         "template and exit")
    ap.add_argument("--no-probe", action="store_true",
                    help="skip the zero-spend probes (the "
                         "file is stamped 'not probed')")
    args = ap.parse_args(argv)

    if args.emit_template:
        try:
            sys.stdout.write(template_text())
        except (FileNotFoundError, ModuleNotFoundError) as e:
            print(f"configure: the shipped bindings template "
                  f"could not be read from the installed "
                  f"package ({e}); the installation is "
                  f"incomplete — reinstall ontosmith",
                  file=sys.stderr)
            return 2
        return 0

    if args.from_file:
        src = Path(args.from_file)
        env_map = parse_bindings_file(src.read_text())
        unfilled = unfilled_placeholders(env_map)
        if unfilled:
            print("configure: template placeholders not "
                  "filled in: " + ", ".join(unfilled),
                  file=sys.stderr)
            return 2
        out = Path(args.out) if args.out else src
        return finish(env_map, out, probe=not args.no_probe)

    try:
        env_map = interactive_env()
    except KeyboardInterrupt:
        print("\nconfigure: aborted — nothing written",
              file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"configure: {e}", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else Path(DEFAULT_OUT)
    return finish(env_map, out, probe=not args.no_probe)


if __name__ == "__main__":
    raise SystemExit(main())
