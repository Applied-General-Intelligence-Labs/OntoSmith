"""Demo app: the static page
``ontosmith serve`` serves at ``/`` over the sample API.

Three layers, all pinned:
- asset resolution — the app directory resolves in the
  source tree under the candidate discipline, and
  asset names are refused unless they are a single
  bare file name with a known content type;
- the app↔API contract — every endpoint and parameter
  the page's JavaScript calls is extracted from the JS
  source and pinned against the server's route table
  (``api.server.API_ROUTES`` / ``api_route``), so a
  rename on either side fails here first;
- the HTTP shell — the page and its assets serve with
  the right content types, a miss is the API's JSON
  404, and one full click-path (page → search → triple
  → entity → neighborhood) runs over a real socket,
  chained on the values each step actually returned.
"""
from __future__ import annotations

import json
import re
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from api.server import (
    API_ROUTES,
    SearchService,
    api_route,
    app_asset,
    app_candidates,
    app_dir,
    build_server,
)
from pipeline import search

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
EXAMPLES = ROOT / "examples"

APP_FILES = ("index.html", "app.css", "app.js",
             "favicon.svg", "logo-mark.svg")


def _worked_example_dir() -> Path:
    """The worked example as one self-contained records
    directory (its recorded triples beside its corpus);
    the app contracts stay pinned against it."""
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


class TestAppResolution(unittest.TestCase):

    def test_app_dir_resolves_in_source_tree(self):
        self.assertEqual(app_dir(), APP)
        for name in APP_FILES:
            self.assertTrue((APP / name).is_file(), name)

    def test_candidates_follow_the_example_discipline(self):
        cands = app_candidates()
        self.assertEqual(cands[0], APP)
        self.assertEqual(
            cands[1],
            ROOT / "share" / "ontosmith" / "app")

    def test_asset_content_types(self):
        expected = {
            "index.html": "text/html",
            "app.css": "text/css",
            "app.js": "text/javascript",
            "favicon.svg": "image/svg+xml",
            "logo-mark.svg": "image/svg+xml",
        }
        for name, prefix in expected.items():
            asset = app_asset(name)
            self.assertIsNotNone(asset, name)
            body, ctype = asset
            self.assertTrue(ctype.startswith(prefix),
                            (name, ctype))
            self.assertEqual(body,
                             (APP / name).read_bytes())

    def test_asset_name_refusals(self):
        for bad in ("", "..", "../app.js", "sub/app.js",
                    ".hidden", "missing.js", "server.py",
                    "app.py"):
            self.assertIsNone(app_asset(bad), bad)


class TestAppApiContract(unittest.TestCase):
    """The page's calls, extracted from its sources and
    pinned against the server's live route table."""

    @classmethod
    def setUpClass(cls):
        cls.js = (APP / "app.js").read_text()
        cls.html = (APP / "index.html").read_text()

    def test_route_table_matches_constant(self):
        svc = _service()
        for path in API_ROUTES:
            self.assertIsNotNone(
                api_route(svc, path, {}), path)
        self.assertIsNone(api_route(svc, "/nope", {}))
        self.assertIsNone(api_route(svc, "/", {}))

    def test_js_calls_exactly_the_api_routes(self):
        used = set(re.findall(
            r"`/(health|search|entity|neighborhood|"
            r"triple)[?`]", self.js))
        self.assertEqual({f"/{name}" for name in used},
                         set(API_ROUTES))

    def test_js_parameters_are_server_parameters(self):
        used = set(re.findall(r"[?&]([a-z_]+)=", self.js))
        allowed = {"q", "k", "id", "name", "hops",
                   "subject", "relation", "object", "index"}
        self.assertLessEqual(used, allowed)
        self.assertLessEqual(
            {"q", "k", "id", "name", "hops", "subject",
             "relation", "object"}, used)

    def test_page_references_only_its_own_assets(self):
        for name in ("app.css", "app.js", "favicon.svg",
                     "logo-mark.svg"):
            self.assertIn(f"/app/{name}", self.html)
        # Offline rule (brief): no external assets —
        # every src/href is same-origin.
        self.assertNotIn('src="http', self.html)
        self.assertNotIn('href="http', self.html)
        self.assertNotIn("http://", self.js)
        self.assertNotIn("https://", self.js)


class TestAppOverHttp(unittest.TestCase):
    """The app and the click-path, over a real socket."""

    @classmethod
    def setUpClass(cls):
        cls.server = build_server("127.0.0.1", 0,
                                  _service())
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get_raw(self, path: str):
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url,
                                        timeout=10) as resp:
                return (resp.status,
                        resp.headers.get_content_type(),
                        resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return (e.code,
                    e.headers.get_content_type(),
                    e.read().decode("utf-8"))

    def _get_json(self, path: str):
        status, _, text = self._get_raw(path)
        return status, json.loads(text)

    def test_index_page(self):
        status, ctype, text = self._get_raw("/")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "text/html")
        self.assertIn('id="search-form"', text)
        self.assertIn("/app/app.js", text)
        self.assertIn("/app/app.css", text)

    def test_assets_serve_with_content_types(self):
        for path, ctype in (
                ("/app/app.css", "text/css"),
                ("/app/app.js", "text/javascript"),
                ("/app/favicon.svg", "image/svg+xml"),
                ("/app/logo-mark.svg", "image/svg+xml")):
            status, got, _ = self._get_raw(path)
            self.assertEqual(status, 200, path)
            self.assertEqual(got, ctype, path)

    def test_asset_miss_is_the_json_404(self):
        status, body = self._get_json("/app/nope.css")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")

    def test_traversal_is_refused(self):
        for path in ("/app/../pyproject.toml",
                     "/app/%2e%2e%2fpyproject.toml"):
            status, body = self._get_json(path)
            self.assertEqual(status, 404, path)
            self.assertEqual(body["error"]["code"],
                             "not_found", path)

    def test_api_unaffected_by_app_routing(self):
        status, ctype, _ = self._get_raw("/health")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "application/json")

    def test_click_path_search_triple_entity_walk(self):
        """The app's flow at HTTP level: the page loads,
        a search returns a triple, the triple opens as a
        report with evidence, its subject opens as an
        entity, and the neighborhood walk answers."""
        status, _, _ = self._get_raw("/")
        self.assertEqual(status, 200)

        q = urllib.parse.quote(
            "Who founded Alder & Finch?")
        status, result = self._get_json(f"/search?q={q}")
        self.assertEqual(status, 200)
        self.assertTrue(result["triples"])
        t = result["triples"][0]

        qs = urllib.parse.urlencode({
            "subject": t["subject"],
            "relation": t["relation"],
            "object": t["object"]})
        status, report = self._get_json(f"/triple?{qs}")
        self.assertEqual(status, 200)
        self.assertEqual(report["triple"]["subject"],
                         t["subject"])
        self.assertTrue(report["evidence"])
        self.assertEqual(report["provenance"]["doc_id"],
                         t["doc_id"])

        status, ent = self._get_json(
            f"/entity?id={urllib.parse.quote(t['subject_id'])}")
        self.assertEqual(status, 200)
        self.assertEqual(ent["name"], t["subject"])

        status, hood = self._get_json(
            f"/neighborhood?id="
            f"{urllib.parse.quote(t['subject_id'])}&hops=1")
        self.assertEqual(status, 200)
        node_ids = {n["entity_id"] for n in hood["nodes"]}
        self.assertIn(t["subject_id"], node_ids)
        self.assertIn(t["object_id"], node_ids)
        self.assertTrue(hood["edges"])


if __name__ == "__main__":
    unittest.main()
