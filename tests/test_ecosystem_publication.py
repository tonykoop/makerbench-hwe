"""Unknown or private ecosystem identities must never enter the public page."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ecosystem_public_builder', ROOT/'site/build_data.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
PUBLIC_URLS = {
    'https://github.com/tonykoop/makerbench-hwe',
    'https://huggingface.co/spaces/tonykoop/makerbench-hwe',
}


def sentinels():
    common = {'kind':'surface', 'role':'sentinel', 'blurb':'sentinel'}
    return [
        dict(common, id='private-sentinel', name='PRIVATE_SENTINEL', private=True,
             url='https://github.com/tonykoop/makerbench-hwe'),
        dict(common, id='unverified-sentinel', name='UNVERIFIED_SENTINEL', private=False,
             url='https://github.com/example/unverified-sentinel'),
        dict(common, id='unknown-visibility-sentinel', name='UNKNOWN_VISIBILITY_SENTINEL',
             url='https://huggingface.co/spaces/tonykoop/makerbench-hwe'),
    ]


def public_nodes():
    return [n for n in builder.ECOSYSTEM_NODES if n['url'] in PUBLIC_URLS and n['private'] is False]


def test_builder_refuses_private_and_unverified_editorial_nodes(monkeypatch):
    monkeypatch.setattr(builder, 'ECOSYSTEM_NODES', public_nodes()+sentinels())
    payload = builder.build_ecosystem([], [])
    serialized = json.dumps(payload)
    for node in sentinels():
        assert node['name'] not in serialized
    assert len(payload['nodes']) == 2


def test_production_prerender_refuses_stale_flags_and_missing_visibility():
    payload = json.loads((ROOT/'site/data/leaderboard.json').read_text())
    payload['ecosystem']['nodes'] = public_nodes()+sentinels()
    rendered = builder.prerender_blocks(payload)['ecosystem']
    for node in sentinels():
        assert node['name'] not in rendered
    assert 'https://github.com/example/unverified-sentinel' not in rendered
    assert 'makerbench-hwe' in rendered and 'HF Space' in rendered
