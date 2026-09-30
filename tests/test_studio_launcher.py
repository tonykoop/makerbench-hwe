"""Exercise the launcher as a separate process outside the checkout."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("missing", ["fastapi", "uvicorn"])
@pytest.mark.parametrize("clone", [True, False])
def test_missing_dependencies_are_an_actionable_hint(tmp_path, missing, clone):
    (tmp_path / "sitecustomize.py").write_text(
        "import importlib.util\noriginal=importlib.util.find_spec\n"
        f"importlib.util.find_spec=lambda name,*a,**k: None if name=={missing!r} "
        "else original(name,*a,**k)\n"
    )
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(tmp_path), str(ROOT))))
    result = subprocess.run(
        [sys.executable, "-m", "makerbench.cli", "studio", "--no-browser"],
        cwd=ROOT if clone else tmp_path, env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 1
    assert missing in result.stdout
    assert "Traceback" not in result.stderr + result.stdout
    expected = 'pip install -e ".[studio]"' if clone else 'uv tool install --force'
    assert expected in result.stdout


def test_server_and_browser_start_from_an_unrelated_directory(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("uvicorn")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    opened = tmp_path / "opened.txt"
    browser = tmp_path / "browser"
    browser.write_text(f'#!/bin/sh\nprintf "%s" "$1" > "{opened}"\n')
    browser.chmod(0o755)
    env = dict(os.environ, PYTHONPATH=str(ROOT), BROWSER=f"{browser} %s")
    env.pop("WSL_DISTRO_NAME", None)
    env.pop("WSL_INTEROP", None)
    with (tmp_path / "server.log").open("w") as output:
        proc = subprocess.Popen(
            [sys.executable, "-m", "makerbench.cli", "studio", "--port", str(port)],
            cwd=tmp_path, env=env, stdout=output, stderr=output,
        )
        try:
            url = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
                        assert json.load(response)["status"] == "ok"
                    if opened.exists():
                        break
                except OSError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail((tmp_path / "server.log").read_text())
            assert opened.read_text() == url + "/"
            with urllib.request.urlopen(url + "/api/tasks", timeout=2) as response:
                assert json.load(response)["tasks"]
            with urllib.request.urlopen(url + "/", timeout=2) as response:
                assert "Starting Arena Studio" in response.read().decode()
        finally:
            proc.terminate()
            proc.wait(timeout=10)


def test_wsl_browser_bridge(monkeypatch):
    from makerbench.studio_launcher import open_browser

    calls = []
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/wslview" if name == "wslview" else None)
    monkeypatch.setattr("subprocess.run", lambda args, **kwargs: (
        calls.append(args) or subprocess.CompletedProcess(args, 0)
    ))
    assert open_browser("http://127.0.0.1:8080/")
    assert calls == [["/usr/bin/wslview", "http://127.0.0.1:8080/"]]
