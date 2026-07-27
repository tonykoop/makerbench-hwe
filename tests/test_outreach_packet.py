"""Regression checks for the adjacent-benchmark outreach packet."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "docs" / "OUTREACH_PACKET.md"


def _packet_text() -> str:
    return PACKET.read_text(encoding="utf-8")


def _normalized_text() -> str:
    return " ".join(_packet_text().split())


def test_outreach_packet_covers_issue_74_targets_and_marb_addendum():
    text = _packet_text()
    for target in (
        "Hephaestus-CCX",
        "BenDFM",
        "CadQuery",
        "build123d",
        "CADFS",
        "FeatureScript",
        "GenCAD-3D",
        "CADGenBench",
        "Hugging Face",
        "MARB",
        "CADCLAW",
    ):
        assert target in text


def test_outreach_packet_names_required_collaboration_asks():
    text = _normalized_text()
    for ask in (
        "task-format alignment",
        "deterministic DFM component",
        "taxonomy vocabulary",
        "FEA prefilter",
        "solver handoff boundary",
        "reverse-engineering task",
        "visual evidence",
    ):
        assert ask in text


def test_outreach_packet_keeps_public_claim_guardrails_visible():
    text = _normalized_text()
    assert "not \"the only manufacturability benchmark.\"" in text
    assert "Do not quote stale leaderboard counts" in text
    assert "Do not imply CADGenBench is limited to build123d" in text
    assert "their benchmark, their tasks, their metric" in text
    assert "no head-to-head claims without" in text


def test_outreach_packet_avoids_known_overclaims():
    text = _packet_text()
    forbidden = (
        "the only benchmark with manufacturability evaluation",
        "HF x Mecado",
        "beats Hephaestus",
        "beats CADGenBench",
    )
    for phrase in forbidden:
        assert phrase not in text
