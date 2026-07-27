"""MakerBench Blender MCP server (mb#93).

Exposes the Blender scene-graph bridge as MCP tools so Claude (or any MCP client)
can drive a local Blender scene over a socket. Every tool-call is recorded by
``makerbench-logger`` with its Human Intervention Index level, so a session emits
a schema-valid ``workflow_manifest.json`` (mb#89, #92) alongside the gradeable
STL the run exports.

Run as an MCP stdio server::

    MB_BRIDGE_HOST=127.0.0.1 MB_BRIDGE_PORT=8765 python -m server

Requires the ``mcp`` package for the live server; the tool registry and the
manifest wiring import and are testable without it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

# Make the sibling bridge_client and the repo-root makerbench_logger importable.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bridge_client import BridgeClient  # noqa: E402

try:
    from makerbench_logger import WorkflowLogger  # noqa: E402
except Exception:  # pragma: no cover - logger always vendored in this repo
    WorkflowLogger = None  # type: ignore

HOST = os.environ.get("MB_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("MB_BRIDGE_PORT", "8765"))

# The Blender bridge methods surfaced as MCP tools. Each entry: (description,
# default HII level). add_/boolean_/set_transform are autonomous geometry ops
# (L0); a human nudging them through chat doesn't change the tool, so the level
# is the *default* — a client may override per call.
TOOLS: dict[str, tuple[str, str]] = {
    "get_scene_info": ("Introspect the scene: objects, transforms, poly counts.", "L0"),
    "clear_scene": ("Delete every object for a clean procedural build.", "L0"),
    "add_cube": ("Add a cube (size, location, name).", "L0"),
    "add_cylinder": ("Add a cylinder (radius, depth, vertices, location, name).", "L0"),
    "boolean_difference": ("Subtract cutter from target (target, cutter).", "L0"),
    "set_transform": ("Set an object's location/rotation/scale.", "L0"),
    "export_stl": ("Export the scene to STL — the gradeable artifact.", "L0"),
    "render": ("Render the scene to an image (filepath, samples).", "L0"),
}


class BlenderSession:
    """A logged Blender driving session: bridge connection + WorkflowManifest.

    Wrap one agent run; every ``call`` goes to Blender and is recorded with its
    HII level. ``emit_manifest`` writes the schema-valid manifest.
    """

    def __init__(
        self,
        *,
        task_id: str,
        seed: int = 0,
        host: str = HOST,
        port: int = PORT,
        orchestrator: str = "claude-code",
    ) -> None:
        self.client = BridgeClient(host, port)
        self._logger = (
            WorkflowLogger(
                task_id=task_id,
                seed=seed,
                orchestrator=orchestrator,
                framework="makerbench-logger",
                host_application="blender",
                execution_bridge="blender-mcp",
            )
            if WorkflowLogger is not None
            else None
        )
        # Set by emit_manifest(..., fail_closed=True): whether the last emitted
        # manifest passed schema validation, and the reason if not.
        self.manifest_valid: bool = True
        self.manifest_validation_error: Optional[str] = None

    def __enter__(self) -> "BlenderSession":
        self.client.connect()
        if self._logger is not None:
            self._logger.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._logger is not None:
            self._logger.stop()
        self.client.close()

    def call(self, method: str, *, steering: str = "L0", **params: Any) -> Any:
        """Run a scene op and record it in the manifest with its HII level."""
        result = self.client.call(method, **params)
        if self._logger is not None:
            self._logger.tool_call(f"blender.{method}", params, steering=steering)
            if method == "export_stl" and isinstance(result, dict) and result.get("filepath"):
                self._logger.add_artifact(result["filepath"])
        return result

    def emit_manifest(
        self, path: str, *, validate: bool = True, fail_closed: bool = False
    ) -> Optional[dict]:
        """Write the workflow manifest and return it.

        With ``fail_closed=True`` a structurally malformed manifest is still
        written to disk as evidence and its validation outcome is recorded on
        ``self.manifest_valid`` / ``self.manifest_validation_error`` instead of
        raising — so a garbled session is gradeable as a failure rather than
        crashing the MCP server / grader. The default (``fail_closed=False``)
        preserves strict raise-on-invalid behavior.
        """
        if self._logger is None:
            return None
        if fail_closed:
            manifest = self._logger.emit(path, validate="soft")
            self.manifest_valid, self.manifest_validation_error = self._logger.last_validation
            return manifest
        return self._logger.emit(path, validate=validate)


def _register_mcp_tools(server: Any) -> None:  # pragma: no cover - needs mcp + live bridge
    """Bind each bridge method as an MCP tool on a FastMCP-style server."""

    def _make(method: str):
        def _tool(**params: Any) -> Any:
            with BridgeClient(HOST, PORT) as client:
                return client.call(method, **params)

        _tool.__name__ = method
        _tool.__doc__ = TOOLS[method][0]
        return _tool

    for method in TOOLS:
        server.add_tool(_make(method))


def build_server() -> Any:  # pragma: no cover - exercised only with mcp installed
    """Construct the FastMCP stdio server with all Blender tools registered."""
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:
        raise RuntimeError(
            "the 'mcp' package is required to run the live server "
            "(pip install mcp); the tool registry itself is importable without it"
        ) from exc
    server = FastMCP("makerbench-blender")
    _register_mcp_tools(server)
    return server


def main() -> None:  # pragma: no cover - live entrypoint
    build_server().run()


if __name__ == "__main__":
    main()
