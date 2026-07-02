"""Browser-native blind voting for the Code-CAD Arena (#602 follow-on).

``arena vote-web`` serves one loopback URL; the voter steps through blind
pairs entirely in the browser — the Left/Draw/Right buttons on the vote page
POST back to this server, which appends the same ``votes.blind.jsonl`` /
``votes.revealed.jsonl`` records the terminal flow writes. No terminal
round-trips (Round 2 voter feedback).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from .code_cad_vote_surface import (
    BlindPair,
    append_vote_record,
    record_vote,
    render_vote_surface,
    reveal_vote,
)


VOTE_JS = """
<script>
function cast(winner) {
  fetch('/vote', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({pair_id: document.querySelector('main').dataset.pairId, winner: winner})
  }).then(r => r.json()).then(() => { window.location = '/queue'; });
}
document.querySelectorAll('button[data-vote]').forEach(b =>
  b.addEventListener('click', () => cast(b.dataset.vote)));
</script>
"""


@dataclass
class QueueItem:
    pair: BlindPair
    meta: dict  # instrument_id / seed / rep / round


@dataclass
class VoteQueue:
    """Ordered blind pairs plus the vote-append bookkeeping."""

    run_dir: Path
    voter: str
    items: list[QueueItem] = field(default_factory=list)
    voted_pair_ids: set = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def next_unvoted(self) -> Optional[QueueItem]:
        for item in self.items:
            if item.pair.pair_id not in self.voted_pair_ids:
                return item
        return None

    def progress(self) -> tuple[int, int]:
        done = sum(1 for item in self.items if item.pair.pair_id in self.voted_pair_ids)
        return done, len(self.items)

    def cast(self, pair_id: str, winner: str) -> bool:
        """Record one vote; returns False for unknown/duplicate pairs."""

        with self.lock:
            item = next(
                (i for i in self.items if i.pair.pair_id == pair_id), None
            )
            if item is None or pair_id in self.voted_pair_ids:
                return False
            vote = record_vote(item.pair, winner=winner, voter_id=self.voter)
            append_vote_record(self.run_dir / "votes.blind.jsonl", vote)
            revealed = reveal_vote(item.pair, vote)
            revealed.update(item.meta)
            append_vote_record(self.run_dir / "votes.revealed.jsonl", revealed)
            self.voted_pair_ids.add(pair_id)
            return True


def render_queue_page(queue: VoteQueue) -> str:
    """The next pair as a self-voting page, or the all-done summary."""

    item = queue.next_unvoted()
    done, total = queue.progress()
    if item is None:
        return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Arena voting complete</title></head>
<body style="font-family: system-ui; max-width: 640px; margin: 80px auto; text-align: center;">
<h1>All {total} pairs voted &#127881;</h1>
<p>Run <code>makerbench arena leaderboard</code> and <code>arena agreement</code>
for the scorelines, or <code>arena report</code> for the full page.</p>
</body></html>"""
    page = render_vote_surface(item.pair)
    banner = (
        f'<p style="text-align:center;font-family:system-ui;color:#555">'
        f"pair {done + 1} of {total} &middot; "
        f'{item.meta.get("instrument_id")} seed={item.meta.get("seed")} '
        f'round={item.meta.get("round")}</p>'
    )
    page = page.replace("<main", banner + "\n  <main", 1)
    return page.replace("</body>", VOTE_JS + "</body>")


class VoteRequestHandler(SimpleHTTPRequestHandler):
    """Static file server for run-dir assets + /queue and /vote endpoints."""

    queue: VoteQueue  # injected via partial

    def __init__(self, *args, queue: VoteQueue, **kwargs):
        self.queue = queue
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def _send_html(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - stdlib naming
        if self.path in ("/", "/queue"):
            self._send_html(render_queue_page(self.queue))
            return
        super().do_GET()

    def do_POST(self):  # noqa: N802 - stdlib naming
        if self.path != "/vote":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length))
            winner = str(payload.get("winner"))
            if winner not in {"left", "right", "draw"}:
                raise ValueError("winner must be left/right/draw")
            ok = self.queue.cast(str(payload.get("pair_id")), winner)
        except (ValueError, json.JSONDecodeError) as exc:
            body = json.dumps({"ok": False, "error": str(exc)}).encode()
            self.send_response(400)
        else:
            body = json.dumps({"ok": ok}).encode()
            self.send_response(200 if ok else 409)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve_vote_queue(
    queue: VoteQueue, port: int = 0
) -> tuple[ThreadingHTTPServer, int]:
    """Serve the queue on 127.0.0.1 (loopback only, like every arena server)."""

    handler = partial(
        VoteRequestHandler,
        queue=queue,
        directory=queue.run_dir.resolve().as_posix(),
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]
