"""Community ops contract tests for mb#113."""

from __future__ import annotations

from pathlib import Path


DOC = Path("docs/COMMUNITY_OPS.md")


def test_issue_113_community_ops_doc_covers_required_surface():
    text = DOC.read_text(encoding="utf-8")

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
    text = DOC.read_text(encoding="utf-8")

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
    text = DOC.read_text(encoding="utf-8")

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
