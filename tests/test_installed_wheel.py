"""Installed-wheel smoke.

The blocker: `ontosmith configure --emit-template`
resolved its template by a source-tree-relative path, so
the door worked in a checkout and died for pip-installed
users — and the guard tests read the same source-tree
path, so nothing could see it. This test builds the
wheel, installs it into a clean venv, and runs the doors
from a NON-repo working directory:

- the wheel physically contains the template (the
  mechanical guard the source-tree tests cannot give);
- --emit-template serves the shipped template;
- a filled copy of that template completes a
  configure --from-file round trip against a stub
  provider;
- the keyless doors (profile, inspect) run over the
  shipped examples from the installed layout, profile
  reproducing the published hash.

Build + install need network for pip's build isolation
and the runtime dependencies; that is the same network
CI itself uses to install the suite's dependencies.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

REPO = Path(__file__).resolve().parent.parent
TEMPLATE_REL = "pipeline/bindings-template.env"


def _run(cmd, cwd, env=None, timeout=900):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(cmd, cwd=cwd, env=full_env,
                          capture_output=True, text=True,
                          timeout=timeout)


class _ProbeStub(BaseHTTPRequestHandler):
    """Just enough provider for configure's zero-spend
    probes: a model list and a decisions route."""

    MODELS: ClassVar[list] = [
        "acme/proposer-x", "acme/judge-y", "acme/esc-m",
        "acme/draft-m", "acme/embed-z"]

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
                {"id": m} for m in self.MODELS]})
        else:
            self._json(404, {})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        if self.path.endswith("/decisions"):
            self._json(200, {})
        else:
            self._json(404, {})


class TestInstalledWheel(unittest.TestCase):
    """One build + install shared by the door tests."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="fe3-wheel-"))
        src = cls.tmp / "src"
        shutil.copytree(
            REPO, src,
            ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "batches", "data",
                "build", "dist", "*.egg-info", ".fe3*"))
        dist = cls.tmp / "dist"
        r = _run([sys.executable, "-m", "pip", "wheel",
                  str(src), "--no-deps", "-w", str(dist)],
                 cwd=cls.tmp)
        if r.returncode != 0:
            raise RuntimeError(
                f"wheel build failed:\n{r.stdout[-2000:]}\n"
                f"{r.stderr[-2000:]}")
        wheels = list(dist.glob("*.whl"))
        assert len(wheels) == 1, wheels
        cls.wheel = wheels[0]

        venv = cls.tmp / "venv"
        r = _run([sys.executable, "-m", "venv", str(venv)],
                 cwd=cls.tmp)
        assert r.returncode == 0, r.stderr[-1000:]
        cls.venv_python = (venv / "bin" / "python")
        r = _run([str(cls.venv_python), "-m", "pip", "install",
                  "--quiet", str(cls.wheel)], cwd=cls.tmp)
        if r.returncode != 0:
            raise RuntimeError(
                f"wheel install failed:\n{r.stdout[-2000:]}\n"
                f"{r.stderr[-2000:]}")
        r = _run([str(cls.venv_python), "-c",
                  "import sys; print(sys.prefix)"],
                 cwd=cls.tmp)
        cls.prefix = Path(r.stdout.strip())
        cls.workdir = cls.tmp / "workdir"
        cls.workdir.mkdir()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _installed(self, *argv, env=None):
        return _run([str(self.venv_python), "-m", "pipeline",
                     *argv], cwd=self.workdir, env=env)

    def test_wheel_contains_template(self):
        with zipfile.ZipFile(self.wheel) as z:
            names = z.namelist()
            self.assertIn(TEMPLATE_REL, names)
            shipped = z.read(TEMPLATE_REL)
        docs_copy = (REPO / "docs"
                     / "bindings-template.env").read_bytes()
        self.assertEqual(shipped, docs_copy)

    def test_emit_template_from_non_repo_cwd(self):
        r = self._installed("configure", "--emit-template")
        self.assertEqual(r.returncode, 0, r.stderr[-1000:])
        docs_copy = (REPO / "docs"
                     / "bindings-template.env").read_text()
        self.assertEqual(r.stdout, docs_copy)

    def test_from_file_round_trip_from_non_repo_cwd(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _ProbeStub)
        self.addCleanup(srv.shutdown)
        threading.Thread(target=srv.serve_forever,
                         daemon=True).start()
        base = (f"http://127.0.0.1:"
                f"{srv.server_address[1]}/v1")

        emitted = self._installed("configure", "--emit-template")
        self.assertEqual(emitted.returncode, 0, emitted.stderr)
        models = {"PROPOSER": "acme/proposer-x",
                  "JUDGE": "acme/judge-y",
                  "ESCALATION": "acme/esc-m",
                  "DRAFTING": "acme/draft-m",
                  "EMBEDDER": "acme/embed-z"}
        filled = []
        for line in emitted.stdout.splitlines():
            m = re.match(r'(ONTOSMITH_([A-Z]+)_(BASE_URL|MODEL|'
                         r'KEY_ENV|DECISIONS_URL))="<[^>]*>"',
                         line)
            if m:
                seat, kind = m.group(2), m.group(3)
                if kind == "BASE_URL":
                    value = base
                elif kind == "MODEL":
                    value = models[seat]
                elif kind == "KEY_ENV":
                    value = "STUB_KEY"
                else:
                    value = f"{base}/decisions"
                line = f'{m.group(1)}="{value}"'
            filled.append(line)
        form = self.workdir / "my-bindings.env"
        form.write_text("\n".join(filled) + "\n")

        out = self.workdir / "ontosmith-bindings.env"
        r = self._installed(
            "configure", "--from-file", str(form),
            "--out", str(out), env={"STUB_KEY": "stub-key"})
        self.assertEqual(r.returncode, 0,
                         f"{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
        rendered = out.read_text()
        self.assertIn("# Binding: custom", rendered)
        self.assertIn("# Validation: validated", rendered)
        self.assertIn("acme/judge-y", rendered)

    def test_keyless_doors_from_non_repo_cwd(self):
        # The installed keyless journey over the shipped
        # examples (share/ontosmith layout): profile
        # reproduces the published profile hash, inspect
        # reads the shipped triples. (The demo replay
        # script is checkout-bound by design — it pins
        # its own tree on sys.path — and the wheel does
        # not promise it; the configure doors above are
        # the installed-layout scope.)
        share = self.prefix / "share" / "ontosmith"
        corpus = share / "examples" / "corpus"
        self.assertTrue(corpus.is_dir(),
                        f"installed examples missing: {corpus}")
        r = self._installed("profile", str(corpus),
                            "--family", "demo")
        self.assertEqual(r.returncode, 0,
                         f"{r.stdout[-1000:]}\n{r.stderr[-1000:]}")
        self.assertIn("sha256:28ede2ab1b3e4fd2", r.stdout)
        r = self._installed(
            "inspect", str(share / "examples" / "triples.jsonl"))
        self.assertEqual(r.returncode, 0,
                         f"{r.stdout[-1000:]}\n{r.stderr[-1000:]}")
        # The shipped demo build's frozen records (the
        # Pride and Prejudice full-text build).
        self.assertIn("1 of 737", r.stdout)
        self.assertIn("Lizzy", r.stdout)


if __name__ == "__main__":
    unittest.main()
