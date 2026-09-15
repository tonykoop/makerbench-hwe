"""Golden tests for Arena Studio export correctness (#716)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from makerbench.arena_studio.service import ArenaStudioService, _escape_markdown_cell


def _write_run(run_dir: Path, run_log: dict, revealed: list[dict] | None = None) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_log.json").write_text(json.dumps(run_log), encoding="utf-8")
    if revealed:
        (run_dir / "votes.revealed.jsonl").write_text(
            "\n".join(json.dumps(r) for r in revealed) + "\n", encoding="utf-8"
        )


@pytest.fixture
def service(tmp_path: Path) -> ArenaStudioService:
    return ArenaStudioService(repo_root=tmp_path)


def test_escape_markdown_cell_neutralizes_pipes_backticks_and_newlines():
    assert _escape_markdown_cell("model|evil") == "model\\|evil"
    # A backtick is substituted, not backslash-escaped: CommonMark code spans
    # don't process backslash escapes, so `\`` would still close a wrapping
    # `{cell}` span early (#718 R2 finding) -- substitution actually prevents
    # that, unlike the inert backslash-escape it replaces.
    assert _escape_markdown_cell("model`evil") == "model'evil"
    assert _escape_markdown_cell("model\nevil") == "model evil"
    assert _escape_markdown_cell("back\\slash") == "back\\\\slash"


def test_export_winners_empty_run_is_clean(service: ArenaStudioService, tmp_path: Path):
    run_dir = tmp_path / "runs" / "code_cad_arena" / "empty_run"
    _write_run(run_dir, {"config": {"model_ids": []}, "trials": []})

    result = service.export_winners(run_dir)
    assert result["success"] is True
    assert result["exported_count"] == 0
    assert result["winners"] == []


def test_export_report_empty_run_is_clean(service: ArenaStudioService, tmp_path: Path):
    run_dir = tmp_path / "runs" / "code_cad_arena" / "empty_run"
    _write_run(run_dir, {"config": {"model_ids": []}, "trials": []})

    report = service.export_report(run_dir)
    assert "Total Trials: 0" in report
    assert "Elo Leaderboard" in report


def test_export_winners_tie_between_two_ghosts_is_deterministic(
    service: ArenaStudioService, tmp_path: Path
):
    """Two zero-vote entrants both default to rank 999 -- the winner must be
    chosen by a stable, documented tiebreak, not trial-list insertion order.
    """
    run_dir = tmp_path / "runs" / "code_cad_arena" / "tie_run"
    scad_a = tmp_path / "a.scad"
    scad_b = tmp_path / "b.scad"
    scad_a.write_text("// a", encoding="utf-8")
    scad_b.write_text("// b", encoding="utf-8")
    run_log = {
        "config": {"model_ids": ["zeta-model", "alpha-model"]},
        "trials": [
            {
                "trial_id": "t-zeta",
                "model_id": "zeta-model",
                "instrument_id": "ocarina",
                "grade": {"compiled": True, "manifold": True},
                "result": {"artifacts": {"scad_path": str(scad_a)}},
            },
            {
                "trial_id": "t-alpha",
                "model_id": "alpha-model",
                "instrument_id": "ocarina",
                "grade": {"compiled": True, "manifold": True},
                "result": {"artifacts": {"scad_path": str(scad_b)}},
            },
        ],
    }
    _write_run(run_dir, run_log)  # no votes.revealed.jsonl -> both entrants are ghosts

    result_1 = service.export_winners(run_dir)
    result_2 = service.export_winners(run_dir)
    assert result_1["winners"] == result_2["winners"]
    assert result_1["exported_count"] == 1
    # Deterministic tiebreak sorts by model_id: "alpha-model" < "zeta-model".
    assert result_1["winners"][0]["model_id"] == "alpha-model"


def test_export_report_escapes_malicious_entrant_and_instrument_names(
    service: ArenaStudioService, tmp_path: Path
):
    run_dir = tmp_path / "runs" / "code_cad_arena" / "evil|run`name"
    run_log = {
        "config": {"model_ids": ["evil|model`name", "clean-model"]},
        "trials": [
            {
                "trial_id": "t1",
                "model_id": "evil|model`name",
                "instrument_id": "ocarina",
                "status": "completed",
                "grade": {"compiled": True, "manifold": True},
                "result": {"objective": {"objective_pass_rate": 1.0}},
            },
            {
                "trial_id": "t2",
                "model_id": "clean-model",
                "instrument_id": "ocarina",
                "status": "completed",
                "grade": {"compiled": True, "manifold": True},
                "result": {"objective": {"objective_pass_rate": 0.5}},
            },
        ],
    }
    revealed = [
        {
            "winner": "left",
            "instrument_id": "ocarina",
            "reveal": {
                "left": {"model_id": "evil|model`name"},
                "right": {"model_id": "clean-model"},
            },
        }
    ]
    _write_run(run_dir, run_log, revealed)

    report = service.export_report(run_dir)
    # No raw, unescaped pipe/backtick from the malicious name survives into
    # the Elo Leaderboard table this method builds (a literal backtick or
    # pipe would corrupt row structure). Every other section is covered by
    # test_export_report_escapes_hostile_entrant_in_every_section below.
    leaderboard_section = report.split("## Elo Leaderboard", 1)[1].split("##", 1)[0]
    assert "evil\\|model'name" in leaderboard_section
    assert "| evil|model`name |" not in leaderboard_section
    lines = [line for line in leaderboard_section.splitlines() if line.startswith("|")]
    header_cols = lines[0].count("|")
    for line in lines[2:]:  # skip header + separator
        assert line.count("|") == header_cols or "\\|" in line
        # Exactly the two backticks this method itself wraps the entrant in
        # -- proves the malicious name's own backtick couldn't smuggle a
        # third one in and close the code span early (#718 R2 finding).
        if "evil" in line:
            assert line.count("`") == 2


def test_export_report_shows_real_rating_and_draws_not_fixed_defaults(
    service: ArenaStudioService, tmp_path: Path
):
    """Regression: the report used to key leaderboard rows as `elo`/`ties`,
    which never matched the real `rating`/`draws` keys -- every report
    silently showed a fixed 1500 Elo and 0 ties regardless of actual score.
    """
    run_dir = tmp_path / "runs" / "code_cad_arena" / "rating_run"
    run_log = {"config": {"model_ids": ["model-a", "model-b"]}, "trials": []}
    revealed = [
        {
            "winner": "left",
            "instrument_id": "ocarina",
            "reveal": {"left": {"model_id": "model-a"}, "right": {"model_id": "model-b"}},
        },
        {
            "winner": "draw",
            "instrument_id": "ocarina",
            "reveal": {"left": {"model_id": "model-a"}, "right": {"model_id": "model-b"}},
        },
    ]
    _write_run(run_dir, run_log, revealed)

    report = service.export_report(run_dir)
    leaderboard = service.get_run_leaderboard(run_dir)["leaderboard"]
    winner_row = next(r for r in leaderboard if r["entrant"] == "model-a")

    assert winner_row["rating"] != 1500.0
    assert winner_row["draws"] == 1
    # Scoped to model-a's own Elo Leaderboard row line, not a substring check
    # against the whole report: a loose `in report` check would also match
    # the same-looking number if it happened to appear in the separate
    # Agreement Analysis section (#718 R2 finding).
    row_line = next(line for line in report.splitlines() if "model-a" in line)
    assert f"{winner_row['rating']:.1f}" in row_line
    # The row for model-a must show its real draw count, not a hardcoded 0.
    assert row_line.rstrip().endswith("| 1 |")


def _unescaped_pipes(line: str) -> int:
    return line.count("|") - line.count("\\|")


def test_export_report_escapes_hostile_entrant_in_every_section(
    service: ArenaStudioService, tmp_path: Path
):
    """sol, #767: the production report renderer, end to end, with hostile entrant
    text reaching every section -- title, Elo table, unrated list and the
    Agreement Analysis tables -- must never let that text add a column, break a
    row or open a code span.
    """
    hostile = "evil|model`x"
    ghost = "ghost|`y\nz"
    run_dir = tmp_path / "runs" / "code_cad_arena" / "hostile|run`name"
    run_log = {
        "config": {"model_ids": [hostile, "clean-model", ghost]},
        "trials": [
            {
                "trial_id": "t1",
                "model_id": hostile,
                "instrument_id": "ocarina",
                "status": "completed",
                "result": {"objective": {"objective_pass_rate": 1.0}},
            },
            {
                "trial_id": "t2",
                "model_id": "clean-model",
                "instrument_id": "ocarina",
                "status": "completed",
                "result": {"objective": {"objective_pass_rate": 0.5}},
            },
        ],
    }
    revealed = [
        {
            "winner": "left",
            "instrument_id": "ocarina",
            "reveal": {"left": {"model_id": hostile}, "right": {"model_id": "clean-model"}},
        }
    ]
    _write_run(run_dir, run_log, revealed)

    report = service.export_report(run_dir)

    # The hostile text reached every section, escaped.
    sections = report.split("\n## ")
    assert any(section.startswith("Elo Leaderboard") and "evil\\|model'x" in section for section in sections)
    assert any(section.startswith("Agreement Analysis") and "evil\\|model'x" in section for section in sections)
    assert "ghost\\|'y z" in report

    # Nowhere in the whole report does any raw piece of it survive.
    for raw in ("evil|model", "model`x", "ghost|", "`y", "hostile|run", "run`name"):
        assert raw not in report, raw

    # Every table row in every table keeps its header's column count.
    header_pipes = None
    for line in report.splitlines():
        if not line.startswith("|"):
            header_pipes = None
            continue
        if header_pipes is None:
            header_pipes = _unescaped_pipes(line)
            continue
        assert _unescaped_pipes(line) == header_pipes, line
