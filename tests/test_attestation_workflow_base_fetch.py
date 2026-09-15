"""verify-attestations must find a merge base for a PR that isn't on the base tip (#811).

The workflow's own "Fetch base ref" command runs against a local repository in
the state CI sees: a full-history checkout of a PR (or its stale merge ref) based
on ``main~1``, with ``main`` moved on. ``changed_result_paths`` must then diff
``origin/main...HEAD`` without error. The pre-#811 ``--depth=1`` command runs as a
control and must reproduce the original ``no merge base`` failure, so this test
can't pass vacuously.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from makerbench.regrade import changed_result_paths

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "regrade-results.yml"
PRE_811_FETCH = 'git fetch origin "${{ github.base_ref }}" --depth=1'

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")


def _step_run(name: str) -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(
        rf"- name: {re.escape(name)}\n(?:[ \t]+(?!- name:)[^\n]*\n)*?[ \t]+run: ([^\n]+)\n", text)
    assert match, f"workflow step {name!r} with a single-line run: not found"
    return match.group(1).strip()


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=ci@example.invalid", "-c", "user.name=ci",
         "-c", "init.defaultBranch=main", *args],
        cwd=cwd, check=True, text=True, capture_output=True,
    ).stdout.strip()


def _commit(repo: Path, name: str) -> None:
    (repo / name).write_text(f"{name}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", name)


@pytest.fixture
def stale_base_checkout(tmp_path: Path):
    """A full-history clone checked out at a PR based on main~1, and main moved on."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _commit(origin, "c1")
    _commit(origin, "c2")
    _git(origin, "checkout", "-q", "-b", "pr")
    _commit(origin, "pr-change")
    # GitHub builds refs/pull/N/merge against the base tip at event time...
    _git(origin, "checkout", "-q", "-b", "merge-ref", "main")
    _git(origin, "merge", "-q", "--no-ff", "--no-edit", "pr")
    # ...and main moves on, so the PR is now based on main~1.
    _git(origin, "checkout", "-q", "main")
    _commit(origin, "c3")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", origin.resolve().as_uri(), clone.as_posix())  # fetch-depth: 0

    def checkout(head: str) -> Path:
        _git(clone, "checkout", "-q", "--detach", f"origin/{head}")
        return clone

    return checkout


def _run_fetch(command: str, clone: Path) -> None:
    command = command.replace("${{ github.base_ref }}", "main")
    subprocess.run(["bash", "-c", command], cwd=clone, check=True, capture_output=True)


def test_workflow_keeps_a_full_history_checkout_and_an_unshallow_base_fetch():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "fetch-depth: 0" in text
    fetch = _step_run("Fetch base ref")
    assert "--depth" not in fetch and "--shallow" not in fetch
    assert 'makerbench verify-attestations \\\n            --base "origin/${{ github.base_ref }}"' in text


@pytest.mark.parametrize("head", ["pr", "merge-ref"])
def test_pr_based_on_main_minus_one_diffs_against_the_moved_base(stale_base_checkout, head):
    clone = stale_base_checkout(head)

    _run_fetch(_step_run("Fetch base ref"), clone)

    assert not (clone / ".git" / "shallow").exists()
    assert _git(clone, "merge-base", "origin/main", "HEAD")
    assert changed_result_paths("origin/main", clone) == []


@pytest.mark.parametrize("head", ["pr", "merge-ref"])
def test_control_the_pre_811_shallow_fetch_reproduces_no_merge_base(stale_base_checkout, head):
    clone = stale_base_checkout(head)

    _run_fetch(PRE_811_FETCH, clone)

    with pytest.raises(subprocess.CalledProcessError) as failure:
        changed_result_paths("origin/main", clone)
    assert failure.value.returncode == 128
    assert "no merge base" in failure.value.stderr
