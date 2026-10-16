"""Keep the public DFM catalog's citable rule identifiers unambiguous."""

import re
from collections import Counter
from pathlib import Path


def test_dfm_catalog_rule_ids_are_unique():
    catalog = Path(__file__).resolve().parents[1] / "docs" / "DFM_RULES.md"
    rule_ids = re.findall(r"^\|\s*([A-Z]+[0-9]+)\s*\|", catalog.read_text(), re.MULTILINE)
    assert rule_ids, "No DFM rule IDs found; check the catalog table format"
    duplicates = {rule_id: count for rule_id, count in Counter(rule_ids).items() if count > 1}
    assert not duplicates, f"Duplicate DFM rule IDs: {duplicates}"
