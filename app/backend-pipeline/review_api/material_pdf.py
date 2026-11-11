"""Printable classroom materials: cleaned SVG drafts -> a letter-size PDF.

Each draft is one page: the SVG (already checked and cleaned by material_svg.sanitize, school header
set by our code) is rendered server-side to a PNG at print resolution and placed on the page, with the
footer "Suggestion - review before use" and the draft number. Rendered in memory on every download /
email and never stored.
"""
from __future__ import annotations

import io

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from . import material_svg as msvg

W, H = letter
M = 0.4 * inch
FOOTER = "Suggestion - review before use. Made from a Teacher Note with AI."
PRINT_WIDTH = 1700          # px for the 800 x 1000 page: ~ 220 dpi at 7.7 in


def render(drafts: list[dict], *, of: int | None = None) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, pageCompression=1)
    c.setTitle("Classroom materials")
    c.setAuthor("")
    c.setSubject("Suggestion - review before use")
    of = of or len(drafts)
    for d in drafts:
        png = msvg.render_png(d["svg"], PRINT_WIDTH)
        avail_w, avail_h = W - 2 * M, H - 2 * M - 0.25 * inch
        scale = min(avail_w / msvg.WIDTH, avail_h / msvg.HEIGHT)
        w, h = msvg.WIDTH * scale, msvg.HEIGHT * scale
        c.drawImage(ImageReader(io.BytesIO(png)), (W - w) / 2, H - M - h, w, h)
        c.setFont("Helvetica-Oblique", 8)
        c.setFillGray(0.35)
        where = f"Page {d['page']} of {d['pages']}." if d.get("page") else f"Draft {d['draft']} of {of}."
        c.drawCentredString(W / 2, M * 0.6, f"{FOOTER}  {where}")
        c.setFillGray(0)
        c.showPage()
    c.save()
    return buf.getvalue()
