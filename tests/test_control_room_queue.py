"""Tonight's Queue uses fixture files, never the nightly task's queue or runs."""

import json
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from makerbench import nightly_preflight as pf
from makerbench import nightly_cad
from makerbench.arena_studio import create_studio_app
from makerbench.arena_studio import control_room_queue as cq
from makerbench.arena_studio import control_room_newrun as nr
from makerbench.run_log_io import atomic_write_json, file_lock

from test_control_room_newrun import PNG, build_newrun_repo

FIXTURE = Path(__file__).parent / "fixtures" / "control_room_queue.json"
ORIGIN = {"Origin": "http://127.0.0.1"}


def build_queue_repo(path):
    repo = build_newrun_repo(path)
    payload = json.loads(FIXTURE.read_text())
    registry = json.loads((repo / "registry.json").read_text())
    for instrument in ("egyptian-harps", "ocarina", "cajon", "duduk"):
        registry["instruments"].append({"id": instrument, "display_name": instrument.replace("-", " ").title(),
                                        "family": "woodwind" if instrument in {"ocarina", "duduk"} else "strings"})
    atomic_write_json(repo / "registry.json", registry)
    for job in payload["jobs"]:
        reference = repo / "runs" / job["reference_image"]
        reference.parent.mkdir(parents=True)
        reference.write_bytes(PNG)
        atomic_write_json(repo / "runs" / "control-room" / "plans" / f"{job['job_id']}.json",
                          {"job_id": job["job_id"], "deliverable": "single"})
    atomic_write_json(repo / "runs" / "nightly-cad-queue.json", payload)
    return repo


@pytest.fixture
def repo(tmp_path):
    return build_queue_repo(tmp_path)


def client(repo, **options):
    return TestClient(create_studio_app(repo_root=repo, registry_path=repo / "registry.json", **options),
                      base_url="http://127.0.0.1")


def edit(api, action, job_id="", order=None, revision=None):
    return api.post("/api/queue/edit", headers=ORIGIN, json={"action": action, "job_id": job_id,
                    "order": order or [], "revision": revision or api.get("/api/queue").json()["revision"]})


def queued(view):
    return [j["job_id"] for j in view["jobs"] if j["status"] in {"queued", "draft"}]


def test_queue_fixture_has_no_host_paths_or_queued_run_folders():
    assert not any(marker in FIXTURE.read_text() for marker in ("/mnt/", "/home/", "Users"))
    assert all(j.get("run_dir") is None and j.get("run_id") is None
               for j in json.loads(FIXTURE.read_text())["jobs"] if j["status"] == "queued")


def test_queue_cards_use_fixture_route_labels_and_confined_plan_images(repo):
    api = client(repo)
    view = api.get("/api/queue").json()
    assert view["queue_writes"] is False and len(view["jobs"]) == 6
    assert view["jobs"][0]["status"] == "running"
    night = view["jobs"][1]
    assert night["name"] == "Egyptian Harps" and night["drafted_by"] == "Fixture · New Run"
    assert night["cap_usd"] == 5.0 and len(night["entrants"]) == 6
    assert any("Codex" in e["label"] and "Blender" in e["label"] for e in night["entrants"])
    assert api.get(night["reference_url"]).content == PNG
    assert view["jobs"][-1]["run_id"] == "fixture-blocked-run"


@pytest.mark.parametrize("writes", [False, True])
def test_reorder_tomorrow_remove_undo_preserve_every_job_and_metadata(repo, writes):
    api = client(repo, control_room_queue_writes=writes)
    path = repo / "runs" / "nightly-cad-queue.json"
    before = path.read_bytes()
    original = json.loads(before)
    order = queued(api.get("/api/queue").json())
    assert edit(api, "reorder", order=list(reversed(order))).status_code == 200
    assert queued(api.get("/api/queue").json()) == list(reversed(order))
    assert edit(api, "tomorrow", job_id=order[-1]).status_code == 200
    assert queued(api.get("/api/queue").json())[-1] == order[-1]
    response = edit(api, "remove", job_id=order[0])
    assert response.status_code == 200
    removed = next(j for j in response.json()["jobs"] if j["job_id"] == order[0])
    assert removed["status"] == "removed" and removed["can_undo"]
    if writes:
        persisted = json.loads(path.read_bytes())
        assert len(persisted["jobs"]) == 6 and persisted["source_note"] == original["source_note"]
        assert persisted["jobs"][0] == original["jobs"][0] and persisted["jobs"][-1] == original["jobs"][-1]
        assert next(j for j in persisted["jobs"] if j["job_id"] == order[0])["status"] == "removed"
    assert edit(api, "undo", job_id=order[0]).status_code == 200
    assert order[0] in queued(api.get("/api/queue").json())
    if not writes:
        assert path.read_bytes() == before and not path.with_name(path.name + ".lock").exists()
        # A second Studio has its own in-memory draft.
        assert queued(client(repo).get("/api/queue").json()) == order


@pytest.mark.parametrize("action", ["remove", "tomorrow", "undo"])
@pytest.mark.parametrize("job_id", ["fixture-sambuca", "fixture-sambuca-blocked", "missing"])
def test_running_and_blocked_nights_cannot_be_mutated(repo, action, job_id):
    api = client(repo, control_room_queue_writes=True)
    path = repo / "runs" / "nightly-cad-queue.json"
    before = path.read_bytes()
    assert edit(api, action, job_id).status_code == 409
    assert path.read_bytes() == before


def test_reorder_requires_exact_pending_set_and_stale_edit_is_rejected(repo):
    api = client(repo, control_room_queue_writes=True)
    path = repo / "runs" / "nightly-cad-queue.json"
    view = api.get("/api/queue").json()
    order = queued(view)
    for bad in [order[:-1], order + [order[0]], order + ["fixture-sambuca"], ["missing", *order[1:]]]:
        assert edit(api, "reorder", order=bad).status_code == 409
    payload = json.loads(path.read_text())
    payload["jobs"][1]["status"] = "running"  # simulate the runner claiming the next night
    atomic_write_json(path, payload)
    before = path.read_bytes()
    assert edit(api, "remove", order[0], revision=view["revision"]).status_code == 409
    assert path.read_bytes() == before


@pytest.mark.parametrize("component", ["queue", "lock", "control-room", "runs"])
def test_symlinked_queue_lock_and_control_room_destinations_are_refused(repo, component, tmp_path):
    queue = repo / "runs" / "nightly-cad-queue.json"
    outside = tmp_path / "outside"
    outside.mkdir()
    if component in {"queue", "lock"}:
        link = queue if component == "queue" else queue.with_name(queue.name + ".lock")
        target = outside / "target.json"
        target.write_bytes(queue.read_bytes())
        if link.exists():
            link.unlink()
        link.symlink_to(target)
    else:
        link = repo / "runs" if component == "runs" else repo / "runs" / "control-room"
        link.rename(outside / "original")
        link.symlink_to(outside / "original", target_is_directory=True)
    before = {p.relative_to(outside): p.read_bytes() for p in outside.rglob("*") if p.is_file()}
    api = client(repo, control_room_queue_writes=True)
    assert api.get("/api/queue").status_code == 409
    assert api.post("/api/queue/edit", headers=ORIGIN, json={"action": "remove", "job_id": "fixture-ocarina", "revision": "x"}).status_code == 409
    assert before == {p.relative_to(outside): p.read_bytes() for p in outside.rglob("*") if p.is_file()}


def test_reference_path_outside_plan_folder_or_through_symlink_is_never_served(repo, tmp_path):
    api = client(repo)
    queue = repo / "runs" / "nightly-cad-queue.json"
    payload = json.loads(queue.read_text())
    outside = tmp_path / "external.png"
    outside.write_bytes(PNG)
    payload["jobs"][1]["reference_image"] = str(outside)
    atomic_write_json(queue, payload)
    assert api.get("/api/queue/reference/fixture-egyptian-harps").status_code == 404
    inside = repo / "runs" / payload["jobs"][2]["reference_image"]
    inside.unlink()
    inside.symlink_to(outside)
    assert api.get("/api/queue/reference/fixture-ocarina").status_code == 404
    assert api.get("/api/queue/reference/unknown").status_code == 404


def test_written_go_cache_limits_both_fetches_and_offline_failures():
    now, calls = [0], []
    def fetch():
        calls.append(True)
        if len(calls) == 2:
            raise OSError("offline")
        return True
    cache = cq.WrittenGo(fetch, clock=lambda: now[0])
    assert cache.read()["given"] is True
    now[0] = 599
    assert cache.read()["given"] is True and len(calls) == 1
    now[0] = 600
    assert cache.read()["text"] == "unknown (offline)"
    now[0] = 1199
    assert cache.read()["given"] is None and len(calls) == 2
    now[0] = 1200
    assert cache.read()["given"] is True and len(calls) == 3


def test_only_comments_starting_with_go_count(monkeypatch):
    response = subprocess.CompletedProcess([], 0, '\n'.join(map(json.dumps, ["example\nGO: not an approval", "No GO"])))
    monkeypatch.setattr(cq.subprocess, "run", lambda *a, **k: response)
    assert cq.fetch_written_go() is False
    response.stdout += '\n' + json.dumps("GO: approved")
    assert cq.fetch_written_go() is True


def test_go_panel_task_cache_and_presence_never_return_secrets(repo, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sensitive-value-never-output")
    monkeypatch.setattr(cq, "status_lights", lambda root: [{"label": "OpenRouter key", "state": "ok", "detail": "present"}])
    api = client(repo)
    api.app.state.control_room_queue.go.fetch = lambda: False
    data = api.get("/api/queue/status")
    assert data.json()["task"] == "unknown (Windows task)" and data.json()["written_go"]["given"] is False
    assert "sensitive-value-never-output" not in data.text
    atomic_write_json(repo / "runs" / "control-room" / "task-state.json", {"state": "Disabled"})
    assert api.get("/api/queue/status").json()["task"] == "Disabled (cached)"
    assert api.post("/api/queue/enable", headers=ORIGIN).status_code in {404, 405}


def test_real_status_lights_use_presence_and_explicit_quota_telemetry(repo, monkeypatch):
    from makerbench import live_cad_runner
    from telemetry.schema import SessionTelemetry
    from telemetry.store import append

    monkeypatch.setenv("OPENROUTER_API_KEY", "sensitive-key-never-output")
    monkeypatch.setenv("HWE_SW_TOKEN", "sensitive-bridge-never-output")
    monkeypatch.setattr(live_cad_runner, "connector_available", lambda config: config.connector == "hwe-solidworks")
    monkeypatch.setattr(cq.shutil, "which", lambda name: "/fixture/bin/" + name)
    monkeypatch.setattr(cq.doe, "backend_availability", lambda backend: {"status": "available", "reason": "executable found"})
    monkeypatch.setattr(pf, "audit_cadam", lambda *a, **kw: pf.CadamStatus("redacted", True, True, "reachable"))
    append(SessionTelemetry(session_id="fixture", agent_id="codex", duration_seconds=1,
                            telemetry={"weekly_quota": {"codex": {"remaining_percent": 42}}}),
           str(repo / "data" / "sessions.jsonl"))
    lights = cq.status_lights(repo)
    text = json.dumps(lights)
    assert "sensitive-key-never-output" not in text and "sensitive-bridge-never-output" not in text
    by_label = {light["label"]: light for light in lights}
    assert by_label["SolidWorks seat"]["detail"] == "authenticated ping answered"
    assert by_label["Fusion seat"]["detail"] == "bridge not reachable"
    assert by_label["Claude weekly quota"]["detail"] == "unknown"
    assert by_label["Codex weekly quota"]["detail"] == "42% remaining (recorded telemetry)"
    assert by_label["Claude CLI"]["detail"] == "on PATH · sign-in unknown"


@pytest.mark.parametrize("pinned", [False, True])
def test_queue_status_uses_real_backend_availability_without_doe_monkeypatch(repo, monkeypatch, pinned):
    from makerbench import freecad_backend, live_cad_runner

    # Only external I/O is stubbed: the real FreeCAD and DoE availability code runs.
    monkeypatch.setattr(live_cad_runner, "connector_available", lambda config: False)
    monkeypatch.setattr(pf, "audit_cadam", lambda *a, **kw: pf.CadamStatus("redacted", False, False, "offline"))
    monkeypatch.delenv("MAKERBENCH_FREECAD_APPDIR", raising=False)
    if pinned:
        appdir = repo / "fixture-freecad-appdir"
        (appdir / "usr" / "bin").mkdir(parents=True)
        (appdir / "AppRun").write_text("# fixture; never executed")
        (appdir / "usr" / "bin" / "python").write_text("# fixture; never executed")
        (appdir / ".makerbench-appimage-sha256").write_text(freecad_backend.APPIMAGE_SHA256)
        monkeypatch.setenv("MAKERBENCH_FREECAD_APPDIR", str(appdir))
    api = client(repo)
    api.app.state.control_room_queue.go.fetch = lambda: False
    response = api.get("/api/queue/status")
    assert response.status_code == 200, response.text
    lights = {light["label"]: light for light in response.json()["lights"]}
    assert lights["FreeCAD"]["state"] == ("ok" if pinned else "warn")
    assert "Pinned FreeCAD AppDir" in lights["FreeCAD"]["detail"]
    assert {"OpenSCAD", "Blender"} <= lights.keys()


def test_preflight_passes_only_selected_snapshot_to_real_report_builder(repo, monkeypatch):
    api = client(repo)
    queue = repo / "runs" / "nightly-cad-queue.json"
    before = queue.read_bytes()
    secrets = repo / "runs" / "makerbench-nightly.env"
    secrets.write_text('\n'.join(f'{key}=opaque-fixture-value' for key in pf.REQUIRED_SECRET_KEYS))
    observed = []
    real = pf.build_report
    def build(**kwargs):
        payload = json.loads(kwargs["queue_path"].read_text())
        observed.append(payload)
        assert kwargs["queue_path"] != queue and len(payload["jobs"]) == 1
        assert kwargs["check_entrants"] is True
        return real(**kwargs, cadam_transport=lambda *a: b"ok", environ={}, which=lambda _: "/fixture/cli")
    monkeypatch.setattr(pf, "build_report", build)
    monkeypatch.setattr(pf, "_freecad_note", lambda *a: (True, "pinned fixture AppDir"))
    # Fixture repo contains the expected runner path, so the paths gate is real.
    runner = repo / "scripts" / "windows" / "run-nightly-cad-arena.ps1"
    runner.parent.mkdir(parents=True)
    runner.write_text("# fixture")
    response = api.post("/api/queue/preflight", headers=ORIGIN, json={"job_id": "fixture-duduk"})
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True and len(response.json()["entrants"]) == 5
    assert all(e["ok"] for e in response.json()["entrants"])
    assert observed[0]["jobs"][0]["job_id"] == "fixture-duduk"
    assert "opaque-fixture-value" not in response.text and queue.read_bytes() == before
    assert not list((repo / "runs").glob("20*"))


def test_explicit_external_queue_and_newrun_share_one_destination(repo, tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    path = external / "queue.json"
    path.write_bytes((repo / "runs" / "nightly-cad-queue.json").read_bytes())
    api = client(repo, control_room_queue=path, control_room_queue_writes=True)
    before = (repo / "runs" / "nightly-cad-queue.json").read_bytes()
    assert edit(api, "remove", "fixture-ocarina").status_code == 200
    built = [{"job": {"job_id": "new-night", "instrument_id": "kora", "reference_image": "reference.png", "entrants": []}}]
    nr.append_jobs(repo / "runs", built, queue_path=path)
    raw = json.loads(path.read_text())
    removed = next(j for j in raw["jobs"] if j["job_id"] == "fixture-ocarina")
    assert removed["status"] == "removed" and removed["queue_previous_status"] == "queued"
    assert (repo / "runs" / "nightly-cad-queue.json").read_bytes() == before


def test_external_runs_default_queue_is_beside_runs_root(repo, tmp_path):
    runs = tmp_path / "external" / "runs"
    runs.mkdir(parents=True)
    queue = runs.parent / "queue.json"
    queue.write_bytes((repo / "runs" / "nightly-cad-queue.json").read_bytes())
    api = client(repo, control_room_runs_root=runs)
    assert len(api.get("/api/queue").json()["jobs"]) == 6


def test_newrun_default_external_queue_matches_windows_task_queue(repo, tmp_path):
    runs = tmp_path / "arena-nightly" / "runs"
    runs.mkdir(parents=True)
    queue = runs.parent / "queue.json"
    queue.write_bytes((repo / "runs" / "nightly-cad-queue.json").read_bytes())
    api = client(repo, control_room_runs_root=runs, control_room_queue_writes=True)
    response = api.post("/api/newrun/tonight", headers=ORIGIN, json={
        "instrument": "sambuca", "brief": "Boat body.", "entrants": ["freecad:opus", "freecad:sol"],
        "turns": 1, "cap_usd": 0, "deliverable": "single", "variants": [], "stop_early": True})
    assert response.status_code == 200 and response.json()["queued"], response.text
    assert len(json.loads(queue.read_text())["jobs"]) == 7
    assert not (runs / "nightly-cad-queue.json").exists()


def test_cross_origin_queue_edits_are_refused(repo):
    api = client(repo, control_room_queue_writes=True)
    assert api.post("/api/queue/edit", headers={"Origin": "https://example.com"}, json={
        "action": "remove", "job_id": "fixture-ocarina", "revision": "x"}).status_code == 403


def test_runner_completion_preserves_edits_made_during_active_night(repo):
    path = repo / "runs" / "nightly-cad-queue.json"
    _, stale_jobs = nightly_cad.load_queue(path)
    running = stale_jobs[0]
    api = client(repo, control_room_queue_writes=True)
    order = queued(api.get("/api/queue").json())
    edit(api, "reorder", order=list(reversed(order))).raise_for_status()
    edit(api, "remove", "fixture-ocarina").raise_for_status()
    running.status, running.run_id = "votable", "fixture-completed"
    nightly_cad.save_active_job(path, running)
    view = api.get("/api/queue").json()
    assert queued(view) == [i for i in reversed(order) if i != "fixture-ocarina"]
    assert view["jobs"][0]["status"] == "votable" and view["jobs"][0]["run_id"] == "fixture-completed"
    assert view["jobs"][0]["display_status"] == "done"
    assert next(j for j in view["jobs"] if j["job_id"] == "fixture-ocarina")["can_undo"]


def test_runner_cannot_resurrect_a_removed_queued_job(repo):
    path = repo / "runs" / "nightly-cad-queue.json"
    _, stale_jobs = nightly_cad.load_queue(path)
    selected = stale_jobs[2]
    edit(client(repo, control_room_queue_writes=True), "remove", selected.job_id).raise_for_status()
    before = path.read_bytes()
    selected.status = "running"
    with pytest.raises(RuntimeError, match="changed"):
        nightly_cad.save_active_job(path, selected)
    assert path.read_bytes() == before


def test_runner_cannot_claim_a_job_reordered_after_its_initial_read(repo):
    path = repo / "runs" / "nightly-cad-queue.json"
    payload = json.loads(path.read_text())
    payload["jobs"][0]["status"] = "votable"
    atomic_write_json(path, payload)
    _, stale_jobs = nightly_cad.load_queue(path)
    selected = stale_jobs[1]
    api = client(repo, control_room_queue_writes=True)
    edit(api, "tomorrow", selected.job_id).raise_for_status()
    before = path.read_bytes()
    selected.status = "running"
    with pytest.raises(RuntimeError, match="order changed"):
        nightly_cad.save_active_job(path, selected)
    assert path.read_bytes() == before


def test_queue_editor_rechecks_revision_after_acquiring_shared_file_lock(repo):
    path = repo / "runs" / "nightly-cad-queue.json"
    store = cq.QueueStore(runs_root=repo / "runs", queue_path=path, repo_root=repo, writes=True)
    revision = store.view()["revision"]
    started, done, errors = threading.Event(), threading.Event(), []
    def write():
        started.set()
        try:
            store.mutate(action="remove", job_id="fixture-ocarina", revision=revision, order=[])
        except cq.QueueError as error:
            errors.append(str(error))
        finally:
            done.set()
    with file_lock(path):
        thread = threading.Thread(target=write)
        thread.start()
        assert started.wait(1) and not done.wait(.05)
        payload = json.loads(path.read_text())
        payload["jobs"][2]["status"] = "running"
        atomic_write_json(path, payload)
    thread.join(timeout=2)
    assert done.is_set() and errors == ["The queue changed. Refresh before editing it."]
    assert json.loads(path.read_text())["jobs"][2]["status"] == "running"


def test_production_runner_keeps_queue_changes_made_inside_an_entrant_handler(repo):
    from test_nightly_cad import _synthetic_handler

    path = repo / "runs" / "nightly-cad-queue.json"
    payload = json.loads(path.read_text())
    for job in payload["jobs"]:
        job["reference_image"] = str(repo / "runs" / job["reference_image"])
    atomic_write_json(path, payload)
    api = client(repo, control_room_queue_writes=True)
    order = queued(api.get("/api/queue").json())
    changed = []
    def handler(job, entrant, run_dir, registry):
        if not changed:
            edit(api, "reorder", order=list(reversed(order))).raise_for_status()
            edit(api, "remove", "fixture-ocarina").raise_for_status()
            changed.append(True)
        return _synthetic_handler(job, entrant, run_dir, registry)
    result = nightly_cad.NightlyExecutor(queue_path=path, registry_path=repo / "registry.json",
                                         output_root=repo / "runs", handlers={"arena": handler}).run()
    assert result["status"] == "votable" and changed
    view = api.get("/api/queue").json()
    assert queued(view) == [i for i in reversed(order) if i != "fixture-ocarina"]
    assert next(j for j in view["jobs"] if j["job_id"] == "fixture-ocarina")["can_undo"]


@pytest.mark.parametrize("action", ["tomorrow", "remove"])
def test_runner_retries_changed_claim_and_creates_no_refused_run_directory(repo, monkeypatch, action):
    from test_nightly_cad import _synthetic_handler

    path = repo / "runs" / "nightly-cad-queue.json"
    payload = json.loads(path.read_text())
    for job in payload["jobs"]:
        job["reference_image"] = str(repo / "runs" / job["reference_image"])
        if job["status"] == "running":
            job["status"] = "votable"
    atomic_write_json(path, payload)
    api = client(repo, control_room_queue_writes=True)
    order = queued(api.get("/api/queue").json())
    original_save, raced, handled = nightly_cad.save_active_job, [], []
    def save(queue, job):
        if job.status == "running" and not raced:
            raced.append(job.job_id)
            edit(api, action, job.job_id).raise_for_status()
        return original_save(queue, job)
    def handler(job, entrant, run_dir, registry):
        handled.append(job.job_id)
        return _synthetic_handler(job, entrant, run_dir, registry)
    monkeypatch.setattr(nightly_cad, "save_active_job", save)
    result = nightly_cad.NightlyExecutor(
        queue_path=path, registry_path=repo / "registry.json", output_root=repo / "runs",
        now=lambda: datetime(2026, 10, 22, 6, 30, tzinfo=timezone.utc),
        handlers={"arena": handler, "live": handler}).run()
    assert raced == [order[0]] and result["job_id"] == order[1]
    assert result["status"] == "votable" and set(handled) == {order[1]}
    assert not (repo / "runs" / f"20261022T063000Z-{order[0]}").exists()


def test_runner_returns_empty_without_a_run_directory_if_all_claims_were_removed(repo, monkeypatch):
    path = repo / "runs" / "nightly-cad-queue.json"
    payload = json.loads(path.read_text())
    for job in payload["jobs"]:
        job["reference_image"] = str(repo / "runs" / job["reference_image"])
        if job["status"] == "running":
            job["status"] = "votable"
    atomic_write_json(path, payload)
    api = client(repo, control_room_queue_writes=True)
    order = queued(api.get("/api/queue").json())
    original_save = nightly_cad.save_active_job
    def save(queue, job):
        for job_id in order:
            edit(api, "remove", job_id).raise_for_status()
        return original_save(queue, job)
    monkeypatch.setattr(nightly_cad, "save_active_job", save)
    result = nightly_cad.NightlyExecutor(
        queue_path=path, registry_path=repo / "registry.json", output_root=repo / "runs",
        handlers={"arena": lambda *a: pytest.fail("removed job was dispatched"),
                  "live": lambda *a: pytest.fail("removed job was dispatched")}).run()
    assert result["status"] == "queue-empty"
    assert not [p for p in (repo / "runs").iterdir() if p.is_dir() and p.name != "control-room"]
