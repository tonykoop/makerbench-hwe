"""CLI onboarding docs stay aligned with the Typer command tree."""

from __future__ import annotations

import re
from pathlib import Path

from typer.testing import CliRunner

from makerbench.cli import app


COMMAND_RE = re.compile(r"^\| `(?P<command>makerbench(?: [^`]+)?)` \|", re.MULTILINE)


def _registered_command_name(command) -> str:
    if command.name:
        return command.name
    if command.callback is None:
        raise AssertionError(f"Typer command lacks name and callback: {command!r}")
    return command.callback.__name__.replace("_", "-")


def _actual_cli_commands() -> set[str]:
    commands = {
        f"makerbench {_registered_command_name(command)}"
        for command in app.registered_commands
    }
    for group in app.registered_groups:
        if not group.name:
            raise AssertionError(f"Typer group lacks an explicit name: {group!r}")
        for command in group.typer_instance.registered_commands:
            commands.add(
                f"makerbench {group.name} {_registered_command_name(command)}"
            )
    return commands


def test_readme_cli_command_reference_matches_registered_commands():
    readme = Path("README.md").read_text(encoding="utf-8")
    section = readme.split("## CLI command reference", 1)[1].split("\n## ", 1)[0]
    documented = set(COMMAND_RE.findall(section))

    assert documented == _actual_cli_commands()


def test_run_help_exposes_workflow_harness_fields_for_issue_88():
    result = CliRunner().invoke(app, ["run", "--help"])

    assert result.exit_code == 0
    assert "--harness-class" in result.stdout
    assert "--harness-subclass" in result.stdout
    assert "assisted-workflow" in result.stdout
    assert "api-driven-code" in result.stdout
    assert "gui-injected-copilot" in result.stdout
    assert "whole-canvas-diffusion-code" in result.stdout
