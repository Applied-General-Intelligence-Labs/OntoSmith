"""Document library + `serve --library` (no spend).

Pins the library's file rules, the rebuild runner's state
machine (with a fake command in place of the real batch
create/run), and the HTTP guards that keep the write routes
local: loopback Host only, same-origin JSON writes only.
"""
import http.client
import json
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from api.server import LibraryService, SearchService, build_server, main
from pipeline import batch_cli
from pipeline import library as lib

ROOT = Path(__file__).resolve().parent.parent


class TmpDir(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.library = lib.DocumentLibrary(self.tmp / "library")
        self.state = self.tmp / "state"


class TestSafeName(unittest.TestCase):
    def test_names(self):
        self.assertEqual(lib.safe_name("notes.txt"), "notes.txt")
        self.assertEqual(lib.safe_name("notes"), "notes.txt")
        self.assertEqual(lib.safe_name("../../etc/passwd"), "passwd.txt")
        self.assertEqual(lib.safe_name("C:\\x\\My Notes (1).TXT"),
                         "My Notes -1.txt")
        for bad in ("", ".txt", "...", "/", "  "):
            with self.assertRaises(lib.LibraryError):
                lib.safe_name(bad)

    def test_hidden_names_are_not_hidden(self):
        self.assertEqual(lib.safe_name(".bashrc"), "bashrc.txt")


class TestDocumentLibrary(TmpDir):
    def test_add_list_remove(self):
        r = self.library.add("a.txt", "Alice met Bob.")
        self.assertEqual(r, {"name": "a.txt", "chars": 14, "words": 3})
        self.library.add("b", "Carol founded Acme.")
        names = [d["name"] for d in self.library.documents()]
        self.assertEqual(names, ["a.txt", "b.txt"])
        self.library.remove("a.txt")
        self.assertEqual([d["name"] for d in self.library.documents()],
                         ["b.txt"])

    def test_refusals(self):
        self.library.add("a.txt", "Alice met Bob.")
        cases = [
            (("a.txt", "Other text."), 409, "name_taken"),
            (("copy.txt", "Alice met Bob."), 409, "duplicate_text"),
            (("e.txt", "   \n"), 400, "empty_document"),
            (("big.txt", "x" * (lib.MAX_DOC_CHARS + 1)), 413,
             "document_too_large"),
        ]
        for args, status, code in cases:
            with self.assertRaises(lib.LibraryError) as cm:
                self.library.add(*args)
            self.assertEqual((cm.exception.status, cm.exception.code),
                             (status, code))
        with self.assertRaises(lib.LibraryError) as cm:
            self.library.remove("missing.txt")
        self.assertEqual(cm.exception.code, "not_found")


def _fake_build(state: Path, final_status="VERIFIED", run_rc=0,
                calls=None, gate=None):
    """A run_cmd stand-in: `batch create` writes a batch.json,
    `batch run` sets its final status and cost."""
    def run(argv, env, on_line):
        if calls is not None:
            calls.append((argv, dict(env)))
        sub = argv[argv.index("batch") + 1]
        if sub == "create":
            bid = argv[argv.index("--batch-id") + 1]
            d = state / "batches" / bid
            d.mkdir(parents=True)
            (d / "batch.json").write_text(json.dumps(
                {"status": "CREATED", "metrics": {}}))
            on_line(f"created batch {bid}")
            return 0
        bid = argv[-1]
        if gate is not None:
            gate.wait(5)
        p = state / "batches" / bid / "batch.json"
        p.write_text(json.dumps({
            "status": final_status,
            "metrics": {"cost_ledger": {"total_usd": 0.0123}}}))
        on_line("batch run complete")
        return run_rc
    return run


class TestRebuilder(TmpDir):
    def _rebuilder(self, run_cmd, verified=None):
        return lib.Rebuilder(
            self.library, pack="open-domain",
            state_root=lambda: self.state,
            on_verified=(verified.append if verified is not None
                         else None),
            run_cmd=run_cmd)

    def test_success_serves_the_new_batch(self):
        self.library.add("a.txt", "Alice met Bob in Paris.")
        calls, verified = [], []
        rb = self._rebuilder(_fake_build(self.state, calls=calls),
                             verified)
        started = rb.start()
        self.assertEqual(started["status"], "running")
        self.assertEqual(started["documents"], 1)
        rb.join(5)
        st = rb.status()
        self.assertEqual(st["status"], "succeeded")
        self.assertEqual(st["cost_usd"], 0.0123)
        self.assertEqual(verified, [started["batch_id"]])
        self.assertIn("batch run complete", st["log"])
        # create covers the whole library folder, under the
        # chosen pack, with the effective cap in its env.
        create_argv, create_env = calls[0]
        self.assertIn(str(self.library.root / "*.txt"), create_argv)
        self.assertEqual(
            create_argv[create_argv.index("--pack") + 1], "open-domain")
        self.assertEqual(create_env[batch_cli.CAP_ENV],
                         repr(lib.LIBRARY_CAP_USD))

    def test_failed_build_is_reported_and_not_served(self):
        self.library.add("a.txt", "Alice met Bob.")
        verified = []
        rb = self._rebuilder(
            _fake_build(self.state, final_status="GATES", run_rc=1),
            verified)
        bid = rb.start()["batch_id"]
        rb.join(5)
        st = rb.status()
        self.assertEqual(st["status"], "failed")
        self.assertIn("stopped at GATES", st["message"])
        self.assertIn(f"batch run {bid}", st["message"])
        self.assertEqual(verified, [])

    def test_refusals(self):
        rb = self._rebuilder(_fake_build(self.state))
        with self.assertRaises(lib.LibraryError) as cm:
            rb.start()
        self.assertEqual(cm.exception.code, "empty_library")
        self.library.add("a.txt", "Alice met Bob.")
        gate = threading.Event()
        rb = self._rebuilder(_fake_build(self.state, gate=gate))
        rb.start()
        with self.assertRaises(lib.LibraryError) as cm:
            rb.start()
        self.assertEqual(cm.exception.code, "build_running")
        gate.set()
        rb.join(5)

    def test_env_cap_wins_and_bad_cap_refuses(self):
        self.library.add("a.txt", "Alice met Bob.")
        calls = []
        rb = self._rebuilder(_fake_build(self.state, calls=calls))
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {batch_cli.CAP_ENV: "0.5"}):
            self.assertEqual(rb.start()["cap_usd"], 0.5)
            rb.join(5)
        self.assertEqual(calls[0][1][batch_cli.CAP_ENV], "0.5")
        with mock.patch.dict(os.environ, {batch_cli.CAP_ENV: "nan"}):
            with self.assertRaises(lib.LibraryError) as cm:
                rb.start()
        self.assertEqual(cm.exception.code, "bad_cap")


class _Store:
    """The library routes never touch the serving store."""

    def describe(self):
        return {"kind": "stub"}


class TestLibraryHttp(TmpDir):
    def setUp(self):
        super().setUp()
        self.gate = threading.Event()
        self.rebuilder = lib.Rebuilder(
            self.library, pack="open-domain",
            state_root=lambda: self.state,
            run_cmd=_fake_build(self.state, gate=self.gate))
        self.service = SearchService(
            _Store(), library=LibraryService(self.library,
                                             self.rebuilder))
        self.server = build_server("127.0.0.1", 0, self.service)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever,
                         daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.gate.set)

    def req(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port,
                                          timeout=10)
        hdrs = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        try:
            return resp.status, json.loads(raw)
        except ValueError:
            return resp.status, raw.decode()

    def test_add_list_remove_over_http(self):
        st, body = self.req("POST", "/library/documents",
                            {"name": "a.txt", "text": "Alice met Bob."})
        self.assertEqual((st, body["name"]), (200, "a.txt"))
        st, body = self.req("GET", "/library")
        self.assertEqual(st, 200)
        self.assertEqual(body["totals"], {"documents": 1, "words": 3})
        self.assertEqual(body["cap"]["usd"], lib.LIBRARY_CAP_USD)
        self.assertEqual(body["build"]["status"], "idle")
        st, body = self.req("DELETE", "/library/documents?name=a.txt")
        self.assertEqual((st, body), (200, {"removed": "a.txt"}))

    def test_named_refusals(self):
        self.req("POST", "/library/documents",
                 {"name": "a.txt", "text": "Alice met Bob."})
        st, body = self.req("POST", "/library/documents",
                            {"name": "a.txt", "text": "Other."})
        self.assertEqual((st, body["error"]["code"]), (409, "name_taken"))
        conn = http.client.HTTPConnection("127.0.0.1", self.port,
                                          timeout=10)
        conn.request("POST", "/library/documents", body=b"nope!",
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = json.loads(resp.read())
        conn.close()
        self.assertEqual((resp.status, body["error"]["code"]),
                         (400, "bad_json"))

    def test_rebuild_over_http(self):
        self.req("POST", "/library/documents",
                 {"name": "a.txt", "text": "Alice met Bob."})
        st, body = self.req("POST", "/library/rebuild", {})
        self.assertEqual((st, body["status"]), (200, "running"))
        st, body = self.req("POST", "/library/rebuild", {})
        self.assertEqual((st, body["error"]["code"]),
                         (409, "build_running"))
        self.gate.set()
        self.rebuilder.join(5)
        st, body = self.req("GET", "/library")
        self.assertEqual(body["build"]["status"], "succeeded")

    def test_write_guards(self):
        doc = {"name": "a.txt", "text": "Alice met Bob."}
        st, body = self.req("POST", "/library/documents", doc,
                            {"Origin": "https://evil.example"})
        self.assertEqual((st, body["error"]["code"]),
                         (403, "forbidden_origin"))
        st, body = self.req("POST", "/library/documents", doc,
                            {"Content-Type": "text/plain"})
        self.assertEqual((st, body["error"]["code"]),
                         (415, "json_required"))
        st, body = self.req("GET", "/library", None,
                            {"Host": "attacker.example:80"})
        self.assertEqual((st, body["error"]["code"]),
                         (403, "forbidden_host"))
        st, body = self.req("POST", "/library/documents", doc,
                            {"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(st, 200)
        self.assertEqual(len(self.library.documents()), 1)

    def test_documents_page_serves(self):
        st, body = self.req("GET", "/documents")
        self.assertEqual(st, 200)
        self.assertIn("/app/library.js", body)


class TestLibraryOff(unittest.TestCase):
    def test_routes_refuse_by_name(self):
        server = build_server("127.0.0.1", 0, SearchService(_Store()))
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever,
                         daemon=True).start()
        try:
            for method, path in (("GET", "/library"),
                                 ("POST", "/library/rebuild"),
                                 ("DELETE", "/library/documents?name=a")):
                conn = http.client.HTTPConnection("127.0.0.1", port,
                                                  timeout=10)
                conn.request(method, path, body=b"{}",
                             headers={"Content-Type": "application/json"})
                resp = conn.getresponse()
                body = json.loads(resp.read())
                conn.close()
                self.assertEqual(
                    (resp.status, body["error"]["code"]),
                    (404, "library_disabled"), path)
        finally:
            server.shutdown()
            server.server_close()

    def test_library_requires_a_loopback_host(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = main(["--library", "lib", "--host", "0.0.0.0"])
        self.assertEqual(rc, 1)
        self.assertIn("loopback", err.getvalue())


class TestLibraryPage(unittest.TestCase):
    def test_js_calls_only_library_routes(self):
        js = (ROOT / "app" / "library.js").read_text()
        used = set(re.findall(r'["`](/library[a-z/]*)', js))
        self.assertEqual(used, {"/library", "/library/documents",
                                "/library/rebuild"})
        self.assertNotIn("http://", js)
        self.assertNotIn("https://", js)
        self.assertNotIn("innerHTML", js)
        # Writes are JSON-only server-side, so every POST
        # the page makes carries a JSON body (a bodiless
        # rebuild POST was refused with 415).
        for call in re.findall(r'call\("POST", [^)]*\)', js):
            self.assertRegex(call, r', [{a-z]', call)

    def test_page_references_only_its_own_assets(self):
        html = (ROOT / "app" / "library.html").read_text()
        for name in ("app.css", "library.js", "favicon.svg",
                     "logo-mark.svg"):
            self.assertIn(f"/app/{name}", html)
        self.assertNotIn('src="http', html)
        self.assertNotIn('href="http', html)


if __name__ == "__main__":
    unittest.main()
