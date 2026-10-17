"""Read-only public showcase: remote renders and a small projection of checks."""
from __future__ import annotations

from html import escape
import json
import math
import re
from urllib.request import HTTPRedirectHandler, Request, build_opener

TAB_ID = "explore"
TITLE = "Explore designs"
# Already merged, regraded public data. Updating this pin requires staging again.
SOURCE_COMMIT = "7059cb7f8666c9c86876082bcf5c44c40a0f458a"
RAW = f"https://raw.githubusercontent.com/tonykoop/makerbench-hwe/{SOURCE_COMMIT}/"
BLOB = f"https://github.com/tonykoop/makerbench-hwe/blob/{SOURCE_COMMIT}/"
SNAPSHOT = "makerbench/arena_studio/data/showcase.json"
DJEMBE = "docs/showcase/djembe/assets/objective_scoreline.json"
DJEMBE_IMAGE = "docs/showcase/djembe/assets/sonnet-5.5-image-tier-seed0.png"
CASES = {"post3-models", "post3-backends", "strings-gallery", "kora"}
CHECKS = (("renders", "Compile / render"), ("watertight", "Watertight"),
          ("nonzero_volume", "Nonzero volume"), ("fits_envelope", "Fits envelope"),
          ("body_count", "Body count"))
# Same render-only subset as build_studio_demo_space.PUBLIC_PNG; no reference photos.
PUBLIC_RENDER = re.compile(
    r"docs/showcase/(?:post3/matchup-model/scored/(?:opus-5\.5|sonnet-5\.5)-seed[012]|"
    r"post3/matchup-backend/after/scored/(?:openscad|cadquery|build123d)-seed[012]|"
    r"post3/matchup-model/(?:opus-5\.5|sonnet-5\.5)-seed0|"
    r"post3/matchup-backend/after/(?:openscad|cadquery|build123d)-seed0|"
    r"strings/gallery/(?:img/design-[0-9]{3}|grid)|kora/assets/(?:blind|image)-seed[012])\.png"
)
EMPTY = '<p role="status">Load the public showcase to browse renders and compare designs.</p>'
OFFLINE = '<p role="status">Showcase unavailable. Check your connection and retry Load showcase.</p>'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_json(path):
    """Only two fixed public JSON documents; no user-selected URLs or redirects."""
    if path not in {SNAPSHOT, DJEMBE}:
        raise ValueError("Unknown showcase document")
    request = Request(RAW + path, headers={"User-Agent": "MakerBench-public-showcase"})
    with build_opener(NoRedirect()).open(request, timeout=8) as response:
        data = response.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError("Showcase document too large")
    content = json.loads(data.decode("utf-8"))
    if not isinstance(content, dict):
        raise ValueError("Unknown showcase document")
    return content


def checks(gates):
    if not isinstance(gates, dict):
        raise ValueError("Unknown check format")
    result = {}
    for key, _ in CHECKS:
        value = gates.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1:
            result[key] = value
    return result


def public_image(image, assets):
    if image is None:
        return None
    if not isinstance(image, str) or not re.fullmatch(r"/api/demo/assets/[a-f0-9]{20}\.png", image):
        raise ValueError("Unknown showcase render")
    relative = assets.get(image.removeprefix('/api/demo/assets/'))
    if not isinstance(relative, str) or not PUBLIC_RENDER.fullmatch(relative):
        raise ValueError("Render outside the licensed public subset")
    return RAW + relative


def project_snapshot(data):
    if data.get('schema') != 'makerbench-studio-showcase-v1' or data.get('demo') is not True:
        raise ValueError("Unknown showcase format")
    result = []
    if {case['id'] for case in data['cases']} != CASES:
        raise ValueError("Unknown showcase groups")
    for case in data['cases']:
        source = case['source']
        if not re.fullmatch(r'docs/showcase/[a-zA-Z0-9_/-]+\.md', source):
            raise ValueError("Unknown public study")
        rows = []
        for row in case['rows']:
            trials = row.get('trials') or [row]
            if any(trial.get('seed') not in (None, 0, 1, 2) for trial in trials):
                raise ValueError('Unexpected run number')
            rows.append({'label': row['label'], 'trials': [
                {'image': public_image(trial.get('image'), data['assets']),
                 'checks': checks(trial.get('gates', {})), 'seed': trial.get('seed')}
                for trial in trials]})
        result.append({'id': case['id'], 'title': case['title'], 'source': BLOB + source, 'rows': rows})
    return result


def project_djembe(data):
    row = data['rows'][0]
    # Use the same single-trial consistency control as build_studio_demo_data.score.
    # If the aggregate no longer describes those six checks, leave them unknown.
    known = {key for key, _ in CHECKS} | {'min_wall'}
    failed = {item['check'] for item in row.get('failed_checks', [])}
    rate = row.get('objective_pass_rate')
    gates = {}
    if (row.get('n_objective_trials') == 1 and failed <= known
            and isinstance(rate, (int, float)) and abs(rate - (6 - len(failed)) / 6) < 1e-6):
        gates = {key: float(key not in failed) for key, _ in CHECKS}
    return {'id': 'djembe', 'title': 'Stave-built djembe',
            'source': BLOB + 'docs/showcase/djembe/CASE_STUDY.md',
            'rows': [{'label': 'Claude Sonnet 5.5 — run 0', 'trials': [
                {'image': RAW + DJEMBE_IMAGE, 'checks': gates, 'seed': 0}]}]}


def gallery_html(cases, case_id, left=0, right=1):
    if not cases:
        return EMPTY
    case = next(item for item in cases if item['id'] == case_id)
    rows = case['rows']
    selected = list(dict.fromkeys([int(left), int(right)]))
    cards = []
    for index in selected:
        row = rows[index]
        pictures, results = [], []
        for trial in row['trials']:
            label = 'Recorded design' if trial['seed'] is None else f"Run {trial['seed']}"
            image = trial['image']
            pictures.append((f'<figure><img src="{escape(image, quote=True)}" alt="{escape(row["label"], quote=True)} — {label}" '
                             'loading="lazy" style="width:100%;max-height:350px;object-fit:contain">'
                             f'<figcaption>{label}</figcaption></figure>') if image else '<p>Render not published.</p>')
            values = []
            for key, title in CHECKS:
                value = trial['checks'].get(key)
                status = 'Not recorded' if value is None else ('Pass' if value == 1 else 'Fail' if value == 0 else f'{value:.0%} of runs')
                values.append(f'<li>{title}: {status}</li>')
            results.append(f'<p>{label}</p><ul>{"".join(values)}</ul>')
        cards.append(f'<article style="flex:1 1 300px;min-width:0"><h3>{escape(row["label"])}</h3>'
                     f'{"".join(pictures)}<details><summary>Objective checks</summary>{"".join(results)}</details></article>')
    return (f'<h2>{escape(case["title"])}</h2><div style="display:flex;flex-wrap:wrap;gap:24px">'
            f'{"".join(cards)}</div><p><a href="{escape(case["source"], quote=True)}" target="_blank" rel="noopener">Read the public study</a></p>'
            '<p>Published renders and recorded checks only; these do not establish sound or build readiness. '
            'If an image is unavailable, open the study or retry later.</p>')


def render():
    import gradio as gr

    gr.Markdown('### Explore designs\nBrowse public renders, then open objective checks. Compare two designs from the same study. Read-only.')
    state = gr.State([])
    load = gr.Button('Load showcase', variant='primary')
    study = gr.Dropdown(label='Study', choices=[], interactive=True)
    with gr.Row():
        left = gr.Dropdown(label='Left design', choices=[], interactive=True)
        right = gr.Dropdown(label='Right design', choices=[], interactive=True)
    view = gr.HTML(EMPTY)

    def choose(cases, case_id):
        case = next(item for item in cases if item['id'] == case_id)
        options = [(row['label'], index) for index, row in enumerate(case['rows'])]
        other = min(1, len(options) - 1)
        return gr.update(choices=options, value=0), gr.update(choices=options, value=other), gallery_html(cases, case_id, 0, other)

    def load_cases():
        try:
            cases = project_snapshot(fetch_json(SNAPSHOT))
            cases.append(project_djembe(fetch_json(DJEMBE)))
            a, b, html = choose(cases, cases[0]['id'])
            return cases, gr.update(choices=[(c['title'], c['id']) for c in cases], value=cases[0]['id']), a, b, html
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
            return [], gr.update(choices=[], value=None), gr.update(choices=[], value=None), gr.update(choices=[], value=None), OFFLINE

    load.click(load_cases, outputs=[state, study, left, right, view], concurrency_id='public-showcase', concurrency_limit=1, api_name=False)
    study.input(choose, inputs=[state, study], outputs=[left, right, view], api_name=False)
    for selector in (left, right):
        selector.input(gallery_html, inputs=[state, study, left, right], outputs=view, api_name=False)
