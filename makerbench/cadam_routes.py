"""CADAM model routes for the nightly entrant (#1102, epic #1099).

``openrouter``  the stock CADAM provider path with the OpenRouter key (CADAM at ``CADAM_BASE_URL``).
``cli-bridge``  CADAM started with ``CADAM_PROVIDER_OVERRIDE=cli-bridge`` (a second instance at
                ``CADAM_BRIDGE_BASE_URL``) that talks to ``tools/cadam_cli_bridge``, which runs
                ``claude -p`` or ``codex exec`` on a subscription.

The two are reported as separate entrants and never merged: "CADAM · OpenRouter · <model>" versus
"CADAM via CLI · <runtime> · <model>". Secrets are classified, never printed.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

ROUTE_OPENROUTER = "openrouter"
ROUTE_CLI_BRIDGE = "cli-bridge"
ROUTES = (ROUTE_OPENROUTER, ROUTE_CLI_BRIDGE)
RUNTIMES = ("claude", "codex")

DEFAULT_CADAM_DIR = "/mnt/c/Users/Tony/Documents/GitHub/_meta/CADAM"
DEFAULT_BRIDGE_CADAM_URL = "http://127.0.0.1:3001"
DEFAULT_BRIDGE_CONFIG_KEY_ENV = "CADAM_CLI_BRIDGE_KEY"
MIN_KEY_CHARS = 16  # the bridge itself refuses a shorter shared key
REPO_ROOT = Path(__file__).resolve().parents[1]

Run = Callable[[Sequence[str]], "tuple[int, str]"]
HttpGet = Callable[[str], int]


def route_label(route: str, runtime: str, model_id: str) -> str:
    if route == ROUTE_CLI_BRIDGE:
        return f"CADAM via CLI · {runtime} · {model_id}"
    return f"CADAM · OpenRouter · {model_id}"


def route_name(route: str, runtime: str) -> str:
    return f"CADAM via CLI · {runtime}" if route == ROUTE_CLI_BRIDGE else "CADAM · OpenRouter"


def default_run(argv: Sequence[str]) -> tuple[int, str]:
    """Run a short, non-inferencing CLI command; returns (exit code, stdout). Never raises."""
    try:
        done = subprocess.run(list(argv), capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return done.returncode, done.stdout


def default_http_get(url: str) -> int:
    """HTTP status of a GET (0 when nothing answers)."""
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (OSError, ValueError):
        return 0


def cli_version(binary: str, run: Run = default_run) -> str:
    code, out = run([binary, "--version"])
    line = out.strip().splitlines()[0] if out.strip() else ""
    return line[:80] if code == 0 and line else "unknown"


def anthropic_provider_pinned(cadam_dir: Path) -> bool:
    """Read-only check that the local routing patch is applied at the SDK call.

    Reverse-checking the canonical hunk also checks its surrounding call site;
    a comment or an unused provider setting elsewhere cannot satisfy preflight.
    """
    patch = REPO_ROOT / "docs/cadam/local-patches/0003-openrouter-anthropic-pin.diff"
    code, _ = default_run([
        "git", "-C", str(cadam_dir), "apply", "--reverse", "--check", str(patch),
    ])
    return code == 0


@dataclass(frozen=True)
class BridgeSettings:
    host: str
    port: int
    claude_bin: str
    codex_bin: str
    models: Mapping[str, Mapping[str, str]]

    @property
    def health_url(self) -> str:
        return f"http://{self.host}:{self.port}/health"

    def binary(self, runtime: str) -> str:
        return self.claude_bin if runtime == "claude" else self.codex_bin


def read_bridge_config(path: Path) -> BridgeSettings:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return BridgeSettings(
        host=str(raw.get("host", "127.0.0.1")),
        port=int(raw.get("port", 8787)),
        claude_bin=str(raw.get("claude_bin", "claude")),
        codex_bin=str(raw.get("codex_bin", "codex")),
        models={str(k): {str(a): str(b) for a, b in v.items()} for k, v in (raw.get("models") or {}).items()},
    )


def bridge_config_problems(settings: BridgeSettings) -> list[str]:
    """Why the bridge could not run with this config (the same rules tools/cadam_cli_bridge enforces)."""
    problems = []
    try:
        if not ipaddress.ip_address(settings.host).is_loopback:
            problems.append(f"bridge host {settings.host} is not loopback (the bridge binds loopback only)")
    except ValueError:
        problems.append(f"bridge host {settings.host!r} is not an IP address")
    if not 0 < settings.port < 65536:
        problems.append(f"bridge port {settings.port} is out of range")
    if not settings.models:
        problems.append("the bridge config maps no models")
    for name, spec in settings.models.items():
        if spec.get("runtime") not in RUNTIMES:
            problems.append(f"model {name}: runtime must be claude or codex")
        if not str(spec.get("cli_model") or "").strip():
            problems.append(f"model {name}: cli_model is required")
    return problems


def key_problem(key: Optional[str]) -> Optional[str]:
    """None when the shared key is usable; otherwise a fixed, value-free reason."""
    if not key or not key.strip():
        return "MISSING"
    return None if len(key) >= MIN_KEY_CHARS else f"TOO SHORT (needs at least {MIN_KEY_CHARS} characters)"


def picker_has_model(cadam_dir: Path, model_id: str) -> Optional[bool]:
    """Whether CADAM's model picker lists ``model_id`` (None: the clone or its catalog is not there)."""
    catalog = Path(cadam_dir) / "src" / "lib" / "utils.ts"
    if not catalog.is_file():
        return None
    return re.search(r"id:\s*['\"]" + re.escape(model_id) + r"['\"]", catalog.read_text(encoding="utf-8")) is not None


class BridgeError(RuntimeError):
    """The CLI bridge could not be started."""


class CliBridge:
    """Start the CLI bridge around one CADAM turn and stop it afterwards (reusing one already listening)."""

    def __init__(
        self,
        config_path: Path,
        key: str,
        *,
        command: Optional[Sequence[str]] = None,
        popen: Callable[..., "subprocess.Popen"] = subprocess.Popen,
        http_get: HttpGet = default_http_get,
        sleep: Callable[[float], None] = time.sleep,
        startup_s: float = 30.0,
        cwd: Path = REPO_ROOT,
    ) -> None:
        self.config_path = Path(config_path)
        self.settings = read_bridge_config(self.config_path)
        problems = bridge_config_problems(self.settings)
        if problems:
            raise BridgeError(problems[0])
        self.key = key
        self.command = list(command) if command else [sys.executable, "-m", "tools.cadam_cli_bridge", "--config", str(self.config_path)]
        self.popen, self.http_get, self.sleep, self.startup_s, self.cwd = popen, http_get, sleep, startup_s, cwd
        self.process: Optional["subprocess.Popen"] = None

    @property
    def owned(self) -> bool:
        return self.process is not None

    def __enter__(self) -> "CliBridge":
        if self.http_get(self.settings.health_url) == 200:
            return self  # already running (an operator's bridge): use it, never stop it
        env = {**os.environ, DEFAULT_BRIDGE_CONFIG_KEY_ENV: self.key}
        try:
            self.process = self.popen(self.command, cwd=self.cwd, env=env, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            raise BridgeError(f"cannot start the CLI bridge: {exc.strerror or exc}") from None
        waited = 0.0
        while waited < self.startup_s:
            if self.process.poll() is not None:
                self.process = None
                raise BridgeError("the CLI bridge exited during startup")
            if self.http_get(self.settings.health_url) == 200:
                return self
            self.sleep(0.25)
            waited += 0.25
        self.stop()
        raise BridgeError(f"the CLI bridge did not answer {self.settings.health_url} within {self.startup_s:g}s")

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def __exit__(self, *_exc: object) -> None:
        self.stop()


def bridge_command_from_env(environ: Mapping[str, str]) -> Optional[list[str]]:
    raw = environ.get("CADAM_CLI_BRIDGE_CMD", "").strip()
    return shlex.split(raw) if raw else None


def bridge_settings_for_run(environ: Mapping[str, str]) -> tuple[Path, str]:
    path = environ.get("CADAM_CLI_BRIDGE_CONFIG", "").strip()
    key = environ.get(DEFAULT_BRIDGE_CONFIG_KEY_ENV, "")
    if not path or not Path(path).is_file():
        raise BridgeError("CADAM_CLI_BRIDGE_CONFIG does not point at a bridge config file")
    if key_problem(key):
        raise BridgeError(f"{DEFAULT_BRIDGE_CONFIG_KEY_ENV} is not set to a shared key of at least {MIN_KEY_CHARS} characters")
    return Path(path), key


def which_binary(name: str) -> Optional[str]:
    return shutil.which(name)
