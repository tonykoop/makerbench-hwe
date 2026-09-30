"""Playwright-free checks for the Studio demo recorder (#850)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("makerbench_record_studio_demo", ROOT / "scripts" / "record_studio_demo.py")
demo = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = demo
SPEC.loader.exec_module(demo)


def _run(root: Path, name: str) -> Path:
    run = root / name
    (run / "render" / "t0").mkdir(parents=True)
    (run / "gen").mkdir()
    (run / "run_log.json").write_text(json.dumps({"trials": []}), encoding="utf-8")
    (run / "run_log.json.lock").write_text("", encoding="utf-8")
    (run / "render" / "t0" / "preview.png").write_bytes(b"png")
    (run / "render" / "t0" / "output.stl").write_bytes(b"stl")
    (run / "render" / "t0" / "output.step").write_bytes(b"step")
    (run / "gen" / "entrant.scad").write_text("cube(1);", encoding="utf-8")
    return run


def test_stage_repo_copies_runs_without_source_geometry_or_locks(tmp_path):
    staged = demo.stage_repo([_run(tmp_path / "src", "run-a")], tmp_path / "work")
    runs = staged / "runs" / "code_cad_arena" / "run-a"
    assert (runs / "run_log.json").is_file()
    assert (runs / "render" / "t0" / "preview.png").is_file()
    for pattern in ("*.scad", "*.stl", "*.step", "*.lock"):
        assert not list(runs.rglob(pattern)), pattern
    assert (staged / "tasks" / "code_cad_arena" / "registry.json").is_file()


def test_stage_repo_rejects_a_directory_without_a_run_log(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError):
        demo.stage_repo([tmp_path / "empty"], tmp_path / "work")


def test_walkthrough_is_about_a_minute_and_avoids_preference_screens():
    assert 45 <= sum(demo.STEPS_HOLD.values()) <= 62
    source = (ROOT / "scripts" / "record_studio_demo.py").read_text(encoding="utf-8")
    for screen in ("#/vote", "#/analytics", "#/morning", "Blind voting", "Agreement analytics"):
        assert screen not in source.split('"""', 2)[2], screen
