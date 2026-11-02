"""Sample API: endpoint contracts.

Two layers, both pinned:
- SearchService (transport-free): every endpoint's
  success shape and every named refusal — 400 for bad
  input, 404 for an unknown entity / unmatched triple;
- the HTTP shell on an ephemeral port: status codes and
  the {"error": {"code", "message"}} body shape a client
  actually receives.
"""
from __future__ import annotations

import io
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from api.server import ApiError, SearchService, build_server, main
from pipeline import search

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def _worked_example_dir() -> Path:
    """The worked example as one self-contained records
    directory (its recorded triples beside its corpus);
    the API contracts stay pinned against it."""
    import shutil
    import tempfile

    d = Path(tempfile.mkdtemp(prefix="af-example-"))
    shutil.copy(EXAMPLES / "alder-finch" / "triples.jsonl",
                d / "triples.jsonl")
    shutil.copytree(EXAMPLES / "corpus", d / "corpus")
    return d


WORKED = _worked_example_dir()


def _service() -> SearchService:
    store = search.FileServingStore(WORKED, root=ROOT)
    return SearchService(store)


class TestServiceContracts(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.svc = _service()

    def test_health(self):
        h = self.svc.health()
        self.assertEqual(h["status"], "ok")
        self.assertEqual(h["store"]["kind"], "file")
        self.assertEqual(h["store"]["triples"], 7)

    def test_search_success(self):
        r = self.svc.search({"q": "Who founded Alder & "
                                   "Finch?"})
        self.assertEqual(r["documents"][0]["doc_id"],
                         "alder-finch-history")
        self.assertEqual(r["triples"][0]["relation"],
                         "founded by")

    def test_search_missing_q_is_400(self):
        with self.assertRaises(ApiError) as cm:
            self.svc.search({})
        self.assertEqual(cm.exception.status, 400)
        self.assertEqual(cm.exception.code,
                         "missing_parameter")

    def test_search_bad_k_is_400(self):
        for bad in ("abc", "0", "101"):
            with self.assertRaises(ApiError) as cm:
                self.svc.search({"q": "studio", "k": bad})
            self.assertEqual(cm.exception.status, 400)
            self.assertEqual(cm.exception.code,
                             "invalid_parameter")

    def test_entity_by_name_and_id(self):
        by_name = self.svc.entity({"name": "Mara Alder"})
        self.assertEqual(by_name["entity_id"], "Mara Alder")
        self.assertEqual(by_name["facts_as_subject"], 1)
        self.assertEqual(by_name["facts_as_object"], 1)
        by_id = self.svc.entity({"id": "Dune"})
        self.assertEqual(by_id["name"], "Dune")

    def test_entity_not_found_is_404(self):
        for params in ({"name": "Nobody"}, {"id": "ent-x"}):
            with self.assertRaises(ApiError) as cm:
                self.svc.entity(params)
            self.assertEqual(cm.exception.status, 404)
            self.assertEqual(cm.exception.code,
                             "entity_not_found")

    def test_entity_no_params_is_400(self):
        with self.assertRaises(ApiError) as cm:
            self.svc.entity({})
        self.assertEqual(cm.exception.status, 400)

    def test_neighborhood_success(self):
        r = self.svc.neighborhood({"name": "Dune",
                                   "hops": "2"})
        self.assertEqual(r["hops"], 2)
        self.assertIn("Mara Alder",
                      {n["entity_id"] for n in r["nodes"]})
        self.assertTrue(r["edges"])

    def test_neighborhood_bad_hops_is_400(self):
        with self.assertRaises(ApiError) as cm:
            self.svc.neighborhood({"name": "Dune",
                                   "hops": "9"})
        self.assertEqual(cm.exception.status, 400)
        self.assertEqual(cm.exception.code,
                         "invalid_parameter")

    def test_triple_with_evidence(self):
        r = self.svc.triple({"subject": "Priya"})
        self.assertEqual(r["triple"]["subject"],
                         "Priya Nair")
        self.assertEqual(r["evidence"][0]["kind"], "quote")
        self.assertIn("joined Alder", r["evidence"][0]["text"])
        self.assertEqual(r["provenance"]["doc_id"],
                         "alder-finch-people")
        self.assertEqual(r["gaps"], [])

    def test_triple_by_index(self):
        r = self.svc.triple({"index": "0"})
        self.assertEqual(r["position"], 0)
        self.assertEqual(r["triples"], 7)

    def test_triple_not_found_is_404(self):
        with self.assertRaises(ApiError) as cm:
            self.svc.triple({"subject": "Zzz"})
        self.assertEqual(cm.exception.status, 404)
        self.assertEqual(cm.exception.code,
                         "triple_not_found")


class TestHttpShell(unittest.TestCase):
    """The same contracts over a real socket."""

    @classmethod
    def setUpClass(cls):
        cls.server = build_server("127.0.0.1", 0, _service())
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path: str):
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url,
                                        timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_health_over_http(self):
        status, body = self._get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_search_over_http(self):
        status, body = self._get(
            "/search?q=Who%20founded%20Alder%20%26%20Finch%3F")
        self.assertEqual(status, 200)
        self.assertEqual(body["documents"][0]["doc_id"],
                         "alder-finch-history")

    def test_error_shapes_over_http(self):
        status, body = self._get("/search")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"],
                         "missing_parameter")
        status, body = self._get("/entity?name=Nobody")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"],
                         "entity_not_found")
        status, body = self._get("/neighborhood?name=Dune"
                                 "&hops=9")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"],
                         "invalid_parameter")
        status, body = self._get("/nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")

    def test_triple_over_http(self):
        status, body = self._get("/triple?subject=Priya")
        self.assertEqual(status, 200)
        self.assertEqual(body["triple"]["object"],
                         "Alder & Finch")


class TestServeMain(unittest.TestCase):

    def test_refusal_on_missing_source(self):
        rc = main(["--source", "/nonexistent-xyz"])
        self.assertEqual(rc, 1)

    def test_embed_keyless_refuses_by_name(self):
        # --embed with an unresolvable embedder key
        # is a named refusal (exit 1 naming the seat and the
        # variable), never a silent local model.
        err = io.StringIO()
        with mock.patch.dict(os.environ,
                             {"ONTOSMITH_EMBEDDER_KEY_ENV":
                              "STUB_MISSING_KEY"}):
            with redirect_stderr(err):
                rc = main(["--source", str(EXAMPLES), "--embed"])
        self.assertEqual(rc, 1)
        self.assertIn("serve refused", err.getvalue())
        self.assertIn("embedder", err.getvalue())
        self.assertIn("STUB_MISSING_KEY", err.getvalue())


if __name__ == "__main__":
    unittest.main()
