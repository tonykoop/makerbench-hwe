"""mb_blender_bridge — a JSON-RPC bridge that exposes a Blender scene graph.

This runs *inside* Blender (as a registered add-on in the GUI, or imported by a
``blender --background --python`` launcher). It listens on a local TCP socket and
speaks newline-delimited JSON-RPC 2.0. The MCP server (``mcp_server/server.py``,
which runs in its own process) connects to this socket and forwards Claude's
tool-calls here.

Why a socket bridge instead of calling ``bpy`` directly from the MCP server?
``bpy`` only exists inside the Blender Python interpreter — you cannot ``pip
install`` it into an arbitrary process. So the agent stack is:

    Claude  --MCP-->  mcp_server (any Python)  --TCP/JSON-RPC-->  this bridge (in Blender)  --bpy-->  scene

The bpy thread-safety pattern (mb#93)
-------------------------------------
Blender's data API (``bpy.data``, operators, mesh edits) is **not thread-safe**
and must only be touched from Blender's main thread. The socket server runs on a
background thread, so every request that mutates or reads scene data is *queued*
and executed on the main thread via ``bpy.app.timers.register``. The worker
thread then blocks on a per-request ``threading.Event`` until the main thread has
produced a result. This is the natural way to drive multi-step edits from an
external agent without crashing the scene graph — and exercising it cleanly is
itself a benchmark stressor (see issue #93).
"""

from __future__ import annotations

import json
import queue
import socket
import threading
import traceback
from typing import Any, Callable, Optional

try:  # available only inside Blender
    import bpy  # type: ignore
    import bmesh  # type: ignore
except Exception:  # pragma: no cover - import guard for tooling outside Blender
    bpy = None  # type: ignore
    bmesh = None  # type: ignore

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765

# --------------------------------------------------------------------------- #
# Main-thread task queue (the bpy thread-safety pattern)
# --------------------------------------------------------------------------- #
# Background socket threads never call bpy directly. They push a (callable,
# Event, result-box) tuple onto this queue; a bpy.app.timers callback drains it
# on the main thread, runs each callable, and signals the waiting thread.
_task_queue: "queue.Queue[tuple[Callable[[], Any], threading.Event, dict]]" = queue.Queue()


def _drain_main_thread_tasks() -> float:
    """Timer callback: run queued scene ops on Blender's main thread.

    Returns the seconds until the next poll (Blender re-registers the timer with
    this interval). A tight 0.02s poll keeps the agent loop responsive.
    """
    while True:
        try:
            fn, done, box = _task_queue.get_nowait()
        except queue.Empty:
            break
        try:
            box["result"] = fn()
        except Exception as exc:  # surface the failure back to the caller
            box["error"] = f"{type(exc).__name__}: {exc}"
            box["traceback"] = traceback.format_exc()
        finally:
            done.set()
    return 0.02


def run_on_main_thread(fn: Callable[[], Any], timeout: float = 30.0) -> Any:
    """Queue ``fn`` for the main thread and block until it returns (or raises).

    Called from a socket worker thread. This is the single chokepoint through
    which every bpy access flows, so the scene graph is only ever touched by one
    thread at a time.
    """
    if bpy is None:
        raise RuntimeError("bpy is unavailable; run this inside Blender")
    done = threading.Event()
    box: dict = {}
    _task_queue.put((fn, done, box))
    if not done.wait(timeout):
        raise TimeoutError(f"main-thread task did not complete within {timeout}s")
    if "error" in box:
        raise RuntimeError(box["error"] + "\n" + box.get("traceback", ""))
    return box["result"]


# --------------------------------------------------------------------------- #
# Scene operations (these BODIES run on the main thread only)
# --------------------------------------------------------------------------- #
def _op_ping(**_: Any) -> dict:
    return {"pong": True, "blender_version": list(bpy.app.version)}


def _op_get_scene_info(**_: Any) -> dict:
    """Introspect the scene graph: objects, transforms, poly counts."""
    scene = bpy.context.scene
    objects = []
    for obj in scene.objects:
        info = {
            "name": obj.name,
            "type": obj.type,
            "location": list(obj.location),
            "rotation_euler": list(obj.rotation_euler),
            "scale": list(obj.scale),
        }
        if obj.type == "MESH":
            mesh = obj.data
            info["vertices"] = len(mesh.vertices)
            info["edges"] = len(mesh.edges)
            info["polygons"] = len(mesh.polygons)
        objects.append(info)
    return {
        "scene": scene.name,
        "object_count": len(objects),
        "objects": objects,
        "frame_current": scene.frame_current,
    }


def _op_clear_scene(**_: Any) -> dict:
    """Delete every object — a clean slate for a procedural build."""
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    return {"cleared": True, "object_count": len(bpy.context.scene.objects)}


def _op_add_cube(size: float = 2.0, location=(0.0, 0.0, 0.0), name: Optional[str] = None) -> dict:
    bpy.ops.mesh.primitive_cube_add(size=size, location=tuple(location))
    obj = bpy.context.active_object
    if name:
        obj.name = name
    return {"name": obj.name, "polygons": len(obj.data.polygons)}


def _op_add_cylinder(
    radius: float = 1.0,
    depth: float = 2.0,
    vertices: int = 32,
    location=(0.0, 0.0, 0.0),
    name: Optional[str] = None,
) -> dict:
    bpy.ops.mesh.primitive_cylinder_add(
        radius=radius, depth=depth, vertices=vertices, location=tuple(location)
    )
    obj = bpy.context.active_object
    if name:
        obj.name = name
    return {"name": obj.name, "polygons": len(obj.data.polygons)}


def _op_boolean_difference(target: str, cutter: str) -> dict:
    """Subtract ``cutter`` from ``target`` (a classic CAD-ish mesh op)."""
    target_obj = bpy.data.objects[target]
    cutter_obj = bpy.data.objects[cutter]
    mod = target_obj.modifiers.new(name="mb_bool", type="BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.object = cutter_obj
    bpy.context.view_layer.objects.active = target_obj
    bpy.ops.object.modifier_apply(modifier=mod.name)
    cutter_obj.select_set(True)
    bpy.context.view_layer.objects.active = cutter_obj
    bpy.ops.object.delete()
    return {"target": target, "polygons": len(target_obj.data.polygons)}


def _op_set_transform(name: str, location=None, rotation_euler=None, scale=None) -> dict:
    obj = bpy.data.objects[name]
    if location is not None:
        obj.location = tuple(location)
    if rotation_euler is not None:
        obj.rotation_euler = tuple(rotation_euler)
    if scale is not None:
        obj.scale = tuple(scale)
    return {"name": name, "location": list(obj.location), "scale": list(obj.scale)}


def _op_export_stl(filepath: str, only_selected: bool = False) -> dict:
    """Export the scene (or selection) to STL — the gradeable artifact.

    Handles both the new Blender 4.x exporter (``bpy.ops.wm.stl_export``) and the
    legacy add-on (``bpy.ops.export_mesh.stl``).
    """
    if hasattr(bpy.ops.wm, "stl_export"):
        bpy.ops.wm.stl_export(filepath=filepath, export_selected_objects=only_selected)
    else:  # pragma: no cover - legacy Blender path
        bpy.ops.export_mesh.stl(filepath=filepath, use_selection=only_selected)
    import os

    size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
    return {"filepath": filepath, "bytes": size}


def _op_render(filepath: str, samples: int = 16) -> dict:
    """Render the current scene to an image (remote render)."""
    scene = bpy.context.scene
    scene.render.filepath = filepath
    if scene.render.engine == "CYCLES":
        scene.cycles.samples = samples
    bpy.ops.render.render(write_still=True)
    return {"filepath": filepath, "engine": scene.render.engine}


# Method name -> main-thread op. The MCP server mirrors these names as tools.
_OPS: dict[str, Callable[..., dict]] = {
    "ping": _op_ping,
    "get_scene_info": _op_get_scene_info,
    "clear_scene": _op_clear_scene,
    "add_cube": _op_add_cube,
    "add_cylinder": _op_add_cylinder,
    "boolean_difference": _op_boolean_difference,
    "set_transform": _op_set_transform,
    "export_stl": _op_export_stl,
    "render": _op_render,
}


def dispatch(method: str, params: dict) -> Any:
    """Resolve a JSON-RPC method to a main-thread op and run it there."""
    if method not in _OPS:
        raise ValueError(f"unknown method {method!r}; known: {sorted(_OPS)}")
    op = _OPS[method]
    return run_on_main_thread(lambda: op(**(params or {})))


# --------------------------------------------------------------------------- #
# Socket server (runs on a background thread)
# --------------------------------------------------------------------------- #
class BridgeServer:
    """Newline-delimited JSON-RPC 2.0 server bound to a local TCP socket."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def _serve(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(4)
        self._sock.settimeout(1.0)
        print(f"[mb_blender_bridge] listening on {self.host}:{self.port}", flush=True)
        while self._running:
            try:
                conn, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            buf = b""
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
                        conn.sendall(self._process(line) + b"\n")

    def _process(self, line: bytes) -> bytes:
        try:
            req = json.loads(line)
            rid = req.get("id")
            result = dispatch(req["method"], req.get("params") or {})
            return json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}).encode()
        except Exception as exc:
            rid = None
            try:
                rid = json.loads(line).get("id")
            except Exception:
                pass
            return json.dumps(
                {"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": str(exc)}}
            ).encode()

    def start(self) -> None:
        if bpy is not None:
            # Register the main-thread drainer so queued ops actually execute.
            bpy.app.timers.register(_drain_main_thread_tasks, persistent=True)
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._sock:
            self._sock.close()


_server: Optional[BridgeServer] = None


def start_bridge(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> BridgeServer:
    global _server
    _server = BridgeServer(host, port)
    _server.start()
    return _server


# --- Blender add-on registration hooks (GUI use) --------------------------- #
bl_info = {
    "name": "MakerBench Blender Bridge",
    "blender": (4, 0, 0),
    "category": "Development",
    "description": "JSON-RPC scene-graph bridge for the MakerBench workflow track (mb#93).",
}


def register() -> None:  # pragma: no cover - GUI entrypoint
    start_bridge()


def unregister() -> None:  # pragma: no cover - GUI entrypoint
    if _server:
        _server.stop()
