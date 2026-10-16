"""Guard: site/assets/app.js must not declare the same function name twice in one scope.

Two PRs that each added a function named ``effortLabel`` merged cleanly as text,
but JavaScript hoisting let the later declaration silently replace the earlier
one, which broke the plain-language labels on main (#1055 + #1060). This test
fails on that pattern before it reaches main.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / "site" / "assets" / "app.js"

# app.js wraps everything in one IIFE; its top-level declarations are indented two spaces.
TOP_LEVEL_FUNCTION = re.compile(r"^  function ([A-Za-z_$][\w$]*)\s*\(", re.MULTILINE)


def test_app_js_top_level_function_names_are_unique():
    names = TOP_LEVEL_FUNCTION.findall(APP_JS.read_text(encoding="utf-8"))
    assert names, "no top-level functions found; has app.js's structure changed?"
    duplicates = {name: count for name, count in Counter(names).items() if count > 1}
    assert not duplicates, f"duplicate top-level function declarations in app.js: {duplicates}"
