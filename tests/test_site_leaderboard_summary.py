"""Summary board grouping, rank integrity, native details, and responsive layout."""

import copy
import importlib.util
import json
import os
import threading
from collections import defaultdict
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("summary_builder", ROOT / "site/build_data.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


# Only these identifier spellings/harness names denote the same underlying model.
# Unlisted identifiers stay distinct so future family collisions fail loudly.
MODEL_ALIASES = {
    "claude-code-sonnet-4-6": "claude-code-sonnet-4.6",
    "claude-code-haiku-4-5": "claude-code-haiku-4.5",
    "antigravity-gemini-3.5-flash": "gemini-3.5-flash",
}


@pytest.mark.parametrize("source", ["committed", "builder"])
def test_every_model_family_contains_one_underlying_model(source):
    payload = json.loads((ROOT / "site/data/leaderboard.json").read_text())
    groups = defaultdict(set)
    for model in payload["models"]:
        identifier = model["identifier"]
        family = model["model_family"] if source == "committed" else builder.model_family(identifier)
        groups[family].add(MODEL_ALIASES.get(identifier, identifier))
    collisions = {family: sorted(ids) for family, ids in groups.items() if len(ids) > 1}
    assert not collisions, f"Distinct underlying models share model_family: {collisions}"


def summary_payload():
    payload = json.loads((ROOT / "site/data/leaderboard.json").read_text())
    template = next(m for m in payload["models"] if m["tracks"]["blind"].get("n_families_scored", 0) >= 4)

    def model(identifier, family, coverage, score, effort="high", league="autonomous"):
        row = copy.deepcopy(template)
        row.update(identifier=identifier, model_family=family, reasoning_level=effort,
                   agent_identifier="test_harness", league=league, is_control=False,
                   is_human_baseline=False, model_page="models/test/index.html")
        for track in row["tracks"].values():
            track.update(has_data=True, n_families_scored=coverage, overall_mean=score)
        return row

    payload["models"] = [
        model("alpha-low", "alpha", 4, 4.0, "low"),
        model("alpha-medium", "alpha", 8, 3.0, "medium"),
        model("alpha-xhigh", "alpha", 8, 3.0, "xhigh"),
        model("alpha-high", "alpha", 8, 3.0),
        model("alpha-high-low-score", "alpha", 8, 2.0),
        model("beta", "beta", 4, 3.5),
        model("sparse", "sparse", 1, 4.0),
        model("assisted", "assisted", 6, 4.0, league="assisted_workflow"),
    ]
    payload["leagues"] = [{"id": "autonomous", "label": "Autonomous"},
                          {"id": "assisted_workflow", "label": "Assisted workflow"}]
    return payload


@pytest.mark.parametrize("source", ["fixture", "real"])
def test_hero_matches_leaderboard_rank_one(source):
    import re

    payload = summary_payload() if source == "fixture" else json.loads(
        (ROOT / "site/data/leaderboard.json").read_text())
    models = payload['models']
    if source == 'fixture':
        # Even perfect, fully covered controls and humans cannot take rank one.
        for flag in ('is_control', 'is_human_baseline'):
            reference = copy.deepcopy(models[0])
            reference.update(identifier=flag, model_family=flag, **{flag: True})
            reference['tracks']['blind'].update(n_families_scored=49, overall_mean=4.0)
            models.append(reference)
    original = copy.deepcopy(payload)
    ranked, _, _ = builder.select_leaderboard_entries(models)
    leader = ranked[0][0]
    hero = builder.build_hero_stats(models, payload['task_families'])['stats'][0]
    board = builder._prerender_leaderboard_html(payload)
    first_row = re.search(r'<tr class="lb-primary">(.*?)</tr>', board, re.S)[1]
    assert '<td data-label="Rank" class="lb-rank">1</td>' in first_row
    assert builder.plain_display_model(leader) in first_row
    assert hero['label'] == '#1 on the leaderboard'
    assert hero['display'] == f"{leader['tracks']['blind']['overall_mean']:.2f}/4"
    assert hero['detail'] == (
        f"{builder.display_model(leader)} · "
        f"{leader['tracks']['blind']['n_families_scored']}/{len(payload['task_families'])} families"
    )
    assert payload == original
    if source == 'fixture':
        assert leader['identifier'] == 'beta'
        assert next(e[0] for e in ranked if e[0]['model_family'] == 'alpha')['identifier'] == 'alpha-high'
        assert hero['value'] == 3.5  # alpha-low is a perfect, narrower variant.
    else:
        assert leader['identifier'] == 'claude-code-sonnet-5'
        assert hero['display'] == '2.93/4'
        assert '11/49 families' in hero['detail']


def test_prerender_keeps_every_variant_and_family_without_mutating_data():
    data = summary_payload()
    original = copy.deepcopy(data)
    html = builder._prerender_leaderboard_html(data)
    assert data == original
    assert html.count('class="lb-primary"') == 4
    assert html.count('class="lb-variant"') == 4
    assert "+4 variants" in html
    assert html.count('class="lb-families"') == len(data["models"])
    assert html.count('class="lb-family-table"') == len(data["models"])
    assert "Limited coverage · unranked" in html
    assert " open" not in html


@pytest.mark.parametrize("javascript", [False, True])
@pytest.mark.parametrize("width", [1440, 390])
def test_summary_rank_details_and_responsiveness(javascript, width):
    playwright = pytest.importorskip("playwright.sync_api")
    payload = summary_payload()
    payload['hero_stats'] = builder.build_hero_stats(payload['models'], payload['task_families'])
    html = (ROOT / "site/index.html").read_text()
    import re
    html = re.sub(r'(<!-- prerender:leaderboard -->).*?(<!-- /prerender:leaderboard -->)',
                  lambda m: m[1] + builder._prerender_leaderboard_html(payload) + m[2],
                  html, flags=re.S)

    html = re.sub(r'(<!-- prerender:hero-stats -->).*?(<!-- /prerender:hero-stats -->)',
                  lambda m: m[1] + builder._prerender_hero_stats_html(payload) + m[2],
                  html, flags=re.S)

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(ROOT / "site")))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(args=os.environ.get("ARENA_STUDIO_BROWSER_ARGS", "").split())
            page = browser.new_page(java_script_enabled=javascript, viewport={"width": width, "height": 1000})
            base = f"http://127.0.0.1:{server.server_port}"
            page.route(base + "/", lambda route: route.fulfill(content_type="text/html", body=html))
            page.route("**/data/leaderboard.json", lambda route: route.fulfill(
                content_type="application/json", body=json.dumps(payload)))
            page.goto(base + "/", wait_until="networkidle")
            if javascript:
                page.locator("#lb-note").filter(has_text="4 models").wait_for()
            board = page.locator("#lb-container")
            autonomous = board.locator('.lb-league[aria-label="Autonomous"]')
            broad = autonomous.locator("table.lb-summary").first
            primary = broad.locator(":scope > tbody > tr.lb-primary")
            assert primary.count() == 2
            assert "beta" in primary.nth(0).inner_text()
            hero = page.locator('#hero-stats .stat').first
            assert hero.locator('.stat-val').inner_text() == '3.50/4'
            assert hero.locator('.stat-detail').inner_text().startswith('beta [high] · 4/')
            assert '#1 on the leaderboard' in hero.inner_text()
            assert "alpha-high" in primary.nth(1).inner_text()
            assert "alpha-high-low-score" not in primary.nth(1).inner_text()
            assert primary.locator(".lb-rank").all_text_contents() == ["1", "2"]
            assert board.locator(".lb-variant .lb-rank").all_text_contents() == ["—"] * 4
            sparse_table = autonomous.locator("table.lb-summary").last
            assert sparse_table.locator(".lb-primary .lb-rank").inner_text() == "—"
            assert "sparse" in sparse_table.locator(".lb-primary").inner_text()
            assert board.locator('.lb-league[aria-label="Assisted workflow"] .lb-primary .lb-rank').inner_text() == "1"
            assert broad.locator(":scope > thead th").count() == 14
            for heading in broad.locator(":scope > thead th").all():
                assert heading.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
            details = broad.locator(".lb-families").first
            assert details.locator("tbody tr").count() == len(payload["task_families"])
            assert not details.get_attribute("open")
            details.locator("summary").click()
            assert details.locator("tbody tr").first.is_visible()
            variants = broad.locator(".lb-variants")
            variants.locator(":scope > summary").click()
            assert variants.locator(".lb-variant").count() == 4
            assert variants.locator(".lb-variant").first.is_visible()
            assert board.evaluate("el => el.scrollWidth <= el.clientWidth")
            assert broad.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
            if javascript:
                overall_header = broad.locator('th[aria-sort="descending"]')
                assert overall_header.locator('button[data-key="overall"]').count() == 1
                broad.locator('button[data-key="overall"]').click()
                assert broad.locator('th[aria-sort="ascending"] button[data-key="overall"]').count() == 1
                broad.locator('button[data-key="model"]').click()
                assert broad.locator('th[aria-sort="ascending"] button[data-key="model"]').count() == 1
                assert broad.locator('button[data-key="overall"]').locator('..').get_attribute("aria-sort") == "none"
                broad.locator('button[data-key="overall"]').click()
                broad.locator('button[data-key="overall"]').click()
                # Sorting changes position, never the overall-derived rank.
                assert broad.locator(":scope > tbody > .lb-primary .lb-rank").all_text_contents() == ["2", "1"]
                page.get_by_role("button", name="Perception", exact=True).click()
                assert board.locator(".lb-primary").count() == 4
            # The committed real-data page must expose every GPT-5.6 model as a
            # primary row in both JS and static rendering, including on phones.
            page.unroute("**/data/leaderboard.json")
            page.unroute(base + "/")
            page.goto(base + "/", wait_until="networkidle")
            assert 'claude-code-sonnet-5' in page.locator('#hero-stats .stat').first.inner_text()
            assert page.locator('#hero-stats .stat-val').first.inner_text() == '2.93/4'
            assert 'claude-code-sonnet-5' in page.locator('#lb-container .lb-primary').first.inner_text()
            for tier in ("terra", "sol", "luna"):
                assert page.locator("#lb-container .lb-primary .model-name").filter(
                    has_text=f"codex-gpt-5.6-{tier}"
                ).count() == 1
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
