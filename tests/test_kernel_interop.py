"""Kernel-interop architecture note stays present, complete, and dependency-free.

Issue #79 asks for a *design note only*: a clear write-up of the kernel-agnostic
"topology graph in -> DFM report out" boundary that explicitly does NOT add a hard
kernel dependency to the core profile. These tests enforce both halves mechanically
so the note can't silently rot or grow a real `cadcore`/WASM dependency later.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "KERNEL_INTEROP.md"


def _declared_dependencies() -> list[str]:
    """Every dependency string in pyproject (core + every optional group).

    Parsed without ``tomllib`` so the test runs on the repo's Python 3.10 floor
    (``tomllib`` is 3.11+). Each ``name = [ ... ]`` array of string literals under
    ``dependencies`` / ``optional-dependencies`` is recovered with ``ast``.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    specs: list[str] = []
    for match in re.finditer(r"(?:^|\n)\s*[\w.-]+\s*=\s*(\[.*?\])", text, re.DOTALL):
        try:
            value = ast.literal_eval(match.group(1))
        except (ValueError, SyntaxError):
            continue
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            specs.extend(value)
    return specs


def test_kernel_interop_note_exists_and_covers_the_boundary():
    text = DOC.read_text(encoding="utf-8")

    # The three acceptance themes: the agnostic topology-graph boundary, the DFM
    # mapping, and the Rust<->Python / WASM execution feasibility.
    for needle in (
        "Kernel-Agnostic Topology Interop",
        "topology graph in",          # the named boundary
        "TopoGraph",                  # the neutral interchange schema
        "kernel-agnostic seam",
        "cadcore",                    # the concrete motivating kernel
        "arena-based",                # arena/typed-ID topology representation
        "FaceId",
        "PyO3",                       # Rust <-> Python binding feasibility
        "WASM",                       # web-native execution path
        "wasmtime",
        "Hugging Face",               # crash-proof web-native validation env (HF Space)
        "optional_local",            # never gates public CI
        "schema_version",             # versioned interchange
        "fail-closed",                # parsing contract
    ):
        assert needle in text, f"missing required content: {needle!r}"


def test_kernel_interop_note_maps_the_three_named_dfm_checks():
    text = DOC.read_text(encoding="utf-8")
    # Issue #79 names bend allowance, min-wall, and thread engagement explicitly,
    # and the note must tie each to its DFM_RULES catalog rule id.
    for needle in (
        "Minimum wall",
        "B1",
        "Bend allowance",
        "C1",
        "thread",          # thread/fastener engagement
        "E1",
        "estimate_min_wall_mm",
        "_expected_flat_length",
        "step_topology_summary",
    ):
        assert needle in text, f"missing DFM mapping content: {needle!r}"


def test_kernel_interop_note_states_the_no_dependency_boundary():
    text = DOC.read_text(encoding="utf-8").lower()
    assert "design note only" in text
    assert "no core" in text and "dependency" in text


def test_core_profile_adds_no_hard_kernel_or_wasm_dependency():
    """The load-bearing acceptance criterion, enforced against pyproject.

    The note must not have smuggled a real kernel / WASM / Rust-binding dependency
    into the core or dev dependency sets. If a future issue genuinely lands a
    binding, it should be an opt-in extra with its own test — this guard makes that
    a deliberate, visible change rather than an accident of the design note.
    """
    declared = _declared_dependencies()
    # Sanity: the parse actually found the real dependency list, so a green test
    # means "scanned and clean", not "scanned nothing".
    assert any(spec.startswith("trimesh") for spec in declared)

    forbidden = ("cadcore", "wasmtime", "wasmer", "pyo3", "maturin", "wasm")
    for spec in declared:
        # Normalize "pkg>=1.0" / "pkg[extra]" to the bare distribution name.
        name = re.split(r"[<>=!~\[ ]", spec, maxsplit=1)[0].strip().lower()
        assert name not in forbidden, (
            f"core/dev dependency {spec!r} pulls a kernel/WASM stack the #79 design "
            f"note explicitly forbids; it must stay optional-local behind the seam"
        )
