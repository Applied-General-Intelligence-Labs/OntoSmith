"""OntoSmith unified CLI.

Usage:
    python -m pipeline <command> [options]
    python -m pipeline help <command>

Commands are grouped below: Core (the build-and-serve
product), Authoring (pack authoring), and Serve. Every
command forwards its options to the underlying module, so
`python -m pipeline batch --help` works too. The lab's
measurement commands (review, evaluate, the gate runners,
and friends) are not part of the product surface; they run
from their module entries, e.g. `python -m
pipeline.gate_cli triple`.
"""
from __future__ import annotations

import sys

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"

# name: (module, group, description, fixed leading args)
COMMANDS: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "configure": ("pipeline.configure", "Core",
                  "Declare provider bindings for the seats "
                  "(interactive)", ()),
    "profile": ("pipeline.profiler", "Core",
                "Corpus profiler: stage-0 profile of a "
                "corpus (deterministic, no LLM)", ()),
    "batch": ("pipeline.batch_cli", "Core",
              "Batch pipeline: create/run/status/rollback "
              "+ ER settlement", ()),
    "inspect": ("pipeline.inspect", "Core",
                "Evidence/provenance viewer: one triple, "
                "its evidence + provenance", ()),
    "verify": ("harness.verification.verify_cli", "Core",
               "Verification cores: score/audit-sets/project/report",
               ()),
    "ontology": ("pipeline.ontology_builder", "Authoring",
                 "Ontology builder: draft/validate/approve/"
                 "emit a pack; check validates a pack "
                 "directory", ()),
    "identity-freeze": ("pipeline.identity_freeze",
                        "Authoring",
                        "Freeze per-point identity-seat "
                        "decisions into a global decisions "
                        "file", ()),
    "search": ("pipeline.search", "Serve",
               "Hybrid search over a built graph "
               "(chunks + triples)", ()),
    "serve": ("api.server", "Serve",
              "Sample API server: search / entity / "
              "neighborhood / triple endpoints", ()),
    "quickstart": ("pipeline.quickstart", "Serve",
                   "First run: build a graph from one text "
                   "file and serve it", ()),
}

GROUPS = ["Core", "Authoring", "Serve"]

# Lab measurement commands that left the unified CLI in 0.1.7,
# mapped to the module entry that still runs them.
RETIRED: dict[str, str] = {
    "review": "python -m pipeline.review",
    "evaluate": "python -m pipeline.evaluate",
    "recalibrate": "python -m pipeline.recalibrate",
    "calibration": "python -m pipeline.calibration",
    **{g: f"python -m pipeline.gate_cli {g}"
       for g in ("llm", "ollama", "triple", "er", "conflict",
                 "onto", "cascade")},
}


def _unknown_command(name: str) -> None:
    if name in RETIRED:
        print(f"{MAGENTA}`{name}` is no longer part of the "
              f"ontosmith CLI; run it as: {RETIRED[name]}{RESET}",
              file=sys.stderr)
        return
    print(f"{MAGENTA}Unknown command: {name}{RESET}\n")
    print_help()

BANNER = r"""
   ___        _        ____            _ _   _
  / _ \ _ __ | |_ ___ / ___| _ __ ___ (_) |_| |__
 | | | | '_ \| __/ _ \\___ \| '_ ` _ \| | __| '_ \
 | |_| | | | | |_ (_) |___) | | | | | | | |_| | | |
  \___/|_| |_|\__\___/|____/|_| |_| |_|_|\__|_| |_|
"""


def print_help() -> None:
    """Print the grouped command list."""
    print(f"{BOLD}{CYAN}{BANNER}{RESET}")
    print(f"  {DIM}decision-gated KG construction — judges, "
          f"not generators{RESET}\n")
    print(f"  {DIM}Usage:{RESET} python -m pipeline "
          f"{BOLD}<command>{RESET} [options]\n")
    for group in GROUPS:
        print(f"  {BOLD}{YELLOW}{group}{RESET}")
        for name, (_mod, grp, desc, _fixed) in COMMANDS.items():
            if grp == group:
                print(f"    {GREEN}{name:<12}{RESET} {DIM}{desc}{RESET}")
        print()
    print(f"  {DIM}python -m pipeline help <command>{RESET}  "
          f"for command options\n")


def print_command_help(name: str) -> None:
    """Show one command's own --help by dispatching to it."""
    mod_name, _, desc, fixed = COMMANDS[name]
    print(f"\n{BOLD}{GREEN}{name}{RESET} {DIM}— {desc}{RESET}")
    print(f"{DIM}Module: {mod_name}. Full options below:{RESET}\n")
    mod = __import__(mod_name, fromlist=["main"])
    try:
        if fixed:
            mod.main([*fixed, "--help"])
        else:
            sys.argv = [f"pipeline {name}", "--help"]
            mod.main()
    except SystemExit:
        pass


def main() -> None:
    """Dispatch argv[1] to the registered command module."""
    if len(sys.argv) < 2 or (sys.argv[1] in ("-h", "--help", "help") \
            and len(sys.argv) == 2):
        print_help()
        return
    if sys.argv[1] == "help" and len(sys.argv) > 2:
        name = sys.argv[2]
        if name in COMMANDS:
            print_command_help(name)
        else:
            _unknown_command(name)
        return

    name = sys.argv[1]
    if name not in COMMANDS:
        _unknown_command(name)
        sys.exit(2)

    mod_name, _, _, fixed = COMMANDS[name]
    mod = __import__(mod_name, fromlist=["main"])
    # Named binding refusals (a seat's key variable unset, a
    # seat used unbound) are user-facing errors with complete
    # messages — print them cleanly, never as a traceback.
    from .connections import (
        BindingCostError,
        BindingKeyError,
        BindingModelError,
    )
    from .neo4j_store import StoreIdentityError, StoreUnreachableError
    try:
        if fixed:
            rc = mod.main([*fixed, *sys.argv[2:]])
        else:
            sys.argv = [f"pipeline {name}", *sys.argv[2:]]
            rc = mod.main()
    except (BindingKeyError, BindingModelError,
            BindingCostError, StoreUnreachableError,
            StoreIdentityError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    # Several command mains return a process exit code
    # (ontology, profile, inspect, search, serve, verify);
    # honor it instead of dropping it — the `ontology check`
    # clean/defect verdict rides on it.
    if isinstance(rc, int):
        sys.exit(rc)


if __name__ == "__main__":
    main()
