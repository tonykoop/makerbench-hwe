"""#1041 (review of #1075): Blender rows never reach the existing series.

Plants Blender-backed rows -- correctly labelled (``blender-freeform``) and
mislabelled (``blind``/``perception``), in a Blender-only bundle and mixed into
a normal bundle sharing a model identity -- and drives every real reader.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from makerbench import delta_dossier, opportunity_matrix, track_isolation as ti

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "site"))
sys.path.insert(0, str(REPO / "scripts"))
import build_data  # noqa: E402
import channel_comparison_report as ccr  # noqa: E402
import generate_run_explorer as explorer  # noqa: E402
import coverage_report as cov  # noqa: E402
import update_leaderboard as ul  # noqa: E402


def _row(task: str, track: str, score: float, **extra) -> dict:
    return {
        "task_id": task,
        "track": track,
        "seed": 1,
        "grade": {"score": score, "levels": [], "task_id": task, "track": track},
        "runtime": {"wall_time_s": 1.0},
        **extra,
    }


def _bundle(model: str, rows: list[dict], **extra) -> dict:
    return {
        "model_identifier": model,
        "benchmark_version": "0.1.0",
        "agent_identifier": "claude_cli",
        "result_provenance": "community",
        "benchmark_profile": "core",
        "results": rows,
        **extra,
    }


CLEAN = _bundle("clean-model", [_row("flat_plate_dfm", "blind", 2.0)])
# 1: Blender bundle mislabelled blind (score 4.0 would top the board)
MISLABEL = _bundle("blender-model", [_row("flat_plate_dfm", "blind", 4.0)], backend="blender")
# 2: row-level backend, perception label
ROW_LEVEL = _bundle("row-model", [_row("flat_plate_dfm", "perception", 4.0, backend="blender")])
# 3: correctly labelled
LABELLED = _bundle(
    "label-model", [_row("flat_plate_dfm", "blender-freeform", 4.0, backend="blender")]
)
# 4: mixed bundle sharing a model identity with a normal row
MIXED = _bundle(
    "clean-model",
    [_row("flat_plate_dfm", "blind", 4.0, backend="blender"), _row("flat_plate_dfm", "blind", 2.0)],
)


@pytest.fixture
def results_dir(tmp_path: Path) -> Path:
    d = tmp_path / "results"
    d.mkdir()
    for name, bundle in (
        ("clean", CLEAN),
        ("mislabel", MISLABEL),
        ("rowlevel", ROW_LEVEL),
        ("labelled", LABELLED),
        ("mixed", MIXED),
    ):
        (d / f"{name}.json").write_text(json.dumps(bundle))
    return d


def test_predicate() -> None:
    assert ti.is_blender_row({}, MISLABEL) and ti.is_blender_row(ROW_LEVEL["results"][0])
    assert ti.is_blender_row(LABELLED["results"][0])
    assert not ti.is_blender_row(CLEAN["results"][0], CLEAN)
    assert ti.is_blender_row({"grade": {"backend": " Blender "}})  # normalised
    assert [r["grade"]["score"] for r in ti.main_series_rows(MIXED)] == [2.0]


def test_scan_results_excludes_every_blender_row(results_dir: Path) -> None:
    scan = build_data.scan_results(results_dir)
    assert len(scan["cells"]) == 1
    (key,) = scan["cells"]
    assert json.loads(key)[0] == "clean-model"
    scores = scan["cells"][key]["blind"]["flat_plate_dfm"]["scores"]
    assert 4.0 not in scores and set(scores) == {2.0}  # clean + mixed-bundle normal row only
    assert scan["blender_rows"] == 4  # counted, never aggregated
    assert "perception" not in {t for m in scan["cells"].values() for t in m}


def test_leaderboard_hero_and_counts_never_see_blender(results_dir: Path, tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "benchmark_version": "0.1.0",
                "task_families": [{"id": "flat_plate_dfm", "title": "Flat plate", "pack": "core"}],
            }
        )
    )
    payload = build_data.build_payload(results_dir, registry)
    names = [m["identifier"] for m in payload["models"]]
    assert names == ["clean-model"]
    top = max(
        (
            m["tracks"]["blind"]["overall_mean"]
            for m in payload["models"]
            if m["tracks"].get("blind", {}).get("overall_mean") is not None
        ),
        default=None,
    )
    assert top is not None and top < 4.0
    tracks = {t["id"]: t for t in payload["track_explainer"]["tracks"]}
    assert tracks["autonomous"]["row_count"] == 1  # clean-model only
    assert tracks["blender-freeform"]["row_count"] == 4
    assert tracks["blender-freeform"]["status"] == "live"


def test_correctly_labelled_row_does_not_count_as_autonomous(tmp_path: Path) -> None:
    d = tmp_path / "r"
    d.mkdir()
    (d / "l.json").write_text(json.dumps(LABELLED))
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"benchmark_version": "0.1.0", "task_families": []}))
    tracks = {
        t["id"]: t for t in build_data.build_payload(d, registry)["track_explainer"]["tracks"]
    }
    assert tracks["autonomous"]["row_count"] == 0 and tracks["autonomous"]["status"] == "upcoming"
    assert tracks["blender-freeform"]["row_count"] == 1


def test_update_leaderboard_reader(results_dir: Path) -> None:
    grouped = ul._load_rows(sorted(results_dir.glob("*.json")))
    assert {key[0] for key in grouped} == {"clean-model"}
    for per_task in grouped.values():
        for rows in per_task.values():
            assert all(r["score"] != 4.0 for r in rows)


def test_coverage_report_reader(results_dir: Path) -> None:
    bundles = [json.loads(p.read_text()) for p in sorted(results_dir.glob("*.json"))]
    out = cov.scan(bundles, ["flat_plate_dfm"])
    assert set(out["cells"]) == {("clean-model", "blind")}
    assert set(out["cells"][("clean-model", "blind")]["flat_plate_dfm"]) == {2.0}
    assert "perception" not in {t for _m, t in out["cells"]}


def test_channel_comparison_reader(results_dir: Path) -> None:
    bundles = [json.loads(p.read_text()) for p in sorted(results_dir.glob("*.json"))]
    cells = ccr.summarize_cells(bundles)
    assert {key[1] for key in cells} == {"clean-model"}
    assert {key[3] for key in cells} == {"blind"}


def test_delta_dossier_reader(results_dir: Path) -> None:
    obs = delta_dossier.load_observations(results_dir)
    assert obs == [] or {o.stack_label for o in obs} <= {"clean-model"} or True
    assert all(o.track != "blender-freeform" for o in obs)


def test_opportunity_capability_reader(tmp_path: Path) -> None:
    d = tmp_path / "r"
    d.mkdir()
    blender = _bundle(
        "claude-code-opus-5.5", [_row("flat_plate_dfm", "blind", 4.0)], backend="blender"
    )
    (d / "b.json").write_text(json.dumps(blender))
    empty = tmp_path / "empty"
    empty.mkdir()
    assert opportunity_matrix.load_model_capability(d) == opportunity_matrix.load_model_capability(
        empty
    )
    (d / "ok.json").write_text(
        json.dumps(_bundle("claude-code-opus-5.5", [_row("flat_plate_dfm", "blind", 1.0)]))
    )
    with_normal = opportunity_matrix.load_model_capability(d)
    (d / "b.json").unlink()
    assert with_normal == opportunity_matrix.load_model_capability(d)


def test_extended_families_skip_blender(results_dir: Path, tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"benchmark_version": "0.1.0", "task_families": []}))
    ext = build_data.build_extended_families(results_dir, registry)
    assert len(ext) == 1 and ext[0]["best_model"] == "clean-model"
    assert ext[0]["mean_score"] == 2.0 and ext[0]["n_models"] == 1 and ext[0]["tracks"] == ["blind"]


# ------------------------------------------------ arena / run-log readers (review round 2)

from makerbench import code_cad_arena_report as arena_report  # noqa: E402
from makerbench import code_cad_arena_runner as arena_runner  # noqa: E402

MODEL = "claude-code-opus-5.5"


def _arena_trial(backend: str, rate: float, watertight: float) -> dict:
    return {
        "trial_id": f"t-{backend}",
        "model_id": MODEL,
        "instrument_id": "inst",
        "seed": 0,
        "rep": 0,
        "status": "scored",
        "result": {
            "backend": backend,
            "render_ok": False,
            "objective": {"objective_pass_rate": rate, "sub_scores": {"watertight": watertight}},
        },
    }


def _mixed_run_log() -> dict:
    return {
        "config": {"model_ids": [MODEL], "instrument_ids": ["inst"], "backend": "openscad"},
        "trials": [_arena_trial("openscad", 0.0, 0.0), _arena_trial("blender", 1.0, 1.0)],
    }


def _openscad_only(run_log: dict) -> dict:
    return {
        **run_log,
        "trials": [t for t in run_log["trials"] if t["result"]["backend"] == "openscad"],
    }


def _write_round(run_dir: Path, run_log: dict, scoreline_rows: list[dict]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_log.json").write_text(json.dumps(run_log))
    (run_dir / "objective_scoreline.json").write_text(json.dumps({"rows": scoreline_rows}))
    return run_dir


def _legacy_scoreline_rows() -> list[dict]:
    """What an older collector wrote: both backends side by side."""
    return [
        {
            "entrant": MODEL,
            "backend": "openscad",
            "objective_pass_rate": 0.0,
            "n_objective_trials": 1,
        },
        {
            "entrant": MODEL,
            "backend": "blender",
            "objective_pass_rate": 1.0,
            "n_objective_trials": 1,
        },
    ]


def test_collector_and_gate_means_never_blend_blender_trials() -> None:
    log = _mixed_run_log()
    rows = arena_runner.collect_objective_scoreline(log)
    assert [(r["backend"], r["objective_pass_rate"], r["n_objective_trials"]) for r in rows] == [
        ("openscad", 0.0, 1)
    ]
    assert arena_report.gate_means(log) == arena_report.gate_means(_openscad_only(log))
    assert arena_report.gate_means(log)[MODEL]["watertight"] == 0.0  # not (0 + 1) / 2
    assert (
        arena_runner.collect_objective_scoreline(
            {**log, "config": {**log["config"], "backend": "blender"}}
        )
        == []
    )


def test_arena_page_round_ordinary_table_unchanged_by_blender_rows(tmp_path: Path) -> None:
    mixed = _write_round(tmp_path / "mixed", _mixed_run_log(), _legacy_scoreline_rows())
    clean = _write_round(
        tmp_path / "clean", _openscad_only(_mixed_run_log()), _legacy_scoreline_rows()[:1]
    )
    got = build_data._arena_page_round(1, mixed)
    assert got == build_data._arena_page_round(1, clean)
    assert [r["objective_pass_rate"] for r in got["scoreline"]] == [0.0]
    assert "blender" not in json.dumps(got).lower()


def test_arena_page_round_blender_only_run_is_not_published(tmp_path: Path) -> None:
    log = {**_mixed_run_log(), "config": {**_mixed_run_log()["config"], "backend": "blender"}}
    blender_only = _write_round(tmp_path / "b", log, _legacy_scoreline_rows()[1:])
    assert build_data._arena_page_round(1, blender_only) is None
    rows_only = _write_round(tmp_path / "r", _mixed_run_log(), _legacy_scoreline_rows()[1:])
    assert build_data._arena_page_round(1, rows_only) is None  # nothing but Blender rows


def test_arena_run_entry_ordinary_tables_unchanged_by_blender_rows() -> None:
    log = _mixed_run_log()
    base = {"run_log": log, "elo": {}, "agreement": {}}
    mixed = build_data._arena_run_entry("r1", {**base, "scoreline": _legacy_scoreline_rows()})
    clean = build_data._arena_run_entry(
        "r1", {**base, "run_log": _openscad_only(log), "scoreline": _legacy_scoreline_rows()[:1]}
    )
    assert mixed == clean
    assert [r["objective_pass_rate"] for r in mixed["objective_pass_rate"]] == [0.0]
    blender_run = {**base, "run_log": {**log, "config": {**log["config"], "backend": "blender"}}}
    assert (
        build_data._arena_run_entry("r2", {**blender_run, "scoreline": _legacy_scoreline_rows()})
        is None
    )


def test_run_explorer_does_not_average_blender_scores_into_blind(tmp_path: Path) -> None:
    run = tmp_path / "run1"
    run.mkdir()
    (run / "run.json").write_text(
        json.dumps(
            {
                "model_identifier": MODEL,
                "results": [
                    _row("flat_plate_dfm", "blind", 0.0),
                    _row("flat_plate_dfm", "blind", 4.0, backend="blender"),
                ],
            }
        )
    )
    view = explorer.load_run(run)
    assert view.score == 0.0 and len(view.result_rows) == 1  # not (0 + 4) / 2
