"""Public matchup provenance and objective-only static publication (#841)."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('site_matchups', ROOT / 'site/build_data.py')
b = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(b)


def bundle():
    return {'schema': 'makerbench-frontier-arena-replay-v1', 'verification_status': 'unverified', 'matchups': [{
        'id': 'ocarina-seed0', 'matchup': {'varied_axis': 'models', 'values': ['stub-a', 'stub-b'],
        'varied_axes': ['models'], 'factorial': False,
        'held': {'instruments': 'ocarina', 'seeds': 0, 'backends': 'openscad', 'context_tiers': 'blind', 'levels': 'L1'}},
        'entrants': [
            {'entrant': 'stub-a', 'backend': 'openscad', 'objective_pass_rate': 5/6, 'n_objective_trials': 1,
             'n_infra_errors': 0, 'failed_checks': {'min_wall': 1}, 'elos': 1500, 'raw_error': '/home/test/private'},
            {'entrant': 'stub-b', 'backend': 'openscad', 'objective_pass_rate': None, 'n_objective_trials': 0,
             'n_infra_errors': 1, 'failed_checks': {}},
        ]}]}


def test_whitelist_and_static_provenance_with_infrastructure_unknown(tmp_path):
    source = tmp_path / 'sample.json'
    source.write_text(json.dumps(bundle()))
    page = b.build_matchups_page(tmp_path)
    text = json.dumps(page)
    assert 'elos' not in text and '/home/test' not in text
    card = b._prerender_matchups_html(page)
    assert 'models' in card and 'ocarina' in card and 'openscad' in card
    assert 'min_wall (1)' in card
    assert '83.33%' in card
    assert 'Unmeasured' in card and 'infrastructure' in card
    assert 'stub-a' in card and 'stub-b' in card
    assert 'unverified' in card


@pytest.mark.parametrize('change', [
    {'varied_axis': 'unknown'},
    {'held': {'models': 'stub-a'}},
    {'values': ['stub-a']},
    {'varied_axes': ['models', 'seeds']},
])
def test_invalid_experiment_claim_is_rejected(tmp_path, change):
    sample = bundle()
    sample['matchups'][0]['matchup'].update(change)
    (tmp_path / 'sample.json').write_text(json.dumps(sample))
    with pytest.raises(ValueError):
        b.build_matchups_page(tmp_path)


def test_empty_section_does_not_invent_measurements(tmp_path):
    data = b.build_matchups_page(tmp_path)
    assert data['matchups'] == []
    assert 'No published matchups yet' in b._prerender_matchups_html(data)


def test_committed_section_is_visible_without_javascript():
    page = (ROOT / 'site/index.html').read_text()
    assert '<section id="matchups">' in page
    assert '<a href="#matchups">Matchups</a>' in page
    assert '<!-- prerender:matchups -->' in page
    data = json.loads((ROOT / 'site/data/matchups.json').read_text())
    assert b._prerender_matchups_html(data) in page


@pytest.mark.parametrize('change', [
    {'backend': 'cadquery'},
    {'entrant': 'stub-c'},
    {'objective_pass_rate': True},
    {'failed_checks': {}},
    {'n_objective_trials': 0},
])
def test_row_cannot_misstate_held_backend_or_measurements(tmp_path, change):
    sample = bundle()
    sample['matchups'][0]['entrants'][0].update(change)
    (tmp_path / 'sample.json').write_text(json.dumps(sample))
    with pytest.raises(ValueError):
        b.build_matchups_page(tmp_path)
