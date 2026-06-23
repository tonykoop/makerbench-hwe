"""Acceptance tests for the maker-ready deliverable packet contract (#103)."""

from pathlib import Path

from makerbench.dossier_scoring import (
    assess_packet_completeness,
    score_design_dossier,
    supported_dossier_categories,
)
from makerbench.schema import (
    BomItem,
    DeliverablePacket,
    DesignDossier,
    GcodeMachineProfile,
    PacketFile,
    ProcessPlan,
    TaskSpec,
)


ROOT = Path(__file__).resolve().parents[1]


def _spec() -> TaskSpec:
    return TaskSpec(
        task_id="enclosure_fastened",
        seed=0,
        params={"n_screws": 4},
        brief="demo",
    )


def _packet() -> DeliverablePacket:
    drawing = PacketFile(
        path="packet/enclosure_gdt.pdf",
        role="drawing_pdf",
        format="pdf",
        sha256="a" * 64,
    )
    mesh = PacketFile(
        path="packet/enclosure.stl",
        role="mesh_stl",
        format="stl",
        sha256="b" * 64,
        bbox_mm=[0.0, 0.0, 0.0, 80.0, 60.0, 30.0],
    )
    gcode = PacketFile(
        path="packet/enclosure.nc",
        role="cnc_gcode",
        format="nc",
        sha256="c" * 64,
    )
    bom = PacketFile(
        path="packet/bom.csv",
        role="bom_csv",
        format="csv",
        sha256="d" * 64,
    )
    sourcing = PacketFile(
        path="packet/sourcing.csv",
        role="sourcing_csv",
        format="csv",
        sha256="e" * 64,
    )
    return DeliverablePacket(
        drawing_pdf=drawing,
        mesh_stl=mesh,
        cnc_gcode=gcode,
        gcode_profile=GcodeMachineProfile(
            machine="Shapeoko 4 XXL",
            controller="GRBL 1.1",
            post_processor="grbl.cps",
            tools=["T1 6mm flat endmill"],
            work_bounds_mm=[-2.0, -2.0, -1.0, 82.0, 62.0, 31.0],
        ),
        bom_csv=bom,
        sourcing_csv=sourcing,
        manifest=[drawing, mesh, gcode, bom, sourcing],
    )


def _dossier(packet: DeliverablePacket | None = None) -> DesignDossier:
    return DesignDossier(
        task_id="enclosure_fastened",
        seed=0,
        fabrication_domain="cnc_milling_geometry",
        bom=[
            BomItem(item_id="base", category="enclosure_base", quantity=1, source="fabricated"),
            BomItem(item_id="lid", category="enclosure_lid", quantity=1, source="fabricated"),
            BomItem(
                item_id="lid_screw",
                category="socket_head_cap_screw",
                quantity=4,
                source="catalog",
                part_number="MB-SHCS-M3-08",
            ),
            BomItem(
                item_id="heat_set_insert",
                category="heat_set_insert",
                quantity=4,
                source="catalog",
                part_number="MB-HSI-M3",
            ),
        ],
        process_plan=ProcessPlan(
            primary_process="cnc_milling",
            material="6061 aluminum",
            assembly_sequence=["Mill base", "Mill lid", "Install inserts", "Fasten lid"],
            validation_gates=["Inspect GD&T drawing", "Dry-run G-code bounds"],
        ),
        packet=packet,
    )


def test_packet_schema_exposes_all_maker_deliverable_roles():
    packet = _packet()
    named_files = [
        packet.drawing_pdf,
        packet.mesh_stl,
        packet.cnc_gcode,
        packet.bom_csv,
        packet.sourcing_csv,
    ]

    assert {file.role for file in named_files} == {
        "drawing_pdf",
        "mesh_stl",
        "cnc_gcode",
        "bom_csv",
        "sourcing_csv",
    }
    assert packet.gcode_profile is not None
    assert packet.gcode_profile.machine == "Shapeoko 4 XXL"
    assert packet.gcode_profile.tools == ["T1 6mm flat endmill"]


def test_manifest_lists_every_named_packet_file_with_checksum():
    packet = _packet()
    named_paths = {
        file.path
        for file in (
            packet.drawing_pdf,
            packet.mesh_stl,
            packet.cnc_gcode,
            packet.bom_csv,
            packet.sourcing_csv,
        )
    }
    manifest_by_path = {entry.path: entry for entry in packet.manifest}

    assert named_paths == set(manifest_by_path)
    assert all(manifest_by_path[path].sha256 for path in named_paths)


def test_complete_packet_passes_disclosure_grade_completeness_hooks():
    result = assess_packet_completeness(_dossier(_packet()), _spec())

    assert result.category == "deliverable_packet"
    assert result.passed is True
    assert result.checks == {
        "packet_present": True,
        "manifest_lists_all_files": True,
        "bom_enumerates_assembly_parts": True,
        "gcode_bounds_enclose_part": True,
    }


def test_incomplete_packet_flags_manifest_and_gcode_bounds_failures():
    packet = _packet()
    packet.manifest = [entry for entry in packet.manifest if entry.role != "sourcing_csv"]
    packet.gcode_profile.work_bounds_mm = [0.0, 0.0, 0.0, 40.0, 30.0, 15.0]

    result = assess_packet_completeness(_dossier(packet), _spec())

    assert result.passed is False
    assert result.checks["manifest_lists_all_files"] is False
    assert result.checks["gcode_bounds_enclose_part"] is False
    assert "dossier.packet.manifest" in result.missing_fields
    assert "dossier.packet.gcode_profile.work_bounds_mm" in result.missing_fields


def test_bom_must_enumerate_parts_created_by_assembly_steps():
    dossier = _dossier(_packet())
    dossier.bom = [item for item in dossier.bom if item.source != "fabricated"]

    result = assess_packet_completeness(dossier, _spec())

    assert result.passed is False
    assert result.checks["bom_enumerates_assembly_parts"] is False
    assert "dossier.bom[source=fabricated|stock_material]" in result.missing_fields


def test_deliverable_packet_remains_optional_and_non_gating():
    absent = assess_packet_completeness(_dossier(None), _spec())

    assert absent.checks == {"packet_present": False}
    assert absent.missing_fields == ["dossier.packet"]
    assert "deliverable_packet" not in supported_dossier_categories()
    scored = score_design_dossier(_dossier(_packet()), _spec())
    assert scored is not None
    assert "deliverable_packet" not in scored.required_categories


def test_public_docs_pin_disclosure_grade_boundary_and_required_roles():
    docs = (ROOT / "docs" / "DELIVERABLE_PACKET.md").read_text(encoding="utf-8")

    assert "Disclosure-grade, never a hard gate" in docs
    assert "packet_manifest.json" in docs
    for role in ("drawing_pdf", "mesh_stl", "cnc_gcode", "bom_csv", "sourcing_csv"):
        assert f"`{role}`" in docs
