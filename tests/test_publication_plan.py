from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "PUBLICATION_PLAN.md"
REPORT = ROOT / "docs" / "ARXIV_TECH_REPORT.md"
CITATION = ROOT / "CITATION.cff"


def _markdown_table(markdown: str, heading: str) -> list[dict[str, str]]:
    marker = f"## {heading}"
    start = markdown.index(marker)
    section = markdown[start:].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("| ")]
    assert len(rows) >= 3
    headers = [cell.strip() for cell in rows[0].strip("|").split("|")]
    parsed = []
    for row in rows[2:]:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        parsed.append({
            key: value.replace("\u2192", "->").replace("\u2194", "<->")
            for key, value in zip(headers, cells)
        })
    return parsed


def test_publication_plan_records_unlaunched_distribution_state() -> None:
    plan = PLAN.read_text(encoding="utf-8")
    report = REPORT.read_text(encoding="utf-8")

    state = plan.split("---", 1)[0]
    state_words = " ".join(state.split())
    assert "Not yet existing: any Hugging Face presence, any arXiv ID" in state
    assert "do not link to the Space before it is uploaded" in state_words
    assert "do not cite an arXiv ID before" in state_words
    assert "#52 is still open" in state

    header = report.split("---", 1)[0]
    assert "pre-submission draft" in header
    assert "no arXiv\nID exists yet" in header
    assert "fill in after the Space exists" in header


def test_publication_cross_links_are_gated_on_real_external_artifacts() -> None:
    plan = PLAN.read_text(encoding="utf-8")
    rows = _markdown_table(plan, "4. Cross-linking (apply each edge only once both ends exist)")
    when_by_edge = {row["Edge"]: row["When"] for row in rows}

    assert when_by_edge["Repo -> Pages"] == "already live"
    assert when_by_edge["Repo -> HF Space"] == "after Space upload"
    assert when_by_edge["HF Space -> repo + Pages"] == "at Space upload"
    assert when_by_edge["Repo -> arXiv"] == "after arXiv ID"
    assert when_by_edge["HF Space -> arXiv"] == "after arXiv ID"
    assert when_by_edge["Repo <-> CADGenBench bridge"] == "after #52"


def test_citation_cff_stays_software_only_until_arxiv_id_exists() -> None:
    citation = yaml.safe_load(CITATION.read_text(encoding="utf-8"))

    assert citation["type"] == "software"
    assert citation["repository-code"] == "https://github.com/tonykoop/makerbench-hwe"
    assert "preferred-citation" not in citation
    identifiers = citation.get("identifiers") or []
    assert not any("arxiv" in str(item).lower() for item in identifiers)


def test_publication_plan_only_contains_placeholder_arxiv_identifier() -> None:
    plan = PLAN.read_text(encoding="utf-8")

    arxiv_ids = re.findall(r"arXiv:(\d{4}\.\d{5}|XXXX\.XXXXX)", plan)
    assert arxiv_ids == ["XXXX.XXXXX"]
    assert "https://arxiv.org/abs/XXXX.XXXXX" in plan
