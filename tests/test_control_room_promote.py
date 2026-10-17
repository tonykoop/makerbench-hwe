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
from makerbench.arena_studio import promote as pm  # noqa: E402

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


def test_publishing_pushes_and_opens_a_refs_pr(setup, monkeypatch):
    studio_repo, root, instrument, origin = setup
    monkeypatch.setattr(pm, "remote_ok", lambda url, name: url == str(origin))  # the scratch origin stands in for GitHub
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
    assert "regenerated in a separate step" in body


def test_promote_refuses_without_a_repo_or_design(setup, tmp_path):
    studio_repo, root, _instrument, _origin = setup
    assert _client(studio_repo, root).post(f"/api/review/{SAMBUCA_RUN}/promote/nope", headers=ORIGIN).status_code == 404
    response = _client(studio_repo, tmp_path / "empty").post(f"/api/review/{SAMBUCA_RUN}/promote/{SOLIDWORKS}", headers=ORIGIN)
    assert response.status_code == 409 and "instrument repo" in response.text
    # The Fusion error row has nothing to promote.
    fusion = _client(studio_repo, root).post(f"/api/review/{SAMBUCA_RUN}/promote/sambuca__seed0__rep0__fusion-sonnet", headers=ORIGIN)
    assert fusion.status_code == 409 and "to promote" in fusion.text
    assert _client(studio_repo, root).post(f"/api/review/{SAMBUCA_RUN}/promote/{SOLIDWORKS}").status_code == 403


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)
    _git(repo, "push", "-q", "origin", "main")


def _no_promotion_branch(repo: Path) -> bool:
    return _git(repo, "branch", "--list", "makerbench/*") == ""


@pytest.mark.parametrize("link", ["cad", "capstone-manifest.json", "images"])
def test_a_tracked_symlink_refuses_the_promotion_and_writes_nothing_outside(setup, tmp_path, link):
    studio_repo, root, instrument, _origin = setup
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "capstone-manifest.json").write_text('{"sentinel": true}', encoding="utf-8")
    if link == "capstone-manifest.json":
        (instrument / link).unlink()
        (instrument / link).symlink_to(outside / "capstone-manifest.json")
    else:
        (instrument / link).symlink_to(outside, target_is_directory=True)
    _commit(instrument, f"track a {link} symlink")
    response = _client(studio_repo, root).post(f"/api/review/{SAMBUCA_RUN}/promote/{SOLIDWORKS}", headers=ORIGIN)
    assert response.status_code == 409 and "symlink" in response.text
    assert sorted(p.name for p in outside.rglob("*")) == ["capstone-manifest.json"]
    assert (outside / "capstone-manifest.json").read_text(encoding="utf-8") == '{"sentinel": true}'
    assert _git(instrument, "worktree", "list").count("\n") == 0 and _no_promotion_branch(instrument)


def test_a_gitdir_redirect_is_never_promoted_to(setup, tmp_path):
    studio_repo, root, instrument, _origin = setup
    decoy, _ = build_scratch_instrument(tmp_path / "decoy" / "instruments")
    redirected = root / "strings" / "sambuca"
    import shutil

    shutil.rmtree(redirected)
    redirected.mkdir()
    (redirected / ".git").write_text(f"gitdir: {decoy / '.git'}\n", encoding="utf-8")
    response = _client(studio_repo, root).post(f"/api/review/{SAMBUCA_RUN}/promote/{SOLIDWORKS}", headers=ORIGIN)
    assert response.status_code == 409 and "plain git checkout" in response.text
    assert _no_promotion_branch(decoy)


def test_a_commondir_redirect_and_a_foreign_origin_are_refused(setup, tmp_path, monkeypatch):
    studio_repo, root, instrument, _origin = setup
    (instrument / ".git" / "commondir").write_text(str(tmp_path / "elsewhere" / ".git"), encoding="utf-8")
    response = _client(studio_repo, root).post(f"/api/review/{SAMBUCA_RUN}/promote/{SOLIDWORKS}", headers=ORIGIN)
    assert response.status_code == 409 and "commondir" in response.text
    (instrument / ".git" / "commondir").unlink()
    # Publishing checks origin first: the scratch origin is not github.com/tonykoop/sambuca.
    client = _client(studio_repo, root, control_room_promote=True, promote_pr_opener=lambda *a: pytest.fail("opened"))
    response = client.post(f"/api/review/{SAMBUCA_RUN}/promote/{SOLIDWORKS}", headers=ORIGIN)
    assert response.status_code == 409 and "refusing to push" in response.text
    assert _no_promotion_branch(instrument)
    assert pm.remote_ok("https://github.com/tonykoop/sambuca.git", "sambuca")
    assert pm.remote_ok("git@github.com:tonykoop/sambuca", "sambuca")
    assert not pm.remote_ok("https://github.com/someone/sambuca.git", "sambuca")
    assert not pm.remote_ok("https://github.com/tonykoop/sambuca.git.evil.example/x", "sambuca")


def test_git_environment_cannot_redirect_the_repo(setup, tmp_path, monkeypatch):
    studio_repo, root, instrument, _origin = setup
    decoy, _ = build_scratch_instrument(tmp_path / "decoy" / "instruments")
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    result = _client(studio_repo, root).post(f"/api/review/{SAMBUCA_RUN}/promote/{SOLIDWORKS}", headers=ORIGIN).json()
    monkeypatch.delenv("GIT_DIR")
    assert _no_promotion_branch(decoy) and result["branch"] in _git(instrument, "branch", "--list", "makerbench/*")


def test_an_oversized_or_invalid_manifest_is_refused(setup):
    studio_repo, root, instrument, _origin = setup
    big = {**MANIFEST, "padding": "x" * pm.MAX_MANIFEST_BYTES}
    (instrument / "capstone-manifest.json").write_text(json.dumps(big), encoding="utf-8")
    _commit(instrument, "big manifest")
    response = _client(studio_repo, root).post(f"/api/review/{SAMBUCA_RUN}/promote/{SOLIDWORKS}", headers=ORIGIN)
    assert response.status_code == 409 and "larger than" in response.text
    (instrument / "capstone-manifest.json").write_text("[1, 2]", encoding="utf-8")
    _commit(instrument, "list manifest")
    response = _client(studio_repo, root).post(f"/api/review/{SAMBUCA_RUN}/promote/{SOLIDWORKS}", headers=ORIGIN)
    assert response.status_code == 409 and "JSON object" in response.text
    assert _no_promotion_branch(instrument)


def test_a_symlinked_worktree_folder_is_refused(setup, tmp_path):
    studio_repo, root, _instrument, _origin = setup
    outside = tmp_path / "wt-outside"
    outside.mkdir()
    control = studio_repo / "runs" / "control-room"
    control.mkdir(exist_ok=True)
    (control / "promote-worktrees").symlink_to(outside, target_is_directory=True)
    response = _client(studio_repo, root).post(f"/api/review/{SAMBUCA_RUN}/promote/{SOLIDWORKS}", headers=ORIGIN)
    assert response.status_code == 409 and "symlink" in response.text
    assert list(outside.iterdir()) == []


def _set_origin(repo: Path, url: str, *pushurls: str) -> None:
    _git(repo, "remote", "set-url", "origin", url)
    for pushurl in pushurls:
        _git(repo, "config", "--add", "remote.origin.pushurl", pushurl)


def test_every_push_url_must_be_the_instrument_repo(setup, tmp_path):
    studio_repo, root, instrument, origin = setup
    elsewhere = tmp_path / "elsewhere.git"
    subprocess.run(["git", "init", "-q", "--bare", str(elsewhere)], check=True)
    good = "https://github.com/tonykoop/sambuca.git"
    _set_origin(instrument, good, good, str(elsewhere))  # the second push URL is the one git push would also hit
    client = _client(studio_repo, root, control_room_promote=True, promote_pr_opener=lambda *a: pytest.fail("opened"))
    response = client.post(f"/api/review/{SAMBUCA_RUN}/promote/{SOLIDWORKS}", headers=ORIGIN)
    assert response.status_code == 409 and "push URL" in response.text
    assert _git(elsewhere, "branch", "--list") == "" and _no_promotion_branch(instrument)
    with pytest.raises(pm.PromoteError, match="push URL"):
        pm._check_remote(instrument)
    _git(instrument, "config", "--unset-all", "remote.origin.pushurl")
    _set_origin(instrument, good, good, "git@github.com:tonykoop/sambuca.git")
    assert pm._check_remote(instrument) == good  # two valid push URLs: the first is pushed to explicitly


@pytest.mark.parametrize("rule", ["insteadOf", "pushInsteadOf"])
def test_a_url_rewrite_of_origin_is_refused(setup, tmp_path, rule):
    _studio_repo, _root, instrument, _origin = setup
    _set_origin(instrument, "https://github.com/tonykoop/sambuca.git")
    _git(instrument, "config", f"url.{tmp_path / 'decoy.git'}.{rule}", "https://github.com/tonykoop/")
    with pytest.raises(pm.PromoteError, match="insteadOf"):
        pm._check_remote(instrument)
    _git(instrument, "config", "--unset", f"url.{tmp_path / 'decoy.git'}.{rule}")
    _git(instrument, "config", "url.https://example.invalid/.insteadOf", "https://unrelated.example/")
    assert pm._check_remote(instrument) == "https://github.com/tonykoop/sambuca.git"  # unrelated rules are fine
    assert pm.github_repo("git@github.com:tonykoop/sambuca.git", "sambuca") == "tonykoop/sambuca"
