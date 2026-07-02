"""Tests for the local Code-CAD Arena HTML report."""

from __future__ import annotations

import json
from pathlib import Path

from makerbench import code_cad_arena_report as report


def _run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "round-test"
    png_dir = run_dir / "render" / "ocarina__seed0__rep0__stub-a"
    png_dir.mkdir(parents=True)
    png = png_dir / "preview.png"
    png.write_bytes(b"\x89PNG\r\n")
    log = {
        "schema": "makerbench-code-cad-orchestration-v1",
        "config": {
            "instrument_ids": ["ocarina"],
            "model_ids": ["stub-a", "stub-<b>"],
            "seeds": [0],
            "reps": 1,
        },
        "summary": {"counts": {"scored": 1, "error": 1}},
        "trials": [
            {
                "trial_id": "ocarina__seed0__rep0__stub-a",
                "instrument_id": "ocarina", "model_id": "stub-a",
                "seed": 0, "rep": 0, "status": "scored",
                "result": {
                    "render_ok": True,
                    "artifacts": {"png_path": png.as_posix()},
                    "objective": {
                        "objective_pass_rate": 0.83,
                        "sub_scores": {
                            "renders": 1.0, "watertight": 1.0, "nonzero_volume": 1.0,
                            "fits_envelope": 1.0, "min_wall": 0.0, "body_count": 1.0,
                        },
                    },
                },
            },
            {
                "trial_id": "ocarina__seed0__rep0__stub-b",
                "instrument_id": "ocarina", "model_id": "stub-<b>",
                "seed": 0, "rep": 0, "status": "error",
                "result": None, "error": "CLI melted",
            },
        ],
    }
    (run_dir / "run_log.json").write_text(json.dumps(log), encoding="utf-8")
    return run_dir


class TestArenaReport:
    def test_report_contains_all_sections(self, tmp_path):
        html_text = report.build_report_html(_run_dir(tmp_path))
        for marker in (
            "Dual scoreline", "Objective pass-rate", "Gate sub-scores",
            "Candidates", "Single-voter Elo is directional",
            "1 instruments × 1 seeds × 1 reps × 2 models",
        ):
            assert marker in html_text

    def test_images_use_relative_paths(self, tmp_path):
        run_dir = _run_dir(tmp_path)
        html_text = report.build_report_html(run_dir)
        assert 'src="render/ocarina__seed0__rep0__stub-a/preview.png"' in html_text
        assert run_dir.as_posix() not in html_text.split("<main>", 1)[1]

    def test_failed_entrant_shown_honestly(self, tmp_path):
        html_text = report.build_report_html(_run_dir(tmp_path))
        assert "no render" in html_text
        assert "✗ error" in html_text

    def test_model_ids_are_escaped(self, tmp_path):
        html_text = report.build_report_html(_run_dir(tmp_path))
        assert "stub-<b>" not in html_text
        assert "stub-&lt;b&gt;" in html_text

    def test_gate_means_count_errors_as_zero(self, tmp_path):
        run_dir = _run_dir(tmp_path)
        log = json.loads((run_dir / "run_log.json").read_text(encoding="utf-8"))
        means = report.gate_means(log)
        assert means["stub-a"]["min_wall"] == 0.0
        assert means["stub-a"]["renders"] == 1.0
        assert means["stub-<b>"]["renders"] == 0.0

    def test_write_report_creates_file(self, tmp_path):
        out = report.write_report(_run_dir(tmp_path))
        assert out.name == "report.html"
        assert out.exists()
