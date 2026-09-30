"""Studio static files must not use names that content blockers drop.

Firefox tracking protection and ad blockers such as uBlock block requests whose
path looks like a tracker (for example ``analytics.js``). One blocked ES module
stops the whole Studio app from booting, leaving the "Starting Arena Studio…"
placeholder on screen.
"""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "makerbench" / "arena_studio" / "static"

# Path segments that common filter lists match on.
BLOCKED = re.compile(
    r"(^|[/_.-])(analytics?|tracking|tracker|telemetry|beacon|ads?|advert\w*|pixel|metrics)([/_.-]|$)",
    re.IGNORECASE,
)


def test_static_paths_avoid_blocker_patterns():
    offenders = [
        str(p.relative_to(STATIC))
        for p in STATIC.rglob("*")
        if p.is_file() and BLOCKED.search(str(p.relative_to(STATIC)))
    ]
    assert offenders == [], f"rename these so content blockers don't drop them: {offenders}"
