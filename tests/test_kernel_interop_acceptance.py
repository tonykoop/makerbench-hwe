"""Issue #79 acceptance tests for the kernel-interop architecture note."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "KERNEL_INTEROP.md"
PYPROJECT = ROOT / "pyproject.toml"


def _doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_topograph_boundary_is_serialized_and_kernel_agnostic() -> None:
    text = _doc_text()
    squashed = _normalized(text)

    assert "topology graph in" in text
    assert "DFM report out" in text
    assert "artifact_formats: [\"topojson\"]" in text
    assert "plain, serializable graph" in text
    assert "kernel-independent schema" in text

    for field in (
        "solids",
        "faces",
        "edges",
        "loops",
        "vertices",
        "meta",
        "units",
        "kernel_version",
        "tolerance_mm",
        "schema_version",
    ):
        assert field in text

    assert "typed-ID solids/faces/edges/loops/ vertices" in squashed
    assert "Producer provenance" in text
    assert "fail *closed*" in text


def test_dfm_rules_are_mapped_to_topology_graph_queries() -> None:
    text = _doc_text()

    for needle in (
        "Minimum wall",
        "DFM rule **B1**",
        "opposed face pairs",
        "faces[].surface_type",
        "faces[].normal_hint",
        "Bend allowance",
        "DFM rule **C1**",
        "params.radius_mm",
        "edges[].dihedral_deg",
        "Thread / fastener engagement",
        "DFM rules **E1–E4**",
        "params.axis",
        "params.radius_mm",
        "faces[].loops",
        "edges[].length_mm",
    ):
        assert needle in text


def test_rust_wasm_routes_are_feasible_but_optional_local() -> None:
    text = _doc_text()

    for needle in (
        "cadcore",
        "from-scratch pure-Rust",
        "WASM with zero C++",
        "PyO3",
        "maturin",
        "wasmtime",
        "wasmer",
        "Hugging Face",
        "optional_local",
        "no kernel dependency at all",
    ):
        assert needle in text


def test_kernel_interop_does_not_add_kernel_or_wasm_dependencies() -> None:
    pyproject = PYPROJECT.read_text(encoding="utf-8").lower()

    for forbidden in ("cadcore", "wasmtime", "wasmer", "pyo3", "maturin"):
        assert forbidden not in pyproject


def test_related_design_notes_link_back_to_the_kernel_interop_boundary() -> None:
    mesh_compiler = (ROOT / "docs" / "MESH_GEOMETRY_COMPILER.md").read_text(
        encoding="utf-8"
    )

    assert "[`KERNEL_INTEROP.md`](KERNEL_INTEROP.md)" in mesh_compiler
    assert "producer/consumer" in mesh_compiler
    assert "artifact in" in mesh_compiler
