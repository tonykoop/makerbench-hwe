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
STUDIO_LAUNCHER = WINDOWS_SCRIPTS / "start-arena-studio.ps1"

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


# R2 P5/#… : Windows/RDP Arena Studio launch path. No pwsh/powershell binary exists
# in this sandbox (confirmed: `which pwsh powershell` finds neither), so these are
# static path-lint/contract checks on the script text — the same class of test the
# nightly-cad scripts above already use for their self-locating/fail-fast guarantees
# — not a real PowerShell execution. Genuinely unverified on a live Windows desktop;
# see the WINDOWS-GATED story filed alongside this script.


def test_studio_launcher_is_self_locating():
    text = STUDIO_LAUNCHER.read_text(encoding="utf-8")
    assert re.search(r'\[string\]\$RepoWsl\s*=\s*""', text)
    assert "$PSScriptRoot" in text
    assert "wslpath" in text


def test_studio_launcher_is_loopback_only():
    text = STUDIO_LAUNCHER.read_text(encoding="utf-8")
    # Strip the leading <# ... #> comment block first: it deliberately documents
    # (in prose) that --allow-remote is never passed, which would otherwise trip a
    # naive substring check on the comment text itself.
    body = re.sub(r"^<#.*?#>", "", text, count=1, flags=re.DOTALL)
    # No parameter can ever point this script at a non-loopback interface: no -Host
    # parameter exists, --allow-remote is never passed, and the arena studio
    # invocation's --host value is the literal string 127.0.0.1, not a variable.
    assert not re.search(r"\[string\]\$Host\b", body)
    assert "--allow-remote" not in body
    assert "--host 127.0.0.1" in body
    # Every URL this script opens or polls is loopback, never a variable that could
    # carry an operator- or environment-controlled remote host.
    for url_match in re.finditer(r'https?://([^:/"\'\s]+)', text):
        assert url_match.group(1) == "127.0.0.1", (
            f"non-loopback host in URL: {url_match.group(0)}"
        )


def test_studio_launcher_waits_for_health_before_opening_browser():
    text = STUDIO_LAUNCHER.read_text(encoding="utf-8")
    # Compare executable code only: the <# ... #> help block mentions /api/health
    # in prose, which would otherwise satisfy the ordering check on its own.
    body = re.sub(r"^<#.*?#>", "", text, count=1, flags=re.DOTALL)
    health_poll_at = body.index("Invoke-WebRequest -Uri $healthUrl")
    browser_calls = [m.start() for m in re.finditer(r"\bStart-Process\b", body)]
    assert browser_calls, "launcher must open the browser via Start-Process"
    assert all(health_poll_at < at for at in browser_calls), (
        "must poll /api/health before opening any browser tab"
    )
    assert "$NoBrowser" in body  # opt-out escape hatch for a Playwright-style caller


def test_studio_launcher_stops_the_server_on_ctrl_c():
    """#743 live Windows run: Ctrl+C interrupted Wait-Job but left the Start-Job child
    and the WSL-side server running. The wait must sit in try/finally, and the finally
    block must stop and remove the job and kill the exact server command inside WSL."""
    text = STUDIO_LAUNCHER.read_text(encoding="utf-8")
    body = re.sub(r"^<#.*?#>", "", text, count=1, flags=re.DOTALL)
    # Executable lines only: the explanatory comments above the block name Wait-Job.
    body = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
    # Brace-free bodies pin the match to the try/finally around Wait-Job itself, not
    # the earlier try/catch in the health-poll loop.
    match = re.search(r"\btry\s*\{(?P<try>[^{}]*)\}\s*finally\s*\{(?P<finally>[^{}]*)\}", body)
    assert match, "Wait-Job must be wrapped in try { ... } finally { ... }"
    assert re.search(r"\bWait-Job\s+-Job\s+\$serverJob\b", match.group("try"))
    assert not re.search(r"\bWait-Job\b", body[: match.start()]), "no Wait-Job outside the try block"
    cleanup = match.group("finally")
    stop_at = cleanup.find("Stop-Job -Job $serverJob")
    remove_at = cleanup.find("Remove-Job -Job $serverJob -Force")
    kill = re.search(
        r'wsl\.exe -d \$Distro -- pkill -f "makerbench\.cli arena studio --host 127\.0\.0\.1 --port \$Port"',
        cleanup,
    )
    assert stop_at != -1, "finally must Stop-Job the server job"
    assert remove_at != -1, "finally must Remove-Job -Force the server job"
    assert stop_at < remove_at, "stop the job before removing it"
    assert kill, "finally must pkill the exact loopback arena studio command inside WSL"
    # The pkill pattern must match what the script actually launches.
    assert "python3 -m makerbench.cli arena studio --host 127.0.0.1 --port $Port" in body


def test_studio_launcher_never_references_legacy_checkout_path():
    # Redundant with test_no_windows_script_references_legacy_checkout_path's
    # rglob, but pinned explicitly so this file's own regression survives a future
    # refactor of that glob.
    text = STUDIO_LAUNCHER.read_text(encoding="utf-8")
    assert not LEGACY_PATTERN.search(text)
