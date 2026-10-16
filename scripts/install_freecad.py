"""Download, verify and extract the pinned Linux x86_64 AppImage; no FUSE/root."""

from __future__ import annotations

import argparse
import hashlib
import platform
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from makerbench.freecad_backend import APPIMAGE_NAME, APPIMAGE_SHA256, APPIMAGE_URL


def install(destination: Path) -> Path:
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "AMD64"}:
        raise RuntimeError("Pinned FreeCAD distribution requires Linux x86_64 (including WSL2)")
    destination = destination.resolve()
    appdir = destination / "squashfs-root"
    marker = appdir / ".makerbench-appimage-sha256"
    if marker.is_file() and marker.read_text().strip() == APPIMAGE_SHA256:
        if (appdir / "AppRun").is_file() and (appdir / "usr/bin/python").is_file():
            print(appdir)
            return appdir
    if appdir.exists():
        raise RuntimeError(
            f"Incomplete or mismatched extraction at {appdir}; use a fresh destination"
        )
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="freecad-download-", dir=destination) as scratch:
        image = Path(scratch) / APPIMAGE_NAME
        urllib.request.urlretrieve(APPIMAGE_URL, image)
        digest = hashlib.sha256()
        with image.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != APPIMAGE_SHA256:
            raise RuntimeError("FreeCAD AppImage SHA-256 mismatch; refusing to execute")
        image.chmod(0o755)
        subprocess.run(
            [str(image), "--appimage-extract"], cwd=scratch, stdout=subprocess.DEVNULL, check=True
        )
        (Path(scratch) / "squashfs-root").rename(appdir)
        marker.write_text(APPIMAGE_SHA256 + "\n")
    print(appdir)
    return appdir


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    install(parser.parse_args().destination)
