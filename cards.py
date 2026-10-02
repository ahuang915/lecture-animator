"""Optional title card and credits card for the stitched lecture.

Both are plain still images rendered with Pillow from text the user types in the
Export step, stored per project in `cards.json`:

    {"title": "...", "subtitle": "...", "author": "...", "credits": "line 1\\nline 2"}

An empty title means no title card; empty credits means no credits card. The
stitcher shows the title card for TITLE_SECONDS before the first scene and the
credits card for CREDITS_SECONDS after the last one (both silent).
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import session as sess_mod

TITLE_SECONDS = 5.0
CREDITS_SECONDS = 6.0

CARD_FIELDS = ("title", "subtitle", "author", "credits")

_W, _H = 1920, 1080
_NAVY = (31, 58, 147)
_DARK = (58, 58, 58)
_GREY = (96, 96, 96)

# First font that exists wins. DejaVu/Liberation ship in the Docker image; the
# others cover a local run on macOS or Windows.
_SERIF_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/Library/Fonts/Microsoft/Times New Roman Bold.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
    "C:/Windows/Fonts/timesbd.ttf",
]
_SERIF = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/Library/Fonts/Microsoft/Times New Roman.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
    "C:/Windows/Fonts/times.ttf",
]


def _cards_path(session: sess_mod.Session) -> Path:
    return session.dir / "cards.json"


def load_cards(session: sess_mod.Session) -> dict[str, str]:
    try:
        data = json.loads(_cards_path(session).read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {key: str(data.get(key) or "") for key in CARD_FIELDS}


def save_cards(session: sess_mod.Session, cards: dict[str, str]) -> dict[str, str]:
    clean = {key: str(cards.get(key) or "").strip() for key in CARD_FIELDS}
    sess_mod._atomic_write_json(_cards_path(session), clean)
    return clean


def _font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _fit_font(draw: ImageDraw.ImageDraw, text: str, candidates: list[str], size: int) -> ImageFont.ImageFont:
    """Largest font <= size whose rendering of `text` fits within 88% of the width."""
    while size > 18:
        font = _font(candidates, size)
        left, _, right, _ = draw.textbbox((0, 0), text, font=font)
        if right - left <= _W * 0.88:
            return font
        size -= 4
    return _font(candidates, size)


def _draw_lines(lines: list[tuple[str, list[str], int, tuple[int, int, int], int]], out: Path) -> Path:
    """lines: (text, font_candidates, size, color, gap_after). Drawn centered as a block."""
    img = Image.new("RGB", (_W, _H), "white")
    draw = ImageDraw.Draw(img)
    laid = []
    for text, candidates, size, color, gap in lines:
        font = _fit_font(draw, text, candidates, size)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        laid.append((text, font, color, right - left, bottom - top, top, gap))
    total = sum(h + gap for _, _, _, _, h, _, gap in laid) - (laid[-1][6] if laid else 0)
    y = (_H - total) / 2
    for text, font, color, w, h, top, gap in laid:
        draw.text(((_W - w) / 2, y - top), text, font=font, fill=color)
        y += h + gap
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def render_title_card(session: sess_mod.Session) -> Path | None:
    cards = load_cards(session)
    if not cards["title"]:
        return None
    lines: list[tuple[str, list[str], int, tuple[int, int, int], int]] = []
    title_lines = [part for part in cards["title"].splitlines() if part.strip()]
    for i, part in enumerate(title_lines):
        last = i == len(title_lines) - 1
        lines.append((part.strip(), _SERIF_BOLD, 104, _NAVY, 70 if last else 24))
    if cards["subtitle"]:
        lines.append((cards["subtitle"], _SERIF, 52, _DARK, 22))
    if cards["author"]:
        lines.append((cards["author"], _SERIF, 44, _GREY, 0))
    return _draw_lines(lines, session.dir / "final" / "title_card.png")


def render_credits_card(session: sess_mod.Session) -> Path | None:
    cards = load_cards(session)
    credit_lines = [part.strip() for part in cards["credits"].splitlines() if part.strip()]
    if not credit_lines:
        return None
    lines = [(text, _SERIF, 48, _DARK, 30) for text in credit_lines]
    return _draw_lines(lines, session.dir / "final" / "credits_card.png")
