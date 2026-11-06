"""Sample API server: ``ontosmith serve``.

A reference HTTP API over the hybrid search
(:mod:`pipeline.search`) and the built graph:

  GET /health        store identity + inventory
  GET /search        hybrid search — the same result
                     dict the ``ontosmith search`` CLI
                     prints (``?q=...&k=...``)
  GET /entity        one entity and its facts
                     (``?id=...`` or ``?name=...``)
  GET /neighborhood  REL neighborhood of an entity
                     (``?id=...&hops=1..4``)
  GET /triple        one triple with its evidence spans
                     and provenance — the inspection report as
                     JSON (``?subject=&relation=&object=``
                     or ``?index=N``)
  GET /              the demo app: the
                     static page in ``app/`` — search box,
                     triple/evidence inspector, entity
                     report + neighborhood walker
  GET /app/<file>    the app's static assets

With ``--library DIR`` (opt-in; localhost only) the server
also manages a document library and rebuilds the graph
from it (:mod:`pipeline.library`):

  GET    /documents          the Documents page
  GET    /library            documents, cap, build status
  POST   /library/documents  add one {name, text} document
  DELETE /library/documents  remove one (``?name=...``)
  POST   /library/rebuild    rebuild the graph from every
                             document; served once VERIFIED

Without the flag the sample API stays read-only.

Framework: the Python standard library
(``http.server.ThreadingHTTPServer``) — a deliberate
choice for a *reference* server: the product's dependency
discipline keeps the core install to numpy +
requests, and a sample API should be readable end-to-end
in one file, not framework-shaped. Anything heavier
belongs to the deployment, not the sample.

Errors are JSON, named, and shaped like the product's
refusals: ``{"error": {"code": ..., "message": ...}}`` —
400 for bad input (missing/invalid parameters), 404 for
an unknown entity or unmatched triple, 500 only for an
unexpected server fault (message named, no traceback).
The server binds localhost by default and takes its store
from the same surfaces as the CLI (``--source`` /
``--batch`` / ``--store`` / ``NEO4J_*``); with no source
it serves the shipped example build, so a stranger can
start it and query the demo graph immediately.
"""
from __future__ import annotations

import argparse
import json
import sys
import sysconfig
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pipeline import connections as _connections
from pipeline import inspect as _inspect
from pipeline import library as _library
from pipeline import search as _search


class ApiError(Exception):
    """A named, shaped API refusal: HTTP status + code."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class SearchService:
    """The endpoint logic over one opened serving store.
    Transport-free: the handler below is a thin shell, so
    the contract is testable without a socket."""

    def __init__(self, store, embedder=None, library=None):
        self.store = store
        self.embedder = embedder
        # LibraryService when `serve --library` is on.
        self.library = library

    # -- endpoints -------------------------------------------
    def health(self) -> dict:
        """Store identity + inventory (the store's own
        describe(), plus a status line)."""
        return {"status": "ok", "store": self.store.describe()}

    def search(self, params: dict) -> dict:
        """GET /search?q=...&k=... — the hybrid_search
        result dict, unchanged."""
        q = _required(params, "q")
        k = _int_param(params, "k", 10, lo=1, hi=100)
        try:
            return _search.hybrid_search(
                self.store, q, k=k, embedder=self.embedder)
        except ValueError as e:
            raise ApiError(400, "bad_query", str(e)) from e

    def entity(self, params: dict) -> dict:
        """GET /entity?id=... | ?name=... — the entity
        record plus every attested fact it endpoints."""
        ent = self._resolve_entity(params)
        facts = [t for t in self.store.triples()
                 if t["subject_id"] == ent["entity_id"]
                 or t["object_id"] == ent["entity_id"]]
        return {
            **ent,
            "facts_as_subject": sum(
                1 for t in facts
                if t["subject_id"] == ent["entity_id"]),
            "facts_as_object": sum(
                1 for t in facts
                if t["object_id"] == ent["entity_id"]),
            "facts": facts,
        }

    def neighborhood(self, params: dict) -> dict:
        """GET /neighborhood?id=...&hops=1..4 — the
        store's neighborhood contract, with the seed
        entity echoed."""
        ent = self._resolve_entity(params)
        hops = _int_param(params, "hops", 1, lo=1, hi=4)
        result = self.store.neighborhood(
            [ent["entity_id"]], hops=hops)
        return {"entity": ent, "hops": hops, **result}

    def triple(self, params: dict) -> dict:
        """GET /triple?subject=&relation=&object=&index=
        — one triple as the inspection report."""
        records = self.store.triple_records()
        index = params.get("index")
        index = int(index) if index is not None else None
        try:
            rec, position, total = _inspect.select(
                records, index=index,
                subject=params.get("subject"),
                relation=params.get("relation"),
                object_=params.get("object"))
        except ValueError as e:
            raise ApiError(404, "triple_not_found",
                           str(e)) from e
        return _inspect.build_report(rec, "api", position,
                                     total)

    # -- helpers -----------------------------------------------
    def _resolve_entity(self, params: dict) -> dict:
        """Entity by id (exact) or by name (the store's
        deterministic link, best hit). 400 when neither
        parameter is given, 404 when nothing resolves."""
        entity_id = params.get("id")
        name = params.get("name")
        if entity_id:
            ent = self.store.entity(entity_id)
            if ent is None:
                raise ApiError(
                    404, "entity_not_found",
                    f"no entity with id {entity_id!r}")
            return ent
        if name:
            hits = self.store.find_entities(name, limit=1)
            if not hits:
                raise ApiError(
                    404, "entity_not_found",
                    f"no entity matches name {name!r}")
            return hits[0]
        raise ApiError(
            400, "missing_parameter",
            "entity lookup needs ?id= or ?name=")


API_ROUTES = frozenset(
    {"/health", "/search", "/entity", "/neighborhood",
     "/triple"})


def api_route(service: SearchService, path: str,
              params: dict):
    """The API route table: a zero-arg callable producing
    the response payload for ``path``, or None when the
    path is not an API route. ``API_ROUTES`` is exactly
    this table's key set (pinned by test) — the demo app
    pins the endpoints it calls against it."""
    routes = {
        "/health": lambda: service.health(),
        "/search": lambda: service.search(params),
        "/entity": lambda: service.entity(params),
        "/neighborhood":
            lambda: service.neighborhood(params),
        "/triple": lambda: service.triple(params),
    }
    return routes.get(path)


def _required(params: dict, name: str) -> str:
    """A required non-empty query parameter."""
    val = params.get(name)
    if val is None or not str(val).strip():
        raise ApiError(400, "missing_parameter",
                       f"missing required parameter: {name}")
    return str(val)


def _int_param(params: dict, name: str, default: int,
               lo: int, hi: int) -> int:
    """An optional integer parameter, range-checked; the
    refusal names the parameter and the allowed range."""
    raw = params.get(name)
    if raw is None:
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        raise ApiError(
            400, "invalid_parameter",
            f"parameter {name} must be an integer, "
            f"got {raw!r}") from None
    if not lo <= val <= hi:
        raise ApiError(
            400, "invalid_parameter",
            f"parameter {name} must be {lo}..{hi}, "
            f"got {val}")
    return val


# ----------------------------------------------------------------------
# Document library — opt-in write surface (serve --library)
# ----------------------------------------------------------------------
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
#: Largest request body the library accepts (bytes): one
#: document at the character limit, UTF-8, plus JSON framing.
MAX_BODY_BYTES = _library.MAX_DOC_CHARS * 4 + 4096


class LibraryService:
    """The library endpoints over one DocumentLibrary and
    its Rebuilder. `served_batch` names the batch the
    server currently serves (None: whatever the store
    holds)."""

    def __init__(self, library, rebuilder,
                 served_batch: str | None = None):
        self.library = library
        self.rebuilder = rebuilder
        self.served_batch = served_batch

    def status(self) -> dict:
        docs = self.library.documents()
        try:
            cap, source = _library.effective_cap()
            cap_info = {"usd": cap, "source": source}
        except _library.LibraryError as e:
            cap_info = {"usd": None, "source": None,
                        "error": e.message}
        return {
            "folder": str(self.library.root),
            "pack": self.rebuilder.pack,
            "documents": docs,
            "totals": {"documents": len(docs),
                       "words": sum(d["words"] for d in docs)},
            "cap": cap_info,
            "served_batch": self.served_batch,
            "build": self.rebuilder.status(),
        }

    def add(self, body: dict) -> dict:
        return self.library.add(body.get("name", ""),
                                body.get("text", ""))

    def remove(self, params: dict) -> dict:
        return self.library.remove(_required(params, "name"))

    def rebuild(self) -> dict:
        return self.rebuilder.start()


def _host_name(host_header: str | None) -> str:
    """The host part of a Host header ("[::1]:8765" →
    "::1", "localhost:8765" → "localhost")."""
    h = (host_header or "").strip()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else ""
    return h.rsplit(":", 1)[0] if ":" in h else h


# ----------------------------------------------------------------------
# Demo app — static assets, served by the same shell
# ----------------------------------------------------------------------
APP_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}


def app_candidates() -> list[Path]:
    """Where the demo app's static files may live, in
    resolution order — the shipped example's candidate
    discipline: the source tree, the pip
    ``--target`` layout (data files beside the packages),
    the regular install layout (the interpreter's data
    path)."""
    pkg_parent = Path(__file__).resolve().parent.parent
    return [
        pkg_parent / "app",
        pkg_parent / "share" / "ontosmith" / "app",
        Path(sysconfig.get_path("data")) / "share"
        / "ontosmith" / "app",
    ]


def app_dir() -> Path | None:
    """The resolved demo-app directory (the first
    candidate holding ``index.html``), or None when no
    candidate resolves — the server then serves the JSON
    API only and says so at startup."""
    for cand in app_candidates():
        if (cand / "index.html").is_file():
            return cand
    return None


def app_asset(name: str) -> tuple[bytes, str] | None:
    """One app asset by bare file name: ``(bytes,
    content type)``, or None. The name must be a single
    path segment — no directories, no traversal, no
    dotfiles — with a known content type, and the file
    must exist in the resolved app directory."""
    if not name or name != Path(name).name \
            or name.startswith("."):
        return None
    root = app_dir()
    if root is None:
        return None
    path = root / name
    ctype = APP_CONTENT_TYPES.get(path.suffix.lower())
    if ctype is None or not path.is_file():
        return None
    return path.read_bytes(), ctype


# ----------------------------------------------------------------------
# HTTP transport — a thin shell over SearchService
# ----------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    """Routes GETs: ``/`` and ``/app/<file>`` serve the
    demo app; the API routes answer JSON. POST and DELETE
    exist only for the opt-in library routes; without
    ``--library`` the sample API is read-only."""

    service: SearchService  # set by build_server
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence access logs
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/" \
                or parsed.path.startswith("/app/"):
            self._serve_app(parsed.path)
            return
        if parsed.path == "/documents":
            self._serve_app("/app/library.html")
            return
        if parsed.path == "/library":
            self._library(lambda lib: lib.status(),
                          write=False)
            return
        params = {k: v[0] for k, v in
                  parse_qs(parsed.query).items()}
        route = api_route(self.service, parsed.path,
                          params)
        if route is None:
            self._send(404, {"error": {
                "code": "not_found",
                "message": f"no such endpoint: "
                           f"{parsed.path}"}})
            return
        try:
            self._send(200, route())
        except ApiError as e:
            self._send(e.status, {"error": {
                "code": e.code, "message": e.message}})
        except Exception as e:  # named, never a traceback
            self._send(500, {"error": {
                "code": "internal",
                "message": f"{type(e).__name__}: {e}"}})

    def do_POST(self):
        # A refused write may leave its body unread; never
        # reuse the connection after one.
        self.close_connection = True
        path = urlparse(self.path).path
        if path == "/library/documents":
            self._library(lambda lib: lib.add(self._json_body()),
                          write=True)
        elif path == "/library/rebuild":
            self._library(lambda lib: lib.rebuild(), write=True)
        else:
            self._send(404, {"error": {
                "code": "not_found",
                "message": f"no such endpoint: {path}"}})

    def do_DELETE(self):
        self.close_connection = True
        parsed = urlparse(self.path)
        if parsed.path != "/library/documents":
            self._send(404, {"error": {
                "code": "not_found",
                "message": f"no such endpoint: {parsed.path}"}})
            return
        params = {k: v[0] for k, v in
                  parse_qs(parsed.query).items()}
        self._library(lambda lib: lib.remove(params), write=True)

    # -- library plumbing --------------------------------------
    def _library(self, call, write: bool) -> None:
        """Run one library call behind its guards: the
        library must be on, the request must come to a
        loopback host name (a DNS-rebinding page cannot
        reach it), and a write must carry JSON from this
        origin (a cross-site form or no-CORS fetch cannot
        add, remove or spend)."""
        lib = self.service.library
        if lib is None:
            self._send(404, {"error": {
                "code": "library_disabled",
                "message": "the document library is off — "
                           "start the server with "
                           "`ontosmith serve --library DIR`"}})
            return
        host = self.headers.get("Host")
        if _host_name(host) not in LOOPBACK_HOSTS:
            self._send(403, {"error": {
                "code": "forbidden_host",
                "message": "the library answers only on a "
                           "loopback host name"}})
            return
        if write:
            origin = self.headers.get("Origin")
            if origin is not None and origin != f"http://{host}":
                self._send(403, {"error": {
                    "code": "forbidden_origin",
                    "message": f"cross-origin write refused "
                               f"({origin})"}})
                return
            ctype = (self.headers.get("Content-Type") or "")
            if self.command == "POST" and not ctype.startswith(
                    "application/json"):
                self._send(415, {"error": {
                    "code": "json_required",
                    "message": "send the request body as "
                               "application/json"}})
                return
        try:
            self._send(200, call(lib))
        except (ApiError, _library.LibraryError) as e:
            self._send(e.status, {"error": {
                "code": e.code, "message": e.message}})
        except Exception as e:  # named, never a traceback
            self._send(500, {"error": {
                "code": "internal",
                "message": f"{type(e).__name__}: {e}"}})

    def _json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            raise ApiError(413, "body_too_large",
                           f"request body over {MAX_BODY_BYTES:,} "
                           f"bytes")
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, "bad_json",
                           "the request body is not JSON") from None
        if not isinstance(body, dict):
            raise ApiError(400, "bad_json",
                           "the request body must be a JSON object")
        return body

    def _serve_app(self, path: str) -> None:
        """The demo app: ``/`` is its index page,
        ``/app/<file>`` one of its assets. A miss is the
        API's JSON 404, never an HTML error page."""
        name = "index.html" if path == "/" \
            else path[len("/app/"):]
        asset = app_asset(name)
        if asset is None:
            self._send(404, {"error": {
                "code": "not_found",
                "message": f"no such app asset: {path}"}})
            return
        body, ctype = asset
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False,
                          ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type",
                         "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def build_server(host: str, port: int,
                 service: SearchService) -> ThreadingHTTPServer:
    """A bound (not yet serving) server for ``service``.
    Port 0 picks an ephemeral port — the test suite's
    route, and a safe default for embedding."""
    handler = type("BoundHandler", (_Handler,),
                   {"service": service})
    return ThreadingHTTPServer((host, port), handler)


def main(argv=None) -> int:
    """``ontosmith serve [--source PATH] [--batch ID]
    [--store auto|file|neo4j] [--host H] [--port N]
    [--embed]`` — serve the sample API (default:
    localhost:8765, the shipped example build). The
    demo app is served at ``/`` on the same port
    when its assets resolve."""
    ap = argparse.ArgumentParser(
        prog="ontosmith serve",
        description="Sample API server over the "
                    "search/graph surface: "
                    "/search, /entity, /neighborhood, "
                    "/triple (with evidence)")
    ap.add_argument("--source", default=None,
                    help="batch directory, records "
                         "directory, or records file "
                         "(default: the shipped example "
                         "build)")
    ap.add_argument("--batch", default=None,
                    help="batch id under the state root's "
                         "batches/ (alternative to --source)")
    ap.add_argument("--store", default="auto",
                    choices=("auto", "file", "neo4j"),
                    help="serving store: auto = Neo4j when "
                         "NEO4J_URL is configured, else the "
                         "file store (default: auto)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind host (default: 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8765,
                    help="bind port (default: 8765)")
    ap.add_argument("--library", default=None, metavar="DIR",
                    help="manage a folder of .txt documents "
                         "from the Documents page at "
                         "/documents and rebuild the graph "
                         "from it (localhost only; each "
                         "rebuild spends, under a cap)")
    ap.add_argument("--pack", default="open-domain",
                    help="the pack library rebuilds use "
                         "(default: open-domain)")
    ap.add_argument("--embed", action="store_true",
                    help="activate the search vector arm "
                         "via the connection layer's "
                         "default embedder (refuses if the "
                         "embedder seat's key is unset)")
    args = ap.parse_args(argv)
    if args.library and args.host not in LOOPBACK_HOSTS:
        print("serve refused: --library changes files and "
              "spends on builds, so it binds a loopback host "
              "only (127.0.0.1, localhost or ::1)",
              file=sys.stderr)
        return 1

    try:
        store = _search.open_store(source=args.source,
                                   batch=args.batch,
                                   store=args.store)
        embedder = None
        if args.embed:
            from pipeline.embedder import default_embedder
            embedder = default_embedder()
        else:
            # Construction only — no call is made here,
            # and hybrid_search never embeds against a
            # store that ships no chunk embeddings.
            embedder = _search.try_embedder()
    except (ValueError, _connections.BindingKeyError) as e:
        print(f"serve refused: {e}", file=sys.stderr)
        return 1
    service = SearchService(store, embedder=embedder)
    if args.library:
        from pipeline.batch_cli import state_root
        lib_service = LibraryService(None, None, args.batch)

        def serve_new_batch(batch_id: str) -> None:
            # The rebuild reached VERIFIED: serve its graph.
            service.store = _search.open_store(
                batch=batch_id, store=args.store)
            lib_service.served_batch = batch_id

        lib_service.library = _library.DocumentLibrary(
            Path(args.library))
        lib_service.rebuilder = _library.Rebuilder(
            lib_service.library, pack=args.pack,
            state_root=state_root, on_verified=serve_new_batch)
        service.library = lib_service
    server = build_server(args.host, args.port, service)
    host, port = server.server_address[:2]
    desc = store.describe()
    print(f"ontosmith serve: {desc['kind']} store "
          f"({desc['triples']} triples, "
          f"{desc['documents']} documents) at "
          f"http://{host}:{port} — /search /entity "
          f"/neighborhood /triple /health")
    if service.library is not None:
        print(f"ontosmith serve: document library "
              f"{service.library.library.root} — manage it at "
              f"http://{host}:{port}/documents")
    if args.source is None and args.batch is None:
        print("ontosmith serve: this is the shipped demo "
              "graph — Pride and Prejudice, the full text, "
              "built by this pipeline. For your own "
              "document: ontosmith quickstart <your-file.txt>")
    app = app_dir()
    if app is not None:
        print(f"ontosmith serve: demo app at "
              f"http://{host}:{port}/ (assets: {app})")
    else:
        print("ontosmith serve: demo app assets not "
              "found — JSON API only")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
