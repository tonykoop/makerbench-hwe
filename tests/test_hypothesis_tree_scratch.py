"""Isolated scratch runs + persistence for the hypothesis-tree runner (#162, #243).

The Coordinator/contract/loop are covered by tests/test_hypothesis_tree.py. These
cover the additions that realize the acceptance sketch's "spawn isolated scratch
runs" + "persist evidence" + "produce a tree report and dashboard summary": the
doc the module references, `scratch_run_executor`, and `persist_report`.
"""

from __future__ import annotations

import json
from pathlib import Path

from makerbench.hypothesis_tree import (
    Coordinator,
    HypothesisEvidence,
    ResearchContract,
    SuccessMetric,
    dashboard_summary,
    persist_report,
    render_report_markdown,
    run_tree,
    scratch_run_executor,
)

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "ARBOR_HYPOTHESIS_TREE.md"


def _contract() -> ResearchContract:
    return ResearchContract(
        objective="minimize plate mass while staying printable",
        task_id="scratch_demo_plate",
        success_metrics=[
            SuccessMetric(name="mass_g", target=40.0, direction="minimize", unit="g"),
            SuccessMetric(name="min_wall_mm", target=0.8, direction="maximize", unit="mm"),
        ],
        token_budget=100_000,
        runtime_budget_seconds=1000.0,
    )


def _tree(contract: ResearchContract) -> Coordinator:
    coord = Coordinator(contract)
    d = coord.add_direction("uniform wall")
    for t in (0.6, 0.9):
        coord.add_hypothesis(d.id, f"wall={t}mm", statement=f"{t} mm wall meets targets")
    return coord


def _executor(contract: ResearchContract, node) -> HypothesisEvidence:
    thickness = float(node.title.split("=")[1].rstrip("mm"))
    # Write a fake artifact into the (scratch) cwd to prove isolation.
    Path("render.txt").write_text(f"wall={thickness}", encoding="utf-8")
    return HypothesisEvidence(
        commands=[f"openscad -D wall={thickness}"],
        geometry_params={"wall_mm": thickness},
        measurements={"mass_g": 30.0 + thickness, "min_wall_mm": thickness},
        artifacts=["render.txt"],
        tokens_spent=4000,
        runtime_seconds=10.0,
    )


def test_doc_exists_and_documents_the_runner():
    assert DOC.is_file(), "docs/ARBOR_HYPOTHESIS_TREE.md (referenced by the module) is missing"
    text = DOC.read_text(encoding="utf-8")
    for needle in (
        "ResearchContract",
        "Coordinator",
        "scratch_run_executor",
        "persist_report",
        "dashboard_summary",
        "held-out",
    ):
        assert needle in text, f"doc missing: {needle!r}"


def test_scratch_run_executor_isolates_each_hypothesis(tmp_path):
    contract = _contract()
    coord = _tree(contract)
    cwd_before = Path.cwd()

    executor = scratch_run_executor(_executor, root_dir=tmp_path)
    report = run_tree(coord, executor)

    # cwd is restored after the run.
    assert Path.cwd() == cwd_before

    # Each tested hypothesis got its own scratch dir with persisted evidence + its
    # own artifact, isolated from siblings.
    tested = [n for n in report.root.walk() if n.kind == "hypothesis" and n.evidence]
    assert tested
    for node in tested:
        scratch = tmp_path / node.id
        assert (scratch / "evidence.json").is_file()
        assert (scratch / "render.txt").is_file()
        evidence = json.loads((scratch / "evidence.json").read_text())
        assert evidence["geometry_params"]["wall_mm"] == node.evidence.geometry_params["wall_mm"]
        # The scratch path is recorded back into the evidence artifacts.
        assert any(str(scratch) in a for a in node.evidence.artifacts)


def test_scratch_run_executor_defaults_to_a_tempdir():
    contract = _contract()
    coord = _tree(contract)
    executor = scratch_run_executor(_executor)  # no root_dir -> tempdir
    report = run_tree(coord, executor)
    node = next(n for n in report.root.walk() if n.kind == "hypothesis" and n.evidence)
    scratch_artifacts = [a for a in node.evidence.artifacts if a.endswith("evidence.json")]
    assert scratch_artifacts and Path(scratch_artifacts[0]).is_file()


def test_persist_report_writes_full_audit_trail(tmp_path):
    contract = _contract()
    coord = _tree(contract)
    report = run_tree(coord, scratch_run_executor(_executor, root_dir=tmp_path / "scratch"))

    out = tmp_path / "out"
    paths = persist_report(report, out)

    report_json = json.loads(Path(paths["report_json"]).read_text())
    assert report_json["contract"]["task_id"] == "scratch_demo_plate"
    assert "Research Report" in Path(paths["report_md"]).read_text()
    dashboard = json.loads(Path(paths["dashboard_json"]).read_text())
    assert dashboard == dashboard_summary(report)
    assert "Research Report" in render_report_markdown(report)
