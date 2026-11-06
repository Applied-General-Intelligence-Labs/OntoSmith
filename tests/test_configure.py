"""``ontosmith configure`` — the front door.

Guards the §7 contract: one mechanism, two doors, one
artifact. Artifact goldens for both §7 configurations live
in tests/golden/configure_{generic,preset}.env; the
interactive door and --from-file must produce byte-identical
files for identical answers; preset-exact answers stamp
`reference`; unfilled template placeholders refuse with the
missing fields named; the zero-spend probe suite runs
against a local stub server (never a live provider); a
probe failure still writes the file, stamped not-validated,
and exits non-zero. Key VALUES never appear in any output.
"""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from pipeline import __main__ as cli_main
from pipeline import configure, connections

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_GENERIC = ROOT / "tests" / "golden" / "configure_generic.env"
GOLDEN_PRESET = ROOT / "tests" / "golden" / "configure_preset.env"

GENERIC_ENV = {}
for _seat, _model in [("PROPOSER", "acme/proposer-x"),
                      ("JUDGE", "acme/judge-x"),
                      ("ESCALATION", "acme/esc-x"),
                      ("DRAFTING", "acme/draft-x"),
                      ("EMBEDDER", "acme/embed-x")]:
    GENERIC_ENV[f"ONTOSMITH_{_seat}_BASE_URL"] = \
        "https://api.example.com/v1"
    GENERIC_ENV[f"ONTOSMITH_{_seat}_MODEL"] = _model
    GENERIC_ENV[f"ONTOSMITH_{_seat}_KEY_ENV"] = "LLM_API_KEY"
    GENERIC_ENV[f"ONTOSMITH_{_seat}_DIALECT"] = "openai"
for _seat in ("JUDGE", "ESCALATION"):
    GENERIC_ENV[f"ONTOSMITH_{_seat}_DECISIONS_URL"] = \
        "https://api.example.com/v1/decisions"


def _preset_env() -> dict[str, str]:
    preset = connections.preset_bindings()
    env = {}
    for name in connections.SEATS:
        s = preset.seat(name)
        if s.model is None:
            continue
        tag = name.upper()
        env[f"ONTOSMITH_{tag}_BASE_URL"] = s.base_url
        env[f"ONTOSMITH_{tag}_MODEL"] = s.model
        env[f"ONTOSMITH_{tag}_KEY_ENV"] = s.key_env
        env[f"ONTOSMITH_{tag}_DIALECT"] = s.dialect
    return env


def _env_file_text(env: dict[str, str]) -> str:
    return "".join(f'{k}="{v}"\n' for k, v in env.items())


class TestArtifactGoldens(unittest.TestCase):
    def _run_from_file(self, env, tmp, extra=("--no-probe",)):
        src = tmp / "in.env"
        src.write_text(_env_file_text(env))
        out = tmp / "out.env"
        with redirect_stdout(io.StringIO()), \
                redirect_stderr(io.StringIO()):
            rc = configure.main(
                ["--from-file", str(src), "--out", str(out),
                 *extra])
        return rc, out

    def test_generic_configuration_golden(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rc, out = self._run_from_file(GENERIC_ENV, Path(td))
            text = out.read_text()
        self.assertEqual(rc, 0)
        self.assertEqual(text, GOLDEN_GENERIC.read_text())

    def test_preset_configuration_golden_and_reference_stamp(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rc, out = self._run_from_file(_preset_env(), Path(td))
            text = out.read_text()
        self.assertEqual(rc, 0)
        self.assertEqual(text, GOLDEN_PRESET.read_text())
        self.assertIn("# Binding: reference", text)

    def test_interactive_answers_match_from_file_bytes(self):
        # The §7(a) configuration through the interactive door:
        # shared base/key/dialect, per-seat models, drafting
        # bound, no overrides, no advanced declarations.
        answers = iter([
            "https://api.example.com/v1",  # shared base
            "LLM_API_KEY",                  # shared key var
            "",                             # dialect (openai)
            "acme/proposer-x", "n",         # proposer
            "acme/judge-x", "n", "",        # judge + decisions
            "acme/esc-x", "n", "",          # escalation + dec.
            "y", "acme/draft-x", "n",       # drafting bound
            "acme/embed-x", "n",            # embedder
            "n",                            # advanced? no
        ])
        with redirect_stdout(io.StringIO()):
            env_map = configure.interactive_env(
                input_fn=lambda prompt="": next(answers))
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "interactive.env"
            with redirect_stdout(io.StringIO()), \
                    redirect_stderr(io.StringIO()):
                rc = configure.finish(env_map, out, probe=False)
            self.assertEqual(rc, 0)
            interactive_bytes = out.read_text()
            rc2, out2 = self._run_from_file(GENERIC_ENV, Path(td))
            self.assertEqual(rc2, 0)
            self.assertEqual(interactive_bytes, out2.read_text())
            self.assertEqual(interactive_bytes,
                             GOLDEN_GENERIC.read_text())


class TestTemplateDoor(unittest.TestCase):
    def test_emit_template_prints_the_shipped_template(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = configure.main(["--emit-template"])
        self.assertEqual(rc, 0)
        self.assertEqual(
            buf.getvalue(),
            (ROOT / "docs" / "bindings-template.env").read_text())

    def test_unfilled_template_refuses_naming_fields(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "tpl.env"
            src.write_text(
                (ROOT / "docs" / "bindings-template.env")
                .read_text())
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), \
                    redirect_stderr(err):
                rc = configure.main(
                    ["--from-file", str(src), "--no-probe"])
            # A refusal writes nothing: the input is untouched.
            self.assertEqual(
                src.read_text(),
                (ROOT / "docs" / "bindings-template.env")
                .read_text())
        self.assertEqual(rc, 2)
        self.assertIn("ONTOSMITH_PROPOSER_MODEL",
                      err.getvalue())
        self.assertIn("ONTOSMITH_JUDGE_DECISIONS_URL",
                      err.getvalue())

    def test_configure_is_registered(self):
        self.assertIn("configure", cli_main.COMMANDS)
        module, _group, _blurb, _fixed = \
            cli_main.COMMANDS["configure"]
        self.assertEqual(module, "pipeline.configure")


# ----------------------------------------------------------------------
# Probe suite against a local stub server
# ----------------------------------------------------------------------

class _ProbeHandler(BaseHTTPRequestHandler):
    models: list = ["acme/proposer-x", "acme/judge-x",  # noqa: RUF012
                    "acme/esc-x", "acme/draft-x", "acme/embed-x"]
    decisions_status = 400  # structural: route exists
    hits: list = []  # noqa: RUF012

    def log_message(self, *a):
        pass

    def _send(self, status, payload):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        type(self).hits.append(("GET", self.path))
        if self.path.endswith("/models"):
            self._send(200, {"data": [
                {"id": m} for m in type(self).models]})
        else:
            self._send(404, {})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        type(self).hits.append(("POST", self.path))
        if self.path.endswith("/decisions"):
            self._send(type(self).decisions_status,
                       {"error": "structural"})
        else:
            self._send(404, {})


class ProbeCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ProbeHandler.hits = []
        cls.server = HTTPServer(("127.0.0.1", 0), _ProbeHandler)
        import threading
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = (f"http://127.0.0.1:"
                    f"{cls.server.server_address[1]}/v1")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _ProbeHandler.hits = []
        _ProbeHandler.decisions_status = 400
        _ProbeHandler.models = ["acme/proposer-x", "acme/judge-x",
                                "acme/esc-x", "acme/draft-x",
                                "acme/embed-x"]

    def _stub_env(self) -> dict[str, str]:
        env = {}
        for seat, model in [("PROPOSER", "acme/proposer-x"),
                            ("JUDGE", "acme/judge-x"),
                            ("ESCALATION", "acme/esc-x"),
                            ("DRAFTING", "acme/draft-x"),
                            ("EMBEDDER", "acme/embed-x")]:
            env[f"ONTOSMITH_{seat}_BASE_URL"] = self.base
            env[f"ONTOSMITH_{seat}_MODEL"] = model
            env[f"ONTOSMITH_{seat}_KEY_ENV"] = "LLM_API_KEY"
            env[f"ONTOSMITH_{seat}_DIALECT"] = "openai"
        return env

    def _run(self, env, key_value="stub-secret-value"):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        src = tmp / "in.env"
        src.write_text(_env_file_text(env))
        out = tmp / "out.env"
        os_env = ({"LLM_API_KEY": key_value}
                  if key_value is not None else {})
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, os_env, clear=False):
            if key_value is None:
                os.environ.pop("LLM_API_KEY", None)
            with redirect_stdout(stdout), \
                    redirect_stderr(stderr):
                rc = configure.main(
                    ["--from-file", str(src), "--out", str(out)])
        return rc, out, stdout.getvalue(), stderr.getvalue()

    def test_probe_happy_path_validates(self):
        rc, out, stdout, _ = self._run(self._stub_env())
        self.assertEqual(rc, 0)
        self.assertIn("# Validation: validated", out.read_text())
        self.assertIn("binding stamp: custom", stdout)
        gets = [h for h in _ProbeHandler.hits if h[0] == "GET"]
        self.assertEqual(len(gets), 1)  # one shared base URL
        posts = [h for h in _ProbeHandler.hits
                 if h[0] == "POST"]
        self.assertEqual(len(posts), 2)  # judge + escalation
        # The key VALUE appears nowhere in file or output.
        self.assertNotIn("stub-secret-value", out.read_text())
        self.assertNotIn("stub-secret-value", stdout)

    def test_missing_key_fails_but_writes_not_validated(self):
        rc, out, _, stderr = self._run(self._stub_env(),
                                       key_value=None)
        self.assertEqual(rc, 2)
        self.assertIn("NOT VALIDATED", out.read_text())
        self.assertIn("LLM_API_KEY", stderr)

    def test_decisions_404_fails_the_probe(self):
        _ProbeHandler.decisions_status = 404
        rc, out, _, stderr = self._run(self._stub_env())
        self.assertEqual(rc, 2)
        self.assertIn("NOT VALIDATED", out.read_text())
        self.assertIn("decisions", stderr)

    def test_missing_model_is_a_warning_not_a_failure(self):
        _ProbeHandler.models = ["acme/proposer-x"]
        rc, out, stdout, _ = self._run(self._stub_env())
        self.assertEqual(rc, 0)
        self.assertIn("# Validation: validated", out.read_text())
        self.assertIn("warning:", stdout)
        self.assertIn("acme/judge-x", stdout)

    def test_no_probe_stamps_not_probed_and_never_calls(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        src = tmp / "in.env"
        src.write_text(_env_file_text(self._stub_env()))
        out = tmp / "out.env"
        with redirect_stdout(io.StringIO()), \
                redirect_stderr(io.StringIO()):
            rc = configure.main(
                ["--from-file", str(src), "--out", str(out),
                 "--no-probe"])
        self.assertEqual(rc, 0)
        self.assertIn("not probed", out.read_text())
        self.assertEqual(_ProbeHandler.hits, [])

    def test_cost_note_surfaces_for_costless_binding(self):
        rc, _out, stdout, _ = self._run(self._stub_env())
        self.assertEqual(rc, 0)
        self.assertIn("ONTOSMITH_MAX_COST_USD", stdout)


if __name__ == "__main__":
    unittest.main()
