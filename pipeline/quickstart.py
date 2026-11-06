"""The first-run door: one document in, a served graph out.

``ontosmith quickstart <file.txt>`` is a thin wrapper over the
existing machinery — batch create + run under the open-domain
pack, then serve — sequenced for a first run:

1. Preflight, before anything is created: the document must
   exist, the proposer seat's key must resolve, and a Neo4j
   must answer at the graph-store URL (the build projects
   its graph into Neo4j at WRITE — a first run learns that
   before spending, not after). Each refusal names its
   remedy, never a half-made batch.
2. Create + run a single-document batch (batch id
   ``quickstart-<stem>`` unless overridden). The batch is a
   real batch: it keeps the full stage record, can be resumed
   with ``ontosmith batch run``, and is served from the same
   state root as any other build. On a binding that meters
   cost, the build is capped at $2 unless
   ONTOSMITH_MAX_COST_USD says otherwise.
3. Serve the batch and open the demo app in a browser.

The command adds no pipeline behavior of its own; every step
is the corresponding batch/serve step with first-run
defaults. ``--no-serve`` stops after the build and prints
the serve command instead.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

# Default first-run spend cap (USD) on a metered binding;
# ONTOSMITH_MAX_COST_USD overrides it.
QUICKSTART_CAP_USD = 2.0


def _neo4j_url() -> str:
    return os.environ.get("NEO4J_URL", "http://localhost:7474")


def _neo4j_reachable() -> bool:
    """True when a Neo4j answers at the graph-store URL
    (its discovery endpoint needs no credentials)."""
    try:
        req = urllib.request.Request(_neo4j_url(), method="GET")
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status < 500
    except Exception:
        return False


def _batch_id_for(path: Path, override: str | None) -> str:
    if override:
        return override
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", path.stem).strip("-")
    return f"quickstart-{stem or 'document'}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="ontosmith quickstart",
        description="Build a knowledge graph from one text "
                    "document and serve it: batch create + "
                    "run (open-domain pack), then serve.")
    ap.add_argument("file",
                    help="the text document to build from")
    ap.add_argument("--batch-id", default=None,
                    help="batch id (default: "
                         "quickstart-<file stem>)")
    ap.add_argument("--port", type=int, default=8765,
                    help="serve port (default: 8765)")
    ap.add_argument("--no-serve", action="store_true",
                    help="build only; print the serve "
                         "command instead of starting it")
    args = ap.parse_args(argv)

    path = Path(args.file)
    if not path.is_file():
        print(f"quickstart: no such file: {path}",
              file=sys.stderr)
        return 2

    # Preflight: the proposer seat's key must resolve before
    # anything is created — a first run must fail as a clear
    # instruction, not as a half-built batch.
    from . import connections
    try:
        connections.seat("proposer").require_key()
    except connections.BindingKeyError as e:
        print(f"quickstart: {e}", file=sys.stderr)
        print("quickstart: set the key variable the message "
              "names (for the reference binding: "
              "OPENROUTER_API_KEY), or declare your own "
              "binding with `ontosmith configure`.",
              file=sys.stderr)
        return 1

    if not _neo4j_reachable():
        print(f"quickstart: no Neo4j answering at "
              f"{_neo4j_url()} — the build projects its "
              f"graph into Neo4j, so start one first, for "
              f"example:\n"
              f"  docker run -d --name ontosmith-neo4j "
              f"-p 7474:7474 -p 7687:7687 \\\n"
              f"    -e NEO4J_AUTH=neo4j/<password> neo4j:5\n"
              f"then set NEO4J_URL / NEO4J_PASSWORD to "
              f"match and re-run.", file=sys.stderr)
        return 1

    batch_id = _batch_id_for(path, args.batch_id)
    from argparse import Namespace

    from . import batch_cli, connections
    # A first run should not spend without limit: when the
    # seats can meter cost and no ONTOSMITH_MAX_COST_USD is
    # set, cap the build at QUICKSTART_CAP_USD. A cost-less
    # binding (e.g. local Ollama) runs uncapped, since a cap
    # there could never trip and would only refuse.
    default_cap = None
    if not connections.resolve_bindings().cost_less_seats(
            list(batch_cli.CAP_SEATS)):
        default_cap = QUICKSTART_CAP_USD
    create_args = Namespace(
        source="quickstart", collection="quickstart",
        docs=[str(path)], batch_id=batch_id, profile=None,
        pack="open-domain", default_max_cost_usd=default_cap)
    try:
        batch_cli.cmd_create(create_args)
        batch_cli.cmd_run(Namespace(
            batch_id=batch_id, reuse_run=False))
    except SystemExit as e:
        return int(e.code or 0)

    from .batch import BatchStatus, batch_dir, load_batch
    from .batch_cli import state_root
    b = load_batch(state_root(), batch_id)
    if b.status != BatchStatus.VERIFIED:
        print(f"quickstart: batch {batch_id} finished at "
              f"{b.status.value}, not VERIFIED — inspect it "
              f"with `ontosmith batch status {batch_id}` and "
              f"resume with `ontosmith batch run {batch_id}`.",
              file=sys.stderr)
        return 1

    triples = 0
    written = batch_dir(state_root(), batch_id) \
        / "written_triples.jsonl"
    if written.is_file():
        triples = sum(1 for line in written.read_text()
                      .splitlines() if line.strip())
    print(f"quickstart: built {triples} triples from "
          f"{path.name} (batch {batch_id})")

    if args.no_serve:
        print(f"quickstart: serve it with: ontosmith serve "
              f"--batch {batch_id} --port {args.port}")
        return 0

    url = f"http://127.0.0.1:{args.port}/"
    threading.Timer(1.5, webbrowser.open, args=[url]).start()
    from api.server import main as serve_main
    return serve_main(["--batch", batch_id,
                       "--port", str(args.port)])


if __name__ == "__main__":
    sys.exit(main())
