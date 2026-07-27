"""Self-contained local HTML report for a Code-CAD Arena run (#591, step 1).

Renders one arena run directory into a single ``report.html`` styled with the
same design tokens as ``site/assets/app.css``, so the markup can later migrate
onto the public leaderboard site with minimal rework. The report is local-only
by design: publication to ``site/`` is gated on multi-voter Elo (see issue
#591 and docs/CODE_CAD_ARENA.md).

Everything is read from the run dir (run log, vote files); image references
are relative paths, so the file works when the run dir is opened locally or
copied elsewhere wholesale.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Mapping, Optional

from . import code_cad_arena_runner as runner
from .code_cad_agreement import build_agreement_summary
from .code_cad_arena import build_elo_leaderboard


SCHEMA = "makerbench-code-cad-arena-report-v1"

GATE_COLUMNS = (
    "renders",
    "watertight",
    "nonzero_volume",
    "fits_envelope",
    "min_wall",
    "body_count",
)


def load_run_payloads(run_dir: Path) -> dict:
    """Assemble everything the report needs from one run directory."""

    run_dir = Path(run_dir)
    run_log = json.loads((run_dir / "run_log.json").read_text(encoding="utf-8"))
    votes = runner.votes_to_elo_votes(run_dir / "votes.revealed.jsonl")
    entrants = (run_log.get("config") or {}).get("model_ids") or []
    elo = build_elo_leaderboard(votes, entrants=entrants)
    scoreline = runner.collect_objective_scoreline(run_log)
    judge = (
        runner.judge_elo_payload(run_dir, run_log)
        if (run_dir / "votes.judge.jsonl").exists()
        else None
    )
    agreement = build_agreement_summary(runner.build_agreement_rows(elo, scoreline, judge))
    return {
        "run_log": run_log,
        "elo": elo,
        "scoreline": scoreline,
        "judge": judge,
        "agreement": agreement,
    }


def gate_means(run_log: Mapping[str, object]) -> dict[str, dict[str, float]]:
    """Mean sub-score per entrant per gate; errored trials count as 0.0."""

    sums: dict[str, dict[str, float]] = {}
    counts: dict[str, int] = {}
    for entry in run_log.get("trials") or []:
        model_id = str(entry.get("model_id") or "")
        status = str(entry.get("status") or "pending")
        if not model_id or status == "pending":
            continue
        sub = ((entry.get("result") or {}).get("objective") or {}).get("sub_scores") or {}
        bucket = sums.setdefault(model_id, {gate: 0.0 for gate in GATE_COLUMNS})
        counts[model_id] = counts.get(model_id, 0) + 1
        for gate in GATE_COLUMNS:
            value = sub.get(gate)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                bucket[gate] += float(value)
    return {
        model_id: {gate: total / counts[model_id] for gate, total in bucket.items()}
        for model_id, bucket in sums.items()
    }


def gallery_cells(run_log: Mapping[str, object], run_dir: Path) -> list[dict]:
    """Per-(instrument, seed, rep) candidate cards, including failed entrants."""

    cells: dict[tuple, list[dict]] = {}
    for entry in run_log.get("trials") or []:
        status = str(entry.get("status") or "pending")
        if status == "pending":
            continue
        result = entry.get("result") or {}
        objective = result.get("objective") or {}
        png = (result.get("artifacts") or {}).get("png_path")
        rel_png: Optional[str] = None
        if png:
            png_path = Path(png)
            try:
                rel_png = png_path.relative_to(run_dir).as_posix()
            except ValueError:
                rel_png = png_path.as_posix()
            if not (run_dir / rel_png).exists():
                rel_png = None
        key = (
            str(entry.get("instrument_id")),
            int(entry.get("seed", 0)),
            int(entry.get("rep", 0)),
        )
        cells.setdefault(key, []).append(
            {
                "model_id": str(entry.get("model_id")),
                "status": status,
                "pass_rate": objective.get("objective_pass_rate"),
                "png": rel_png,
                "error": entry.get("error"),
                "failure_stage": result.get("failure_stage"),
            }
        )
    out = []
    for (instrument_id, seed, rep), cards in sorted(cells.items()):
        cards.sort(key=lambda card: card["model_id"])
        out.append(
            {"instrument_id": instrument_id, "seed": seed, "rep": rep, "cards": cards}
        )
    return out


def _esc(value: object) -> str:
    return html.escape(str(value))


def _fmt_rate(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):.2f}"
    return "–"


def _chip(card: Mapping[str, object]) -> str:
    rate = card.get("pass_rate")
    if card["status"] == "error":
        return '<span class="chip chip-bad">✗ error</span>'
    if not isinstance(rate, (int, float)) or isinstance(rate, bool):
        return '<span class="chip chip-bad">✗ no score</span>'
    rate = float(rate)
    if rate >= 1.0:
        cls, icon = "chip-good", "✓"
    elif rate >= 0.75:
        cls, icon = "chip-mid", "◐"
    else:
        cls, icon = "chip-bad", "✗"
    return f'<span class="chip {cls}">{icon} {rate:.2f}</span>'


def _stat_tiles(payloads: Mapping[str, object]) -> str:
    log = payloads["run_log"]
    # Count trial rows directly: the orchestrator only writes summary.counts at
    # the end of a pass, so a mid-run report would otherwise show all zeros.
    counts: dict[str, int] = {}
    for entry in log.get("trials") or []:
        status = str(entry.get("status") or "pending")
        counts[status] = counts.get(status, 0) + 1
    total = len(log.get("trials") or [])
    done = sum(v for k, v in counts.items() if k in ("scored", "auto_fail", "error"))
    elo = payloads["elo"]
    rho = (payloads["agreement"].get("agreement") or {}).get("rho")
    tiles = [
        ("Trials", f"{done}/{total}"),
        ("Scored", str(counts.get("scored", 0))),
        ("Auto-fail", str(counts.get("auto_fail", 0))),
        ("Errors", str(counts.get("error", 0))),
        ("Votes", str(elo.get("votes", 0))),
        ("Voters", str(elo.get("voters", 0))),
        ("Spearman ρ", "n/a" if rho is None else f"{float(rho):.3f}"),
    ]
    cells = "".join(
        f'<div class="tile"><div class="tile-value">{_esc(v)}</div>'
        f'<div class="tile-label">{_esc(k)}</div></div>'
        for k, v in tiles
    )
    return f'<section class="tiles" aria-label="Run summary">{cells}</section>'


def _dual_scoreline_table(agreement: Mapping[str, object]) -> str:
    has_judge = bool(agreement.get("matrix"))
    rows = []
    for row in agreement.get("rankings") or []:
        record = dict(row)
        judge_cells = ""
        if has_judge:
            judge_cells = (
                f'<td class="num">{_esc(record.get("judge_elo") or "–")}</td>'
                f'<td class="num">{_esc(record.get("judge_rank") or "–")}</td>'
                f'<td class="num">{_esc(record.get("n_judge_votes", 0))}</td>'
            )
        rows.append(
            "<tr>"
            f'<td class="mono">{_esc(record["entrant"])}</td>'
            f'<td class="num">{_esc(record.get("subjective_elo") or "–")}</td>'
            f'<td class="num">{_esc(record.get("subjective_rank") or "–")}</td>'
            f'<td class="num">{_fmt_rate(record.get("objective_pass_rate"))}</td>'
            f'<td class="num">{_esc(record.get("objective_rank") or "–")}</td>'
            f'{judge_cells}'
            f'<td class="num">{_esc(record.get("rank_delta") if record.get("rank_delta") is not None else "–")}</td>'
            f'<td class="num">{_esc(record.get("n_subjective_votes", 0))}</td>'
            f'<td class="num">{_esc(record.get("n_objective_trials", 0))}</td>'
            "</tr>"
        )
    judge_head = (
        '<th class="num">Judge Elo</th><th class="num">Judge rank</th>'
        '<th class="num">Judge votes</th>'
        if has_judge
        else ""
    )
    return (
        '<table><thead><tr><th>Entrant</th><th class="num">Elo</th>'
        '<th class="num">Elo rank</th><th class="num">Objective</th>'
        f'<th class="num">Obj rank</th>{judge_head}<th class="num">Rank Δ</th>'
        '<th class="num">Votes</th><th class="num">Trials</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def _triangulation_matrix(matrix: Mapping[str, object]) -> str:
    rows = []
    for key, label in (
        ("subjective_objective", "Subjective Elo × Objective"),
        ("subjective_judge", "Subjective Elo × VLM judge"),
        ("objective_judge", "Objective × VLM judge"),
    ):
        cell = dict(matrix.get(key) or {})
        rho = cell.get("rho")
        rows.append(
            "<tr>"
            f'<td>{_esc(label)}</td>'
            f'<td class="num">{"–" if rho is None else f"{float(rho):.3f}"}</td>'
            f'<td class="num">{_esc(cell.get("n", 0))}</td>'
            f'<td>{_esc(cell.get("interpretation") or "n/a")}</td>'
            "</tr>"
        )
    return (
        '<table><thead><tr><th>Pair</th><th class="num">Spearman ρ</th>'
        '<th class="num">n</th><th>Interpretation</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def _objective_bars(scoreline: list[Mapping[str, object]]) -> str:
    bars = []
    for row in scoreline:
        rate = float(row["objective_pass_rate"])
        pct = max(0.0, min(1.0, rate)) * 100
        bars.append(
            '<div class="bar-row" '
            f'title="{_esc(row["entrant"])}: {rate:.3f} mean pass-rate over '
            f'{_esc(row["n_objective_trials"])} trials">'
            f'<span class="bar-name mono">{_esc(row["entrant"])}</span>'
            f'<span class="bar-track"><span class="bar-fill" style="width:{pct:.1f}%"></span></span>'
            f'<span class="bar-value">{rate:.2f}</span>'
            "</div>"
        )
    return f'<div class="bars" role="img" aria-label="Mean objective pass-rate per entrant">{"".join(bars)}</div>'


def _gate_matrix(means: Mapping[str, Mapping[str, float]]) -> str:
    head = "".join(f'<th class="num">{_esc(g)}</th>' for g in GATE_COLUMNS)
    rows = []
    for entrant in sorted(means):
        cells = "".join(
            f'<td class="num">{means[entrant][gate]:.2f}</td>' for gate in GATE_COLUMNS
        )
        rows.append(f'<tr><td class="mono">{_esc(entrant)}</td>{cells}</tr>')
    return (
        f'<table><thead><tr><th>Entrant</th>{head}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def _gallery(cells: list[dict]) -> str:
    sections = []
    for cell in cells:
        cards = []
        for card in cell["cards"]:
            if card["png"]:
                media = (
                    f'<img src="{_esc(card["png"])}" loading="lazy" '
                    f'alt="Rendered candidate from {_esc(card["model_id"])}">'
                )
            else:
                stage = card.get("failure_stage") or "generation"
                media = f'<div class="no-render">no render<br><small>{_esc(stage)}</small></div>'
            cards.append(
                '<figure class="card">'
                f"{media}"
                f'<figcaption><span class="mono">{_esc(card["model_id"])}</span>'
                f"{_chip(card)}</figcaption></figure>"
            )
        sections.append(
            f'<h3>{_esc(cell["instrument_id"])} · seed {cell["seed"]} · rep {cell["rep"]}</h3>'
            f'<div class="cards">{"".join(cards)}</div>'
        )
    return "".join(sections)


def build_report_html(run_dir: Path) -> str:
    """Render the full self-contained report for one arena run directory."""

    run_dir = Path(run_dir)
    payloads = load_run_payloads(run_dir)
    config = payloads["run_log"].get("config") or {}
    matrix = (
        f'{len(config.get("instrument_ids") or [])} instruments × '
        f'{len(config.get("seeds") or [])} seeds × '
        f'{config.get("reps", 1)} reps × '
        f'{len(config.get("model_ids") or [])} models'
    )
    voters = payloads["elo"].get("voters", 0)
    caveat = (
        "Single-voter Elo is directional — it measures one voter's blind "
        "preference under this protocol, not a population claim."
        if voters <= 1
        else "Subjective Elo and objective pass-rate are separate scorelines by design."
    )
    triangulation = payloads["agreement"].get("matrix")
    triangulation_section = (
        f"""
  <h2>Triangulated agreement</h2>
  <p class="sub">Three independent scorelines, pairwise Spearman rank correlation.
  A third judge that agrees with neither human nor mesh gate is still evidence,
  not noise (#598).</p>
  {_triangulation_matrix(triangulation)}
"""
        if triangulation
        else ""
    )

    return f"""<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow, noai, noimageai">
<title>Code-CAD Arena report — {_esc(run_dir.name)}</title>
<style>
:root {{
  --bg:#ffffff; --bg-soft:#f6f7f9; --bg-elev:#ffffff; --border:#e3e6ea;
  --border-strong:#cdd2d9; --text:#15181d; --text-soft:#5b626d;
  --text-faint:#9aa1ac; --accent:#d6562b; --accent-soft:#fbe9e2;
  --good:#2f8f5b; --mid:#c79100; --bad:#c4453a; --bar:#d6562b;
  --radius:12px; --maxw:1080px;
  --font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
}}
[data-theme="dark"] {{
  --bg:#0e1116; --bg-soft:#151a21; --bg-elev:#171d25; --border:#262d37;
  --border-strong:#333c48; --text:#e8ecf1; --text-soft:#9aa4b2;
  --text-faint:#69727f; --accent:#f0794f; --accent-soft:#2a1c16;
  --good:#4fb37e; --mid:#d9b13e; --bad:#e0695e; --bar:#e2653a;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:var(--font); background:var(--bg); color:var(--text); }}
main {{ max-width:var(--maxw); margin:0 auto; padding:32px 20px 64px; }}
h1 {{ font-size:1.6rem; margin:0 0 4px; }}
h2 {{ font-size:1.15rem; margin:36px 0 12px; }}
h3 {{ font-size:0.95rem; margin:24px 0 8px; color:var(--text-soft); }}
.sub {{ color:var(--text-soft); margin:0 0 16px; }}
.mono {{ font-family:var(--mono); font-size:0.85em; }}
.banner {{ background:var(--accent-soft); border:1px solid var(--border);
  border-radius:var(--radius); padding:10px 14px; color:var(--text-soft); font-size:0.9rem; }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(110px,1fr));
  gap:10px; margin:20px 0; }}
.tile {{ background:var(--bg-soft); border:1px solid var(--border);
  border-radius:var(--radius); padding:12px 14px; }}
.tile-value {{ font-size:1.35rem; font-weight:700; font-variant-numeric:tabular-nums; }}
.tile-label {{ color:var(--text-soft); font-size:0.8rem; margin-top:2px; }}
table {{ border-collapse:collapse; width:100%; font-size:0.9rem; }}
th,td {{ text-align:left; padding:7px 10px; border-bottom:1px solid var(--border); }}
th {{ color:var(--text-soft); font-weight:600; font-size:0.8rem; }}
td.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.bars {{ margin:14px 0; }}
.bar-row {{ display:grid; grid-template-columns:220px 1fr 52px; align-items:center;
  gap:10px; margin-bottom:2px; min-height:26px; }}
.bar-name {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.bar-track {{ background:var(--bg-soft); border-radius:4px; height:20px; overflow:hidden; }}
.bar-fill {{ display:block; height:100%; background:var(--bar); border-radius:0 4px 4px 0; }}
.bar-value {{ text-align:right; font-variant-numeric:tabular-nums; font-size:0.85rem; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:12px; }}
.card {{ margin:0; border:1px solid var(--border); border-radius:var(--radius);
  overflow:hidden; background:var(--bg-elev); }}
.card img, .no-render {{ display:block; width:100%; aspect-ratio:4/3;
  object-fit:contain; background:var(--bg-soft); }}
.no-render {{ display:flex; flex-direction:column; align-items:center;
  justify-content:center; color:var(--text-faint); font-size:0.85rem; }}
figcaption {{ display:flex; justify-content:space-between; align-items:center;
  gap:6px; padding:8px 10px; font-size:0.8rem; }}
.chip {{ border-radius:999px; padding:2px 9px; font-size:0.75rem; font-weight:600;
  white-space:nowrap; color:var(--bg); }}
.chip-good {{ background:var(--good); }}
.chip-mid {{ background:var(--mid); }}
.chip-bad {{ background:var(--bad); }}
footer {{ margin-top:48px; color:var(--text-faint); font-size:0.8rem; }}
.theme-btn {{ float:right; border:1px solid var(--border); background:var(--bg-soft);
  color:var(--text); border-radius:8px; padding:4px 10px; cursor:pointer; }}
</style>
</head>
<body>
<main>
  <button class="theme-btn" onclick="var h=document.documentElement;h.setAttribute('data-theme',h.getAttribute('data-theme')==='dark'?'light':'dark')" aria-label="Toggle theme">◐</button>
  <h1>Code-CAD Arena — {_esc(run_dir.name)}</h1>
  <p class="sub">{_esc(matrix)} · schema {_esc(SCHEMA)}</p>
  <p class="banner">{_esc(caveat)} Local report only — publication to the
  leaderboard site is gated on multi-voter Elo (#591).</p>

  {_stat_tiles(payloads)}

  <h2>Dual scoreline</h2>
  <p class="sub">Subjective blind-vote Elo vs objective render/DFM pass-rate —
  kept separate, never blended. Disagreement is evidence, not a defect.</p>
  {_dual_scoreline_table(payloads["agreement"])}
  {triangulation_section}
  <h2>Objective pass-rate</h2>
  {_objective_bars(payloads["scoreline"])}

  <h2>Gate sub-scores (mean per entrant)</h2>
  {_gate_matrix(gate_means(payloads["run_log"]))}

  <h2>Candidates</h2>
  {_gallery(gallery_cells(payloads["run_log"], run_dir))}

  <footer>Generated from run_log.json + vote files in this directory.
  All candidate artifacts stay local (never committed); errored trials are
  shown and count as 0.0 — honest failure reporting.</footer>
</main>
</body>
</html>
"""


def write_report(run_dir: Path) -> Path:
    out_path = Path(run_dir) / "report.html"
    out_path.write_text(build_report_html(run_dir), encoding="utf-8")
    return out_path
