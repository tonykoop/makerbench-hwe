"""Explore designs tab (placeholder).

Story #1029 replaces this placeholder with a read-only browser of the public
Studio showcase. Until then the tab explains what is coming and where the
showcase already lives.
"""

from __future__ import annotations

TAB_ID = "explore"
TITLE = "Explore designs"

PLACEHOLDER_MD = """\
### Explore designs (coming soon)

This tab will be a read-only gallery of designs produced in the MakerBench
Code-CAD Arena, with each design's public objective checks. It is being built
in [#1029](https://github.com/tonykoop/makerbench-hwe/issues/1029).

Until then, browse the showcase on the
[MakerBench site](https://tonykoop.github.io/makerbench-hwe/).
"""


def render() -> None:
    """Build the Explore designs tab inside the current ``gr.Tab`` context."""
    import gradio as gr

    gr.Markdown(PLACEHOLDER_MD)
