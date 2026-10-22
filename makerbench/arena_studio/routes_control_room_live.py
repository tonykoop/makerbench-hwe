"""Live routes; mutations use Studio's existing same-origin middleware."""
from fastapi import HTTPException
from fastapi.responses import FileResponse, Response

from . import control_room_live as live


def register_live_routes(app, service, *, runs_root, queue_path, writes=False, opener=None):
    store = live.LiveStore(runs_root, queue_path, service.repo_root)

    def view():
        try:
            return store.view()
        except (OSError, ValueError, TypeError, KeyError):
            raise HTTPException(409, 'Live metadata is unreadable or unsafe') from None

    def lane(run_id, entrant):
        snapshot = view()
        if not snapshot['job'] or snapshot['job']['run_id'] != run_id:
            raise HTTPException(409, 'The active run changed; refresh Live')
        item = next((lane for lane in snapshot['lanes'] if lane['id'] == entrant), None)
        if item is None:
            raise HTTPException(404, 'Unknown entrant')
        return store.folder(run_id), item

    @app.get('/api/live')
    def get_live():
        return {**view(), 'writes': writes}

    @app.post('/api/live/{run_id}/stop')
    def stop(run_id: str):
        if not writes:
            raise HTTPException(403, 'Start Studio with --control-room-queue-writes to request a stop')
        try:
            return store.stop(run_id)
        except (ValueError, OSError):
            raise HTTPException(409, 'The run is no longer running or its path is unsafe') from None

    @app.get('/api/live/{run_id}/transcript/{entrant}')
    def transcript(run_id: str, entrant: str):
        run, item = lane(run_id, entrant)
        rows = live.live_status_rows(live.read(run / 'run_log.json'))
        row = next((r for r in rows if r.get('model_id') == item['id']), {})
        return {'text': store.transcript(run, entrant, row)}

    def artifact(run_id, entrant, native=False):
        run, item = lane(run_id, entrant)
        rows = live.live_status_rows(live.read(run / 'run_log.json'))
        row = next((r for r in rows if r.get('model_id') == item['id']), {})
        result = row.get('result') or {}
        path = store.artifact(run, result.get('native_path') if native else item.get('preview_path'))
        if not path or (native and item['state'] != 'scored'):
            raise HTTPException(404, 'Artifact not available')
        return path

    @app.get('/api/live/{run_id}/render/{entrant}')
    def render(run_id: str, entrant: str):
        path = artifact(run_id, entrant)
        with path.open('rb') as stream:
            if stream.read(8) != b'\x89PNG\r\n\x1a\n':
                raise HTTPException(404, 'PNG preview not available')
        return FileResponse(path, media_type='image/png', headers={'Cache-Control': 'no-store'})

    @app.post('/api/live/{run_id}/open/{entrant}')
    def open_native(run_id: str, entrant: str):
        path = artifact(run_id, entrant, native=True)
        if path.suffix.lower() not in live.cr.NATIVE_APPS:
            raise HTTPException(400, 'Unsupported native format')
        try:
            opener(path)
        except Exception:
            raise HTTPException(409, 'Native application could not open the file') from None
        return {'opened': path.name}

    @app.post('/api/live/{run_id}/capture/{entrant}')
    def capture(run_id: str, entrant: str):
        _, item = lane(run_id, entrant)
        if not item['live'] or item['state'] != 'running' or item['backend'] not in {'fusion-live', 'solidworks-live'}:
            raise HTTPException(409, 'Only an active live CAD entrant can capture a view')
        try:
            image = live.capture(item['backend'])
        except (OSError, ValueError, KeyError):
            raise HTTPException(409, 'Bridge capture unavailable; the build continues') from None
        return Response(image, media_type='image/png', headers={'Cache-Control': 'no-store'})
