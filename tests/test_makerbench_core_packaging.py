"""The standalone ``makerbench-core`` distribution stays lightweight and real.

Issue #80 promises a genuine ``pip install makerbench-core`` install surface
that is separate from the heavy MakerBench harness wheel. These tests enforce
the contract mechanically so it can't silently rot:

* a dedicated build config exists under ``packaging/makerbench-core/``,
* it declares the ``makerbench-core`` distribution with **zero** runtime
  dependencies (the whole point of the lightweight surface),
* it exposes the ``makerbench-dfm-score`` console script,
* it builds from the shared ``makerbench_core`` source (single source of truth),
* and importing ``makerbench_core`` pulls in **no** heavy harness dependency.

Parsed without ``tomllib`` so the test runs on the repo's Python 3.10 floor
(``tomllib`` is 3.11+).
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = ROOT / "packaging" / "makerbench-core"
PKG_PYPROJECT = PKG_DIR / "pyproject.toml"

# Heavy third-party packages the full harness needs but the portable score must
# never require at runtime.
HEAVY_DEPS = (
    "trimesh",
    "manifold3d",
    "rtree",
    "scipy",
    "networkx",
    "numpy",
    "shapely",
    "pydantic",
    "typer",
    "rich",
)


def _scalar(text: str, key: str) -> str | None:
    match = re.search(rf'(?m)^\s*{re.escape(key)}\s*=\s*"([^"]*)"', text)
    return match.group(1) if match else None


def _array(text: str, key: str) -> list[str] | None:
    match = re.search(rf"(?m)^\s*{re.escape(key)}\s*=\s*(\[.*?\])", text, re.DOTALL)
    if not match:
        return None
    value = ast.literal_eval(match.group(1))
    return list(value) if isinstance(value, list) else None


def test_standalone_build_config_exists():
    assert PKG_PYPROJECT.is_file(), "packaging/makerbench-core/pyproject.toml is missing"
    assert (PKG_DIR / "README.md").is_file(), "standalone README is missing"


def test_distribution_name_and_version():
    text = PKG_PYPROJECT.read_text(encoding="utf-8")
    assert _scalar(text, "name") == "makerbench-core"
    # The portable scorer falls back to "0.1.0"; keep the distribution pinned to
    # the same value so a cited ``makerbench-core==X.Y.Z`` is consistent.
    assert _scalar(text, "version") == "0.1.0"


def test_zero_runtime_dependencies():
    text = PKG_PYPROJECT.read_text(encoding="utf-8")
    deps = _array(text, "dependencies")
    assert deps == [], f"makerbench-core must have zero runtime deps, got {deps!r}"


def test_exposes_dfm_score_console_script():
    text = PKG_PYPROJECT.read_text(encoding="utf-8")
    assert 'makerbench-dfm-score = "makerbench_core.cli:main"' in text


def test_builds_from_shared_core_source():
    text = PKG_PYPROJECT.read_text(encoding="utf-8")
    # Force-include keeps a single source of truth instead of vendoring a copy.
    assert '"../../makerbench_core" = "makerbench_core"' in text
    shared = (PKG_DIR / ".." / ".." / "makerbench_core").resolve()
    assert shared == (ROOT / "makerbench_core").resolve()
    assert (shared / "scoring.py").is_file()


def test_readme_advertises_pip_install():
    text = (PKG_DIR / "README.md").read_text(encoding="utf-8")
    assert "pip install makerbench-core" in text


def test_core_import_pulls_in_no_heavy_dependency():
    """Importing the package must not drag in the harness's heavy stack."""
    code = (
        "import sys, makerbench_core\n"
        f"heavy = [m for m in {HEAVY_DEPS!r} if m in sys.modules]\n"
        "print(','.join(heavy))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    leaked = [m for m in proc.stdout.strip().split(",") if m]
    assert leaked == [], f"makerbench_core leaked heavy imports: {leaked}"


def test_scoring_runs_with_only_stdlib(tmp_path):
    """A score can be produced in a subprocess that imports no heavy deps."""
    artifact = tmp_path / "candidate.scad"
    artifact.write_text("cube([10, 10, 2]);\n", encoding="utf-8")
    code = (
        "import sys\n"
        "from makerbench_core import score_file\n"
        "r = score_file(sys.argv[1])\n"
        f"heavy = [m for m in {HEAVY_DEPS!r} if m in sys.modules]\n"
        "print(r.makerbench_dfm_score, ';'.join(heavy))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code, str(artifact)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    score_str, _, heavy = proc.stdout.strip().partition(" ")
    assert float(score_str) >= 0.0
    assert heavy == "", f"scoring leaked heavy imports: {heavy}"
