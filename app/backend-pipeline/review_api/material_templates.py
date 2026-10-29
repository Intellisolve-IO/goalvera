"""Code-built classroom pages (David, Oct 10 2026; tasks#99).

Math and literacy pages are not drawn by the AI. The AI only fills a few structured parameters (which
table, which range, which words); this module computes every number and the whole layout itself,
so the page is always arithmetically right. The photo-style flashcard layout is built here too, from
the image model's pictures. Everything returns an SVG page (viewBox 0 0 800 1000, art below the school
header, which material_svg.set_header() puts on top); rendering and the PDF are the same as for SVG drafts.
"""
from __future__ import annotations

import base64
import math
import re
from xml.sax.saxutils import escape

from .material_svg import FONT, SVG_NS

PHOTO_MAX_WORD = 24
LAYOUTS = 3        # every template makes 3 drafts (layouts) for the teacher to pick from

DEFAULT_PARAMS_INSTRUCTION = {
    "multiplication_table": (
        "Choose the multiplication table that fits the teacher's request. start and end are the smallest and "
        "largest factor (whole numbers from 0 to 12; a '1-12 table' is start 1, end 12). focus lists up to 4 "
        "factors the teacher wants practiced (e.g. [3, 4] for 'the 3s and 4s'), or [] for the whole table. "
        "title is a short page title for students, such as 'Multiplication 1-12'."),
    "number_line": (
        "Choose the number line that fits the teacher's request and the students' grade. start and end are whole "
        "numbers (end greater than start), step is the gap between marks (1, 2, 5, 10, ...), with at most 20 "
        "steps. label_every says how often a mark is numbered (1 = every mark). title is a short page title."),
    "letter_tracing": (
        "Choose what the students trace. lines lists 2 to 8 short lines, each a letter pair like 'A a', a short "
        "word or a two-word phrase (letters only, at most 12 characters), simple and right for the grade and the "
        "topic. case is upper, lower or as_written. Never use anyone's name. title is a short page title."),
    "flashcards_photo": (
        "Clear photo-style picture of {subject}, the whole subject centered, plain soft background, bright natural "
        "light, friendly and realistic, for a young child's flashcard. No text, letters, numbers, logos, watermarks "
        "or people's faces."),
}
PARAMS_TECH = {
    "flashcards_photo": (
        "Choose the flashcards. cards lists the cards (as many as asked, at most 8): word is the one to three word "
        "label printed on the card (no names), picture says what the photo shows in a few plain words. title is "
        "a short page title. Return JSON only."),
}
LAYOUT_NAMES = {
    "multiplication_table": ["full table to keep", "practice: fill in some blanks", "practice: fill in more blanks"],
    "number_line": ["every mark numbered", "fill in the missing numbers", "three short lines to complete"],
    "letter_tracing": ["one model and one line to trace", "two lines to trace per item", "dotted letters only"],
}
_F = f'font-family="{FONT}"'


def _svg(body: str) -> str:
    return f'<svg xmlns="{SVG_NS}" viewBox="0 0 800 1000" width="800" height="1000">{body}</svg>'


def _title(t: str, y: int = 150) -> str:
    return f'<text x="400" y="{y}" {_F} font-size="30" font-weight="bold" text-anchor="middle">{escape(t)}</text>'


# ------------------------------------------------------------ multiplication table
def multiplication_table(p: dict, layout: int) -> str:
    """layout 1: whole table; 2: about 1 in 3 products blank; 3: about 2 in 3 blank (header rows stay)."""
    a, b = p["start"], p["end"]
    nums = list(range(a, b + 1))
    n = len(nums) + 1
    size = min(56, 700 // n)
    x0 = (800 - size * n) // 2
    y0 = 185
    fs = max(14, int(size * 0.42))
    focus = set(p.get("focus") or [])
    out = [_title(p.get("title") or f"Multiplication {a}-{b}")]
    for r in range(n):
        for c in range(n):
            x, y = x0 + c * size, y0 + r * size
            head = r == 0 or c == 0
            hi = not head and focus and (nums[r - 1] in focus or nums[c - 1] in focus)
            fill = "#D9D9D9" if head else ("#F2F2F2" if hi else "#FFFFFF")
            out.append(f'<rect x="{x}" y="{y}" width="{size}" height="{size}" fill="{fill}" stroke="#000000" stroke-width="2"/>')
            if r == 0 and c == 0:
                txt = "×"
            elif r == 0:
                txt = str(nums[c - 1])
            elif c == 0:
                txt = str(nums[r - 1])
            else:
                i, j = nums[r - 1], nums[c - 1]
                blank = (layout == 2 and (r * 7 + c * 5) % 3 == 0) or (layout == 3 and (r * 7 + c * 5) % 3 != 1)
                txt = "" if blank else str(i * j)
            if txt:
                w = ' font-weight="bold"' if head else ""
                out.append(f'<text x="{x + size / 2}" y="{y + size / 2 + fs * 0.36:.1f}" {_F} font-size="{fs}"{w} '
                           f'text-anchor="middle">{txt}</text>')
    if layout > 1:
        out.append(f'<text x="400" y="{y0 + n * size + 40}" {_F} font-size="20" text-anchor="middle">'
                   'Fill in the empty boxes.</text>')
    return _svg("".join(out))


# ------------------------------------------------------------ number line
def _line(x0: float, x1: float, y: float, start: int, step: int, n: int, shown, fs: int) -> str:
    out = [f'<line x1="{x0 - 25}" y1="{y}" x2="{x1 + 25}" y2="{y}" stroke="#000000" stroke-width="4"/>',
           f'<polygon points="{x0 - 40},{y} {x0 - 22},{y - 10} {x0 - 22},{y + 10}" fill="#000000"/>',
           f'<polygon points="{x1 + 40},{y} {x1 + 22},{y - 10} {x1 + 22},{y + 10}" fill="#000000"/>']
    gap = (x1 - x0) / max(1, n - 1)
    for k in range(n):
        x = x0 + k * gap
        out.append(f'<line x1="{x:.1f}" y1="{y - 14}" x2="{x:.1f}" y2="{y + 14}" stroke="#000000" stroke-width="3"/>')
        if shown(k):
            out.append(f'<text x="{x:.1f}" y="{y + 14 + fs + 6}" {_F} font-size="{fs}" text-anchor="middle">{start + k * step}</text>')
        else:
            bw = min(gap - 4, fs * 2.2)
            out.append(f'<rect x="{x - bw / 2:.1f}" y="{y + 22}" width="{bw:.1f}" height="{fs + 10}" fill="#FFFFFF" '
                       f'stroke="#000000" stroke-width="2" rx="4"/>')
    return "".join(out)


def number_line(p: dict, layout: int) -> str:
    start, step = p["start"], p["step"]
    n = (p["end"] - start) // step + 1
    every = p.get("label_every") or 1
    fs = 26 if n <= 11 else 20 if n <= 16 else 16
    out = [_title(p.get("title") or f"Number line {start} to {p['end']}")]
    if layout == 1:
        out.append(_line(80, 720, 520, start, step, n, lambda k: k % every == 0 or k == n - 1, fs))
    elif layout == 2:
        out.append(_line(80, 720, 360, start, step, n, lambda k: k % every == 0 or k == n - 1, fs))
        out.append(_line(80, 720, 640, start, step, n, lambda k: k in (0, n - 1) or k % 3 == 0, fs))
        out.append(f'<text x="400" y="860" {_F} font-size="20" text-anchor="middle">Fill in the missing numbers.</text>')
    else:
        for i, y in enumerate((300, 520, 740)):
            out.append(_line(80, 720, y, start, step, n, lambda k, i=i: k in (0, n - 1) or (k == (i + 2) * n // 5), fs))
        out.append(f'<text x="400" y="900" {_F} font-size="20" text-anchor="middle">Write the numbers in the boxes.</text>')
    return _svg("".join(out))


# ------------------------------------------------------------ letter tracing
def letter_tracing(p: dict, layout: int) -> str:
    lines = p["lines"]
    rows = [(t, k) for t in lines for k in range(2 if layout == 2 else 1)]
    h = min(160, 760 // max(1, len(rows)))
    fs = int(h * 0.62)
    out = [_title(p.get("title") or "Trace the letters")]
    for i, (t, k) in enumerate(rows):
        top = 180 + i * h
        base = top + h * 0.8
        mid = base - fs * 0.52
        cap = base - fs * 0.74
        out.append(f'<line x1="50" y1="{cap:.1f}" x2="750" y2="{cap:.1f}" stroke="#7F7F7F" stroke-width="2"/>'
                   f'<line x1="50" y1="{mid:.1f}" x2="750" y2="{mid:.1f}" stroke="#7F7F7F" stroke-width="2" stroke-dasharray="10 8"/>'
                   f'<line x1="50" y1="{base:.1f}" x2="750" y2="{base:.1f}" stroke="#000000" stroke-width="3"/>')
        t_ = escape(t)
        if layout == 1:
            out.append(f'<text x="60" y="{base:.1f}" {_F} font-size="{fs}" fill="#000000">{t_}</text>'
                       f'<text x="420" y="{base:.1f}" {_F} font-size="{fs}" fill="none" stroke="#8C8C8C" '
                       f'stroke-width="2" stroke-dasharray="4 4">{t_}</text>')
        else:
            first = layout == 2 and k == 0
            out.append(f'<circle cx="62" cy="{cap:.1f}" r="6" fill="#000000"/>' if not first else "")
            for x in (60, 420):
                if first and x == 60:
                    out.append(f'<text x="60" y="{base:.1f}" {_F} font-size="{fs}" fill="#000000">{t_}</text>')
                else:
                    out.append(f'<text x="{x}" y="{base:.1f}" {_F} font-size="{fs}" fill="none" stroke="#8C8C8C" '
                               f'stroke-width="2" stroke-dasharray="{"2 5" if layout == 3 else "4 4"}">{t_}</text>')
    return _svg("".join(out))


BUILD = {"multiplication_table": multiplication_table, "number_line": number_line, "letter_tracing": letter_tracing}


# ------------------------------------------------------------ photo-style flashcards
PHOTO_PER_PAGE = 4      # 2 x 2: pictures about 3.3 in wide when printed on letter paper


def photo_pages(title: str, cards: list[dict], pictures: dict[int, bytes]) -> list[str]:
    """Photo-style flashcards, PHOTO_PER_PAGE big cards per page (2 x 2), as many pages as needed. Each
    card: the picture (PNG bytes from the image model, re-encoded by us) and its word; a card whose
    picture is not made yet shows an empty dashed frame."""
    n = len(cards)
    pages = max(1, math.ceil(n / PHOTO_PER_PAGE))
    out = []
    for p in range(pages):
        chunk = cards[p * PHOTO_PER_PAGE:(p + 1) * PHOTO_PER_PAGE]
        t = title or "Flashcards"
        body = [_title(f"{t} ({p + 1} of {pages})" if pages > 1 else t)]
        cw, ch = 340, 385
        for j, c in enumerate(chunk):
            i = p * PHOTO_PER_PAGE + j
            x = 50 + (j % 2) * (cw + 20)
            y = 175 + (j // 2) * (ch + 20)
            body.append(f'<rect x="{x}" y="{y}" width="{cw}" height="{ch}" rx="18" fill="#FFFFFF" stroke="#000000" '
                        f'stroke-width="4" stroke-dasharray="14 8"/>')
            ps = 300
            px, py = x + (cw - ps) / 2, y + 18
            pic = pictures.get(i)
            if pic:
                uri = "data:image/png;base64," + base64.b64encode(pic).decode()
                body.append(f'<image x="{px:.1f}" y="{py}" width="{ps}" height="{ps}" href="{uri}" '
                            f'preserveAspectRatio="xMidYMid slice"/>'
                            f'<rect x="{px:.1f}" y="{py}" width="{ps}" height="{ps}" fill="none" stroke="#000000" stroke-width="2"/>')
            else:
                body.append(f'<rect x="{px:.1f}" y="{py}" width="{ps}" height="{ps}" fill="none" stroke="#9E9E9E" '
                            f'stroke-width="3" stroke-dasharray="12 10"/>')
            fs = 40 if len(c["word"]) <= 10 else 32 if len(c["word"]) <= 14 else 24
            body.append(f'<text x="{x + cw / 2}" y="{y + ch - 22}" {_F} font-size="{fs}" font-weight="bold" '
                        f'text-anchor="middle">{escape(c["word"])}</text>')
        if pages > 1 or n > 1:
            body.append(f'<text x="400" y="992" {_F} font-size="14" fill="#555555" text-anchor="middle">'
                        'Cut along the dashed lines.</text>')
        out.append(_svg("".join(body)))
    return out


def photo_cards(title: str, cards: list[dict], pictures: dict[int, bytes]) -> str:
    """The first page (kept for callers that want one page)."""
    return photo_pages(title, cards, pictures)[0]


def picture_png(raw: bytes, size: int = 512) -> bytes:
    """Any image from the model -> RGB PNG at most size x size, metadata dropped."""
    import io

    from PIL import Image, ImageOps
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((size, size))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


_WORD = re.compile(r"[^A-Za-z' \-]")


def clean_word(v, n: int = 24) -> str:
    return " ".join(_WORD.sub("", str(v or "")).split())[:n].strip()
