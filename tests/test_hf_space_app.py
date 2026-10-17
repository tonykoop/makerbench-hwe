"""HF Space app smoke tests (#1026).

The stdlib-only checks always run. The Blocks build test needs gradio and is
skipped cleanly when it is not installed (it is an optional dependency, pinned
in spaces/makerbench_hwe/requirements.txt).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPACE_DIR = ROOT / "spaces" / "makerbench_hwe"
LEADERBOARD_URL = "https://tonykoop.github.io/makerbench-hwe/"
EXPECTED_TABS = ["Grade a part", "Explore designs", "Leaderboard"]


def _load_app():
    for name in [m for m in sys.modules if m == "tabs" or m.startswith("tabs.")]:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location("makerbench_hf_space_app", SPACE_DIR / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def app():
    saved_path = list(sys.path)
    module = _load_app()
    yield module
    sys.path[:] = saved_path
    for name in [m for m in sys.modules if m == "tabs" or m.startswith("tabs.")]:
        del sys.modules[name]


def test_app_imports_without_gradio_and_orders_three_tabs(app) -> None:
    assert [m.TITLE for m in app.TAB_MODULES] == EXPECTED_TABS
    assert [m.TAB_ID for m in app.TAB_MODULES] == ["grade", "explore", "leaderboard"]
    for module in app.TAB_MODULES:
        assert callable(module.render)
        assert Path(module.__file__).parent == SPACE_DIR / "tabs"


def test_leaderboard_tab_links_live_site_and_copies_no_snapshot(app) -> None:
    lb = app.leaderboard
    assert lb.LEADERBOARD_URL == LEADERBOARD_URL
    assert LEADERBOARD_URL in lb.SUMMARY_MD
    assert f'src="{LEADERBOARD_URL}"' in lb.EMBED_HTML
    text = (SPACE_DIR / "tabs" / "leaderboard.py").read_text(encoding="utf-8")
    # No copied snapshot: no data files, no model counts or scores in the tab.
    assert not any(p.suffix in {".json", ".csv", ".parquet"} for p in SPACE_DIR.rglob("*"))
    assert not re.search(r"\b\d+\s+models?\b", text)
    assert "leaderboard.json" not in text


def test_requirements_pin_matches_space_card_sdk_version() -> None:
    readme = (SPACE_DIR / "README.md").read_text(encoding="utf-8")
    sdk = re.search(r"^sdk_version:\s*(\S+)$", readme, re.MULTILINE).group(1)
    reqs = (SPACE_DIR / "requirements.txt").read_text(encoding="utf-8")
    assert re.findall(r"^gradio==(\S+)$", reqs, re.MULTILINE) == [sdk]


def _walk(block):
    yield block
    for child in getattr(block, "children", []) or []:
        yield from _walk(child)


def test_blocks_app_builds_with_three_rendered_tabs(app) -> None:
    gr = pytest.importorskip("gradio")

    demo = app.build_demo()
    assert isinstance(demo, gr.Blocks)
    assert demo._queue.default_concurrency_limit == 1
    assert demo._queue.max_size == 8
    grading_functions = [fn for fn in demo.fns.values() if fn.concurrency_id == "public-grader"]
    assert len(grading_functions) == 3
    assert all(fn.concurrency_limit == 1 for fn in grading_functions)
    assert not any(isinstance(b, gr.File) for b in _walk(demo))

    tabs = [b for b in _walk(demo) if isinstance(b, gr.Tab)]
    assert [t.label for t in tabs] == EXPECTED_TABS
    for tab in tabs:
        assert list(_walk(tab))[1:], f"tab {tab.label!r} rendered no components"

    leaderboard = tabs[2]
    buttons = [b for b in _walk(leaderboard) if isinstance(b, gr.Button)]
    assert any(b.link == LEADERBOARD_URL for b in buttons)

    # The app config serializes (what the Space front end loads) without launching.
    config = demo.get_config_file()
    assert config["title"] == "MakerBench HWE"
