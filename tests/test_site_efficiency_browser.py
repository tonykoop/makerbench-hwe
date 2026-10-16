"""Presentation helpers and real Chart.js configuration for #1017."""

import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def page():
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(ROOT / "site"))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            tab = browser.new_page()
            tab.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
            tab.wait_for_function("window.MakerBenchEfficiency && Chart.getChart('chart-efficiency')")
            yield tab
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_grouping_order_and_unset_effort(page):
    assert page.evaluate("""() => {
      const h = MakerBenchEfficiency;
      const point = (id, agent, effort, x) => ({x, y: 2, _model: {
        identifier: id, agent_identifier: agent, reasoning_level: effort}});
      const points = [point('a','cli','xhigh',1), point('a','cli','high',2),
        point('a','cli',null,3), point('a','cli','default_or_unset',4),
        point('a','cli','low',9), point('a','cli','medium',8),
        point('a','api','low',1), point('a','api','high',2),
        point('b','cli','low',1), point('a','legacy_unknown','medium',1),
        point('a','legacy_unknown','high',2), point('a',null,'low',1),
        point('a',null,'high',2)];
      const groups = h.effortGroups(points);
      return groups.map(g => g.map(p => [p._model.identifier,
        p._model.agent_identifier, h.effortCode(p._model), p.x]));
    }""") == [
        [["a", "cli", "L", 9], ["a", "cli", "M", 8],
         ["a", "cli", "H", 2], ["a", "cli", "X", 1]],
        [["a", "api", "L", 1], ["a", "api", "H", 2]],
    ]


def test_colors_ignore_row_order_and_preserve_baselines(page):
    assert page.evaluate("""() => {
      const color = MakerBenchEfficiency.modelColor;
      const a = {identifier:'a-low', model_family:'family'};
      const b = {identifier:'a-high', model_family:'family'};
      const css = key => getComputedStyle(document.documentElement).getPropertyValue(key).trim();
      return color(0,a) === color(99,b) &&
        color(0,{identifier:'fallback'}) === color(22,{identifier:'fallback'}) &&
        color(0,{is_control:true}) === css('--control') &&
        color(0,{is_human_baseline:true}) === css('--human');
    }""")


def test_frontier_excludes_failed_and_invalid_telemetry(page):
    assert page.evaluate("""() => {
      const points = [{x:0,y:0}, {x:0,y:4}, {x:1,y:0}, {x:-1,y:4},
        {x:NaN,y:4}, {x:Infinity,y:4}, {x:1,y:1}, {x:2,y:2}, {x:3,y:1}];
      return [false,true].map(higher =>
        MakerBenchEfficiency.frontierPoints(points,higher).map(p => [p.x,p.y]));
    }""") == [[[1, 1], [2, 2]], [[2, 2], [3, 1]]]


def test_real_codex_rows_and_chart_options(page):
    models = json.loads((ROOT / "site/data/leaderboard.json").read_text())["models"]
    codex = [m for m in models if m["identifier"] == "codex-gpt-5.5"]
    assert len(codex) == 7
    assert page.evaluate("""models => {
      const h = MakerBenchEfficiency;
      const groups = h.effortGroups(models.map(m => ({_model:m})));
      return groups.length === 1 && groups[0].length === 4 &&
        groups[0].every(p => p._model.agent_identifier === 'codex_cli') &&
        groups[0].map(p => h.effortCode(p._model)).join('') === 'LMHX' &&
        new Set(models.map((m,i) => h.modelColor(i,m))).size === 1;
    }""", codex)
    for metric in ("time", "cost", "tokens"):
        page.locator(f'#efficiency-metric-toggle button[data-metric="{metric}"]').click()
        assert page.evaluate("""metric => {
          const chart = Chart.getChart('chart-efficiency');
          const lines = chart.data.datasets.filter(d => d.label.startsWith('codex-gpt-5.5 ·'));
          const x = chart.options.scales.x;
          const tooltip = chart.options.plugins.tooltip.callbacks.title;
          const labels = lines.flatMap(d => d.data).length;
          const frontier = chart.data.datasets.find(d => d.label === 'efficient frontier');
          const points = chart.data.datasets.find(d => d.label === 'models').data;
          const legend = document.querySelector('.efficiency-model-legend');
          const raw = points
            .find(p => p._model.identifier === 'codex-gpt-5.5' && p._model.reasoning_level === 'high' && p._model.agent_identifier === 'codex_cli');
          return Array.from(new Set(points.map(p => p._model.identifier)))
            .every(id => legend.textContent.includes(id)) && x.reverse === false && x.type === 'logarithmic' &&
            x.title.text.includes('top-left') && lines.length === (metric === 'time' ? 1 : 0) &&
            labels === (metric === 'time' ? 4 : 0) && tooltip([{raw}]).includes('effort H') && frontier.stepped === 'after' &&
            frontier.data.every(p => p.x > 0 && p.y > 0);
        }""", metric)
