"""Live CAD backend for the arena — autonomous agent builds the entrant live.

`arena run --backend solidworks-live|fusion-live` generates each entrant by
spawning an autonomous driver agent (``codex exec -m gpt-6.1-sol`` or
``claude -p --model claude-sonnet-5-5``) that
drives the hwe-solidworks / hwe-fusion Luthier-Bridge MCP connector to build the
instrument in the live CAD app from the reference image + brief, then exports an
STL. The arena wraps that STL through the same objective gate and trial-row
schema every other backend uses, so votes / Elo / agreement / export need zero
special-casing.

This is the *agentic* live modality (the WSL driver talks HTTP to the Windows
adapter). It deliberately does NOT use #627's static-macro Windows job-dir
watcher — the proven path is an LLM driving the connector's MCP tools.

Because the CAD seat is single-writer and each build is minutes-long and
non-deterministic, live trials are strictly serialized and their entrants form
their own *live tier* (weaker blindness than blind single-shot text entrants —
the agent gets interactive ``capture_view`` feedback). Never fold live-tier
entrants into the blind Elo pool.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional

from . import cad_names, render
from . import code_cad_providers as providers
from .code_cad_arena_runner import (
    evaluate_objective_trial,
    mesh_objective_gate,
    raise_with_trial_meta,
)
from .code_cad_generator import instrument_spec_from_registry
from .code_cad_objective import RenderArtifacts
from .code_cad_orchestrator import ArenaTrial, TrialExecutor

LIVE_BACKENDS = ("solidworks-live", "fusion-live")
DEFAULT_DRIVER_MODEL = "gpt-6.1-sol"
DRIVER_RUNTIMES = ("codex", "claude")

# The StudioPipeline-hwe MCP shim each connector runs, and the bridge env it needs. The driver is
# told about its connector explicitly on every run (never through a user's CLI config), and the
# env is forwarded by NAME, so no token is written to disk or put on a command line.
_MCP_MODULES = {"hwe-solidworks": "driver.solidworks_mcp_server", "hwe-fusion": "driver.fusion_mcp_server"}
_CONNECTOR_ENV = {
    "hwe-solidworks": ("HWE_SW_HOST", "HWE_SW_PORT", "HWE_SW_TOKEN"),
    "hwe-fusion": ("HWE_FUSION_HOST", "HWE_FUSION_PORT", "HWE_FUSION_TOKEN"),
}
# Claude Code expands an unset ``${NAME:-}`` to "", and the shims take a set-but-empty
# HWE_*_HOST as the host itself (``http://:8766`` -> "Name or service not known", #663), so the
# generated config carries the shims' own defaults instead of an empty fallback.
_CONNECTOR_DEFAULTS = {
    "HWE_SW_HOST": "127.0.0.1", "HWE_SW_PORT": "8767",
    "HWE_FUSION_HOST": "127.0.0.1", "HWE_FUSION_PORT": "8766",
}
_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def driver_route(model_id: str) -> tuple[str, str]:
    """``(runtime, CLI model)`` for a live entrant's driver model id.

    ``codex-<model>`` drives with Codex, ``claude-code-<model>`` / ``claude-<model>`` with Claude
    Code (``opus-5.5`` becomes the full name ``claude-opus-5-5``), and a bare id stays a Codex
    model, as every live entrant was before the runtime could be chosen.
    """
    raw = str(model_id or "").strip()
    lowered = raw.lower()
    for prefix, runtime in (("claude-code-", "claude"), ("codex-", "codex")):
        if lowered.startswith(prefix):
            name = raw[len(prefix):]
            break
    else:
        if lowered.startswith("claude-"):
            runtime, name = "claude", raw
        else:
            runtime, name = "codex", raw
    if not name:
        raise ValueError(f"live driver model id {model_id!r} names no model")
    if runtime == "claude":
        name = providers.claude_cli_model(name)
    return runtime, name

# instrument family -> a short structural + negative reference hint appended to
# the brief when an image is unavailable; the image is preferred when present.
_ASSIGNMENT_TEMPLATE = """You are an AUTONOMOUS CAD agent competing in the Code-CAD A/B Arena. Build the instrument below in LIVE CAD via the `{connector}` MCP connector, replicating the reference, fully hands-off. Do NOT ask anything — decide and act (build-loop autonomy).

STEP 0 — ground yourself (read fully before touching geometry):
- {docs_dir}/luthier-bridge-build-loop.md   (the build contract)
- {docs_dir}/luthier-bridge-conventions.md  (units mm / origin / axis / feature vocabulary)
{image_line}
CONNECTOR: `{connector}` MCP tools are registered ({tool_menu}). A disposable empty part is open — the seat is yours. First 3 calls read-only: ping, get_context, list_pending. Do NOT assume a tool name — the exact vocabulary is whatever get_context / the tool list advertises; use those names.

INSTRUMENT: {instrument_id} ({family}).
BRIEF: {brief}

CONSTRAINTS (arena mesh-gate — you are scored on these): envelope {env} mm; >= {min_bodies} distinct bodies; every body a WATERTIGHT solid (bodies must NOT interpenetrate — braze or gap cleanly); nonzero volume. Use the right features: revolve for flares/cups; sweep/loft for tubing; shell for hollow bores; boss-extrude + revolve for casings/buttons. If a feature fails, read the error and self-correct (gentler bends, loft segments, reposition) — never fake geometry.

LOOP: plan -> stage -> list_pending -> confirm -> capture_view (compare to the reference) -> measure (bbox in envelope? bodies?) -> self-correct -> iterate until it matches the reference.

WHEN DONE you MUST export the finished part: call the connector `{export_tool}` tool with format "stl" (the bridge writes mm), absolute path exactly:
    {win_stl}
{native_line}FILE NAMES: {naming_rule} Use exactly the paths above.
Then print a one-paragraph build summary. The STL at that path is your arena entry — if you do not export it, your entry does not count.
"""

# Connector-specific tool vocabulary hints (the agent still verifies against the
# live tool list — these keep the assignment from naming SolidWorks-only tools to
# a Fusion agent or vice-versa).
_TOOL_MENU = {
    "hwe-solidworks": ("ping, get_context, list_pending, stage_feature/revolve/sweep/loft/"
                       "shell/cylinder, confirm, capture_view, measure, delete_body, "
                       "move_body, rotate_body, export"),
    "hwe-fusion": ("ping, get_context, list_pending, stage_feature/stage_revolve/stage_sketch, "
                   "fillet_body/chamfer_body/shell_body, circular_pattern_body/mirror_body, "
                   "confirm, capture_view, measure, move_body/transform_body, delete_body, "
                   "export_design"),
}
_EXPORT_TOOL = {"hwe-solidworks": "export", "hwe-fusion": "export_design"}


@dataclass
class LiveCadConfig:
    """How to run one live CAD entrant."""

    backend: str                                   # "solidworks-live" | "fusion-live"
    driver_model: str                              # the CLI model, e.g. "gpt-6.1-sol"
    connector: str = "hwe-solidworks"              # MCP server name
    connector_cwd: str = "/mnt/c/Users/Tony/Documents/GitHub/StudioPipeline-hwe"
    driver_argv: tuple[str, ...] = (
        "codex", "exec", "-m", "{model}",
        "--sandbox", "workspace-write",
        "--ephemeral", "--skip-git-repo-check",
        "-C", "{workspace}",
    )
    win_staging_root: str = r"C:\Users\Tony\Documents\StudioPipeline-Pilot\arena-live"
    wsl_staging_root: str = "/mnt/c/Users/Tony/Documents/StudioPipeline-Pilot/arena-live"
    images_root: Optional[Path] = None             # dir of <instrument_id>.png references
    image_paths: Mapping[str, Path] = field(default_factory=dict)
    context_tier: str = "blind"
    env: Mapping[str, str] = field(default_factory=dict)   # HWE_SW_TOKEN/HOST/PORT etc.
    timeout_s: int = 1800
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run
    cad_stem: Optional[str] = None                 # <instrument>_<variant>_<revision> (#1134); from the spec if unset
    driver_runtime: str = "codex"                  # "codex" (driver_argv) | "claude"
    wire_mcp: bool = True                          # pass the connector to the driver explicitly
    mcp_python: str = "python3"                    # runs the StudioPipeline-hwe MCP shim

    @property
    def cad_name(self) -> str:
        return self.backend.replace("-live", "")

    def for_fusion(self) -> bool:
        return self.backend == "fusion-live"


def _mcp_server(config: LiveCadConfig) -> dict:
    module = _MCP_MODULES.get(config.connector)
    if module is None:
        raise ValueError(f"no MCP shim is known for connector {config.connector}")
    return {"command": config.mcp_python, "args": ["-m", module],
            "pythonpath": Path(config.connector_cwd).as_posix(),
            "env_names": list(_CONNECTOR_ENV.get(config.connector, ()))}


def driver_command(config: LiveCadConfig, gen_dir: Path) -> list[str]:
    """The driver argv (without the assignment) for the entrant's runtime.

    Codex keeps ``driver_argv`` (workspace-write sandbox, ephemeral) and gets the connector
    through ``-c mcp_servers.<connector>.*`` overrides, forwarding the bridge env by name
    (``env_vars``). Claude Code gets a generated ``--mcp-config`` whose env values are
    ``${NAME:-}`` references, loads no other MCP server (``--strict-mcp-config``), drops
    command-running tools (``--restricted``) and runs ``dontAsk`` with only Read and the
    connector's tools allowed, so it can read its brief and photo and drive CAD, nothing else.
    """
    workspace = gen_dir.resolve().as_posix()
    if config.driver_runtime == "codex":
        argv = [tok.format(model=config.driver_model, workspace=workspace) for tok in config.driver_argv]
        if config.wire_mcp:
            server = _mcp_server(config)
            key = f"mcp_servers.{config.connector}"
            argv += [
                "-c", f"{key}.command={json.dumps(server['command'])}",
                "-c", f"{key}.args={json.dumps(server['args'])}",
                "-c", f"{key}.env={{PYTHONPATH={json.dumps(server['pythonpath'])}}}",
                "-c", f"{key}.env_vars={json.dumps(server['env_names'])}",
            ]
            if config.connector == "hwe-solidworks":
                # codex exec cannot prompt. Approve this CAD connector only;
                # shell commands retain the workspace-write sandbox.
                argv += ["-c", f'{key}.default_tools_approval_mode="approve"']
        return argv
    if config.driver_runtime == "claude":
        server = _mcp_server(config)
        mcp_config = gen_dir / "driver-mcp.json"
        env = {name: "${%s:-%s}" % (name, _CONNECTOR_DEFAULTS.get(name, "")) for name in server["env_names"]}
        env["PYTHONPATH"] = server["pythonpath"]
        mcp_config.write_text(json.dumps(
            {"mcpServers": {config.connector: {"command": server["command"], "args": server["args"], "env": env}}},
            indent=2, sort_keys=True), encoding="utf-8")
        return [
            "claude", "-p", "--model", config.driver_model, "--output-format", "text",
            f"--mcp-config={mcp_config.resolve().as_posix()}", "--strict-mcp-config", "--restricted",
            "--permission-mode", "dontAsk", f"--allowedTools=Read,mcp__{config.connector}",
        ]
    raise ValueError(f"unknown live driver runtime {config.driver_runtime!r} (expected one of {DRIVER_RUNTIMES})")


_CAD_NAMES = {"hwe-solidworks": "SolidWorks", "hwe-fusion": "Fusion"}


def live_brief(brief: str, connector: str) -> str:
    """The registry brief for a live CAD agent, without its code-CAD wording.

    Registry briefs are written for the OpenSCAD arena ("as ONE self-contained OpenSCAD program",
    "one module per part"); a SolidWorks or Fusion agent builds bodies in a part instead.
    """
    cad = _CAD_NAMES.get(connector, "CAD")
    text = re.sub(r"as ONE self-contained OpenSCAD program", f"in ONE {cad} part", brief)
    text = re.sub(r"\bOpenSCAD (program|model|file)s?\b", f"{cad} part", text)
    text = re.sub(r"\bone module per part\b", "one named body per part", text, flags=re.I)
    text = re.sub(r"\ba top-level assembly module placing every part\b", "every body placed", text, flags=re.I)
    return text.replace("OpenSCAD", cad)


def _win_path(root: str, trial_id: str, name: str) -> str:
    return f"{root.rstrip(chr(92))}\\{trial_id}\\{name}"


def _reference_image(config: LiveCadConfig, instrument_id: str) -> Optional[Path]:
    mapped = config.image_paths.get(instrument_id)
    if mapped is not None:
        mapped_path = Path(mapped)
        return mapped_path if mapped_path.is_file() else None
    if config.images_root is None:
        return None
    candidate = Path(config.images_root) / f"{instrument_id}.png"
    return candidate if candidate.exists() else None


_NATIVE_EXT = {"hwe-solidworks": "SLDPRT", "hwe-fusion": "f3d"}


def deliverable_names(spec: Mapping[str, object], config: LiveCadConfig) -> tuple[str, str, str]:
    """``(stem, STL name, native name)`` for the entry, per the #1134 naming rule."""
    stem = config.cad_stem or cad_names.stem(str(spec.get("id")))
    native = cad_names.name(stem, cad_names.MODEL_PART, _NATIVE_EXT.get(config.connector, "SLDPRT"))
    return stem, cad_names.name(stem, cad_names.MODEL_PART, "stl"), native


def build_assignment(
    spec: Mapping[str, object],
    config: LiveCadConfig,
    win_stl: str,
    *,
    docs_dir: Optional[Path] = None,
    reference_image: Optional[Path] = None,
    win_native: Optional[str] = None,
) -> str:
    """Render the driver agent's assignment for one instrument."""
    instrument_id = str(spec.get("id"))
    image = Path(reference_image) if reference_image is not None else _reference_image(
        config, instrument_id
    )
    if config.context_tier == "image" and image is None:
        raise ValueError(
            f"live context tier 'image' needs a readable reference for {instrument_id}"
        )
    image_line = (
        f"- REFERENCE PHOTO you must replicate: {image}  — open and study it.\n"
        if image else
        "- No reference photo available; replicate a real example of this instrument from the brief.\n"
    )
    env = spec.get("envelope_mm") or []
    return _ASSIGNMENT_TEMPLATE.format(
        connector=config.connector,
        docs_dir=(docs_dir or Path(config.connector_cwd) / "docs").as_posix(),
        image_line=image_line,
        tool_menu=_TOOL_MENU.get(config.connector, _TOOL_MENU["hwe-solidworks"]),
        export_tool=_EXPORT_TOOL.get(config.connector, "export"),
        instrument_id=instrument_id,
        family=spec.get("family", "instrument"),
        brief=live_brief(str(spec.get("task_brief") or spec.get("task_brief_short") or instrument_id), config.connector),
        env=" x ".join(str(x) for x in env) if env else "533 x 200 x 180",
        min_bodies=spec.get("min_bodies") or 5,
        win_stl=win_stl,
        native_line=(f"If the connector can also save the native {config.cad_name} file, save it as exactly:\n    {win_native}\n"
                     if win_native else ""),
        naming_rule=cad_names.RULE,
    )


def _prepare_driver_workspace(gen_dir: Path, config: LiveCadConfig) -> Path:
    """Stage only the connector instructions required by the autonomous driver.

    The driver runs with ``gen_dir`` as its workspace root.  Copying the two
    read-only instruction files avoids granting the agent write access to the
    connector checkout merely so it can read the build contract.
    """

    docs_dir = gen_dir / "connector-docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    source_docs = Path(config.connector_cwd) / "docs"
    for name in ("luthier-bridge-build-loop.md", "luthier-bridge-conventions.md"):
        source = source_docs / name
        if source.is_file():
            shutil.copyfile(source, docs_dir / name)
    return docs_dir


def _redact_transcript(text: str, env: Mapping[str, str]) -> str:
    """Remove connector/provider credentials before persisting agent output."""

    redacted = text
    for key, value in env.items():
        if value and (key.endswith("_TOKEN") or key.endswith("_KEY")):
            redacted = redacted.replace(value, "[REDACTED]")
    redacted = re.sub(
        r"(?i)(authorization\s*:\s*bearer\s+)[^\s\"']+",
        r"\1[REDACTED]",
        redacted,
    )
    return redacted


def connector_available(config: LiveCadConfig) -> bool:
    """Best-effort preflight: the adapter answers an authenticated ping.

    WSL cannot see the Windows CAD app directly; this only checks the HTTP
    boundary. A False here means don't bother launching agents.
    """
    prefix = "HWE_FUSION" if config.for_fusion() else "HWE_SW"
    default_port = "8766" if config.for_fusion() else "8767"
    host = config.env.get(f"{prefix}_HOST") or os.environ.get(f"{prefix}_HOST", "127.0.0.1")
    port = config.env.get(f"{prefix}_PORT") or os.environ.get(f"{prefix}_PORT", default_port)
    token = config.env.get(f"{prefix}_TOKEN") or os.environ.get(f"{prefix}_TOKEN", "")
    import json as _json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(f"http://{host}:{port}/ping")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read().decode())
        # SolidWorks reports liveness via ``api_available``; Fusion via ``have_adsk``.
        return bool(data.get("ok") and (data.get("api_available") or data.get("have_adsk")))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def expand_mcp_env(value: str, environ: Mapping[str, str]) -> str:
    """Resolve ``${NAME}`` / ``${NAME:-default}`` the way Claude Code does for ``--mcp-config``.

    An unset or empty variable takes the default (or "" without one), as the probe below found.
    """
    def resolve(match: re.Match) -> str:
        current = environ.get(match.group(1)) or ""
        return current or (match.group(2) or "")
    return _ENV_REF.sub(resolve, value)


def _mcp_launch(config: LiveCadConfig, workspace: Path, environ: Mapping[str, str]) -> tuple[list[str], dict, list[str]]:
    """``(server argv, server env, problems)`` exactly as the driver's runtime would launch it."""
    argv = driver_command(config, workspace)
    problems: list[str] = []
    if config.driver_runtime == "claude":
        allowed = next((tok.split("=", 1)[1] for tok in argv if tok.startswith("--allowedTools=")), "")
        if f"mcp__{config.connector}" not in allowed.split(","):
            problems.append(f"--allowedTools does not allow mcp__{config.connector}")
        server = json.loads((workspace / "driver-mcp.json").read_text(encoding="utf-8"))["mcpServers"][config.connector]
        env = {**environ, **{key: expand_mcp_env(str(value), environ) for key, value in server.get("env", {}).items()}}
        return [server["command"], *server["args"]], env, problems
    server = _mcp_server(config)
    # Codex forwards ``env_vars`` by name from its own env and sets ``env`` (PYTHONPATH) literally.
    env = {**environ, "PYTHONPATH": server["pythonpath"]}
    return [server["command"], *server["args"]], env, problems


def connector_mcp_probe(
    config: LiveCadConfig,
    *,
    environ: Optional[Mapping[str, str]] = None,
    timeout_s: float = 30.0,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> tuple[bool, str]:
    """Launch the connector's MCP shim the way the driver does and call its ``ping`` tool.

    ``connector_available`` only proves WSL can reach the bridge; this proves the shim the agent
    gets (same generated config, same env expansion, same allowed tools) can too (#663).
    """
    base = {**os.environ, **config.env} if environ is None else {**environ}
    with tempfile.TemporaryDirectory(prefix="mcp-probe-") as tmp:
        workspace = Path(tmp)
        try:
            argv, env, problems = _mcp_launch(config, workspace, base)
        except (OSError, ValueError, KeyError) as exc:
            return False, f"{config.connector} MCP config could not be built: {exc}"
        if problems:
            return False, "; ".join(problems)
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "makerbench-preflight", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "ping", "arguments": {}}},
        ]
        try:
            proc = run(argv, input="".join(json.dumps(m) + "\n" for m in messages), cwd=workspace, env=env,
                       capture_output=True, text=True, timeout=timeout_s, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"{config.connector} MCP shim did not run: {exc}"
    reply = None
    for line in (getattr(proc, "stdout", "") or "").splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("id") == 2:
            reply = message
    if reply is None:
        tail = _redact_transcript((getattr(proc, "stderr", "") or "").strip(), env)[-300:]
        return False, f"{config.connector} MCP shim gave no ping reply (exit {getattr(proc, 'returncode', '?')}): {tail}"
    result = reply.get("result") or {}
    text = " ".join(str(part.get("text", "")) for part in result.get("content") or [] if isinstance(part, dict))
    if reply.get("error") or result.get("isError"):
        detail = text or str((reply.get("error") or {}).get("message", "error"))
        return False, f"{config.connector} MCP ping failed through the driver's launch path: {_redact_transcript(detail, env)[:300]}"
    return True, f"{config.connector} MCP shim answered ping through the driver's launch path"


def run_live_agent(spec: Mapping[str, object], gen_dir: Path,
                   config: LiveCadConfig) -> Path:
    """Spawn the driver agent to build the instrument live and export an STL.

    Returns the WSL path to the exported STL (copied into ``gen_dir``). Raises
    ``render.CompileError`` if the agent fails or produces no STL — the arena
    turns that into a uniform ``auto_fail`` row.
    """
    trial_id = gen_dir.name
    gen_dir.mkdir(parents=True, exist_ok=True)
    stem, stl_name, native_name = deliverable_names(spec, config)
    win_stl = _win_path(config.win_staging_root, trial_id, f"{stem}\\{stl_name}")
    win_native = _win_path(config.win_staging_root, trial_id, f"{stem}\\{native_name}")
    staging = Path(config.wsl_staging_root) / trial_id / stem
    wsl_stl = staging / stl_name
    staging.mkdir(parents=True, exist_ok=True)
    # Never accept or advertise anything from a previous attempt of this trial (a resumed night
    # reruns it): clear the staging folder and the copies this function made last time.
    previous = [*staging.iterdir(), *((gen_dir / stem).iterdir() if (gen_dir / stem).is_dir() else ()),
                gen_dir / "naming-notes.txt", gen_dir / "output.stl", gen_dir / "preview.png"]
    for stale in previous:
        if stale.is_file() or stale.is_symlink():
            stale.unlink()

    docs_dir = _prepare_driver_workspace(gen_dir, config)
    source_image = _reference_image(config, str(spec.get("id")))
    staged_image = None
    if source_image is not None:
        staged_image = gen_dir / "reference-image.png"
        shutil.copyfile(source_image, staged_image)
    assignment = build_assignment(
        spec,
        config,
        win_stl,
        docs_dir=docs_dir,
        reference_image=staged_image,
        win_native=win_native,
    )
    (gen_dir / "assignment.md").write_text(assignment, encoding="utf-8")
    argv = driver_command(config, gen_dir)
    argv.append(assignment)
    env = {**os.environ, **config.env}
    try:
        proc = config.runner(
            argv, cwd=gen_dir, env=env, capture_output=True,
            text=True, timeout=config.timeout_s, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise render.CompileError(f"live {config.backend} driver failed to run: {exc}")

    transcript = gen_dir / "build_transcript.txt"
    transcript.write_text(
        _redact_transcript(
            (getattr(proc, "stdout", "") or "")
            + "\n--- stderr ---\n"
            + (getattr(proc, "stderr", "") or ""),
            env,
        ),
        encoding="utf-8",
    )

    notes = []
    if not wsl_stl.exists() or wsl_stl.stat().st_size == 0:
        # A misnamed export is a clear note, not a silent failure (#1134): one other STL is taken.
        others = sorted(p for p in staging.glob("*.stl") if p.is_file() and p.stat().st_size > 0)
        if len(others) != 1:
            found = ", ".join(p.name for p in others) or "nothing"
            raise render.CompileError(
                f"live {config.backend} agent produced no STL at {wsl_stl} (found {found}; "
                f"driver exit {getattr(proc, 'returncode', '?')})"
            )
        notes.append(f"exported {others[0].name}; the naming rule (#1134) asks for {stl_name}")
        wsl_stl = others[0]
    deliverables = gen_dir / stem
    deliverables.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(wsl_stl, deliverables / stl_name)
    native = staging / native_name
    if native.is_file() and native.stat().st_size > 0:
        shutil.copyfile(native, deliverables / native_name)
    else:
        misnamed = sorted(p for p in staging.glob(f"*.{_NATIVE_EXT.get(config.connector, 'SLDPRT')}")
                          if p.is_file() and p.name.lower() != native_name.lower())
        if misnamed:
            notes.append(f"saved {misnamed[0].name}; the naming rule (#1134) asks for {native_name}")
            shutil.copyfile(misnamed[0], deliverables / native_name)
    if notes:
        (gen_dir / "naming-notes.txt").write_text("\n".join(notes) + "\n", encoding="utf-8")
    staged = gen_dir / "output.stl"  # the grader's working copy; the named deliverables sit beside it
    shutil.copyfile(wsl_stl, staged)
    # carry a preview if the agent left one beside the STL
    for png_name in ("preview.png", "output.png", f"{trial_id}.png", f"{stem}_preview.png"):
        src = staging / png_name
        if src.exists():
            shutil.copyfile(src, gen_dir / "preview.png")
            break
    return staged


def make_live_execute_trial(
    *,
    registry: Mapping[str, object],
    run_dir: Path,
    config: LiveCadConfig,
    gate_factory: Callable[[Mapping[str, object]], Callable] = mesh_objective_gate,
) -> TrialExecutor:
    """A TrialExecutor that builds each entrant live via the driver agent.

    Row shape is identical to ``make_execute_trial`` (via ``evaluate_objective_trial``
    + a pass-through STL compiler), so everything downstream is unchanged. Adds
    ``backend`` and ``tier: "live"`` to the payload so the live entrants can be
    kept out of the blind Elo pool and reported as their own tier.
    """

    def execute(trial: ArenaTrial) -> dict:
        try:
            return _execute_body(trial)
        except Exception as exc:
            # A live build that failed (driver error, no STL) keeps its backend/tier, or it
            # would land in the run config's row.
            meta = {"backend": config.backend, "tier": "live", "context_tier": config.context_tier}
            raise_with_trial_meta(exc, meta)

    def _execute_body(trial: ArenaTrial) -> dict:
        spec = instrument_spec_from_registry(registry, trial.instrument_id)
        objective_gate = gate_factory(spec)
        gen_dir = run_dir / "gen" / trial.trial_id
        stl = run_live_agent(spec, gen_dir, config)      # may raise CompileError
        preview = gen_dir / "preview.png"

        def compiler(_scad: Path, out_dir: Path) -> RenderArtifacts:
            out_dir.mkdir(parents=True, exist_ok=True)
            staged_stl = out_dir / "output.stl"
            shutil.copyfile(stl, staged_stl)
            if preview.exists():
                staged_png = out_dir / "preview.png"
                shutil.copyfile(preview, staged_png)
            else:
                staged_png = out_dir / "preview.missing.png"
            return RenderArtifacts(stl_path=staged_stl, png_path=staged_png)

        payload = evaluate_objective_trial(
            trial_id=trial.trial_id,
            model_id=trial.model_id,
            instrument_id=trial.instrument_id,
            seed=trial.seed,
            scad_path=gen_dir / "build_transcript.txt",   # provenance stand-in
            out_dir=run_dir / "render" / trial.trial_id,
            objective_gate=objective_gate,
            compiler=compiler,
        )
        payload["rep"] = trial.rep
        payload["gen"] = {"transcript_path": (gen_dir / "build_transcript.txt").as_posix(),
                          "assignment_path": (gen_dir / "assignment.md").as_posix()}
        payload["backend"] = config.backend
        payload["tier"] = "live"
        payload["context_tier"] = config.context_tier
        stem, _stl, native_name = deliverable_names(spec, config)
        native = gen_dir / stem / native_name
        if native.is_file():
            payload["native_path"] = native.as_posix()  # Morning Review's "Open in <CAD>"
        notes = gen_dir / "naming-notes.txt"
        if notes.is_file():
            payload["naming_notes"] = notes.read_text(encoding="utf-8").splitlines()
        return payload

    return execute
