"""Gamified Human Intervention Index badges (mb#106).

The single "Built with MakerBench" shields badge (mb#99, ``site/build_data.py``)
says *a* model scored *something*. This module adds the **HII badge classes** —
gamified, dynamically-rendered badges keyed to *how autonomously* a passing run
was produced, so a contributor's README can flex the human-AI stack behind a
verified row:

    L0 — Pure Autonomy   (emerald) — 100% of tool-calls via the API loop, zero
                                      human keystrokes/geometry edits on a complex
                                      seed. Flex: AI-systems architect.
    L1 — Elite Copilot   (blue)    — passing grade with light NL steering on an
                                      enterprise-grade family. Flex: cyborg engineer.
    L2 — Master Triage   (purple)  — cleared a volatile multi-constraint challenge
                                      with heavy human triage. Flex: physical-domain
                                      mastery.

The badge *class* equals the run's HII ``highest_level`` — L0/L1/L2 are not a
score (an L0 row is more impressive than an L2 one, but all three are valid
disclosed rows; see ``schema.HumanInterventionIndex``).

**Anti-fake.** A badge's authority comes from the signed ``.mbc`` certificate
(``makerbench.certificate``) plus verified leaderboard state — never self-report.
:func:`badge_from_certificate` re-verifies the HMAC over the canonical payload
before reading the tier off it: a hand-edited score/ratio/tier breaks the
signature and the badge degrades to a grey *unverified* state. The leaderboard's
``verification_status`` is treated as an independent gate on top of the signature,
so an authentic-but-unverified row also stays grey.

**Dynamic endpoint.** :func:`serve_badge` is the server-agnostic core of the
``/api/badge?user=<u>`` endpoint described in the issue. It is pure stdlib and
takes a ``lookup`` callback so a Hugging Face Space / WSGI / Flask backend can
mount it without this module knowing how rows are stored:

    from makerbench.hii_badge import serve_badge
    status, content_type, body = serve_badge(environ["QUERY_STRING"], key=KEY,
                                             lookup=latest_verified_row)

This module is additive and self-contained; it imports only the read-only
certificate verifier and the HII level type, and alters no grading path.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any, Callable, Optional, Union
from urllib.parse import parse_qs

from pydantic import BaseModel, Field

from makerbench.certificate import MbcCertificate, _coerce_certificate, verify_mbc
from makerbench.schema import HiiLevel

BADGE_SCHEMA_VERSION = "0.1"

# Leaderboard verification states that count as "verified" for badge purposes.
# ``unverified`` and ``rejected`` never earn a flex class. Mirrors
# schema.VerificationStatus without importing the Literal (kept additive).
VERIFIED_STATUSES = frozenset({"public-regrade-verified", "official-heldout-verified"})

# Neutral grey used for every un-earned / unverified badge.
UNVERIFIED_COLOR = "#6b7280"


@dataclass(frozen=True)
class HiiBadgeClass:
    """Static descriptor for one gamified HII badge tier."""

    level: HiiLevel
    title: str
    color: str  # hex, used for the right-hand SVG segment + shields color
    flex: str  # the contributor "flex" tagline


# Keyed by HII highest_level. Colors: emerald / blue / purple per the issue.
BADGE_CLASSES: dict[HiiLevel, HiiBadgeClass] = {
    "L0": HiiBadgeClass(
        level="L0",
        title="Pure Autonomy",
        color="#10b981",  # emerald
        flex="AI-systems architect",
    ),
    "L1": HiiBadgeClass(
        level="L1",
        title="Elite Copilot",
        color="#3b82f6",  # blue
        flex="cyborg engineer",
    ),
    "L2": HiiBadgeClass(
        level="L2",
        title="Master Triage",
        color="#a855f7",  # purple
        flex="physical-domain mastery",
    ),
}


def badge_class_for(level: HiiLevel) -> HiiBadgeClass:
    """Return the static badge descriptor for an HII ``highest_level``."""
    try:
        return BADGE_CLASSES[level]
    except KeyError as exc:  # pragma: no cover - guards against schema drift
        raise ValueError(f"unknown HII level: {level!r}") from exc


class HiiBadge(BaseModel):
    """A renderable HII badge for one contributor's latest run.

    ``earned`` is the anti-fake gate: it is True only when the run *passed*, its
    ``.mbc`` signature verified, and the leaderboard row reached a verified
    status. An un-earned badge still renders — as a grey *unverified* badge — so
    the endpoint always returns something rather than a broken image.
    """

    schema_version: str = BADGE_SCHEMA_VERSION
    user: str
    highest_level: HiiLevel = "L0"
    autonomy_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    score: Optional[int] = None
    passed: bool = False
    signature_verified: bool = False
    leaderboard_verified: bool = False

    @property
    def earned(self) -> bool:
        """True when the badge may show its flex class (all gates passed)."""
        return self.passed and self.signature_verified and self.leaderboard_verified

    @property
    def badge_class(self) -> HiiBadgeClass:
        """The class descriptor this badge corresponds to (by HII tier)."""
        return badge_class_for(self.highest_level)

    @property
    def color(self) -> str:
        """Render color: the tier color when earned, else neutral grey."""
        return self.badge_class.color if self.earned else UNVERIFIED_COLOR

    @property
    def message(self) -> str:
        """Right-hand badge text, e.g. ``L0 Pure Autonomy · 3/4`` or ``unverified``."""
        if not self.earned:
            return "unverified"
        cls = self.badge_class
        head = f"{cls.level} {cls.title}"
        if self.score is not None:
            return f"{head} · {self.score}/4"
        return head


def make_badge(
    *,
    user: str,
    highest_level: HiiLevel = "L0",
    autonomy_ratio: Optional[float] = None,
    score: Optional[int] = None,
    passed: bool = False,
    signature_verified: bool = False,
    leaderboard_verified: bool = False,
) -> HiiBadge:
    """Build an :class:`HiiBadge` from already-resolved fields.

    Prefer :func:`badge_from_certificate` for the anti-fake path; this is the
    plain constructor for callers that have verified the inputs themselves.
    """
    return HiiBadge(
        user=user,
        highest_level=highest_level,
        autonomy_ratio=autonomy_ratio,
        score=score,
        passed=passed,
        signature_verified=signature_verified,
        leaderboard_verified=leaderboard_verified,
    )


def badge_from_certificate(
    source: Union[str, bytes, MbcCertificate, dict[str, Any]],
    key: Union[str, bytes],
    *,
    user: str,
    verification_status: Optional[str] = None,
    expected_nonce: Optional[str] = None,
) -> HiiBadge:
    """Issue an HII badge from a ``.mbc`` certificate — the anti-fake path.

    The certificate's HMAC is re-verified over the canonical payload before the
    HII tier/score are read off it, so a hand-edited payload yields an
    ``unverified`` (grey) badge. ``verification_status`` is the leaderboard's
    independent gate: only a status in :data:`VERIFIED_STATUSES` lets the badge
    show its flex class. Malformed certificates degrade gracefully to an
    unverified badge rather than raising.
    """
    signature_verified = verify_mbc(source, key, expected_nonce=expected_nonce)
    try:
        cert = _coerce_certificate(source)
        payload = cert.payload
    except Exception:  # noqa: BLE001 - any parse failure → unverified badge
        return make_badge(user=user)

    level: HiiLevel = payload.hii_highest_level or "L0"  # type: ignore[assignment]
    leaderboard_verified = (verification_status in VERIFIED_STATUSES)
    return make_badge(
        user=user,
        highest_level=level,
        autonomy_ratio=payload.autonomy_ratio,
        score=payload.score,
        passed=payload.passed,
        signature_verified=signature_verified,
        leaderboard_verified=leaderboard_verified,
    )


def badge_shields_payload(badge: HiiBadge) -> dict[str, Any]:
    """A shields.io endpoint payload (schemaVersion 1) for ``badge``.

    Lets the gamified HII badge plug into the same ``img.shields.io/endpoint``
    machinery as the existing per-model badges (``site/build_data.py``), for
    contributors who prefer shields over the self-rendered SVG.
    """
    return {
        "schemaVersion": 1,
        "label": "MakerBench HII",
        "message": badge.message,
        "color": badge.color,
        "namedLogo": "opensourcehardware",
        "labelColor": "15181d",
    }


# ---------------------------------------------------------------------------
# Self-contained SVG rendering (no external shields call — anti-fake/offline).
# ---------------------------------------------------------------------------

# ~6px per char at 11px DejaVu Sans is a good-enough monospace-ish width estimate
# for a flat shields-style badge; padding keeps text off the rounded corners.
_CHAR_PX = 6.5
_PAD = 8.0


def _seg_width(text: str) -> float:
    return len(text) * _CHAR_PX + 2 * _PAD


def render_badge_svg(badge: HiiBadge) -> str:
    """Render ``badge`` as a self-contained, flat two-segment SVG string.

    Left segment is the label (dark); right segment is the message in the tier
    color (or grey when unverified). Deterministic for a given badge so the
    bytes are stable across renders. No network or font files required.
    """
    label = "MakerBench HII"
    message = badge.message
    lw = round(_seg_width(label), 1)
    rw = round(_seg_width(message), 1)
    total = round(lw + rw, 1)
    label_color = "#15181d"
    msg_color = badge.color
    label_mid = round(lw / 2, 1)
    msg_mid = round(lw + rw / 2, 1)
    title = f"{label}: {message}"
    esc_label = html.escape(label)
    esc_msg = html.escape(message)
    esc_title = html.escape(title)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{total}" height="20" role="img" '
        f'aria-label="{esc_title}">'
        f"<title>{esc_title}</title>"
        f'<linearGradient id="s" x2="0" y2="100%">'
        f'<stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
        f'<stop offset="1" stop-opacity=".1"/></linearGradient>'
        f'<clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>'
        f'<g clip-path="url(#r)">'
        f'<rect width="{lw}" height="20" fill="{label_color}"/>'
        f'<rect x="{lw}" width="{rw}" height="20" fill="{msg_color}"/>'
        f'<rect width="{total}" height="20" fill="url(#s)"/></g>'
        f'<g fill="#fff" text-anchor="middle" '
        f'font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">'
        f'<text x="{label_mid}" y="15" fill="#010101" fill-opacity=".3">{esc_label}</text>'
        f'<text x="{label_mid}" y="14">{esc_label}</text>'
        f'<text x="{msg_mid}" y="15" fill="#010101" fill-opacity=".3">{esc_msg}</text>'
        f'<text x="{msg_mid}" y="14">{esc_msg}</text>'
        f"</g></svg>"
    )


# ---------------------------------------------------------------------------
# Dynamic endpoint: /api/badge?user=<u>
# ---------------------------------------------------------------------------

# A lookup returns the data the endpoint needs for a user, or None when there is
# no row. Shape: a mapping with a ``certificate`` (.mbc source) + ``key``, and
# optionally ``verification_status`` and ``expected_nonce``.
BadgeLookup = Callable[[str], Optional[dict[str, Any]]]

SVG_CONTENT_TYPE = "image/svg+xml; charset=utf-8"


def serve_badge(
    query_string: str,
    *,
    lookup: BadgeLookup,
    key: Union[str, bytes, None] = None,
) -> tuple[int, str, str]:
    """Server-agnostic core of ``/api/badge?user=<u>``.

    Parses ``user`` from ``query_string``, asks ``lookup`` for that user's latest
    verified row, verifies the certificate, and returns ``(status, content_type,
    svg_body)``. Always returns an SVG (a grey ``unknown``/``unverified`` badge
    on miss or bad signature) so a README image never breaks. The ``key`` here is
    a fallback; a per-row key in the lookup result takes precedence.

    Status codes are advisory for the mounting server: 200 when a flex badge is
    earned, 404 when the user has no row, 200 with a grey badge otherwise.
    """
    params = parse_qs(query_string or "")
    users = params.get("user") or []
    user = users[0].strip() if users else ""
    if not user:
        return 400, SVG_CONTENT_TYPE, render_badge_svg(make_badge(user="unknown"))

    row = lookup(user)
    if not row:
        return 404, SVG_CONTENT_TYPE, render_badge_svg(make_badge(user=user))

    cert_source = row.get("certificate")
    row_key = row.get("key", key)
    if cert_source is None or row_key is None:
        return 404, SVG_CONTENT_TYPE, render_badge_svg(make_badge(user=user))

    badge = badge_from_certificate(
        cert_source,
        row_key,
        user=user,
        verification_status=row.get("verification_status"),
        expected_nonce=row.get("expected_nonce"),
    )
    return 200, SVG_CONTENT_TYPE, render_badge_svg(badge)
