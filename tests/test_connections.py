"""Connection-layer tests: provider-neutral bindings.

Three proofs:
1. PRESET EQUIVALENCE — under the reference preset, the
   outbound requests emitted by every routed transport
   are byte-identical to the pre-generalization code path
   (golden: tests/golden/preset_requests.json, captured
   from the pre-change tree with the same call suite).
2. CUSTOM BINDING — a localhost stub server (a real
   OpenAI-compatible HTTP endpoint) is actually called by
   the proposer, judge, escalation, drafting, and
   embedder paths when the seats are rebound to it.
3. REFUSAL — a declared binding whose key variable is
   unset refuses with BindingKeyError naming the seat
   and the variable; nothing falls back silently.
Plus binding resolution / provenance unit tests and the
batch-create provenance stamp.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import requests

from pipeline import (
    batch,
    batch_cli,
    connections,
    decisions,
    embedder,
    proposer,
    query_stack,
    setup_service,
)
from pipeline import (
    reference_profile as rp,
)

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden" / "preset_requests.json"
CAPTURE_KEY = "sk-capture-key"


@contextlib.contextmanager
def binding_env(overrides: dict | None = None):
    """Scrub every binding-related variable, then apply
    `overrides`. Restores the ambient environment after."""
    overrides = overrides or {}
    with mock.patch.dict(os.environ, overrides):
        for name in list(os.environ):
            if name.startswith("ONTOSMITH_") \
                    or name == "OPENROUTER_API_KEY":
                if name not in overrides:
                    del os.environ[name]
        yield


# ----------------------------------------------------------------------
# 1. Preset equivalence (capture-compare vs the golden)
# ----------------------------------------------------------------------

class _Resp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


class _UrlResp:
    def __init__(self, body):
        self._data = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._data


class PresetCapture:
    """The canonical call suite, mirroring the harness
    that captured the golden from the pre-change tree."""

    def __init__(self):
        self.records = []
        self.chat_content = "ok"
        self.decisions_answers = {}

    def fake_post(self, url, headers=None, json=None, timeout=None,
                  **kw):
        self.records.append({
            "transport": "requests", "url": url,
            "headers": {str(k).lower(): v
                        for k, v in (headers or {}).items()},
            "payload": json,
        })
        if url.endswith("/decisions") or "/alpha/decisions" in url:
            return _Resp({
                "answers": self.decisions_answers,
                "usage": {"prompt_tokens": 9,
                          "completion_tokens": 0, "cost": 0.003},
                "provider": "stub-provider",
            })
        payload = json or {}
        if payload.get("logprobs"):
            body = {
                "choices": [{
                    "message": {"content": "Yes"},
                    "logprobs": {"content": [{
                        "token": "Yes", "logprob": -0.11,
                        "top_logprobs": [
                            {"token": "Yes", "logprob": -0.11},
                            {"token": "No", "logprob": -2.3},
                        ],
                    }]},
                }],
                "usage": {"prompt_tokens": 11,
                          "completion_tokens": 1, "cost": 0.001},
                "provider": "stub-provider",
            }
        else:
            body = {
                "choices": [{"message": {
                    "content": self.chat_content}}],
                "usage": {"prompt_tokens": 11,
                          "completion_tokens": 7, "cost": 0.002},
                "provider": "stub-provider",
            }
        return _Resp(body)

    def fake_urlopen(self, req, timeout=None, **kw):
        url = req.full_url
        payload = json.loads(req.data.decode()) if req.data else None
        self.records.append({
            "transport": "urllib", "url": url,
            "headers": {str(k).lower(): v
                        for k, v in req.headers.items()},
            "payload": payload,
        })
        if url.endswith("/embeddings"):
            return _UrlResp({
                "data": [
                    {"index": 0, "embedding": [0.6, 0.8]},
                    {"index": 1, "embedding": [0.0, 1.0]},
                ],
                "usage": {"prompt_tokens": 3, "cost": 0.001},
            })
        return _UrlResp({
            "answers": {"q1": {"noul": 0.75}},
            "usage": {"prompt_tokens": 5},
        })

    def run(self) -> list:
        out = []

        def snap(name, fn):
            before = len(self.records)
            fn()
            for rec in self.records[before:]:
                out.append({"call": name, **rec})

        with mock.patch.object(requests, "post", self.fake_post), \
                mock.patch.object(urllib.request, "urlopen",
                                  self.fake_urlopen):
            self.chat_content = "chat-ok"
            snap("proposer._chat", lambda: proposer._chat(
                "CONTENT-P", rp.PROPOSER_MODEL, 64, CAPTURE_KEY))

            self.decisions_answers = {"holds": {"noul": 0.42}}
            snap("decisions.validate_triple", lambda:
                 decisions.validate_triple(
                     "DOC", "Alice", "met", "Bob"))

            self.chat_content = '{"answer": "Yes"}'
            snap("decisions.llm_text_judge", lambda:
                 decisions.llm_text_judge(
                     "DOC", "Alice", "met", "Bob",
                     model="some/model-x"))

            self.chat_content = "draft-ok"
            # The drafting seat ships unbound, so
            # the model here is an explicit caller argument
            # (as it always was to _chat); the golden pins
            # the transport bytes for that call.
            snap("setup_service._chat", lambda:
                 setup_service._chat(
                     "openai/gpt-5", "SYS", "USER", CAPTURE_KEY))

            self.chat_content = "answer-text"
            snap("query_stack._chat", lambda: query_stack._chat(
                "question?", 100, CAPTURE_KEY))

            snap("embedder.embed", lambda:
                 embedder.OpenRouterEmbedder().embed(
                     ["alpha", "beta"]))

            snap("batch_cli._decide_fn", lambda:
                 batch_cli._decide_fn("CTX", "Alice", "met", "Bob"))

            snap("batch_cli._er_decide_fn", lambda:
                 batch_cli._er_decide_fn(
                     "Alice", "Alicia", "person", "CTX"))
        return out


class TestPresetEquivalence(unittest.TestCase):
    def test_requests_identical_to_pre_change_golden(self):
        golden = json.loads(GOLDEN.read_text())
        with binding_env({"OPENROUTER_API_KEY": CAPTURE_KEY}):
            actual = PresetCapture().run()
        self.assertEqual(actual, golden)


# ----------------------------------------------------------------------
# 2. Custom binding against a localhost stub server
# ----------------------------------------------------------------------

class _StubHandler(BaseHTTPRequestHandler):
    hits: list = []  # noqa: RUF012 — shared hit log, reset per test
    chat_content = "stub-answer"

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        type(self).hits.append({
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "payload": payload,
        })
        if self.path.endswith("/embeddings"):
            body = {"data": [{"index": 0,
                              "embedding": [0.6, 0.8]}],
                    "usage": {"prompt_tokens": 2, "cost": 0.0}}
        elif self.path.endswith("/systemone"):
            body = {"answers": {"q1": {"noul": 0.75}},
                    "usage": {"prompt_tokens": 5}}
        elif self.path.endswith("/decisions"):
            body = {"answers": {"holds": {"noul": 0.42}},
                    "usage": {"prompt_tokens": 9}}
        elif payload.get("logprobs"):
            body = {
                "choices": [{
                    "message": {"content": "Yes"},
                    "logprobs": {"content": [{
                        "token": "Yes", "logprob": -0.11,
                        "top_logprobs": [
                            {"token": "Yes", "logprob": -0.11},
                            {"token": "No", "logprob": -2.3}],
                    }]},
                }],
                "usage": {"prompt_tokens": 11,
                          "completion_tokens": 1},
            }
        else:
            body = {
                "choices": [{"message": {
                    "content": type(self).chat_content}}],
                "usage": {"prompt_tokens": 11,
                          "completion_tokens": 7},
            }
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class StubServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _StubHandler.hits = []
        cls.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), _StubHandler)
        cls.port = cls.server.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}/v1"
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _StubHandler.hits = []
        _StubHandler.chat_content = "stub-answer"

    def env(self, extra=None):
        base = {
            "ONTOSMITH_BASE_URL": self.base,
            "ONTOSMITH_KEY_ENV": "STUB_KEY",
            "STUB_KEY": "sk-stub",
        }
        base.update(extra or {})
        return binding_env(base)

    def hit(self, suffix):
        for h in _StubHandler.hits:
            if h["path"].endswith(suffix):
                return h
        return None


class TestCustomBindingStub(StubServerCase):
    def test_proposer_chat_hits_stub_with_custom_model(self):
        with self.env({"ONTOSMITH_PROPOSER_MODEL": "acme/prop-1"}):
            text, _usage = proposer._chat(
                "hello", "acme/prop-1", 32,
                proposer.api_key())
        self.assertEqual(text, "stub-answer")
        hit = self.hit("/chat/completions")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["authorization"], "Bearer sk-stub")
        self.assertEqual(hit["payload"]["model"], "acme/prop-1")

    def test_judge_systemone_hits_stub(self):
        with self.env():
            p, _u = batch_cli._decide_fn("CTX", "A", "met", "B")
        self.assertEqual(p, 0.75)
        hit = self.hit("/systemone")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["payload"]["model"], rp.JUDGE_MODEL)

    def test_escalation_seat_derives_decisions_route(self):
        with self.env():
            p, _u = decisions.validate_triple("DOC", "A", "met", "B")
        self.assertEqual(p, 0.42)
        self.assertIsNotNone(self.hit("/decisions"))

    def test_escalation_route_override_wins(self):
        with self.env({
                "ONTOSMITH_ESCALATION_DECISIONS_URL":
                    self.base + "/decisions"}):
            p, _u = decisions.validate_triple("DOC", "A", "met", "B")
        self.assertEqual(p, 0.42)
        self.assertIsNotNone(self.hit("/decisions"))

    def test_drafting_chat_hits_stub(self):
        with self.env():
            text, _u = setup_service._chat(
                "draft/model-x", "SYS", "USER", "sk-stub")
        self.assertEqual(text, "stub-answer")
        self.assertIsNotNone(self.hit("/chat/completions"))

    def test_embedder_hits_stub(self):
        with self.env():
            arr = embedder.OpenRouterEmbedder().embed(["alpha"])
        self.assertEqual(arr.shape, (1, 2))
        hit = self.hit("/embeddings")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["payload"]["model"], rp.EMBED_MODEL)
        self.assertEqual(hit["authorization"], "Bearer sk-stub")


# ----------------------------------------------------------------------
# 3. Refusals
# ----------------------------------------------------------------------

class TestRefusals(StubServerCase):
    def test_judge_missing_key_refuses_before_any_request(self):
        with binding_env({
                "ONTOSMITH_JUDGE_BASE_URL": self.base,
                "ONTOSMITH_JUDGE_KEY_ENV": "STUB_MISSING_KEY"}):
            with self.assertRaises(
                    connections.BindingKeyError) as cm:
                batch_cli._decide_fn("CTX", "A", "met", "B")
        msg = str(cm.exception)
        self.assertIn("judge", msg)
        self.assertIn("STUB_MISSING_KEY", msg)
        self.assertEqual(_StubHandler.hits, [])

    def test_escalation_missing_key_refuses(self):
        with binding_env({
                "ONTOSMITH_ESCALATION_KEY_ENV": "STUB_MISSING_KEY"}):
            with self.assertRaises(connections.BindingKeyError):
                decisions.validate_triple("DOC", "A", "met", "B")

    def test_proposer_chat_missing_key_refuses(self):
        with binding_env({"ONTOSMITH_KEY_ENV": "STUB_MISSING_KEY"}):
            with self.assertRaises(connections.BindingKeyError):
                proposer._chat("x", rp.PROPOSER_MODEL, 8,
                               proposer.api_key())

    def test_embedder_declared_missing_key_refuses(self):
        with binding_env({
                "ONTOSMITH_EMBEDDER_BASE_URL": self.base,
                "ONTOSMITH_EMBEDDER_KEY_ENV": "STUB_MISSING_KEY"}):
            with self.assertRaises(connections.BindingKeyError):
                embedder.default_embedder()

    def test_embedder_undeclared_keyless_refuses(self):
        # There is no local fallback — an
        # undeclared, keyless embedder use refuses by name,
        # exactly like the declared case above.
        with binding_env():
            with self.assertRaises(connections.BindingKeyError) as cm:
                embedder.default_embedder()
        msg = str(cm.exception)
        self.assertIn("embedder", msg)
        self.assertIn("OPENROUTER_API_KEY", msg)

    def test_neo4j_registry_keyless_candidate_search_refuses(self):
        # Candidate search over the Neo4j registry
        # must not silently downgrade to substring when the
        # embedder binding's key is missing — the refusal
        # propagates out of find_candidates by name.
        from pipeline import neo4j_store

        class _FakeClient:
            def run(self, statements):
                stmt = statements[0]["statement"]
                if "CREATE CONSTRAINT" in stmt:
                    return [[]]
                return [[{"row": ["e1", "Oswald", "", []]}]]

        registry = neo4j_store.Neo4jEntityRegistry(
            client=_FakeClient())
        with binding_env():
            with self.assertRaises(connections.BindingKeyError):
                registry.find_candidates("Oswald")

    def test_embedder_constructor_refuses_without_key(self):
        with binding_env():
            with self.assertRaises(connections.BindingKeyError):
                embedder.OpenRouterEmbedder()


# ----------------------------------------------------------------------
# 4. Resolution + provenance
# ----------------------------------------------------------------------

class TestResolution(unittest.TestCase):
    def test_preset_is_the_reference_profile(self):
        with binding_env():
            b = connections.resolve_bindings()
        self.assertFalse(b.is_custom)
        self.assertEqual(b.name, "reference")
        for seat_name, model in (
                ("proposer", rp.PROPOSER_MODEL),
                ("judge", rp.JUDGE_MODEL),
                ("escalation", rp.ESCALATION_MODEL),
                ("drafting", None),
                ("embedder", rp.EMBED_MODEL)):
            seat = b.seat(seat_name)
            self.assertEqual(seat.model, model, seat_name)
            self.assertEqual(seat.base_url, rp.PROVIDER_BASE_URL)
            self.assertEqual(seat.key_env, "OPENROUTER_API_KEY")
            self.assertEqual(seat.chat_url, rp.CHAT_URL)
        self.assertEqual(b.seat("embedder").embeddings_url,
                         rp.EMBEDDINGS_URL)
        self.assertEqual(b.seat("judge").systemone_url,
                         rp.SYSTEMONE_URL)
        self.assertEqual(b.seat("judge").decisions_endpoint,
                         rp.DECISIONS_URL)
        self.assertEqual(b.seat("escalation").decisions_endpoint,
                         rp.DECISIONS_URL)

    def test_per_seat_beats_global_beats_preset(self):
        env = {
            "ONTOSMITH_BASE_URL": "http://global.example/v1",
            "ONTOSMITH_PROPOSER_BASE_URL": "http://seat.example/v2",
            "ONTOSMITH_KEY_ENV": "GLOBAL_KEY",
            "ONTOSMITH_JUDGE_KEY_ENV": "JUDGE_KEY",
        }
        with binding_env(env):
            b = connections.resolve_bindings()
        self.assertEqual(b.seat("proposer").base_url,
                         "http://seat.example/v2")
        self.assertEqual(b.seat("judge").base_url,
                         "http://global.example/v1")
        self.assertEqual(b.seat("judge").key_env, "JUDGE_KEY")
        self.assertEqual(b.seat("drafting").key_env, "GLOBAL_KEY")
        self.assertTrue(b.is_custom)
        self.assertEqual(b.name, "custom")

    def test_base_rebind_derives_decisions_route(self):
        with binding_env(
                {"ONTOSMITH_BASE_URL": "http://gw.example/v1"}):
            b = connections.resolve_bindings()
        self.assertEqual(b.seat("escalation").decisions_endpoint,
                         "http://gw.example/v1/decisions")

    def test_decision_env_vars_are_inert(self):
        # The decision seat is gone (its work moved
        # to the escalation seat). ONTOSMITH_DECISION_* —
        # the no-op rule — resolve nothing and do
        # not make the binding custom.
        with binding_env({
                "ONTOSMITH_DECISION_MODEL": "x/y",
                "ONTOSMITH_DECISION_BASE_URL": "http://dead.example/v1",
                "ONTOSMITH_DECISION_KEY_ENV": "DEAD_KEY"}):
            b = connections.resolve_bindings()
        self.assertEqual(tuple(b.seats), connections.SEATS)
        self.assertNotIn("decision", b.seats)
        self.assertFalse(b.is_custom)
        self.assertEqual(b.name, "reference")

    def test_escalation_env_override_binds(self):
        with binding_env({
                "ONTOSMITH_ESCALATION_MODEL": "lab/escalation-x"}):
            b = connections.resolve_bindings()
        self.assertEqual(b.seat("escalation").model,
                         "lab/escalation-x")
        self.assertTrue(b.is_custom)

    def test_provenance_carries_no_key_material(self):
        with binding_env({
                "ONTOSMITH_BASE_URL": "http://gw.example:9000/v1",
                "ONTOSMITH_KEY_ENV": "MY_SECRET_KEY_NAME"}):
            prov = connections.resolve_bindings().provenance()
        blob = json.dumps(prov)
        self.assertEqual(prov["name"], "custom")
        self.assertEqual(prov["seats"]["proposer"]["host"],
                         "gw.example:9000")
        self.assertNotIn("MY_SECRET_KEY_NAME", blob)
        self.assertNotIn("OPENROUTER_API_KEY", blob)

    def test_proposer_api_key_reads_binding_key_env(self):
        with binding_env({"ONTOSMITH_KEY_ENV": "ALT_KEY",
                          "ALT_KEY": "alt-value"}):
            self.assertEqual(proposer.api_key(), "alt-value")
            self.assertEqual(proposer.key_env(), "ALT_KEY")


class TestDraftingSeatUnbound(unittest.TestCase):
    """The drafting seat ships unbound — no preset
    model is presented for it. A drafting call resolves its
    model from the user's declaration only and refuses,
    before any request, with a named error otherwise."""

    def test_preset_drafting_seat_has_no_model(self):
        with binding_env():
            seat = connections.seat("drafting")
        self.assertIsNone(seat.model)

    def test_require_model_refuses_with_named_error(self):
        with binding_env():
            seat = connections.seat("drafting")
            with self.assertRaises(
                    connections.BindingModelError) as cm:
                seat.require_model()
        msg = str(cm.exception)
        self.assertIn("drafting", msg)
        self.assertIn("ONTOSMITH_DRAFTING_MODEL", msg)

    def test_env_declaration_binds_the_seat(self):
        with binding_env({
                "ONTOSMITH_DRAFTING_MODEL": "lab/drafter-1"}):
            seat = connections.seat("drafting")
        self.assertEqual(seat.require_model(), "lab/drafter-1")
        self.assertEqual(seat.model, "lab/drafter-1")


class TestBatchProvenanceStamp(unittest.TestCase):
    def test_create_stamps_binding_into_batch_and_manifest(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        doc = tmp / "doc1.txt"
        doc.write_text("Alice met Bob in Paris.")
        args = argparse.Namespace(
            docs=[str(doc)], collection="stubcoll",
            batch_id="stub-batch", source="test",
            # The custom gateway binding is cost-less (portable
            # dialect, no declared rates); with no cap set
            # (ONTOSMITH_MAX_COST_USD unset) the create proceeds.
            profile=None, pack=None)
        with binding_env({
                "ONTOSMITH_BASE_URL": "http://gw.example:9000/v1"}):
            with mock.patch.object(batch_cli, "ROOT", tmp):
                with mock.patch.object(
                        batch_cli, "state_root", return_value=tmp):
                    batch_cli.cmd_create(args)
        b = batch.load_batch(tmp, "stub-batch")
        self.assertEqual(b.binding_provenance["name"], "custom")
        self.assertEqual(
            b.binding_provenance["seats"]["judge"]["host"],
            "gw.example:9000")
        self.assertNotIn("KEY", json.dumps(b.binding_provenance))
        manifest = batch.write_manifest(
            b, [{"doc_id": "d1"}], tmp)
        self.assertEqual(manifest["binding"]["name"], "custom")

    def test_legacy_batch_dict_without_binding_loads(self):
        d = {
            "batch_id": "x", "run_id": "r", "source": "s",
            "collection": "c", "doc_ids": [],
            "config": {}, "status": "CREATED",
        }
        b = batch.Batch.from_dict(d)
        self.assertEqual(b.binding_provenance, {})

    def test_legacy_provenance_with_removed_seat_loads(self):
        # Batches created before the sllm seat was
        # removed carry it in their stored binding
        # provenance. Provenance is historical data: it
        # loads and re-embeds in the manifest unchanged —
        # no read path validates seat names against the
        # current seat list.
        import tempfile
        prov = {
            "name": "reference",
            "seats": {
                "sllm": {"host": "openrouter.ai",
                         "model": "meta-llama/llama-3.1-8b-instruct"},
                "judge": {"host": "openrouter.ai",
                          "model": rp.JUDGE_MODEL},
            },
        }
        d = {
            "batch_id": "x", "run_id": "r", "source": "s",
            "collection": "c", "doc_ids": [],
            "config": {}, "status": "CREATED",
            "binding_provenance": prov,
        }
        b = batch.Batch.from_dict(d)
        self.assertEqual(b.binding_provenance, prov)
        tmp = Path(tempfile.mkdtemp())
        manifest = batch.write_manifest(b, [{"doc_id": "d1"}], tmp)
        self.assertEqual(manifest["binding"], prov)

    def test_legacy_provenance_with_decision_seat_loads(self):
        # Batches created while the preset carried
        # a "decision" seat name it in their stored binding
        # provenance (its model was the gate-evaluation
        # default of the day). Same rule as the sllm
        # fixture: provenance is historical data — it loads
        # and re-embeds in the manifest unchanged.
        import tempfile
        prov = {
            "name": "reference",
            "seats": {
                "decision": {"host": "openrouter.ai",
                             "model": "microsoft/microsoft-decision-1"},
                "judge": {"host": "openrouter.ai",
                          "model": rp.JUDGE_MODEL},
            },
        }
        d = {
            "batch_id": "x", "run_id": "r", "source": "s",
            "collection": "c", "doc_ids": [],
            "config": {}, "status": "CREATED",
            "binding_provenance": prov,
        }
        b = batch.Batch.from_dict(d)
        self.assertEqual(b.binding_provenance, prov)
        tmp = Path(tempfile.mkdtemp())
        manifest = batch.write_manifest(b, [{"doc_id": "d1"}], tmp)
        self.assertEqual(manifest["binding"], prov)


if __name__ == "__main__":
    unittest.main()
