"""CADAM CLI bridge: an Anthropic Messages API shim over `claude -p` / `codex exec` (#1100, epic #1099).

Run it with ``python -m tools.cadam_cli_bridge --config <config.json>``; see README.md.
"""

from .server import BridgeConfig, BridgeServer, ModelRoute, load_config, make_server

__all__ = ["BridgeConfig", "BridgeServer", "ModelRoute", "load_config", "make_server"]
