"""HTTP side of the bridge: auth, model mapping, tool-envelope retry, JSON and SSE responses."""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Optional

from .backends import BackendError, BackendTimeout, ClaudeBackend, CliResult, CodexBackend
from .prompt import EnvelopeError, RequestError, build_prompt, parse_envelope

log = logging.getLogger("cadam_cli_bridge")  # never given prompt text, only method/path/model/status/timing

KEY_ENV = "CADAM_CLI_BRIDGE_KEY"
PING_EVERY_S = 10.0


@dataclass(frozen=True)
class ModelRoute:
    runtime: str  # "claude" | "codex"
    cli_model: str


@dataclass
class BridgeConfig:
    models: dict[str, ModelRoute]
    host: str = "127.0.0.1"
    port: int = 8787
    timeout_s: float = 600.0
    max_concurrent: int = 2
    max_body_bytes: int = 32 * 1024 * 1024
    claude_bin: str = "claude"
    codex_bin: str = "codex"
    api_key: str = field(default="", repr=False)


def load_config(path: Path, *, environ: Optional[Mapping[str, str]] = None) -> BridgeConfig:
    env = os.environ if environ is None else environ
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    models = {
        str(name): ModelRoute(str(spec["runtime"]), str(spec["cli_model"]))
        for name, spec in (raw.get("models") or {}).items()
    }
    if not models:
        raise ValueError("the config maps no models")
    for name, route in models.items():
        if route.runtime not in {"claude", "codex"}:
            raise ValueError(f"model {name}: runtime must be claude or codex")
    key = env.get(str(raw.get("key_env") or KEY_ENV), "")
    if len(key) < 16:
        raise ValueError(f"set ${raw.get('key_env') or KEY_ENV} to a shared key of at least 16 characters")
    return BridgeConfig(
        models=models, host=str(raw.get("host", "127.0.0.1")), port=int(raw.get("port", 8787)),
        timeout_s=float(raw.get("timeout_s", 600)), max_concurrent=int(raw.get("max_concurrent", 2)),
        max_body_bytes=int(raw.get("max_body_bytes", 32 * 1024 * 1024)),
        claude_bin=str(raw.get("claude_bin", "claude")), codex_bin=str(raw.get("codex_bin", "codex")), api_key=key,
    )


class ApiError(Exception):
    def __init__(self, status: int, kind: str, message: str) -> None:
        super().__init__(message)
        self.status, self.kind, self.message = status, kind, message


def _error_body(kind: str, message: str) -> dict:
    return {"type": "error", "error": {"type": kind, "message": message}}


def _sse(event: str, data: Mapping[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode("utf-8")


def _chunks(text: str, size: int = 400) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, config: BridgeConfig, backends: Optional[Mapping[str, Any]] = None) -> None:
        if not ipaddress.ip_address(config.host).is_loopback:
            raise ValueError("the bridge binds loopback addresses only")
        self.config = config
        self.backends = dict(backends) if backends else {
            "claude": ClaudeBackend(config.claude_bin), "codex": CodexBackend(config.codex_bin),
        }
        self.slots = threading.BoundedSemaphore(max(1, config.max_concurrent))
        super().__init__((config.host, config.port), _Handler)

    # ---- the model call ----------------------------------------------------------------
    def complete(self, body: Mapping[str, Any]) -> tuple[list[dict], CliResult, str]:
        model = body.get("model")
        route = self.config.models.get(str(model))
        if route is None:
            raise ApiError(404, "not_found_error", f"model {model} is not mapped by the bridge")
        try:
            prompt = build_prompt(body)
        except RequestError as exc:
            raise ApiError(400, "invalid_request_error", str(exc)) from None
        backend = self.backends[route.runtime]
        tools = body.get("tools") or []
        transcript = prompt.transcript
        last_problem = ""
        with self.slots:
            for attempt in range(2 if prompt.tools_required else 1):
                if attempt:
                    transcript += (
                        f"\n\nYour previous answer was rejected: {last_problem}. "
                        "Reply again with ONLY the single JSON object described above."
                    )
                try:
                    result = backend.run(model=route.cli_model, system=prompt.system, transcript=transcript,
                                         images=prompt.images, timeout=self.config.timeout_s)
                except BackendTimeout as exc:
                    raise ApiError(504, "timeout_error", str(exc)) from None
                except BackendError as exc:
                    raise ApiError(502, "api_error", str(exc)) from None
                if not prompt.tools_required:
                    return [{"type": "text", "text": result.text}], result, str(model)
                try:
                    return parse_envelope(result.text, tools, body.get("tool_choice")), result, str(model)
                except EnvelopeError as exc:
                    last_problem = str(exc)
        raise ApiError(502, "api_error", f"the model did not return a valid tool-call envelope ({last_problem})")


class _Handler(BaseHTTPRequestHandler):
    server: BridgeServer
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: object) -> None:  # replaced by explicit, prompt-free logging
        return

    # ---- plumbing ----------------------------------------------------------------------
    def _send_json(self, status: int, payload: Mapping[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        supplied = self.headers.get("x-api-key") or ""
        bearer = self.headers.get("Authorization") or ""
        if bearer.lower().startswith("bearer "):
            supplied = supplied or bearer[7:].strip()
        return hmac.compare_digest(supplied.encode("utf-8"), self.server.config.api_key.encode("utf-8"))

    def _route(self) -> str:
        return self.path.split("?", 1)[0].rstrip("/")

    def do_GET(self) -> None:  # noqa: N802
        if self._route() == "/health":
            self._send_json(200, {"status": "ok", "models": sorted(self.server.config.models)})
        else:
            self._send_json(404, _error_body("not_found_error", "not found"))

    def do_POST(self) -> None:  # noqa: N802
        started, status, model = time.monotonic(), 200, "-"
        try:
            if self._route() not in {"/v1/messages", "/messages"}:
                raise ApiError(404, "not_found_error", "not found")
            if not self._authorized():
                raise ApiError(401, "authentication_error", "invalid or missing bridge key")
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > self.server.config.max_body_bytes:
                raise ApiError(413 if length > 0 else 400, "invalid_request_error", "request body missing or too large")
            try:
                body = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                raise ApiError(400, "invalid_request_error", "body is not valid JSON") from None
            if not isinstance(body, dict):
                raise ApiError(400, "invalid_request_error", "body must be a JSON object")
            model = str(body.get("model"))
            if body.get("stream"):
                status = self._stream(body)
            else:
                blocks, result, name = self.server.complete(body)
                self._send_json(200, self._message(blocks, result, name))
        except ApiError as exc:
            status = exc.status
            self._send_json(exc.status, _error_body(exc.kind, exc.message))
        except (BrokenPipeError, ConnectionResetError):
            status = 499
        log.info("%s %s model=%s status=%s %.1fs", self.command, self._route(), model, status, time.monotonic() - started)

    # ---- responses ---------------------------------------------------------------------
    @staticmethod
    def _message(blocks: list[dict], result: CliResult, model: str) -> dict:
        used = any(b["type"] == "tool_use" for b in blocks)
        return {
            "id": f"msg_bridge_{uuid.uuid4().hex[:20]}", "type": "message", "role": "assistant", "model": model,
            "content": blocks, "stop_reason": "tool_use" if used else "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens},
        }

    def _stream(self, body: Mapping[str, Any]) -> int:
        """SSE: headers and ``message_start`` go out at once (with pings while the CLI runs), then the
        validated blocks are replayed as content_block_* events. Errors after the headers are SSE errors."""
        message_id = f"msg_bridge_{uuid.uuid4().hex[:20]}"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        write = self.wfile.write
        write(_sse("message_start", {"type": "message_start", "message": {
            "id": message_id, "type": "message", "role": "assistant", "model": str(body.get("model")),
            "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}}))
        self.wfile.flush()
        outcome: dict[str, Any] = {}

        def work() -> None:
            try:
                outcome["value"] = self.server.complete(body)
            except ApiError as exc:
                outcome["error"] = exc

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        while worker.is_alive():
            worker.join(PING_EVERY_S)
            if worker.is_alive():
                write(_sse("ping", {"type": "ping"}))
                self.wfile.flush()
        if "error" in outcome:
            err = outcome["error"]
            write(_sse("error", _error_body(err.kind, err.message)))
            return err.status
        blocks, result, _ = outcome["value"]
        for index, block in enumerate(blocks):
            if block["type"] == "text":
                write(_sse("content_block_start", {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}}))
                for piece in _chunks(block["text"]):
                    write(_sse("content_block_delta", {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": piece}}))
            else:
                write(_sse("content_block_start", {"type": "content_block_start", "index": index, "content_block": {
                    "type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}}))
                write(_sse("content_block_delta", {"type": "content_block_delta", "index": index, "delta": {
                    "type": "input_json_delta", "partial_json": json.dumps(block["input"])}}))
            write(_sse("content_block_stop", {"type": "content_block_stop", "index": index}))
        used = any(b["type"] == "tool_use" for b in blocks)
        write(_sse("message_delta", {"type": "message_delta", "delta": {
            "stop_reason": "tool_use" if used else "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": result.output_tokens}}))
        write(_sse("message_stop", {"type": "message_stop"}))
        self.wfile.flush()
        return 200


def make_server(config: BridgeConfig, backends: Optional[Mapping[str, Any]] = None) -> BridgeServer:
    return BridgeServer(config, backends)
