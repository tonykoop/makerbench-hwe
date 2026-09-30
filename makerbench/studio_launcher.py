"""Installed, cwd-independent Studio launcher; optional HTTP imports stay lazy."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import threading
import webbrowser

import typer


def install_hint() -> str:
    root = Path(__file__).resolve().parents[1]
    if (root / "pyproject.toml").is_file() and (Path.cwd() / "makerbench").is_dir():
        return 'pip install -e ".[studio]"'
    return ('uv tool install --force "makerbench-hwe[studio] @ '
            'git+https://github.com/tonykoop/makerbench-hwe"')


def default_registry() -> Path:
    root = Path(__file__).resolve().parents[1]
    source = root / "tasks/code_cad_arena/registry.json"
    return source if source.is_file() else root / "makerbench/arena_studio/data/registry.json"


def open_browser(url: str) -> bool:
    """Use the Windows browser in WSL when a bridge is available."""
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        bridge = shutil.which("wslview")
        if bridge:
            return subprocess.run([bridge, url], check=False).returncode == 0
        powershell = shutil.which("powershell.exe")
        if powershell:
            return subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command",
                 "Start-Process '" + url.replace("'", "''") + "'"], check=False,
            ).returncode == 0
    return webbrowser.open(url)


def studio(
    run_dir: str | None = typer.Option(None, help="Initial run directory."),
    port: int = typer.Option(8080, min=1, max=65535),
    registry: Path | None = typer.Option(None, help="Override the bundled instrument registry."),
    browser: bool = typer.Option(True, "--browser/--no-browser", help="Open the browser when ready."),
    allow_live: bool = typer.Option(False, help="Allow explicit provider-backed launches."),
) -> None:
    """Start Arena Studio and open its local browser window, from any directory."""
    missing = [name for name in ("fastapi", "uvicorn") if importlib.util.find_spec(name) is None]
    if missing:
        typer.echo("Studio needs " + ", ".join(missing) + ". Install with:\n" + install_hint())
        raise typer.Exit(1)
    import uvicorn
    from .arena_studio import create_studio_app

    app = create_studio_app(
        default_run_dir=Path(run_dir) if run_dir else None,
        registry_path=registry or default_registry(),
        repo_root=Path.cwd(),
        allow_live=allow_live,
    )
    url = f"http://127.0.0.1:{port}/"
    typer.echo(f"Arena Studio: {url} (Ctrl+C to stop)")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port))
    stopped = threading.Event()

    def when_ready():
        while not stopped.wait(0.1):
            if server.started:
                try:
                    opened = open_browser(url)
                except (OSError, subprocess.SubprocessError):
                    opened = False
                if not opened:
                    typer.echo(f"Open this URL in your browser: {url}")
                return

    if browser:
        threading.Thread(target=when_ready, daemon=True).start()
    try:
        server.run()
    finally:
        stopped.set()
