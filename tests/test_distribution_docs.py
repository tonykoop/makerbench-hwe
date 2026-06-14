"""Distribution packet contract tests."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_distribution_packet_covers_issue_99_acceptance():
    text = (ROOT / "docs" / "DISTRIBUTION.md").read_text(encoding="utf-8")

    required = [
        "Built with MakerBench",
        "NN/100 blind",
        "MCP Registry Submission",
        "awesome-mcp",
        "CadQuery",
        "build123d",
        "CQ-Editor",
        "OpenSCAD",
        "[Workflow Stack]",
        "[Prompt/Trace]",
        "[Blender MCP]",
        "[Moonshot Entry]",
        "[Maker Log]",
        "Prompt-to-STEP",
        "CONTAMINATION_RESPONSE.md",
    ]
    missing = [needle for needle in required if needle not in text]

    assert missing == []


def test_committed_badge_endpoints_use_distribution_label():
    badge_files = sorted((ROOT / "site" / "data" / "badges").glob("*.json"))
    stale = []
    for path in badge_files:
        if path.name == "index.json":
            continue
        text = path.read_text(encoding="utf-8")
        if '"label": "Built with MakerBench"' not in text:
            stale.append(path.relative_to(ROOT).as_posix())

    assert stale == []
