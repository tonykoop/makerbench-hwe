import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from makerbench import nightly_cad
from makerbench.nightly_cad import (
    BudgetGuard,
    NightlyEntrant,
    NightlyExecutor,
    NightlyJob,
    _resume_budget,
    nightly_lease,
)
from makerbench.run_log_io import atomic_write_json, file_lock


def _image(path: Path) -> Path:
    Image.new("RGB", (320, 240), (90, 60, 30)).save(path)
    return path


def _queue(path: Path, reference: Path) -> Path:
    atomic_write_json(
        path,
        {
            "schema": "makerbench-nightly-cad-queue-v1",
            "jobs": [
                {
                    "job_id": "sambuca-night",
                    "instrument_id": "sambuca",
                    "reference_image": reference.as_posix(),
                    "budget_usd": 5.0,
                    "entrants": [
                        {
                            "entrant_id": "cadam-fable-image",
                            "kind": "cadam",
                            "model_id": "anthropic/claude-fable-5",
                            "max_cost_usd": 3.0,
                        },
                        {
                            "entrant_id": "codex-openscad",
                            "kind": "arena",
                            "model_id": "codex-gpt-5.6-sol",
                        },
                        {
                            "entrant_id": "codex-live",
                            "kind": "live",
                            "model_id": "gpt-5.6-sol",
                            "backend": "fusion-live",
                        },
                    ],
                }
            ],
        },
    )
    return path


def _registry(path: Path) -> Path:
    atomic_write_json(
        path,
        {
            "instruments": [
                {
                    "id": "sambuca",
                    "family": "strings",
                    "task_brief": "A historical boat-bodied harp.",
                    "envelope_mm": [800, 400, 250],
                    "min_bodies": 1,
                }
            ]
        },
    )
    return path


def _synthetic_handler(job, entrant, run_dir, _registry):
    if entrant.entrant_id == "codex-live":
        raise RuntimeError("connector unavailable")
    trial_id = f"{job.instrument_id}__seed{job.seed}__rep0__{entrant.entrant_id}"
    preview = run_dir / "render" / trial_id / "preview.png"
    preview.parent.mkdir(parents=True, exist_ok=True)
    _image(preview)
    entry = {
        "trial_id": trial_id,
        "instrument_id": job.instrument_id,
        "model_id": entrant.entrant_id,
        "seed": job.seed,
        "rep": 0,
        "provider": entrant.kind,
        "status": "scored",
        "attempts": 1,
        "result": {
            "render_ok": True,
            "artifacts": {"png_path": preview.as_posix(), "stl_path": None},
            "objective": {"objective_pass_rate": 0.75},
        },
    }
    run_log_path = run_dir / "run_log.json"
    with file_lock(run_log_path):
        payload = json.loads(run_log_path.read_text())
        payload["trials"].append(entry)
        atomic_write_json(run_log_path, payload)
    return 0.66 if entrant.kind == "cadam" else 0.0


def test_budget_guard_refuses_worst_case_overspend():
    guard = BudgetGuard(5.0, spent_usd=3.0)
    entrant = NightlyEntrant(entrant_id="paid", kind="cadam", model_id="fable", max_cost_usd=2.01)
    with pytest.raises(RuntimeError, match="remaining nightly budget"):
        guard.reserve(entrant)


def test_resume_budget_restores_spend_and_charges(tmp_path):
    atomic_write_json(
        tmp_path / "nightly-state.json",
        {
            "budget": {
                "limit_usd": 5.0,
                "spent_usd": 1.25,
                "charges": [{"entrant_id": "cadam", "cost_usd": 1.25}],
            }
        },
    )

    guard = _resume_budget(tmp_path, limit_usd=5.0)

    assert guard.spent_usd == 1.25
    assert guard.remaining_usd == 3.75
    assert guard.charges == [{"entrant_id": "cadam", "cost_usd": 1.25}]


def test_budget_guard_halts_after_actual_cost_exceeds_entrant_ceiling():
    guard = BudgetGuard(5.0)
    paid = NightlyEntrant(
        entrant_id="cadam",
        kind="cadam",
        model_id="fable",
        max_cost_usd=1.0,
    )
    guard.reserve(paid)

    violation = guard.charge(paid, 1.25)

    assert "exceeded" in violation
    assert guard.spent_usd == 1.25
    assert guard.halted_reason == violation
    with pytest.raises(RuntimeError, match="spending halted"):
        guard.reserve(
            NightlyEntrant(entrant_id="next", kind="arena", model_id="free")
        )


def test_single_seat_lease_is_nonblocking(tmp_path):
    def now():
        return datetime(2026, 7, 19, tzinfo=timezone.utc)

    path = tmp_path / "seat.lock"
    with nightly_lease(path, now=now):
        with pytest.raises(RuntimeError, match="another nightly CAD run"):
            with nightly_lease(path, now=now):
                pass


def test_nightly_run_survives_one_failed_entrant_and_builds_blind_bundle(tmp_path):
    def now():
        return datetime(2026, 7, 19, 7, 30, tzinfo=timezone.utc)

    queue = _queue(tmp_path / "queue.json", _image(tmp_path / "reference.png"))
    executor = NightlyExecutor(
        queue_path=queue,
        registry_path=_registry(tmp_path / "registry.json"),
        output_root=tmp_path / "runs",
        now=now,
        handlers={
            "cadam": _synthetic_handler,
            "arena": _synthetic_handler,
            "live": _synthetic_handler,
        },
    )
    result = executor.run()

    assert result["status"] == "votable"
    assert result["budget"]["spent_usd"] == 0.66
    assert [row["status"] for row in result["outcomes"]] == [
        "complete",
        "complete",
        "error",
    ]
    run_dir = Path(result["run_dir"])
    summary = json.loads((run_dir / "morning-summary.json").read_text())
    assert summary["votable"] is True
    assert summary["valid_candidate_count"] == 2
    assert summary["failed_candidate_count"] == 1
    assert len(summary["pair_files"]) == 1
    pair_html = (run_dir / "morning-vote" / summary["pair_files"][0]).read_text()
    assert "cadam-fable-image" not in pair_html
    assert "codex-openscad" not in pair_html
    assert not (run_dir / "reveal.json").exists()
    reveal = (
        run_dir.parent / ".makerbench-private" / f"{run_dir.name}-reveal.json"
    ).read_text()
    assert "cadam-fable-image" in reveal and "codex-openscad" in reveal

    saved_queue = json.loads(queue.read_text())
    assert saved_queue["jobs"][0]["status"] == "votable"
    assert executor.run() == {"status": "queue-empty"}


def test_paid_dispatch_crash_resumes_same_conversation_without_new_identity(tmp_path):
    def now():
        return datetime(2026, 7, 19, 7, 30, tzinfo=timezone.utc)

    class CrashExecutor(NightlyExecutor):
        calls = []
        crash = True

        def _run_cadam(
            self,
            job,
            entrant,
            run_dir,
            registry,
            *,
            dispatch,
            resume_only,
        ):
            self.calls.append((dispatch["conversation_id"], resume_only))
            if self.crash:
                raise SystemExit("simulated process death after durable dispatch")
            from makerbench.cadam_adapter import CadamRecoveryRequiredError

            raise CadamRecoveryRequiredError("persisted paid call still unresolved")

    queue = _queue(tmp_path / "queue.json", _image(tmp_path / "reference.png"))
    kwargs = {
        "queue_path": queue,
        "registry_path": _registry(tmp_path / "registry.json"),
        "output_root": tmp_path / "runs",
        "now": now,
        "handlers": {"arena": _synthetic_handler, "live": _synthetic_handler},
    }
    first = CrashExecutor(**kwargs)
    with pytest.raises(SystemExit, match="simulated process death"):
        first.run()

    saved_queue = json.loads(queue.read_text())
    run_dir = Path(saved_queue["jobs"][0]["run_dir"])
    state = json.loads((run_dir / "nightly-state.json").read_text())
    dispatch = state["budget"]["dispatches"]["cadam-fable-image"]
    assert dispatch["status"] == "dispatched"
    assert state["budget"]["reservations"]["cadam-fable-image"] == 3.0
    first_call = first.calls[0]
    CrashExecutor.calls.clear()

    second = CrashExecutor(**kwargs)
    second.crash = False
    result = second.run()

    assert first_call[1] is False
    assert second.calls[0] == (first_call[0], True)
    assert result["outcomes"][0]["status"] == "paid-dispatch-uncertain"


def test_nightly_build123d_threads_backend_into_trial_payload(tmp_path, monkeypatch):
    """#799: the production ``_run_arena`` path hands the entrant's backend to
    ``make_execute_trial``. Each trial payload's backend wins over the run config in
    the objective scoreline, so a missing argument would file every nightly build123d
    trial under OpenSCAD."""
    seen: dict = {}

    def fake_make_execute_trial(**kwargs):
        seen.update(kwargs)
        return lambda trial: {}

    def fake_run_orchestration(*, config, run_log_path, execute_trial):
        seen["config"] = config

    monkeypatch.setattr(nightly_cad.providers, "resolve_generator",
                        lambda model_id, **kwargs: (lambda request: ""))
    monkeypatch.setattr(nightly_cad.arena_runner, "make_execute_trial", fake_make_execute_trial)
    monkeypatch.setattr(nightly_cad, "run_orchestration", fake_run_orchestration)
    entrant = NightlyEntrant(entrant_id="stub-build123d", kind="arena", model_id="stub-a",
                             backend="build123d")
    job = NightlyJob(job_id="b123d-night", instrument_id="sambuca",
                     reference_image=_image(tmp_path / "reference.png").as_posix(),
                     entrants=[entrant, entrant])
    executor = NightlyExecutor(queue_path=tmp_path / "queue.json",
                               registry_path=_registry(tmp_path / "registry.json"),
                               output_root=tmp_path / "runs")

    executor._run_arena(job, entrant, tmp_path, registry={})

    assert seen["backend"] == "build123d"
    assert seen["compiler"] is nightly_cad.arena_runner.compiler_for_backend("build123d")
    assert seen["config"].backend == "build123d"


def test_failed_cadam_dispatch_persists_no_token_in_run_log_or_state(tmp_path, monkeypatch):
    """#660: a transport error that echoes the fresh bearer token must not reach run_log/state."""

    import urllib.error

    from makerbench import cadam_adapter

    token = "fresh-bearer-SECRET-token"

    def transport(method, url, headers, body):
        if "/auth/v1/token" in url:
            return json.dumps({"access_token": token}).encode()
        if method == "GET":
            return b"[]"
        if "/api/parametric-chat" in url:
            raise urllib.error.HTTPError(url, 401, f"rejected Authorization: Bearer {token}", {}, None)
        return b"{}"

    real_client = cadam_adapter.CadamClient
    monkeypatch.setattr(
        nightly_cad, "CadamClient", lambda config: real_client(config, transport=transport, sleep_fn=lambda _s: None)
    )
    for key, value in {
        "CADAM_USER_ID": "user-1",
        "SUPABASE_SERVICE_ROLE_KEY": "service-role",
        "CADAM_BOT_EMAIL": "bot@example.test",
        "CADAM_BOT_PASSWORD": "bot-password",
        "VITE_SUPABASE_ANON_KEY": "anon-key",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("CADAM_ACCESS_TOKEN", raising=False)

    queue = _queue(tmp_path / "queue.json", _image(tmp_path / "reference.png"))
    executor = NightlyExecutor(
        queue_path=queue,
        registry_path=_registry(tmp_path / "registry.json"),
        output_root=tmp_path / "runs",
        now=lambda: datetime(2026, 7, 19, 7, 30, tzinfo=timezone.utc),
        handlers={"arena": _synthetic_handler, "live": _synthetic_handler},
    )
    result = executor.run()
    run_dir = Path(result["run_dir"])
    persisted = "".join(p.read_text(errors="replace") for p in run_dir.rglob("*.json"))
    assert token not in persisted and token not in json.dumps(result, default=str)
    assert "HTTP 401" in persisted  # the redacted reason is what gets recorded


def _matchup_queue(path: Path, reference: Path, **job) -> Path:
    atomic_write_json(path, {"schema": "makerbench-nightly-cad-queue-v1", "jobs": [{
        "job_id": "sambuca-matchup", "instrument_id": "sambuca", "reference_image": reference.as_posix(),
        "budget_usd": 2.5,
        "entrants": [
            {"entrant_id": "cadam-opus-5.5", "kind": "cadam", "model_id": "anthropic/claude-opus-5.5", "max_cost_usd": 2.0},
            {"entrant_id": "freecad-opus-5.5", "kind": "arena", "model_id": "claude-code-opus-5.5",
             "backend": "freecad", "context_tier": "image"},
            {"entrant_id": "codex-live", "kind": "live", "model_id": "codex-gpt-6.1-sol", "backend": "solidworks-live"},
            {"entrant_id": "fusion-sonnet-5.5", "kind": "live", "model_id": "claude-code-sonnet-5.5", "backend": "fusion-live"},
        ],
        **job,
    }]})
    return path


def test_route_labels_name_runtime_model_and_backend():
    label = lambda **e: nightly_cad.route_label(NightlyEntrant.from_dict({"entrant_id": "x", **e}))  # noqa: E731
    assert label(kind="cadam", model_id="anthropic/claude-opus-5.5") == "CADAM · OpenRouter · Opus 5.5"
    assert label(kind="arena", model_id="claude-code-opus-5.5", backend="freecad") == "Claude Code · Opus 5.5 · FreeCAD"
    assert label(kind="live", model_id="codex-gpt-6.1-sol", backend="solidworks-live") == "Codex · GPT-6.1 Sol · SolidWorks live bridge"
    assert label(kind="live", model_id="gpt-6.1-sol", backend="solidworks-live") == "Codex · GPT-6.1 Sol · SolidWorks live bridge"
    assert label(kind="live", model_id="claude-code-sonnet-5.5", backend="fusion-live") == "Claude Code · Sonnet 5.5 · Fusion live bridge"


def test_deliverable_is_recorded_and_assembly_nights_are_refused(tmp_path):
    reference = _image(tmp_path / "reference.png")
    job = NightlyJob.from_dict({"job_id": "j", "instrument_id": "sambuca", "reference_image": reference.as_posix(),
                                "entrants": [{"entrant_id": "a", "model_id": "a"}, {"entrant_id": "b", "model_id": "b"}]})
    job.validate()
    assert job.deliverable == "single" and job.as_dict()["deliverable"] == "single"
    for value, message in (("assembly", "not supported by the nightly runner yet"), ("kit", "unknown nightly deliverable")):
        job.deliverable = value
        with pytest.raises(ValueError, match=message):
            job.validate()


def test_a_matchup_night_writes_what_morning_review_reads(tmp_path):
    from makerbench.arena_studio import control_room as cr

    photo = tmp_path / "SambucaPhoto.jpg"
    Image.new("RGB", (320, 240), (90, 60, 30)).save(photo, format="JPEG")
    queue = _matchup_queue(tmp_path / "queue.json", photo)

    def handler(job, entrant, run_dir, registry):
        if entrant.entrant_id == "codex-live":
            raise RuntimeError("connector unavailable")
        return _synthetic_handler(job, entrant, run_dir, registry)

    executor = NightlyExecutor(queue_path=queue, registry_path=_registry(tmp_path / "registry.json"),
                               output_root=tmp_path / "runs", now=lambda: datetime(2026, 10, 18, 7, 30, tzinfo=timezone.utc),
                               handlers={"cadam": handler, "arena": handler, "live": handler})
    result = executor.run()
    run_dir = Path(result["run_dir"])
    brief = json.loads((run_dir / "brief.json").read_text())
    assert brief["night"] == "2026-10-18" and brief["deliverable"] == "single" and brief["reference_image"] == "reference.jpg"
    assert brief["brief"] == "A historical boat-bodied harp." and "entrants" not in brief
    assert (run_dir / "reference.jpg").read_bytes() == photo.read_bytes()

    view = cr.review_view(run_dir, tmp_path)
    routes = {d["entrant"]: d["route"] for d in view["designs"]}
    assert routes == {
        "cadam-opus-5.5": "CADAM · OpenRouter · Opus 5.5",
        "freecad-opus-5.5": "Claude Code · Opus 5.5 · FreeCAD",
        "codex-live": "Codex · GPT-6.1 Sol · SolidWorks live bridge",  # the failed entrant is labelled too
        "fusion-sonnet-5.5": "Claude Code · Sonnet 5.5 · Fusion live bridge",
    }
    assert view["brief"]["has_reference"] is True and cr.discover_nights(tmp_path / "runs") == [run_dir.resolve()]
    summary = json.loads((run_dir / "morning-summary.json").read_text())
    for name in summary["pair_files"]:
        page = (run_dir / "morning-vote" / name).read_text()
        assert "Opus" not in page and "Sonnet" not in page and "OpenRouter" not in page  # vote pages stay blind


def test_an_unroutable_entrant_fails_alone_and_the_night_still_finishes(tmp_path):
    photo = _image(tmp_path / "reference.png")
    queue = tmp_path / "queue.json"
    atomic_write_json(queue, {"schema": "makerbench-nightly-cad-queue-v1", "jobs": [{
        "job_id": "night", "instrument_id": "sambuca", "reference_image": photo.as_posix(), "budget_usd": 2.0,
        "entrants": [
            {"entrant_id": "mystery", "kind": "arena", "model_id": "unprefixed-model"},   # no provider prefix
            {"entrant_id": "bad-live", "kind": "live", "model_id": "codex-", "backend": "solidworks-live"},
            {"entrant_id": "good-a", "kind": "cadam", "model_id": "anthropic/claude-opus-5.5", "max_cost_usd": 1.0},
            {"entrant_id": "good-b", "kind": "cadam", "model_id": "anthropic/claude-opus-5.5", "max_cost_usd": 1.0},
        ]}]})
    executor = NightlyExecutor(queue_path=queue, registry_path=_registry(tmp_path / "registry.json"),
                               output_root=tmp_path / "runs", handlers={"cadam": _synthetic_handler})
    result = executor.run()  # the arena and live entrants fail in dispatch; labelling must not raise
    assert [row["status"] for row in result["outcomes"]] == ["error", "error", "complete", "complete"]
    assert result["status"] == "votable" and (Path(result["run_dir"]) / "morning-summary.json").is_file()
    rows = json.loads((Path(result["run_dir"]) / "run_log.json").read_text())["trials"]
    routes = {row["model_id"]: row.get("meta", {}).get("route") for row in rows}
    assert routes["mystery"] == "arena · unprefixed-model · OpenSCAD (route not recognized)"
    assert routes["bad-live"] == "live · codex- · SolidWorks live bridge (route not recognized)"
    assert routes["good-a"] == "CADAM · OpenRouter · Opus 5.5"


def test_cadam_deliverable_is_named_and_linked_for_morning_review(tmp_path, monkeypatch):
    from makerbench.cadam_adapter import CadamResult

    class FakeCadam:
        def __init__(self, config):
            self.config = config

        def generate(self, *, prompt, reference_image, output_dir, conversation_id, resume_only):
            output_dir.mkdir(parents=True, exist_ok=True)
            scad = output_dir / "candidate.scad"
            scad.write_text("cube([40, 20, 10]);\n")
            return CadamResult(conversation_id=conversation_id, scad_path=scad, stream_path=output_dir / "s.bin",
                               prepared_image_path=reference_image, image_sha256="0" * 64, cost_usd=0.4,
                               model=self.config.model)

    monkeypatch.setattr(nightly_cad, "CadamClient", FakeCadam)
    photo = _image(tmp_path / "reference.png")
    queue = tmp_path / "queue.json"
    atomic_write_json(queue, {"schema": "makerbench-nightly-cad-queue-v1", "jobs": [{
        "job_id": "sambuca-matchup", "instrument_id": "sambuca", "reference_image": photo.as_posix(), "budget_usd": 2.0,
        "entrants": [{"entrant_id": "cadam-opus", "kind": "cadam", "model_id": "anthropic/claude-opus-5.5", "max_cost_usd": 2.0},
                     {"entrant_id": "other", "kind": "arena", "model_id": "stub"}]}]})
    executor = NightlyExecutor(queue_path=queue, registry_path=_registry(tmp_path / "registry.json"),
                               output_root=tmp_path / "runs", handlers={"arena": _synthetic_handler})
    result = executor.run()
    run_dir = Path(result["run_dir"])
    named = run_dir / "cadam" / "cadam-opus" / "sambuca_base_r1" / "sambuca_base_r1_model.scad"
    assert named.read_text() == "cube([40, 20, 10]);\n"
    from makerbench.arena_studio import control_room as cr

    design = next(d for d in cr.review_view(run_dir, tmp_path)["designs"] if d["entrant"] == "cadam-opus")
    assert design["native"]["file"] == "sambuca_base_r1_model.scad"
    assert nightly_cad.night_stem(NightlyJob(job_id="j", instrument_id="tongue_drum_wood_assembly",
                                             reference_image="", entrants=[])) == "tongue-drum-wood-assembly_base_r1"


def test_studio_can_review_nights_from_the_nightly_output_root(tmp_path):
    from fastapi.testclient import TestClient

    from makerbench.arena_studio import create_studio_app

    photo = _image(tmp_path / "reference.png")
    queue = _matchup_queue(tmp_path / "queue.json", photo)
    output_root = tmp_path / "arena-nightly" / "runs"
    handlers = {"cadam": _synthetic_handler, "arena": _synthetic_handler,
                "live": lambda *a: (_ for _ in ()).throw(RuntimeError("bridge down"))}
    run_id = NightlyExecutor(queue_path=queue, registry_path=_registry(tmp_path / "registry.json"),
                             output_root=output_root, handlers=handlers).run()["run_id"]
    repo = tmp_path / "repo"
    (repo / "runs").mkdir(parents=True)
    default = TestClient(create_studio_app(registry_path=tmp_path / "registry.json", repo_root=repo),
                         base_url="http://127.0.0.1")
    assert default.get("/api/review/nights").json()["nights"] == []  # the repo's runs/ has no nights
    pointed = TestClient(create_studio_app(registry_path=tmp_path / "registry.json", repo_root=repo,
                                           control_room_runs_root=output_root), base_url="http://127.0.0.1")
    assert [n["run_id"] for n in pointed.get("/api/review/nights").json()["nights"]] == [run_id]
    view = pointed.get(f"/api/review/{run_id}").json()
    assert {d["route"] for d in view["designs"]} >= {"CADAM · OpenRouter · Opus 5.5", "Claude Code · Sonnet 5.5 · Fusion live bridge"}


def test_definite_cadam_failure_reaches_morning_summary(tmp_path):
    from makerbench.cadam_adapter import CadamStreamFailedError

    reason = "Insufficient credits.\nModel call failed: No output generated."

    def failed(*args):
        raise CadamStreamFailedError(reason)

    executor = NightlyExecutor(queue_path=_queue(tmp_path / 'queue.json', _image(tmp_path / 'ref.png')),
                              registry_path=_registry(tmp_path / 'registry.json'), output_root=tmp_path / 'runs',
                              handlers={'cadam': failed, 'arena': _synthetic_handler, 'live': _synthetic_handler})
    result = executor.run()
    assert result['outcomes'][0]['status'] == 'failed'
    assert result['outcomes'][0]['error'] == reason
    assert 'safety_block' not in result['morning']
    summary = json.loads((Path(result['run_dir']) / 'morning-summary.json').read_text())
    assert any(row['error'] == reason for row in summary['failures'])
