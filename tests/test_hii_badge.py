"""Gamified HII badge contract tests (mb#106).

Covers the anti-fake derivation (badge trust comes from the signed ``.mbc``
certificate + verified leaderboard state), the L0/L1/L2 class mapping, the
self-contained SVG render, and the ``/api/badge?user=`` endpoint core.
"""

from __future__ import annotations

import json
import xml.dom.minidom as minidom
from pathlib import Path

from makerbench.certificate import MbcCheckResult, MbcPayload, build_certificate, write_mbc
from makerbench.hii_badge import (
    BADGE_CLASSES,
    UNVERIFIED_COLOR,
    badge_class_for,
    badge_from_certificate,
    badge_gallery_payload,
    badge_shields_payload,
    make_badge,
    render_badge_svg,
    serve_badge,
)

KEY = "shared-nonce-key-v0.1"
ROOT = Path(__file__).resolve().parents[1]


def _payload(**overrides) -> MbcPayload:
    base = dict(
        task_id="vented_plate",
        seed=0,
        score=3,
        passed=True,
        checks=[MbcCheckResult(name="structural", passed=True)],
        artifact_fingerprint="a" * 64,
        autonomy_ratio=1.0,
        hii_highest_level="L0",
        timestamp="2026-06-14T00:00:00Z",
        nonce="nonce-123",
    )
    base.update(overrides)
    return MbcPayload(**base)


def _cert_text(**overrides) -> str:
    return build_certificate(_payload(**overrides), KEY).model_dump_json()


# --- class mapping ---------------------------------------------------------


def test_badge_classes_cover_all_three_tiers():
    assert set(BADGE_CLASSES) == {"L0", "L1", "L2"}
    assert badge_class_for("L0").title == "Pure Autonomy"
    assert badge_class_for("L1").title == "Elite Copilot"
    assert badge_class_for("L2").title == "Master Triage"
    # Distinct colors per tier (emerald / blue / purple).
    colors = {badge_class_for(lvl).color for lvl in ("L0", "L1", "L2")}
    assert len(colors) == 3


def test_badge_class_for_unknown_level_raises():
    import pytest

    with pytest.raises(ValueError):
        badge_class_for("L9")  # type: ignore[arg-type]


# --- anti-fake: certificate-derived badge ----------------------------------


def test_verified_cert_earns_flex_class():
    badge = badge_from_certificate(
        _cert_text(hii_highest_level="L1", score=4, autonomy_ratio=0.7),
        KEY,
        user="ada",
        verification_status="public-regrade-verified",
    )
    assert badge.earned
    assert badge.highest_level == "L1"
    assert badge.score == 4
    assert badge.color == BADGE_CLASSES["L1"].color
    assert "L1 Elite Copilot" in badge.message
    assert "4/4" in badge.message


def test_tampered_certificate_degrades_to_unverified():
    # A valid cert, then a hand-edited score: signature no longer matches.
    cert = build_certificate(_payload(score=1), KEY).model_dump()
    cert["payload"]["score"] = 4  # forge a better score
    badge = badge_from_certificate(
        cert, KEY, user="mallory", verification_status="public-regrade-verified"
    )
    assert not badge.signature_verified
    assert not badge.earned
    assert badge.color == UNVERIFIED_COLOR
    assert badge.message == "unverified"


def test_wrong_key_is_not_verified():
    badge = badge_from_certificate(
        _cert_text(), "attacker-key", user="ada",
        verification_status="public-regrade-verified",
    )
    assert not badge.signature_verified
    assert not badge.earned


def test_unverified_leaderboard_status_blocks_flex_class():
    badge = badge_from_certificate(
        _cert_text(), KEY, user="ada", verification_status="unverified"
    )
    assert badge.signature_verified
    assert not badge.leaderboard_verified
    assert not badge.earned
    assert badge.color == UNVERIFIED_COLOR


def test_failing_run_does_not_earn_badge():
    badge = badge_from_certificate(
        _cert_text(passed=False), KEY, user="ada",
        verification_status="official-heldout-verified",
    )
    assert badge.signature_verified
    assert not badge.passed
    assert not badge.earned


def test_official_heldout_status_counts_as_verified():
    badge = badge_from_certificate(
        _cert_text(hii_highest_level="L2"), KEY, user="ada",
        verification_status="official-heldout-verified",
    )
    assert badge.earned
    assert badge.highest_level == "L2"
    assert "Master Triage" in badge.message


def test_malformed_certificate_returns_unverified_badge():
    badge = badge_from_certificate("{not json", KEY, user="ada")
    assert not badge.earned
    assert badge.highest_level == "L0"


def test_certificate_from_file(tmp_path):
    path = tmp_path / "run.mbc"
    write_mbc(_payload(hii_highest_level="L1"), KEY, path=path)
    badge = badge_from_certificate(
        str(path), KEY, user="ada", verification_status="public-regrade-verified"
    )
    assert badge.earned
    assert badge.highest_level == "L1"


# --- shields payload -------------------------------------------------------


def test_shields_payload_is_endpoint_schema():
    badge = make_badge(
        user="ada", highest_level="L0", score=3, passed=True,
        signature_verified=True, leaderboard_verified=True,
    )
    payload = badge_shields_payload(badge)
    assert payload["schemaVersion"] == 1
    assert payload["label"] == "MakerBench HII"
    assert payload["color"] == BADGE_CLASSES["L0"].color
    assert "Pure Autonomy" in payload["message"]


def test_shields_payload_unverified_is_grey():
    badge = make_badge(user="ada", highest_level="L0", passed=False)
    payload = badge_shields_payload(badge)
    assert payload["color"] == UNVERIFIED_COLOR
    assert payload["message"] == "unverified"


def test_badge_gallery_payload_covers_all_classes_and_unverified_state():
    gallery = badge_gallery_payload(user="ada", score=4)
    entries = gallery["entries"]
    assert gallery["schema_version"] == "0.1"
    assert [entry["level"] for entry in entries] == ["L0", "L1", "L2", "unverified"]

    earned = entries[:3]
    assert all(entry["earned"] for entry in earned)
    assert [entry["title"] for entry in earned] == [
        "Pure Autonomy",
        "Elite Copilot",
        "Master Triage",
    ]
    assert {entry["color"] for entry in earned} == {
        BADGE_CLASSES["L0"].color,
        BADGE_CLASSES["L1"].color,
        BADGE_CLASSES["L2"].color,
    }
    assert all(entry["shields_endpoint"]["schemaVersion"] == 1 for entry in entries)
    assert entries[-1]["color"] == UNVERIFIED_COLOR
    assert entries[-1]["message"] == "unverified"


def test_gallery_example_matches_helper_output():
    example = json.loads(
        (ROOT / "examples" / "hii_badge_gallery.example.json").read_text(encoding="utf-8")
    )
    generated = badge_gallery_payload(user="ada-lovelace", score=4)
    assert example["schema_version"] == generated["schema_version"]
    assert example["source"] == generated["source"]
    assert example["entries"] == generated["entries"]


# --- SVG render ------------------------------------------------------------


def test_render_badge_svg_is_well_formed_and_themed():
    badge = make_badge(
        user="ada", highest_level="L2", score=2, passed=True,
        signature_verified=True, leaderboard_verified=True,
    )
    svg = render_badge_svg(badge)
    # Parses as XML.
    minidom.parseString(svg)
    assert svg.startswith("<svg")
    assert BADGE_CLASSES["L2"].color in svg
    assert "Master Triage" in svg
    assert "MakerBench HII" in svg


def test_render_badge_svg_is_deterministic():
    badge = make_badge(user="ada", highest_level="L0", score=3, passed=True,
                       signature_verified=True, leaderboard_verified=True)
    assert render_badge_svg(badge) == render_badge_svg(badge)


def test_render_unverified_svg_is_grey():
    svg = render_badge_svg(make_badge(user="ada"))
    minidom.parseString(svg)
    assert UNVERIFIED_COLOR in svg
    assert "unverified" in svg


def test_svg_escapes_user_controlled_message():
    # Even if a future message carried markup, it must be escaped, not injected.
    badge = make_badge(user="<script>", highest_level="L0")
    svg = render_badge_svg(badge)
    minidom.parseString(svg)  # would raise if raw "<script>" leaked in


# --- endpoint --------------------------------------------------------------


def _lookup_table(table):
    def lookup(user):
        return table.get(user)
    return lookup


def test_serve_badge_hit_renders_class():
    table = {
        "ada": {
            "certificate": _cert_text(hii_highest_level="L0", score=4),
            "key": KEY,
            "verification_status": "public-regrade-verified",
        }
    }
    status, content_type, body = serve_badge(
        "user=ada", lookup=_lookup_table(table)
    )
    assert status == 200
    assert content_type.startswith("image/svg+xml")
    assert BADGE_CLASSES["L0"].color in body
    assert "Pure Autonomy" in body


def test_serve_badge_unknown_user_is_404_grey():
    status, _, body = serve_badge("user=nobody", lookup=_lookup_table({}))
    assert status == 404
    assert UNVERIFIED_COLOR in body
    assert "unverified" in body


def test_serve_badge_missing_user_param_is_400():
    status, _, body = serve_badge("", lookup=_lookup_table({}))
    assert status == 400
    assert UNVERIFIED_COLOR in body


def test_serve_badge_uses_fallback_key():
    table = {
        "ada": {
            "certificate": _cert_text(),
            "verification_status": "public-regrade-verified",
        }
    }
    status, _, body = serve_badge("user=ada", lookup=_lookup_table(table), key=KEY)
    assert status == 200
    assert "Pure Autonomy" in body


def test_serve_badge_bad_signature_renders_grey_not_error():
    cert = build_certificate(_payload(score=1), KEY).model_dump()
    cert["payload"]["score"] = 4
    table = {
        "ada": {
            "certificate": cert,
            "key": KEY,
            "verification_status": "public-regrade-verified",
        }
    }
    status, _, body = serve_badge("user=ada", lookup=_lookup_table(table))
    assert status == 200
    assert UNVERIFIED_COLOR in body
    assert "unverified" in body
