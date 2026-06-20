"""Community ops contract tests for mb#113."""

from __future__ import annotations

from pathlib import Path


DOC = Path("docs/COMMUNITY_OPS.md")


def _text() -> str:
    return DOC.read_text(encoding="utf-8")


def _section(title: str) -> str:
    marker = f"## {title}"
    text = _text()
    start = text.index(marker)
    following = text.find("\n## ", start + len(marker))
    return text[start:] if following == -1 else text[start:following]


def _markdown_table(section_title: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    table_lines = [
        line for line in _section(section_title).splitlines()
        if line.startswith("|") and line.endswith("|")
    ]
    headers = [cell.strip() for cell in table_lines[0].strip("|").split("|")]
    for line in table_lines[2:]:
        cells = [cell.strip().strip("`") for cell in line.strip("|").split("|")]
        rows.append(dict(zip(headers, cells)))
    return rows


def _fenced_block(section_title: str, language: str) -> str:
    section = _section(section_title)
    fence = f"```{language}"
    start = section.index(fence) + len(fence)
    end = section.index("```", start)
    return section[start:end].strip()


def test_issue_113_community_ops_doc_covers_required_surface():
    text = _text()

    for flair in (
        "[Workflow Stack]",
        "[Prompt/Trace]",
        "[Blender MCP]",
        "[Moonshot Entry]",
        "[Maker Log]",
    ):
        assert flair in text

    for phrase in (
        "Show the Geometry",
        "image or video",
        "physical or rendered result",
        "generic prompt slop",
        "source artifacts",
        "private oracle",
        "verification_status",
    ):
        assert phrase in text


def test_issue_113_prompt_to_step_template_is_actionable():
    text = _text()

    for field in (
        "Goal:",
        "Stack:",
        "Prompt Primitive:",
        "The Hack That Saved It:",
        "Geometry Evidence:",
    ):
        assert field in text

    assert "The first four fields are the exchange core" in text
    assert "Geometry Evidence" in text


def test_issue_113_moonshot_and_leaderboard_loop_is_linked():
    text = _text()

    for phrase in (
        "Moonshot drop",
        "mb#96",
        "Build thread",
        "Leaderboard movement",
        "mb#98",
        "Closeout",
    ):
        assert phrase in text


def test_issue_113_related_docs_route_to_community_ops():
    challenge = Path("docs/CHALLENGE_SPEC.md").read_text(encoding="utf-8")
    workflow = Path("docs/WORKFLOW_TRACK.md").read_text(encoding="utf-8")

    assert "COMMUNITY_OPS.md" in challenge
    assert "[Moonshot Entry]" in challenge
    assert "Show the Geometry" in challenge
    assert "community build thread" in challenge

    assert "COMMUNITY_OPS.md" in workflow
    assert "community ops layer (#113)" in workflow


def test_issue_113_flair_table_is_structured():
    rows = _markdown_table("Flairs")
    by_flair = {row["Flair"]: row for row in rows}

    assert set(by_flair) == {
        "[Workflow Stack]",
        "[Prompt/Trace]",
        "[Blender MCP]",
        "[Moonshot Entry]",
        "[Maker Log]",
    }
    assert by_flair["[Workflow Stack]"]["Required anchor"] == (
        "WorkflowManifest or planned manifest fields."
    )
    assert "trace hash" in by_flair["[Prompt/Trace]"]["Required anchor"]
    assert "Challenge seed id" in by_flair["[Moonshot Entry]"]["Required anchor"]
    assert "Geometry evidence" in by_flair["[Maker Log]"]["Required anchor"]


def test_issue_113_exchange_template_fields_are_parseable_in_order():
    template = _fenced_block("Prompt-to-STEP Exchange Template", "md")
    fields = [
        line.split(":", 1)[0]
        for line in template.splitlines()
        if ":" in line and not line.startswith(" ")
    ]

    assert fields == [
        "Flair",
        "Goal",
        "Stack",
        "Prompt Primitive",
        "The Hack That Saved It",
        "Geometry Evidence",
    ]
