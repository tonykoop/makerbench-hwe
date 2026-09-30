"""CI tripwire for subjective ranking fields anywhere in published site data."""

import json
import re
from pathlib import Path

import pytest


SITE = Path(__file__).resolve().parents[1] / "site"
FIELD = re.compile(
    r"(?:^|_)(?:elo|votes?|voters?|ratings?|ballots?|subjective)(?:_|$)",
    re.IGNORECASE,
)
# Quoted and JavaScript bare object keys, including embedded HTML script data.
TEXT_FIELD = re.compile(r'''["']([^"'\n]+)["']\s*:|\b([A-Za-z_$][\w$]*)\s*:''')


def forbidden_field(key):
    # Split camelCase too, so voteCount/eloRating cannot evade the guard.
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    normalized = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", normalized)
    normalized = re.sub(r"[^\w]+", "_", normalized)
    return FIELD.search(normalized) is not None


def audit_json(payload, path="$"):
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert not forbidden_field(key), f"Forbidden publication field at {path}.{key}"
            audit_json(value, f"{path}.{key}")
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            audit_json(value, f"{path}[{index}]")


def audit_file(path):
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        audit_json(json.loads(text), str(path))
    else:
        for match in TEXT_FIELD.finditer(text):
            key = match.group(1) or match.group(2)
            assert not forbidden_field(key), f"Forbidden publication field in {path}: {key}"


def test_committed_site_has_no_subjective_ranking_fields():
    paths = sorted(p for p in SITE.rglob("*") if p.suffix in {".json", ".html", ".js"})
    assert paths, "No site publication files found"
    for path in paths:
        audit_file(path)


@pytest.mark.parametrize("key", ["elo", "subjective_elo", "vote_count", "voteCount",
                                 "vote-count", "rating", "ratings", "voters", "eloRating",
                                 "ELOLeaderboard", "ELO"])
def test_guard_rejects_nested_fields(key):
    with pytest.raises(AssertionError, match="Forbidden publication field"):
        audit_json({"rounds": [{"nested": {key: 123}}]})


@pytest.mark.parametrize("suffix,text", [
    (".json", '{"rows": [{"vote_count": 1}]}'),
    (".js", 'const payload = {eloRating: 1500};'),
    (".html", '<script type="application/json">{"rating":1500}</script>'),
])
def test_guard_rejects_fields_in_public_files(tmp_path, suffix, text):
    path = tmp_path / ("injected" + suffix)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(AssertionError, match="Forbidden publication field"):
        audit_file(path)


def test_policy_prose_and_unrelated_fields_are_allowed():
    audit_json({"policy": "Elo and votes are withheld", "development": 1,
                "objective_pass_rate": 0.5, "agreement": {"rho": 0.07}})
