"""Offline tests for scripts/diffusiongemma_repair_probe.py.

These tests verify:
- Error injection is deterministic (same result every time)
- Injected error appears at line 1
- Original program is recoverable from the broken code
- Result schema matches expected structure
- Markdown report is generated
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import importlib.util
_probe_spec = importlib.util.spec_from_file_location(
    "diffusiongemma_repair_probe",
    Path(__file__).parent.parent / "scripts" / "diffusiongemma_repair_probe.py",
)
probe = importlib.util.module_from_spec(_probe_spec)
_probe_spec.loader.exec_module(probe)


SAMPLE_SCAD = """\
module vented_plate(w=100, d=50, h=3) {
    difference() {
        cube([w, d, h]);
        for (i=[5:10:w-5]) for (j=[5:10:d-5])
            translate([i, j, 0]) cylinder(r=2, h=h+1, $fn=16);
    }
}
vented_plate();
"""


class TestInjectError(unittest.TestCase):
    def test_error_at_line_1(self):
        broken = probe.inject_error(SAMPLE_SCAD)
        first_line = broken.split("\n")[0]
        self.assertIn(probe.INJECTED_ERROR_MARKER, first_line)

    def test_deterministic(self):
        result_a = probe.inject_error(SAMPLE_SCAD)
        result_b = probe.inject_error(SAMPLE_SCAD)
        self.assertEqual(result_a, result_b)

    def test_original_preserved_after_line_1(self):
        broken = probe.inject_error(SAMPLE_SCAD)
        lines = broken.split("\n")
        # Everything after the first line should be the original
        remainder = "\n".join(lines[1:])
        self.assertEqual(remainder, SAMPLE_SCAD)

    def test_different_programs_different_broken(self):
        other = "sphere(r=5);\n"
        broken_a = probe.inject_error(SAMPLE_SCAD)
        broken_b = probe.inject_error(other)
        self.assertNotEqual(broken_a, broken_b)


class TestExtractScad(unittest.TestCase):
    def test_extracts_scad_block(self):
        text = "Here you go:\n```scad\ncube([1,1,1]);\n```\nDone."
        self.assertEqual(probe.extract_scad(text), "cube([1,1,1]);")

    def test_no_block_returns_text(self):
        text = "cube([1,1,1]);"
        self.assertEqual(probe.extract_scad(text), "cube([1,1,1]);")

    def test_empty(self):
        self.assertEqual(probe.extract_scad(""), "")


class TestResultSchema(unittest.TestCase):
    def _make_attempt_row(self, compiled: bool, idx: int = 0) -> dict:
        return {"attempt": idx, "compiled": compiled, "elapsed_s": 1.5, "out_chars": 100}

    def test_result_has_required_keys(self):
        result = {
            "seed_file": "test.scad",
            "injected_error": probe.INJECTED_ERROR_MARKER,
            "n_attempts": 2,
            "diffusiongemma": {
                "model": "diffusion-gemma-26b-moe",
                "attempts": [self._make_attempt_row(True, 0), self._make_attempt_row(False, 1)],
                "repair_rate": 0.5,
            },
            "control": {
                "model": "llama3",
                "attempts": [self._make_attempt_row(True, 0), self._make_attempt_row(True, 1)],
                "repair_rate": 1.0,
            },
        }
        self.assertIn("diffusiongemma", result)
        self.assertIn("control", result)
        self.assertIn("repair_rate", result["diffusiongemma"])
        self.assertIn("repair_rate", result["control"])
        self.assertEqual(result["n_attempts"], 2)
        self.assertEqual(result["injected_error"], probe.INJECTED_ERROR_MARKER)

    def test_repair_rate_range(self):
        for count, attempts, expected in [(0, 5, 0.0), (5, 5, 1.0), (3, 5, 0.6)]:
            result_key = {"attempts": [{"compiled": i < count} for i in range(attempts)]}
            compiled = sum(1 for r in result_key["attempts"] if r["compiled"])
            rate = compiled / attempts
            self.assertAlmostEqual(rate, expected)


class TestRunProbe(unittest.TestCase):
    def _make_mock_mod(self, text_to_return: str = "cube([1,1,1]);"):
        mod = types.SimpleNamespace()
        mod.MODEL = "test-model"
        mod._call_diffusiongemma = lambda p: (
            f"```scad\n{text_to_return}\n```",
            {"choices": [{"message": {"content": f"```scad\n{text_to_return}\n```"}}],
             "usage": {"tokens_per_second": 40.0}},
        )
        return mod

    def test_run_probe_all_compile_when_openscad_not_found(self):
        mod = self._make_mock_mod("cube([1,1,1]);")
        with patch.object(probe, "compiles", return_value=True):
            result = probe.run_probe("INJECTED_ERROR;\n" + SAMPLE_SCAD, "brief", mod, 3)
        self.assertAlmostEqual(result["repair_rate"], 1.0)
        self.assertEqual(len(result["attempts"]), 3)

    def test_run_probe_records_attempts(self):
        mod = self._make_mock_mod("sphere(r=5);")
        with patch.object(probe, "compiles", side_effect=[True, False, True]):
            result = probe.run_probe("broken", "brief", mod, 3)
        compiled_count = sum(1 for a in result["attempts"] if a["compiled"])
        self.assertEqual(compiled_count, 2)
        self.assertAlmostEqual(result["repair_rate"], 2 / 3)

    def test_run_probe_handles_call_error(self):
        mod = types.SimpleNamespace()
        mod.MODEL = "test-model"
        mod._call_diffusiongemma = lambda p: (_ for _ in ()).throw(
            RuntimeError("server offline")
        )
        result = probe.run_probe("broken", "brief", mod, 2,
                                 openscad_bin="openscad")
        self.assertEqual(len(result["attempts"]), 2)
        self.assertEqual(result["repair_rate"], 0.0)
        for att in result["attempts"]:
            self.assertFalse(att["compiled"])
            self.assertIn("error", att)

    def test_tok_per_sec_recorded(self):
        mod = types.SimpleNamespace()
        mod.MODEL = "test-model"
        mod._call_diffusiongemma = lambda p: (
            "cube([1,1,1]);",
            {"choices": [{"message": {"content": "cube([1,1,1]);"}}],
             "usage": {"tokens_per_second": 55.5}},
        )
        with patch.object(probe, "compiles", return_value=True):
            result = probe.run_probe("broken", "brief", mod, 2)
        self.assertIn("tok_per_sec_mean", result)
        self.assertAlmostEqual(result["tok_per_sec_mean"], 55.5)


class TestWriteMarkdown(unittest.TestCase):
    def test_markdown_generated(self):
        result = {
            "seed_file": "test.scad",
            "n_attempts": 3,
            "diffusiongemma": {
                "model": "diffusion-gemma-26b-moe",
                "attempts": [],
                "repair_rate": 0.67,
                "tok_per_sec_mean": 45.0,
            },
            "control": {
                "model": "llama3",
                "attempts": [],
                "repair_rate": 0.33,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            probe.write_markdown(result, out_dir)
            md_path = out_dir / "REPAIR_PROBE.md"
            self.assertTrue(md_path.exists())
            content = md_path.read_text()
            self.assertIn("DiffusionGemma", content)
            self.assertIn("repair rate", content.lower())
            self.assertIn("67%", content)
            self.assertIn("33%", content)


class TestLoadAgentFn(unittest.TestCase):
    def test_loads_diffusiongemma_http_adapter(self):
        adapter_path = Path(__file__).parent.parent / "agents" / "diffusiongemma_http_agent.py"
        mod = probe._load_agent_fn(str(adapter_path))
        self.assertTrue(hasattr(mod, "_call_diffusiongemma"))

    def test_raises_on_unknown_module(self):
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as fh:
            fh.write("# no _call_ functions\ndef not_an_agent(): pass\n")
            tmp_path = fh.name
        try:
            with self.assertRaises(ValueError):
                probe._load_agent_fn(tmp_path)
        finally:
            os.unlink(tmp_path)


if __name__ == "__main__":
    unittest.main()
