"""Confinement tests for the sandboxed OpenSCAD compiler (#788 W0).

Construction tests are hermetic. The real-sandbox tests run ``openscad``
inside the actual Bubblewrap wrapper against sentinels placed outside the
workspace. They are skipped when an unprivileged bwrap, openscad or xvfb-run
is missing, unless ``MAKERBENCH_REQUIRE_SANDBOX=1`` (CI), in which case a
missing sandbox is a failure rather than a silent skip.
"""

from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path

import pytest

from makerbench import render, scad_sandbox
from makerbench.code_cad_arena_runner import compiler_for_backend
from makerbench.code_cad_objective import RenderArtifacts

REQUIRE_SANDBOX = os.environ.get("MAKERBENCH_REQUIRE_SANDBOX") == "1"
_AVAILABLE = scad_sandbox.sandbox_available()

if REQUIRE_SANDBOX and not _AVAILABLE:  # pragma: no cover - CI guard
    pytest.fail(
        "MAKERBENCH_REQUIRE_SANDBOX=1 but the OpenSCAD sandbox cannot start "
        "(need bwrap + openscad + xvfb-run + unprivileged user namespaces)",
        pytrace=False,
    )

needs_sandbox = pytest.mark.skipif(not _AVAILABLE, reason="OpenSCAD sandbox unavailable here")

CUBE = "w = 10;\ncube([w, w, w]);\n"


def _facets(stl_path: Path) -> int:
    return stl_path.read_text(encoding="utf-8", errors="replace").count("facet normal")


def _binds(cmd: list[str]) -> list[tuple[str, str, str]]:
    out = []
    for i, tok in enumerate(cmd):
        if tok in {"--ro-bind", "--bind", "--dev-bind"}:
            out.append((tok, cmd[i + 1], cmd[i + 2]))
    return out


# --- construction (hermetic) -------------------------------------------------


class TestBuildCommand:
    def test_isolation_flags_runtime_and_env_allowlist(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scad_sandbox, "_bwrap", lambda: "/usr/bin/bwrap")
        monkeypatch.setattr(scad_sandbox, "_openscad", lambda: "/usr/bin/openscad")
        monkeypatch.setattr(scad_sandbox, "_xvfb_run", lambda: "/usr/bin/xvfb-run")
        work, out = tmp_path / "work", tmp_path / "out"
        cmd = scad_sandbox.build_command(
            work_dir=work, out_dir=out, openscad_args=["-o", "/out/output.stl", "/work/input.scad"],
            with_xvfb=False,
        )
        for flag in scad_sandbox.ISOLATION_FLAGS:
            assert flag in cmd
        assert "--unshare-net" in cmd
        assert "--clearenv" in cmd
        assert "--die-with-parent" in cmd
        # tmpfs /tmp is size-capped
        i = cmd.index("--tmpfs")
        assert cmd[i - 2:i] == ["--size", str(scad_sandbox.TMPFS_SIZE_BYTES)]
        # env allow-list only
        envs = {cmd[i + 1] for i, t in enumerate(cmd) if t == "--setenv"}
        assert envs == {"PATH", "HOME", "LANG", "TMPDIR"}
        assert cmd[-3:] == ["/usr/bin/openscad", "-o", "/out/output.stl"] or cmd[-4:-1] == [
            "/usr/bin/openscad", "-o", "/out/output.stl"
        ]

    def test_no_root_mnt_home_or_repo_bind_and_workspace_read_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scad_sandbox, "_bwrap", lambda: "/usr/bin/bwrap")
        monkeypatch.setattr(scad_sandbox, "_openscad", lambda: "/usr/bin/openscad")
        monkeypatch.setattr(scad_sandbox, "_xvfb_run", lambda: "/usr/bin/xvfb-run")
        work, out = tmp_path / "work", tmp_path / "out"
        cmd = scad_sandbox.build_command(
            work_dir=work, out_dir=out, openscad_args=["x"], with_xvfb=True
        )
        binds = _binds(cmd)
        sources = {src for _, src, _ in binds}
        repo_root = Path(__file__).resolve().parents[1].as_posix()
        home = Path.home().resolve().as_posix()
        assert "/" not in sources
        assert not any(s.startswith("/mnt") for s in sources)
        assert not any(s == home or s.startswith(home + "/") for s in sources)
        assert not any(s == repo_root or s.startswith(repo_root + "/") for s in sources)
        assert ("--ro-bind", work.as_posix(), "/work") in binds
        writable = [(src, dst) for kind, src, dst in binds if kind == "--bind"]
        assert writable == [(out.as_posix(), "/out")]
        # xvfb-run wraps openscad inside the sandbox, after "--"
        sep = cmd.index("--")
        assert cmd[sep + 1 : sep + 3] == ["/usr/bin/xvfb-run", "-a"]
        assert cmd[sep + 3] == "/usr/bin/openscad"

    def test_missing_binaries_fail_closed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scad_sandbox, "_bwrap", lambda: None)
        with pytest.raises(scad_sandbox.SandboxUnavailable, match="bwrap"):
            scad_sandbox.build_command(
                work_dir=tmp_path / "w", out_dir=tmp_path / "o", openscad_args=[], with_xvfb=False
            )
        monkeypatch.setattr(scad_sandbox, "_bwrap", lambda: "/usr/bin/bwrap")
        monkeypatch.setattr(scad_sandbox, "_openscad", lambda: None)
        with pytest.raises(scad_sandbox.SandboxUnavailable, match="openscad"):
            scad_sandbox.build_command(
                work_dir=tmp_path / "w", out_dir=tmp_path / "o", openscad_args=[], with_xvfb=False
            )
        monkeypatch.setattr(scad_sandbox, "_openscad", lambda: "/usr/bin/openscad")
        monkeypatch.setattr(scad_sandbox, "_xvfb_run", lambda: None)
        with pytest.raises(scad_sandbox.SandboxUnavailable, match="xvfb-run"):
            scad_sandbox.build_command(
                work_dir=tmp_path / "w", out_dir=tmp_path / "o", openscad_args=[], with_xvfb=True
            )

    def test_work_and_out_must_differ_and_be_absolute(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scad_sandbox, "_bwrap", lambda: "/usr/bin/bwrap")
        monkeypatch.setattr(scad_sandbox, "_openscad", lambda: "/usr/bin/openscad")
        with pytest.raises(scad_sandbox.SandboxUnavailable, match="differ"):
            scad_sandbox.build_command(
                work_dir=tmp_path, out_dir=tmp_path, openscad_args=[], with_xvfb=False
            )
        with pytest.raises(scad_sandbox.SandboxUnavailable, match="absolute"):
            scad_sandbox.build_command(
                work_dir=Path("rel"), out_dir=tmp_path, openscad_args=[], with_xvfb=False
            )


class TestFailClosed:
    def test_unavailable_sandbox_starts_no_process(self, tmp_path, monkeypatch):
        src = tmp_path / "a.scad"
        src.write_text(CUBE, encoding="utf-8")
        monkeypatch.setattr(scad_sandbox, "sandbox_available", lambda: False)
        launched = []
        monkeypatch.setattr(
            scad_sandbox.subprocess, "run", lambda *a, **k: launched.append(a) or None
        )
        monkeypatch.setattr(render, "_run", lambda *a, **k: launched.append(("host", a)) or None)
        with pytest.raises(scad_sandbox.SandboxUnavailable):
            scad_sandbox.compile_scad_sandboxed(src, tmp_path / "out")
        assert launched == []
        assert not (tmp_path / "out" / "output.stl").exists()

    def test_bwrap_failure_is_an_environment_error_not_a_candidate_failure(
        self, tmp_path, monkeypatch
    ):
        src = tmp_path / "a.scad"
        src.write_text(CUBE, encoding="utf-8")
        monkeypatch.setattr(scad_sandbox, "sandbox_available", lambda: True)
        monkeypatch.setattr(scad_sandbox, "_bwrap", lambda: "/usr/bin/bwrap")
        monkeypatch.setattr(scad_sandbox, "_openscad", lambda: "/usr/bin/openscad")
        monkeypatch.setattr(scad_sandbox, "_xvfb_run", lambda: "/usr/bin/xvfb-run")

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="bwrap: no permissions")

        monkeypatch.setattr(scad_sandbox.subprocess, "run", fake_run)
        with pytest.raises(scad_sandbox.SandboxUnavailable, match="sandbox failed"):
            scad_sandbox.compile_scad_sandboxed(src, tmp_path / "out")

    def test_empty_png_is_a_failure(self, tmp_path, monkeypatch):
        src = tmp_path / "a.scad"
        src.write_text(CUBE, encoding="utf-8")
        out = tmp_path / "out"
        monkeypatch.setattr(scad_sandbox, "sandbox_available", lambda: True)
        monkeypatch.setattr(scad_sandbox, "_bwrap", lambda: "/usr/bin/bwrap")
        monkeypatch.setattr(scad_sandbox, "_openscad", lambda: "/usr/bin/openscad")
        monkeypatch.setattr(scad_sandbox, "_xvfb_run", lambda: "/usr/bin/xvfb-run")

        def fake_run(cmd, **kwargs):
            # Mimic OpenSCAD with no display: exit 0, mesh fine, PNG empty.
            target = cmd[cmd.index("-o") + 1]
            host = out / Path(target).name
            host.write_bytes(b"solid x\nfacet normal 0 0 0\nendsolid\n" if target.endswith(".stl") else b"")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(scad_sandbox.subprocess, "run", fake_run)
        with pytest.raises(render.CompileError, match="empty or missing PNG"):
            scad_sandbox.compile_scad_sandboxed(src, out)

    def test_bad_timeout_and_cap_env_are_rejected(self, tmp_path, monkeypatch):
        src = tmp_path / "a.scad"
        src.write_text(CUBE, encoding="utf-8")
        monkeypatch.setenv(scad_sandbox.OPENSCAD_TIMEOUT_ENV, "0")
        with pytest.raises(ValueError):
            scad_sandbox.compile_scad_sandboxed(src, tmp_path / "out")
        monkeypatch.delenv(scad_sandbox.OPENSCAD_TIMEOUT_ENV)
        monkeypatch.setenv(scad_sandbox.OUTPUT_CAP_ENV, "-5")
        with pytest.raises(ValueError):
            scad_sandbox.compile_scad_sandboxed(src, tmp_path / "out")


class TestCompilerTable:
    def test_sandboxed_table_refuses_host_backends(self):
        assert compiler_for_backend("openscad", sandboxed=True) is scad_sandbox.compile_scad_sandboxed
        assert (
            compiler_for_backend("cadquery", sandboxed=True)
            is compiler_for_backend("cadquery")
        )
        for backend in ("blender", "solidworks", "fusion"):
            with pytest.raises(ValueError, match="no sandboxed compiler"):
                compiler_for_backend(backend, sandboxed=True)
        with pytest.raises(ValueError, match="unknown arena backend"):
            compiler_for_backend("nope", sandboxed=True)

    def test_unsandboxed_table_is_unchanged(self):
        from makerbench.code_cad_objective import compile_scad_to_artifacts

        assert compiler_for_backend("openscad") is compile_scad_to_artifacts


# --- real sandbox ------------------------------------------------------------


@needs_sandbox
class TestRealSandbox:
    def test_cube_compiles_to_mesh_and_preview(self, tmp_path):
        src = tmp_path / "cube.scad"
        src.write_text(CUBE, encoding="utf-8")
        out = tmp_path / "out"
        artifacts = scad_sandbox.compile_scad_sandboxed(src, out)
        assert isinstance(artifacts, RenderArtifacts)
        assert artifacts.stl_path == out / "output.stl"
        assert artifacts.png_path == out / "preview.png"
        assert _facets(artifacts.stl_path) == 12
        assert artifacts.png_path.stat().st_size > 0
        assert artifacts.png_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        # nothing but the two artifacts lands in out_dir
        assert sorted(p.name for p in out.iterdir()) == ["output.stl", "preview.png"]

    def test_include_of_a_host_file_is_refused_and_undisclosed(self, tmp_path):
        token = secrets.token_hex(12)
        sentinel = tmp_path / "outside" / "sentinel.scad"
        sentinel.parent.mkdir()
        sentinel.write_text(f'echo("{token}");\ncube(40);\n', encoding="utf-8")
        src = tmp_path / "src" / "a.scad"
        src.parent.mkdir()
        src.write_text(f"include <{sentinel.as_posix()}>\ncube(5);\n", encoding="utf-8")
        artifacts = scad_sandbox.compile_scad_sandboxed(src, tmp_path / "out")
        assert _facets(artifacts.stl_path) == 12, "sentinel geometry leaked in"
        blob = "\n".join(artifacts.warnings)
        assert token not in blob
        assert "Can't open include file" in blob
        assert token not in (tmp_path / "out" / "output.stl").read_text(errors="replace")

    def test_sibling_include_is_not_visible_either(self, tmp_path):
        # On the host ``include <lib.scad>`` next to the source works; the
        # sandbox sees only the single copied file.
        token = secrets.token_hex(12)
        (tmp_path / "lib.scad").write_text(f'echo("{token}");\ncube(40);\n', encoding="utf-8")
        src = tmp_path / "a.scad"
        src.write_text("include <lib.scad>\ncube(5);\n", encoding="utf-8")
        artifacts = scad_sandbox.compile_scad_sandboxed(src, tmp_path / "out")
        assert _facets(artifacts.stl_path) == 12
        assert token not in "\n".join(artifacts.warnings)

    def test_surface_and_import_of_host_files_are_refused(self, tmp_path):
        token = secrets.token_hex(12)
        data = tmp_path / "outside" / f"{token}.dat"
        data.parent.mkdir()
        data.write_text("1 2\n3 4\n", encoding="utf-8")
        big = tmp_path / "outside" / "big.stl"
        big.write_text(
            "solid big\n" + "facet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 50 0 0\n"
            "vertex 0 50 0\nendloop\nendfacet\n" * 4 + "endsolid big\n",
            encoding="utf-8",
        )
        src = tmp_path / "a.scad"
        src.write_text(
            f'surface(file="{data.as_posix()}");\nimport("{big.as_posix()}");\ncube(5);\n',
            encoding="utf-8",
        )
        artifacts = scad_sandbox.compile_scad_sandboxed(src, tmp_path / "out")
        assert _facets(artifacts.stl_path) == 12
        assert token not in (tmp_path / "out" / "output.stl").read_text(errors="replace")

    def test_candidate_syntax_error_is_a_compile_error(self, tmp_path):
        src = tmp_path / "bad.scad"
        src.write_text("cube(5;\n", encoding="utf-8")
        with pytest.raises(render.CompileError, match="OpenSCAD exited"):
            scad_sandbox.compile_scad_sandboxed(src, tmp_path / "out")
        assert not (tmp_path / "out" / "output.stl").exists()

    def test_no_geometry_is_a_compile_error(self, tmp_path):
        src = tmp_path / "empty.scad"
        src.write_text("echo(1);\n", encoding="utf-8")
        with pytest.raises(render.CompileError):
            scad_sandbox.compile_scad_sandboxed(src, tmp_path / "out")

    def test_heavy_csg_obeys_the_timeout(self, tmp_path, monkeypatch):
        monkeypatch.setenv(scad_sandbox.OPENSCAD_TIMEOUT_ENV, "1")
        src = tmp_path / "slow.scad"
        src.write_text(
            "union() { for (i = [0:400]) translate([i * 3, 0, 0]) sphere(10, $fn = 120); }\n",
            encoding="utf-8",
        )
        with pytest.raises(render.CompileError, match="timed out"):
            scad_sandbox.compile_scad_sandboxed(src, tmp_path / "out")

    def test_output_cap_is_enforced(self, tmp_path, monkeypatch):
        monkeypatch.setenv(scad_sandbox.OUTPUT_CAP_ENV, "64")
        src = tmp_path / "cube.scad"
        src.write_text(CUBE, encoding="utf-8")
        with pytest.raises(render.CompileError, match="exceeds the sandbox cap"):
            scad_sandbox.compile_scad_sandboxed(src, tmp_path / "out")
        assert not any((tmp_path / "out").iterdir())

    def test_host_secret_env_never_reaches_the_sandbox(self, tmp_path, monkeypatch):
        # OpenSCAD has no getenv, so probe the wrapper itself with the real
        # bwrap argv: run ``env`` where openscad would run.
        monkeypatch.setenv("GH_TOKEN", "w0-canary-" + secrets.token_hex(4))
        work, out = tmp_path / "work", tmp_path / "out"
        work.mkdir(), out.mkdir()
        cmd = scad_sandbox.build_command(
            work_dir=work, out_dir=out, openscad_args=[], with_xvfb=False
        )
        cmd[cmd.index("--") + 1] = "/usr/bin/env"
        proc = subprocess.run(cmd, capture_output=True, text=True, env=scad_sandbox._host_env())
        assert proc.returncode == 0, proc.stderr
        assert "GH_TOKEN" not in proc.stdout
        # bwrap itself sets PWD for --chdir; everything else is our allow-list.
        assert set(line.split("=", 1)[0] for line in proc.stdout.splitlines()) <= {
            "PATH", "HOME", "LANG", "TMPDIR", "PWD"
        }

    def test_repo_home_and_mnt_are_invisible(self, tmp_path):
        work, out = tmp_path / "work", tmp_path / "out"
        work.mkdir(), out.mkdir()
        cmd = scad_sandbox.build_command(
            work_dir=work, out_dir=out, openscad_args=[], with_xvfb=False
        )
        repo_root = Path(__file__).resolve().parents[1].as_posix()
        probe = (
            f'for p in "{repo_root}" "{Path.home().resolve().as_posix()}" /mnt "{tmp_path.as_posix()}"; '
            'do if [ -e "$p" ]; then echo "VISIBLE $p"; fi; done; '
            "touch /work/x 2>/dev/null && echo WRITABLE_WORK; echo done"
        )
        cmd[cmd.index("--") + 1 :] = ["/bin/sh", "-c", probe]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=scad_sandbox._host_env())
        assert proc.returncode == 0, proc.stderr
        assert "VISIBLE" not in proc.stdout
        assert "WRITABLE_WORK" not in proc.stdout
        assert "done" in proc.stdout
