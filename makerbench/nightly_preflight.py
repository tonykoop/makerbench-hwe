"""Redacted preflight doctor for the nightly CAD arena (#658).

Audits activation state without ever printing secret values:

- Secrets: each required key is PRESENT / MISSING / PLACEHOLDER. With bot sign-in
  credentials (``CADAM_BOT_EMAIL`` / ``CADAM_BOT_PASSWORD`` / ``VITE_SUPABASE_ANON_KEY``) the static
  ``CADAM_ACCESS_TOKEN`` is no longer required.
- CADAM routes (#1102): for every CADAM entrant in the queue, the route it will use. OpenRouter: key
  present, model in CADAM's picker. CLI bridge: bridge config + shared key, model mapped to the
  entrant's runtime, CLI binary present and logged in, bridge startable (or already listening), and the
  bridge-mode CADAM instance answering. Only classifications and fixed text are printed.
- CADAM (#660): the local app at ``CADAM_BASE_URL`` answers (any HTTP reply below 500; a 5xx or no
  answer is NO-GO), in both authentication modes. With bot credentials a real password sign-in must
  also succeed. Only the outcome is reported, never a token or a reply body.
- Lock: ``runs/.nightly-cad.lock`` — absent, held by a live PID, or stale.
- Queue: per-job status summary (job ids + statuses, blocked jobs called out).
- Paths: runner script, repo root, queue, output root, instruments root.
- Entrants (#663): GO/NO-GO for each entrant of the next queued job. CADAM needs the app check above
  and a cost ceiling; a CLI entrant needs its binary (and, for FreeCAD, the pinned AppDir); a live
  entrant needs its driver CLI, its connector's MCP shim and an authenticated bridge ping. The
  driver model and the serialized live order (one CAD seat) are printed.

Read-only by design: no queue or lock mutation code paths live here. The
report is safe to paste into a GO review — values never leave this module,
only classifications.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from . import cadam_routes
from .cadam_adapter import CadamAuthError, HttpTransport, sign_in

BOT_SECRET_KEYS: tuple[str, ...] = (
    "CADAM_BOT_EMAIL",
    "CADAM_BOT_PASSWORD",
    "VITE_SUPABASE_ANON_KEY",
)
DEFAULT_CADAM_BASE_URL = "http://127.0.0.1:3000"
DEFAULT_SUPABASE_URL = "http://127.0.0.1:54321"
CADAM_START_HINT = "start it: cd _meta/CADAM && npm install && npm run dev"

REQUIRED_SECRET_KEYS: tuple[str, ...] = (
    "CADAM_USER_ID",
    "CADAM_ACCESS_TOKEN",
    "SUPABASE_SERVICE_ROLE_KEY",
)

# Values that are clearly stand-ins rather than real credentials.
_PLACEHOLDER_PATTERN = re.compile(
    r"^(?:changeme|change[-_]me|placeholder|todo|fixme|dummy|example|test|"
    r"your[-_].*|<[^>]*>|x{3,}|\*{3,}|\.{3,})$",
    re.IGNORECASE,
)

SECRET_PRESENT = "PRESENT"
SECRET_MISSING = "MISSING"
SECRET_PLACEHOLDER = "PLACEHOLDER"

LOCK_ABSENT = "ABSENT"
LOCK_ACTIVE = "ACTIVE"
LOCK_STALE = "STALE"
LOCK_UNREADABLE = "UNREADABLE"


@dataclass(frozen=True)
class SecretStatus:
    key: str
    status: str  # PRESENT / MISSING / PLACEHOLDER

    @property
    def ok(self) -> bool:
        return self.status == SECRET_PRESENT


@dataclass(frozen=True)
class LockStatus:
    status: str  # ABSENT / ACTIVE / STALE / UNREADABLE
    pid: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.status == LOCK_ABSENT


@dataclass(frozen=True)
class QueueStatus:
    ok: bool
    error: Optional[str] = None
    jobs: tuple[tuple[str, str], ...] = ()  # (job_id, status)

    @property
    def blocked_job_ids(self) -> tuple[str, ...]:
        return tuple(job_id for job_id, status in self.jobs if status == "blocked")


@dataclass(frozen=True)
class PathStatus:
    name: str
    path: Path
    exists: bool


@dataclass(frozen=True)
class CadamStatus:
    base_url: str
    server_ok: bool
    signin_ok: bool
    message: str  # fixed text only: never a token, password, or response body

    @property
    def ok(self) -> bool:
        return self.server_ok and self.signin_ok


@dataclass(frozen=True)
class RouteStatus:
    label: str
    checks: tuple[tuple[bool, str], ...]  # (ok, fixed text)

    @property
    def ok(self) -> bool:
        return all(ok for ok, _ in self.checks)


@dataclass(frozen=True)
class EntrantCheck:
    entrant_id: str
    route: str
    ok: bool
    notes: tuple[str, ...] = ()  # fixed text only: never a token or a reply body


@dataclass(frozen=True)
class EntrantsStatus:
    job_id: Optional[str]
    job_notes: tuple[str, ...] = ()
    job_ok: bool = True
    entrants: tuple[EntrantCheck, ...] = ()

    @property
    def ok(self) -> bool:
        return self.job_ok and all(item.ok for item in self.entrants)


@dataclass(frozen=True)
class PreflightReport:
    secrets: tuple[SecretStatus, ...]
    lock: LockStatus
    queue: QueueStatus
    paths: tuple[PathStatus, ...] = field(default_factory=tuple)
    cadam: Optional[CadamStatus] = None
    routes: tuple[RouteStatus, ...] = ()
    entrants: Optional[EntrantsStatus] = None

    @property
    def secrets_ok(self) -> bool:
        return all(item.ok for item in self.secrets)

    @property
    def paths_ok(self) -> bool:
        return all(item.exists for item in self.paths)

    @property
    def ok(self) -> bool:
        cadam_ok = self.cadam is None or self.cadam.ok
        routes_ok = all(route.ok for route in self.routes)
        entrants_ok = self.entrants is None or self.entrants.ok
        return (self.secrets_ok and self.lock.ok and self.queue.ok and self.paths_ok and cadam_ok and routes_ok
                and entrants_ok)


def classify_secret_value(value: Optional[str]) -> str:
    """Classify a secret's value WITHOUT retaining or returning it."""

    if value is None:
        return SECRET_MISSING
    stripped = value.strip()
    if not stripped or _PLACEHOLDER_PATTERN.match(stripped):
        return SECRET_PLACEHOLDER
    return SECRET_PRESENT


def parse_secrets_file(path: Path) -> dict[str, str]:
    """Parse a bash-sourceable ``KEY=value`` env file (values stay in-process)."""

    entries: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        entries[key] = value
    return entries


def _bot_mode(entries: Mapping[str, str]) -> bool:
    """Bot sign-in is in use as soon as either bot credential has a value in the file."""

    return any(entries.get(key, "").strip() for key in ("CADAM_BOT_EMAIL", "CADAM_BOT_PASSWORD"))


def required_keys_for(entries: Mapping[str, str]) -> tuple[str, ...]:
    if _bot_mode(entries):
        return tuple(k for k in REQUIRED_SECRET_KEYS if k != "CADAM_ACCESS_TOKEN") + BOT_SECRET_KEYS
    return REQUIRED_SECRET_KEYS


def audit_secrets(
    secrets_path: Path,
    required_keys: Optional[Sequence[str]] = None,
) -> tuple[SecretStatus, ...]:
    if not secrets_path.exists():
        return tuple(SecretStatus(key, SECRET_MISSING) for key in required_keys or REQUIRED_SECRET_KEYS)
    entries = parse_secrets_file(secrets_path)
    required_keys = required_keys or required_keys_for(entries)
    return tuple(
        SecretStatus(key, classify_secret_value(entries.get(key)))
        for key in required_keys
    )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def audit_lock(
    lock_path: Path,
    *,
    pid_alive: Callable[[int], bool] = _pid_alive,
) -> LockStatus:
    """Ownership check only — never mutates or removes the lock."""

    if not lock_path.exists():
        return LockStatus(LOCK_ABSENT)
    try:
        import json

        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        pid = int(payload["pid"])
    except (ValueError, KeyError, TypeError, OSError):
        return LockStatus(LOCK_UNREADABLE)
    return LockStatus(LOCK_ACTIVE if pid_alive(pid) else LOCK_STALE, pid=pid)


def audit_queue(queue_path: Path) -> QueueStatus:
    if not queue_path.exists():
        return QueueStatus(ok=False, error=f"queue file missing: {queue_path}")
    try:
        import json

        payload = json.loads(queue_path.read_text(encoding="utf-8"))
        raw_jobs = payload.get("jobs") or []
        jobs = tuple(
            (
                str(job.get("job_id") or f"<unnamed-{index}>"),
                str(job.get("status") or "queued"),
            )
            for index, job in enumerate(raw_jobs)
        )
    except (ValueError, OSError) as exc:
        return QueueStatus(ok=False, error=f"queue unreadable: {exc}")
    blocked = tuple(job_id for job_id, status in jobs if status == "blocked")
    return QueueStatus(ok=not blocked, jobs=jobs, error=None)


def audit_paths(named_paths: Mapping[str, Path]) -> tuple[PathStatus, ...]:
    return tuple(
        PathStatus(name=name, path=Path(path), exists=Path(path).exists())
        for name, path in named_paths.items()
    )


def _probe_transport(method: str, url: str, headers: Mapping[str, str], body: Optional[bytes]) -> bytes:
    """Short-timeout transport so a hung app cannot stall the doctor."""

    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read()


def audit_cadam(
    entries: Mapping[str, str],
    *,
    transport: Optional[HttpTransport] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> CadamStatus:
    """The local CADAM app answers at ``CADAM_BASE_URL`` and the bot can sign in.

    URLs come from the secrets file, then the environment, then the local defaults. Failures are
    reduced to fixed messages; the sign-in token is discarded on receipt.
    """

    env = os.environ if environ is None else environ
    transport = transport or _probe_transport

    def setting(key: str, default: str) -> str:
        return (entries.get(key) or env.get(key) or default).strip()

    base_url = setting("CADAM_BASE_URL", DEFAULT_CADAM_BASE_URL)
    supabase_url = setting("VITE_SUPABASE_URL", DEFAULT_SUPABASE_URL)
    try:
        transport("GET", base_url, {}, None)
    except urllib.error.HTTPError as exc:
        if exc.code >= 500:
            return CadamStatus(
                base_url, False, False, f"CADAM server at {base_url} answered HTTP {exc.code} ({CADAM_START_HINT})"
            )
        # 4xx (e.g. 404 on the root route) still proves the app is listening
    except (OSError, ValueError):
        return CadamStatus(base_url, False, False, f"CADAM server not reachable at {base_url} ({CADAM_START_HINT})")
    if not _bot_mode(entries):
        return CadamStatus(base_url, True, True, f"CADAM server up at {base_url} (static-token mode; no sign-in)")
    try:
        sign_in(
            supabase_url,
            entries.get("VITE_SUPABASE_ANON_KEY", ""),
            entries.get("CADAM_BOT_EMAIL", ""),
            entries.get("CADAM_BOT_PASSWORD", ""),
            transport,
        )
    except CadamAuthError as exc:
        return CadamStatus(
            base_url, True, False, f"{exc} against {supabase_url}: check the bot credentials and that Supabase is up"
        )
    return CadamStatus(base_url, True, True, f"CADAM server up at {base_url}; bot sign-in succeeded")


def queued_cadam_entrants(queue_path: Path) -> list[dict]:
    """Distinct (route, runtime, model) of the CADAM entrants in queued/running jobs, in queue order."""
    try:
        import json

        jobs = json.loads(Path(queue_path).read_text(encoding="utf-8")).get("jobs") or []
    except (OSError, ValueError, AttributeError):
        return []
    seen, found = set(), []
    for job in jobs:
        if not isinstance(job, Mapping) or str(job.get("status") or "queued") not in {"queued", "running"}:
            continue
        for entrant in job.get("entrants") or []:
            if not isinstance(entrant, Mapping) or entrant.get("kind") != "cadam":
                continue
            key = (
                str(entrant.get("route") or cadam_routes.ROUTE_OPENROUTER),
                str(entrant.get("runtime") or ""),
                str(entrant.get("model_id") or ""),
            )
            if key not in seen:
                seen.add(key)
                found.append({"route": key[0], "runtime": key[1], "model_id": key[2]})
    return found


def audit_routes(
    queue_path: Path,
    entries: Mapping[str, str],
    *,
    environ: Optional[Mapping[str, str]] = None,
    run: cadam_routes.Run = cadam_routes.default_run,
    http_get: cadam_routes.HttpGet = cadam_routes.default_http_get,
    which: Callable[[str], Optional[str]] = cadam_routes.which_binary,
    repo_root: Path = cadam_routes.REPO_ROOT,
) -> tuple[RouteStatus, ...]:
    """Route-specific readiness for every CADAM entrant queued. Read-only; no model call is made."""
    env = os.environ if environ is None else environ

    def setting(key: str, default: str = "") -> str:
        return (entries.get(key) or env.get(key) or default).strip()

    statuses = []
    for entrant in queued_cadam_entrants(queue_path):
        route, runtime, model = entrant["route"], entrant["runtime"], entrant["model_id"]
        label = cadam_routes.route_label(route, runtime, model)
        checks: list[tuple[bool, str]] = []
        cadam_dir = Path(setting("CADAM_DIR", cadam_routes.DEFAULT_CADAM_DIR))
        if route == cadam_routes.ROUTE_OPENROUTER:
            status = classify_secret_value(entries.get("OPENROUTER_API_KEY") or env.get("OPENROUTER_API_KEY"))
            checks.append((status == SECRET_PRESENT, f"OPENROUTER_API_KEY: {status}"))
            if model.startswith("anthropic/"):
                pinned = cadam_routes.anthropic_provider_pinned(cadam_dir)
                checks.append((pinned, "OpenRouter Anthropic provider pin "
                               + ("configured (Anthropic preferred, fallbacks enabled)" if pinned else
                                  "MISSING or drifted; apply docs/cadam/local-patches/0001-0004 in order")))
            listed = cadam_routes.picker_has_model(cadam_dir, model)
            if listed is None:
                checks.append((False, f"CADAM clone or model catalog not found at {cadam_dir} (set CADAM_DIR)"))
            else:
                checks.append((listed, f"model {model} {'is in' if listed else 'is NOT in'} CADAM's picker"))
        elif route == cadam_routes.ROUTE_CLI_BRIDGE:
            checks.extend(_bridge_checks(setting, entries, env, runtime, model, run, http_get, which, repo_root))
        else:
            checks.append((False, f"unknown CADAM route {route!r} (use openrouter or cli-bridge)"))
        statuses.append(RouteStatus(label, tuple(checks)))
    return tuple(statuses)


def _bridge_checks(setting, entries, env, runtime, model, run, http_get, which, repo_root):
    checks: list[tuple[bool, str]] = []
    if runtime not in cadam_routes.RUNTIMES:
        return [(False, f"runtime {runtime!r} is not claude or codex")]
    key_value = entries.get(cadam_routes.DEFAULT_BRIDGE_CONFIG_KEY_ENV) or env.get(cadam_routes.DEFAULT_BRIDGE_CONFIG_KEY_ENV)
    key_status = classify_secret_value(key_value)
    if key_status == SECRET_PRESENT:
        too_short = cadam_routes.key_problem(key_value)
        key_status = too_short or SECRET_PRESENT  # same rule the runner and the bridge enforce; the value is never shown
    checks.append((key_status == SECRET_PRESENT, f"{cadam_routes.DEFAULT_BRIDGE_CONFIG_KEY_ENV}: {key_status}"))
    config_path = Path(setting("CADAM_CLI_BRIDGE_CONFIG"))
    settings = None
    if not setting("CADAM_CLI_BRIDGE_CONFIG") or not config_path.is_file():
        checks.append((False, "CADAM_CLI_BRIDGE_CONFIG does not point at a bridge config file"))
    else:
        try:
            settings = cadam_routes.read_bridge_config(config_path)
        except (OSError, ValueError, AttributeError):
            checks.append((False, "bridge config is not valid JSON"))
    if settings is not None:
        problems = cadam_routes.bridge_config_problems(settings)
        checks.extend((False, problem) for problem in problems)
        mapped = settings.models.get(model)
        if mapped is None:
            checks.append((False, f"model {model} is not mapped in the bridge config"))
        elif mapped.get("runtime") != runtime:
            checks.append((False, f"model {model} is mapped to runtime {mapped.get('runtime')}, not {runtime}"))
        elif str(mapped.get("cli_model") or "").strip():
            checks.append((True, f"model {model} -> {runtime} {mapped.get('cli_model')}"))
        else:
            checks.append((False, f"model {model} has no cli_model in the bridge config"))
        binary = settings.binary(runtime)
        if which(binary) is None and not Path(binary).is_file():
            checks.append((False, f"{runtime} CLI ({binary}) not found on PATH"))
        else:
            login = ["claude", "auth", "status"] if runtime == "claude" else ["codex", "login", "status"]
            login[0] = binary
            code, _ = run(login)
            checks.append((code == 0, f"{runtime} CLI {'is logged in' if code == 0 else 'is NOT logged in (run the CLI login)'}"
                           f" ({cadam_routes.cli_version(binary, run)})"))
        listening = http_get(settings.health_url) == 200 and not problems
        startable = (repo_root / "tools" / "cadam_cli_bridge" / "__init__.py").is_file() and not problems
        checks.append((listening or startable,
                       "bridge is already listening" if listening else
                       "bridge will be started by the runner" if startable else
                       "bridge cannot run with this config (see above)" if problems else
                       "bridge is not listening and tools/cadam_cli_bridge is missing"))
    bridge_cadam = setting("CADAM_BRIDGE_BASE_URL", cadam_routes.DEFAULT_BRIDGE_CADAM_URL)
    status = http_get(bridge_cadam)
    up = 0 < status < 500
    checks.append((up, f"bridge-mode CADAM {'answers' if up else 'is not reachable'} at {bridge_cadam}"
                       + ("" if up else " (start it with CADAM_PROVIDER_OVERRIDE=cli-bridge; see docs/cadam/LOCAL_SETUP.md)")))
    return checks


SOLIDWORKS_START_HINT = (r"start it: powershell -File C:\Users\Tony\Documents\GitHub\StudioPipeline-hwe"
                         r"\scripts\start_solidworks_bridge.ps1 (SolidWorks open with a blank part)")
FUSION_UNATTENDED_HINT = ("Restart the Luthier Bridge add-in with LUTHIER_UNATTENDED=1 and HWE_FUSION_TOKEN set "
                          "(StudioPipeline/hwe#544).")
FUSION_START_HINT = "start it: open Fusion with the Luthier Bridge add-in running (listener on 8766)"


def _freecad_note(entries: Mapping[str, str], env: Mapping[str, str]) -> tuple[bool, str]:
    from .freecad_backend import APPIMAGE_SHA256, FREECAD_VERSION

    value = (entries.get("MAKERBENCH_FREECAD_APPDIR") or env.get("MAKERBENCH_FREECAD_APPDIR") or "").strip()
    if not value:
        return False, "MAKERBENCH_FREECAD_APPDIR is not set (install: python scripts/install_freecad.py <dir>)"
    appdir = Path(value)
    marker = appdir / ".makerbench-appimage-sha256"
    if not (appdir / "AppRun").is_file() or not marker.is_file() or marker.read_text().strip() != APPIMAGE_SHA256:
        return False, f"FreeCAD AppDir at {appdir} is not the pinned {FREECAD_VERSION} extraction"
    return True, f"FreeCAD {FREECAD_VERSION} AppDir at {appdir} (checksum marker matches)"


def audit_entrants(
    queue_path: Path,
    entries: Mapping[str, str],
    *,
    cadam: Optional[CadamStatus],
    environ: Optional[Mapping[str, str]] = None,
    which: Callable[[str], Optional[str]] = shutil.which,
    ping: Optional[Callable[[object], bool]] = None,
    routes: Sequence[RouteStatus] = (),
    connector_root: Optional[Path] = None,
    mcp_probe: Optional[Callable[..., tuple[bool, str]]] = None,
    unattended: Optional[Callable[[object], Optional[bool]]] = None,
    part_document: Optional[Callable[[object], tuple[bool, str]]] = None,
) -> EntrantsStatus:
    """GO/NO-GO per entrant of the job the runner would claim next (read-only, nothing runs).

    A live entrant whose bridge answers is also probed through the driver's own MCP launch
    (``connector_mcp_probe``: the generated config, its env expansion, a ``ping`` tool call), since a
    raw ping passing did not stop the #663 Fusion driver failing. A Fusion entrant also needs the
    bridge in unattended mode (hwe#544), or every ``confirm`` waits for a human. Both follow the
    transport: a caller that injects ``ping`` skips them unless it injects them too.
    """
    from . import code_cad_providers as providers
    from .live_cad_runner import (
        LiveCadConfig,
        _MCP_MODULES,
        connector_available,
        connector_part_document,
        connector_mcp_probe,
        connector_unattended,
        driver_command,
        driver_route,
    )
    from .nightly_cad import NightlyJob, route_label

    env = os.environ if environ is None else environ
    if ping is None:
        mcp_probe = mcp_probe or connector_mcp_probe
        unattended = unattended or connector_unattended
    ping = ping or connector_available
    part_document = part_document or connector_part_document
    try:
        payload = json.loads(Path(queue_path).read_text(encoding="utf-8"))
        raw = next((job for job in payload.get("jobs") or [] if str(job.get("status") or "queued") in {"queued", "running"}), None)
    except (OSError, ValueError, AttributeError):
        return EntrantsStatus(None, ("queue unreadable",), job_ok=False)
    if raw is None:
        return EntrantsStatus(None, ("no queued job",), job_ok=True)
    job = NightlyJob.from_dict(raw)
    notes, job_ok = [], True
    try:
        job.validate()
        notes.append(f"{job.instrument_id}, {job.deliverable} part, reference {Path(job.reference_image).name}")
    except ValueError as exc:
        notes.append(str(exc))
        job_ok = False
    paid = sum(entrant.max_cost_usd for entrant in job.entrants)
    if paid > job.budget_usd:
        notes.append(f"paid ceilings ${paid:.2f} exceed the night's ${job.budget_usd:.2f} budget: the last paid entrant would be refused")
        job_ok = False
    else:
        notes.append(f"worst-case paid spend ${paid:.2f} of a ${job.budget_usd:.2f} budget")
    live = [entrant for entrant in job.entrants if entrant.kind == "live"]
    if live:
        order = " then ".join("SolidWorks" if e.backend == "solidworks-live" else "Fusion" for e in live)
        notes.append(f"live CAD runs one at a time on one seat: {order}")

    def bridge_env() -> dict[str, str]:
        keys = ("HWE_SW_HOST", "HWE_SW_PORT", "HWE_SW_TOKEN", "HWE_FUSION_HOST", "HWE_FUSION_PORT", "HWE_FUSION_TOKEN")
        return {key: (entries.get(key) or env.get(key) or "") for key in keys if entries.get(key) or env.get(key)}

    checks = []
    for entrant in job.entrants:
        lines, ok = [], True
        try:
            route = route_label(entrant)
        except ValueError as exc:
            checks.append(EntrantCheck(entrant.entrant_id, "unknown route", False, (str(exc),)))
            continue
        if entrant.kind == "cadam":
            # The route's own readiness (#1102: key or bridge, model, CLI login) comes from audit_routes.
            label = cadam_routes.route_label(entrant.route, entrant.runtime, entrant.model_id)
            route_status = next((item for item in routes if item.label == label), None)
            if route_status is not None:
                ok = route_status.ok
                lines.extend(text for passed, text in route_status.checks if not passed)
            if entrant.route == cadam_routes.ROUTE_CLI_BRIDGE:
                lines.append(f"model {entrant.model_id} via CADAM's CLI bridge ({entrant.runtime} subscription)")
            else:
                lines.append(f"model {entrant.model_id} via the local CADAM app")
                if cadam is None or not cadam.ok:
                    ok = False
                    lines.append(cadam.message if cadam is not None else "CADAM app not checked")
                if entrant.max_cost_usd <= 0:
                    ok = False
                    lines.append("a paid entrant needs a max_cost_usd ceiling")
                else:
                    lines.append(f"ceiling ${entrant.max_cost_usd:.2f}")
        elif entrant.kind == "arena":
            provider, cli_model = providers.cli_model_for_model_id(entrant.model_id)
            model = cli_model or "CLI default"
            binary = providers.CLI_BINARIES.get(provider)
            lines.append(f"{provider} CLI, model {model}, backend {entrant.backend}, context {entrant.context_tier}")
            problem = providers.claude_cli_model_problem(cli_model) if provider == "claude" else None
            if problem:
                ok = False  # the CLI exits rc=1 unrecognized_model (dry night, #663)
                lines.append(problem)
            if binary and which(binary) is None:
                ok = False
                lines.append(f"{binary} is not on PATH")
            if entrant.backend == "freecad":
                freecad_ok, note = _freecad_note(entries, env)
                ok = ok and freecad_ok
                lines.append(note)
        elif entrant.kind == "live":
            runtime, model = driver_route(entrant.model_id)
            connector = "hwe-fusion" if entrant.backend == "fusion-live" else "hwe-solidworks"
            lines.append(f"driver {runtime} --model {model}, connector {connector}")
            problem = providers.claude_cli_model_problem(model) if runtime == "claude" else None
            if problem:
                ok = False
                lines.append(problem)
            if which(runtime) is None:
                ok = False
                lines.append(f"{runtime} is not on PATH")
            config = LiveCadConfig(backend=entrant.backend, driver_model=model, driver_runtime=runtime,
                                   connector=connector, env=bridge_env(),
                                   **({"connector_cwd": Path(connector_root).as_posix()} if connector_root else {}))
            if runtime == "codex" and connector == "hwe-solidworks":
                # Inspect the real generated command without launching a driver or writing config.
                argv = driver_command(config, Path("."))
                overrides = dict(argv[i + 1].split("=", 1) for i, token in enumerate(argv)
                                 if token == "-c" and "=" in argv[i + 1])
                approved = overrides.get(f"mcp_servers.{connector}.default_tools_approval_mode") == '"approve"'
                sandboxed = ("--sandbox" in argv and argv[argv.index("--sandbox") + 1] == "workspace-write"
                             and "--dangerously-bypass-approvals-and-sandbox" not in argv)
                ok = ok and approved and sandboxed
                lines.append("hwe-solidworks tools pre-approved; workspace-write sandbox retained"
                             if approved and sandboxed else
                             "Codex config must pre-approve hwe-solidworks tools and retain workspace-write sandbox")
            shim = Path(config.connector_cwd) / (_MCP_MODULES[connector].replace(".", "/") + ".py")
            if not shim.is_file():
                ok = False
                lines.append(f"MCP shim missing: {shim}")
            token_key = "HWE_SW_TOKEN" if connector == "hwe-solidworks" else "HWE_FUSION_TOKEN"
            if connector == "hwe-solidworks" and not config.env.get(token_key):
                ok = False  # the SolidWorks bridge refuses unauthenticated calls
                lines.append(f"{token_key} is not set in the secrets file or the environment (the bridge requires it)")
            if ping(config):
                lines.append(f"{connector} bridge answered an authenticated ping")
                if connector == "hwe-solidworks":
                    part_open, reason = part_document(config)
                    ok = ok and part_open
                    lines.append(reason)
                if mcp_probe is not None:
                    launched, message = mcp_probe(config, environ={**env, **config.env})
                    ok = ok and launched
                    lines.append(message)
                if unattended is not None and connector == "hwe-fusion":
                    mode = unattended(config)
                    if mode is True:
                        lines.append("Fusion bridge is in unattended mode (confirm applies without a prompt)")
                    else:
                        ok = False
                        lines.append("Fusion bridge is NOT in unattended mode"
                                     + (" (it does not report the mode; update the Luthier Bridge add-in)" if mode is None else "")
                                     + ": every confirm would wait for a human. " + FUSION_UNATTENDED_HINT)
            else:
                ok = False
                host_key = "HWE_SW_HOST" if connector == "hwe-solidworks" else "HWE_FUSION_HOST"
                host = config.env.get(host_key, "127.0.0.1")
                lines.append(f"{connector} bridge did not answer at {host} "
                             f"({SOLIDWORKS_START_HINT if connector == 'hwe-solidworks' else FUSION_START_HINT}; "
                             "from WSL, 127.0.0.1 reaches Windows only with mirrored networking)")
        checks.append(EntrantCheck(entrant.entrant_id, route, ok, tuple(lines)))
    return EntrantsStatus(job.job_id, tuple(notes), job_ok, tuple(checks))


def build_report(
    *,
    secrets_path: Path,
    lock_path: Path,
    queue_path: Path,
    named_paths: Mapping[str, Path],
    required_keys: Optional[Sequence[str]] = None,
    pid_alive: Callable[[int], bool] = _pid_alive,
    cadam_transport: Optional[HttpTransport] = None,
    environ: Optional[Mapping[str, str]] = None,
    route_run: cadam_routes.Run = cadam_routes.default_run,
    route_http_get: cadam_routes.HttpGet = cadam_routes.default_http_get,
    route_which: Callable[[str], Optional[str]] = cadam_routes.which_binary,
    check_entrants: bool = False,
    which: Callable[[str], Optional[str]] = shutil.which,
    bridge_ping: Optional[Callable[[object], bool]] = None,
    bridge_mcp_probe: Optional[Callable[..., tuple[bool, str]]] = None,
    bridge_unattended: Optional[Callable[[object], Optional[bool]]] = None,
) -> PreflightReport:
    entries = parse_secrets_file(secrets_path) if secrets_path.exists() else {}
    cadam = audit_cadam(entries, transport=cadam_transport, environ=environ)
    routes = audit_routes(queue_path, entries, environ=environ, run=route_run, http_get=route_http_get, which=route_which)
    return PreflightReport(
        secrets=audit_secrets(secrets_path, required_keys),
        lock=audit_lock(lock_path, pid_alive=pid_alive),
        queue=audit_queue(queue_path),
        paths=audit_paths(named_paths),
        cadam=cadam,
        routes=routes,
        entrants=(audit_entrants(queue_path, entries, cadam=cadam, environ=environ, which=which, ping=bridge_ping,
                                 routes=routes, mcp_probe=bridge_mcp_probe,
                                 unattended=bridge_unattended)
                  if check_entrants else None),
    )


def render_report_lines(report: PreflightReport) -> list[str]:
    """Human-readable, redaction-safe report lines (no secret values, ever)."""

    lines: list[str] = ["nightly CAD arena preflight"]
    lines.append(f"[{'PASS' if report.secrets_ok else 'FAIL'}] secrets")
    for item in report.secrets:
        lines.append(f"  {item.key}: {item.status}")
    if report.cadam is not None:
        lines.append(f"[{'PASS' if report.cadam.ok else 'FAIL'}] cadam")
        lines.append(f"  {report.cadam.message}")
    for route in report.routes:
        lines.append(f"[{'PASS' if route.ok else 'FAIL'}] route: {route.label}")
        lines.extend(f"  [{'ok' if ok else 'FAIL'}] {text}" for ok, text in route.checks)
    lock = report.lock
    lock_detail = f" pid={lock.pid}" if lock.pid is not None else ""
    lines.append(f"[{'PASS' if lock.ok else 'FAIL'}] lock: {lock.status}{lock_detail}")
    queue = report.queue
    lines.append(f"[{'PASS' if queue.ok else 'FAIL'}] queue")
    if queue.error:
        lines.append(f"  {queue.error}")
    for job_id, status in queue.jobs:
        marker = " <-- blocked" if status == "blocked" else ""
        lines.append(f"  {job_id}: {status}{marker}")
    lines.append(f"[{'PASS' if report.paths_ok else 'FAIL'}] paths")
    for item in report.paths:
        state = "exists" if item.exists else "MISSING"
        lines.append(f"  {item.name}: {item.path} ({state})")
    if report.entrants is not None:
        status = report.entrants
        lines.append(f"[{'PASS' if status.ok else 'FAIL'}] entrants: job {status.job_id or '-'}")
        for note in status.job_notes:
            lines.append(f"  {note}")
        for item in status.entrants:
            lines.append(f"  {'GO   ' if item.ok else 'NO-GO'} {item.entrant_id} · {item.route}")
            for note in item.notes:
                lines.append(f"        {note}")
    lines.append(f"preflight: {'GO' if report.ok else 'NO-GO'}")
    return lines
