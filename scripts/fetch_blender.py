"""Fetch the pinned Blender LTS into ~/.cache/makerbench/blender (outside the repo).

The only network-touching path of the Blender runner (#1040). Downloads the
official tarball from a release mirror, verifies the pinned sha256 before
unpacking, and prints the binary path. Idempotent.

    python scripts/fetch_blender.py
"""

from __future__ import annotations

import sys
import tarfile
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from makerbench import blender_runner as br  # noqa: E402


def fetch() -> Path:
    binary = br.pinned_binary()
    if binary.is_file():
        return binary
    br.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tarball = br.CACHE_DIR / br.PINNED_TARBALL
    if not tarball.is_file() or br.sha256_file(tarball) != br.PINNED_SHA256:
        last: Exception | None = None
        for mirror in br.PINNED_MIRRORS:
            try:
                urllib.request.urlretrieve(f"{mirror}/{br.PINNED_TARBALL}", tarball)  # noqa: S310
                if br.sha256_file(tarball) == br.PINNED_SHA256:
                    break
                last = RuntimeError(f"checksum mismatch from {mirror}")
            except OSError as exc:
                last = exc
        else:
            tarball.unlink(missing_ok=True)
            raise SystemExit(f"could not fetch a checksum-valid {br.PINNED_TARBALL}: {last}")
    dest = br.CACHE_DIR / br.PINNED_VERSION
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball) as tf:
        members = tf.getmembers()
        top = members[0].name.split("/")[0]
        for m in members:
            m.name = m.name[len(top) :].lstrip("/")
            if not m.name or m.name.startswith("..") or m.name.startswith("/"):
                continue
            tf.extract(m, dest)  # noqa: S202 - checksum-verified official tarball
    return binary


if __name__ == "__main__":
    print(fetch())
