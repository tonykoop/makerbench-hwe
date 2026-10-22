import json
import urllib.error
from pathlib import Path

import pytest

from makerbench.cadam_adapter import CadamClient, CadamConfig, CadamRecoveryRequiredError, CadamStreamFailedError, stream_failure
from makerbench.nightly_preflight import openrouter_credit_check
from makerbench.nightly_cad import resolve_dispatch, nightly_lease
from datetime import datetime, timezone

STREAM = (Path(__file__).parent / 'fixtures/cadam/credit-error.sse').read_bytes()


@pytest.mark.parametrize('stream,failed', [(STREAM, True), (STREAM.replace(b'data: [DONE]', b''), False),
    (STREAM.replace(b'data: [DONE]', b'data: {"type":"text-delta","delta":"x"}\n\ndata: [DONE]'), False)])
def test_stream_and_adapter(tmp_path, stream, failed):
    assert bool(stream_failure(stream)) == failed
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url))
        return stream if url.endswith('/api/parametric-chat') else b'[]'

    client = CadamClient(CadamConfig(base_url='http://cadam.test', supabase_url='http://supabase.test',
        user_id='test', access_token='token', service_role_key='service'), transport=transport, sleep_fn=lambda _: None)
    with pytest.raises(CadamStreamFailedError if failed else CadamRecoveryRequiredError) as error:
        client.generate(prompt='fixture', output_dir=tmp_path)
    if failed:
        assert str(error.value) == stream_failure(STREAM)
    assert sum(url.endswith('/api/parametric-chat') for _, url in calls) == 1
    with pytest.raises(CadamStreamFailedError if failed else CadamRecoveryRequiredError):
        client.generate(prompt='fixture', output_dir=tmp_path, resume_only=True)
    assert sum(url.endswith('/api/parametric-chat') for _, url in calls) == 1


@pytest.mark.parametrize('remaining,ok', [(0, False), (-1, False), (2, True), (None, True)])
def test_credit_counters(remaining, ok):
    def transport(method, url, headers, body):
        assert method == 'GET' and headers == {'Authorization': 'Bearer secret'}
        return json.dumps({'data': {'limit_remaining': remaining, 'usage': 3, 'label': 'secret'}}).encode()
    result = openrouter_credit_check('secret', transport)
    assert result[0] is ok
    assert 'secret' not in result[1]


@pytest.mark.parametrize('error,ok', [(urllib.error.HTTPError('url', 401, 'secret', {}, None), False),
                                    (OSError('secret'), True)])
def test_credit_errors(error, ok):
    def transport(*args):
        raise error
    result = openrouter_credit_check('secret', transport)
    assert result[0] is ok and 'secret' not in result[1]


@pytest.mark.parametrize('blocker', [None, 'budget overrun', 'other dispatch'])
@pytest.mark.parametrize('outcome', ['lost', 'received'])
def test_resolution(tmp_path, blocker, outcome):
    root = tmp_path / 'runs'
    run = root / 'r1'
    run.mkdir(parents=True)
    queue = tmp_path / 'queue.json'
    queue.write_text(json.dumps({'schema': 'makerbench-nightly-cad-queue-v1', 'jobs': [
        {'job_id': 'j', 'run_id': 'r1', 'instrument_id': 'test', 'status': 'blocked', 'entrants': []}]}))
    rows = [{'entrant_id': 'a', 'status': 'paid-dispatch-uncertain', 'error': 'uncertain'}]
    if blocker == 'other dispatch':
        rows.append({'entrant_id': 'b', 'status': 'paid-dispatch-uncertain', 'error': blocker})
    state = {'outcomes': rows, 'budget': {'halted_reason': blocker if blocker == 'budget overrun' else None,
             'reservations': {'a': 3}, 'dispatches': {'a': {'status': 'dispatched'}}}}
    (run / 'nightly-state.json').write_text(json.dumps(state))
    (run / 'morning-summary.json').write_text(json.dumps({'safety_block': 'uncertain', 'votable': False,
                                                        'valid_candidate_count': 2, 'pair_files': ['p.html']}))
    args = dict(run_id='r1', entrant_id='a', outcome=outcome, runs_root=root, queue_path=queue)
    with nightly_lease(root / '.nightly-cad.lock', now=lambda: datetime.now(timezone.utc)):
        with pytest.raises(RuntimeError, match='another nightly'):
            resolve_dispatch(**args)
    result = resolve_dispatch(**args)
    assert result['outcomes'][0]['resolution']['outcome'] == outcome
    assert result['budget']['reservations'] == {'a': 3}
    assert result['morning']['votable'] is (blocker is None)
    assert result['morning'].get('safety_block') == blocker
    assert json.loads((run / 'nightly-state.json').read_text()) == result
    with pytest.raises(ValueError, match='no unresolved'):
        resolve_dispatch(**args)


def test_never_purchased_credits_is_no_go():
    assert openrouter_credit_check('secret', lambda *args: b'{"data":{"limit_remaining":null,"usage":0,"is_free_tier":true}}')[0] is False
