"""Physical Verification Track contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from makerbench.schema import (
    FABRICATION_STAGE_BONUS,
    PHYSICAL_VERIFICATION_STAGE_ORDER,
    AlphaBuild,
    BetaInspection,
    FabricationEvidence,
    PhysicalVerificationTrack,
    ProductionMaster,
    WorkflowManifest,
)


def _alpha(disclosed: bool = True) -> AlphaBuild:
    evidence = FabricationEvidence(
        stage="alpha",
        role="assembly_photo",
        format="png",
        evidence_sha256=("a" * 64) if disclosed else None,
    )
    return AlphaBuild(
        process="fdm",
        tool_matrix=["Prusa MK4", "desktop laser"],
        makerspace_optimized=True,
        evidence=[evidence],
    )


def _beta() -> BetaInspection:
    evidence = FabricationEvidence(
        stage="beta",
        role="cmm_report",
        format="pdf",
        evidence_url="https://example.com/cmm.pdf",
    )
    return BetaInspection(
        vendor="xometry",
        process="cnc_milling",
        dimensional_conformance={"bore_dia": 0.012},
        evidence=[evidence],
    )


def _production() -> ProductionMaster:
    evidence = FabricationEvidence(
        stage="production",
        role="gdt_drawing",
        format="pdf",
        evidence_sha256="d" * 64,
    )
    return ProductionMaster(
        bom_finalized=True,
        eco_id="ECO-001",
        gdt_finalized=True,
        hard_tooling=True,
        evidence=[evidence],
    )


def test_empty_track_earns_no_credit():
    pvt = PhysicalVerificationTrack(task_id="vented_plate", seed=0)

    assert pvt.attained_stages() == []
    assert pvt.highest_verified_stage() is None
    assert pvt.fabrication_multiplier() == 1.0
    assert pvt.credited_score(3) == 3


@pytest.mark.parametrize(
    ("stage", "track", "expected_multiplier"),
    [
        ("alpha", PhysicalVerificationTrack(task_id="t", seed=0, alpha=_alpha()), 1.05),
        ("beta", PhysicalVerificationTrack(task_id="t", seed=0, beta=_beta()), 1.15),
        (
            "production",
            PhysicalVerificationTrack(task_id="t", seed=0, production=_production()),
            1.30,
        ),
    ],
)
def test_single_stage_multiplier(
    stage: str,
    track: PhysicalVerificationTrack,
    expected_multiplier: float,
):
    assert track.highest_verified_stage() == stage
    assert track.fabrication_multiplier() == expected_multiplier


def test_highest_stage_wins_without_stacking():
    track = PhysicalVerificationTrack(
        task_id="vented_plate",
        seed=0,
        alpha=_alpha(),
        beta=_beta(),
        production=_production(),
    )

    assert track.attained_stages() == ["alpha", "beta", "production"]
    assert track.highest_verified_stage() == "production"
    assert track.fabrication_multiplier() == 1.30
    assert track.credited_score(4) == 5.2


def test_stages_are_independent():
    track = PhysicalVerificationTrack(task_id="vented_plate", seed=0, beta=_beta())

    assert track.attained_stages() == ["beta"]
    assert track.highest_verified_stage() == "beta"
    assert track.fabrication_multiplier() == 1.15


def test_stage_without_disclosed_evidence_earns_nothing():
    track = PhysicalVerificationTrack(
        task_id="vented_plate",
        seed=0,
        alpha=_alpha(disclosed=False),
    )

    assert track.alpha is not None
    assert track.alpha.evidence
    assert not track.alpha.evidence[0].is_disclosed
    assert track.attained_stages() == []
    assert track.fabrication_multiplier() == 1.0


def test_stage_with_empty_evidence_earns_nothing():
    track = PhysicalVerificationTrack(
        task_id="vented_plate",
        seed=0,
        beta=BetaInspection(vendor="fictiv"),
    )

    assert track.attained_stages() == []
    assert track.fabrication_multiplier() == 1.0


def test_evidence_disclosure_predicate_accepts_hash_or_url():
    hashed = FabricationEvidence(
        stage="alpha",
        role="assembly_photo",
        format="png",
        evidence_sha256="a",
    )
    linked = FabricationEvidence(
        stage="beta",
        role="inspection_report",
        format="pdf",
        evidence_url="https://example.com/report.pdf",
    )
    undisclosed = FabricationEvidence(stage="production", role="bom", format="csv")

    assert hashed.is_disclosed
    assert linked.is_disclosed
    assert not undisclosed.is_disclosed


def test_bonus_table_matches_stage_order():
    assert set(FABRICATION_STAGE_BONUS) == set(PHYSICAL_VERIFICATION_STAGE_ORDER)
    bonuses = [FABRICATION_STAGE_BONUS[stage] for stage in PHYSICAL_VERIFICATION_STAGE_ORDER]
    assert bonuses == sorted(bonuses)


def test_attaches_to_workflow_manifest_and_round_trips():
    pvt = PhysicalVerificationTrack(
        task_id="vented_plate",
        seed=0,
        alpha=_alpha(),
        beta=_beta(),
    )
    manifest = WorkflowManifest(
        task_id="vented_plate",
        seed=0,
        physical_verification=pvt,
    )

    restored = WorkflowManifest.model_validate_json(manifest.model_dump_json())

    assert restored.physical_verification is not None
    assert restored.physical_verification.fabrication_multiplier() == 1.15
    assert restored.physical_verification.beta is not None
    assert restored.physical_verification.beta.dimensional_conformance["bore_dia"] == 0.012


def test_legacy_workflow_manifest_defaults_to_no_physical_verification():
    manifest = WorkflowManifest(task_id="vented_plate", seed=0)

    assert manifest.physical_verification is None


def test_invalid_physical_verification_stage_is_rejected():
    with pytest.raises(ValidationError):
        FabricationEvidence.model_validate(
            {"stage": "gamma", "role": "assembly_photo", "format": "png"}
        )
