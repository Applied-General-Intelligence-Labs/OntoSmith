"""The document library: a folder of plain-text documents
that one graph is built from, plus the runner that rebuilds
that graph.

The Neo4j store holds ONE graph, and a build under a new
batch replaces it (neo4j_store.check_store_write). A batch's
document list is fixed at create. So "adding documents" is:
put the files in the library, then rebuild — a new batch over
EVERY document in the library, old and new — and serve the
result once it reaches VERIFIED. Each rebuild re-processes
the whole library, so its spend grows with the library.

`ontosmith serve --library DIR` exposes this through the
Documents page (app/library.html). The logic here is
transport-free, so it is testable without a socket.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from . import batch_cli

#: Default spend cap (USD) for a library rebuild on a binding
#: that meters cost; ONTOSMITH_MAX_COST_USD overrides it.
LIBRARY_CAP_USD = 5.0
#: Largest single document accepted (characters).
MAX_DOC_CHARS = 2_000_000
#: Log lines kept for the page (the full log is the batch's).
LOG_LINES = 400

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


class LibraryError(ValueError):
    """A named refusal: `code` is machine-readable, the
    message says what to do. `status` is the HTTP status the
    server answers with."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def safe_name(name: str) -> str:
    """A library file name from a user-supplied one: the
    base name only, unsafe characters replaced, always
    ending .txt. Refuses names that end up empty or hidden."""
    base = Path(str(name or "").replace("\\", "/")).name.strip()
    if base.lower().endswith(".txt"):
        base = base[:-4]
    base = _SAFE_NAME.sub("-", base).strip(" .-")
    if not base:
        raise LibraryError(400, "bad_name",
                           f"not a usable file name: {name!r}")
    return f"{base[:120]}.txt"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DocumentLibrary:
    """A folder of .txt documents. Names are unique; two
    documents with identical text are refused, since the
    build would ingest them once anyway."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # name -> ((mtime_ns, size), facts): a file is read
        # again only when it changes on disk.
        self._cache: dict[str, tuple[tuple[int, int], dict]] = {}

    def _paths(self) -> list[Path]:
        return sorted(p for p in self.root.glob("*.txt")
                      if p.is_file() and not p.name.startswith("."))

    def _facts(self, p: Path) -> dict:
        """chars, words, digest and modified time of one
        file, from the cache while the file is unchanged."""
        st = p.stat()
        key = (st.st_mtime_ns, st.st_size)
        hit = self._cache.get(p.name)
        if hit is not None and hit[0] == key:
            return hit[1]
        text = p.read_text(encoding="utf-8", errors="replace")
        facts = {
            "chars": len(text),
            "words": len(text.split()),
            "digest": _digest(text),
            "modified": datetime.fromtimestamp(
                st.st_mtime, UTC).isoformat(timespec="seconds"),
        }
        self._cache[p.name] = (key, facts)
        return facts

    def documents(self) -> list[dict]:
        paths = self._paths()
        out = []
        for p in paths:
            f = self._facts(p)
            out.append({"name": p.name, "chars": f["chars"],
                        "words": f["words"],
                        "modified": f["modified"]})
        names = {p.name for p in paths}
        for gone in [n for n in self._cache if n not in names]:
            self._cache.pop(gone, None)
        return out

    def add(self, name: str, text: str) -> dict:
        fname = safe_name(name)
        if not isinstance(text, str) or not text.strip():
            raise LibraryError(400, "empty_document",
                               f"{fname} has no text")
        if len(text) > MAX_DOC_CHARS:
            raise LibraryError(
                413, "document_too_large",
                f"{fname} has {len(text):,} characters; the "
                f"limit is {MAX_DOC_CHARS:,} — split it into "
                f"smaller files")
        with self._lock:
            path = self.root / fname
            if path.exists():
                raise LibraryError(
                    409, "name_taken",
                    f"the library already has {fname} — remove "
                    f"it first or rename the new file")
            digest = _digest(text)
            for other in self._paths():
                if self._facts(other)["digest"] == digest:
                    raise LibraryError(
                        409, "duplicate_text",
                        f"{fname} has the same text as "
                        f"{other.name}, which is already in the "
                        f"library")
            path.write_text(text, encoding="utf-8")
        return {"name": fname, "chars": len(text),
                "words": len(text.split())}

    def remove(self, name: str) -> dict:
        """Remove a document by the name the library lists
        it under. Files copied into the folder by hand keep
        their own names, which safe_name might rewrite, so
        an exact listed name is matched first; safe_name is
        only the fallback for a name typed loosely."""
        with self._lock:
            listed = {p.name: p for p in self._paths()}
            path = listed.get(str(name or ""))
            if path is None:
                path = listed.get(safe_name(name))
            if path is None:
                raise LibraryError(404, "not_found",
                                   f"no document named {name!r}")
            path.unlink()
            self._cache.pop(path.name, None)
        return {"removed": path.name}


def effective_cap(env=None) -> tuple[float | None, str]:
    """The cap a rebuild runs under, and where it comes
    from. Raises LibraryError when ONTOSMITH_MAX_COST_USD is
    set to something unusable."""
    env = os.environ if env is None else env
    try:
        cap = batch_cli.cap_from_env(env)
    except ValueError as e:
        raise LibraryError(400, "bad_cap", str(e)) from e
    if cap is not None:
        return cap, batch_cli.CAP_ENV
    cap = batch_cli.metered_default_cap(LIBRARY_CAP_USD)
    if cap is not None:
        return cap, "library default"
    return None, "none (this binding can't report cost)"


def _run_streaming(argv: list[str], env: dict,
                   on_line: Callable[[str], None],
                   on_proc: Callable[[subprocess.Popen | None],
                                     None] | None = None) -> int:
    """Run `argv`, feeding each output line to `on_line`;
    returns the exit code. `on_proc` is told the running
    process (and None once it exits), so its owner can stop
    it."""
    proc = subprocess.Popen(
        argv, env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1)
    if on_proc is not None:
        on_proc(proc)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            on_line(line.rstrip("\n"))
        return proc.wait()
    finally:
        if on_proc is not None:
            on_proc(None)


class Rebuilder:
    """Rebuilds the graph from the whole library in a
    background thread, one build at a time. `on_verified`
    is called with the new batch id once the build reaches
    VERIFIED (the server switches its store there);
    `on_failed` with the batch id when it does not (the
    server re-reads what the store now holds). `run_cmd` is
    injectable so tests never spend."""

    def __init__(self, library: DocumentLibrary, pack: str,
                 state_root: Callable[[], Path],
                 on_verified: Callable[[str], None] | None = None,
                 on_failed: Callable[[str], None] | None = None,
                 run_cmd=None):
        self.library = library
        self.pack = pack
        self.state_root = state_root
        self.on_verified = on_verified
        self.on_failed = on_failed
        self.run_cmd = run_cmd or self._run_tracked
        self._proc: subprocess.Popen | None = None
        self._stopping = False
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._log: deque[str] = deque(maxlen=LOG_LINES)
        self.state: dict = {"status": "idle"}

    # -- read side -------------------------------------------
    def status(self) -> dict:
        with self._lock:
            return {**self.state, "log": list(self._log)}

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- write side ------------------------------------------
    def start(self) -> dict:
        """Start a rebuild; refuses while one is running, on
        an empty library, or on an unusable cap."""
        with self._lock:
            if self.running:
                raise LibraryError(
                    409, "build_running",
                    f"a rebuild is already running "
                    f"({self.state.get('batch_id')})")
            docs = self.library.documents()
            if not docs:
                raise LibraryError(
                    400, "empty_library",
                    "the library has no documents — add at "
                    "least one .txt file first")
            cap, cap_source = effective_cap()
            batch_id = self._new_batch_id()
            self._stopping = False
            self._log.clear()
            self.state = {
                "status": "running", "batch_id": batch_id,
                "documents": len(docs),
                "words": sum(d["words"] for d in docs),
                "cap_usd": cap, "cap_source": cap_source,
                "started_at": _now(), "finished_at": None,
                "cost_usd": None, "message": "creating the batch"}
            self._thread = threading.Thread(
                target=self._build, args=(batch_id, cap),
                name=f"rebuild-{batch_id}", daemon=True)
            self._thread.start()
            return dict(self.state)

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def stop(self, timeout: float = 10.0) -> None:
        """Stop a running build: its batch command is
        terminated (the batch stays resumable with `ontosmith
        batch run`). The server calls this on shutdown, so a
        build never outlives the page that started it and
        keeps spending unseen."""
        with self._lock:
            self._stopping = True
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.join(timeout)

    def _new_batch_id(self) -> str:
        """A fresh batch id: the time to the second, with a
        counter when that id is already taken (a fast failure
        retried within the same second)."""
        base = "library-" + datetime.now(UTC).strftime(
            "%Y%m%d-%H%M%S")
        batches = self.state_root() / "batches"
        batch_id, n = base, 1
        while (batches / batch_id).exists():
            n += 1
            batch_id = f"{base}-{n}"
        return batch_id

    def _track(self, proc: subprocess.Popen | None) -> None:
        with self._lock:
            self._proc = proc
        if proc is not None and self._stopping:
            proc.terminate()

    def _run_tracked(self, argv, env, on_line) -> int:
        return _run_streaming(argv, env, on_line,
                              on_proc=self._track)

    # -- the build -------------------------------------------
    def _line(self, text: str) -> None:
        with self._lock:
            self._log.append(text)

    def _set(self, **fields) -> None:
        with self._lock:
            self.state.update(fields)

    def _build(self, batch_id: str, cap: float | None) -> None:
        env = dict(os.environ)
        if cap is None:
            env.pop(batch_cli.CAP_ENV, None)
        else:
            env[batch_cli.CAP_ENV] = repr(cap)
        try:
            ok, message = self._create_and_run(batch_id, env)
            if ok and self.on_verified is not None:
                self.on_verified(batch_id)
        except Exception as e:  # reported on the page
            ok, message = False, f"{type(e).__name__}: {e}"
        if not ok and self.on_failed is not None:
            try:
                self.on_failed(batch_id)
            except Exception as e:  # reported on the page
                message += f"; then {type(e).__name__}: {e}"
        self._finish("succeeded" if ok else "failed", batch_id,
                     "built and now being served" if ok
                     else message)

    def _create_and_run(self, batch_id: str,
                        env: dict) -> tuple[bool, str]:
        """`batch create` then `batch run`: (reached
        VERIFIED, what to tell the page)."""
        cli = [sys.executable, "-m", "pipeline", "batch"]
        # The library path is escaped: create expands the
        # pattern with glob, and a folder named like
        # "notes [2026]" would otherwise match nothing.
        docs = os.path.join(glob.escape(str(self.library.root)),
                            "*.txt")
        rc = self.run_cmd(
            [*cli, "create", "--source", "library",
             "--collection", "library", "--docs", docs,
             "--pack", self.pack, "--batch-id", batch_id],
            env, self._line)
        if rc != 0:
            return False, f"batch create exited with {rc}"
        if self._stopping:
            return False, "stopped before the build started"
        self._set(message="building (this re-processes "
                          "every document)")
        rc = self.run_cmd([*cli, "run", batch_id], env, self._line)
        status = self._batch_status(batch_id)
        if rc != 0 or status != "VERIFIED":
            return False, (
                f"the build stopped at {status or 'unknown'} "
                f"(exit {rc}); resume it with: ontosmith batch "
                f"run {batch_id}")
        return True, ""

    def _batch_json(self, batch_id: str) -> dict | None:
        p = self.state_root() / "batches" / batch_id / "batch.json"
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            return None

    def _batch_status(self, batch_id: str) -> str | None:
        b = self._batch_json(batch_id)
        return b.get("status") if b else None

    def _finish(self, status: str, batch_id: str,
                message: str) -> None:
        b = self._batch_json(batch_id) or {}
        ledger = (b.get("metrics") or {}).get("cost_ledger") or {}
        self._set(status=status, message=message,
                  finished_at=_now(),
                  cost_usd=ledger.get("total_usd"))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
