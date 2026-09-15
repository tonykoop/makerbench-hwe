"""Guards the public TwinGrid archival kit under docs/plans/r1-twingrid/.

The kit is a recovered sprint record published in a public repository, so it
must not carry operator host paths or private repository coordinates. Private
lanes appear only as neutral placeholders, and the committed R1 handoffs must
match what the generator emits from the sanitized sources.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1] / "docs" / "plans" / "r1-twingrid"

PUBLIC_REPOS = {"tonykoop/makerbench-hwe", "makerbench-hwe"}
PLACEHOLDER_REPOS = {"private-lane-a", "private-lane-b"}
ALLOWED_REPOS = PUBLIC_REPOS | PLACEHOLDER_REPOS

HOST_PATH = re.compile(r"(/home/|/mnt/[A-Za-z]/|/Users/|[A-Za-z]:\\\\Users\\\\)")
# owner/repo#N issue coordinates; public ones only.
ISSUE_COORD = re.compile(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#\d+")
GH_REPO_FLAG = re.compile(r"-R\s+(\S+?)[`\s)]")


def _kit_files() -> list[Path]:
    return sorted(p for p in KIT.rglob("*") if p.is_file())


def test_kit_exists() -> None:
    assert (KIT / "gen-handoffs.sh").is_file()
    assert _kit_files()


def test_no_host_absolute_paths() -> None:
    offenders = [
        f"{p.relative_to(KIT)}:{n}"
        for p in _kit_files()
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if HOST_PATH.search(line)
    ]
    assert not offenders, f"host-specific paths in public kit: {offenders}"


def test_issue_coordinates_are_public_or_placeholder() -> None:
    offenders = []
    for p in _kit_files():
        text = p.read_text(encoding="utf-8")
        for repo in ISSUE_COORD.findall(text) + GH_REPO_FLAG.findall(text):
            if repo not in ALLOWED_REPOS:
                offenders.append(f"{p.relative_to(KIT)}: {repo}")
    assert not offenders, f"non-allowlisted repository coordinates: {offenders}"


def _tsv_rows(name: str) -> list[list[str]]:
    rows = []
    for line in (KIT / name).read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            rows.append(line.split("\t"))
    return rows


@pytest.mark.parametrize(
    ("name", "columns"),
    [("persona-map.tsv", (2, 3)), ("r2-assignments.tsv", (2, 3)), ("r3-assignments.tsv", (1, 2))],
)
def test_assignment_repos_are_allowlisted(name: str, columns: tuple[int, int]) -> None:
    for row in _tsv_rows(name):
        for col in columns:
            assert row[col] in ALLOWED_REPOS, f"{name}: {row[0]} uses {row[col]!r}"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
def test_committed_handoffs_match_generator(tmp_path: Path) -> None:
    copy = tmp_path / "kit"
    shutil.copytree(KIT, copy)
    shutil.rmtree(copy / "handoffs")
    subprocess.run(
        ["bash", str(copy / "gen-handoffs.sh")],
        check=True,
        capture_output=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "WT_ROOT": "<worktree-root>"},
    )
    for rel in ["persona-launch.generated.tsv", *(
        str(p.relative_to(KIT)) for p in sorted((KIT / "handoffs").glob("*.md"))
    )]:
        assert (copy / rel).read_bytes() == (KIT / rel).read_bytes(), f"{rel} drifted from generator"
    assert len(list((copy / "handoffs").glob("*.md"))) == len(list((KIT / "handoffs").glob("*.md")))


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
@pytest.mark.parametrize("script", ["gen-handoffs.sh", "setup-worktrees.sh"])
def test_scripts_require_explicit_roots(tmp_path: Path, script: str) -> None:
    copy = tmp_path / "kit"
    shutil.copytree(KIT, copy)
    result = subprocess.run(
        ["bash", str(copy / script)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(tmp_path)},
    )
    assert result.returncode != 0
    assert "_ROOT" in result.stderr
