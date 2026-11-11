"""AI for classroom materials: suggestions, the brief, SVG drafts.

All calls use the review service's own Vertex AI access (MATERIALS_AI = live | offline | off, default:
the PLANNER_AI setting):

  suggest   Teacher Notes windows (masked transcript text) -> per note: is it a request for
            classroom materials, and up to 3 suggested actions {type, count, topic, due}.
  brief     the teacher's choice + de-identified context -> the inferred teacher request (one or two
            plain sentences, no names), a title and 3 variation focuses. Our code turns that into the
            per-draft user prompt (material_svg.user_prompt: Teacher Request / Activity Type / Draft
            Variation N of 3).
  svg       one printable page as SVG, under the admin-editable system instruction, on a Gemini text
            model (MATERIALS_SVG_MODEL, default gemini-3.8-flash). The reply is untrusted:
            material_svg.sanitize() checks and cleans it, the code sets the school header.

What may go to the model is built in materials.py and checked there (no names, ids, emails,
transcripts beyond the note's window, present levels, eligibility or parent data).
offline: local stand-ins with the same shapes (tests, laptop demo). No network.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re

TYPES = ("worksheet", "coloring", "flashcards", "labels", "diagram", "multiplication_table", "number_line",
         "letter_tracing")
TYPE_LABEL = {"worksheet": "Worksheet", "coloring": "Coloring page", "flashcards": "Flashcards",
              "labels": "Classroom labels", "diagram": "Diagram / life cycle",
              "multiplication_table": "Multiplication table", "number_line": "Number line",
              "letter_tracing": "Letter tracing"}
# count = what goes on the one page: questions (worksheet), pictures (coloring), cards, labels, stages
# (diagram), the table's last factor, marks on the line, tracing lines; (min, default, max).
COUNT = {"worksheet": (2, 5, 8), "coloring": (1, 1, 4), "flashcards": (2, 8, 12), "labels": (2, 8, 12),
         "diagram": (2, 4, 8), "multiplication_table": (5, 12, 12), "number_line": (5, 10, 20),
         "letter_tracing": (2, 5, 8)}
UNIT = {"worksheet": "question", "coloring": "picture", "flashcards": "card", "labels": "label", "diagram": "stage",
        "multiplication_table": "row", "number_line": "mark", "letter_tracing": "line"}
STYLES = ("line", "color", "photo")             # flashcards only; everything else is line art or a template
STYLE_LABEL = {"line": "Line art", "color": "Color", "photo": "Photo-style (slow)"}
TEMPLATE_TYPES = ("multiplication_table", "number_line", "letter_tracing")
DRAFTS = 3
PHOTO_MAX = 8                                    # photo-style cards per set (one picture each)
SUGGEST_VERSION = "materials-suggest-v3-2026-10"
PARAMS_VERSION = "materials-params-v2-2026-10"   # v2: class_domain guidance
DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-lite-image"
DEFAULT_IMAGE_LOCATION = "global"


def renderer(kind: str, style: str = "line") -> str:
    """svg (gemini text model writes SVG), template (our code builds it from AI-filled parameters) or
    photo (image model, one picture per card, made in paced steps)."""
    if kind in TEMPLATE_TYPES:
        return "template"
    if kind == "flashcards" and style == "photo":
        return "photo"
    return "svg"


def instruction_key(kind: str, style: str = "line") -> str:
    return f"flashcards_{style}" if kind == "flashcards" else kind


INSTRUCTION_KEYS = ("coloring", "labels", "worksheet", "diagram", "flashcards_line", "flashcards_color",
                    "flashcards_photo", "multiplication_table", "number_line", "letter_tracing")
INSTRUCTION_LABEL = {"coloring": "Coloring page (SVG, line art)", "labels": "Classroom labels (SVG, line art)",
                     "worksheet": "Worksheet (SVG, line art)", "diagram": "Diagram / life cycle (SVG, line art)",
                     "flashcards_line": "Flashcards, line art (SVG)", "flashcards_color": "Flashcards, color (SVG)",
                     "flashcards_photo": "Flashcards, photo-style (image model)",
                     "multiplication_table": "Multiplication table (template parameters)",
                     "number_line": "Number line (template parameters)",
                     "letter_tracing": "Letter tracing (template parameters)"}
BRIEF_VERSION = "materials-brief-v3-2026-10"     # v2: class_domain guidance; v3: flashcard style
DEFAULT_SVG_MODEL = "gemini-3.8-flash"
DEFAULT_SVG_LOCATION = "global"

SUGGEST_SCHEMA = {"type": "object", "required": ["notes"], "properties": {"notes": {"type": "array", "items": {
    "type": "object", "required": ["index", "is_request", "suggestions"],
    "properties": {
        "index": {"type": "integer"}, "is_request": {"type": "boolean"},
        "suggestions": {"type": "array", "items": {
            "type": "object", "required": ["type", "count", "topic"],
            "properties": {"type": {"type": "string", "enum": list(TYPES)}, "count": {"type": "integer"},
                           "topic": {"type": "string"}, "due": {"type": "string"},
                           "style": {"type": "string", "enum": list(STYLES)}}}}}}}}}
BRIEF_SCHEMA = {"type": "object", "required": ["title", "teacher_request", "variations"], "properties": {
    "title": {"type": "string"}, "teacher_request": {"type": "string"},
    "variations": {"type": "array", "items": {"type": "string"}},
    "goal_ids": {"type": "array", "items": {"type": "string"}}}}

PARAMS_SCHEMA = {
    "multiplication_table": {"type": "object", "required": ["start", "end"], "properties": {
        "title": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"},
        "focus": {"type": "array", "items": {"type": "integer"}}}},
    "number_line": {"type": "object", "required": ["start", "end", "step"], "properties": {
        "title": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"},
        "step": {"type": "integer"}, "label_every": {"type": "integer"}}},
    "letter_tracing": {"type": "object", "required": ["lines"], "properties": {
        "title": {"type": "string"}, "lines": {"type": "array", "items": {"type": "string"}},
        "case": {"type": "string", "enum": ["upper", "lower", "as_written"]}}},
    "flashcards_photo": {"type": "object", "required": ["cards"], "properties": {
        "title": {"type": "string"}, "cards": {"type": "array", "items": {"type": "object",
            "required": ["word", "picture"], "properties": {"word": {"type": "string"}, "picture": {"type": "string"}}}}}},
}

SUGGEST_SYSTEM = """You help a special-education teacher. Each note is what was said in the classroom
right after the teacher pressed "Teacher Notes" on a recorder (about a minute, one line per speaker turn).
For each note decide whether the teacher is asking for printable classroom materials to be prepared
(any subject: math, English, science, ...). Most notes are NOT requests.
If it is a request, give up to 3 suggested actions, the most likely first. Each has:
  type: one of worksheet, coloring, flashcards, labels, diagram (diagrams, life cycles, sequences),
        multiplication_table, number_line, letter_tracing (handwriting / tracing practice)
  count: what goes on one page: questions (worksheet), pictures (coloring, usually 1), cards, labels,
        stages (diagram), the last factor (multiplication_table, e.g. 12 for 1-12), marks (number_line),
        lines (letter_tracing)
  style: flashcards only: "line" (black line art), "color" (bright cartoon color) or "photo" (photo-like
        pictures; only if the teacher asks for real or photo pictures)
  topic: a few plain words (e.g. "trains", "colors and shapes", "classroom supplies")
  due: "today", "tomorrow", a date YYYY-MM-DD, or "" if not said
Never include anyone's name; say "the student". If it is not a request, return is_request false and
no suggestions. Return JSON only."""

BRIEF_SYSTEM = """You help a special-education teacher get one printable page made (a worksheet, a
coloring page, flashcards or classroom labels). You get the teacher's choices (type, how many items on
the page, topic, due) and, when there is one, what the teacher said in the classroom after pressing
"Teacher Notes" (masked), plus de-identified context about the student(s): grade, IEP goal ids and
descriptions, accommodations, recent session summaries.

Return JSON:
  teacher_request: what the teacher wants on the page, in one or two plain sentences, as the teacher
    would ask a designer: the activity, how many items, the topic, the grade level and anything from the
    IEP context that changes the design (e.g. "large print", "few items per page", "picture support").
    Never a name: say "the student" if needed. No diagnoses, no disability words, no scores.
  title: a short title for the page (a few words, no names).
  variations: exactly 3 short design focuses that make the 3 drafts clearly different, e.g.
    "simple geometric shapes and extra thick borders", "one large picture with lots of open space",
    "big bold words under each picture".
  goal_ids: the IEP goal ids this supports (from the context only), may be empty.
When class_domain is given, it is the class's subject and the school's core teaching methodology for it
(e.g. "Mathematics: Concrete-Pictorial-Abstract (CPA) ..."): shape the request and the 3 variations
so the page fits that approach (e.g. pictures of manipulatives before numbers for CPA, multisensory
letter work for Orton-Gillingham). Do not name the methodology on the page.
When style is given (flashcards), every part of the request and all 3 variations must fit that style
(style_label says what it means): for "color" ask for flat, bright cartoon color and never photos or
plain outlines; for "line" ask for black line art with no color fills; for "photo" ask for photo-like
pictures. The variations then differ in layout and wording, not in style.
Return JSON only."""


STYLE_GUIDE = {"line": "black line art on a plain page (easy to color in), no color fills",
               "color": "flat, bright cartoon color with dark outlines (no photos, no plain outlines)",
               "photo": "photo-like pictures"}

def mode() -> str:
    m = os.environ.get("MATERIALS_AI")
    if m in ("live", "offline", "off"):
        return m
    from . import planner_ai
    return planner_ai.mode()


def svg_model() -> str:
    return os.environ.get("MATERIALS_SVG_MODEL") or DEFAULT_SVG_MODEL


def svg_location() -> str:
    return os.environ.get("MATERIALS_SVG_LOCATION") or DEFAULT_SVG_LOCATION


# ---------------------------------------------------------------- shape checks

def clamp_count(kind: str, n) -> int:
    lo, default, hi = COUNT[kind]
    try:
        n = int(n)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _s(v, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def clean_suggestions(reply: dict, indexes: list[int]) -> dict:
    """index -> {"is_request", "suggestions": [<= 3 {type, count, topic, due}]}."""
    out = {i: {"is_request": False, "suggestions": []} for i in indexes}
    for n in (reply or {}).get("notes") or []:
        if not isinstance(n, dict) or n.get("index") not in out:
            continue
        sugs, seen = [], set()
        for s in n.get("suggestions") or []:
            if not isinstance(s, dict) or s.get("type") not in TYPES:
                continue
            kind = s["type"]
            item = {"type": kind, "count": clamp_count(kind, s.get("count")), "topic": _s(s.get("topic"), 60),
                    "due": _due(s.get("due"))}
            if kind == "flashcards":
                item["style"] = s.get("style") if s.get("style") in STYLES else "line"
            key = (kind, item["topic"].lower())
            if key in seen:
                continue
            seen.add(key)
            sugs.append(item)
            if len(sugs) == 3:
                break
        req = bool(n.get("is_request")) and bool(sugs)
        out[n["index"]] = {"is_request": req, "suggestions": sugs if req else []}
    return out


def _due(v) -> str:
    v = str(v or "").strip().lower()
    if v in ("today", "tomorrow"):
        return v
    return v if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else ""


def clean_brief(reply: dict, kind: str, count: int, topic: str) -> dict:
    """{title, teacher_request, variations: [3], goal_ids}; falls back to plain defaults per field."""
    from .material_svg import DEFAULT_VARIATIONS
    reply = reply if isinstance(reply, dict) else {}
    req = _s(reply.get("teacher_request"), 400)
    if not req:
        req = f"Make {count} {TYPE_LABEL[kind].lower()} item{'s' if count != 1 else ''}" + (f" about {topic}" if topic else "") + "."
    var = [_s(v, 120) for v in reply.get("variations") or [] if _s(v, 120)]
    for d in DEFAULT_VARIATIONS[kind]:
        if len(var) >= DRAFTS:
            break
        if d not in var:
            var.append(d)
    goal_ids = [g for g in (reply.get("goal_ids") or []) if isinstance(g, str) and re.fullmatch(r"[A-Za-z0-9_.\-]{1,24}", g)]
    return {"title": _s(reply.get("title"), 80) or (f"{TYPE_LABEL[kind]}: {topic}" if topic else TYPE_LABEL[kind]),
            "teacher_request": req, "variations": var[:DRAFTS], "goal_ids": goal_ids[:6]}


def _int(v, lo: int, hi: int, default: int) -> int:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def clean_params(key: str, reply: dict, count: int, topic: str) -> dict:
    """The AI's parameters for a code template / photo set, clamped to what the page can hold. The
    numbers on the page are computed by our code from these, never written by the AI."""
    from .material_templates import PHOTO_MAX_WORD, clean_word
    r = reply if isinstance(reply, dict) else {}
    title = " ".join(re.sub(r"[^\w ,.'&()\-]", "", str(r.get("title") or "")).split())[:60]
    if key == "multiplication_table":
        end = _int(r.get("end"), 1, 12, count)
        start = _int(r.get("start"), 0, end, 1)
        focus = sorted({_int(f, start, end, start) for f in (r.get("focus") or [])[:4] if isinstance(f, (int, float, str))})
        return {"title": title or f"Multiplication {start}-{end}", "start": start, "end": end, "focus": focus}
    if key == "number_line":
        start = _int(r.get("start"), -100, 1000, 0)
        step = _int(r.get("step"), 1, 100, 1)
        end = _int(r.get("end"), start + step, start + 100 * step, start + count * step)
        if (end - start) // step > 20:
            step = -(-(end - start) // 20)
        end = start + ((end - start) // step) * step
        return {"title": title or f"Number line {start} to {end}", "start": start, "end": end, "step": step,
                "label_every": _int(r.get("label_every"), 1, 5, 1)}
    if key == "letter_tracing":
        case = r.get("case") if r.get("case") in ("upper", "lower", "as_written") else "as_written"
        lines = [clean_word(x, 12) for x in (r.get("lines") or []) if clean_word(x, 12)]
        lines = [x.upper() if case == "upper" else x.lower() if case == "lower" else x for x in lines][:count]
        if not lines:
            lines = ["A a", "B b", "C c", "D d", "E e", "F f", "G g", "H h"][:count]
        return {"title": title or "Trace the letters", "lines": lines, "case": case}
    if key == "flashcards_photo":
        cards = []
        for c in r.get("cards") or []:
            if isinstance(c, dict) and clean_word(c.get("word")):
                pic = " ".join(str(c.get("picture") or c.get("word")).replace('"', "'").split())[:160]
                cards.append({"word": clean_word(c.get("word"), PHOTO_MAX_WORD), "picture": pic})
        n = min(count, PHOTO_MAX)
        if not cards:
            cards = [{"word": w, "picture": w} for w in (topic or "apple").split()[:n]]
        return {"title": title or (f"{topic.title()} Flashcards" if topic else "Flashcards"), "cards": cards[:n]}
    raise ValueError(key)


# ---------------------------------------------------------------- offline stand-ins

_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
        "ten": 10, "twelve": 12, "a couple": 2, "a few": 3, "a dozen": 12, "dozen": 12}
_KIND_WORDS = [("multiplication_table", r"multiplication|times tables?"), ("number_line", r"number lines?"),
               ("letter_tracing", r"tracing|trace (?:the )?letters|handwriting"),
               ("diagram", r"life cycle|diagram"), ("coloring", r"colou?r(ing)?\b|colou?r in|crayon page"), ("flashcards", r"flash ?cards?|cards"),
               ("labels", r"labels?|label the|tags? for"), ("worksheet", r"work ?sheets?|practice sheets?|handouts?")]
_REQUEST = re.compile(r"\b(make|create|print|prepare|prep|need|get|generate|put together|could you|can you|let'?s have)\b", re.I)


def offline_suggest(payload: dict) -> dict:
    out = []
    for n in payload.get("notes") or []:
        text = " ".join(line.split(": ", 1)[-1] for line in n.get("transcript") or [])
        kinds = [k for k, rx in _KIND_WORDS if re.search(rx, text, re.I)]
        if not kinds or not _REQUEST.search(text):
            out.append({"index": n["index"], "is_request": False, "suggestions": []})
            continue
        m = re.search(r"\b(\d{1,2}|" + "|".join(_NUM) + r")\b\s+(?:\w+\s+){0,3}?(?:work ?sheets?|colou?ring|pages?|flash ?cards?|cards|labels?)", text, re.I)
        count = None
        if m:
            g = m.group(1).lower()
            count = int(g) if g.isdigit() else _NUM.get(g)
        topic = ""
        for rx in (r"\babout\s+((?:the\s+)?[a-z]+(?:\s+(?:and|&)\s+[a-z]+)?)", r"\b(?:on|with|of)\s+((?:the\s+)?[a-z]+(?:\s+(?:and|&)\s+[a-z]+)?)"):
            for tm in re.finditer(rx, text, re.I):
                cand = re.sub(r"^the\s+", "", tm.group(1), flags=re.I)
                if cand.lower().split()[0] not in ("student", "students", "their", "his", "her", "it", "them", "a", "an",
                                                   "my", "your", "past", "grade", "level", "iep", "each", "all"):
                    topic = cand
                    break
            if topic:
                break
        due = "tomorrow" if re.search(r"\btomorrow\b", text, re.I) else "today" if re.search(r"\btoday\b", text, re.I) else ""
        first = kinds[0]
        others = [k for k in ("worksheet", "coloring", "flashcards", "labels") if k != first]
        sugs = [{"type": first, "count": count, "topic": topic, "due": due}]
        if first == "flashcards":
            sugs[0]["style"] = ("photo" if re.search(r"\b(photos?|real pictures?|realistic)\b", text, re.I)
                                else "color" if re.search(r"\bcolou?r(ed|ful)?\b", text, re.I) else "line")
        for k in others[:2]:
            sugs.append({"type": k, "count": None, "topic": topic, "due": due})
        out.append({"index": n["index"], "is_request": True, "suggestions": sugs})
    return {"notes": out}


def offline_brief(payload: dict) -> dict:
    kind, count = payload["type"], payload["count"]
    topic = payload.get("topic") or "our classroom"
    grade = (payload.get("grades") or [""])[0]
    req = f"Make {count} {TYPE_LABEL[kind].lower()} item{'s' if count != 1 else ''} about {topic}"
    req += f" for grade {grade}, with large print and simple pictures." if grade else ", with large print and simple pictures."
    return {"title": f"{topic.title()} {TYPE_LABEL[kind]}", "teacher_request": req, "variations": [],
            "goal_ids": [g["goal_id"] for s in payload.get("students") or [] for g in s.get("iep_goals") or []][:2]}


_FARM = ["cow", "pig", "horse", "sheep", "chicken", "duck", "goat", "rabbit"]


def offline_params(key: str, payload: dict) -> dict:
    count, topic = payload["count"], payload.get("topic") or ""
    if key == "multiplication_table":
        return {"start": 1, "end": count, "focus": []}
    if key == "number_line":
        return {"start": 0, "end": count, "step": 1}
    if key == "letter_tracing":
        words = [w for w in re.findall(r"[A-Za-z]+", topic)][:count]
        return {"lines": words or ["A a", "B b", "C c"][:count], "case": "as_written"}
    words = [w for w in re.findall(r"[A-Za-z]+", topic) if w.lower() not in ("and", "the", "of", "farm", "animals")]
    words = words or _FARM
    return {"title": f"{topic.title() or 'Picture'} Flashcards",
            "cards": [{"word": words[i % len(words)].title(), "picture": f"a {words[i % len(words)]}"} for i in range(count)]}


def offline_picture(prompt: str) -> bytes:
    """A plain colored square standing in for the image model."""
    import io

    from PIL import Image
    h = hashlib.sha256(prompt.encode()).digest()
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (h[0], h[1], h[2])).save(buf, "PNG")
    return buf.getvalue()


def offline_svg(system: str, prompt: str) -> str:
    """A simple deterministic page standing in for the model (boxes with the request's words)."""
    from xml.sax.saxutils import escape
    m = re.search(r'Teacher Request: "([^"]*)"', prompt)
    words = (m.group(1) if m else "practice").split()[:8]
    n = int(hashlib.sha256(prompt.encode()).hexdigest()[:2], 16) % 3 + 4
    cells = []
    for i in range(n):
        x, y = 60 + (i % 2) * 350, 160 + (i // 2) * 190
        cells.append(f'<rect x="{x}" y="{y}" width="330" height="170" rx="20" fill="#FFFFFF" stroke="#000000" stroke-width="5"/>'
                     f'<circle cx="{x + 165}" cy="{y + 70}" r="40" fill="none" stroke="#000000" stroke-width="5"/>'
                     f'<text x="{x + 165}" y="{y + 150}" font-size="28" text-anchor="middle">{escape(words[i % len(words)] if words else "")}</text>')
    return ('```svg\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 1000">'
            '<g id="school-header"><text x="400" y="55">[INSERT SCHOOL NAME HERE]</text></g>'
            + "".join(cells) + "</svg>\n```")


# ---------------------------------------------------------------- live calls

def _json_call(system: str, payload: dict, schema: dict, temperature: float = 0.3) -> dict:
    from gemini_analyzer.analyzer import _client, model_name
    client = _client()
    r = client.models.generate_content(
        model=model_name(), contents=json.dumps(payload, ensure_ascii=False, indent=1),
        config={"system_instruction": system, "response_mime_type": "application/json",
                "response_json_schema": schema, "temperature": temperature})
    text = (r.text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def _sleep(seconds: float) -> None:
    import time
    time.sleep(seconds)


def live_svg(system: str, prompt: str) -> str:
    from google import genai
    client = genai.Client(vertexai=True, project=os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("PROJECT_ID"),
                          location=svg_location())
    for attempt in range(3):  # shared quota answers 429 in bursts: back off and retry
        try:
            r = client.models.generate_content(
                model=svg_model(), contents=prompt,
                config={"system_instruction": system, "temperature": 0.7, "max_output_tokens": 24000,
                        "thinking_config": {"thinking_level": "low"}})
            return r.text or ""
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "code", None) != 429 or attempt == 2:
                raise
            _sleep(2 * 3 ** attempt)  # 2, 6 s
    return ""


def image_model() -> str:
    return os.environ.get("MATERIALS_IMAGE_MODEL") or DEFAULT_IMAGE_MODEL


def image_location() -> str:
    return os.environ.get("MATERIALS_IMAGE_LOCATION") or DEFAULT_IMAGE_LOCATION


class PictureBusy(Exception):
    """The image model's quota answered 429: try again on a later step."""


def live_picture(prompt: str) -> bytes:
    """One picture, one call, no in-call retry: the caller paces calls (the quota is about 2 a minute)."""
    from google import genai
    client = genai.Client(vertexai=True, project=os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("PROJECT_ID"),
                          location=image_location())
    try:
        r = client.models.generate_content(
            model=image_model(), contents=prompt,
            config={"response_modalities": ["IMAGE"], "image_config": {"aspect_ratio": "1:1", "image_size": "1K"}})
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "code", None) == 429:
            raise PictureBusy() from exc
        raise
    for cand in r.candidates or []:
        for part in (cand.content.parts if cand.content else None) or []:
            data = getattr(getattr(part, "inline_data", None), "data", None)
            if data:
                return data
    raise RuntimeError("no image in the reply")


PARAMS_TAIL = ("\n\nYou are given the teacher's request (names already removed). When class_domain is given (the "
               "class's subject and core teaching methodology), fit the content to it. Return JSON only, in the given schema.")


class MaterialAI:
    """The AI calls; tests pass call= / svg= / picture= stand-ins."""

    def __init__(self, *, call=None, svg=None, picture=None):
        self._call, self._svg, self._picture = call, svg, picture

    def json(self, what: str, system: str, payload: dict, schema: dict) -> dict:
        if self._call is not None:
            return self._call(what, payload)
        m = mode()
        if m == "offline":
            if what.startswith("params:"):
                return offline_params(what[7:], payload)
            return offline_suggest(payload) if what == "suggest" else offline_brief(payload)
        if m == "live":
            return _json_call(system, payload, schema)
        raise RuntimeError("materials AI off")

    def suggest(self, payload: dict) -> dict:
        return self.json("suggest", SUGGEST_SYSTEM, payload, SUGGEST_SCHEMA)

    def brief(self, payload: dict) -> dict:
        return self.json("brief", BRIEF_SYSTEM, payload, BRIEF_SCHEMA)

    def params(self, key: str, system: str, payload: dict) -> dict:
        return self.json(f"params:{key}", system + PARAMS_TAIL, payload, PARAMS_SCHEMA[key])

    def picture(self, prompt: str) -> bytes:
        if self._picture is not None:
            return self._picture(prompt)
        m = mode()
        if m == "offline":
            return offline_picture(prompt)
        if m == "live":
            return live_picture(prompt)
        raise RuntimeError("materials AI off")

    def svg(self, system: str, prompt: str) -> str:
        if self._svg is not None:
            return self._svg(system, prompt)
        m = mode()
        if m == "offline":
            return offline_svg(system, prompt)
        if m == "live":
            return live_svg(system, prompt)
        raise RuntimeError("materials AI off")
