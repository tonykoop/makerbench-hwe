"""#1101: scripts/cadam_local_sync.sh and the committed CADAM patch set (synthetic repos; no network, no npm)."""

import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cadam_local_sync.sh"
PATCHES = ROOT / "docs" / "cadam" / "local-patches"
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

ORIGINAL = "line one\nprovider = direct\nline three\n"
PATCHED = "line one\nprovider = fallback\nline three\n"
PATCH = textwrap.dedent("""\
    diff --git a/src/server/aiChat.ts b/src/server/aiChat.ts
    --- a/src/server/aiChat.ts
    +++ b/src/server/aiChat.ts
    @@ -1,3 +1,3 @@
     line one
    -provider = direct
    +provider = fallback
     line three
    """)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def world(tmp_path):
    """upstream (bare) + a clone + a patch dir holding one patch."""
    upstream = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(upstream)], check=True, env=ENV)
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True, env=ENV)
    (seed / "src/server").mkdir(parents=True)
    (seed / "src/server/aiChat.ts").write_text(ORIGINAL)
    (seed / "README.md").write_text("v1\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "base")
    git(seed, "remote", "add", "origin", str(upstream))
    git(seed, "push", "-q", "origin", "main")
    clone = tmp_path / "CADAM"
    subprocess.run(["git", "clone", "-q", str(upstream), str(clone)], check=True, env=ENV)
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "0001-fallback.diff").write_text(PATCH)

    class World:
        pass

    w = World()
    w.upstream, w.seed, w.clone, w.patches, w.tmp = upstream, seed, clone, patches, tmp_path

    def advance(path="README.md", content="v2\n"):
        (seed / path).write_text(content)
        git(seed, "commit", "-qam", "upstream moves")
        git(seed, "push", "-q", "origin", "main")

    w.advance = advance

    def run(*args, extra_env=None):
        env = {**ENV, "CADAM_DIR": str(clone), "PATCH_DIR": str(patches), **(extra_env or {})}
        return subprocess.run(["bash", str(SCRIPT), "--no-install", *args], env=env, capture_output=True, text=True)

    w.run = run
    return w


def text(w, rel="src/server/aiChat.ts"):
    return (w.clone / rel).read_text()


def test_pulls_upstream_and_applies_the_patch_without_committing(world):
    world.advance()
    result = world.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "applied 0001-fallback.diff" in result.stdout
    assert text(world) == PATCHED and text(world, "README.md") == "v2\n"
    assert git(world.clone, "rev-parse", "HEAD") == git(world.seed, "rev-parse", "HEAD")  # no commit made in the clone
    assert "M src/server/aiChat.ts" in git(world.clone, "status", "--short")


def test_local_changes_are_stashed_and_kept_not_dropped(world):
    (world.clone / "src/server/aiChat.ts").write_text("a stale whole-file copy\n")
    git(world.clone, "add", "src/server/aiChat.ts")  # staged, like the real clone
    result = world.run()
    assert result.returncode == 0, result.stdout
    assert text(world) == PATCHED  # the patch set replaces the old edit
    stash = git(world.clone, "stash", "list")
    assert "cadam-local-sync-" in stash
    assert "stale whole-file copy" in git(world.clone, "stash", "show", "-p", "stash@{0}")
    assert "previous local changes are in stash@{0}" in result.stdout


def test_a_conflicting_patch_is_reported_not_forced(world):
    world.advance("src/server/aiChat.ts", "line one\nprovider = renamed upstream\nline three\n")
    (world.patches / "0002-second.diff").write_text(PATCH.replace("provider", "other"))
    result = world.run()
    assert result.returncode == 3
    assert "CONFLICT: 0001-fallback.diff" in result.stdout and "0002" not in result.stdout.split("CONFLICT")[1]
    assert text(world) == "line one\nprovider = renamed upstream\nline three\n"  # untouched, no partial hunk
    assert not list(world.clone.rglob("*.rej")) and not list(world.clone.rglob("*.orig"))


def test_an_already_merged_patch_is_skipped(world):
    world.advance("src/server/aiChat.ts", PATCHED)
    result = world.run()
    assert result.returncode == 0 and "skipped 0001-fallback.diff" in result.stdout
    assert text(world) == PATCHED


def test_patches_apply_in_filename_order(world):
    second = PATCH.replace("-provider = direct", "-provider = fallback").replace("+provider = fallback", "+provider = bridge")
    (world.patches / "0002-bridge.diff").write_text(second)
    result = world.run()
    assert result.returncode == 0 and text(world) == "line one\nprovider = bridge\nline three\n"
    assert result.stdout.index("0001-fallback") < result.stdout.index("0002-bridge")


def test_diverged_history_stops_before_patching_and_restores_local_changes(world):
    (world.clone / "README.md").write_text("local commit\n")
    git(world.clone, "commit", "-qam", "local only")
    world.advance("README.md", "upstream commit\n")
    (world.clone / "src/server/aiChat.ts").write_text("dirty edit\n")
    result = world.run()
    assert result.returncode == 2 and "No patches applied" in result.stdout
    assert text(world) == "dirty edit\n"  # restored from the stash
    assert "cadam-local-sync-" in git(world.clone, "stash", "list")  # and the stash is kept


def test_failed_pull_recovery_preserves_staged_and_unstaged_content_separately(world):
    (world.clone / "README.md").write_text("local commit\n")
    git(world.clone, "commit", "-qam", "local only")
    world.advance("README.md", "upstream commit\n")
    target = world.clone / "src/server/aiChat.ts"
    target.write_text("staged edit\n")
    git(world.clone, "add", "src/server/aiChat.ts")
    target.write_text("unstaged edit\n")
    result = world.run()
    assert result.returncode == 2
    assert git(world.clone, "show", ":src/server/aiChat.ts") == "staged edit"  # the index content came back
    assert target.read_text() == "unstaged edit\n"
    stash = git(world.clone, "stash", "list")
    assert "cadam-local-sync-" in stash and "stash@{0}" in result.stdout  # kept, and its ref is reported
    assert git(world.clone, "show", "stash@{0}^2:src/server/aiChat.ts") == "staged edit"  # the stash still holds the staged version


def test_failed_pull_recovery_that_conflicts_keeps_the_stash_and_names_it(world):
    (world.clone / "src/server/aiChat.ts").write_text("my edit\n")
    git(world.clone, "commit", "-qam", "local commit touching the same file")
    world.advance("src/server/aiChat.ts", "upstream edit\n")
    (world.clone / "src/server/aiChat.ts").write_text("dirty on top\n")
    result = world.run()
    assert result.returncode == 2
    assert "stash@{0}" in result.stdout and "cadam-local-sync-" in git(world.clone, "stash", "list")


def test_script_is_executable_and_runs_directly(world):
    assert git(ROOT, "ls-files", "-s", "scripts/cadam_local_sync.sh").startswith("100755")
    assert os.access(SCRIPT, os.X_OK)
    env = {**ENV, "CADAM_DIR": str(world.clone), "PATCH_DIR": str(world.patches)}
    done = subprocess.run([str(SCRIPT), "--help"], env=env, capture_output=True, text=True)
    assert done.returncode == 0 and "cadam_local_sync.sh" in done.stdout
    ran = subprocess.run([str(SCRIPT), "--no-install"], env=env, capture_output=True, text=True)
    assert ran.returncode == 0 and text(world) == PATCHED


def test_dry_run_changes_nothing(world):
    world.advance()
    before = git(world.clone, "rev-parse", "HEAD")
    result = world.run("--dry-run")
    assert result.returncode == 0 and "dry run" in result.stdout
    assert git(world.clone, "rev-parse", "HEAD") == before and text(world) == ORIGINAL


def test_never_pushes(world):
    world.advance()
    before = git(world.upstream, "rev-parse", "main")
    world.run()
    assert git(world.upstream, "rev-parse", "main") == before


def test_npm_install_runs_in_the_clone_after_the_patches(world, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "npm-ran"
    fake = bin_dir / "npm"
    fake.write_text(f'#!/bin/sh\npwd > "{marker}"; cat src/server/aiChat.ts >> "{marker}"\n')
    fake.chmod(0o755)
    env = {**ENV, "CADAM_DIR": str(world.clone), "PATCH_DIR": str(world.patches), "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
    ran = marker.read_text()
    assert ran.startswith(str(world.clone)) and "provider = fallback" in ran  # patched before install


def test_refuses_a_non_clone(tmp_path):
    result = subprocess.run(["bash", str(SCRIPT)], env={**ENV, "CADAM_DIR": str(tmp_path)}, capture_output=True, text=True)
    assert result.returncode == 64 and "not a git clone" in result.stdout


# ---- the committed patch set ------------------------------------------------------------------


def test_committed_patches_are_wellformed_and_scoped():
    diffs = sorted(PATCHES.glob("*.diff"))
    assert [d.name for d in diffs] == ["0001-openrouter-fallback.diff", "0002-cli-bridge-provider-switch.diff", "0003-openrouter-anthropic-pin.diff"]
    assert re.fullmatch(r"[0-9a-f]{40}", (PATCHES / "BASE").read_text().strip())
    for diff in diffs:
        touched = set(re.findall(r"^diff --git a/(\S+) b/", diff.read_text(), re.M))
        assert touched <= {"src/server/aiChat.ts", "src/server/anthropic.ts"}, (diff.name, touched)
        subprocess.run(["git", "apply", "--stat", str(diff)], check=True, capture_output=True)  # parses
    switch = (PATCHES / "0002-cli-bridge-provider-switch.diff").read_text()
    assert "CADAM_PROVIDER_OVERRIDE" in switch and "cli-bridge" in switch
    assert "anthropic/" in switch and "openai/" in switch and "ANTHROPIC_BASE_URL" in switch
