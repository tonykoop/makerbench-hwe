"""Interactive, step-by-step live CAD sessions wired like the nightly driver (#663).

The nightly driver runs ``codex exec`` / ``claude -p`` once, hands-off. Fixing a complex
assembly ("make the sambuca neck meet the hull") goes better step by step: measure, adjust,
re-measure, render, with a person (or the manager) giving each instruction. This launches the
same runtime *interactively* with the same connector wiring as ``live_cad_runner.driver_command``:

- the connector's MCP tools are pre-approved, for that one server only;
- the bridge env is forwarded by NAME (Codex ``env_vars``, Claude ``${NAME:-default}``), so no
  token is written to disk or put on a command line;
- everything else keeps its normal prompts / sandbox, and no global CLI config is touched.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

from . import code_cad_providers as providers
from .live_cad_runner import (
    LiveCadConfig,
    codex_mcp_overrides,
    connector_available,
    connector_mcp_probe,
    write_claude_mcp_config,
)

DEFAULT_SECRETS = Path("/mnt/c/Users/Tony/Documents/StudioPipeline-Pilot/arena-nightly/makerbench-nightly.env")
DEFAULT_MODELS = {"codex": "gpt-6.1-sol", "claude": "claude-opus-5-5"}
CADS = {"solidworks": ("solidworks-live", "hwe-solidworks"), "fusion": ("fusion-live", "hwe-fusion")}
BRIDGE_ENV_PREFIXES = ("HWE_SW_", "HWE_FUSION_")
_START_HINTS = {
    "hwe-solidworks": "start it with StudioPipeline-hwe scripts\\start_solidworks_bridge.ps1",
    "hwe-fusion": "open Fusion with the Luthier Bridge add-in running",
}


class SessionError(RuntimeError):
    """The session cannot start; the message says why and is safe to print."""


@dataclass(frozen=True)
class SessionPlan:
    argv: list[str]          # never holds a secret value
    cwd: Path
    env: dict[str, str]      # the launcher's env plus the bridge env from the secrets file
    connector: str


def bridge_env(entries: Mapping[str, str], environ: Mapping[str, str]) -> dict[str, str]:
    """HWE_SW_* / HWE_FUSION_* values: the secrets file wins over the inherited environment."""
    merged = {key: value for key, value in environ.items() if key.startswith(BRIDGE_ENV_PREFIXES)}
    merged.update({key: value for key, value in entries.items() if key.startswith(BRIDGE_ENV_PREFIXES) and value})
    return merged


def session_config(cad: str, runtime: str, model: Optional[str], env: Mapping[str, str],
                   connector_root: Optional[str] = None) -> LiveCadConfig:
    if cad not in CADS:
        raise SessionError(f"--cad must be one of {sorted(CADS)}, not {cad!r}")
    if runtime not in DEFAULT_MODELS:
        raise SessionError(f"--runtime must be one of {sorted(DEFAULT_MODELS)}, not {runtime!r}")
    name = model or DEFAULT_MODELS[runtime]
    if runtime == "claude":
        name = providers.claude_cli_model(name)
        problem = providers.claude_cli_model_problem(name)
        if problem:
            raise SessionError(problem)
    backend, connector = CADS[cad]
    extra = {"connector_cwd": Path(connector_root).as_posix()} if connector_root else {}
    return LiveCadConfig(backend=backend, driver_model=name, connector=connector, driver_runtime=runtime,
                         env=dict(env), **extra)


def session_command(config: LiveCadConfig, workdir: Path, session_dir: Path,
                    prompt: Optional[str] = None) -> list[str]:
    """The interactive argv. ``session_dir`` holds Claude's generated MCP config (no secrets)."""
    work = workdir.resolve().as_posix()
    if config.driver_runtime == "codex":
        # workspace-write bounds shell writes, and the session's writable roots replace any the
        # user's config.toml adds, so the workdir is the only write root; on-request overrides an
        # inherited approval_policy="never". Only this connector's tools are pre-approved.
        argv = ["codex", "-m", config.driver_model, "--sandbox", "workspace-write", "--ask-for-approval", "on-request",
                "-C", work, f"--add-dir={work}",
                "-c", f"sandbox_workspace_write.writable_roots={json.dumps([work])}",
                *codex_mcp_overrides(config, approve=True)]
    elif config.driver_runtime == "claude":
        mcp_config = write_claude_mcp_config(config, session_dir / "session-mcp.json")
        # Shell stays available but prompts: the mode is pinned to "manual" and no user/project/local
        # settings load (--setting-sources=), so an inherited defaultMode, allow rule or
        # additionalDirectories cannot widen approval. Managed policy settings and CLAUDE.md still load.
        argv = ["claude", "--model", config.driver_model, "--permission-mode", "manual", "--setting-sources=",
                f"--mcp-config={mcp_config.resolve().as_posix()}", "--strict-mcp-config",
                f"--allowedTools=Read,mcp__{config.connector}", f"--add-dir={work}"]
    else:
        raise SessionError(f"unknown runtime {config.driver_runtime!r}")
    if prompt:
        argv += ["--", prompt]  # a prompt that starts with "-" is never parsed as an option
    return argv


def plan_session(
    *,
    cad: str,
    runtime: str,
    workdir: Path,
    model: Optional[str] = None,
    secrets: Path = DEFAULT_SECRETS,
    prompt_file: Optional[Path] = None,
    connector_root: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    session_dir: Optional[Path] = None,
    ping: Optional[Callable[[LiveCadConfig], bool]] = None,
    mcp_probe: Optional[Callable[[LiveCadConfig], tuple[bool, str]]] = None,
) -> SessionPlan:
    """Check everything and build the session; raises ``SessionError`` with a printable reason."""
    from .nightly_preflight import parse_secrets_file

    base = dict(os.environ if environ is None else environ)
    if not workdir.is_dir():
        raise SessionError(f"--workdir {workdir} is not a folder")
    if not secrets.is_file():
        raise SessionError(f"secrets file not found: {secrets}")
    bridge = bridge_env(parse_secrets_file(secrets), base)
    config = session_config(cad, runtime, model, bridge, connector_root)
    prompt = None
    if prompt_file is not None:
        if not prompt_file.is_file():
            raise SessionError(f"--prompt-file {prompt_file} not found")
        prompt = prompt_file.read_text(encoding="utf-8").strip() or None
    if not (ping or connector_available)(config):
        raise SessionError(f"{config.connector} bridge did not answer an authenticated ping "
                           f"({_START_HINTS[config.connector]}); not launching")
    ok, message = (mcp_probe or connector_mcp_probe)(config)
    if not ok:
        raise SessionError(f"{message}; not launching")
    session_dir = session_dir or Path(tempfile.mkdtemp(prefix="live-cad-session-"))
    argv = session_command(config, workdir, session_dir, prompt)
    return SessionPlan(argv=argv, cwd=workdir.resolve(), env={**base, **bridge}, connector=config.connector)


def launch(plan: SessionPlan) -> None:  # pragma: no cover - replaces this process
    os.chdir(plan.cwd)
    os.execvpe(plan.argv[0], plan.argv, plan.env)
