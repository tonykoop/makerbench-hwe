"""Presentation helpers and real Chart.js configuration for #1017."""

import copy
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
            tab.goto(f"http://127.0.0.1:{server.server_port}/charts.html", wait_until="networkidle")
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
      const groups = MakerBenchEfficiency.effortGroups(points);
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
        color(0,{is_human_baseline:true}) === css('--human') &&
        new Set(Array.from({length:100}, (_,i) => color(i,{model_family:'family-'+i}))).size === 12;
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
          const frontier = chart.data.datasets.find(d => d.label === 'Pareto frontier');
          const points = chart.data.datasets.find(d => d.label === 'models').data;
          const legend = document.querySelector('.efficiency-model-legend');
          const raw = points
            .find(p => p._model.identifier === 'codex-gpt-5.5' && p._model.reasoning_level === 'high' && p._model.agent_identifier === 'codex_cli');
          const groups = MakerBenchEfficiency.effortGroups(points);
          const entries = legend.querySelectorAll('.efficiency-legend-entry');
          return entries.length === Math.min(groups.length, chart.width < 500 ? 4 : 8) &&
            x.reverse === false && x.type === 'logarithmic' &&
            x.title.text.includes('top-left') && lines.length === (metric === 'time' ? 1 : 0) &&
            labels === (metric === 'time' ? 4 : 0) && tooltip([{raw}]).includes('effort H') && frontier.stepped === 'after' &&
            frontier.data.every(p => p.x > 0 && p.y > 0);
        }""", metric)


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("track", ["blind", "perception"])
def test_efficiency_followups_layout_and_contrast(page, width, theme, track):
    page.set_viewport_size({"width": width, "height": 1000})
    if page.locator("html").get_attribute("data-theme") != theme:
        page.locator('#theme-toggle').click()
    page.locator(f'#track-toggle button[data-track="{track}"]').click()
    for metric in ("time", "cost", "tokens", "attempts", "score_per_dollar", "score_per_million_tokens"):
        page.locator(f'#efficiency-metric-toggle button[data-metric="{metric}"]').click()
        page.wait_for_timeout(250)  # Chart.js finishes the responsive layout before bounds are inspected.
        result = page.evaluate("""() => {
          const chart = Chart.getChart('chart-efficiency');
          const marks = chart.data.datasets.find(d => d.label === 'models');
          const frontier = chart.data.datasets.find(d => d.label === 'Pareto frontier');
          const lines = chart.data.datasets.filter(d => d._effortLine);
          const legend = document.querySelector('.efficiency-model-legend');
          const entries = Array.from(legend.querySelectorAll('.efficiency-legend-entry'));
          const limit = chart.width < 500 ? 4 : 8;
          const groups = MakerBenchEfficiency.effortGroups(marks.data);
          const extra = legend.querySelector('.efficiency-legend-more');
          const scale = chart.scales.x;
          const bg = getComputedStyle(document.documentElement).getPropertyValue('--bg-elev').trim();
          const luminance = hex => {
            const rgb = hex.slice(1).match(/../g).map(v => parseInt(v,16)/255)
              .map(v => v <= .04045 ? v/12.92 : ((v+.055)/1.055)**2.4);
            return .2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2];
          };
          const contrast = color => {
            const a=luminance(color), b=luminance(bg);
            return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
          };
          const allowedTime = new Set(['0.1s','0.2s','0.5s','1s','2s','5s','10s','30s',
            '1m','2m','5m','10m','20m','30m','1h','2h','5h','10h','24h']);
          const ticks = scale.ticks.map(t => ({value:t.value,label:t.label}));
          const guide = frontier.data.filter(p => p._boundary);
          const measured = frontier.data.filter(p => !p._boundary);
          const expected = MakerBenchEfficiency.frontierPoints(marks.data,
            chart.options.scales.x.title.text.includes('top-right'));
          return {
            legendBound: entries.length === Math.min(groups.length,limit),
            legendIdentity: entries.every((el,i) => el.textContent.includes(groups[i][0]._model.identifier)),
            overflow: groups.length <= limit ? !extra : extra.textContent === `+${groups.length-limit} more (hover dots)`,
            ticks: ticks.length > 0 && ticks.length <= limit &&
              (scale.type !== 'logarithmic' || ticks.every(t => t.value > 0)),
            timeLabels: !chart.options.scales.x.title.text.startsWith('Avg wall-clock') ||
              ticks.every(t => allowedTime.has(t.label)),
            tickSeparation: ticks.slice(1).every((tick,i) => {
              const a = ticks[i];
              return scale.getPixelForValue(tick.value)-scale.getPixelForValue(a.value) >
                (chart.ctx.measureText(tick.label).width+chart.ctx.measureText(a.label).width)/2+4;
            }),
            padding: scale.type !== 'logarithmic' || marks.data.every(p => scale.getPixelForValue(p.x) > chart.chartArea.left+6 &&
              scale.getPixelForValue(p.x) < chart.chartArea.right-6),
            frontier: frontier.stepped === 'after' && frontier.fill === 'origin' &&
              frontier.borderWidth >= 2.5 && frontier.order > marks.order &&
              lines.every(d => frontier.order > d.order) && guide.length === (expected.length ? 1 : 0) &&
              JSON.stringify(measured) === JSON.stringify(expected.map(p => ({x:p.x,y:p.y}))),
            fill: frontier.backgroundColor.endsWith(',0.06)'),
            contrast: marks.pointBorderColor.every(c => contrast(c) >= 3) &&
              lines.every(d => contrast(d._outlineColor) >= 3),
            tooltip: marks.data.every(raw => chart.options.plugins.tooltip.callbacks.title([{raw}])
              .includes(raw._model.identifier)),
            legendLayout: entries.every(el => el.scrollWidth <= el.clientWidth+1),
          };
        }""")
        assert all(result.values()), (width, theme, track, metric, result)


@pytest.mark.parametrize("width, shown", [(1440, 8), (390, 4)])
def test_effort_legend_reports_overflow_without_hiding_dots(page, width, shown):
    payload = json.loads((ROOT / "site/data/leaderboard.json").read_text())
    template = next(m for m in payload["models"] if m["identifier"] == "codex-gpt-5.5"
                    and m["reasoning_level"] == "high" and m["agent_identifier"] == "codex_cli")
    payload["models"] = []
    for i in range(10):
        for effort, duration in (("low", 5), ("high", 20)):
            model = copy.deepcopy(template)
            model.update(identifier=f"public-model-{i}", model_family=f"public-model-{i}",
                         row_id=f"public-{i}-{effort}", reasoning_level=effort)
            model["tracks"]["blind"]["efficiency"]["metrics"]["time"]["value"] = duration + i
            payload["models"].append(model)
    pattern = "**/data/leaderboard.json"
    page.route(pattern, lambda route: route.fulfill(content_type="application/json", body=json.dumps(payload)))
    try:
        page.set_viewport_size({"width": width, "height": 1000})
        page.reload(wait_until="networkidle")
        page.locator('#track-toggle button[data-track="blind"]').click()
        page.locator('#efficiency-metric-toggle button[data-metric="time"]').click()
        assert page.locator('.efficiency-legend-entry').count() == shown
        assert page.locator('.efficiency-legend-more').inner_text() == f"+{10-shown} more (hover dots)"
        assert page.evaluate("""() => {
          const chart=Chart.getChart('chart-efficiency');
          const marks=chart.data.datasets.find(d=>d.label==='models').data;
          return marks.length===20 && chart.data.datasets.filter(d=>d._effortLine).length===10 &&
            marks.every(raw=>chart.options.plugins.tooltip.callbacks.title([{raw}]).includes(raw._model.identifier));
        }""")
    finally:
        page.unroute(pattern)
        page.reload(wait_until="networkidle")


def test_single_dot_keeps_ticks_and_a_visible_frontier(page):
    payload = json.loads((ROOT / "site/data/leaderboard.json").read_text())
    model = copy.deepcopy(next(m for m in payload["models"] if m["identifier"] == "codex-gpt-5.5"
                               and m["reasoning_level"] == "high" and m["agent_identifier"] == "codex_cli"))
    model["tracks"]["blind"]["efficiency"]["metrics"]["time"]["value"] = 17
    payload["models"] = [model]
    pattern = "**/data/leaderboard.json"
    page.route(pattern, lambda route: route.fulfill(content_type="application/json", body=json.dumps(payload)))
    try:
        page.reload(wait_until="networkidle")
        page.locator('#track-toggle button[data-track="blind"]').click()
        page.locator('#efficiency-metric-toggle button[data-metric="time"]').click()
        assert page.evaluate("""() => {
          const chart=Chart.getChart('chart-efficiency');
          const dots=chart.data.datasets.find(d=>d.label==='models').data;
          const frontier=chart.data.datasets.find(d=>d.label==='Pareto frontier').data;
          return dots.length===1 && dots[0].x===17 && chart.scales.x.ticks.length>=2 &&
            frontier.length===2 && frontier[0].x===17 && frontier[1]._boundary && frontier[1].x>17;
        }""")
    finally:
        page.unroute(pattern)
        page.reload(wait_until="networkidle")
