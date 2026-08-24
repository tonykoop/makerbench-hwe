"""Redact host-absolute filesystem paths out of captured tool output (#684).

Public result bundles embed captured renderer stderr. When OpenSCAD fails to
parse a candidate it names the temporary file it was reading, so the operator's
temp directory — and on Windows their *username* — lands in
``results[*].perception_trace[*].warnings[*]`` and gets committed to a public
repository.

The patterns here are the single source of truth for that class of value: the
runner imports :func:`redact_host_paths` to stop new leaks at capture time, and
``scripts/audit_public_artifacts.py`` imports :func:`find_host_paths` to fail CI
on any that reach committed JSON. Sharing one pattern set is deliberate — a
guard that recognises less than the redactor rewrites (or vice versa) would let
the two drift apart silently.

Canonical form: the whole matched path collapses to ``REDACTION_TOKEN``. The
basename is dropped along with the directory because these are
randomly-named temp files (``tmpr72t7bwb.scad``) whose names carry no
diagnostic value, and keeping any tail would re-introduce a judgement call
about which tails are safe.
"""

from __future__ import annotations

import re


REDACTION_TOKEN = "<redacted-host-path>"

# Trailing character class stops at whitespace and the quoting/punctuation that
# wraps a path inside a prose error message ("Can't parse file '/tmp/x.scad'!").
_TAIL = r"[^\s'\"`<>|,;!)\]]*"
_SEG = r"[^\s'\"`<>|,;!)\]/\\]+"
# A *real* account name, not a documentation placeholder: `/mnt/c/Users/<you>/`
# and `C:\Users\%USERNAME%\` are instructions, not disclosures, and a guard that
# flags them would push docs toward inventing their own evasions.
_USER_SEG = r"(?![<{$%])" + _SEG
_USERS_RE = rf"[\\/]users[\\/]{_USER_SEG}{_TAIL}"

#: Ordered longest-prefix-first: a Windows user path must win over the bare
#: ``/tmp`` rule so ``C:\Users\Tony\AppData\Local\Temp\x.scad`` collapses whole
#: rather than leaving ``C:\Users\Tony\AppData\Local`` behind.
HOST_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    # WSL view of a Windows user directory: /mnt/c/Users/<name>/...
    re.compile(rf"[\\/]mnt[\\/][a-z][\\/]users[\\/]{_USER_SEG}{_TAIL}", re.IGNORECASE),
    # Session-scoped scratch dirs that encode a mangled repo path + session UUID.
    re.compile(rf"[\\/]tmp[\\/]claude-[0-9]+{_TAIL}", re.IGNORECASE),
    # Native Windows user directory, incl. AppData\Local\Temp.
    re.compile(rf"[a-z]:[\\/]users[\\/]{_USER_SEG}{_TAIL}", re.IGNORECASE),
    # POSIX home directories.
    re.compile(rf"[\\/]home[\\/]{_USER_SEG}{_TAIL}", re.IGNORECASE),
    # macOS /Users/<name>/... and its per-user temp store.
    # Guarded against firing inside a URL path such as github.com/users/<login>.
    re.compile(rf"(?<![a-z0-9.]){_USERS_RE}", re.IGNORECASE),
    re.compile(rf"[\\/]private[\\/]var[\\/]folders[\\/]{_TAIL}", re.IGNORECASE),
    # Bare temp dirs, including the relative climb OpenSCAD prints alongside the
    # absolute form ("../../../../tmp/tmpXXXX.scad").
    re.compile(rf"(?:(?:\.\.[\\/])+|[\\/])tmp[\\/]{_TAIL}", re.IGNORECASE),
    re.compile(rf"[a-z]:[\\/]temp[\\/]{_TAIL}", re.IGNORECASE),
)


def find_host_paths(value: str) -> list[str]:
    """Return every host-absolute path substring in ``value``.

    Matches are found longest-prefix-first and already-redacted regions are not
    re-reported, so the result is the set of *distinct* leaks a reader would see.
    """
    if not isinstance(value, str) or not value:
        return []
    found: list[str] = []
    remaining = value
    for pattern in HOST_PATH_PATTERNS:
        hits = pattern.findall(remaining)
        if not hits:
            continue
        found.extend(hits)
        remaining = pattern.sub(REDACTION_TOKEN, remaining)
    return found


def redact_host_paths(value: str) -> str:
    """Replace every host-absolute path in ``value`` with :data:`REDACTION_TOKEN`."""
    if not isinstance(value, str) or not value:
        return value
    for pattern in HOST_PATH_PATTERNS:
        value = pattern.sub(REDACTION_TOKEN, value)
    return value


#: Marks the run-scoped tail of a captured artifact path. Perception artifacts are
#: written under ``<local scratch>/runs/<model>/<task>__seed…/perceive/iter_N/``,
#: so everything from ``runs/`` onward is reproducible provenance while the prefix
#: is pure host detail.
_RUN_ROOT_RE = re.compile(r"(?:^|[\\/])(runs[\\/].*)$", re.IGNORECASE)


def run_relative_path(value: str) -> str:
    """Reduce a captured artifact path to its run-scoped tail.

    Structured path fields have a canonical form that free-text stderr does not:
    the run directory. ``/home/tony/bench-wt/runs/m/task/perceive/iter_1/x.png``
    becomes ``runs/m/task/perceive/iter_1/x.png`` — which iteration and viewport
    the artifact came from survives, the operator's home directory does not.

    Paths with no ``runs/`` segment fall back to :func:`redact_host_paths`, and a
    value that was never host-absolute is returned untouched.
    """
    if not isinstance(value, str) or not value:
        return value
    if not find_host_paths(value):
        return value
    match = _RUN_ROOT_RE.search(value.replace("\\", "/"))
    if match:
        return match.group(1)
    return redact_host_paths(value)
