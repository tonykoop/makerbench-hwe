import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from makerbench.nightly_cad import (
    BudgetGuard,
    NightlyEntrant,
    NightlyExecutor,
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
