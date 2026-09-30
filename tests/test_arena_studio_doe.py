"""Tests for the Arena Studio DoE Matrix Builder (#697 D3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from makerbench.arena_studio import create_studio_app
from makerbench.arena_studio import doe
from makerbench.arena_studio.service import ArenaStudioService
from makerbench import nightly_cad


def test_expand_matrix_full_cross_product():
    cells = doe.expand_matrix(
        ["ocarina", "kora"],
        ["claude-code-opus-5", "codex-gpt-5.6"],
        levels=["L1", "L2"],
        context_tiers=["blind"],
        seeds=[0, 1],
    )
    assert len(cells) == 2 * 2 * 2 * 1 * 2
    assert len({c["cell_id"] for c in cells}) == len(cells)


def test_expand_matrix_is_deduplicated():
    cells_a = doe.expand_matrix(["ocarina"], ["model-a"], levels=["L1"], seeds=[0])
    cells_b = doe.expand_matrix(["ocarina"], ["model-a"], levels=["L1"], seeds=[0, 0])
    assert len(cells_a) == 1
    assert len(cells_b) == 1


def test_subscription_model_cost_is_known_zero_not_unknown():
    estimate = doe.estimate_model_cost_and_time(
        "claude-code-opus-5", telemetry_store="/nonexistent/sessions.jsonl"
    )
    assert estimate["cost_usd"] == 0.0
    assert estimate["cost_source"] == "subscription_zero_marginal"


def test_unrecognized_paid_model_cost_stays_unknown_never_zero():
    estimate = doe.estimate_model_cost_and_time(
        "openrouter-some-exotic-model", telemetry_store="/nonexistent/sessions.jsonl"
    )
    assert estimate["cost_usd"] is None
    assert estimate["cost_source"] == "unknown"
    assert estimate["duration_s"] is None


def test_cost_and_time_from_telemetry_store(tmp_path: Path):
    store = tmp_path / "sessions.jsonl"
    records = [
        {
            "session_id": "s1",
            "agent_id": "openrouter-some-exotic-model",
            "duration_seconds": 120.0,
            "telemetry": {"cost_usd": 0.5},
        },
        {
            "session_id": "s2",
            "agent_id": "openrouter-some-exotic-model",
            "duration_seconds": 180.0,
            "telemetry": {"cost_usd": 1.5},
        },
    ]
    store.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    estimate = doe.estimate_model_cost_and_time(
        "openrouter-some-exotic-model", telemetry_store=str(store)
    )
    assert estimate["cost_usd"] == pytest.approx(1.0)
    assert estimate["n_cost_samples"] == 2
    assert estimate["duration_s"] == pytest.approx(150.0)
    assert estimate["n_duration_samples"] == 2


def test_matrix_summary_flags_unknown_cost_and_time():
    cells = [
        {"estimate": {"cost_usd": 0.0, "duration_s": None}},
        {"estimate": {"cost_usd": None, "duration_s": 60.0}},
    ]
    summary = doe.matrix_summary(cells)
    assert summary["n_cells"] == 2
    assert summary["known_cost_usd"] == 0.0
    assert summary["has_unknown_cost_cells"] is True
    assert summary["known_time_s"] == 60.0
    assert summary["has_unknown_time_cells"] is True


def test_build_nightly_queue_groups_by_instrument_seed_context(tmp_path: Path):
    ref_a = tmp_path / "ocarina.png"
    ref_a.write_bytes(b"fake-png")

    cells = doe.expand_matrix(
        ["ocarina"], ["model-a", "model-b"], levels=["L1", "L2"], seeds=[0]
    )
    payload, jobs = doe.build_nightly_queue(
        cells, reference_images={"ocarina": str(ref_a)}, budget_usd=5.0
    )
    assert payload["schema"] == nightly_cad.SCHEMA
    assert len(jobs) == 1
    job = jobs[0]
    assert job.instrument_id == "ocarina"
    # 2 models x 2 levels = 4 entrants in this one job.
    assert len(job.entrants) == 4
    entrant_ids = {e.entrant_id for e in job.entrants}
    assert entrant_ids == {"model-a::L1", "model-a::L2", "model-b::L1", "model-b::L2"}
    job.validate()  # must round-trip through the real schema's own validation


def test_build_nightly_queue_skips_unapproved_and_missing_images(tmp_path: Path):
    cells = doe.expand_matrix(["ocarina", "kora"], ["model-a", "model-b"], levels=["L1"], seeds=[0])
    payload, jobs = doe.build_nightly_queue(
        cells,
        reference_images={"ocarina": str(tmp_path / "missing.png")},
        is_approved=lambda inst: inst == "kora",
    )
    assert jobs == []
    reasons = {s["instrument_id"]: s["reason"] for s in payload["skipped"]}
    assert reasons["ocarina"] == "reference_image_not_approved"
    assert reasons["kora"] == "no_reference_image_on_disk"


def test_build_nightly_queue_skips_single_entrant_groups(tmp_path: Path):
    ref = tmp_path / "ocarina.png"
    ref.write_bytes(b"fake-png")
    cells = doe.expand_matrix(["ocarina"], ["model-a"], levels=["L1"], seeds=[0])
    payload, jobs = doe.build_nightly_queue(cells, reference_images={"ocarina": str(ref)})
    assert jobs == []
    assert payload["skipped"][0]["reason"] == "fewer_than_two_entrants"


def test_write_queue_file_round_trips_through_load_queue(tmp_path: Path):
    ref = tmp_path / "ocarina.png"
    ref.write_bytes(b"fake-png")
    cells = doe.expand_matrix(["ocarina"], ["model-a", "model-b"], levels=["L1"], seeds=[0])
    payload, jobs = doe.build_nightly_queue(cells, reference_images={"ocarina": str(ref)})

    queue_path = tmp_path / "queue.json"
    doe.write_queue_file(queue_path, payload, jobs)

    loaded_payload, loaded_jobs = nightly_cad.load_queue(queue_path)
    assert loaded_payload["schema"] == nightly_cad.SCHEMA
    assert len(loaded_jobs) == 1
    assert loaded_jobs[0].instrument_id == "ocarina"


def test_resolve_max_cost_refuses_unknown_cost_model_never_defaults_zero():
    with pytest.raises(ValueError, match="openrouter-paid-a"):
        doe.resolve_max_cost_usd_by_model(
            ["openrouter-paid-a"], telemetry_store="/nonexistent/sessions.jsonl"
        )


def test_resolve_max_cost_known_subscription_and_explicit_override():
    resolved = doe.resolve_max_cost_usd_by_model(
        ["claude-code-opus-5", "openrouter-paid-a"],
        overrides={"openrouter-paid-a": 2.5},
        telemetry_store="/nonexistent/sessions.jsonl",
    )
    assert resolved == {"claude-code-opus-5": 0.0, "openrouter-paid-a": 2.5}


def test_resolve_max_cost_zero_override_is_not_a_free_pass():
    # A 0.0 override is indistinguishable from "no cap" for BudgetGuard, so
    # it must be refused exactly like an absent/unknown cost, not accepted.
    with pytest.raises(ValueError, match="openrouter-paid-a"):
        doe.resolve_max_cost_usd_by_model(
            ["openrouter-paid-a"],
            overrides={"openrouter-paid-a": 0.0},
            telemetry_store="/nonexistent/sessions.jsonl",
        )


@pytest.mark.parametrize("override", [float("nan"), float("inf"), float("-inf")], ids=["nan", "+inf", "-inf"])
def test_resolve_max_cost_rejects_non_finite_override(override: float):
    # sol, #770: NaN <= 0 is False, so a NaN override used to be accepted as a
    # "ceiling" that BudgetGuard's comparisons could never trip; +inf is no cap.
    with pytest.raises(ValueError, match="openrouter-paid-a"):
        doe.resolve_max_cost_usd_by_model(
            ["openrouter-paid-a"],
            overrides={"openrouter-paid-a": override},
            telemetry_store="/nonexistent/sessions.jsonl",
        )


@pytest.fixture
def fake_registry(tmp_path: Path) -> Path:
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(
        json.dumps({"instruments": [{"id": "ocarina", "family": "woodwind"}]}), encoding="utf-8"
    )
    return reg_path


@pytest.fixture
def repo_root_with_reference(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    ref_dir = root / "tasks" / "ocarina"
    ref_dir.mkdir(parents=True)
    (ref_dir / "reference.png").write_bytes(b"fake-png")
    return root


@pytest.fixture
def client(fake_registry: Path, repo_root_with_reference: Path) -> TestClient:
    studio_app = create_studio_app(registry_path=fake_registry, repo_root=repo_root_with_reference)
    # feat/696-arena-studio's same-origin-POST guard (atlas's A1-A4) requires a
    # matching Origin header on every POST; TestClient sends none by default.
    # Loopback base URL: B1's TrustedHost guard rejects TestClient's default "testserver".
    return TestClient(
        studio_app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"}
    )


def test_doe_preview_route(client: TestClient):
    response = client.get(
        "/api/doe/preview",
        params={"instruments": "ocarina", "models": "claude-code-opus-5", "levels": "L1,L2", "seeds": "0"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["n_cells"] == 2
    assert all(c["estimate"]["cost_usd"] == 0.0 for c in data["cells"])


def test_doe_queue_route(client: TestClient, repo_root_with_reference: Path):
    # #697 D4 landed after this test was first written: an existing reference
    # image is no longer enough on its own, it must be explicitly approved.
    approve_res = client.post("/api/tasks/ocarina/approve?approved=true")
    assert approve_res.json()["approved"] is True

    response = client.post(
        "/api/doe/queue",
        json={
            "run_id": "doe_test_run",
            "instruments": ["ocarina"],
            "models": ["claude-code-opus-5", "codex-gpt-5.6"],
            "levels": ["L1"],
            "seeds": [0],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["n_jobs"] == 1
    # B1's API-boundary redaction publishes repo-relative paths, never host paths.
    assert data["queue_path"] == "runs/code_cad_arena/doe_test_run/doe_queue.json"
    queue_path = repo_root_with_reference / data["queue_path"]
    assert queue_path.exists()
    payload, jobs = nightly_cad.load_queue(queue_path)
    assert len(jobs) == 1


def test_doe_preview_route_answers_400_for_non_integer_seeds(client: TestClient):
    response = client.get(
        "/api/doe/preview",
        params={"instruments": "ocarina", "models": "claude-code-opus-5", "seeds": "0,zero"},
    )
    assert response.status_code == 400
    assert "seeds must be integers" in response.json()["detail"]


def test_doe_queue_route_answers_400_for_unknown_cost_model(client: TestClient, repo_root_with_reference: Path):
    # Tony (2026-09-14): validation errors are the client's to fix, so 400, not 500.
    response = client.post(
        "/api/doe/queue",
        json={"run_id": "doe_unknown_cost_route", "instruments": ["ocarina"], "models": ["openrouter-paid-a"], "levels": ["L1"], "seeds": [0]},
    )
    assert response.status_code == 400
    assert "openrouter-paid-a" in response.json()["detail"]
    assert not (repo_root_with_reference / "runs" / "code_cad_arena" / "doe_unknown_cost_route" / "doe_queue.json").exists()


@pytest.mark.parametrize("bad_run_id", ["../escaped", "a/b", ".."])
def test_doe_queue_route_answers_400_for_unsafe_run_id(client: TestClient, bad_run_id: str):
    response = client.post(
        "/api/doe/queue",
        json={"run_id": bad_run_id, "instruments": ["ocarina"], "models": ["claude-code-opus-5"], "levels": ["L1"], "seeds": [0]},
    )
    assert response.status_code == 400
    assert "run_id" in response.json()["detail"]


def test_doe_queue_route_refuses_to_replace_an_existing_queue_without_confirmation(
    client: TestClient, fake_registry: Path, repo_root_with_reference: Path
):
    # Claude UI review #780: a reused run name must not silently reset a queue the
    # nightly runner may already be working through.
    ArenaStudioService(registry_path=fake_registry, repo_root=repo_root_with_reference).set_task_approval("ocarina", True)
    # Two entrants: a nightly job needs at least two to compare.
    body = {"run_id": "doe_replace_run", "instruments": ["ocarina"], "models": ["claude-code-opus-5", "codex-gpt-5.6"], "levels": ["L1"], "seeds": [0]}
    assert client.post("/api/doe/queue", json=body).status_code == 200
    queue_path = repo_root_with_reference / "runs" / "code_cad_arena" / "doe_replace_run" / "doe_queue.json"
    before = queue_path.read_bytes()

    again = client.post("/api/doe/queue", json={**body, "budget_usd": 9.0})
    assert again.status_code == 409
    detail = again.json()["detail"]
    assert "runs/code_cad_arena/doe_replace_run/doe_queue.json" in detail
    assert str(repo_root_with_reference) not in detail
    assert queue_path.read_bytes() == before

    confirmed = client.post("/api/doe/queue", json={**body, "budget_usd": 9.0, "replace": True})
    assert confirmed.status_code == 200
    _payload, jobs = nightly_cad.load_queue(queue_path)
    assert jobs[0].budget_usd == 9.0


def test_doe_queue_route_validates_before_asking_to_replace(client: TestClient, repo_root_with_reference: Path):
    # Re-review #780 (non-blocking): an invalid request answers 400 even when the run
    # name already has a queue, instead of first asking to replace it.
    run_dir = repo_root_with_reference / "runs" / "code_cad_arena" / "doe_invalid_existing"
    run_dir.mkdir(parents=True)
    (run_dir / "doe_queue.json").write_text("{}", encoding="utf-8")
    response = client.post(
        "/api/doe/queue",
        json={"run_id": "doe_invalid_existing", "instruments": ["ocarina"], "models": ["openrouter-paid-a", "openrouter-paid-b"], "levels": ["L1"], "seeds": [0]},
    )
    assert response.status_code == 400
    assert "openrouter-paid-a" in response.json()["detail"]


def test_doe_routes_keep_500_for_unexpected_server_errors(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("telemetry store unreadable")

    monkeypatch.setattr(ArenaStudioService, "preview_doe_matrix", boom)
    monkeypatch.setattr(ArenaStudioService, "write_doe_queue", boom)
    preview = client.get("/api/doe/preview", params={"instruments": "ocarina", "models": "claude-code-opus-5"})
    queue = client.post(
        "/api/doe/queue",
        json={"run_id": "doe_boom", "instruments": ["ocarina"], "models": ["claude-code-opus-5"]},
    )
    assert (preview.status_code, queue.status_code) == (500, 500)


def test_doe_validation_error_is_still_a_value_error():
    # Existing callers that catch ValueError keep working.
    assert issubclass(doe.DoeValidationError, ValueError)


@pytest.mark.parametrize("override", [float("nan"), float("inf"), float("-inf")], ids=["nan", "+inf", "-inf"])
def test_write_doe_queue_refuses_non_finite_ceiling_before_writing(
    fake_registry: Path, repo_root_with_reference: Path, override: float
):
    service = ArenaStudioService(registry_path=fake_registry, repo_root=repo_root_with_reference)
    service.set_task_approval("ocarina", True)
    with pytest.raises(ValueError, match="openrouter-paid-a"):
        service.write_doe_queue(
            "doe_non_finite_run",
            ["ocarina"],
            ["openrouter-paid-a", "openrouter-paid-b"],
            levels=["L1"],
            seeds=[0],
            max_cost_usd_by_model={"openrouter-paid-a": override, "openrouter-paid-b": 1.0},
        )
    run_dir = repo_root_with_reference / "runs" / "code_cad_arena" / "doe_non_finite_run"
    assert not (run_dir / "doe_queue.json").exists()


def test_write_doe_queue_refuses_unknown_cost_model_not_silently_zero(
    fake_registry: Path, repo_root_with_reference: Path
):
    service = ArenaStudioService(registry_path=fake_registry, repo_root=repo_root_with_reference)
    with pytest.raises(ValueError, match="openrouter-paid-a"):
        service.write_doe_queue(
            "doe_unknown_cost_run",
            ["ocarina"],
            ["openrouter-paid-a", "openrouter-paid-b"],
            levels=["L1"],
            seeds=[0],
        )
    # Refused before any queue file is written for this run.
    run_dir = repo_root_with_reference / "runs" / "code_cad_arena" / "doe_unknown_cost_run"
    assert not (run_dir / "doe_queue.json").exists()


def test_write_doe_queue_accepts_unknown_cost_model_with_explicit_override(
    fake_registry: Path, repo_root_with_reference: Path
):
    service = ArenaStudioService(registry_path=fake_registry, repo_root=repo_root_with_reference)
    # #697 D4 gatekeeper: an existing reference image alone is no longer
    # enough, it must be explicitly approved.
    service.set_task_approval("ocarina", True)
    result = service.write_doe_queue(
        "doe_override_run",
        ["ocarina"],
        ["openrouter-paid-a", "openrouter-paid-b"],
        levels=["L1"],
        seeds=[0],
        max_cost_usd_by_model={"openrouter-paid-a": 1.0, "openrouter-paid-b": 1.0},
    )
    assert result["n_jobs"] == 1
    _payload, jobs = nightly_cad.load_queue(Path(result["queue_path"]))
    assert {e.max_cost_usd for e in jobs[0].entrants} == {1.0}


@pytest.mark.parametrize("bad_run_id", ["../escaped", "/tmp/escaped", "..", "a/b"])
def test_write_doe_queue_rejects_path_traversal_run_id(
    bad_run_id: str, fake_registry: Path, repo_root_with_reference: Path
):
    service = ArenaStudioService(registry_path=fake_registry, repo_root=repo_root_with_reference)
    with pytest.raises(ValueError, match="run_id"):
        service.write_doe_queue(
            bad_run_id,
            ["ocarina"],
            ["claude-code-opus-5", "codex-gpt-5.6"],
            levels=["L1"],
            seeds=[0],
        )
    # Nothing escaped the intended runs/code_cad_arena directory.
    assert not (repo_root_with_reference.parent / "escaped").exists()
    assert not Path("/tmp/escaped").exists()


def test_default_backend_cell_ids_are_golden():
    assert doe.expand_matrix(["ocarina"], ["model-a"], levels=["L1"])[0]["cell_id"] == "dd6a9f06265a35d4"
    assert doe.expand_matrix(["kora"], ["codex-gpt-5.6"], levels=["L2"],
                             context_tiers=["image"], seeds=[1],
                             backends=["openscad"])[0]["cell_id"] == "bd5289265aeb32cf"


def test_backend_axis_cross_product_and_live_drivers():
    cells = doe.expand_matrix(["ocarina"], ["model-a"], levels=["L1"],
                              backends=["openscad", "fusion", "fusion-live"],
                              driver_models=["gpt-5.6-sol", "gpt-6-sol"])
    assert len(cells) == 4
    assert len({c["cell_id"] for c in cells}) == 4
    live = [c for c in cells if c["backend"] == "fusion-live"]
    assert {c["model_id"] for c in live} == {"gpt-5.6-sol", "gpt-6-sol"}
    assert all(c["driver_model"] == c["model_id"] for c in live)


def test_mixed_backend_queue_roundtrip_and_live_kind(tmp_path):
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"fake-png")
    cells = doe.expand_matrix(["ocarina"], ["model-a"], levels=["L1"],
                              backends=["openscad", "solidworks", "fusion-live"],
                              driver_models=["gpt-5.6-sol"])
    payload, jobs = doe.build_nightly_queue(cells, reference_images={"ocarina": str(ref)})
    assert len(jobs) == 1
    assert {e.backend for e in jobs[0].entrants} == {"openscad", "solidworks", "fusion-live"}
    assert len({e.entrant_id for e in jobs[0].entrants}) == 3
    live = next(e for e in jobs[0].entrants if e.backend == "fusion-live")
    assert live.kind == "live" and live.model_id == "gpt-5.6-sol"
    jobs[0].validate()
    path = tmp_path / "queue.json"
    doe.write_queue_file(path, payload, jobs)
    assert nightly_cad.load_queue(path)[1] == jobs


def test_unavailable_backend_remains_visible_in_preview(monkeypatch):
    monkeypatch.setattr(doe.os, "name", "posix")
    original = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda p: False if str(p) == "/mnt/c" else original(p))
    cells = doe.annotate_matrix_with_estimates(doe.expand_matrix(
        ["ocarina"], ["claude-code-opus-5"], levels=["L1"], backends=["solidworks"]))
    assert len(cells) == 1
    assert cells[0]["availability"]["status"] == "unavailable"
    assert doe.matrix_summary(cells)["n_unavailable_cells"] == 1


def test_backend_specific_duration_does_not_borrow_openscad_history(tmp_path):
    store = tmp_path / "sessions.jsonl"
    store.write_text(json.dumps({"session_id": "s1", "agent_id": "claude-code-opus-5",
                                 "duration_seconds": 123, "telemetry": {}}) + "\n")
    cells = doe.expand_matrix(["ocarina"], ["claude-code-opus-5"], levels=["L1"],
                              backends=["openscad", "fusion"])
    annotated = doe.annotate_matrix_with_estimates(cells, telemetry_store=str(store))
    assert annotated[0]["estimate"]["duration_s"] == 123
    assert annotated[1]["estimate"]["duration_s"] is None
    assert doe.matrix_summary(annotated)["backends"] == ["fusion", "openscad"]


def test_preview_route_accepts_backends_and_rejects_unknown(client):
    params = {"instruments": "ocarina", "models": "claude-code-opus-5", "levels": "L1",
              "backends": "openscad,fusion-live", "driver_models": "gpt-5.6-sol"}
    response = client.get("/api/doe/preview", params=params)
    assert response.status_code == 200
    assert response.json()["summary"]["n_cells"] == 2
    assert client.get("/api/doe/preview", params={**params, "backends": "unknown"}).status_code == 400


def test_queue_route_preserves_selected_backend(client, repo_root_with_reference):
    client.post("/api/tasks/ocarina/approve?approved=true")
    response = client.post("/api/doe/queue", json={
        "run_id": "backend_queue", "instruments": ["ocarina"],
        "models": ["claude-code-opus-5"], "levels": ["L1"],
        "backends": ["openscad", "fusion-live"], "driver_models": ["gpt-5.6-sol"]})
    assert response.status_code == 200
    _, jobs = nightly_cad.load_queue(repo_root_with_reference / response.json()["queue_path"])
    assert [(e.backend, e.kind) for e in jobs[0].entrants] == [("openscad", "arena"), ("fusion-live", "live")]


def test_empty_backend_axis_and_live_studio_refused():
    with pytest.raises(doe.DoeValidationError):
        doe.expand_matrix(["ocarina"], ["model-a"], backends=[])
    with pytest.raises(doe.DoeValidationError, match="studio"):
        doe.expand_matrix(["ocarina"], ["model-a"], backends=["fusion-live"], driver_models=["gpt-5.6-sol"], context_tiers=["studio"])



@pytest.mark.parametrize("backends,drivers,message", [
    (["fusion-live"], None, "require explicit driver_models"),
    (["solidworks-live"], None, "require explicit driver_models"),
    (["openscad"], ["gpt-5.6-sol"], "only apply to live backends"),
])
def test_driver_axis_is_explicit_and_applicable(backends, drivers, message):
    with pytest.raises(doe.DoeValidationError, match=message):
        doe.expand_matrix(["ocarina"], ["claude-code-opus-5"],
                          backends=backends, driver_models=drivers)


@pytest.mark.parametrize("backends,drivers", [(["fusion-live"], None), (["openscad"], ["gpt-5.6-sol"])])
def test_driver_axis_validation_routes_fail_before_writing(client, repo_root_with_reference, backends, drivers):
    params = {"instruments": "ocarina", "models": "claude-code-opus-5", "backends": ",".join(backends)}
    if drivers is not None:
        params["driver_models"] = ",".join(drivers)
    assert client.get("/api/doe/preview", params=params).status_code == 400
    response = client.post("/api/doe/queue", json={
        "run_id": "invalid_driver", "instruments": ["ocarina"], "models": ["claude-code-opus-5"],
        "backends": backends, "driver_models": drivers})
    assert response.status_code == 400
    assert not (repo_root_with_reference / "runs" / "code_cad_arena" / "invalid_driver").exists()


def _windows_mount(monkeypatch, present):
    original = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda p: present if str(p) == "/mnt/c" else original(p))
    monkeypatch.setattr(doe.os, "name", "posix")


@pytest.mark.parametrize("present,status", [(False, "unavailable"), (True, "requires_preflight")])
def test_queue_payload_keeps_windows_backend_warnings(tmp_path, monkeypatch, present, status):
    _windows_mount(monkeypatch, present)
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"fake-png")
    cells = doe.expand_matrix(["ocarina"], ["model-a", "model-b"], levels=["L1"], backends=["solidworks"])
    payload, jobs = doe.build_nightly_queue(cells, reference_images={"ocarina": str(ref)})
    assert len(jobs) == 1 and len(jobs[0].entrants) == 2
    assert payload["backend_warnings"] == [{"backend": "solidworks", **doe.backend_availability("solidworks")}]
    assert payload["backend_warnings"][0]["status"] == status
    assert all(e.backend == "solidworks" for e in jobs[0].entrants)


@pytest.mark.parametrize("present,status", [(False, "unavailable"), (True, "requires_preflight")])
def test_queue_route_returns_and_persists_backend_warnings(client, repo_root_with_reference, monkeypatch, present, status):
    _windows_mount(monkeypatch, present)
    client.post("/api/tasks/ocarina/approve?approved=true")
    response = client.post("/api/doe/queue", json={
        "run_id": "warning_queue", "instruments": ["ocarina"],
        "models": ["claude-code-opus-5", "codex-gpt-5.6"], "levels": ["L1"], "backends": ["fusion"]})
    assert response.status_code == 200
    data = response.json()
    assert data["backend_warnings"] == [{"backend": "fusion", **doe.backend_availability("fusion")}]
    assert data["backend_warnings"][0]["status"] == status
    payload, jobs = nightly_cad.load_queue(repo_root_with_reference / data["queue_path"])
    assert payload["backend_warnings"] == data["backend_warnings"]
    assert len(jobs[0].entrants) == 2


@pytest.mark.parametrize("backend", ["cadquery", "build123d"])
def test_installed_cad_runtime_requires_sandbox_preflight(monkeypatch, backend):
    monkeypatch.setattr(doe.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(doe.shutil, "which", lambda name: "/usr/bin/" + name)
    cells = doe.annotate_matrix_with_estimates(doe.expand_matrix(
        ["ocarina"], ["model-a", "model-b"], levels=["L1"], backends=[backend]))
    assert all(c["availability"]["status"] == "requires_preflight" for c in cells)
    assert doe.matrix_summary(cells)["n_preflight_cells"] == 2


def test_backend_availability_is_memoized_per_backend(monkeypatch, tmp_path):
    calls = []
    def availability(backend):
        calls.append(backend)
        return {"status": "requires_preflight", "reason": "test"}
    monkeypatch.setattr(doe, "backend_availability", availability)
    cells = doe.expand_matrix(["ocarina"], ["model-a", "model-b"], backends=["solidworks", "fusion"])
    doe.annotate_matrix_with_estimates(cells)
    assert calls == ["solidworks", "fusion"]
    calls.clear()
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"fake-png")
    doe.build_nightly_queue(cells, reference_images={"ocarina": str(ref)})
    assert calls == ["fusion", "solidworks"]


def test_python_and_javascript_backend_vocabularies_match():
    import re
    source = (Path(__file__).parents[1] / "makerbench" / "arena_studio" / "static" / "app" / "lib" / "doe.js").read_text()
    values = re.search(r"export const BACKENDS = (\[.*?\]);", source).group(1)
    assert json.loads(values) == list(doe.BACKENDS)
