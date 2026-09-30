"""Offline wheel install: runtime/provenance must use this distribution's metadata."""
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("legacy", [False, True])
def test_installed_package_version_matches_metadata_not_legacy(tmp_path, legacy):
    version = "9.8.7"
    wheel = tmp_path / f"makerbench_hwe-{version}-py3-none-any.whl"
    info = f"makerbench_hwe-{version}.dist-info"
    entries = {}
    for name in ("__init__.py", "provenance.py", "render.py", "run_log_io.py"):
        entries["makerbench/" + name] = (ROOT / "makerbench" / name).read_bytes()
    entries[info + "/METADATA"] = (
        f"Metadata-Version: 2.1\nName: makerbench-hwe\nVersion: {version}\n"
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
        "print(json.dumps([m.version('makerbench-hwe'),makerbench.__version__,"
        "grader_environment()['makerbench']]))"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path,
                            check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == [version, version, version]
