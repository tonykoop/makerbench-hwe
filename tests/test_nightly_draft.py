"""Drafting a matchup job through the New Run code path, never into the live queue (#663)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

from makerbench import nightly_cad
from makerbench.cli import app
from makerbench.nightly_draft import DraftError, draft_matchup

ENTRANTS = ["cadam:opus", "freecad:opus", "solidworks:sol", "fusion:sonnet"]
BRIEF = "Model a multi-part sambuca assembly as ONE self-contained OpenSCAD program."


def _setup(tmp_path: Path) -> dict:
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"instruments": [
        {"id": "sambuca", "display_name": "Sambuca", "family": "strings", "task_brief": BRIEF,
         "envelope_mm": [900, 300, 900], "assembly": True, "min_bodies": 4, "repo_path": "strings/sambuca"}]}))
    images = tmp_path / "instruments" / "strings" / "sambuca" / "images"
    images.mkdir(parents=True)
    Image.new("RGB", (64, 48), (120, 80, 40)).save(images / "Museum.jpg", format="JPEG")
    runs = tmp_path / "runs"
    runs.mkdir()
    live = runs / "nightly-cad-queue.json"
    live.write_text('{"schema": "makerbench-nightly-cad-queue-v1", "jobs": []}')
    return {"registry_path": registry, "runs_root": runs, "instruments_root": tmp_path / "instruments", "live": live,
            "photo": images / "Museum.jpg"}


def test_the_sambuca_matchup_drafts_like_add_to_tonight(tmp_path):
    env = _setup(tmp_path)
    before = env["live"].read_bytes()
    out = tmp_path / "draft" / "sambuca-draft-queue.json"
    drafted = draft_matchup(instrument="sambuca", entrants=ENTRANTS, registry_path=env["registry_path"],
                            runs_root=env["runs_root"], out_path=out, reference="Museum.jpg",
                            instruments_root=env["instruments_root"], cap_usd=2.0,
                            now=datetime(2026, 10, 18, 6, 0, tzinfo=timezone.utc))
    assert env["live"].read_bytes() == before  # the live queue is never written
    payload = json.loads(out.read_text())
    assert payload["schema"] == nightly_cad.SCHEMA and payload["draft"] is True and len(payload["jobs"]) == 1
    _queue, jobs = nightly_cad.load_queue(out)
    job = jobs[0]
    job.validate()
    assert job.deliverable == "single" and job.budget_usd == 2.0
    assert job.turns == 3 and job.stop_early is True
    assert Path(job.reference_image).read_bytes() == env["photo"].read_bytes()  # copied into the plan folder
    assert [(e.entrant_id, e.kind, e.model_id, e.backend, e.context_tier, e.max_cost_usd) for e in job.entrants] == [
        ("cadam-opus", "cadam", "anthropic/claude-opus-5.5", "openscad", "image", 2.0),
        ("freecad-opus", "arena", "claude-code-opus-5.5", "freecad", "image", 0.0),
        ("solidworks-sol", "live", "codex-gpt-6.1-sol", "solidworks-live", "image", 0.0),
        ("fusion-sonnet", "live", "claude-code-sonnet-5.5", "fusion-live", "image", 0.0),
    ]
    assert drafted["plan"]["runner_cannot_apply"] == []
    assert (env["runs_root"] / payload["plan"]).is_file()


def test_draft_carries_turn_allowance_and_stop_early(tmp_path):
    env = _setup(tmp_path)
    drafted = draft_matchup(instrument="sambuca", entrants=ENTRANTS, registry_path=env["registry_path"],
                            runs_root=env["runs_root"], out_path=tmp_path / "draft.json", reference="Museum.jpg",
                            instruments_root=env["instruments_root"], turns=8, stop_early=False)
    assert drafted["job"]["turns"] == drafted["plan"]["max_turns"] == 8
    assert drafted["job"]["stop_early"] is drafted["plan"]["stop_when_all_checks_pass"] is False
    assert drafted["plan"]["runner_cannot_apply"] == []


@pytest.mark.parametrize("name", ["nightly-cad-queue.json", "queue.json", None, "task"])
def test_the_live_queue_is_refused(tmp_path, name):
    env = _setup(tmp_path)
    task_queue = tmp_path / "arena-nightly" / "tonight.json"  # the scheduled task's queue, whatever it is called
    out = {None: env["live"], "task": task_queue}.get(name) or tmp_path / "elsewhere" / name
    with pytest.raises(DraftError, match="live nightly queue"):
        draft_matchup(instrument="sambuca", entrants=ENTRANTS, registry_path=env["registry_path"],
                      runs_root=env["runs_root"], out_path=out, reference="Museum.jpg",
                      instruments_root=env["instruments_root"], live_queues=[task_queue])
    assert not out.exists() or out == env["live"]


def test_existing_drafts_unknown_tasks_and_runner_gaps_are_refused(tmp_path):
    env = _setup(tmp_path)
    out = tmp_path / "draft.json"
    out.write_text("{}")
    kwargs = dict(registry_path=env["registry_path"], runs_root=env["runs_root"], reference="Museum.jpg",
                  instruments_root=env["instruments_root"])
    with pytest.raises(DraftError, match="already exists"):
        draft_matchup(instrument="sambuca", entrants=ENTRANTS, out_path=out, **kwargs)
    with pytest.raises(DraftError, match="not an arena registry task"):
        draft_matchup(instrument="lyre", entrants=ENTRANTS, out_path=tmp_path / "d2.json", **kwargs)
    with pytest.raises(DraftError, match="at least two entrants"):
        draft_matchup(instrument="sambuca", entrants=["cadam:opus"], out_path=tmp_path / "d3.json", **kwargs)
    with pytest.raises(DraftError, match="Reference image Missing.jpg"):
        draft_matchup(instrument="sambuca", entrants=ENTRANTS, out_path=tmp_path / "d4.json",
                      **{**kwargs, "reference": "Missing.jpg"})


def test_cli_draft_matchup_writes_the_draft_and_explains_refusals(tmp_path):
    env = _setup(tmp_path)
    args = ["arena", "draft-matchup", "--registry", str(env["registry_path"]), "--runs-root", str(env["runs_root"]),
            "--instruments-root", str(env["instruments_root"]), "--reference", "Museum.jpg"]
    result = CliRunner().invoke(app, [*args, "--out", str(tmp_path / "draft.json"), "--turns", "5", "--no-stop-early"])
    assert result.exit_code == 0, result.output
    assert '"deliverable": "single"' in result.output
    assert '"turns": 5' in result.output and '"stop_early": false' in result.output
    refused = CliRunner().invoke(app, [*args, "--out", str(env["live"])])
    assert refused.exit_code == 1 and "draft not written" in refused.output


@pytest.mark.parametrize("alias", ["QUEUE.JSON", "Nightly-CAD-Queue.json", "TONIGHT.JSON", "hardlink"])
def test_case_and_identity_aliases_of_the_live_queue_are_refused(tmp_path, alias):
    env = _setup(tmp_path)
    task_queue = tmp_path / "arena-nightly" / "tonight.json"
    task_queue.parent.mkdir()
    task_queue.write_text('{"jobs": []}')
    before = task_queue.read_bytes()
    if alias == "hardlink":  # another name for the same file (what QUEUE.JSON is on /mnt/c)
        out = tmp_path / "arena-nightly" / "innocent-draft.json"
        os.link(task_queue, out)
    else:
        out = task_queue.parent / alias
    with pytest.raises(DraftError, match="live nightly queue"):
        draft_matchup(instrument="sambuca", entrants=ENTRANTS, registry_path=env["registry_path"],
                      runs_root=env["runs_root"], out_path=out, reference="Museum.jpg",
                      instruments_root=env["instruments_root"], live_queues=[task_queue], replace=True)
    assert task_queue.read_bytes() == before
