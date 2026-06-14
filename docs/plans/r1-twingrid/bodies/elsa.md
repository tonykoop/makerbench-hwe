## elsa — Blender-Driver (StudioPipeline/hwe · #1)
### Why
Runnable core of the agentic-CAD plugin; independent, no upstream dep.
### Scope
1. `driver/mcp_server.py` — local MCP server (socket + JSON-RPC) bridging an LLM to Blender.
2. `addon/hwe_bridge.py` — Blender addon executing ops THREAD-SAFELY: queue ALL bpy mutations on the main thread via `bpy.app.timers` (bpy is not thread-safe — #1 crash to design out).
3. Scene introspection + primitive/modifier ops + clean STEP/STL export.
4. README quickstart + headless smoke (add cube → export STL).
### Guardrails
Every bpy call through the timer queue; no re-entry-breaking global state.
### Validation
Headless smoke writes a watertight STL.
### Deliverable
PR `feat(driver): Blender MCP driver + thread-safe bpy queue` — `Refs #1`.
