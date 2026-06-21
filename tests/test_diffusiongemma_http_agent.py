"""Offline tests for agents/diffusiongemma_http_agent.py."""

from __future__ import annotations

import importlib
import io
import json
import os
import sys
import types
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import agents.diffusiongemma_http_agent as dg


class TestExtractScad(unittest.TestCase):
    def test_fenced_scad(self):
        text = "Some prose.\n```scad\ncube([10,10,10]);\n```\nDone."
        self.assertEqual(dg._extract_scad(text), "cube([10,10,10]);")

    def test_fenced_openscad(self):
        text = "```openscad\nsphere(r=5);\n```"
        self.assertEqual(dg._extract_scad(text), "sphere(r=5);")

    def test_generic_fence(self):
        text = "```\ncylinder(r=3, h=10);\n```"
        self.assertEqual(dg._extract_scad(text), "cylinder(r=3, h=10);")

    def test_no_fence_returns_stripped(self):
        text = "  translate([0,0,0]) cube([1,1,1]);  "
        self.assertEqual(dg._extract_scad(text), "translate([0,0,0]) cube([1,1,1]);")

    def test_empty_input(self):
        self.assertEqual(dg._extract_scad(""), "")

    def test_none_input(self):
        self.assertEqual(dg._extract_scad(None), "")


class TestExtractText(unittest.TestCase):
    def test_output_text_field(self):
        payload = {"output_text": "hello world"}
        self.assertEqual(dg._extract_text(payload), "hello world")

    def test_openai_choices(self):
        payload = {
            "choices": [{"message": {"content": "cube([5,5,5]);"}}]
        }
        self.assertEqual(dg._extract_text(payload), "cube([5,5,5]);")

    def test_multiple_choices(self):
        payload = {
            "choices": [
                {"message": {"content": "line1"}},
                {"message": {"content": "line2"}},
            ]
        }
        result = dg._extract_text(payload)
        self.assertIn("line1", result)
        self.assertIn("line2", result)

    def test_output_list_format(self):
        payload = {
            "output": [
                {"content": [{"type": "output_text", "text": "from output list"}]}
            ]
        }
        self.assertEqual(dg._extract_text(payload), "from output list")

    def test_empty_payload(self):
        self.assertEqual(dg._extract_text({}), "")


class TestUsageFromResponse(unittest.TestCase):
    def test_standard_usage(self):
        payload = {
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "model": "diffusion-gemma-26b-moe",
        }
        usage = dg._usage_from_response(payload)
        self.assertIsNotNone(usage)
        self.assertEqual(usage.input_tokens, 100)
        self.assertEqual(usage.output_tokens, 50)
        self.assertEqual(usage.total_tokens, 150)
        self.assertEqual(usage.provider, "unknown")
        self.assertEqual(usage.source, "measured")

    def test_alternate_field_names(self):
        payload = {"usage": {"input_tokens": 200, "output_tokens": 80}}
        usage = dg._usage_from_response(payload)
        self.assertIsNotNone(usage)
        self.assertEqual(usage.input_tokens, 200)
        self.assertEqual(usage.output_tokens, 80)

    def test_no_usage_field(self):
        self.assertIsNone(dg._usage_from_response({}))

    def test_usage_without_counts(self):
        payload = {"usage": {"tokens_per_second": 45.2}}
        self.assertIsNone(dg._usage_from_response(payload))

    def test_total_computed_when_both_known(self):
        payload = {"usage": {"prompt_tokens": 10, "completion_tokens": 20}}
        usage = dg._usage_from_response(payload)
        self.assertEqual(usage.total_tokens, 30)


class TestTokPerSec(unittest.TestCase):
    def test_tokens_per_second_key(self):
        payload = {"usage": {"tokens_per_second": 42.5}}
        self.assertAlmostEqual(dg._tok_per_sec(payload), 42.5)

    def test_tok_per_sec_key(self):
        payload = {"usage": {"tok_per_sec": 38.0}}
        self.assertAlmostEqual(dg._tok_per_sec(payload), 38.0)

    def test_throughput_key(self):
        payload = {"usage": {"throughput_tok_per_sec": 55.1}}
        self.assertAlmostEqual(dg._tok_per_sec(payload), 55.1)

    def test_no_tps_field(self):
        self.assertIsNone(dg._tok_per_sec({}))
        self.assertIsNone(dg._tok_per_sec({"usage": {}}))

    def test_zero_is_ignored(self):
        self.assertIsNone(dg._tok_per_sec({"usage": {"tokens_per_second": 0}}))


class TestTraceMetadata(unittest.TestCase):
    def test_required_keys(self):
        meta = dg._trace_metadata({})
        self.assertEqual(meta["provider"], "unknown")
        self.assertEqual(meta["generation_paradigm"], "block_diffusion")
        self.assertTrue(meta["bidirectional"])
        self.assertIn("canvas_tokens", meta)
        self.assertIn("denoising_passes", meta)
        self.assertEqual(meta["gateway"], "diffusiongemma_local")

    def test_hw_description_included(self):
        with patch.dict(os.environ, {"LOCAL_OPENAI_HW_DESCRIPTION": "2× A100"}):
            importlib.reload(dg)
            meta = dg._trace_metadata({})
            self.assertEqual(meta.get("hw_description"), "2× A100")
        importlib.reload(dg)

    def test_tps_included_when_provided(self):
        meta = dg._trace_metadata({}, tps=88.5)
        self.assertAlmostEqual(meta["tok_per_sec"], 88.5)

    def test_tps_absent_when_none(self):
        meta = dg._trace_metadata({}, tps=None)
        self.assertNotIn("tok_per_sec", meta)


class TestHttpErrorSurfaced(unittest.TestCase):
    def test_http_error_raises_runtime_error(self):
        import urllib.error

        def bad_open(*a, **kw):
            raise urllib.error.HTTPError(
                url="", code=503, msg="Service Unavailable",
                hdrs={}, fp=io.BytesIO(b"temporarily down"),
            )

        with patch("urllib.request.urlopen", bad_open):
            with self.assertRaises(RuntimeError) as ctx:
                dg._call_diffusiongemma("test prompt")
        self.assertIn("503", str(ctx.exception))

    def test_url_error_raises_runtime_error(self):
        import urllib.error

        def bad_open(*a, **kw):
            raise urllib.error.URLError("connection refused")

        with patch("urllib.request.urlopen", bad_open):
            with self.assertRaises(RuntimeError) as ctx:
                dg._call_diffusiongemma("test prompt")
        self.assertIn("request failed", str(ctx.exception).lower())

    def test_empty_text_raises(self):
        payload = {"choices": [{"message": {"content": ""}}]}
        response = MagicMock()
        response.read.return_value = json.dumps(payload).encode()
        response.__enter__ = lambda s: s
        response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaises(RuntimeError):
                dg._call_diffusiongemma("test prompt")


class TestCallDiffusiongemmaHTTPStructure(unittest.TestCase):
    def _fake_response(self, payload: dict):
        raw = json.dumps(payload).encode()
        response = MagicMock()
        response.read.return_value = raw
        response.__enter__ = lambda s: s
        response.__exit__ = MagicMock(return_value=False)
        return response

    def test_sends_post_with_model(self):
        payload = {
            "choices": [{"message": {"content": "cube([1,1,1]);"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        captured_requests = []

        def capture_open(req, **kw):
            captured_requests.append(req)
            return self._fake_response(payload)

        with patch("urllib.request.urlopen", capture_open):
            text, raw = dg._call_diffusiongemma("make a cube")

        self.assertEqual(len(captured_requests), 1)
        body = json.loads(captured_requests[0].data)
        self.assertEqual(body["model"], dg.MODEL)
        messages = body["messages"]
        self.assertTrue(any(m["role"] == "system" for m in messages))
        self.assertTrue(any(m["role"] == "user" for m in messages))
        user_msg = next(m for m in messages if m["role"] == "user")
        self.assertIn("make a cube", user_msg["content"])

    def test_no_auth_header_when_key_empty(self):
        payload = {"choices": [{"message": {"content": "sphere(r=1);"}}]}
        captured = []

        def capture_open(req, **kw):
            captured.append(req)
            return self._fake_response(payload)

        with patch.dict(os.environ, {"DIFFUSIONGEMMA_API_KEY": ""}):
            importlib.reload(dg)
            with patch("urllib.request.urlopen", capture_open):
                dg._call_diffusiongemma("test")

        req = captured[0]
        self.assertNotIn("Authorization", req.headers)
        importlib.reload(dg)

    def test_auth_header_sent_when_key_set(self):
        payload = {"choices": [{"message": {"content": "sphere(r=2);"}}]}
        captured = []

        def capture_open(req, **kw):
            captured.append(req)
            return self._fake_response(payload)

        with patch.dict(os.environ, {"DIFFUSIONGEMMA_API_KEY": "test-key-123"}):
            importlib.reload(dg)
            with patch("urllib.request.urlopen", capture_open):
                dg._call_diffusiongemma("test")

        req = captured[0]
        auth = req.get_header("Authorization")
        self.assertIsNotNone(auth)
        self.assertIn("test-key-123", auth)
        importlib.reload(dg)


class TestSumUsage(unittest.TestCase):
    def _make_usage(self, inp, out, total=None):
        from makerbench.schema import UsageReport
        return UsageReport(
            source="measured", provider="unknown", model="diffusion-gemma-26b-moe",
            input_tokens=inp, output_tokens=out, total_tokens=total,
            cached_input_tokens=None, reasoning_tokens=None,
        )

    def test_sum_two(self):
        a = self._make_usage(100, 50, 150)
        b = self._make_usage(200, 80, 280)
        result = dg._sum_usage([a, b])
        self.assertEqual(result.input_tokens, 300)
        self.assertEqual(result.output_tokens, 130)
        self.assertEqual(result.total_tokens, 430)

    def test_empty_returns_none(self):
        self.assertIsNone(dg._sum_usage([]))

    def test_partial_none(self):
        from makerbench.schema import UsageReport
        a = UsageReport(source="measured", provider="unknown", model="m",
                        input_tokens=None, output_tokens=None, total_tokens=None,
                        cached_input_tokens=None, reasoning_tokens=None)
        b = self._make_usage(50, 20, 70)
        result = dg._sum_usage([a, b])
        self.assertEqual(result.input_tokens, 50)


class TestAgentBlindTrack(unittest.TestCase):
    def _make_spec(self):
        from makerbench.schema import TaskSpec
        return TaskSpec(
            task_id="vented_plate", seed=0,
            params={}, brief="Design a vented plate.",
        )

    def _fake_call(self, text):
        payload = {
            "id": "fake-id",
            "model": "diffusion-gemma-26b-moe",
            "choices": [{"message": {"content": f"```scad\n{text}\n```"}}],
            "usage": {"prompt_tokens": 80, "completion_tokens": 40,
                      "tokens_per_second": 12.5},
        }
        return text, payload

    def test_blind_track_returns_attempt(self):
        spec = self._make_spec()
        with patch.object(dg, "_call_diffusiongemma",
                          side_effect=lambda p: self._fake_call("cube([1,1,1]);")):
            attempt = dg.agent(spec, track="blind", tools={})
        self.assertEqual(attempt.task_id, "vented_plate")
        self.assertEqual(attempt.track, "blind")
        self.assertIn("cube", attempt.source)
        self.assertEqual(attempt.iterations, 1)
        self.assertIsNone(attempt.cost)

    def test_blind_track_no_perception_calls(self):
        spec = self._make_spec()
        call_count = [0]

        def fake_call(p):
            call_count[0] += 1
            return self._fake_call("cube([2,2,2]);")

        perceive_calls = [0]
        def fake_perceive(src):
            perceive_calls[0] += 1
            return {"compiled": True, "bbox_mm": [], "warnings": []}

        with patch.object(dg, "_call_diffusiongemma", side_effect=fake_call):
            dg.agent(spec, track="blind", tools={}, perceive=fake_perceive, budget=3)

        self.assertEqual(call_count[0], 1)
        self.assertEqual(perceive_calls[0], 0)

    def test_usage_included_in_attempt(self):
        spec = self._make_spec()
        with patch.object(dg, "_call_diffusiongemma",
                          side_effect=lambda p: self._fake_call("sphere(r=5);")):
            attempt = dg.agent(spec, track="blind", tools={})
        self.assertIsNotNone(attempt.usage)
        self.assertEqual(attempt.usage.input_tokens, 80)
        self.assertEqual(attempt.usage.output_tokens, 40)

    def test_perception_track_loops(self):
        spec = self._make_spec()
        call_count = [0]
        responses = [
            "```scad\ncube([1,1,1]);\n```",
            "LOOKS_GOOD",
        ]

        def fake_call(p):
            idx = min(call_count[0], len(responses) - 1)
            call_count[0] += 1
            payload = {
                "id": f"r{call_count[0]}",
                "model": "diffusion-gemma-26b-moe",
                "choices": [{"message": {"content": responses[idx]}}],
                "usage": {},
            }
            return responses[idx], payload

        def fake_perceive(src):
            return {"compiled": True, "bbox_mm": [100, 50, 5], "warnings": []}

        with patch.object(dg, "_call_diffusiongemma", side_effect=fake_call):
            attempt = dg.agent(spec, track="perception", tools={},
                               perceive=fake_perceive, budget=3)
        self.assertEqual(attempt.track, "perception")
        self.assertGreaterEqual(attempt.iterations, 1)

    def test_trace_contains_generation_paradigm(self):
        spec = self._make_spec()
        with patch.object(dg, "_call_diffusiongemma",
                          side_effect=lambda p: self._fake_call("cube([3,3,3]);")):
            attempt = dg.agent(spec, track="blind", tools={})
        self.assertTrue(any(
            step.get("generation_paradigm") == "block_diffusion"
            for step in attempt.trace
        ))


class TestLoadAgentFn(unittest.TestCase):
    def test_loads_diffusiongemma_http_adapter(self):
        import importlib.util as ilu
        adapter_path = Path(__file__).parent.parent / "agents" / "diffusiongemma_http_agent.py"
        spec = ilu.spec_from_file_location("probe_agent", adapter_path)
        mod = ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertTrue(hasattr(mod, "_call_diffusiongemma"))


if __name__ == "__main__":
    unittest.main()
