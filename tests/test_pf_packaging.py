"""Packaging hygiene for prototyping_funnel (epic #240).

Verifies that pyproject.toml declares the CLI entry-point, the package is
included in the wheel build, and the public __all__ is consistent with the
module contents.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
PF_INIT = ROOT / "prototyping_funnel" / "__init__.py"


def _scalar(text: str, key: str) -> str | None:
    match = re.search(rf'(?m)^\s*{re.escape(key)}\s*=\s*"([^"]*)"', text)
    return match.group(1) if match else None


def test_pyproject_includes_prototyping_funnel_package():
    text = PYPROJECT.read_text(encoding="utf-8")
    assert "prototyping_funnel" in text


def test_pyproject_declares_protofunnel_cli():
    text = PYPROJECT.read_text(encoding="utf-8")
    assert 'makerbench-protofunnel = "prototyping_funnel.cli:main"' in text


def test_prototyping_funnel_package_is_importable():
    import prototyping_funnel  # noqa: F401 (import check)
    assert hasattr(prototyping_funnel, "PrototypingFunnel")
    assert hasattr(prototyping_funnel, "ManufacturingBridge")
    assert hasattr(prototyping_funnel, "breakeven_analysis")
    assert hasattr(prototyping_funnel, "run_funnel_stages")
    assert hasattr(prototyping_funnel, "build_report")


def test_all_exported_symbols_importable():
    import prototyping_funnel as pf
    for name in pf.__all__:
        assert hasattr(pf, name), f"__all__ declares {name!r} but it is not importable"


def test_cli_module_has_main_and_build_parser():
    from prototyping_funnel.cli import build_parser, main
    parser = build_parser()
    assert parser is not None
    assert callable(main)


def test_cli_build_parser_has_required_args():
    from prototyping_funnel.cli import build_parser
    parser = build_parser()
    # Parse with just a dummy artifact path — should not raise
    args = parser.parse_args(["dummy.step"])
    assert args.artifact == "dummy.step"
    assert args.dfm_pass_threshold == 80.0
    assert args.quantity == 1
    assert args.sale_price is None


def test_submodules_are_all_present():
    expected = {"stages", "cost_model", "mfg_bridge", "roi", "runner", "report", "cli"}
    for module in expected:
        path = ROOT / "prototyping_funnel" / f"{module}.py"
        assert path.is_file(), f"missing module: prototyping_funnel/{module}.py"
