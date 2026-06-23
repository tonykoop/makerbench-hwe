#!/usr/bin/env python3
"""generate_opportunity_matrix.py — the Opportunity Matrix ("CAD Lore") views (#120).

Builds the scored model x CAD x plugin cube (see makerbench/opportunity_matrix.py)
and emits three companion surfaces:

  * opportunity-matrix.json   — the full scored cube (schema makerbench-opportunity-matrix-v1).
  * opportunity-matrix.html   — an interactive heatmap: one model-faceted heatmap of
                                CAD (rows) x plugin (cols), proven cells solid and
                                empty high-potential cells dashed (= plugin vacancies),
                                plus a ranked Top Vacancies backlog panel. Reuses the
                                run-library visual theme (mb#104).
  * OPPORTUNITY_VACANCIES.md  — the Top Vacancies report = the ranked build backlog.

Demonstrated capability is read from results/ (RunResults); WorkflowManifest
evidence (when present) promotes a coordinate from "vacancy" to "proven". The core
is stdlib-only, so the cube regenerates anywhere.

Usage:
    python3 scripts/generate_opportunity_matrix.py \
        [--results-dir results] [--runs-root results] [--with-domain | --with-process] \
        [--output-json opportunity-matrix.json] \
        [--output-html opportunity-matrix.html] \
        [--output-report docs/OPPORTUNITY_VACANCIES.md]
"""
from __future__ import annotations

import argparse
import html
import importlib.util
import json
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "makerbench_opportunity_matrix",
    Path(__file__).resolve().parents[1] / "makerbench" / "opportunity_matrix.py",
)
_om = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _om
_SPEC.loader.exec_module(_om)


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def _heat_color(score, *, vacancy: bool) -> str:
    """Cream->cedar->lapis ramp; vacancies render lighter (dashed border in CSS)."""
    if score is None:
        return "#efe9dc"
    s = max(0.0, min(1.0, float(score)))
    # interpolate cream(248,244,233) -> cedar(185,139,71) -> lapis(30,58,138)
    if s < 0.5:
        t = s / 0.5
        r = int(248 + (185 - 248) * t)
        g = int(244 + (139 - 244) * t)
        b = int(233 + (71 - 233) * t)
    else:
        t = (s - 0.5) / 0.5
        r = int(185 + (30 - 185) * t)
        g = int(139 + (58 - 139) * t)
        b = int(71 + (138 - 71) * t)
    alpha = 0.55 if vacancy else 1.0
    return f"rgba({r},{g},{b},{alpha})"


def _text_color(score) -> str:
    return "#fff" if isinstance(score, (int, float)) and score >= 0.6 else "#1f1a14"


def render_heatmap(cube: dict) -> str:
    """One CAD x plugin heatmap per model, cells colored by score."""
    cad_axis = cube["axes"]["cad"]
    plugin_axis = cube["axes"]["plugin"]
    catalog = _om.Catalog()
    by_coord = {}
    for c in cube["coordinates"]:
        by_coord.setdefault(c["model"], {})[(c["cad"], c["plugin"])] = c

    blocks = []
    for model in cube["axes"]["model"]:
        mid = model["id"]
        cells_for_model = by_coord.get(mid, {})
        if not cells_for_model:
            continue
        header = "".join(f"<th class='rot'><span>{esc(p['label'])}</span></th>" for p in plugin_axis)
        rows = []
        for cad in cad_axis:
            tds = []
            for plugin in plugin_axis:
                coord = cells_for_model.get((cad["id"], plugin["id"]))
                if coord is None:
                    tds.append("<td class='na'></td>")
                    continue
                score = coord["score"]
                vacancy = not coord["has_evidence"]
                bg = _heat_color(score, vacancy=vacancy)
                fg = _text_color(score)
                cls = "vac" if vacancy else "proven"
                txt = f"{score:.2f}" if isinstance(score, (int, float)) else "—"
                title = (
                    f"{_om.coordinate_label(coord, catalog)} · "
                    f"{'potential' if vacancy else 'opportunity'} {txt} · "
                    f"open {coord['openness']} · ease {coord['ease']}"
                )
                tds.append(
                    f"<td class='cell {cls}' style='background:{bg};color:{fg}' "
                    f"title='{esc(title)}'>{txt}</td>"
                )
            rows.append(f"<tr><th class='rowh'>{esc(cad['label'])}</th>{''.join(tds)}</tr>")
        blocks.append(
            f"<section class='facet'><h3>{esc(model['label'])}</h3>"
            f"<div class='scroll'><table class='heat'>"
            f"<thead><tr><th class='corner'>CAD ＼ plugin</th>{header}</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div></section>"
        )
    return "\n".join(blocks)


def render_vacancies(cube: dict, limit: int = 20) -> str:
    catalog = _om.Catalog()
    items = []
    for rank, v in enumerate(cube["top_vacancies"][:limit], start=1):
        items.append(
            f"<li><span class='rank'>{rank}</span>"
            f"<span class='vlabel'>{esc(_om.coordinate_label(v, catalog))}</span>"
            f"<span class='vscore'>{v['potential_score']:.3f}</span></li>"
        )
    return "<ol class='vac-list'>" + "".join(items) + "</ol>"


def render_html(cube: dict) -> str:
    counts = cube["counts"]
    weights = " · ".join(f"{k} {v}" for k, v in cube["weights"].items())
    return HTML_TEMPLATE.format(
        heatmaps=render_heatmap(cube),
        vacancies=render_vacancies(cube),
        n_coords=counts["coordinates"],
        n_proven=counts["proven"],
        n_vac=counts["vacancies"],
        weights=esc(weights),
    )


HTML_TEMPLATE = """\
<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="index, follow, noai, noimageai" />
<title>MakerBench · Opportunity Matrix (CAD Lore)</title>
<style>
:root{{--walnut:#3d2817;--cedar:#b98b47;--cream:#f8f4e9;--paper:#fff;--lapis:#1e3a8a;--gold:#d4a017;--ink:#1f1a14;--muted:#6b5e4d;--rule:#e0d6c5;--ui:"Inter","Helvetica Neue",Arial,sans-serif;--serif:Georgia,serif;--mono:ui-monospace,Menlo,Consolas,monospace}}
*,*::before,*::after{{box-sizing:border-box}}
body{{margin:0;background:var(--cream);color:var(--ink);font-family:var(--ui);font-size:15px;line-height:1.5}}
a{{color:var(--lapis)}}
.app-bar{{position:sticky;top:0;z-index:30;background:var(--walnut);color:var(--cream);border-bottom:3px solid var(--gold);display:flex;align-items:center;gap:18px;padding:12px 22px}}
.app-bar .wm{{font-family:var(--serif);font-size:20px;color:var(--gold)}}
.app-bar .tag{{font-size:13px;color:var(--cream);opacity:.85}}
.wrap{{max-width:1400px;margin:0 auto;padding:24px 24px 80px}}
.hero h1{{font-family:var(--serif);font-size:30px;color:var(--walnut);margin:0 0 4px}}
.hero p{{color:var(--muted);max-width:820px;margin:0 0 6px}}
.stats{{display:flex;gap:12px;margin:14px 0}}
.stat{{background:var(--paper);border:1px solid var(--rule);border-radius:5px;padding:9px 16px;min-width:120px}}
.stat .k{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}}
.stat .v{{font-family:var(--serif);font-size:21px;color:var(--walnut)}}
.layout{{display:grid;grid-template-columns:1fr 320px;gap:22px;align-items:start}}
@media(max-width:1000px){{.layout{{grid-template-columns:1fr}}}}
.facet{{background:var(--paper);border:1px solid var(--rule);border-radius:5px;padding:12px 14px;margin-bottom:16px}}
.facet h3{{font-family:var(--serif);color:var(--walnut);margin:0 0 8px;font-size:17px}}
.scroll{{overflow-x:auto}}
table.heat{{border-collapse:collapse;font-size:12px}}
table.heat th{{font-weight:600;color:var(--muted)}}
table.heat .corner{{text-align:left;font-size:10px;color:var(--muted);padding:4px 6px}}
table.heat .rowh{{text-align:right;padding:4px 8px;color:var(--walnut);white-space:nowrap}}
table.heat th.rot{{height:84px;vertical-align:bottom;padding:0 2px}}
table.heat th.rot span{{display:inline-block;writing-mode:vertical-rl;transform:rotate(180deg);white-space:nowrap;font-size:11px}}
td.cell{{width:46px;height:30px;text-align:center;font-family:var(--mono);font-size:11px;border:1px solid rgba(255,255,255,.5);border-radius:2px}}
td.cell.vac{{border:1px dashed var(--cedar)}}
td.na{{width:46px;height:30px;background:repeating-linear-gradient(45deg,#f2ecdf,#f2ecdf 4px,#e9e1cf 4px,#e9e1cf 8px)}}
aside{{position:sticky;top:70px}}
aside h2{{font-family:var(--serif);color:var(--walnut);font-size:19px;margin:0 0 4px}}
aside .sub{{color:var(--muted);font-size:13px;margin:0 0 10px}}
.vac-list{{list-style:none;margin:0;padding:0;counter-reset:none}}
.vac-list li{{display:flex;align-items:center;gap:10px;background:var(--paper);border:1px solid var(--rule);border-left:3px solid var(--cedar);border-radius:4px;padding:7px 10px;margin-bottom:6px}}
.vac-list .rank{{font-family:var(--mono);font-size:11px;color:var(--muted);min-width:18px}}
.vac-list .vlabel{{flex:1;font-size:12.5px;color:var(--walnut)}}
.vac-list .vscore{{font-family:var(--mono);font-size:12px;color:var(--lapis)}}
.legend{{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin:6px 0 16px}}
.legend .sw{{display:inline-block;width:14px;height:14px;border-radius:2px;vertical-align:-2px;margin-right:4px}}
footer{{margin-top:40px;color:var(--muted);font-size:12px;font-family:var(--mono)}}
.formula{{font-family:var(--mono);font-size:12px;color:var(--muted)}}
</style></head>
<body>
<div class="app-bar"><span class="wm">MakerBench</span><span class="tag">Opportunity Matrix · CAD Lore · mb#120</span></div>
<div class="wrap">
<div class="hero"><h1>Opportunity Matrix — "CAD Lore"</h1>
<p>The space of AI-HWE workflow <em>combinations</em> — LLM model × CAD host × plugin/bridge — scored to show which coordinates are proven powerful and which are empty <strong>plugin vacancies</strong> worth building. High score = proven combo. High potential but empty (dashed) = the build backlog.</p>
<p class="formula">score = {weights}</p></div>
<div class="stats">
  <div class="stat"><div class="k">Coordinates</div><div class="v">{n_coords}</div></div>
  <div class="stat"><div class="k">Proven</div><div class="v">{n_proven}</div></div>
  <div class="stat"><div class="k">Vacancies</div><div class="v">{n_vac}</div></div>
</div>
<div class="legend">
  <span><span class="sw" style="background:rgba(248,244,233,1);border:1px solid #e0d6c5"></span>low</span>
  <span><span class="sw" style="background:rgba(185,139,71,1)"></span>mid</span>
  <span><span class="sw" style="background:rgba(30,58,138,1)"></span>high</span>
  <span><span class="sw" style="border:1px dashed var(--cedar)"></span>vacancy (no evidence — potential shown)</span>
  <span><span class="sw" style="background:repeating-linear-gradient(45deg,#f2ecdf,#f2ecdf 3px,#e9e1cf 3px,#e9e1cf 6px)"></span>incompatible</span>
</div>
<div class="layout">
<main>{heatmaps}</main>
<aside>
  <h2>Top Vacancies</h2>
  <p class="sub">Ranked empty high-potential coordinates = the build backlog. Filling the top cell is the highest-leverage next stack to stand up.</p>
  {vacancies}
</aside>
</div>
<footer>Generated by scripts/generate_opportunity_matrix.py · MakerBench Opportunity Matrix (mb#120)</footer>
</div>
</body></html>
"""


def render_report(cube: dict, limit: int = 25) -> str:
    """Markdown Top Vacancies backlog + proven-stack summary."""
    catalog = _om.Catalog()
    counts = cube["counts"]
    lines = [
        "# Opportunity Matrix — Top Vacancies backlog",
        "",
        "> Generated by `scripts/generate_opportunity_matrix.py` from the scored",
        "> model × CAD × plugin cube. Do not edit by hand — see",
        "> [`OPPORTUNITY_MATRIX.md`](OPPORTUNITY_MATRIX.md) for the scoring spec.",
        "",
        f"- Coordinates scored: **{counts['coordinates']}**",
        f"- Proven (have run evidence): **{counts['proven']}**",
        f"- Plugin vacancies (empty, ranked below): **{counts['vacancies']}**",
        "",
        "## Top plugin vacancies (ranked by potential)",
        "",
        "Each row is an empty high-value coordinate — a stack worth standing up.",
        "Potential = model capability prior × openness × ease × autonomy affinity,",
        "weighted per the scoring spec.",
        "",
        "| # | Model × CAD × Plugin | Potential | Openness | Ease | Capability prior |",
        "| - | -------------------- | --------: | -------: | ---: | ---------------: |",
    ]
    for rank, v in enumerate(cube["top_vacancies"][:limit], start=1):
        lines.append(
            f"| {rank} | {_om.coordinate_label(v, catalog)} | "
            f"{v['potential_score']:.3f} | {v['openness']} | {v['ease']} | "
            f"{v['capability_prior']} |"
        )

    lines += ["", "## Proven combinations (have workflow-track evidence)", ""]
    if cube["top_proven"]:
        lines += [
            "| # | Model × CAD × Plugin | Opportunity | Capability | Autonomy | Runs |",
            "| - | -------------------- | ----------: | ---------: | -------: | ---: |",
        ]
        for rank, p in enumerate(cube["top_proven"], start=1):
            lines.append(
                f"| {rank} | {_om.coordinate_label(p, catalog)} | "
                f"{p['opportunity_score']:.3f} | {p['capability']} | "
                f"{p['autonomy']} | {p['n_runs']} |"
            )
    else:
        lines.append(
            "_No WorkflowManifest evidence committed yet — every coordinate is "
            "currently a vacancy. As the workflow track (#100) lands manifests, "
            "proven stacks will graduate out of the backlog above._"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description="Generate the Opportunity Matrix (CAD Lore) cube + views")
    p.add_argument("--results-dir", type=Path, default=repo_root / "results")
    p.add_argument("--runs-root", type=Path, default=repo_root / "results",
                   help="Where to scan for WorkflowManifest evidence (default: results/).")
    axis = p.add_mutually_exclusive_group()
    axis.add_argument("--with-domain", action="store_true", help="Add the 4th domain axis.")
    axis.add_argument(
        "--with-process",
        action="store_true",
        help="Add the #183 fabrication-process/craft axis.",
    )
    p.add_argument("--output-json", type=Path, default=repo_root / "site" / "data" / "opportunity-matrix.json")
    p.add_argument("--output-html", type=Path, default=repo_root / "site" / "opportunity-matrix.html")
    p.add_argument("--output-report", type=Path, default=repo_root / "docs" / "OPPORTUNITY_VACANCIES.md")
    a = p.parse_args(argv)

    cube = _om.build_cube(
        results_dir=a.results_dir if a.results_dir.exists() else None,
        runs_root=a.runs_root if a.runs_root.exists() else None,
        with_domain=a.with_domain,
        with_process=a.with_process,
    )
    for path in (a.output_json, a.output_html, a.output_report):
        path.parent.mkdir(parents=True, exist_ok=True)
    a.output_json.write_text(json.dumps(cube, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    a.output_html.write_text(render_html(cube), encoding="utf-8")
    a.output_report.write_text(render_report(cube), encoding="utf-8")
    print(
        f"opportunity-matrix: {cube['counts']['coordinates']} coords "
        f"({cube['counts']['proven']} proven, {cube['counts']['vacancies']} vacancies) -> "
        f"{a.output_json}, {a.output_html}, {a.output_report}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
