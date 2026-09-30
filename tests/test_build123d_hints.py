"""The build123d entrant prompt states the API version and curve signatures (#875)."""
from __future__ import annotations

import importlib.metadata as md
import inspect

import pytest

from makerbench import build123d_hints as hints
from makerbench import code_cad_providers as providers


def test_version_constants_are_pinned():
    # Bump deliberately, together with the signature table, after re-verifying it.
    assert hints.BUILD123D_HINT_VERSION == "0.13.0"
    assert hints.BUILD123D_VERIFIED_VERSIONS == ("0.12.0", "0.13.0")


def test_prompt_states_every_signature_and_the_arc_rule():
    prompt = providers.BACKEND_SYSTEM["build123d"]
    for sig in hints.BUILD123D_CURVE_SIGNATURES.values():
        assert sig in prompt
    assert "arc_size" in prompt and "NOT an end angle" in prompt
    assert prompt.rstrip().endswith("nothing else.")
    for sig in hints.BUILD123D_CURVE_SIGNATURES.values():
        assert "end_angle" not in sig


def test_other_backends_are_untouched():
    assert "arc_size" not in providers.BACKEND_SYSTEM["openscad"]
    assert "arc_size" not in providers.BACKEND_SYSTEM["cadquery"]


def test_hint_claims_a_check_only_for_verified_versions():
    ok = hints.build123d_hint("0.12.0")
    assert "installed build123d is 0.12.0" in ok and "checked against it" in ok
    unverified = hints.build123d_hint("0.11.1")
    assert "installed build123d is 0.11.1" in unverified
    assert "NOT checked against" in unverified and "checked against it" not in unverified
    assert "9.9.9" in hints.build123d_hint("9.9.9") and "NOT checked" in hints.build123d_hint("9.9.9")


def test_hint_says_so_when_the_version_cannot_be_detected(monkeypatch):
    def missing(_name):
        raise md.PackageNotFoundError

    monkeypatch.setattr(md, "version", missing)
    assert hints.detected_build123d_version() is None
    text = hints.build123d_hint(None, detected=False)
    assert "could not be detected" in text and "0.12.0, 0.13.0" in text
    assert "installed build123d is" not in text and "checked against it" not in text


def render_signature(cls) -> str:
    """Render an installed class's __init__ the way the hint table writes it: parameter
    order, the keyword-only marker, and every default (repr-free, e.g. Mode.ADD, 90.0)."""
    parts, star = [], False
    for name, p in inspect.signature(cls.__init__).parameters.items():
        if name == "self":
            continue
        if p.kind is p.VAR_POSITIONAL:
            parts.append("*" + name)
            star = True
            continue
        if p.kind is p.KEYWORD_ONLY and not star:
            parts.append("*")
            star = True
        parts.append(name if p.default is p.empty else f"{name}={p.default}")
    return f"{cls.__name__}({', '.join(parts)})"


def test_render_signature_notices_default_and_kind_drift():
    class Good:
        def __init__(self, a, b=1.0, *, c=90.0):  # noqa: D401
            pass

    class DefaultChanged:
        def __init__(self, a, b=1.0, *, c=180.0):
            pass

    class KindChanged:
        def __init__(self, a, b=1.0, c=90.0):
            pass

    good = render_signature(Good)
    assert good == "Good(a, b=1.0, *, c=90.0)"
    assert render_signature(DefaultChanged).replace("DefaultChanged", "Good") != good
    assert render_signature(KindChanged).replace("KindChanged", "Good") != good


def test_hint_matches_installed_build123d():
    b3d = pytest.importorskip("build123d")
    assert b3d.__version__ in hints.BUILD123D_VERIFIED_VERSIONS, (
        "installed build123d is not a version the signature table was verified against; "
        "re-check BUILD123D_CURVE_SIGNATURES and extend BUILD123D_VERIFIED_VERSIONS"
    )
    for name, sig in hints.BUILD123D_CURVE_SIGNATURES.items():
        assert render_signature(getattr(b3d, name)) == sig


def test_drift_in_a_real_default_would_be_caught(monkeypatch):
    """Control from the #888 review: changing EllipticalCenterArc's arc_size default must
    make the table comparison fail (names alone would not notice)."""
    b3d = pytest.importorskip("build123d")
    cls = b3d.EllipticalCenterArc
    assert cls.__init__.__kwdefaults__["arc_size"] == 90.0
    monkeypatch.setitem(cls.__init__.__kwdefaults__, "arc_size", 180.0)
    assert render_signature(cls) != hints.BUILD123D_CURVE_SIGNATURES["EllipticalCenterArc"]
