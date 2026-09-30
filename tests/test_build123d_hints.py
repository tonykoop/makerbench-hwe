"""The build123d entrant prompt states the API version and curve signatures (#875)."""
from __future__ import annotations

import inspect
import re

import pytest

from makerbench import build123d_hints as hints
from makerbench import code_cad_providers as providers


def test_version_string_is_pinned():
    # Bump deliberately, together with the signature table, after re-verifying it.
    assert hints.BUILD123D_HINT_VERSION == "0.13.0"
    assert hints.BUILD123D_VERIFIED_VERSIONS == ("0.12.0", "0.13.0")


def test_hint_names_the_given_version_and_falls_back_to_the_pin(monkeypatch):
    assert "installed build123d is 9.9.9" in hints.build123d_hint("9.9.9")
    import importlib.metadata as md

    def missing(_name):
        raise md.PackageNotFoundError

    monkeypatch.setattr(md, "version", missing)
    assert hints.installed_build123d_version() == hints.BUILD123D_HINT_VERSION


def test_prompt_states_version_and_every_signature():
    prompt = providers.BACKEND_SYSTEM["build123d"]
    assert f"installed build123d is {hints.installed_build123d_version()}" in prompt
    for sig in hints.BUILD123D_CURVE_SIGNATURES.values():
        assert sig in prompt
    assert prompt.rstrip().endswith("nothing else.")


def test_prompt_rules_out_end_angle_on_arcs():
    prompt = providers.BACKEND_SYSTEM["build123d"]
    assert "arc_size" in prompt and "NOT an end angle" in prompt
    for sig in hints.BUILD123D_CURVE_SIGNATURES.values():
        assert "end_angle" not in sig


def test_other_backends_are_untouched():
    assert "arc_size" not in providers.BACKEND_SYSTEM["openscad"]
    assert "arc_size" not in providers.BACKEND_SYSTEM["cadquery"]


def _param_names(signature: str) -> list[str]:
    inner = signature[signature.index("(") + 1: signature.rindex(")")]
    return [re.split(r"[=:]", p.strip())[0].strip() for p in inner.split(",") if p.strip()]


def test_hint_matches_installed_build123d():
    b3d = pytest.importorskip("build123d")
    assert b3d.__version__ in hints.BUILD123D_VERIFIED_VERSIONS, (
        "installed build123d is not a version the signature table was verified against; "
        "re-check BUILD123D_CURVE_SIGNATURES and extend BUILD123D_VERIFIED_VERSIONS"
    )
    for name, sig in hints.BUILD123D_CURVE_SIGNATURES.items():
        real = inspect.signature(getattr(b3d, name).__init__)
        real_names = []
        for pname, p in real.parameters.items():
            if pname == "self":
                continue
            if p.kind is p.VAR_POSITIONAL:
                real_names.append("*" + pname)
            elif p.kind is p.KEYWORD_ONLY and "*" not in real_names and not any(n.startswith("*") for n in real_names):
                real_names.append("*")
                real_names.append(pname)
            else:
                real_names.append(pname)
        assert _param_names(sig) == real_names, f"{name}: hint {sig!r} != installed {real}"
