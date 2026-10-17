"""Promote to library (#1111), against a scratch instrument repo only."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("trimesh")

from fastapi.testclient import TestClient  # noqa: E402

from control_room_fixture import SAMBUCA_RUN, build_control_room_repo  # noqa: E402

from makerbench.arena_studio import create_studio_app  # noqa: E402

ORIGIN = {"Origin": "http://127.0.0.1"}
SOLIDWORKS = "sambuca__seed0__rep0__solidworks-sol"
MANIFEST = {"instrument": "Sambuca", "status": "private_review",
            "release_gate": {"public_candidate": False, "required_before_public": ["cultural review"]}}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def build_scratch_instrument(root: Path) -> tuple[Path, Path]:
    """``<root>/strings/sambuca``: a git repo on main with a bare origin. Synthetic content only."""
    origin = root.parent / "origin-sambuca.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = root / "strings" / "sambuca"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "capstone-manifest.json").write_text(json.dumps(MANIFEST, indent=2), encoding="utf-8")
    (repo / "README.md").write_text("# Sambuca\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")
    _git(repo, "remote", "set-head", "origin", "main")
    return repo, origin


@pytest.fixture
def setup(tmp_path: Path):
    studio_repo = build_control_room_repo(tmp_path / "studio")
    instrument, origin = build_scratch_instrument(tmp_path / "instruments")
    return studio_repo, tmp_path / "instruments", instrument, origin


def _client(studio_repo: Path, root: Path, **kwargs) -> TestClient:
    app = create_studio_app(registry_path=studio_repo / "registry.json", repo_root=studio_repo, instruments_root=root, **kwargs)
    return TestClient(app, base_url="http://127.0.0.1")


def test_promote_commits_only_the_chosen_design_on_a_local_branch(setup):
    studio_repo, root, instrument, origin = setup
    main_before = _git(instrument, "rev-parse", "main")
    result = _client(studio_repo, root).post(f"/api/review/{SAMBUCA_RUN}/promote/{SOLIDWORKS}", headers=ORIGIN).json()
    assert result["pushed"] is False and result["pr_url"] is None and result["publish"] is False
    branch = result["branch"]
    assert branch.startswith("makerbench/promote-") and branch.endswith("-solidworks-sol")
    assert _git(instrument, "rev-parse", "main") == main_before  # the default branch is untouched
    assert _git(origin, "branch", "--list", branch) == ""  # nothing pushed
    assert _git(instrument, "worktree", "list").count("\n") == 0  # the temporary worktree is gone

    files = _git(instrument, "show", "--name-only", "--format=", branch).splitlines()
    folder = next(f for f in files if f.endswith("provenance.json")).rsplit("/", 1)[0]
    assert sorted(files) == sorted([
        "capstone-manifest.json", f"{folder}/PROVENANCE.md", f"{folder}/provenance.json",
        f"{folder}/sambuca.SLDPRT", f"{folder}/sambuca.glb", next(f for f in files if f.startswith("images/makerbench/")),
    ])
    record = json.loads(_git(instrument, "show", f"{branch}:{folder}/provenance.json"))
    assert record["tool_and_model"] == "Codex · GPT-6.1 Sol · live bridge" and record["checks_passed"] == "4/5"
    text = json.dumps(record) + _git(instrument, "show", f"{branch}:{folder}/PROVENANCE.md")
    for leaked in ("pass_rate", "objective", "sub_scores", "CADAM", "FreeCAD", "Fusion", "cadam-fable", "seed0"):
        assert leaked not in text, leaked
    manifest = json.loads(_git(instrument, "show", f"{branch}:capstone-manifest.json"))
    assert manifest["status"] == MANIFEST["status"] and manifest["release_gate"] == MANIFEST["release_gate"]
    assert manifest["makerbench_promotions"][0]["glb"].endswith("sambuca.glb")


def test_publishing_pushes_and_opens_a_refs_pr(setup):
    studio_repo, root, instrument, origin = setup
    opened = []

    def opener(worktree, branch, title, body):
        opened.append((branch, title, body))
        return "https://github.example/tonykoop/sambuca/pull/1"

    client = _client(studio_repo, root, control_room_promote=True, promote_pr_opener=opener)
    result = client.post(f"/api/review/{SAMBUCA_RUN}/promote/{SOLIDWORKS}", headers=ORIGIN).json()
    assert result["pushed"] is True and result["pr_url"].endswith("/pull/1")
    assert _git(origin, "branch", "--list", result["branch"]).strip().endswith(result["branch"])
    branch, title, body = opened[0]
    assert branch == result["branch"] and "SolidWorks" in title
    assert "Refs tonykoop/makerbench-hwe#1111" in body and "Closes" not in body


def test_promote_refuses_without_a_repo_or_design(setup, tmp_path):
    studio_repo, root, _instrument, _origin = setup
    assert _client(studio_repo, root).post(f"/api/review/{SAMBUCA_RUN}/promote/nope", headers=ORIGIN).status_code == 404
    response = _client(studio_repo, tmp_path / "empty").post(f"/api/review/{SAMBUCA_RUN}/promote/{SOLIDWORKS}", headers=ORIGIN)
    assert response.status_code == 409 and "instrument repo" in response.text
    # The Fusion error row has nothing to promote.
    fusion = _client(studio_repo, root).post(f"/api/review/{SAMBUCA_RUN}/promote/sambuca__seed0__rep0__fusion-sonnet", headers=ORIGIN)
    assert fusion.status_code == 409 and "to promote" in fusion.text
    assert _client(studio_repo, root).post(f"/api/review/{SAMBUCA_RUN}/promote/{SOLIDWORKS}").status_code == 403
