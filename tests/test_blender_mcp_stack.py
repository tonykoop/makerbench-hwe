"""Tests for the Claude + Blender MCP reference stack (mb#93).

These run without Docker or a real Blender: a fake in-process JSON-RPC server
stands in for the headless-Blender bridge, exercising the BridgeClient wire
format, the bpy main-thread queue pattern, the logged BlenderSession, and the
sample task — and confirming the run emits a schema-valid WorkflowManifest that
preserves the L1 vent-cut steering disclosure.
"""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

STACK = Path(__file__).resolve().parents[1] / "examples" / "blender_mcp_stack"


# --------------------------------------------------------------------------- #
# A fake bridge: same newline-delimited JSON-RPC 2.0 wire shape as the Blender
# add-on, but the ops are pure-Python stand-ins (no bpy). Lets the whole client
# / session / task pipeline run in CI.
# --------------------------------------------------------------------------- #
class FakeBridge:
    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.objects: dict[str, dict] = {}
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._running = True

    def start(self) -> "FakeBridge":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._running = False
        self.sock.close()

    def _serve(self) -> None:
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        buf = b""
        with conn:
            while self._running:
                try:
                    chunk = conn.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        conn.sendall(self._handle(line) + b"\n")

    def _handle(self, line: bytes) -> bytes:
        req = json.loads(line)
        try:
            result = self._dispatch(req["method"], req.get("params") or {})
            return json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": result}).encode()
        except Exception as exc:
            return json.dumps(
                {"jsonrpc": "2.0", "id": req.get("id"), "error": {"message": str(exc)}}
            ).encode()

    def _dispatch(self, method: str, params: dict):
        if method == "clear_scene":
            self.objects.clear()
            return {"cleared": True, "object_count": 0}
        if method in ("add_cube", "add_cylinder"):
            name = params.get("name") or f"obj_{len(self.objects)}"
            self.objects[name] = {"polygons": 6 if method == "add_cube" else 64}
            return {"name": name, "polygons": self.objects[name]["polygons"]}
        if method == "set_transform":
            return {"name": params["name"]}
        if method == "boolean_difference":
            self.objects.pop(params["cutter"], None)
            return {"target": params["target"], "polygons": 24}
        if method == "get_scene_info":
            return {"object_count": len(self.objects), "objects": []}
        if method == "export_stl":
            Path(params["filepath"]).write_bytes(b"solid fake\nendsolid fake\n")
            return {"filepath": params["filepath"], "bytes": 26}
        raise ValueError(f"unknown method {method}")


@pytest.fixture()
def fake_bridge():
    bridge = FakeBridge().start()
    yield bridge
    bridge.stop()


@pytest.fixture(autouse=True)
def _stack_on_path():
    import sys
    for p in (str(STACK / "mcp_server"), str(STACK / "tasks")):
        if p not in sys.path:
            sys.path.insert(0, p)
    yield


def test_bridge_client_roundtrips(fake_bridge):
    from bridge_client import BridgeClient

    with BridgeClient("127.0.0.1", fake_bridge.port) as client:
        assert client.call("clear_scene")["object_count"] == 0
        added = client.call("add_cube", name="plate", size=1.0)
        assert added["name"] == "plate"


def test_blender_session_logs_and_emits_valid_manifest(fake_bridge, tmp_path):
    from server import BlenderSession

    out = tmp_path / "wm.json"
    with BlenderSession(task_id="vented_plate", seed=0, host="127.0.0.1",
                        port=fake_bridge.port) as session:
        session.call("clear_scene")
        session.call("add_cube", name="plate")
        session.call("boolean_difference", target="plate", cutter="plate", steering="L1")
        stl = tmp_path / "p.stl"
        session.call("export_stl", filepath=str(stl))
        manifest = session.emit_manifest(str(out))

    assert stl.exists()
    assert manifest["task_id"] == "vented_plate"
    assert manifest["stack"]["host_application"] == {"name": "blender", "version": None}
    assert manifest["hii"]["highest_level"] == "L1"
    assert manifest["hii"]["l1_nl_steering_events"] == 1
    assert str(stl) in manifest["artifacts"]
    # Round-trips through the authoritative schema when present, preserving HII.
    schema = pytest.importorskip("makerbench.schema")
    parsed = schema.WorkflowManifest.model_validate(manifest)
    assert parsed.hii.highest_level == "L1"
    assert parsed.hii.l1_nl_steering_events == 1


def test_sample_task_produces_gradeable_artifact(fake_bridge, tmp_path):
    import vented_plate_demo

    rc = vented_plate_demo.main([
        "--host", "127.0.0.1", "--port", str(fake_bridge.port),
        "--out", str(tmp_path), "--task-id", "vented_plate", "--seed", "0",
    ])
    assert rc == 0
    stl = tmp_path / "vented_plate.stl"
    manifest = tmp_path / "workflow_manifest.json"
    assert stl.exists() and stl.stat().st_size > 0
    data = json.loads(manifest.read_text())
    # The vent cut is the L1 design choice; it must be disclosed, not collapsed.
    assert data["hii"]["highest_level"] == "L1"
    assert data["metrics"]["tool_calls_count"] >= 10


def test_tool_registry_matches_bridge_methods():
    from server import TOOLS

    # Every advertised MCP tool maps to a bridge op the add-on implements.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "mb_blender_bridge", STACK / "blender_addon" / "mb_blender_bridge.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for tool in TOOLS:
        assert tool in mod._OPS, f"tool {tool} has no bridge op"
