"""Render approved session notes as a PDF, in memory only (never written to disk
or to the bucket). Plain, one-column layout; built-in Helvetica so the output
looks the same on any machine."""
from __future__ import annotations

import datetime as dt
import io
import os
import unicodedata
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .incident_print import incident_numbers, incident_title, recording_start, time_line

SCHOOL_TZ = ZoneInfo(os.environ.get("SCHOOL_TZ", "America/New_York"))

INK = colors.HexColor("#1f2a37")
MUTED = colors.HexColor("#5b6b7f")
RULE = colors.HexColor("#d8e0ea")
ACCENT = colors.HexColor("#136f55")
SOFT = colors.HexColor("#eef6f2")

_ST = {
    "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=17, leading=21, textColor=INK),
    "sub": ParagraphStyle("sub", fontName="Helvetica", fontSize=9, leading=12, textColor=MUTED),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=11.5, leading=15, textColor=INK,
                         spaceBefore=12, spaceAfter=4, keepWithNext=1),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=14, textColor=INK, alignment=TA_LEFT),
    "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8.5, leading=11.5, textColor=MUTED),
    "label": ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=MUTED),
    "approved": ParagraphStyle("approved", fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=ACCENT),
}

_REPL = {"\u2011": "-", "\u2212": "-", "\u00a0": " ", "\u202f": " ", "\u2009": " "}


def _txt(s) -> str:
    """Escape for Paragraph markup and keep to what built-in Helvetica can draw (cp1252)."""
    s = unicodedata.normalize("NFC", str(s if s is not None else ""))
    s = "".join(_REPL.get(c, c) for c in s)
    s = s.encode("cp1252", "replace").decode("cp1252")
    return escape(s).replace("\n", "<br/>")


def _p(text, style="body") -> Paragraph:
    return Paragraph(_txt(text), _ST[style])


def _local(ts: str | None, *, assume_local: bool = False) -> str:
    """ISO time -> 'Tue, Sep 29, 2026, 1:15 PM EDT' in the school's time zone."""
    if not ts:
        return "-"
    try:
        t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    if t.tzinfo is None:
        t = t.replace(tzinfo=SCHOOL_TZ if assume_local else dt.timezone.utc)
    t = t.astimezone(SCHOOL_TZ)
    hour = t.hour % 12 or 12
    return f"{t:%a}, {t:%b} {t.day}, {t.year}, {hour}:{t:%M} {t:%p} {t.tzname()}"


def _mmss(sec) -> str:
    s = int(sec or 0)
    return f"{s // 60:02d}:{s % 60:02d}"


def _kv_table(rows: list[tuple[str, str]]) -> Table:
    t = Table([[_p(k, "label"), _p(v)] for k, v in rows], colWidths=[1.35 * inch, 5.15 * inch])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def render_notes_pdf(*, session: dict, approval: dict, goals: dict, recording_id: str,
                     generated_at: dt.datetime, sample_data: bool = True) -> bytes:
    a = approval.get("analysis") or {}
    student = " ".join(x for x in [session.get("student_first_name"), session.get("student_last_initial")] if x) or "-"
    mins = max(1, round((session.get("duration_seconds") or 0) / 60))
    approved_line = f"Approved by {approval.get('approved_by_name') or approval.get('approved_by_id')} at {_local(approval.get('approved_at'))}"

    story = [
        _p("Approved session notes", "title"),
        Spacer(1, 2),
        _p(("SAMPLE DATA - synthetic audio, fictional people. " if sample_data else "")
           + "Human-reviewed notes. Downloaded from the review screen; not sent to anyone.", "sub"),
        Spacer(1, 10),
        _kv_table([
            ("Student", student),
            ("Classroom", session.get("class_code") or "-"),
            ("Recorded", f"{_local(session.get('timestamp_start'), assume_local=True)}  ({mins} min)"),
            ("Teacher", session.get("teacher_name") or "-"),
            ("Recording ID", recording_id),
        ]),
        Spacer(1, 10),
    ]
    box = Table([[_p(approved_line, "approved")]], colWidths=[6.5 * inch])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SOFT), ("BOX", (0, 0), (-1, -1), 0.6, ACCENT),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
    ]))
    story.append(box)

    markers = [m for m in (session.get("markers") or []) if str(m.get("type") or "").lower() != "bug"]   # bug reports: never here
    if markers:
        story.append(_p("Markers", "h2"))
        story.append(_kv_table([(_mmss(m.get("offset_seconds")), m.get("type", "")) for m in markers]))

    evals = a.get("goal_evaluations") or []
    if not evals:
        story += [_p("IEP goal evaluations", "h2"), _p("No goal evaluations.", "small")]
    for i, g in enumerate(evals):
        info = goals.get(g.get("goal_id")) or {}
        head = f"{g.get('goal_id')}" + (f" - {info['category']}" if info.get("category") else "") + f":  {g.get('status')}"
        block = ([_p("IEP goal evaluations", "h2")] if i == 0 else []) + [Paragraph(f"<b>{_txt(head)}</b>", _ST["body"])]
        if info.get("description"):
            block.append(_p(info["description"], "small"))
        if g.get("evidence_quote"):
            block.append(Paragraph(f"<b>Evidence:</b> \u201c{_txt(g['evidence_quote'])}\u201d", _ST["body"]))
        if g.get("analysis"):
            block.append(_p(g["analysis"]))
        block.append(Spacer(1, 6))
        story.append(KeepTogether(block))

    # numbered earliest first, titled and timed like the review screen and the incident print view:
    # "Incident 1: <name>" / "1:02 PM · 12:34 into recording" (Eastern; offset only without a start time)
    start = recording_start(session)
    for i, (num, inc) in enumerate(incident_numbers(a.get("incident_analysis") or [])):
        # the section heading travels with the first incident so it is never orphaned
        head = [_p("Incident notes (ABC)", "h2")] if i == 0 else []
        story.append(KeepTogether(head + [
                Paragraph(f"<b>{_txt(incident_title(num, inc.get('name')))}</b>", _ST["body"]),
                _p(time_line(start, inc.get("timestamp_seconds")), "small"),
                _kv_table([("Antecedent", inc.get("antecedent", "")), ("Behavior", inc.get("behavior", "")),
                           ("Consequence", inc.get("consequence_intervention", ""))]),
                Spacer(1, 6),
            ]))

    story.append(_p("Session summary", "h2"))
    story.append(_p(a.get("general_session_summary") or "-"))
    # No per-session parent email: it belongs to the weekly student summary.

    footer = (f"Generated on request {_local(generated_at.isoformat())} - not stored. "
              f"{recording_id}")

    def _page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(doc.leftMargin, 0.5 * inch, footer.encode("cp1252", "replace").decode("cp1252"))
        canvas.drawRightString(LETTER[0] - doc.rightMargin, 0.5 * inch, f"Page {doc.page}")
        if sample_data:
            canvas.drawRightString(LETTER[0] - doc.rightMargin, LETTER[1] - 0.45 * inch, "SAMPLE DATA")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=inch, rightMargin=inch,
                            topMargin=0.8 * inch, bottomMargin=0.8 * inch,
                            title=f"Approved session notes {recording_id}", author="Open Classroom Recorder",
                            subject="Approved session notes", creator="review_api")
    doc.build(story, onFirstPage=_page, onLaterPages=_page)
    return buf.getvalue()
