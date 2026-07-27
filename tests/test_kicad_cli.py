"""Tests for the optional-local KiCad CLI ERC/DRC report harness."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from makerbench.kicad_cli import run_kicad_erc_drc


def test_skips_requested_checks_when_kicad_cli_is_absent(monkeypatch, tmp_path):
    monkeypatch.setattr("makerbench.kicad_cli.shutil.which", lambda _name: None)

    report = run_kicad_erc_drc(
        pcb_path=tmp_path / "board.kicad_pcb",
        sch_path=tmp_path / "board.kicad_sch",
    )

    assert report.available is False
    assert report.skipped is True
    assert report.passed is None
    assert [check.check for check in report.checks] == ["erc", "drc"]
    assert all(check.status == "skipped" for check in report.checks)
    assert report.to_dict()["violation_count"] == 0


def test_runs_erc_and_drc_and_normalizes_json_violations(monkeypatch, tmp_path):
    monkeypatch.setattr("makerbench.kicad_cli.shutil.which", lambda _name: "/usr/bin/kicad-cli")
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        report_path = Path(command[command.index("--output") + 1])
        if command[1:3] == ["sch", "erc"]:
            report_path.write_text(json.dumps({"violations": []}), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        report_path.write_text(
            json.dumps(
                {
                    "violations": [
                        {
                            "severity": "error",
                            "code": "clearance",
                            "description": "Track too close to pad",
                            "location": {"x": 12.5, "y": 4.0},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 5, stdout="", stderr="1 DRC violation")

    monkeypatch.setattr("makerbench.kicad_cli.subprocess.run", fake_run)

    report = run_kicad_erc_drc(
        pcb_path=tmp_path / "board.kicad_pcb",
        sch_path=tmp_path / "board.kicad_sch",
        work_dir=tmp_path / "reports",
    )

    assert report.available is True
    assert report.skipped is False
    assert report.passed is False
    assert report.violation_count == 1
    assert [cmd[1:3] for cmd in commands] == [["sch", "erc"], ["pcb", "drc"]]
    assert report.checks[0].status == "passed"
    assert report.checks[1].status == "violations"
    violation = report.checks[1].violations[0]
    assert violation.check == "drc"
    assert violation.severity == "error"
    assert violation.code == "clearance"
    assert violation.message == "Track too close to pad"
    assert violation.location == "12.5,4.0"


def test_nonzero_without_parseable_report_is_tool_error(monkeypatch, tmp_path):
    monkeypatch.setattr("makerbench.kicad_cli.shutil.which", lambda _name: "/usr/bin/kicad-cli")

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 2, stdout="not json", stderr="failed")

    monkeypatch.setattr("makerbench.kicad_cli.subprocess.run", fake_run)

    report = run_kicad_erc_drc(pcb_path=tmp_path / "board.kicad_pcb", work_dir=tmp_path)

    assert report.passed is False
    assert report.checks[0].status == "tool_error"
    assert "parseable violation report" in report.checks[0].error
