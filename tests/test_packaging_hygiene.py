"""Packaging hygiene guards for importable public modules."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_adapters_has_one_import_target():
    """The live adapters API is the package, not a shadowing sibling module."""
    adapters_package = ROOT / "makerbench" / "adapters"
    shadow_module = adapters_package.with_suffix(".py")

    assert adapters_package.is_dir()
    assert not shadow_module.exists()

    spec = importlib.util.find_spec("makerbench.adapters")
    assert spec is not None
    assert spec.origin == str(adapters_package / "__init__.py")
    assert spec.submodule_search_locations is not None
