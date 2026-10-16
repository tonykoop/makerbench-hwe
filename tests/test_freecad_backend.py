"""Lightweight runner/installer checks; never download or import FreeCAD."""

import subprocess
from pathlib import Path

import pytest

from makerbench import freecad_backend as backend
from scripts import install_freecad


def test_missing_runtime_is_explicit(monkeypatch, tmp_path):
    monkeypatch.delenv("MAKERBENCH_FREECAD_APPDIR", raising=False)
    with pytest.raises(backend.FreeCADError, match="MAKERBENCH_FREECAD_APPDIR"):
        backend.run_freecad(tmp_path / "source.py", tmp_path / "out")


def test_unpinned_runtime_cannot_execute(tmp_path):
    with pytest.raises(backend.FreeCADError, match="checksum-pinned"):
        backend.run_freecad(tmp_path / "source.py", tmp_path / "out", appdir=tmp_path)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout(timeout, tmp_path):
    with pytest.raises(ValueError, match="finite and positive"):
        backend.run_freecad(tmp_path / "s.py", tmp_path / "out", timeout_s=timeout)


def test_installer_rejects_bad_checksum_before_execution(monkeypatch, tmp_path):
    monkeypatch.setattr(install_freecad.platform, "system", lambda: "Linux")
    monkeypatch.setattr(install_freecad.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        install_freecad.urllib.request,
        "urlretrieve",
        lambda url, path: Path(path).write_bytes(b"corrupt download"),
    )

    def no_execution(*args, **kwargs):
        pytest.fail("Checksum mismatch must never execute the downloaded file")

    monkeypatch.setattr(subprocess, "run", no_execution)
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        install_freecad.install(tmp_path / "install")
    assert not (tmp_path / "install/squashfs-root").exists()


def test_installer_cache_hit_does_not_download(monkeypatch, tmp_path):
    runtime = tmp_path / "squashfs-root"
    (runtime / "usr/bin").mkdir(parents=True)
    (runtime / "AppRun").touch()
    (runtime / "usr/bin/python").touch()
    (runtime / ".makerbench-appimage-sha256").write_text(backend.APPIMAGE_SHA256)

    def no_download(*args):
        pytest.fail("Cache hit downloaded the AppImage")

    monkeypatch.setattr(install_freecad.urllib.request, "urlretrieve", no_download)
    assert install_freecad.install(tmp_path) == runtime
