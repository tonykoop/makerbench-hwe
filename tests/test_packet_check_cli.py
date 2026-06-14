"""Contract tests for the `makerbench packet-check` CLI (#103).

The deliverable-packet schema and the disclosure-grade completeness hooks land
elsewhere; this exercises the runnable entry point that exposes them so a
contributor can validate a packet without writing Python.
"""

import json

from typer.testing import CliRunner

from makerbench.cli import app

runner = CliRunner()

FULL_DOSSIER = "examples/deliverable_packet_dossier.example.json"
BARE_PACKET = "examples/deliverable_packet.example.json"


def test_full_dossier_packet_reports_complete():
    result = runner.invoke(app, ["packet-check", FULL_DOSSIER])

    assert result.exit_code == 0
    assert "deliverable_packet" in result.stdout
    assert "complete" in result.stdout
    # The four disclosure hooks are surfaced by name.
    for check in (
        "packet_present",
        "manifest_lists_all_files",
        "bom_enumerates_assembly_parts",
        "gcode_bounds_enclose_part",
    ):
        assert check in result.stdout


def test_full_dossier_json_output_is_structured_and_passing():
    result = runner.invoke(app, ["packet-check", "--json", FULL_DOSSIER])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["category"] == "deliverable_packet"
    assert payload["passed"] is True
    assert payload["checks"]["bom_enumerates_assembly_parts"] is True


def test_bare_packet_is_incomplete_but_does_not_gate_by_default():
    # A stand-alone packet lacks the surrounding BOM/assembly, so the cross-check
    # cannot be satisfied — but the check is disclosure-grade, so exit stays 0.
    result = runner.invoke(app, ["packet-check", BARE_PACKET])

    assert result.exit_code == 0
    assert "incomplete" in result.stdout
    assert "bom_enumerates_assembly_parts" in result.stdout


def test_strict_sets_nonzero_exit_on_incomplete_packet():
    result = runner.invoke(app, ["packet-check", "--strict", BARE_PACKET])

    assert result.exit_code == 1


def test_strict_stays_zero_on_complete_packet():
    result = runner.invoke(app, ["packet-check", "--strict", FULL_DOSSIER])

    assert result.exit_code == 0
