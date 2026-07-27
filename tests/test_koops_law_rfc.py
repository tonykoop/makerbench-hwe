from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RFC = ROOT / "docs" / "rfc" / "KOOPS_LAW.md"
RUNTIME_ROOTS = [
    ROOT / "makerbench",
    ROOT / "tasks",
    ROOT / "agents",
    ROOT / "scripts",
]


def _text() -> str:
    return RFC.read_text(encoding="utf-8")


def _sections(markdown: str) -> dict[str, str]:
    headings = list(re.finditer(r"^## (?P<title>.+)$", markdown, re.MULTILINE))
    sections: dict[str, str] = {}
    for idx, match in enumerate(headings):
        start = match.end()
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(markdown)
        sections[match.group("title")] = markdown[start:end]
    return sections


def test_koops_law_rfc_is_structurally_deferred() -> None:
    markdown = _text()
    sections = _sections(markdown)
    status_block = markdown.split("This RFC is deliberately speculative.", 1)[0]

    assert "# Koop's Law" in status_block
    assert "DEFERRED" in status_block
    assert "not a\n> dependency" in status_block
    assert "Deferred until S_e measurement is concrete" in status_block
    assert "issue #273" in status_block
    assert sections.keys() >= {
        "Thesis",
        "Defining S_e (Evaluated Spatial Complexity)",
        "How the leaderboard would measure alpha",
        "Kardashev framing",
        "Open questions / why deferred",
    }


def test_koops_law_formula_symbols_are_defined_before_any_use() -> None:
    markdown = _text()
    thesis = _sections(markdown)["Thesis"]
    formula_blocks = re.findall(r"```text\n(?P<formula>.*?)\n```", thesis, re.DOTALL)

    assert formula_blocks == ["C = k \u00b7 (S_e)^alpha"]

    defined_symbols = {
        match.group("symbol")
        for match in re.finditer(r"^- `(?P<symbol>[^`]+)`\s+[-\u2014]+", thesis, re.MULTILINE)
    }
    assert {"C", "S_e", "k", "alpha"} <= defined_symbols


def test_koops_law_dependencies_are_document_links_not_runtime_imports() -> None:
    markdown = _text()
    alpha_section = _sections(markdown)["How the leaderboard would measure alpha"]
    links = {
        target
        for target in re.findall(r"\[[^\]]+\]\((?P<target>[^)]+)\)", alpha_section)
        if not target.startswith("http")
    }

    assert {"CAPABILITY_INDEX.md", "../PHYSICAL_VERIFICATION_TRACK.md"} <= links
    for target in links:
        assert (RFC.parent / target).resolve().exists()

    offenders: list[str] = []
    needles = ("KOOPS_LAW", "Koop's Law", "Koops Law")
    for root in RUNTIME_ROOTS:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if any(needle in text for needle in needles):
                offenders.append(path.relative_to(ROOT).as_posix())

    assert offenders == []
