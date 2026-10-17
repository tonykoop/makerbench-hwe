"""Real Git ancestry controls for the Space's deployment package pin."""
import importlib.util
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("space_pin_gate", ROOT / "scripts/build_hf_space.py")
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def requirement(sha):
    return f"makerbench-hwe @ https://github.com/tonykoop/makerbench-hwe/archive/{sha}.zip\n"


@pytest.fixture
def history(tmp_path):
    def git(*args):
        return subprocess.check_output(
            ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
            cwd=tmp_path, stderr=subprocess.PIPE, text=True,
        ).strip()

    git("init", "-q")
    git("commit", "--allow-empty", "-qm", "merged package")
    merged = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", merged)
    git("commit", "--allow-empty", "-qm", "unmerged PR package")
    unmerged = git("rev-parse", "HEAD")
    return tmp_path, git, merged, unmerged


def test_package_on_main_is_accepted(history):
    repo, git, merged, _ = history
    build.assert_package_pin_on_main(requirement(merged), repo)
    # The pin can also be an older main commit, not only the current tip.
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    build.assert_package_pin_on_main(requirement(merged), repo)


def test_pr_only_package_is_refused_until_merged(history):
    repo, git, _, unmerged = history
    with pytest.raises(SystemExit, match="not proven.*ancestor of origin/main"):
        build.assert_package_pin_on_main(requirement(unmerged), repo)
    git("update-ref", "refs/remotes/origin/main", unmerged)
    build.assert_package_pin_on_main(requirement(unmerged), repo)


def test_missing_main_or_unknown_commit_fails_closed(history):
    repo, git, merged, _ = history
    with pytest.raises(SystemExit, match="Fetch origin/main"):
        build.assert_package_pin_on_main(requirement("f" * 40), repo)
    git("update-ref", "-d", "refs/remotes/origin/main")
    with pytest.raises(SystemExit, match="Fetch origin/main"):
        build.assert_package_pin_on_main(requirement(merged), repo)


@pytest.mark.parametrize("sha", ["main", "a" * 7, "a" * 39, "a" * 41])
def test_non_exact_pin_refused(history, sha):
    with pytest.raises(SystemExit, match="40-character"):
        build.assert_package_pin_on_main(requirement(sha), history[0])


def test_cli_exits_without_success_message_on_ancestry_failure(tmp_path, monkeypatch, capsys):
    def fake_stage(out):
        out.mkdir()
        (out / "requirements.txt").write_text(requirement("a" * 40))
        return 1

    def reject(requirements):
        raise SystemExit("unmerged package refused")

    monkeypatch.setattr(build, "stage", fake_stage)
    monkeypatch.setattr(build, "assert_package_pin_on_main", reject)
    monkeypatch.setattr("sys.argv", ["build_hf_space.py", "--out", str(tmp_path / "stage")])
    with pytest.raises(SystemExit, match="unmerged package refused"):
        build.main()
    assert "integrity checks passed" not in capsys.readouterr().out
