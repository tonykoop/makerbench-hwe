"""Consumer-adapter wiring tests (offtheshelf issue #6).

Unlike tests/test_offtheshelf_adapter.py (offtheshelf issue #4's narrow
back-compat sync for the 20 legacy MB-* fasteners), this covers the *broader*
catalog surface: querying offtheshelf's full mechanical + electronic parts
through makerbench-hwe's parts_search tool contract (category/package/tags/
free-text query), additive to (and independent of) the default
`PartsLibrary().search()`/`.get()` behavior existing graders depend on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from makerbench.catalog.offtheshelf_adapter import load_offtheshelf_catalog
from makerbench.parts import PartsLibrary, as_offtheshelf_catalog_tool


def _write_component(root: Path, category: str, mpn: str, metadata: str) -> None:
    component_dir = root / "components" / category / mpn
    component_dir.mkdir(parents=True)
    (component_dir / "metadata.yaml").write_text(metadata.lstrip(), encoding="utf-8")
    (component_dir / "symbol.json").write_text("{}", encoding="utf-8")
    (component_dir / "footprint.kicad_mod").write_text("(module fixture)", encoding="utf-8")
    (component_dir / "model.step").write_text("fixture", encoding="utf-8")


def _write_offtheshelf_catalog(tmp_path: Path) -> Path:
    root = tmp_path / "offtheshelf"
    _write_component(
        root,
        "electronic",
        "GENERIC-CAP-0805",
        """
mpn: GENERIC-CAP-0805
manufacturer: "(generic)"
description: Generic 0805 chip capacitor.
category: electronic
package: "0805"
files:
  symbol: symbol.json
  footprint: footprint.kicad_mod
  model_step: null
physical:
  length_mm: 2.0
  width_mm: 1.25
  height_mm: 0.6
electrical:
  type: capacitor
provenance:
  license: CC-BY-4.0
  redistributable: true
  source: fixture
  source_url: null
tags: [passive, capacitor, smd, "0805"]
""",
    )
    _write_component(
        root,
        "electronic",
        "GENERIC-SOIC-8",
        """
mpn: GENERIC-SOIC-8
manufacturer: "(generic)"
description: Generic SOIC-8 package.
category: electronic
package: "SOIC-8"
files:
  symbol: symbol.json
  footprint: footprint.kicad_mod
  model_step: model.step
physical:
  length_mm: 5.81
  width_mm: 3.9
  height_mm: 1.75
electrical:
  type: generic-ic-package
  pin_count: 8
provenance:
  license: CC-BY-4.0
  redistributable: true
  source: fixture
  source_url: null
tags: [ic-package, smd, soic, generated]
""",
    )
    _write_component(
        root,
        "electronic",
        "PRIVATE-IC",
        """
mpn: PRIVATE-IC
description: Non-redistributable controller IC.
category: electronic
package: QFN-32
files:
  symbol: symbol.json
  footprint: footprint.kicad_mod
  model_step: model.step
electrical:
  type: controller
provenance:
  license: proprietary
  redistributable: false
  source: fixture
  source_url: null
tags: [controller]
""",
    )
    _write_component(
        root,
        "mechanical",
        "GENERIC-SHCS-M3-10",
        """
mpn: GENERIC-SHCS-M3-10
description: Socket head cap screw, M3x0.5, 10 mm, ISO 4762 / DIN 912.
category: mechanical
package: "ISO 4762 M3"
files:
  symbol: null
  footprint: null
  model_step: model.step
physical:
  length_mm: 10
  width_mm: 5.5
  height_mm: 3.0
  thread: M3
provenance:
  license: CC-BY-4.0
  redistributable: true
  source: fixture
  source_url: null
tags: [fastener, screw, socket-head-cap-screw, shcs, iso-4762, m3]
""",
    )
    return root


def test_load_offtheshelf_catalog_returns_both_categories(tmp_path):
    root = _write_offtheshelf_catalog(tmp_path)

    results = load_offtheshelf_catalog(root)

    mpns = {p["mpn"] for p in results}
    assert mpns == {"GENERIC-CAP-0805", "GENERIC-SOIC-8", "GENERIC-SHCS-M3-10"}
    # PRIVATE-IC excluded by default redistributable_only=True.


def test_load_offtheshelf_catalog_records_are_parts_library_compatible(tmp_path):
    """Every record must carry `part_number` -- the key PartsLibrary.search()/
    .get() index on -- even though these records come from a totally
    different source than the legacy fasteners/bearings/tubing catalog."""
    root = _write_offtheshelf_catalog(tmp_path)

    results = load_offtheshelf_catalog(root)

    for record in results:
        assert record["part_number"] == record["mpn"]


def test_load_offtheshelf_catalog_filters_by_category(tmp_path):
    root = _write_offtheshelf_catalog(tmp_path)

    electronic = load_offtheshelf_catalog(root, category="electronic")
    mechanical = load_offtheshelf_catalog(root, category="mechanical")

    assert {p["mpn"] for p in electronic} == {"GENERIC-CAP-0805", "GENERIC-SOIC-8"}
    assert {p["mpn"] for p in mechanical} == {"GENERIC-SHCS-M3-10"}


def test_load_offtheshelf_catalog_filters_by_package(tmp_path):
    root = _write_offtheshelf_catalog(tmp_path)

    results = load_offtheshelf_catalog(root, package="SOIC-8")

    assert [p["mpn"] for p in results] == ["GENERIC-SOIC-8"]


def test_load_offtheshelf_catalog_filters_by_tags(tmp_path):
    root = _write_offtheshelf_catalog(tmp_path)

    results = load_offtheshelf_catalog(root, tags=["shcs"])

    assert [p["mpn"] for p in results] == ["GENERIC-SHCS-M3-10"]


def test_load_offtheshelf_catalog_free_text_query(tmp_path):
    root = _write_offtheshelf_catalog(tmp_path)

    results = load_offtheshelf_catalog(root, query="soic")

    assert [p["mpn"] for p in results] == ["GENERIC-SOIC-8"]


def test_load_offtheshelf_catalog_respects_redistributable_only(tmp_path):
    root = _write_offtheshelf_catalog(tmp_path)

    assert load_offtheshelf_catalog(root, query="controller") == []
    hits = load_offtheshelf_catalog(root, query="controller", redistributable_only=False)
    assert [p["mpn"] for p in hits] == ["PRIVATE-IC"]


def test_load_offtheshelf_catalog_resolves_file_paths(tmp_path):
    root = _write_offtheshelf_catalog(tmp_path)

    results = load_offtheshelf_catalog(root, package="SOIC-8")

    assert results[0]["resolved_files"]["footprint"] == (
        "components/electronic/GENERIC-SOIC-8/footprint.kicad_mod"
    )
    assert results[0]["resolved_files"]["model_step"] == (
        "components/electronic/GENERIC-SOIC-8/model.step"
    )


def test_load_offtheshelf_catalog_requires_components_dir(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_offtheshelf_catalog(tmp_path / "not-a-checkout")


def test_load_offtheshelf_catalog_respects_limit(tmp_path):
    root = _write_offtheshelf_catalog(tmp_path)

    results = load_offtheshelf_catalog(root, limit=1)

    assert len(results) == 1


def test_parts_library_search_offtheshelf_catalog_requires_root(tmp_path, monkeypatch):
    monkeypatch.delenv("MAKERBENCH_OFFTHESHELF_ROOT", raising=False)
    lib = PartsLibrary()

    with pytest.raises(FileNotFoundError, match="no offtheshelf checkout configured"):
        lib.search_offtheshelf_catalog()


def test_parts_library_search_offtheshelf_catalog_with_param(tmp_path):
    root = _write_offtheshelf_catalog(tmp_path)
    lib = PartsLibrary()  # default fastener/bearing/tube catalog, untouched

    results = lib.search_offtheshelf_catalog(offtheshelf_root=root, category="electronic")

    assert {p["mpn"] for p in results} == {"GENERIC-CAP-0805", "GENERIC-SOIC-8"}
    # The default catalog this PartsLibrary loaded is completely unaffected.
    assert lib.get("MB-SHCS-M3-08") is not None


def test_parts_library_search_offtheshelf_catalog_env_var(tmp_path, monkeypatch):
    root = _write_offtheshelf_catalog(tmp_path)
    monkeypatch.setenv("MAKERBENCH_OFFTHESHELF_ROOT", str(root))
    # Bypass PartsLibrary.__init__'s own env-var-triggered fastener sync (issue
    # #4's back-compat path, which expects MB-* parts this fixture doesn't
    # have) by supplying an explicit (empty, unused) catalog -- isolates this
    # test to search_offtheshelf_catalog()'s own env-var handling.
    lib = PartsLibrary(catalog={"parts": []})

    results = lib.search_offtheshelf_catalog(package="SOIC-8")

    assert [p["mpn"] for p in results] == ["GENERIC-SOIC-8"]


def test_as_offtheshelf_catalog_tool_returns_none_without_root(monkeypatch):
    monkeypatch.delenv("MAKERBENCH_OFFTHESHELF_ROOT", raising=False)

    assert as_offtheshelf_catalog_tool() is None


def test_as_offtheshelf_catalog_tool_is_parts_search_shaped(tmp_path):
    root = _write_offtheshelf_catalog(tmp_path)

    tool = as_offtheshelf_catalog_tool(root)

    assert tool is not None
    hits = tool(category="mechanical")
    assert [p["mpn"] for p in hits] == ["GENERIC-SHCS-M3-10"]
    # Callable takes the same kwargs as the fixture-catalog parts_search tool.
    assert tool(query="capacitor")[0]["mpn"] == "GENERIC-CAP-0805"


def test_as_offtheshelf_catalog_tool_via_env_var(tmp_path, monkeypatch):
    root = _write_offtheshelf_catalog(tmp_path)
    monkeypatch.setenv("MAKERBENCH_OFFTHESHELF_ROOT", str(root))

    tool = as_offtheshelf_catalog_tool()

    assert tool is not None
    assert [p["mpn"] for p in tool(package="SOIC-8")] == ["GENERIC-SOIC-8"]
