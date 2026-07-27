"""Tests for the PCBA ERC/DRC grading harness (electrical half of the dual gate)."""

from __future__ import annotations

import json

from makerbench.pcba_erc_drc import (
    copper_edge_clearance_mm,
    grade_electrical_dfm,
    power_net_ids_from_params,
    power_nets_missing_thermal_via,
    rectangle_edge_distance,
    run_kicad_pcb_erc_drc,
)


def test_rectangle_edge_distance_picks_nearest_side():
    assert rectangle_edge_distance((5.0, 5.0), 50.0, 32.0) == 5.0
    assert rectangle_edge_distance((1.0, 20.0), 50.0, 32.0) == 1.0
    assert rectangle_edge_distance((25.0, 30.0), 50.0, 32.0) == 2.0


def test_copper_edge_clearance_subtracts_half_extent():
    # pad centre 6 mm in, half-size 0.85 -> 5.15 mm clearance
    clearance = copper_edge_clearance_mm(
        conductors=[((6.0, 16.0), 0.85), ((25.0, 16.0), 0.4)],
        board_w=50.0,
        board_h=32.0,
    )
    assert clearance == 6.0 - 0.85


def test_power_nets_missing_thermal_via_reports_uncovered_nets():
    assert power_nets_missing_thermal_via(via_net_ids=[1], power_net_ids=[1]) == []
    assert power_nets_missing_thermal_via(via_net_ids=[2], power_net_ids=[1, 2]) == [1]
    assert power_nets_missing_thermal_via(via_net_ids=[], power_net_ids=[1]) == [1]


def test_grade_electrical_dfm_pass_and_fail():
    ok = grade_electrical_dfm(
        conductors=[((6.0, 16.0), 0.85)],
        via_net_ids=[1],
        power_net_ids=[1],
        board_w=50.0,
        board_h=32.0,
        min_edge_clearance_mm=0.5,
    )
    assert ok.passed
    assert ok.min_edge_clearance_mm == 5.15
    assert ok.power_nets_without_via == []

    bad = grade_electrical_dfm(
        conductors=[((0.2, 16.0), 0.15)],  # 0.05 mm from the edge
        via_net_ids=[2],  # power net 1 has no via
        power_net_ids=[1],
        board_w=50.0,
        board_h=32.0,
        min_edge_clearance_mm=0.5,
    )
    assert not bad.passed
    assert bad.checks["copper_edge_keepout_meets_rule"] is False
    assert bad.checks["power_nets_have_thermal_via"] is False
    assert bad.power_nets_without_via == [1]


def test_power_net_ids_from_params_resolves_names():
    params = {"net_ids": {"ROW_A": 1, "ROW_B": 2}, "power_nets": ["ROW_A"]}
    assert power_net_ids_from_params(params) == [1]
    assert power_net_ids_from_params({"net_ids": {}, "power_nets": ["X"]}) == []
    assert power_net_ids_from_params({}) == []


_PCB = '(kicad_pcb (version 20221018)\n  (net 1 "ROW_A")\n)\n'


def test_run_kicad_pcb_erc_drc_skips_when_cli_absent(monkeypatch):
    monkeypatch.setattr("makerbench.kicad_cli.shutil.which", lambda _name: None)
    report = run_kicad_pcb_erc_drc(_PCB)
    assert report["skipped"] is True
    assert report["available"] is False
    assert report["passed"] is None


def test_run_kicad_pcb_erc_drc_folds_in_native_drc(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "makerbench.kicad_cli.shutil.which", lambda _name: "/usr/bin/kicad-cli"
    )

    def fake_run(command, capture_output, text, timeout, check):  # noqa: ANN001
        report_path = command[command.index("--output") + 1]
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(
                {"violations": [
                    {"severity": "error", "type": "clearance", "description": "too close"}
                ]},
                fh,
            )

        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Proc()

    monkeypatch.setattr("makerbench.kicad_cli.subprocess.run", fake_run)
    report = run_kicad_pcb_erc_drc(_PCB)
    assert report["available"] is True
    assert report["skipped"] is False
    assert report["violation_count"] == 1
    assert report["passed"] is False
