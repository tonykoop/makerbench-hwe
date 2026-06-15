"""headless_launcher.py — start the bridge inside headless Blender.

Run with::

    blender --background --python headless_launcher.py

``--background`` (a.k.a. ``-b``) gives a real bpy interpreter with no GUI, so the
bridge and its ``bpy.app.timers`` main-thread queue work exactly as they do in
the GUI. We then spin the timer loop ourselves because background Blender exits
once the script returns — ``bpy.app.timers`` only fire while an event loop runs.

Env vars:
    MB_BRIDGE_HOST (default 0.0.0.0)
    MB_BRIDGE_PORT (default 8765)
"""

from __future__ import annotations

import os
import sys
import time

# Make the add-on importable whether launched from this dir or elsewhere.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy  # type: ignore  # noqa: E402

import mb_blender_bridge as bridge  # noqa: E402

HOST = os.environ.get("MB_BRIDGE_HOST", bridge.DEFAULT_HOST)
PORT = int(os.environ.get("MB_BRIDGE_PORT", bridge.DEFAULT_PORT))


def main() -> None:
    # Start clean so a run is reproducible regardless of the default startup file.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bridge.start_bridge(HOST, PORT)
    print(f"[headless_launcher] bridge up on {HOST}:{PORT}; entering timer loop", flush=True)
    # Background Blender has no modal loop, so drive the main-thread task queue
    # ourselves. This is the same drainer the GUI timer would call.
    try:
        while True:
            bridge._drain_main_thread_tasks()
            time.sleep(0.02)
    except KeyboardInterrupt:
        print("[headless_launcher] shutting down", flush=True)
        if bridge._server:
            bridge._server.stop()


if __name__ == "__main__":
    main()
