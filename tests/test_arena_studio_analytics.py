"""Golden tests for Arena Studio agreement analytics (#699 D1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from makerbench.arena_studio import analytics
from makerbench.arena_studio import create_studio_app
from makerbench.code_cad_arena import Vote


def _vote(left: str, right: str, winner: str, instrument_id: str = "ocarina") -> Vote:
    return Vote(left=left, right=right, winner=winner, instrument_id=instrument_id)


VOTES_FIXTURE = [
    _vote("model-a", "model-b", "left"),
    _vote("model-a", "model-b", "left"),
    _vote("model-a", "model-c", "left"),
    _vote("model-b", "model-c", "right"),
    _vote("model-a", "model-b", "right"),
]
ENTRANTS_FIXTURE = ["model-a", "model-b", "model-c", "model-ghost"]


def test_bootstrap_elo_ci_is_seed_reproducible():
    ci_1 = analytics.bootstrap_elo_ci(VOTES_FIXTURE, ENTRANTS_FIXTURE, seed=42, n_resamples=200)
    ci_2 = analytics.bootstrap_elo_ci(VOTES_FIXTURE, ENTRANTS_FIXTURE, seed=42, n_resamples=200)
    assert ci_1 == ci_2

    ci_other_seed = analytics.bootstrap_elo_ci(
        VOTES_FIXTURE, ENTRANTS_FIXTURE, seed=7, n_resamples=200
    )
    # Different seeds may coincidentally match on a tiny fixture, but the
    # rated entrants' bounds should at least be present and ordered.
    for entrant in ("model-a", "model-b", "model-c"):
        bounds = ci_1[entrant]
        assert bounds["lo"] is not None and bounds["hi"] is not None
        assert bounds["lo"] <= bounds["hi"]
        assert ci_other_seed[entrant]["lo"] is not None


def test_bootstrap_elo_ci_ghost_entrant_has_no_bounds():
    ci = analytics.bootstrap_elo_ci(VOTES_FIXTURE, ENTRANTS_FIXTURE, seed=0, n_resamples=100)
    assert ci["model-ghost"]["lo"] is None
    assert ci["model-ghost"]["hi"] is None


def test_bootstrap_elo_ci_zero_resamples_is_all_none():
    ci = analytics.bootstrap_elo_ci(VOTES_FIXTURE, ENTRANTS_FIXTURE, seed=0, n_resamples=0)
    assert all(bounds["lo"] is None for bounds in ci.values())


def test_leaderboard_with_ci_matches_published_ratings_and_adds_bounds(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    revealed = run_dir / "votes.revealed.jsonl"
    lines = []
    for v in VOTES_FIXTURE:
        lines.append(
            json.dumps(
                {
                    "winner": v.winner,
                    "instrument_id": v.instrument_id,
                    "reveal": {"left": {"model_id": v.left}, "right": {"model_id": v.right}},
                }
            )
        )
    revealed.write_text("\n".join(lines) + "\n", encoding="utf-8")

    run_log = {"config": {"model_ids": ["model-a", "model-b", "model-c", "model-ghost"]}}

    from makerbench.code_cad_arena import build_elo_leaderboard
    from makerbench.code_cad_arena_runner import votes_to_elo_votes

    published = build_elo_leaderboard(
        votes_to_elo_votes(revealed), entrants=run_log["config"]["model_ids"]
    )
    published_by_entrant = {row["entrant"]: row["rating"] for row in published["leaderboard"]}

    with_ci = analytics.leaderboard_with_ci(run_dir, run_log, seed=1, n_resamples=100)

    assert "model-ghost" not in [row["entrant"] for row in with_ci["leaderboard"]]
    assert "model-ghost" in with_ci["unrated_entrants"]
    for row in with_ci["leaderboard"]:
        # The published rating must not move — analytics only annotates it.
        assert row["rating"] == published_by_entrant[row["entrant"]]
        assert "ci_low" in row and "ci_high" in row
        if row["games"] > 0:
            assert row["ci_low"] <= row["rating"] + 1e-6 or row["ci_low"] is not None
    assert with_ci["ci_method"]["seed"] == 1


def test_agreement_with_caveats_flags_small_sample():
    rows = [
        {"entrant": "model-a", "subjective_elo": 1600, "objective_pass_rate": 0.9},
        {"entrant": "model-b", "subjective_elo": 1500, "objective_pass_rate": 0.5},
        {"entrant": "model-c", "subjective_elo": 1400, "objective_pass_rate": 0.3},
    ]
    summary = analytics.agreement_with_caveats(rows)
    assert summary["agreement"]["n"] == 3
    assert summary["agreement"]["small_sample"] is True
    assert "n=3" in summary["agreement"]["caveat"]
    # rho itself must be the unmodified value build_agreement_summary produces.
    from makerbench.code_cad_agreement import build_agreement_summary

    unmodified = build_agreement_summary(rows)
    assert summary["agreement"]["rho"] == unmodified["agreement"]["rho"]


def test_agreement_with_caveats_no_flag_when_n_is_large():
    rows = [
        {"entrant": f"model-{i}", "subjective_elo": 1500 + i, "objective_pass_rate": i / 10}
        for i in range(10)
    ]
    summary = analytics.agreement_with_caveats(rows)
    assert summary["agreement"]["n"] == 10
    assert summary["agreement"]["small_sample"] is False
    assert "caveat" not in summary["agreement"]


def test_find_outliers_picks_large_rank_deltas():
    rows = [
        {"entrant": "model-a", "subjective_elo": 1600, "objective_pass_rate": 0.1},
        {"entrant": "model-b", "subjective_elo": 1500, "objective_pass_rate": 0.5},
        {"entrant": "model-c", "subjective_elo": 1400, "objective_pass_rate": 0.9},
    ]
    summary = analytics.agreement_with_caveats(rows)
    outliers = analytics.find_outliers(summary, threshold=1.5)
    assert {row["entrant"] for row in outliers} == {"model-a", "model-c"}


def test_per_family_breakdown_slices_by_registry_family(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    revealed = run_dir / "votes.revealed.jsonl"
    records = [
        {
            "winner": "left",
            "instrument_id": "ocarina",
            "reveal": {"left": {"model_id": "model-a"}, "right": {"model_id": "model-b"}},
        },
        {
            "winner": "right",
            "instrument_id": "kora",
            "reveal": {"left": {"model_id": "model-a"}, "right": {"model_id": "model-b"}},
        },
    ]
    revealed.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    run_log = {
        "config": {"model_ids": ["model-a", "model-b"]},
        "trials": [
            {
                "instrument_id": "ocarina",
                "model_id": "model-a",
                "status": "completed",
                "result": {"objective": {"objective_pass_rate": 1.0}},
            },
            {
                "instrument_id": "ocarina",
                "model_id": "model-b",
                "status": "completed",
                "result": {"objective": {"objective_pass_rate": 0.0}},
            },
            {
                "instrument_id": "kora",
                "model_id": "model-a",
                "status": "completed",
                "result": {"objective": {"objective_pass_rate": 0.0}},
            },
            {
                "instrument_id": "kora",
                "model_id": "model-b",
                "status": "completed",
                "result": {"objective": {"objective_pass_rate": 1.0}},
            },
        ],
    }
    registry_tasks = [
        {"id": "ocarina", "family": "woodwind"},
        {"id": "kora", "family": "strings"},
    ]

    breakdown = analytics.per_family_breakdown(run_dir, run_log, registry_tasks)

    assert set(breakdown) == {"woodwind", "strings"}
    assert breakdown["woodwind"]["n_trials"] == 2
    woodwind_leaders = {row["entrant"]: row for row in breakdown["woodwind"]["leaderboard"]}
    assert woodwind_leaders["model-a"]["wins"] == 1
    strings_leaders = {row["entrant"]: row for row in breakdown["strings"]["leaderboard"]}
    assert strings_leaders["model-b"]["wins"] == 1


@pytest.fixture
def fake_registry(tmp_path: Path) -> Path:
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(
        json.dumps(
            {
                "instruments": [
                    {"id": "ocarina", "family": "woodwind"},
                    {"id": "kora", "family": "strings"},
                ]
            }
        ),
        encoding="utf-8",
    )
    return reg_path


@pytest.fixture
def fake_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "arena_run"
    run_dir.mkdir()
    (run_dir / "run_log.json").write_text(
        json.dumps(
            {
                "config": {"model_ids": ["model-a", "model-b"], "instruments": ["ocarina"]},
                "trials": [
                    {
                        "instrument_id": "ocarina",
                        "model_id": "model-a",
                        "status": "completed",
                        "result": {"objective": {"objective_pass_rate": 1.0}},
                    },
                    {
                        "instrument_id": "ocarina",
                        "model_id": "model-b",
                        "status": "completed",
                        "result": {"objective": {"objective_pass_rate": 0.0}},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "votes.revealed.jsonl").write_text(
        json.dumps(
            {
                "winner": "left",
                "instrument_id": "ocarina",
                "reveal": {"left": {"model_id": "model-a"}, "right": {"model_id": "model-b"}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return run_dir


@pytest.fixture
def client(fake_run: Path, fake_registry: Path, tmp_path: Path) -> TestClient:
    studio_app = create_studio_app(
        default_run_dir=fake_run, registry_path=fake_registry, repo_root=tmp_path
    )
    # Loopback base URL: B1's TrustedHost guard rejects TestClient's default "testserver".
    return TestClient(studio_app, base_url="http://127.0.0.1")


def test_leaderboard_ci_route(client: TestClient, fake_run: Path):
    response = client.get(f"/api/runs/{fake_run.name}/leaderboard/ci?seed=3&n_resamples=50")
    assert response.status_code == 200
    data = response.json()
    assert data["leaderboard"]
    assert all("ci_low" in row for row in data["leaderboard"])
    assert data["ci_method"]["seed"] == 3


def test_agreement_detailed_route(client: TestClient, fake_run: Path):
    response = client.get(f"/api/runs/{fake_run.name}/agreement/detailed")
    assert response.status_code == 200
    data = response.json()
    assert "small_sample" in data["agreement"]


def test_agreement_families_route(client: TestClient, fake_run: Path):
    response = client.get(f"/api/runs/{fake_run.name}/agreement/families")
    assert response.status_code == 200
    data = response.json()
    assert "woodwind" in data["families"]
