"""Path-lint and preflight tests for the nightly CAD arena Windows scripts.

Issue #657: the runner used to default -RepoWsl to the dead legacy checkout
``.../GitHub/makerbench-hwe`` (pre-ecosystem layout). These tests pin the
repair: no legacy path anywhere under scripts/windows/, the runner is
self-locating and fail-fast, and the WSL-side preflight actually rejects
bogus paths (RED) while passing real ones (green).
"""

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_SCRIPTS = REPO_ROOT / "scripts" / "windows"
RUNNER = WINDOWS_SCRIPTS / "run-nightly-cad-arena.ps1"
INSTALLER = WINDOWS_SCRIPTS / "install-nightly-cad-task.ps1"
PREFLIGHT = WINDOWS_SCRIPTS / "check-nightly-cad-paths.sh"

# The dead pre-ecosystem checkout: GitHub/makerbench-hwe NOT preceded by the
# ecosystem directory. Matches both windows and WSL spellings.
LEGACY_PATTERN = re.compile(r"GitHub[/\\]makerbench-hwe")


def test_no_windows_script_references_legacy_checkout_path():
    offenders = []
    for path in sorted(WINDOWS_SCRIPTS.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if LEGACY_PATTERN.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, "legacy GitHub/makerbench-hwe checkout referenced:\n" + "\n".join(
        offenders
    )


def test_runner_is_self_locating_and_fail_fast():
    text = RUNNER.read_text(encoding="utf-8")
    # No hardcoded default checkout: -RepoWsl defaults empty and is derived
    # from the script's own location.
    assert re.search(r'\[string\]\$RepoWsl\s*=\s*""', text)
    assert "$PSScriptRoot" in text
    assert "wslpath" in text
    # The preflight runs before the arena command is launched.
    assert "check-nightly-cad-paths.sh" in text
    preflight_at = text.index("check-nightly-cad-paths.sh")
    arena_at = text.index("arena overnight")
    assert preflight_at < arena_at
    # Every required path parameter is handed to the preflight.
    for name in ("RepoWsl", "QueueWsl", "SecretsWsl", "OutputRootWsl"):
        assert f'"{name}=${name}"' in text, f"preflight missing {name}"


def test_installer_validates_runner_script_exists():
    text = INSTALLER.read_text(encoding="utf-8")
    assert re.search(r"Test-Path\s+-LiteralPath\s+\$RunnerScript", text)
    assert "throw" in text


def test_preflight_red_on_bogus_path(tmp_path):
    queue = tmp_path / "queue.json"
    queue.write_text("{}")
    bogus = tmp_path / "does-not-exist"
    proc = subprocess.run(
        [
            "bash",
            str(PREFLIGHT),
            f"QueueWsl={queue}",
            f"RepoWsl={bogus}",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "-RepoWsl" in proc.stderr
    assert str(bogus) in proc.stderr
    # Actionable: points the operator at the canonical checkout + doc.
    assert "makerbench_ecosystem" in proc.stderr
    assert "NIGHTLY_CAD_TASK.md" in proc.stderr
    # The path that DOES exist is not reported as missing.
    assert "-QueueWsl" not in proc.stderr


def test_preflight_reports_every_missing_path(tmp_path):
    proc = subprocess.run(
        [
            "bash",
            str(PREFLIGHT),
            f"RepoWsl={tmp_path / 'nope-repo'}",
            f"SecretsWsl={tmp_path / 'nope-secrets'}",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "-RepoWsl" in proc.stderr
    assert "-SecretsWsl" in proc.stderr


def test_preflight_green_when_all_paths_exist(tmp_path):
    queue = tmp_path / "queue.json"
    queue.write_text("{}")
    out_root = tmp_path / "runs"
    out_root.mkdir()
    proc = subprocess.run(
        [
            "bash",
            str(PREFLIGHT),
            f"RepoWsl={tmp_path}",
            f"QueueWsl={queue}",
            f"SecretsWsl={queue}",
            f"OutputRootWsl={out_root}",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
