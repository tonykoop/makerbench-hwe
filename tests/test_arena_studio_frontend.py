"""Static contract tests for the rebuilt Arena Studio frontend.

These run in the normal CI matrix without a browser: the shell and every module
it imports resolve locally, nothing reaches the network, vendored code matches
its recorded hashes, no raw-HTML injection APIs are used, the modules parse,
their unit tests pass under Node, and theme colors meet WCAG contrast.
Real-browser checks live in tests/test_arena_studio_browser.py.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from makerbench.arena_studio import create_studio_app

REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC = REPO_ROOT / "makerbench" / "arena_studio" / "static"
APP_DIR = STATIC / "app"
VENDOR_DIR = STATIC / "vendor"
JS_TESTS = REPO_ROOT / "tests" / "js"

# XML namespace identifiers inside vendored Preact: strings, never fetched.
ALLOWED_URL_STRINGS = {
    "http://www.w3.org/1998/Math/MathML",
    "http://www.w3.org/1999/xhtml",
    "http://www.w3.org/2000/svg",
}

RAW_HTML_APIS = re.compile(
    r"\b(innerHTML|outerHTML|insertAdjacentHTML|dangerouslySetInnerHTML|document\.write)\b"
)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"instruments": []}), encoding="utf-8")
    app = create_studio_app(registry_path=registry, repo_root=tmp_path)
    return TestClient(app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})


def _shipped_files(*suffixes: str) -> list[Path]:
    return sorted(p for p in STATIC.rglob("*") if p.is_file() and p.suffix in suffixes)


def _import_map(index_html: str) -> dict[str, str]:
    block = re.search(r'<script type="importmap">(.*?)</script>', index_html, re.DOTALL)
    assert block, "index.html must declare an import map"
    return json.loads(block.group(1))["imports"]


def _url_for(path: Path) -> str:
    return "/static/" + path.relative_to(STATIC).as_posix()


def test_root_serves_the_studio_shell(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<script type="module" src="/static/app/main.js"></script>' in response.text
    assert set(_import_map(response.text)) == {"preact", "preact/hooks", "htm"}


def test_every_shell_reference_and_module_import_resolves(client: TestClient):
    index = client.get("/").text
    imports = _import_map(index)
    pending = [m for m in re.findall(r'(?:src|href)="(/static/[^"]+)"', index)]
    pending += list(imports.values())
    seen: set[str] = set()
    while pending:
        url = pending.pop()
        if url in seen:
            continue
        seen.add(url)
        response = client.get(url)
        assert response.status_code == 200, url
        if url.endswith(".js"):
            assert "javascript" in response.headers["content-type"], url
            base = url.rsplit("/", 1)[0]
            for spec in re.findall(r'(?:from|import)\s*"([^"]+)"', response.text):
                if spec.startswith("./") or spec.startswith("../"):
                    parts = (base + "/" + spec).split("/")
                    resolved: list[str] = []
                    for part in parts:
                        if part == "..":
                            resolved.pop()
                        elif part not in ("", "."):
                            resolved.append(part)
                    pending.append("/" + "/".join(resolved))
                else:
                    assert spec in imports, f"{url} imports unmapped bare specifier {spec!r}"
        elif url.endswith(".css"):
            assert "text/css" in response.headers["content-type"], url
    assert "/static/app/screens/runs.js" in seen


def test_nothing_shipped_reaches_the_network():
    offenders = []
    for path in _shipped_files(".html", ".css", ".js", ".json"):
        for url in re.findall(r"https?://[^\s\"'`)<>]+", path.read_text(encoding="utf-8")):
            if url not in ALLOWED_URL_STRINGS:
                offenders.append(f"{path.relative_to(STATIC)}: {url}")
    assert offenders == []


def test_vendored_modules_match_their_recorded_hashes():
    manifest = json.loads((VENDOR_DIR / "VENDOR.json").read_text(encoding="utf-8"))
    recorded = {entry["file"] for entry in manifest["files"]}
    shipped = {p.name for p in VENDOR_DIR.glob("*.js")}
    assert recorded == shipped
    for entry in manifest["files"]:
        digest = hashlib.sha256((VENDOR_DIR / entry["file"]).read_bytes()).hexdigest()
        assert digest == entry["sha256"], entry["file"]
        assert (VENDOR_DIR / entry["license_file"]).is_file()


def test_studio_code_never_uses_raw_html_apis():
    offenders = []
    for path in [STATIC / "index.html", *APP_DIR.rglob("*.js")]:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if RAW_HTML_APIS.search(line):
                offenders.append(f"{path.relative_to(STATIC)}:{number}: {line.strip()}")
    assert offenders == []


def test_studio_modules_parse():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    failures = []
    for path in sorted(APP_DIR.rglob("*.js")):
        result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
        if result.returncode != 0:
            failures.append(f"{path.relative_to(STATIC)}\n{result.stderr}")
    assert failures == []


def test_studio_module_unit_tests_pass():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, "--test", *sorted(str(p) for p in JS_TESTS.glob("*.test.mjs"))],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _tokens(css_block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})\s*;", css_block))


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


CONTRAST_PAIRS = [
    ("ink", "ground"),
    ("ink", "surface"),
    ("muted", "ground"),
    ("muted", "surface"),
    ("accent", "ground"),
    ("accent", "surface"),
    ("on-accent", "accent"),
    ("brass", "ground"),
    ("brass", "surface"),
    ("danger", "ground"),
    ("danger", "surface"),
    ("ok", "surface"),
    ("stage-ink", "stage"),
]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_theme_colors_meet_wcag_aa(theme: str):
    css = (STATIC / "studio.css").read_text(encoding="utf-8")
    light = _tokens(css.split("@media (prefers-color-scheme: dark)")[0])
    dark_block = css.split("@media (prefers-color-scheme: dark)")[1].split("\n}\n")[0]
    tokens = light if theme == "light" else {**light, **_tokens(dark_block)}
    failing = [
        f"{fg} on {bg}: {_contrast(tokens[fg], tokens[bg]):.2f}"
        for fg, bg in CONTRAST_PAIRS
        if _contrast(tokens[fg], tokens[bg]) < 4.5
    ]
    assert failing == []
    # The focus ring must stand out from the ground it sits on (WCAG 1.4.11).
    assert _contrast(tokens["focus"], tokens["ground"]) >= 3.0
