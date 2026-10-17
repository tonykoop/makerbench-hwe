"""#1041: Blender entrant confinement, #963-style parity.

Construction tests need no Blender/bwrap. The ``confined`` tests execute hostile
entrant code under the real sandbox and need the pinned Blender + an
unprivileged bwrap (skipped otherwise; CI's blender job runs them).
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest

from makerbench import blender_runner as br
from makerbench import blender_sandbox as bs
from makerbench import entrant_sandbox as sb

BLENDER = br.resolve_blender()
_pinned = False
if BLENDER is not None and BLENDER.is_file():
    import subprocess

    _pinned = (
        br.parse_version(
            subprocess.run([str(BLENDER), "-b", "--version"], capture_output=True, text=True).stdout
        )
        == br.PINNED_VERSION
    )
_CONFINED_OK = _pinned and sb.sandbox_available()
REQUIRE_CONFINED = os.environ.get("MAKERBENCH_REQUIRE_CONFINED") == "1"
if REQUIRE_CONFINED and not _CONFINED_OK:
    # Hosted CI sets this: a missing runtime must fail collection, never skip green.
    pytest.fail(
        "MAKERBENCH_REQUIRE_CONFINED=1 but the pinned Blender or an unprivileged bwrap "
        "(entrant_sandbox.sandbox_available) is missing",
        pytrace=False,
    )
needs_confined = pytest.mark.skipif(
    not _CONFINED_OK,
    reason="pinned Blender and unprivileged bwrap required",
)


def test_hosted_ci_has_confinement_prerequisites() -> None:
    """Same prerequisite check as the live tests; enforced when the CI flag is set."""
    if not REQUIRE_CONFINED:
        pytest.skip("MAKERBENCH_REQUIRE_CONFINED not set (local run)")
    assert _pinned, "pinned Blender missing in the hosted confinement job"
    assert sb.sandbox_available(), "unprivileged bubblewrap unavailable in the hosted job"


CUBE = "import bpy\nbpy.ops.mesh.primitive_cube_add(size=0.05)\n"


# ---------------------------------------------------------------- construction


def test_missing_bwrap_fails_closed_before_any_execution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sb, "_bwrap", lambda: None)
    fake = tmp_path / "blender"
    fake.write_bytes(b"x")
    ran = []
    monkeypatch.setattr(
        br.subprocess, "run", lambda *a, **k: ran.append(a) or pytest.fail("executed")
    )
    script = tmp_path / "e.py"
    script.write_text(CUBE)
    with pytest.raises(bs.BlenderSandboxUnavailable):
        # version probe is skipped by require_pinned=False + a fake that never runs
        br.run_blender(
            script, tmp_path / "out", blender_bin=fake, require_pinned=False, sandbox=True
        )
    assert not ran or all("--version" in a[0] for a in ran)


def test_command_is_allow_list_only_no_net_cleared_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sb, "_bwrap", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr(sb, "sandbox_available", lambda: True)
    install = tmp_path / "blender-install"
    install.mkdir()
    (install / "blender").write_bytes(b"x")
    job = tmp_path / "job"
    job.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    cmd = bs.wrap_blender(install / "blender", ["-b"], job_dir=job, scratch=scratch)
    argv = cmd.argv
    assert argv[0] == "/usr/bin/bwrap" and argv[1] == "--unshare-net"
    for flag in sb.ISOLATION_FLAGS:
        assert flag in argv
    assert "--clearenv" in argv
    binds = sb.bound_sources(argv)
    sources = {src for _flag, src, _dst in binds}
    assert install.resolve().as_posix() in sources
    assert job.resolve().as_posix() in sources
    # nothing from the host home, /mnt, the repo or private/ is bound
    forbidden = (str(Path.home()), "/mnt", str(Path(br.__file__).resolve().parents[1]), "private")
    for _flag, src, _dst in binds:
        assert not any(src == f or src.startswith(f + "/") for f in forbidden[:3]), src
        assert "private" not in Path(src).parts
    # workspace and install are read-only; only scratch home is writable
    rw = [(src, dst) for flag, src, dst in binds if flag == "--bind"]
    assert rw == [((scratch.resolve() / "home").as_posix(), sb.SANDBOX_HOME)]
    assert argv[argv.index("--") + 1].startswith(bs.BLENDER_MOUNT + "/")
    env = sb.sandbox_env(argv)
    assert env["HOME"] == sb.SANDBOX_HOME and "PYTHONHASHSEED" in env
    assert not any(k.lower().endswith(("_proxy", "token", "key")) for k in env)


# ---------------------------------------------------------------- real confinement


def _probe(tmp_path: Path, body: str) -> dict:
    """Run hostile entrant code confined; it reports by raising a JSON error."""
    script = tmp_path / "probe.py"
    script.write_text(
        "import json, os, socket\nres = {}\n"
        + body
        + "\nraise RuntimeError('PROBE' + json.dumps(res))\n"
    )
    with pytest.raises(br.BlenderRunnerError) as err:
        br.run_blender(script, tmp_path / "out", sandbox=True)
    msg = str(err.value)
    return json.loads(msg[msg.index("PROBE") + 5 : msg.rindex("}") + 1])


@needs_confined
def test_confined_run_matches_unconfined_geometry(tmp_path: Path) -> None:
    script = tmp_path / "cube.py"
    script.write_text(CUBE)
    a = br.run_blender(script, tmp_path / "a", sandbox=True)
    b = br.run_blender(script, tmp_path / "b", sandbox=False)
    assert a.stl_path.read_bytes() == b.stl_path.read_bytes()
    assert a.provenance.bbox_mm == (50.0, 50.0, 50.0)


@needs_confined
def test_host_files_are_not_visible(tmp_path: Path) -> None:
    secret = tmp_path / "secret-host-file.txt"
    secret.write_text("SENTINEL-1041\n")
    home_marker = Path.home()
    res = _probe(
        tmp_path,
        f"res['secret'] = os.path.exists({str(secret)!r})\n"
        f"res['home'] = os.path.exists({str(home_marker)!r})\n"
        "res['mnt'] = os.path.exists('/mnt')\n"
        "res['root'] = sorted(os.listdir('/'))\n",
    )
    assert res["secret"] is False and res["home"] is False and res["mnt"] is False
    assert "home" in res["root"]  # only the synthetic /home/entrant
    assert "mnt" not in res["root"] and "root" not in res["root"]


@needs_confined
def test_repo_and_private_oracles_are_not_reachable(tmp_path: Path) -> None:
    repo = Path(br.__file__).resolve().parents[1]
    res = _probe(
        tmp_path,
        f"res['repo'] = os.path.exists({str(repo)!r})\n"
        f"res['oracles'] = os.path.exists({str(repo / 'private' / 'oracles')!r})\n",
    )
    assert res == {"repo": False, "oracles": False}


@needs_confined
def test_entrant_script_and_blender_install_are_read_only(tmp_path: Path) -> None:
    res = _probe(
        tmp_path,
        "import sys\n"
        "def can_write(p):\n"
        "    try:\n        open(p, 'w').close(); os.unlink(p); return True\n"
        "    except OSError:\n        return False\n"
        "res['install'] = can_write('/opt/makerbench-entrant/blender/pwn')\n"
        "res['job'] = can_write(os.path.join(os.path.dirname(sys.argv[-2]), 'pwn'))\n"
        "res['usr'] = can_write('/usr/pwn')\n"
        "res['home'] = can_write('/home/entrant/ok')\n"
        "open('/etc/makerbench-1041-pwn', 'w').close()  # lands on the throwaway root tmpfs\n",
    )
    assert not Path("/etc/makerbench-1041-pwn").exists()  # never reaches the host
    assert res["job"] is False
    assert res["install"] is False and res["usr"] is False
    assert res["home"] is True  # the one writable scratch path


@needs_confined
def test_network_is_unreachable(tmp_path: Path) -> None:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        res = _probe(
            tmp_path,
            "try:\n"
            f"    socket.create_connection(('127.0.0.1', {port}), timeout=3); res['loopback'] = True\n"
            "except OSError:\n    res['loopback'] = False\n"
            "try:\n"
            "    socket.create_connection(('1.1.1.1', 53), timeout=3); res['internet'] = True\n"
            "except OSError:\n    res['internet'] = False\n",
        )
    finally:
        srv.close()
    assert res == {"loopback": False, "internet": False}


@needs_confined
def test_environment_does_not_leak_host_secrets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MAKERBENCH_FAKE_TOKEN_1041", "hunter2")
    res = _probe(
        tmp_path,
        "res['env'] = sorted(os.environ)\nres['val'] = os.environ.get('MAKERBENCH_FAKE_TOKEN_1041')\n",
    )
    assert res["val"] is None
    assert os.environ["MAKERBENCH_FAKE_TOKEN_1041"] == "hunter2"


# ---------------------------------------------------------------- output collection (P1)


def _stl_bytes() -> bytes:
    import struct

    tri = struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 10, 0, 0, 0, 20, 0, 0)
    return br.STL_HEADER.ljust(80, b"\0") + struct.pack("<I", 1) + tri


class TestCollectOutput:
    def test_regular_file_is_copied(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        (home / "output.stl").write_bytes(_stl_bytes())
        assert bs.collect_output(home, "output.stl", tmp_path / "dest.stl") is True
        assert (tmp_path / "dest.stl").read_bytes() == _stl_bytes()

    def test_missing_output_is_not_an_error(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        assert bs.collect_output(home, "output.stl", tmp_path / "dest.stl") is False

    def test_symlink_to_host_file_is_refused_and_not_copied(self, tmp_path: Path) -> None:
        secret = tmp_path / "host-secret.stl"
        secret.write_bytes(_stl_bytes())
        home = tmp_path / "home"
        home.mkdir()
        os.symlink(secret, home / "output.stl")
        with pytest.raises(bs.UnsafeOutput):
            bs.collect_output(home, "output.stl", tmp_path / "dest.stl")
        assert not (tmp_path / "dest.stl").exists()

    def test_dangling_symlink_and_fifo_and_dir_are_refused(self, tmp_path: Path) -> None:
        for kind in ("dangling", "fifo", "dir"):
            home = tmp_path / kind
            home.mkdir()
            target = home / "output.stl"
            if kind == "dangling":
                os.symlink("/nonexistent/host/file", target)
            elif kind == "fifo":
                os.mkfifo(target)
            else:
                target.mkdir()
            with pytest.raises(bs.UnsafeOutput):
                bs.collect_output(home, "output.stl", tmp_path / f"{kind}.stl")

    def test_symlinked_scratch_home_is_refused(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        (real / "output.stl").write_bytes(_stl_bytes())
        os.symlink(real, tmp_path / "home")
        with pytest.raises(bs.UnsafeOutput):
            bs.collect_output(tmp_path / "home", "output.stl", tmp_path / "dest.stl")

    def test_hardlinked_and_oversize_output_are_refused(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        outside = tmp_path / "outside.stl"
        outside.write_bytes(_stl_bytes())
        os.link(outside, home / "output.stl")
        with pytest.raises(bs.UnsafeOutput):
            bs.collect_output(home, "output.stl", tmp_path / "dest.stl")
        (home / "output.stl").unlink()
        (home / "output.stl").write_bytes(b"x" * 100)
        with pytest.raises(bs.UnsafeOutput):
            bs.collect_output(home, "output.stl", tmp_path / "dest.stl", max_bytes=10)
        assert not (tmp_path / "dest.stl").exists()


@needs_confined
@pytest.mark.parametrize("exit_style", ["forged_ok_exit0", "plain_error"])
def test_entrant_planted_symlink_output_is_rejected(tmp_path: Path, exit_style: str) -> None:
    """Review of #1075: output.stl -> an absolute host file must never be read."""
    host_file = tmp_path / "host-only.stl"
    host_file.write_bytes(_stl_bytes())  # valid STL: the old code returned success
    script = tmp_path / "evil.py"
    tail = (
        "print('BLENDER_DRIVER_OK', flush=True)\nos._exit(0)\n"
        if exit_style == "forged_ok_exit0"
        else "raise RuntimeError('boom')\n"
    )
    script.write_text(
        f"import os\nos.symlink({str(host_file)!r}, '/home/entrant/output.stl')\n" + tail
    )
    out = tmp_path / "out"
    with pytest.raises(br.BlenderRunnerError):
        br.run_blender(script, out, sandbox=True)
    assert not (out / "output.stl").exists()
