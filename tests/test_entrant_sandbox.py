"""#785: outer Bubblewrap filesystem sandbox for codex/agy arena entrants.

Two layers, no model calls:

* command-construction unit tests (run everywhere a ``bwrap`` binary exists
  or is faked): allow-list binds only, workspace read-only, network shared,
  environment cleared;
* real confinement tests that execute the actual wrapper around ``/bin/sh``
  and ``cat`` (skipped when an unprivileged bwrap can't start).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from makerbench import entrant_sandbox as sb

REPO_ROOT = Path(__file__).resolve().parents[1]

needs_bwrap = pytest.mark.skipif(not sb.sandbox_available(), reason="unprivileged bubblewrap unavailable")


@pytest.fixture
def layout(tmp_path):
    """A fake host: a workspace, a fake $HOME with a secret, a /mnt/c mirror."""

    workspace = tmp_path / "runs" / "code_cad_arena" / "r1" / "gen" / "t0" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "README.md").write_text("workspace readme ok\n", encoding="utf-8")
    fake_home = tmp_path / "home" / "tony"
    (fake_home / ".codex").mkdir(parents=True)
    (fake_home / ".codex" / "auth.json").write_text('{"fake": "auth"}\n', encoding="utf-8")
    (fake_home / "sentinel-home.txt").write_text("SENTINEL-HOME-785\n", encoding="utf-8")
    mnt_mirror = tmp_path / "mnt" / "c" / "Users" / "Tony" / "Documents" / "GitHub" / "worktrees"
    mnt_mirror.mkdir(parents=True)
    (mnt_mirror / "sentinel-mnt.txt").write_text("SENTINEL-MNT-785\n", encoding="utf-8")
    private = tmp_path / "private" / "oracles"
    private.mkdir(parents=True)
    (private / "oracle.json").write_text("SENTINEL-ORACLE-785\n", encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return {
        "workspace": workspace,
        "home": fake_home,
        "home_sentinel": fake_home / "sentinel-home.txt",
        "mnt_sentinel": mnt_mirror / "sentinel-mnt.txt",
        "oracle": private / "oracle.json",
        "scratch": scratch,
    }


def _run(argv, workspace, profile=None):
    with sb.sandboxed(profile or sb.generic_profile(), argv, workspace=workspace) as wrapped:
        return subprocess.run(wrapped.argv, env=wrapped.env, capture_output=True, text=True, timeout=60)


# ---------------------------------------------------------------- construction


class TestCommandConstruction:
    @pytest.fixture(autouse=True)
    def _fake_bwrap(self, monkeypatch):
        monkeypatch.setattr(sb, "_bwrap", lambda: "/usr/bin/bwrap")

    def _build(self, layout, profile=None, argv=("/bin/cat", "README.md")):
        return sb.build_command(
            profile or sb.generic_profile(), list(argv), workspace=layout["workspace"], scratch=layout["scratch"]
        )

    def test_isolation_flags_present_and_network_shared(self, layout):
        cmd = self._build(layout).argv
        for flag in sb.ISOLATION_FLAGS:
            assert flag in cmd
        assert "--unshare-net" not in cmd
        assert "--unshare-all" not in cmd  # would also unshare the network

    def test_never_binds_root_mnt_or_real_home(self, layout):
        cmd = self._build(layout).argv
        binds = sb.bound_sources(cmd)
        home = Path.home().resolve()
        for _flag, source, dest in binds:
            src = Path(source).resolve()
            assert src != Path("/"), "never --ro-bind / /"
            assert dest != "/"
            assert not source.startswith("/mnt") and not dest.startswith("/mnt")
            assert src != home and not home.is_relative_to(src), f"{source} exposes the real $HOME"
            assert not REPO_ROOT.is_relative_to(src), f"{source} exposes the makerbench checkout"

    def test_workspace_is_read_only_at_its_own_path_with_chdir(self, layout):
        cmd = self._build(layout).argv
        ws = layout["workspace"].resolve().as_posix()
        assert ("--ro-bind", ws, ws) in sb.bound_sources(cmd)
        assert ("--bind", ws, ws) not in sb.bound_sources(cmd)
        assert cmd[cmd.index("--chdir") + 1] == ws

    def test_scratch_home_is_the_only_writable_bind(self, layout):
        cmd = self._build(layout).argv
        writable = [b for b in sb.bound_sources(cmd) if b[0] == "--bind"]
        assert writable == [("--bind", (layout["scratch"].resolve() / "home").as_posix(), sb.SANDBOX_HOME)]
        assert ("--tmpfs" in cmd) and cmd[cmd.index("--tmpfs") + 1] == "/tmp"

    def test_environment_is_cleared_and_allowlisted(self, layout, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "should-not-leak")
        monkeypatch.setenv("GH_TOKEN", "should-not-leak")
        wrapped = self._build(layout)
        assert "--clearenv" in wrapped.argv
        env = sb.sandbox_env(wrapped.argv)
        assert set(env) <= {"PATH", "HOME", "LANG", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR", "CODEX_HOME"}
        assert env["HOME"] == sb.SANDBOX_HOME
        assert set(wrapped.env) == {"PATH", "LANG"}  # bwrap's own env
        assert "should-not-leak" not in " ".join(wrapped.argv)

    def test_codex_profile_binds_only_auth_file_read_only(self, layout, tmp_path, monkeypatch):
        package = tmp_path / "pkg" / "0.154.0"
        (package / "bin").mkdir(parents=True)
        (package / "codex-path").mkdir()
        (package / "codex-package.json").write_text("{}", encoding="utf-8")
        exe = package / "bin" / "codex"
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o755)
        monkeypatch.delenv("CODEX_HOME", raising=False)
        monkeypatch.setattr(sb.shutil, "which", lambda name, path=None: str(exe) if name == "codex" else "/usr/bin/bwrap")
        profile = sb.codex_profile("codex", host_home=layout["home"])
        wrapped = self._build(layout, profile, argv=("codex", "exec", "hi"))
        binds = sb.bound_sources(wrapped.argv)
        auth = (layout["home"] / ".codex" / "auth.json").as_posix()
        assert ("--ro-bind", auth, f"{sb.SANDBOX_HOME}/.codex/auth.json") in binds
        assert not any(src == (layout["home"] / ".codex").as_posix() for _f, src, _d in binds)
        assert ("--ro-bind", package.as_posix(), f"{sb.SANDBOX_CLI_ROOT}/codex") in binds
        assert wrapped.argv[wrapped.argv.index("--") + 1] == f"{sb.SANDBOX_CLI_ROOT}/codex/bin/codex"
        env = sb.sandbox_env(wrapped.argv)
        assert env["CODEX_HOME"] == f"{sb.SANDBOX_HOME}/.codex"
        # The mount point placeholder is empty: no token bytes are copied.
        assert (layout["scratch"] / "home" / ".codex" / "auth.json").read_text() == ""

    def test_agy_profile_exposes_only_token_settings_and_id(self, layout, tmp_path, monkeypatch):
        state = layout["home"] / ".gemini" / "antigravity-cli"
        for rel in ("antigravity-oauth-token", "settings.json", "installation_id", "bin/agentapi",
                    "conversations/secret.pb", "brain/x/notes.md", "history.jsonl"):
            (state / rel).parent.mkdir(parents=True, exist_ok=True)
            (state / rel).write_text("x", encoding="utf-8")
        exe = tmp_path / "agy"
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setattr(sb.shutil, "which", lambda name, path=None: str(exe) if name == "agy" else "/usr/bin/bwrap")
        wrapped = self._build(layout, sb.agy_profile("agy", host_home=layout["home"]), argv=("agy", "--print", "hi"))
        sources = {src for _f, src, _d in sb.bound_sources(wrapped.argv)}
        exposed = {Path(s).relative_to(state).as_posix() for s in sources if Path(s).is_relative_to(state)}
        # The real settings.json (with its git/gh/python allow list) is never mounted.
        assert exposed == {"antigravity-oauth-token", "installation_id", "bin/agentapi"}
        assert all(flag == "--ro-bind" for flag, src, _d in sb.bound_sources(wrapped.argv) if Path(src).is_relative_to(state))
        # A generated per-trial settings.json is bound read-only from scratch instead.
        dest = f"{sb.SANDBOX_HOME}/.gemini/antigravity-cli/settings.json"
        settings_binds = [b for b in sb.bound_sources(wrapped.argv) if b[2] == dest]
        assert len(settings_binds) == 1
        flag, source, _dest = settings_binds[0]
        assert flag == "--ro-bind" and Path(source).is_relative_to(layout["scratch"].resolve())
        generated = json.loads(Path(source).read_text(encoding="utf-8"))
        assert generated == sb.agy_settings(layout["workspace"].resolve())

    def test_real_agy_settings_content_is_never_copied(self, layout, tmp_path, monkeypatch):
        state = layout["home"] / ".gemini" / "antigravity-cli"
        state.mkdir(parents=True, exist_ok=True)
        (state / "antigravity-oauth-token").write_text("x", encoding="utf-8")
        (state / "settings.json").write_text(
            json.dumps({"permissions": {"allow": ["command(git commit)", "command(gh)", "command(python3)"]},
                        "model": "host-model-canary"}),
            encoding="utf-8",
        )
        exe = tmp_path / "agy"
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setattr(sb.shutil, "which", lambda name, path=None: str(exe) if name == "agy" else "/usr/bin/bwrap")
        self._build(layout, sb.agy_profile("agy", host_home=layout["home"]), argv=("agy", "--print", "hi"))
        text = (layout["scratch"] / "home" / ".gemini" / "antigravity-cli" / "settings.json").read_text(encoding="utf-8")
        generated = json.loads(text)
        assert "host-model-canary" not in text and "model" not in generated
        assert "command(git commit)" not in text
        for host_rule in ("command(git commit)", "command(gh)", "command(python3)"):
            assert host_rule not in generated["permissions"]["allow"]
        assert generated == sb.agy_settings(layout["workspace"].resolve())


    def test_missing_auth_fails_closed(self, layout, monkeypatch, tmp_path):
        monkeypatch.setattr(sb.shutil, "which", lambda name, path=None: "/usr/bin/true")
        monkeypatch.delenv("CODEX_HOME", raising=False)
        with pytest.raises(sb.SandboxUnavailable):
            sb.codex_profile("codex", host_home=tmp_path / "nobody")
        with pytest.raises(sb.SandboxUnavailable):
            sb.agy_profile("agy", host_home=tmp_path / "nobody")

    def test_missing_bwrap_fails_closed(self, layout, monkeypatch):
        monkeypatch.setattr(sb, "_bwrap", lambda: None)
        with pytest.raises(sb.SandboxUnavailable):
            self._build(layout)
        assert sb.sandbox_available() is False

    def test_refuses_relative_or_home_workspace(self, layout):
        with pytest.raises(sb.SandboxUnavailable):
            sb.build_command(sb.generic_profile(), ["/bin/true"], workspace=Path("runs/ws"), scratch=layout["scratch"])
        with pytest.raises(sb.SandboxUnavailable):
            sb.build_command(sb.generic_profile(), ["/bin/true"], workspace=Path.home(), scratch=layout["scratch"])

    def test_scratch_is_removed_after_the_call(self, layout):
        with sb.sandboxed(sb.generic_profile(), ["/bin/true"], workspace=layout["workspace"]) as wrapped:
            scratch = wrapped.scratch
            assert scratch.is_dir() and not scratch.is_relative_to(REPO_ROOT)
        assert not scratch.exists()


# ------------------------------------------------------------ real confinement


@needs_bwrap
class TestRealConfinement:
    def test_workspace_readme_is_readable(self, layout):
        proc = _run(["/bin/cat", "README.md"], layout["workspace"])
        assert proc.returncode == 0, proc.stderr
        assert "workspace readme ok" in proc.stdout

    def test_sentinel_under_fake_home_is_unreadable(self, layout):
        proc = _run(["/bin/sh", "-c", f"cat {layout['home_sentinel']}"], layout["workspace"])
        assert proc.returncode != 0
        assert "SENTINEL-HOME-785" not in proc.stdout + proc.stderr

    def test_sentinel_under_mnt_c_mirror_is_unreadable(self, layout):
        proc = _run(["/bin/sh", "-c", f"cat {layout['mnt_sentinel']}; ls /mnt"], layout["workspace"])
        assert proc.returncode != 0
        assert "SENTINEL-MNT-785" not in proc.stdout + proc.stderr

    def test_private_oracle_next_to_the_run_is_unreadable(self, layout):
        proc = _run(["/bin/sh", "-c", f"cat {layout['oracle']}; cat ../../../../../private/oracles/oracle.json"],
                    layout["workspace"])
        assert "SENTINEL-ORACLE-785" not in proc.stdout + proc.stderr

    def test_real_home_and_mnt_are_invisible(self, layout):
        home = Path.home()
        proc = _run(["/bin/sh", "-c", f"ls -A {home} 2>/dev/null | head -1; test -e /mnt && echo MNT_VISIBLE; true"],
                    layout["workspace"])
        assert proc.returncode == 0, proc.stderr
        assert "MNT_VISIBLE" not in proc.stdout
        if home.is_dir() and any(home.iterdir()):
            assert proc.stdout.strip() == ""

    def test_writing_into_workspace_fails(self, layout):
        proc = _run(["/bin/sh", "-c", "echo pwned > README.md || exit 3; echo pwned > new.txt || exit 4"],
                    layout["workspace"])
        assert proc.returncode != 0
        assert (layout["workspace"] / "README.md").read_text(encoding="utf-8") == "workspace readme ok\n"
        assert not (layout["workspace"] / "new.txt").exists()

    def test_makerbench_repo_is_invisible(self, layout):
        target = REPO_ROOT / "pyproject.toml"
        assert target.exists()
        proc = _run(["/bin/sh", "-c", f"test -e {target} && echo REPO_VISIBLE; ls {REPO_ROOT} 2>&1 | head -3; true"],
                    layout["workspace"])
        assert "REPO_VISIBLE" not in proc.stdout

    def test_environment_does_not_leak_host_secrets(self, layout, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "leak-canary-785")
        with sb.sandboxed(sb.generic_profile(), ["/usr/bin/env"], workspace=layout["workspace"]) as wrapped:
            proc = subprocess.run(wrapped.argv, env={**os.environ, **wrapped.env}, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert "leak-canary-785" not in proc.stdout
        assert f"HOME={sb.SANDBOX_HOME}" in proc.stdout


class TestAgyGeneratedSettings:
    """Tony, 2026-09-15: sandboxed agy gets a read-only allow list only."""

    WRITE_OR_EXEC = re.compile(
        r"^(write_file|read_url|execute_url|unsandboxed|escalate_admin)\(|"
        r"^command\((sh|bash|zsh|dash|fish|env|xargs|python\d*(\.\d+)?|node|npx|perl|ruby|curl|wget|git|gh|"
        r"rm|mv|cp|tee|touch|mkdir|chmod|dd|sed|awk)\b"
    )

    def test_exact_allow_list(self, tmp_path):
        settings = sb.agy_settings(tmp_path / "ws")
        assert settings["permissions"]["allow"] == [
            "read_file(*)",
            "command(ls)", "command(cat)", "command(find)", "command(grep)",
            "command(head)", "command(tail)", "command(wc)", "command(pwd)",
        ]

    def test_allow_list_has_no_write_interpreter_network_or_git_rules(self, tmp_path):
        for rule in sb.agy_settings(tmp_path / "ws")["permissions"]["allow"]:
            assert not self.WRITE_OR_EXEC.search(rule), f"non-read-only rule in allow list: {rule}"

    def test_deny_list_keeps_write_and_exec_risks_explicit(self, tmp_path):
        deny = set(sb.agy_settings(tmp_path / "ws")["permissions"]["deny"])
        for rule in ("write_file(*)", "read_url(*)", "execute_url(*)", "unsandboxed(*)", "escalate_admin(*)",
                     "command(sh)", "command(bash)", "command(python3)", "command(node)", "command(git)",
                     "command(gh)", "command(curl)", "command(wget)", "command(rm)", "command(find -delete)",
                     "command(find -exec)"):
            assert rule in deny
        assert not deny & set(sb.agy_settings(tmp_path / "ws")["permissions"]["allow"])

    def test_workspace_only_and_no_non_workspace_access(self, tmp_path):
        settings = sb.agy_settings(tmp_path / "ws")
        assert settings["allowNonWorkspaceAccess"] is False
        assert settings["trustedWorkspaces"] == [(tmp_path / "ws").as_posix()]
        assert set(settings) == {"allowNonWorkspaceAccess", "trustedWorkspaces", "permissions"}
        assert set(settings["permissions"]) == {"allow", "deny"}
