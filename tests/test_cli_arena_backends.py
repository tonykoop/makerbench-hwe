"""CLI preflight tests for the SolidWorks/Fusion CAD-backend axis (#627).

Only exercises the `--backend` preflight gate in `arena run` — there is no
real Windows watcher to complete a job in CI/tests, so these tests stop at
"preflight fails cleanly with a clear message when the job-dir path isn't
available" and never attempt an actual run.
"""

from __future__ import annotations

from typer.testing import CliRunner

from makerbench import fusion_backend
from makerbench import solidworks_backend
from makerbench.cli import app

runner = CliRunner()


def _run_args(backend: str) -> list[str]:
    return [
        "arena",
        "run",
        "--run-dir",
        "runs/does-not-matter",
        "--instruments",
        "ocarina",
        "--models",
        "stub-a",
        "--stub",
        "--backend",
        backend,
    ]


class TestSolidworksBackendPreflight:
    def test_fails_cleanly_when_jobdir_unavailable(self, monkeypatch):
        monkeypatch.setattr(solidworks_backend, "solidworks_jobdir_available", lambda: False)
        result = runner.invoke(app, _run_args("solidworks"))
        assert result.exit_code == 1
        assert "SolidWorks job-dir handoff path not available" in result.stdout

    def test_unknown_backend_still_rejected_first(self):
        result = runner.invoke(app, _run_args("fusion360"))
        assert result.exit_code == 1
        assert "unknown --backend" in result.stdout


class TestFusionBackendPreflight:
    def test_fails_cleanly_when_jobdir_unavailable(self, monkeypatch):
        monkeypatch.setattr(fusion_backend, "fusion_jobdir_available", lambda: False)
        result = runner.invoke(app, _run_args("fusion"))
        assert result.exit_code == 1
        assert "Fusion job-dir handoff path not available" in result.stdout
