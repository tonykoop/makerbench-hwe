"""Contract tests for the strings carousel builder (#910). No browser or model needed."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("makerbench_build_strings_carousel", ROOT / "scripts" / "build_strings_carousel.py")
carousel = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = carousel
SPEC.loader.exec_module(carousel)

CONTENT = {
    "title": "T", "subtitle": "S", "status": "GATED: test", "method": ["m1", "m2"],
    "axes": [{"axis": "Model", "held": "h", "source": "s", "rows": [["A", 0.5], ["B", 1.0]], "note": "n"}],
    "yardstick": ["y1"], "caveats": ["c1"], "footer": "f",
}


def _gallery(root: Path, n: int) -> Path:
    (root / "img").mkdir(parents=True)
    cards = []
    for i in range(1, n + 1):
        img = f"img/design-{i:03d}.png"
        Image.new("RGB", (400, 300), (200, 120, 60)).save(root / img)
        cards.append({"label": f"Design {i}", "image": img, "status": "scored",
                      "objective_pass_rate": 0.833333, "sub_scores": {"renders": 1.0, "min_wall": 0.0}})
    cards[-1].update(status="failed", image=None, objective_pass_rate=0.0, sub_scores={})
    (root / "gallery.json").write_text(json.dumps({"n_designs": n, "designs": cards}), encoding="utf-8")
    return root


def test_builds_1080x1350_slides_within_the_six_to_ten_range_and_a_pdf(tmp_path):
    gallery = _gallery(tmp_path / "g", 18)
    out = tmp_path / "out"
    paths = carousel.build(gallery, CONTENT, out)
    assert 6 <= len(paths) <= 10
    for path in paths:
        with Image.open(path) as im:
            assert im.size == (1080, 1350)
    assert (out / "strings-carousel.pdf").read_bytes().startswith(b"%PDF")


def test_alt_text_has_one_line_per_slide_and_no_elo(tmp_path):
    gallery = _gallery(tmp_path / "g", 18)
    cards = json.loads((gallery / "gallery.json").read_text(encoding="utf-8"))["designs"]
    paths = carousel.build(gallery, CONTENT, tmp_path / "out")
    text = carousel.alt_text(CONTENT, cards)
    assert len(text.strip().splitlines()) == len(paths)
    assert "elo" not in text.lower() and "vote" not in text.lower().replace("no votes", "")
    assert "Failed before scoring" in text


def test_output_is_deterministic(tmp_path):
    gallery = _gallery(tmp_path / "g", 7)
    a = carousel.build(gallery, CONTENT, tmp_path / "a")
    b = carousel.build(gallery, CONTENT, tmp_path / "b")
    assert [p.read_bytes() for p in a] == [p.read_bytes() for p in b]
