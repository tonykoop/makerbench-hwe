"""Control Room New Run: catalog, estimate, job payload and queue flag (#1114)."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from makerbench import nightly_cad  # noqa: E402
from makerbench.arena_studio import control_room_newrun as nr  # noqa: E402
from makerbench.arena_studio import create_studio_app  # noqa: E402

ORIGIN = {"Origin": "http://127.0.0.1"}


def _png() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 80), (120, 96, 70)).save(buffer, format="PNG")
    return buffer.getvalue()


PNG = _png()
INSTRUMENTS = [
    {"id": "sambuca", "display_name": "Sambuca", "family": "strings", "task_brief": "Boat body.", "envelope_mm": [1300, 560, 320]},
    {"id": "tongue_drum_wood_assembly", "display_name": "Tongue drum", "family": "percussion", "assembly": True, "task_brief": "Six panels."},
    {"id": "kora", "display_name": "Kora", "family": "strings", "task_brief": "Calabash harp."},
]
FORM = {"instrument": "sambuca", "entrants": ["cadam:fable", "freecad:opus", "solidworks:sol"], "brief": "Boat body.",
        "turns": 5, "cap_usd": 2.0, "deliverable": "single", "variants": []}


def build_newrun_repo(repo: Path) -> Path:
    """Registry with three instruments; sambuca and the drum have a task reference image, the kora none."""
    (repo / "registry.json").write_text(json.dumps({"instruments": INSTRUMENTS}), encoding="utf-8")
    for task in ("sambuca", "tongue_drum_wood_assembly"):
        (repo / "tasks" / task).mkdir(parents=True)
        (repo / "tasks" / task / "reference.png").write_bytes(PNG)
    (repo / "runs").mkdir()
    (repo / "runs" / "nightly-cad-queue.json").write_text(
        json.dumps({"schema": nightly_cad.SCHEMA, "jobs": [{"job_id": "existing", "instrument_id": "kora",
                    "reference_image": "x.png", "entrants": [{"entrant_id": "a", "model_id": "a"}]}]}), encoding="utf-8")
    return repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return build_newrun_repo(tmp_path)


def _client(repo: Path, **kwargs) -> TestClient:
    app = create_studio_app(registry_path=repo / "registry.json", repo_root=repo, **kwargs)
    return TestClient(app, base_url="http://127.0.0.1")


def _fake_estimator(model_id, *, telemetry_store, backend):
    if model_id.startswith("anthropic/claude-fable"):
        return {"cost_usd": 0.31, "cost_source": "telemetry", "duration_s": 1200.0}
    if model_id.startswith("openai/"):
        return {"cost_usd": None, "cost_source": "unknown", "duration_s": None}
    return {"cost_usd": 0.0, "cost_source": "subscription_zero_marginal", "duration_s": 1800.0}


def test_catalog_lists_registry_instruments_and_explains_blocked_cells():
    catalog = nr.catalog(INSTRUMENTS, availability=lambda backend: {"status": "available", "reason": "ok"})
    assert [i["id"] for i in catalog["instruments"]] == ["sambuca", "tongue_drum_wood_assembly", "kora"]
    tools = {t["id"]: t for t in catalog["tools"]}
    assert tools["freecad"]["cells"]["fable"] == "Fable 5.1 is only reachable through OpenRouter."
    assert tools["cadam"]["cells"]["fable"] is None
    assert all(reason and "#1099" in reason for reason in tools["cadam-cli"]["cells"].values())
    assert tools["solidworks"]["seat"] and tools["fusion"]["seat"] and not tools["freecad"]["seat"]


def test_estimate_totals_seat_tools_serially_and_keeps_unknowns_unknown():
    form = {**FORM, "entrants": ["cadam:fable", "freecad:opus", "solidworks:sol", "fusion:sonnet"], "variants": ["15", "16"]}
    est = nr.estimate(form, estimator=_fake_estimator)
    assert est["designs"] == 8
    assert est["openrouter_usd"] == 0.62 and est["openrouter_complete"] is True
    assert est["subscription_designs"] == {"claude": 4, "codex": 2}
    # Seat tools: 2 × 1800 × 2 variants serial = 7200 s; the others overlap (max 3600 s).
    assert est["wall_time_s"] == 7200.0
    est = nr.estimate({**FORM, "entrants": ["cadam:sol", "freecad:opus"]}, estimator=_fake_estimator)
    assert est["openrouter_complete"] is False and est["unknown_cost"] == ["cadam:sol"]
    assert est["over_cap"] is False
    with pytest.raises(nr.NewRunError):
        nr.estimate({**FORM, "entrants": ["freecad:fable"]}, estimator=_fake_estimator)


def test_jobs_are_valid_nightly_jobs_with_a_plan_per_variant(repo: Path):
    now = datetime(2026, 10, 18, 22, 0, tzinfo=timezone.utc)
    built = nr.build_jobs({**FORM, "variants": ["15", "16"]}, instruments={i["id"]: i for i in INSTRUMENTS},
                          runs_root=repo / "runs", task_reference=lambda t: repo / "tasks" / t / "reference.png", now=now)
    assert [b["job"]["job_id"] for b in built] == ["cr-20261018T220000Z-sambuca-15", "cr-20261018T220000Z-sambuca-16"]
    job = nightly_cad.NightlyJob.from_dict(built[0]["job"])
    job.validate()
    # The $2 cap bounds the whole two-variant sweep: $1 per job, all of it for the one paid entrant.
    assert job.budget_usd == 1.0
    assert [(e.entrant_id, e.kind, e.model_id, e.backend, e.max_cost_usd) for e in job.entrants] == [
        ("cadam-fable", "cadam", "anthropic/claude-fable-5.1", "openscad", 1.0),
        ("freecad-opus", "arena", "claude-code-opus-5.5", "freecad", 0.0),
        ("solidworks-sol", "live", "codex-gpt-6.1-sol", "solidworks-live", 0.0),
    ]
    plan = built[1]["plan"]
    assert plan["schema"] == nr.PLAN_SCHEMA and plan["variant"] == "16" and plan["max_turns"] == 5
    assert plan["deliverable"] == "single" and plan["stop_when_all_checks_pass"] is True


@pytest.mark.parametrize("change, message", [
    ({"instrument": "nope"}, "Choose an instrument"),
    ({"entrants": ["freecad:opus"]}, "at least two entrants"),
    ({"entrants": ["freecad:fable", "cadam:opus"]}, "only reachable through OpenRouter"),
    ({"turns": 9}, "Turns must be"),
    ({"cap_usd": -1}, "cap must be"),
    ({"brief": "  "}, "Write a brief"),
    ({"deliverable": "kit"}, "Deliverable"),
    ({"instrument": "kora"}, "Add a reference image"),
    ({"references": [{"data": "data:image/png;base64," + base64.b64encode(b"GIF89a").decode()}]}, "PNG, JPEG or WebP"),
    ({"references": [{"data": "data:text/html;base64,PGI+"}]}, "PNG, JPEG or WebP"),
])
def test_bad_forms_are_refused_with_a_reason(repo: Path, change, message):
    response = _client(repo).post("/api/newrun/tonight", json={**FORM, **change}, headers=ORIGIN)
    assert response.status_code in (400, 422)
    assert message in response.text


def test_add_to_tonight_with_queue_writes_off_leaves_the_queue_alone(repo: Path):
    queue = repo / "runs" / "nightly-cad-queue.json"
    before = queue.read_bytes()
    result = _client(repo).post("/api/newrun/tonight", json=FORM, headers=ORIGIN).json()
    assert result["queued"] is False and queue.read_bytes() == before
    draft = json.loads((repo / "runs" / result["draft"]).read_text())
    assert draft["schema"] == nr.DRAFT_SCHEMA and draft["jobs"][0]["instrument_id"] == "sambuca"
    assert (repo / "runs" / result["plans"][0]).is_file()


def test_add_to_tonight_with_queue_writes_on_appends_through_the_runner_format(repo: Path):
    queue = repo / "runs" / "nightly-cad-queue.json"
    upload = "data:image/png;base64," + base64.b64encode(PNG).decode()
    # Only settings the runner applies: the registry brief, one turn, one reference.
    result = _client(repo, control_room_queue_writes=True).post(
        "/api/newrun/tonight", json={**FORM, "instrument": "kora", "brief": "Calabash harp.", "turns": 1,
                                     "references": [{"name": "kora.png", "data": upload}]}, headers=ORIGIN
    ).json()
    assert result["queued"] is True and result["added"] == 1
    payload, jobs = nightly_cad.load_queue(queue)
    assert [job.job_id for job in jobs][0] == "existing"
    added = jobs[1]
    assert added.instrument_id == "kora" and added.status == "queued"
    reference = Path(added.reference_image)
    assert reference.read_bytes() == PNG and reference.is_relative_to(repo / "runs" / "control-room" / "plans")


def test_catalog_route_reports_presence_only_lights(repo: Path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-not-shown")
    body = _client(repo).get("/api/newrun/catalog")
    data = body.json()
    assert "sk-not-shown" not in body.text
    assert data["queue_writes"] is False and data["allow_live"] is False
    assert {"state": "ok", "text": "OpenRouter key present"} in data["lights"]
    assert len(data["instruments"]) == 3


def test_run_now_needs_allow_live_and_one_arena_backend(repo: Path, monkeypatch):
    assert _client(repo).post("/api/newrun/run-now", json=FORM, headers=ORIGIN).status_code == 403
    client = _client(repo, allow_live=True)
    assert client.post("/api/newrun/run-now", json=FORM, headers=ORIGIN).status_code == 400  # mixed, with CADAM
    calls = []
    monkeypatch.setattr(client.app.state.service, "launch_competition", lambda config: calls.append(config) or {"success": True})
    form = {**FORM, "entrants": ["freecad:opus", "freecad:sol"]}
    assert client.post("/api/newrun/run-now", json=form, headers=ORIGIN).json() == {"success": True}
    assert calls == [{"instruments": ["sambuca"], "models": ["claude-code-opus-5.5", "codex-gpt-6.1-sol"],
                      "backend": "freecad", "max_turns": 5, "live": True}]


def test_estimate_route_never_launches(repo: Path, monkeypatch):
    client = _client(repo)
    monkeypatch.setattr(client.app.state.service, "launch_competition", lambda config: pytest.fail("estimate launched a run"))
    data = client.post("/api/newrun/estimate", json=FORM, headers=ORIGIN).json()
    assert data["designs"] == 3 and data["subscription_designs"] == {"claude": 1, "codex": 1}


RUNNABLE = {**FORM, "turns": 1}  # the sambuca registry brief, one attempt, no variants: nothing the runner drops


def test_settings_the_runner_cannot_apply_are_staged_not_queued(repo: Path):
    queue = repo / "runs" / "nightly-cad-queue.json"
    before = queue.read_bytes()
    client = _client(repo, control_room_queue_writes=True)
    form = {**FORM, "brief": "Boat body, but taller.", "variants": ["15"], "deliverable": "assembly", "turns": 5}
    gaps = client.post("/api/newrun/estimate", json=form, headers=ORIGIN).json()["runner_gaps"]
    assert len(gaps) == 4 and "edited brief" in gaps[0]
    result = client.post("/api/newrun/tonight", json=form, headers=ORIGIN).json()
    assert result["queued"] is False and result["staged_because"] == gaps
    assert queue.read_bytes() == before
    draft = json.loads((repo / "runs" / result["draft"]).read_text())
    assert draft["staged_because"] == gaps
    plan = json.loads((repo / "runs" / result["plans"][0]).read_text())
    assert plan["runner_cannot_apply"] == gaps and plan["brief"] == "Boat body, but taller."
    queued = client.post("/api/newrun/tonight", json=RUNNABLE, headers=ORIGIN).json()
    assert queued["queued"] is True and queued["added"] == 1


def test_the_cap_bounds_the_whole_sweep(repo: Path):
    built = nr.build_jobs({**FORM, "entrants": ["cadam:fable", "cadam:opus", "freecad:opus"], "variants": ["A4", "D4", "G4"],
                           "cap_usd": 3.0}, instruments={i["id"]: i for i in INSTRUMENTS}, runs_root=repo / "runs",
                          task_reference=lambda t: repo / "tasks" / t / "reference.png")
    budgets = [job["job"]["budget_usd"] for job in built]
    allowances = [e["max_cost_usd"] for job in built for e in job["job"]["entrants"]]
    assert budgets == [1.0, 1.0, 1.0] and sum(budgets) <= 3.0
    assert sum(allowances) <= 3.0 and sorted(set(allowances)) == [0.0, 0.5]


def test_same_second_runs_get_distinct_job_ids(repo: Path):
    now = datetime(2026, 10, 18, 22, 0, tzinfo=timezone.utc)
    ids = []
    for _ in range(2):
        built = nr.build_jobs(FORM, instruments={i["id"]: i for i in INSTRUMENTS}, runs_root=repo / "runs",
                              task_reference=lambda t: repo / "tasks" / t / "reference.png", now=now)
        nr.write_plans(repo / "runs", built)
        ids.append(built[0]["job"]["job_id"])
    assert ids == ["cr-20261018T220000Z-sambuca", "cr-20261018T220000Z-sambuca-2"]


@pytest.mark.parametrize("link", ["runs", "control-room"])
def test_symlinked_write_destinations_are_refused(tmp_path: Path, link: str):
    (tmp_path / "repo").mkdir()
    repo = build_newrun_repo(tmp_path / "repo")
    outside = tmp_path / "outside"
    outside.mkdir()
    if link == "runs":
        (outside / "nightly-cad-queue.json").write_bytes((repo / "runs" / "nightly-cad-queue.json").read_bytes())
        import shutil
        shutil.rmtree(repo / "runs")
        (repo / "runs").symlink_to(outside, target_is_directory=True)
    else:
        (repo / "runs" / "control-room").symlink_to(outside, target_is_directory=True)
    before = sorted(p.name for p in outside.rglob("*"))
    for flag in (False, True):
        response = _client(repo, control_room_queue_writes=flag).post("/api/newrun/tonight", json=RUNNABLE, headers=ORIGIN)
        assert response.status_code == 409 and "symlink" in response.text
    assert sorted(p.name for p in outside.rglob("*")) == before  # nothing written outside


@pytest.mark.parametrize("change, message", [
    ({"instrument": "nope"}, "Choose an instrument"),
    ({"turns": -99}, "Turns must be"),
    ({"cap_usd": -5}, "cap must be"),
    ({"brief": ""}, "Write a brief"),
    ({"variants": ["x"] * 13}, "Variants must be"),
])
def test_run_now_and_estimate_validate_like_tonight(repo: Path, monkeypatch, change, message):
    client = _client(repo, allow_live=True)
    monkeypatch.setattr(client.app.state.service, "launch_competition", lambda config: pytest.fail("launched"))
    form = {**FORM, "entrants": ["freecad:opus", "freecad:sol"], **change}
    response = client.post("/api/newrun/run-now", json=form, headers=ORIGIN)
    assert response.status_code == 400 and message in response.text
    if "brief" not in change:  # an estimate does not need a brief yet
        estimate = client.post("/api/newrun/estimate", json=form, headers=ORIGIN)
        assert estimate.status_code == 400 and message in estimate.text


@pytest.mark.parametrize("change, carried", [
    ({"brief": "Boat body, but taller."}, "edited brief"),
    ({"variants": ["15", "16"]}, "variants"),
    ({"deliverable": "assembly"}, "wood-assembly"),
    ({"references": [{"name": "a.png", "data": "data:image/png;base64,AAAA"}]}, "reference images"),
])
def test_run_now_refuses_settings_it_cannot_carry(repo: Path, monkeypatch, change, carried):
    client = _client(repo, allow_live=True)
    calls = []
    monkeypatch.setattr(client.app.state.service, "launch_competition", lambda config: calls.append(config) or {"success": True})
    form = {**FORM, "entrants": ["freecad:opus", "freecad:sol"]}
    estimate = client.post("/api/newrun/estimate", json={**form, **change}, headers=ORIGIN).json()
    assert any(carried in gap for gap in estimate["run_now_gaps"])
    response = client.post("/api/newrun/run-now", json={**form, **change}, headers=ORIGIN)
    assert response.status_code == 400
    assert "Run now uses the default brief and one variant; use Add to tonight for custom settings" in response.text
    assert carried in response.text and calls == []
    # Turns are carried, so the defaults with any turn count still launch.
    assert client.post("/api/newrun/run-now", json={**form, "turns": 6}, headers=ORIGIN).json() == {"success": True}
    assert calls[0]["max_turns"] == 6
