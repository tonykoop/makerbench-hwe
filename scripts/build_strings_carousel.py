#!/usr/bin/env python3
"""build_strings_carousel.py: turn the strings gallery into a 1080x1350 LinkedIn carousel.

Reads the anonymized gallery (``grid.png`` and ``gallery.json`` from
``generate_render_gallery.py``) and a small ``content.json`` (title, method, one result per
matchup axis, caveats), and writes the slides as PNGs, one PDF, and paste-ready alt
text. Deterministic: the same inputs give the same slides.

Slides (10 for 18 designs): title, method, the designs six per slide, one result per
axis, the yardstick caveat, and the caveats. Every result slide carries a "provisional" banner while ``content.json`` has
a ``status`` string, and no slide shows an Elo, a vote count or a model name inside the
grid (the grid stays anonymized; only the axis-result slides name the compared setups,
as the matchup reports do).

Usage:
    python3 scripts/build_strings_carousel.py --gallery docs/showcase/strings/gallery \
        --content docs/showcase/strings/carousel/content.json \
        --out docs/showcase/strings/carousel
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1350
MARGIN = 72
BG, FG, MUTED, ACCENT, WARN = (246, 246, 242), (27, 31, 35), (94, 102, 110), (47, 127, 121), (161, 43, 43)
FONT_DIRS = ("/usr/share/fonts/truetype/noto", "/usr/share/fonts/truetype/dejavu")
FONT_FILES = {"regular": ("NotoSans-Regular.ttf", "DejaVuSans.ttf"), "bold": ("NotoSans-Bold.ttf", "DejaVuSans-Bold.ttf")}


def font(size: int, weight: str = "regular") -> ImageFont.ImageFont:
    for directory in FONT_DIRS:
        for name in FONT_FILES[weight]:
            path = Path(directory) / name
            if path.is_file():
                return ImageFont.truetype(path.as_posix(), size)
    return ImageFont.load_default()


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, width: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=fnt) <= width:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def paragraph(draw, text, x, y, fnt, fill, width, gap=10) -> int:
    for line in wrap(draw, text, fnt, width):
        draw.text((x, y), line, font=fnt, fill=fill)
        y += fnt.size + gap if hasattr(fnt, "size") else 30
    return y


def new_slide(n: int, total: int, provisional: Optional[str]):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, W, 14], fill=ACCENT)
    draw.text((MARGIN, H - 64), "MakerBench", font=font(26, "bold"), fill=MUTED)
    label = f"{n} / {total}"
    draw.text((W - MARGIN - draw.textlength(label, font=font(26)), H - 64), label, font=font(26), fill=MUTED)
    if provisional:
        draw.rounded_rectangle([MARGIN, 44, W - MARGIN, 104], radius=10, fill=(255, 240, 214), outline=(196, 130, 30), width=2)
        draw.text((MARGIN + 18, 58), "PROVISIONAL: wall-thickness check under calibration", font=font(26, "bold"), fill=(120, 74, 8))
    return img, draw


def slide_title(c, n, total):
    img, d = new_slide(n, total, c.get("status"))
    y = paragraph(d, c["title"], MARGIN, 360, font(76, "bold"), FG, W - 2 * MARGIN, 14)
    paragraph(d, c["subtitle"], MARGIN, y + 30, font(40), MUTED, W - 2 * MARGIN, 12)
    return img


def slide_method(c, n, total):
    img, d = new_slide(n, total, None)
    d.text((MARGIN, 150), "Method", font=font(64, "bold"), fill=FG)
    y = 270
    for item in c["method"]:
        d.ellipse([MARGIN, y + 16, MARGIN + 14, y + 30], fill=ACCENT)
        y = paragraph(d, item, MARGIN + 44, y, font(38), FG, W - 2 * MARGIN - 44, 10) + 34
    return img


def _card_caption(card: dict) -> tuple[str, str]:
    if card["status"] == "error":
        return "No design produced (not graded)", ""
    if card["status"] == "failed":
        return "Failed before scoring", ""
    bad = [name for name, value in card["sub_scores"].items() if value < 1.0]
    return f"Pass rate {card['objective_pass_rate']:.3f}", ("fails: " + ", ".join(bad)) if bad else ""


def slide_designs(gallery: Path, cards: list[dict], part: int, parts: int, n: int, total: int, provisional: Optional[str] = None):
    img, d = new_slide(n, total, provisional)
    d.text((MARGIN, 130), f"The designs ({part}/{parts})", font=font(56, "bold"), fill=FG)
    d.text((MARGIN, 205), "Neutral labels. Objective checks only.", font=font(32), fill=MUTED)
    cols, cell_w, cell_h, gap = 2, (W - 2 * MARGIN - 24) // 2, 222, 24
    for i, card in enumerate(cards):
        x = MARGIN + (i % cols) * (cell_w + gap)
        y = 270 + (i // cols) * (cell_h + 100 + 16)
        d.rounded_rectangle([x, y, x + cell_w, y + cell_h + 92], radius=12, fill=(255, 255, 255), outline=(215, 217, 212))
        if card["image"]:
            with Image.open(gallery / card["image"]) as pic:
                tile = pic.convert("RGB")
                tile.thumbnail((cell_w - 16, cell_h - 8), Image.LANCZOS)
                img.paste(tile, (x + (cell_w - tile.width) // 2, y + 6 + (cell_h - 8 - tile.height) // 2))
        else:
            d.text((x + 20, y + cell_h // 2), "no render produced", font=font(28), fill=MUTED)
        head, tail = _card_caption(card)
        d.text((x + 16, y + cell_h + 6), card["label"], font=font(30, "bold"), fill=FG)
        d.text((x + 16 + d.textlength(card["label"] + "  ", font=font(30, "bold")), y + cell_h + 10), head, font=font(24), fill=MUTED)
        d.text((x + 16, y + cell_h + 54), tail, font=font(24), fill=WARN)
    return img


def slide_axis(c, axis, n, total):
    img, d = new_slide(n, total, c.get("status"))
    d.text((MARGIN, 150), f"Varied: {axis['axis']}", font=font(60, "bold"), fill=FG)
    d.text((MARGIN, 232), f"Held: {axis['held']}", font=font(34), fill=MUTED)
    y = 340
    bar_max = W - 2 * MARGIN
    for label, value in axis["rows"]:
        d.text((MARGIN, y), label, font=font(38, "bold"), fill=FG)
        y += 62
        d.rounded_rectangle([MARGIN, y, MARGIN + bar_max, y + 46], radius=8, fill=(228, 230, 226))
        d.rounded_rectangle([MARGIN, y, MARGIN + max(8, int(bar_max * value)), y + 46], radius=8, fill=ACCENT)
        d.text((MARGIN + 16, y + 3), f"{value:.3f}", font=font(32, "bold"), fill=(255, 255, 255))
        y += 100
    paragraph(d, "Mean objective pass rate, three seeds.", MARGIN, y, font(30), MUTED, bar_max)
    paragraph(d, axis["note"], MARGIN, y + 70, font(36), FG, bar_max, 12)
    return img


def slide_list(title, items, n, total, warn=False):
    img, d = new_slide(n, total, None)
    d.text((MARGIN, 150), title, font=font(60, "bold"), fill=WARN if warn else FG)
    y = 280
    for item in items:
        y = paragraph(d, item, MARGIN, y, font(40), FG, W - 2 * MARGIN, 12) + 40
    return img


def build(gallery: Path, content: dict, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    cards = json.loads((gallery / "gallery.json").read_text(encoding="utf-8"))["designs"]
    per = 6
    chunks = [cards[i : i + per] for i in range(0, len(cards), per)]
    total = 2 + len(chunks) + len(content["axes"]) + 2
    slides = [slide_title(content, 1, total), slide_method(content, 2, total)]
    for part, chunk in enumerate(chunks, start=1):
        slides.append(slide_designs(gallery, chunk, part, len(chunks), len(slides) + 1, total, content.get("status")))
    for axis in content["axes"]:
        slides.append(slide_axis(content, axis, len(slides) + 1, total))
    slides.append(slide_list("The yardstick moves", content["yardstick"], len(slides) + 1, total, warn=True))
    slides.append(slide_list("What this does not show", content["caveats"], len(slides) + 1, total))
    paths = []
    for i, slide in enumerate(slides, start=1):
        path = out / f"slide-{i:02d}.png"
        slide.save(path, format="PNG", optimize=True)
        paths.append(path)
    slides[0].save(out / "strings-carousel.pdf", save_all=True, append_images=slides[1:], resolution=100.0)
    return paths


def alt_text(content: dict, cards: list[dict]) -> str:
    per = 6
    lines = [
        f"Slide 1: Title slide, \"{content['title']}\". {content['subtitle']}",
        "Slide 2: Method. " + " ".join(content["method"]),
    ]
    chunks = [cards[i : i + per] for i in range(0, len(cards), per)]
    for part, chunk in enumerate(chunks, start=1):
        detail = "; ".join(f"{c['label']}: " + " ".join(x for x in _card_caption(c) if x) for c in chunk)
        lines.append(f"Slide {len(lines) + 1}: Part {part} of {len(chunks)} of the designs. Rendered 3D models of a boat-shaped harp, each with a neutral label and its objective result. {detail}.")
    for axis in content["axes"]:
        rows = "; ".join(f"{label} {value:.3f}" for label, value in axis["rows"])
        lines.append(f"Slide {len(lines) + 1}: Varied {axis['axis']}, held {axis['held']}. Mean pass rates, provisional: {rows}. {axis['note']}")
    lines.append(f"Slide {len(lines) + 1}: The yardstick moves. " + " ".join(content["yardstick"]))
    lines.append(f"Slide {len(lines) + 1}: What this does not show. " + " ".join(content["caveats"]))
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--gallery", type=Path, required=True)
    parser.add_argument("--content", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    content = json.loads(args.content.read_text(encoding="utf-8"))
    cards = json.loads((args.gallery / "gallery.json").read_text(encoding="utf-8"))["designs"]
    paths = build(args.gallery, content, args.out)
    (args.out / "alt-text.txt").write_text(alt_text(content, cards), encoding="utf-8")
    print(f"wrote {len(paths)} slides, strings-carousel.pdf and alt-text.txt to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
