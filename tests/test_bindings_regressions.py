"""Bindings regressions.

The blockers lived in test blind spots: the
None-cost (unmetered binding) path was unit-tested at the
normalize/ledger level but never run through the GATES
stage or a whole build, and the pip-installed configure
door was never exercised from an installed layout. This
file carries the stage-level and end-to-end layers:

- stage_gates with a None-cost judge completes, prints a
  count-based projection (no fabricated dollars), and
  records the stage + total as null in the ledger
 ;
- the review's Try-It B as a repeatable end-to-end test:
  a cost-less custom binding on a stub OpenAI-compatible
  server (chat + systemone decisions + embeddings) —
  capped create refused by name, --no-cost-cap build
  completing through a stop-and-resume cycle, and the
  manifest/wire/config model triple in agreement
 ;
- the smaller findings' units: export-prefixed bindings
  files parse, probe failures speak plainly
 , a nonexistent batch id errors by name
 , cost_basis() on an empty seat set is
  not-provided and require_key never prints "model
  None", the shipped template's two copies are
  byte-identical.
"""
import argparse
import io
import json
import os
import re
import shutil
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from unittest import mock

from pipeline import batch_cli, batch_run, configure, connections
from pipeline.batch import (
    Batch,
    BatchConfig,
    BatchStatus,
    batch_dir,
    load_batch,
    save_batch,
)

STUB_MODELS = {"proposer": "acme/proposer-x",
               "judge": "acme/judge-y",
               "embedder": "acme/embed-z"}


def _clear_binding_env():
    for key in list(os.environ):
        if key.startswith("ONTOSMITH_"):
            del os.environ[key]


def _stub_env(base: str) -> dict:
    env = {"STUB_KEY": "stub-key"}
    for seat, model in STUB_MODELS.items():
        p = f"ONTOSMITH_{seat.upper()}"
        env[f"{p}_BASE_URL"] = base
        env[f"{p}_MODEL"] = model
        env[f"{p}_KEY_ENV"] = "STUB_KEY"
        env[f"{p}_DIALECT"] = "openai"
    return env


# ----------------------------------------------------------------------
# Stub provider (the Try-It B server)
# ----------------------------------------------------------------------

class _StubHandler(BaseHTTPRequestHandler):
    wire: ClassVar[list] = []
    fail_embeddings: ClassVar[bool] = False

    def log_message(self, *args):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._json(200, {"data": [
                {"id": m} for m in STUB_MODELS.values()]})
        else:
            self._json(404, {})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            payload = json.loads(raw or b"{}")
        except Exception:
            payload = {}
        type(self).wire.append((self.path, payload))
        if self.path.endswith("/systemone"):
            state = payload.get("state", "")
            noul = 0.40 if "Tomas Rey" in state else 0.99
            self._json(200, {"answers": {"q1": {"noul": noul}}})
        elif self.path.endswith("/chat/completions"):
            prompt = ""
            try:
                prompt = payload["messages"][0]["content"]
            except Exception:
                pass
            triples = []
            if "Mara Voss" in prompt:
                triples.append({
                    "subject": "Mara Voss", "verb": "founded",
                    "relation": "founded", "object": "Alder & Finch",
                    "quote": "Mara Voss founded Alder & Finch "
                             "in 2019"})
            if "Tomas Rey" in prompt:
                triples.append({
                    "subject": "Tomas Rey", "verb": "manages",
                    "relation": "manages", "object": "Alder & Finch",
                    "quote": "Tomas Rey manages Alder & Finch"})
            self._json(200, {
                "choices": [{"message": {
                    "content": json.dumps(triples)}}],
                "usage": {"prompt_tokens": 12,
                          "completion_tokens": 4}})
        elif self.path.endswith("/embeddings"):
            if type(self).fail_embeddings:
                self._json(500, {"error": "stub embeddings down"})
                return
            inputs = payload.get("input") or []
            if isinstance(inputs, str):
                inputs = [inputs]
            self._json(200, {
                "data": [{"object": "embedding", "index": i,
                          "embedding": [0.1 * (i + 1)] * 8}
                         for i, _ in enumerate(inputs)],
                "usage": {"prompt_tokens": 3}})
        else:
            self._json(404, {})


class _FakeGraph:
    """The non-Neo4j graph double stage_write needs
    (upsert_entities / write_triples); stage_verify
    records its graph checks as not-applicable, by
    design, for a store without a Neo4j handle."""

    def __init__(self):
        self.entities = []
        self.triples = []

    def upsert_entities(self, records):
        self.entities.extend(records)
        return len(records)

    def write_triples(self, triples):
        self.triples.extend(triples)
        return len(triples)


# ----------------------------------------------------------------------
# (a) Stage-level None-cost tests
# ----------------------------------------------------------------------

class TestStageGatesUnmetered(unittest.TestCase):
    def setUp(self):
        _clear_binding_env()
        self.addCleanup(_clear_binding_env)
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def _batch_with_candidates(self, n=2):
        cfg = BatchConfig(max_cost_usd=None)
        b = Batch(batch_id="bindings-stage", run_id="run-0001",
                  source="src", collection="coll",
                  doc_ids=["d1"], config=cfg,
                  status=BatchStatus.CANDIDATES_DONE)
        d = batch_dir(self.root, b.batch_id)
        d.mkdir(parents=True)
        cands = [{
            "doc_id": "d1", "subject": f"Subject {i}",
            "relation": "founded", "object": "Alder & Finch",
            "context": "Mara Voss founded Alder & Finch in 2019.",
        } for i in range(n)]
        (d / "candidates.jsonl").write_text(
            "".join(json.dumps(c) + "\n" for c in cands))
        save_batch(b, self.root)
        return b

    @staticmethod
    def _none_cost_decide(ctx, s, r, o, model=None):
        return 0.99, {"cost": None,
                      "cost_basis": "not-provided",
                      "input_tokens": 5, "output_tokens": 2}

    def test_unmetered_judge_completes_and_records_null(self):
        # The blocker shape: before the fix this
        # stage died with "unsupported operand type(s) for
        # +=: 'float' and 'NoneType'" on the first
        # candidate and wedged the batch.
        base = "http://127.0.0.1:9/v1"  # never called: decide_fn is injected
        with mock.patch.dict(os.environ, _stub_env(base)):
            b = self._batch_with_candidates()
            out = io.StringIO()
            with redirect_stdout(out):
                batch_run.stage_gates(
                    b, self.root, ["INGEST", "NER", "CANDIDATES"],
                    self._none_cost_decide)
        ledger = b.metrics["cost_ledger"]
        self.assertIsNone(ledger["GATES"])
        self.assertIsNone(ledger["total_usd"])
        self.assertEqual(ledger["metered_usd"], 0.0)
        self.assertIsNone(b.metrics["gates"]["cost_usd"])
        self.assertEqual(b.metrics["gates"]["judged"], 2)
        # the banner projects a judgment count on an
        # unmetered binding — no dollar figure is printed.
        banner = out.getvalue()
        self.assertIn("no $ projection", banner)
        self.assertNotIn("projected $", banner)
        self.assertIn("cost=unmetered", banner)

    def test_metered_judge_banner_keeps_dollar_projection(self):
        # The metered path is unchanged: preset judge
        # (provider-reported) still projects dollars and
        # still enforces the pre-check.
        with mock.patch.dict(
                os.environ, {"OPENROUTER_API_KEY": "sk-test"}):
            b = self._batch_with_candidates()
            out = io.StringIO()
            with redirect_stdout(out):
                batch_run.stage_gates(
                    b, self.root, ["INGEST", "NER", "CANDIDATES"],
                    lambda ctx, s, r, o, model=None: (
                        0.99, {"cost": 0.001,
                               "cost_basis": "provider-reported",
                               "input_tokens": 5,
                               "output_tokens": 2}))
        self.assertIn("projected $", out.getvalue())
        self.assertEqual(b.metrics["cost_ledger"]["GATES"], 0.002)
        self.assertEqual(b.metrics["cost_ledger"]["total_usd"],
                         0.002)


class TestRecordCostTotals(unittest.TestCase):
    def setUp(self):
        _clear_binding_env()
        self.addCleanup(_clear_binding_env)

    def _batch(self):
        return Batch(batch_id="bindings-ledger", run_id="run-1",
                     source="s", collection="c", doc_ids=[],
                     config=BatchConfig(max_cost_usd=None))

    def test_unmetered_stage_makes_total_unknown(self):
        b = self._batch()
        batch_run._record_cost(b, "INGEST", 0.0)
        batch_run._record_cost(b, "CANDIDATES", None)
        batch_run._record_cost(b, "GATES", 1.5)
        ledger = b.metrics["cost_ledger"]
        self.assertIsNone(ledger["total_usd"])
        self.assertEqual(ledger["metered_usd"], 1.5)
        self.assertIsNone(ledger["CANDIDATES"])

    def test_all_metered_total_is_the_sum(self):
        b = self._batch()
        batch_run._record_cost(b, "CANDIDATES", 0.5)
        batch_run._record_cost(b, "GATES", 1.5)
        ledger = b.metrics["cost_ledger"]
        self.assertEqual(ledger["total_usd"], 2.0)
        self.assertEqual(ledger["metered_usd"], 2.0)


# ----------------------------------------------------------------------
# (c) Try-It B as a repeatable end-to-end test
# ----------------------------------------------------------------------

class TestTryItBEndToEnd(unittest.TestCase):
    def setUp(self):
        _clear_binding_env()
        self.addCleanup(_clear_binding_env)
        _StubHandler.wire = []
        _StubHandler.fail_embeddings = False
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0),
                                       _StubHandler)
        self.addCleanup(self.srv.shutdown)
        threading.Thread(target=self.srv.serve_forever,
                         daemon=True).start()
        self.base = (f"http://127.0.0.1:"
                     f"{self.srv.server_address[1]}/v1")
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        docs = self.tmp / "docs"
        docs.mkdir()
        (docs / "a.txt").write_text(
            "Alder & Finch is a ceramics studio in Lisbon. "
            "Mara Voss founded Alder & Finch in 2019.\n")
        (docs / "b.txt").write_text(
            "Tomas Rey manages Alder & Finch. The studio sells "
            "stoneware bowls and cups to cafes across the city, "
            "and every piece is glazed by hand in small batches "
            "before it leaves the workshop.\n")
        self.docs_glob = str(docs / "*.txt")
        self.state = self.tmp / "state"
        self.env = _stub_env(self.base)
        self.env["ONTOSMITH_ROOT"] = str(self.state)

    def _create(self, capped=False):
        args = argparse.Namespace(
            source="stubsrc", collection="stubcoll",
            docs=[self.docs_glob], batch_id="fe3-e2e",
            profile=None, pack="core")
        env = dict(self.env)
        if capped:
            # The cap's only home is the internal env var.
            env["ONTOSMITH_MAX_COST_USD"] = "10"
        with mock.patch.dict(os.environ, env):
            with redirect_stdout(io.StringIO()):
                batch_cli.cmd_create(args)
        return load_batch(self.state, "fe3-e2e")

    def _run(self, reuse=False):
        from pipeline.file_registry import SqliteEntityRegistry
        b = load_batch(self.state, "fe3-e2e")
        d = batch_dir(self.state, "fe3-e2e")
        state_p = d / "state.json"
        done = (json.loads(state_p.read_text()).get(
            "stages_completed", []) if state_p.exists() else [])
        doc_texts = None
        if not done:
            paths = json.loads((d / "doc_paths.json").read_text())
            doc_texts = {p: Path(p).read_text() for p in paths}
        registry = SqliteEntityRegistry(
            self.state / "data" / "registry.db")
        out = io.StringIO()
        with mock.patch.dict(os.environ, self.env):
            with redirect_stdout(out):
                batch_run.run_batch(
                    b, self.state, doc_texts,
                    decide_fn=batch_cli._decide_fn,
                    er_decide_fn=batch_cli._er_decide_fn,
                    registry=registry, graph=_FakeGraph(),
                    live_decide=True, keep_run_id=reuse)
        return out.getvalue()

    def test_try_it_b_journey(self):
        # 1. A capped create on a cost-less binding refuses
        #    by name (the seats + the basis), before any
        #    batch exists.
        with self.assertRaises(
                connections.BindingCostError) as cm:
            self._create(capped=True)
        self.assertIn("not-provided", str(cm.exception))
        self.assertIn("proposer", str(cm.exception))

        # 2. An uncapped create freezes the seat models
        #    into the batch config.
        b = self._create()
        self.assertEqual(b.config.llm_model,
                         STUB_MODELS["proposer"])
        self.assertEqual(b.config.gate_model,
                         STUB_MODELS["judge"])
        self.assertEqual(b.config.embed_model,
                         STUB_MODELS["embedder"])
        run_id_at_create = b.run_id

        # 3. First run: the embedder is down, so the build
        #    stops FAILED at INDEX — after GATES judged on
        #    the unmetered binding (the wedge point).
        _StubHandler.fail_embeddings = True
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError):
            self._run()
        b = load_batch(self.state, "fe3-e2e")
        self.assertEqual(b.status, BatchStatus.FAILED)
        done = json.loads((batch_dir(self.state, "fe3-e2e")
                           / "state.json").read_text())[
            "stages_completed"]
        self.assertIn("GATES", done)
        self.assertNotIn("INDEX", done)
        # GATES recorded its spend as unknown, not $0.
        self.assertIsNone(b.metrics["cost_ledger"]["GATES"])

        # 4. Resume keeping the run_id: completes, and the
        #    run names exactly one run_id — the kept one
        #   .
        _StubHandler.fail_embeddings = False
        out = self._run(reuse=True)
        b = load_batch(self.state, "fe3-e2e")
        self.assertEqual(b.status, BatchStatus.VERIFIED)
        self.assertEqual(b.run_id, run_id_at_create)
        uuids = set(re.findall(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}", out))
        self.assertEqual(uuids, {run_id_at_create})

        # 5. Manifest provenance == config == wire
        #   : the models the manifest names are
        #    the models the stub actually saw.
        manifest = json.loads((batch_dir(self.state, "fe3-e2e")
                               / "manifest.json").read_text())
        seats = manifest["binding"]["seats"]
        for seat, model in STUB_MODELS.items():
            self.assertEqual(seats[seat]["model"], model)
        wire = _StubHandler.wire
        chat = {p.get("model") for path, p in wire
                if path.endswith("/chat/completions")}
        decisions = {p.get("model") for path, p in wire
                     if path.endswith("/systemone")}
        embeddings = {p.get("model") for path, p in wire
                      if path.endswith("/embeddings")}
        self.assertEqual(chat, {STUB_MODELS["proposer"]})
        self.assertEqual(decisions, {STUB_MODELS["judge"]})
        self.assertEqual(embeddings, {STUB_MODELS["embedder"]})

        # 6. Ledger + status agree: every live
        #    stage unmetered, the total null — and the
        #    status JSON prints it that way.
        ledger = b.metrics["cost_ledger"]
        for stage in ("CANDIDATES", "GATES", "INDEX"):
            self.assertIsNone(ledger[stage], stage)
        self.assertIsNone(ledger["total_usd"])
        self.assertEqual(ledger["metered_usd"], 0.0)
        out = io.StringIO()
        with mock.patch.dict(os.environ, self.env):
            with redirect_stdout(out):
                batch_cli.cmd_status(argparse.Namespace(
                    batch_id="fe3-e2e"))
        self.assertIn('"total_usd": null', out.getvalue())


# ----------------------------------------------------------------------
# Smaller findings
# ----------------------------------------------------------------------

class TestSmallerFindings(unittest.TestCase):
    def setUp(self):
        _clear_binding_env()
        self.addCleanup(_clear_binding_env)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_export_prefixed_lines_parse(self):
        # a shell-style file must not silently drop
        # its declarations (which stamped a false
        # `reference` on the preset fallback).
        text = ("export ONTOSMITH_PROPOSER_BASE_URL="
                "\"https://api.example.com/v1\"\n"
                "export ONTOSMITH_PROPOSER_MODEL=\"acme/x\"\n"
                "ONTOSMITH_PROPOSER_KEY_ENV=\"MY_KEY\"\n")
        decls = configure.parse_bindings_file(text)
        self.assertEqual(decls["ONTOSMITH_PROPOSER_BASE_URL"],
                         "https://api.example.com/v1")
        self.assertEqual(decls["ONTOSMITH_PROPOSER_MODEL"],
                         "acme/x")
        bindings = configure.resolve_from(decls)
        self.assertEqual(bindings.seat("proposer").model,
                         "acme/x")
        prov = bindings.provenance()
        self.assertEqual(prov["name"], "custom")

    def test_probe_failure_speaks_plainly(self):
        # a dead endpoint's probe failure names the
        # reason in plain words — no urllib plumbing in
        # the report (or the artifact header built from it).
        decls = {
            "ONTOSMITH_PROPOSER_BASE_URL": "http://127.0.0.1:9/v1",
            "ONTOSMITH_PROPOSER_MODEL": "acme/x",
            "ONTOSMITH_PROPOSER_KEY_ENV": "STUB_KEY",
            "ONTOSMITH_PROPOSER_DIALECT": "openai",
        }
        bindings = configure.resolve_from(decls)
        with mock.patch.dict(os.environ,
                             {"STUB_KEY": "stub-key"}):
            failures, _warnings = configure.probe_bindings(
                bindings)
        self.assertTrue(failures)
        joined = " ".join(failures)
        self.assertIn("unreachable", joined)
        self.assertNotIn("<urlopen error", joined)
        self.assertNotIn("Errno", joined)

    def test_nonexistent_batch_id_errors_by_name(self):
        # a typo'd batch id is a named error with
        # exit code 2, never a traceback.
        env = {"ONTOSMITH_ROOT": str(self.tmp)}
        err = io.StringIO()
        with mock.patch.dict(os.environ, env):
            with redirect_stderr(err):
                with self.assertRaises(SystemExit) as cm:
                    batch_cli.cmd_status(argparse.Namespace(
                        batch_id="demo2-typo"))
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("demo2-typo", err.getvalue())
        self.assertIn("no such batch", err.getvalue())

    def test_cost_basis_empty_seat_set_is_not_provided(self):
        # with no seat carrying a bound model the
        # aggregate basis is defined as not-provided —
        # "mixed" would claim a disagreement that does
        # not exist.
        seats = {name: connections.SeatBinding(
            seat=name, base_url="https://example.com/v1",
            model=None, key_env="") for name in
            connections.SEATS}
        self.assertEqual(
            connections.Bindings(seats).cost_basis(),
            "not-provided")

    def test_require_key_never_prints_model_none(self):
        binding = connections.SeatBinding(
            seat="drafting", base_url="https://example.com/v1",
            model=None, key_env="DEFINITELY_UNSET_KEY")
        with self.assertRaises(
                connections.BindingKeyError) as cm:
            binding.require_key(env={})
        self.assertNotIn("model None", str(cm.exception))
        self.assertIn("none bound", str(cm.exception))

    def test_template_copies_byte_identical(self):
        # the package-data template (what
        # --emit-template serves from an install) and the
        # documented docs/ copy are the same bytes.
        repo = Path(__file__).resolve().parent.parent
        docs_copy = (repo / "docs"
                     / "bindings-template.env").read_bytes()
        self.assertEqual(configure.template_text().encode(),
                         docs_copy)


if __name__ == "__main__":
    unittest.main()
