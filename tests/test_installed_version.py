"""Offline wheel install: runtime/provenance must use this distribution's metadata."""
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("distribution,legacy", [
    ("makerbench-hwe", False), ("makerbench-hwe", True), ("makerbench", False),
])
def test_installed_package_version_matches_metadata_not_legacy(tmp_path, distribution, legacy):
    version = "9.8.7"
    normalized = distribution.replace("-", "_")
    wheel = tmp_path / f"{normalized}-{version}-py3-none-any.whl"
    info = f"{normalized}-{version}.dist-info"
    entries = {}
    for name in ("__init__.py", "provenance.py", "render.py", "run_log_io.py"):
        entries["makerbench/" + name] = (ROOT / "makerbench" / name).read_bytes()
    for name in ("__init__.py", "scoring.py"):
        entries["makerbench_core/" + name] = (ROOT / "makerbench_core" / name).read_bytes()
    entries[info + "/METADATA"] = (
        f"Metadata-Version: 2.1\nName: {distribution}\nVersion: {version}\n"
    ).encode()
    entries[info + "/WHEEL"] = (
        "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    ).encode()
    entries[info + "/RECORD"] = "".join(f"{name},,\n" for name in entries).encode()
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    target = tmp_path / "installed"
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
         "--disable-pip-version-check", "--target", str(target), str(wheel)],
        check=True, capture_output=True, text=True,
    )
    if legacy:
        decoy = target / "makerbench-0.0.7.dist-info"
        decoy.mkdir()
        (decoy / "METADATA").write_text("Metadata-Version: 2.1\nName: makerbench\nVersion: 0.0.7\n")
    code = (
        f"import sys; sys.path.insert(0, {str(target)!r}); "
        "import importlib.metadata as m, makerbench; "
        "from makerbench.provenance import grader_environment; import json; "
        "from makerbench_core.scoring import _package_version; "
        f"print(json.dumps([m.version({distribution!r}),makerbench.__version__,"
        "grader_environment()['makerbench'],_package_version()]))"
    )
    # Exclude site-packages too: an installed checkout must not mask legacy-only fixtures.
    result = subprocess.run([sys.executable, "-I", "-S", "-c", code], cwd=tmp_path,
                            check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == [version] * 4
