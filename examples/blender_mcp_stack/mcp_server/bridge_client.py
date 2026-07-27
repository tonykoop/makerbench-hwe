"""bridge_client — a tiny newline-delimited JSON-RPC client for the Blender bridge.

Stdlib-only. Used by the MCP server and the sample task to drive the scene graph
running inside (headless) Blender. Each call is one request/response over a
persistent TCP connection.
"""

from __future__ import annotations

import json
import socket
import time
from typing import Any, Optional


class BridgeError(RuntimeError):
    """Raised when the Blender bridge returns a JSON-RPC error."""


class BridgeClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765, timeout: float = 30.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._buf = b""
        self._id = 0

    # -- connection --------------------------------------------------------- #
    def connect(self, retries: int = 30, delay: float = 1.0) -> "BridgeClient":
        """Connect, retrying while headless Blender finishes booting."""
        last: Optional[Exception] = None
        for _ in range(retries):
            try:
                self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
                self._sock.settimeout(self.timeout)
                return self
            except OSError as exc:  # bridge not up yet
                last = exc
                time.sleep(delay)
        raise BridgeError(f"could not connect to Blender bridge at {self.host}:{self.port}: {last}")

    def close(self) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None

    def __enter__(self) -> "BridgeClient":
        return self.connect()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- rpc ---------------------------------------------------------------- #
    def call(self, method: str, **params: Any) -> Any:
        if self._sock is None:
            raise BridgeError("not connected; call connect() first")
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        self._sock.sendall(json.dumps(req).encode() + b"\n")
        line = self._read_line()
        resp = json.loads(line)
        if "error" in resp and resp["error"]:
            raise BridgeError(resp["error"].get("message", str(resp["error"])))
        return resp.get("result")

    def _read_line(self) -> bytes:
        while b"\n" not in self._buf:
            chunk = self._sock.recv(65536)  # type: ignore[union-attr]
            if not chunk:
                raise BridgeError("bridge closed the connection")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line
