"""Provider generalization — P1a/P1b/P1c tests.

The preset's byte identity is guarded by
tests/test_connections.py's golden; this module guards the
NEW behavior on custom bindings:

- dialect resolution and payload gating (provider.sort,
  usage.include, the reasoning field are OpenRouter-dialect
  only);
- cost honesty: one normalization point, cost float | None
  with a stamped basis, user-declared rates producing
  `estimated`, and the USD stop-line refusing by name on a
  cost-less binding instead of silently never tripping;
- capability declarations replacing name-substring
  heuristics, with the derivation kept as the default;
- the embedder's new name (SeatEmbedder) with the old name
  kept as an alias.
"""

from __future__ import annotations

import argparse
import json
import unittest
from unittest import mock

import requests

from pipeline import (
    batch,
    batch_cli,
    batch_run,
    connections,
    decisions,
    proposer,
    query_stack,
)
from pipeline import (
    embedder as embedder_mod,
)
from pipeline.connections import BindingCostError
from tests.test_connections import CAPTURE_KEY, binding_env


class _Resp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


def _chat_body(content="ok", usage=None):
    return {"choices": [{"message": {"content": content}}],
            "usage": usage if usage is not None else
            {"prompt_tokens": 10, "completion_tokens": 5}}


# A user's own OpenAI-compatible gateway: every seat rebound
# to it, keys under the user's own variable name (declared via
# ONTOSMITH_API_KEY_ENV, the product's convention).
CUSTOM_ENV = {
    "ONTOSMITH_BASE_URL": "http://127.0.0.1:9/v1",
    "ONTOSMITH_API_KEY_ENV": "LLM_API_KEY",
    "LLM_API_KEY": "stub-key",
    "OPENROUTER_API_KEY": CAPTURE_KEY,
}


# ----------------------------------------------------------------------
# Dialect resolution
# ----------------------------------------------------------------------

class TestDialectResolution(unittest.TestCase):
    def test_preset_seats_are_openrouter(self):
        with binding_env({"OPENROUTER_API_KEY": CAPTURE_KEY}):
            b = connections.resolve_bindings()
        for seat in connections.SEATS:
            self.assertEqual(b.seat(seat).dialect, "openrouter",
                             seat)

    def test_rebound_route_defaults_to_portable(self):
        with binding_env(CUSTOM_ENV):
            b = connections.resolve_bindings()
        for seat in connections.SEATS:
            self.assertEqual(b.seat(seat).dialect, "openai", seat)

    def test_declared_dialect_wins_over_default(self):
        env = dict(CUSTOM_ENV, ONTOSMITH_DIALECT="openrouter")
        with binding_env(env):
            b = connections.resolve_bindings()
        self.assertEqual(b.seat("proposer").dialect, "openrouter")

    def test_per_seat_dialect_overrides_global(self):
        env = dict(CUSTOM_ENV, ONTOSMITH_DIALECT="openrouter",
                   ONTOSMITH_JUDGE_DIALECT="openai")
        with binding_env(env):
            b = connections.resolve_bindings()
        self.assertEqual(b.seat("judge").dialect, "openai")
        self.assertEqual(b.seat("proposer").dialect, "openrouter")

    def test_unknown_dialect_is_a_named_error(self):
        env = dict(CUSTOM_ENV, ONTOSMITH_DIALECT="carrier-pigeon")
        with binding_env(env):
            with self.assertRaises(ValueError) as cm:
                connections.resolve_bindings()
        self.assertIn("carrier-pigeon", str(cm.exception))

    def test_model_only_rebind_keeps_preset_dialect(self):
        env = {"OPENROUTER_API_KEY": CAPTURE_KEY,
               "ONTOSMITH_PROPOSER_MODEL": "other/model-x"}
        with binding_env(env):
            b = connections.resolve_bindings()
        self.assertEqual(b.seat("proposer").dialect, "openrouter")


# ----------------------------------------------------------------------
# Payload gating (P1a)
# ----------------------------------------------------------------------

class TestPayloadGating(unittest.TestCase):
    def _proposer_payload(self, env, model="acme/plain-model"):
        seen = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            seen.update(json)
            return _Resp(_chat_body())

        with binding_env(env):
            with mock.patch.object(requests, "post",
                                   fake_post):
                proposer._chat("prompt", model, 64, "stub-key")
        return seen

    def test_custom_binding_sends_no_provider_block(self):
        seen = self._proposer_payload(CUSTOM_ENV)
        self.assertNotIn("provider", seen)
        self.assertEqual(seen["temperature"], 0)

    def test_preset_binding_sends_provider_sort(self):
        seen = self._proposer_payload(
            {"OPENROUTER_API_KEY": CAPTURE_KEY},
            model="anthropic/claude-haiku-5.5")
        self.assertEqual(seen["provider"], {"sort": "throughput"})

    def test_text_judge_reasoning_fields_are_dialect_only(self):
        def judge_payload(env):
            seen = {}

            def fake_post(url, headers=None, json=None,
                          timeout=None):
                seen.update(json)
                return _Resp({
                    "choices": [{"message": {
                        "content": '{"answer": "Yes"}'}}],
                    "usage": {"prompt_tokens": 3,
                              "completion_tokens": 1}})

            with binding_env(env):
                with mock.patch.object(decisions.requests, "post",
                                       fake_post):
                    decisions.llm_text_judge(
                        "doc", "S", "rel", "O",
                        model="acme/gpt-5-mini")
            return seen

        custom = judge_payload(CUSTOM_ENV)
        self.assertIn("max_completion_tokens", custom)
        self.assertNotIn("reasoning", custom)
        self.assertNotIn("provider", custom)

        preset = judge_payload({"OPENROUTER_API_KEY": CAPTURE_KEY})
        self.assertEqual(preset["reasoning"],
                         {"effort": "minimal"})
        self.assertEqual(preset["provider"],
                         {"sort": "throughput"})


# ----------------------------------------------------------------------
# Capabilities (P1c)
# ----------------------------------------------------------------------

class TestCapabilities(unittest.TestCase):
    def test_derivation_table(self):
        d = connections.derive_capabilities
        self.assertIn(connections.CAP_REASONING_TOKENS,
                      d("acme/gpt-5-mini"))
        self.assertIn(connections.CAP_REASONING_TOKENS,
                      d("acme/o3-deep"))
        self.assertIn(connections.CAP_REASONING_TOKENS,
                      d("acme/deepseek-r1"))
        self.assertIn(connections.CAP_NO_TEMPERATURE,
                      d("anthropic/claude-haiku-5.5"))
        self.assertEqual(d("acme/plain-model"), frozenset())

    def test_declared_override_adds_and_removes(self):
        env = dict(CUSTOM_ENV)
        env["ONTOSMITH_PROPOSER_CAPABILITIES"] = (
            "+reasoning-tokens,-no-temperature")
        env["ONTOSMITH_PROPOSER_MODEL"] = "anthropic/claude-haiku-5.5"
        with binding_env(env):
            seat = connections.seat("proposer")
        self.assertTrue(seat.has_capability(
            connections.CAP_REASONING_TOKENS))
        self.assertFalse(seat.has_capability(
            connections.CAP_NO_TEMPERATURE))

    def test_unknown_capability_is_a_named_error(self):
        env = dict(CUSTOM_ENV)
        env["ONTOSMITH_PROPOSER_CAPABILITIES"] = "+telepathy"
        with binding_env(env):
            with self.assertRaises(ValueError) as cm:
                connections.resolve_bindings()
        self.assertIn("telepathy", str(cm.exception))

    def test_declared_no_temperature_omits_the_field(self):
        seen = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            seen.update(json)
            return _Resp(_chat_body())

        env = dict(CUSTOM_ENV)
        env["ONTOSMITH_PROPOSER_MODEL"] = "acme/plain-model"
        env["ONTOSMITH_PROPOSER_CAPABILITIES"] = "no-temperature"
        with binding_env(env):
            with mock.patch.object(requests, "post",
                                   fake_post):
                proposer._chat("prompt", "acme/plain-model",
                               64, "stub-key")
        self.assertNotIn("temperature", seen)


# ----------------------------------------------------------------------
# Cost normalization (P1b)
# ----------------------------------------------------------------------

class TestCostNormalization(unittest.TestCase):
    def _seat(self, env, name="proposer"):
        with binding_env(env):
            return connections.seat(name)

    def test_provider_reported_cost_passes_through(self):
        seat = self._seat({"OPENROUTER_API_KEY": CAPTURE_KEY})
        n = connections.normalize_usage(
            {"prompt_tokens": 100, "completion_tokens": 50,
             "cost": 0.0123}, seat)
        self.assertEqual(n["cost"], 0.0123)
        self.assertEqual(n["cost_basis"], "provider-reported")
        self.assertEqual(n["input_tokens"], 100)

    def test_missing_cost_is_none_not_zero(self):
        seat = self._seat(CUSTOM_ENV)
        n = connections.normalize_usage(
            {"prompt_tokens": 100, "completion_tokens": 50}, seat)
        self.assertIsNone(n["cost"])
        self.assertEqual(n["cost_basis"], "not-provided")
        self.assertEqual(n["input_tokens"], 100)
        self.assertEqual(n["output_tokens"], 50)

    def test_declared_rates_produce_an_estimate(self):
        env = dict(CUSTOM_ENV)
        env["ONTOSMITH_PROPOSER_PRICE_IN_PER_MTOK"] = "2.0"
        env["ONTOSMITH_PROPOSER_PRICE_OUT_PER_MTOK"] = "10.0"
        seat = self._seat(env)
        n = connections.normalize_usage(
            {"prompt_tokens": 1_000_000,
             "completion_tokens": 100_000}, seat)
        self.assertAlmostEqual(n["cost"], 3.0)
        self.assertEqual(n["cost_basis"], "estimated")
        self.assertEqual(seat.cost_basis, "estimated")

    def test_half_declared_rates_are_a_named_error(self):
        env = dict(CUSTOM_ENV)
        env["ONTOSMITH_PROPOSER_PRICE_IN_PER_MTOK"] = "2.0"
        with binding_env(env):
            with self.assertRaises(ValueError) as cm:
                connections.resolve_bindings()
        self.assertIn("PRICE_OUT", str(cm.exception))

    def test_provider_cost_wins_over_declared_rates(self):
        env = dict(CUSTOM_ENV,
                   ONTOSMITH_PROPOSER_PRICE_IN_PER_MTOK="2.0",
                   ONTOSMITH_PROPOSER_PRICE_OUT_PER_MTOK="10.0",
                   ONTOSMITH_PROPOSER_DIALECT="openrouter")
        seat = self._seat(env)
        n = connections.normalize_usage(
            {"prompt_tokens": 5, "completion_tokens": 5,
             "cost": 0.5}, seat)
        self.assertEqual(n["cost"], 0.5)
        self.assertEqual(n["cost_basis"], "provider-reported")

    def test_add_cost_none_propagation(self):
        self.assertEqual(connections.add_cost(1.5, 2.5), 4.0)
        self.assertIsNone(connections.add_cost(1.5, None))
        self.assertIsNone(connections.add_cost(None, 2.5))
        self.assertEqual(connections.add_cost(None, None), None)

    def test_chat_cost_never_invents_a_price(self):
        # The deleted fallback priced a missing cost from
        # hardcoded list rates; unknown must stay unknown.
        self.assertIsNone(query_stack._chat_cost(
            {"prompt_tokens": 1_000_000,
             "completion_tokens": 1_000_000}))
        self.assertEqual(query_stack._chat_cost({"cost": 0.25}),
                         0.25)


class TestStopLineRefusal(unittest.TestCase):
    def test_require_metered_names_the_seats(self):
        with binding_env(CUSTOM_ENV):
            b = connections.resolve_bindings()
        with self.assertRaises(BindingCostError) as cm:
            connections.require_metered(b, 10.0, ["proposer"])
        msg = str(cm.exception)
        self.assertIn("proposer", msg)
        self.assertIn("not-provided", msg)
        self.assertIn("--no-cost-cap", msg)

    def test_require_metered_passes_when_metered_or_uncapped(self):
        with binding_env({"OPENROUTER_API_KEY": CAPTURE_KEY}):
            b = connections.resolve_bindings()
        connections.require_metered(b, 10.0, ["proposer"])
        with binding_env(CUSTOM_ENV):
            b = connections.resolve_bindings()
        connections.require_metered(b, None, ["proposer"])

    def test_estimated_seats_satisfy_the_cap(self):
        env = dict(CUSTOM_ENV)
        for seat in ("PROPOSER", "JUDGE", "EMBEDDER"):
            env[f"ONTOSMITH_{seat}_PRICE_IN_PER_MTOK"] = "1.0"
            env[f"ONTOSMITH_{seat}_PRICE_OUT_PER_MTOK"] = "1.0"
        with binding_env(env):
            b = connections.resolve_bindings()
        connections.require_metered(
            b, 10.0, ["proposer", "judge", "embedder"])

    def test_batch_create_refuses_costless_cap_by_name(self):
        import tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp())
        doc = tmp / "doc1.txt"
        doc.write_text("Alice met Bob in Paris.")
        args = argparse.Namespace(
            docs=[str(doc)], collection="stubcoll",
            batch_id="cap-batch", source="test",
            profile=None, pack=None)
        with binding_env(CUSTOM_ENV):
            with mock.patch.object(batch_cli, "ROOT", tmp), \
                    mock.patch.object(batch_cli, "state_root",
                                      return_value=tmp):
                # An internal cap (env home) on a cost-less
                # binding refuses by name; uncapped, the
                # same create proceeds.
                with mock.patch.dict(
                        "os.environ",
                        {"ONTOSMITH_MAX_COST_USD": "10"}):
                    with self.assertRaises(BindingCostError):
                        batch_cli.cmd_create(args)
                batch_cli.cmd_create(args)
        b = batch.load_batch(tmp, "cap-batch")
        self.assertIsNone(b.config.max_cost_usd)

    def test_ledger_records_unmetered_as_none(self):
        cfg = batch.BatchConfig(max_cost_usd=None)
        b = batch.Batch(batch_id="led", run_id="r1", source="t",
                        collection="c", doc_ids=[], config=cfg)
        with binding_env(CUSTOM_ENV):
            batch_run._record_cost(b, "GATES", None)
        ledger = b.metrics["cost_ledger"]
        self.assertIsNone(ledger["GATES"])
        self.assertEqual(ledger["cost_basis"], "not-provided")
        self.assertIsNone(ledger["cap_usd"])

    def test_ledger_refuses_unmetered_under_a_cap(self):
        cfg = batch.BatchConfig(max_cost_usd=5.0)
        b = batch.Batch(batch_id="led2", run_id="r1", source="t",
                        collection="c", doc_ids=[], config=cfg)
        with self.assertRaises(BindingCostError):
            batch_run._record_cost(b, "GATES", None)
        # ...while a metered stage records and enforces as before
        batch_run._record_cost(b, "GATES", 1.25)
        self.assertEqual(
            b.metrics["cost_ledger"]["total_usd"], 1.25)
        with self.assertRaises(RuntimeError):
            batch_run._record_cost(b, "WRITE", 99.0)


# ----------------------------------------------------------------------
# Embedder rename (P1c)
# ----------------------------------------------------------------------

class TestEmbedderRename(unittest.TestCase):
    def test_new_name_and_backward_compatible_alias(self):
        self.assertIs(embedder_mod.SeatEmbedder,
                      embedder_mod.OpenRouterEmbedder)

    def test_unmetered_embedder_accumulates_none(self):
        import urllib.request

        class _UResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({
                    "data": [{"index": 0,
                              "embedding": [1.0, 0.0]}],
                    "usage": {"prompt_tokens": 4}}).encode()

        with binding_env(CUSTOM_ENV):
            emb = embedder_mod.SeatEmbedder("acme/embed-x")
            with mock.patch.object(urllib.request, "urlopen",
                                   lambda req, timeout=None:
                                       _UResp()):
                emb.embed(["hello"])
        self.assertIsNone(emb.usage["cost"])
        self.assertEqual(emb.usage["cost_basis"], "not-provided")
        self.assertEqual(emb.usage["input_tokens"], 4)


if __name__ == "__main__":
    unittest.main()
