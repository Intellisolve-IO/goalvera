"""SVG pages for classroom materials (David, Oct 10 2026; tasks#99).

A Gemini text model (MATERIALS_SVG_MODEL, default gemini-3.8-flash) writes each draft as one SVG page
under an admin-editable system instruction (DEFAULT_INSTRUCTION, David's text, verbatim). What the model
returns is untrusted:

  sanitize()    markdown fences and chatter stripped; must parse as XML with an <svg> root; no DOCTYPE /
                entities; only drawing elements survive (no script, foreignObject, image, use, a,
                animation, ...); only presentation attributes survive (no on* handlers, no href, no
                url(...) anywhere, CSS in style / <style> limited to plain declarations); fonts pinned to
                the bundled sans font; root forced to viewBox 0 0 800 1000. Anything else -> SvgInvalid
                (the caller retries the model).
  set_header()  the school-header group is always ours: any group the model made is dropped and the
                code inserts the school name from the app's setting plus blank Name / Date lines.
                No student name is ever put in it.
  render_png()  server-side rendering with resvg (bundled fonts, no system fonts, no external files).
                The browser only ever gets PNG previews and PDFs, never the SVG.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

SVG_NS = "http://www.w3.org/2000/svg"
VIEWBOX = "0 0 800 1000"
WIDTH, HEIGHT = 800, 1000
MAX_SVG_CHARS = 400_000
MAX_ELEMENTS = 4000
MAX_INSTRUCTION = 8000
SCHOOL_PLACEHOLDER = "[INSERT SCHOOL NAME HERE]"
FONT = "Bitstream Vera Sans"
ACTIVITY = {"coloring": "Coloring", "flashcards": "Flashcard", "worksheet": "Worksheet", "labels": "Labels",
            "diagram": "Diagram"}
DEFAULT_VARIATIONS = {
    "coloring": ["one large simple picture with extra thick outlines", "simple geometric shapes and extra thick borders",
                 "a small scene with a few big shapes and lots of open space"],
    "flashcards": ["simple geometric shapes and extra thick borders", "large pictures with big bold words",
                   "rounded cards with a simple picture and a short word"],
    "worksheet": ["simple geometric shapes and extra thick borders", "large pictures with circle-the-answer choices",
                  "matching with big pictures and dotted lines to trace"],
    "labels": ["simple geometric shapes and extra thick borders", "big bold words with a small simple picture",
               "rounded labels with icons and lots of open space"],
    "diagram": ["a circle of stages joined by thick arrows", "stages in a row, left to right, with big numbered arrows",
                "a large central picture with labeled parts and leader lines"],
}

DEFAULT_INSTRUCTION = """You are an expert SVG designer creating printable visual activities, flashcards, and coloring pages for special education students.
System Instructions: 
OUTPUT CONSTRAINTS:
1. Output MUST be valid, raw SVG code inside an <svg>...</svg> root tag.
2. Return ONLY the raw SVG code. Do NOT wrap the output in markdown code blocks (no ```xml or ```svg). Do NOT include conversational greetings or text explanations.
3. Use viewBox="0 0 800 1000" for standard portrait printable page dimensions.

SCHOOL BRANDING MANDATE:
Include a standardized header group at the top of every SVG:
<g id="school-header">
  <rect x="20" y="20" width="760" height="90" fill="none" stroke="#000000" stroke-width="2"/>
  <text x="400" y="55" font-family="Arial, sans-serif" font-size="24" font-weight="bold" fill="#000000" text-anchor="middle">[INSERT SCHOOL NAME HERE]</text>
  <text x="40" y="90" font-family="Arial, sans-serif" font-size="16" fill="#000000">Name: ________________________</text>
  <text x="550" y="90" font-family="Arial, sans-serif" font-size="16" fill="#000000">Date: ______________</text>
</g>

SPECIAL EDUCATION VISUAL STYLE:
- Line Art: Use high-contrast black strokes (`stroke="#000000"`, `stroke-width="4px"` to `6px"`, `stroke-linecap="round"`, `stroke-linejoin="round"`).
- Fills: Default to `fill="none"` or `fill="#FFFFFF"` so items are easy to color in or trace over.
- Geometry: Use clear, simple, recognized shapes. Avoid tiny, intricate details or overlapping paths that cause visual confusion.
- Bounds: Keep all activity artwork within x: 50 to 750 and y: 150 to 950 to fit below the school header."""

_LINE_STYLE = DEFAULT_INSTRUCTION[DEFAULT_INSTRUCTION.index("SPECIAL EDUCATION VISUAL STYLE:"):]
# Color flashcards (David, Oct 10): the same instruction with the visual style switched to flat, bright cartoon color.
COLOR_INSTRUCTION = DEFAULT_INSTRUCTION.replace(_LINE_STYLE, """SPECIAL EDUCATION VISUAL STYLE (COLOR):
- Color: Flat, bright cartoon colors (solid fills such as #F94144, #F8961E, #F9C74F, #90BE6D, #43AA8B, #577590). No gradients, shadows, textures or photographs.
- Outlines: Keep a clear dark outline on every shape (`stroke="#000000"`, `stroke-width="3px"` to `5px"`, `stroke-linecap="round"`, `stroke-linejoin="round"`).
- Contrast: Words in large bold black text on a white or very light card. Never put text on a busy or dark fill.
- Geometry: Use clear, simple, recognized shapes. Avoid tiny, intricate details or overlapping paths that cause visual confusion.
- Bounds: Keep all activity artwork within x: 50 to 750 and y: 150 to 950 to fit below the school header.""")

ALLOWED = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan",
           "defs", "style", "title", "desc", "clipPath", "linearGradient", "radialGradient", "stop", "pattern",
           "symbol", "marker"}
ALLOWED_ATTRS = {
    "id", "class", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "width", "height", "d",
    "points", "transform", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin",
    "stroke-dasharray", "stroke-dashoffset", "stroke-miterlimit", "stroke-opacity", "fill-opacity",
    "fill-rule", "clip-rule", "opacity", "font-family", "font-size", "font-weight", "font-style",
    "text-anchor", "dominant-baseline", "alignment-baseline", "letter-spacing", "word-spacing",
    "text-decoration", "dx", "dy", "rotate", "textLength", "lengthAdjust", "style", "viewBox",
    "preserveAspectRatio", "offset", "stop-color", "stop-opacity", "gradientUnits", "gradientTransform",
    "patternUnits", "patternTransform", "markerWidth", "markerHeight", "refX", "refY", "orient",
    "markerUnits", "clipPathUnits", "clip-path", "marker-start", "marker-mid", "marker-end", "display",
    "visibility", "vector-effect", "paint-order",
}
REF_ATTRS = {"clip-path", "marker-start", "marker-mid", "marker-end", "fill", "stroke"}
CSS_PROPS = {a for a in ALLOWED_ATTRS if "-" in a or a in ("fill", "stroke", "opacity", "display", "visibility")}
_LOCAL_REF = re.compile(r"^url\(\s*#[A-Za-z][\w.-]{0,63}\s*\)$")
_BAD_VALUE = re.compile(r"url\s*\(|javascript:|data:|expression\s*\(|@import|behavio(u)?r\s*:|\\", re.I)
_CSS_OK = re.compile(r"^[\w\s#.,:;%()\-{}*>+\"'/]*$")


class SvgInvalid(ValueError):
    pass


def _local(tag) -> str:
    return tag.split("}", 1)[1] if isinstance(tag, str) and tag.startswith("{") else str(tag)


def extract(raw: str) -> str:
    """The <svg>...</svg> part of a model reply (markdown fences and chatter dropped)."""
    text = str(raw or "")
    text = re.sub(r"```[a-zA-Z]*", "", text)
    a, b = text.find("<svg"), text.rfind("</svg>")
    if a < 0 or b < a:
        raise SvgInvalid("no svg root")
    return text[a:b + len("</svg>")]


def _css(text: str) -> str:
    """Plain CSS declarations only; any rule or declaration with url()/import/expression is dropped."""
    text = re.sub(r"/\*.*?\*/", "", str(text or ""), flags=re.S)
    if _BAD_VALUE.search(text) or "@" in text or "<" in text or not _CSS_OK.match(text):
        return ""
    out = []
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text):
        decl = _decls(body)
        if decl:
            out.append(f"{sel.strip()} {{ {decl} }}")
    return "\n".join(out)


def _decls(body: str) -> str:
    keep = []
    for part in str(body or "").split(";"):
        if ":" not in part:
            continue
        k, v = (x.strip() for x in part.split(":", 1))
        if k.lower() in CSS_PROPS and v and not _BAD_VALUE.search(v):
            if k.lower() == "font-family":
                v = FONT
            keep.append(f"{k.lower()}: {v}")
    return "; ".join(keep)


def sanitize(raw: str) -> str:
    """Model reply -> a safe SVG string (viewBox 0 0 800 1000). Raises SvgInvalid."""
    if re.search(r"<!DOCTYPE|<!ENTITY|<\?xml-stylesheet", str(raw or ""), re.I):
        raise SvgInvalid("doctype")
    text = extract(raw)
    if len(text) > MAX_SVG_CHARS:
        raise SvgInvalid("too large")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SvgInvalid("not xml") from exc
    if _local(root.tag) != "svg":
        raise SvgInvalid("root is not svg")
    count = [0]

    def clean(el) -> None:
        for child in list(el):
            tag = _local(child.tag)
            if not isinstance(child.tag, str) or tag not in ALLOWED or tag == "svg":
                el.remove(child)
                continue
            count[0] += 1
            clean(child)
        for k in list(el.attrib):
            name = _local(k)
            v = el.attrib[k]
            del el.attrib[k]
            if name not in ALLOWED_ATTRS or name.lower().startswith("on") or k.startswith("{"):
                continue
            if name == "style":
                v = _decls(v)
                if not v:
                    continue
            elif name == "font-family":
                v = FONT
            elif _BAD_VALUE.search(v):
                if not (name in REF_ATTRS and _LOCAL_REF.match(v.strip())):
                    continue
            el.set(name, v)
        if _local(el.tag) == "style":
            el.text = _css(el.text or "")
            for c in list(el):
                el.remove(c)

    clean(root)
    if count[0] > MAX_ELEMENTS:
        raise SvgInvalid("too many elements")
    drawn = sum(1 for e in root.iter() if _local(e.tag) in ("path", "rect", "circle", "ellipse", "line",
                                                               "polyline", "polygon"))
    if drawn < 3:
        raise SvgInvalid("nothing drawn")
    for k in list(root.attrib):
        if _local(k) in ("width", "height", "viewBox", "preserveAspectRatio", "x", "y"):
            del root.attrib[k]
    root.set("viewBox", VIEWBOX)
    root.set("width", str(WIDTH))
    root.set("height", str(HEIGHT))
    ET.register_namespace("", SVG_NS)
    root.tag = f"{{{SVG_NS}}}svg"
    for e in root.iter():
        if isinstance(e.tag, str) and not e.tag.startswith("{"):
            e.tag = f"{{{SVG_NS}}}{e.tag}"
    return ET.tostring(root, encoding="unicode")


def header_group(school: str) -> str:
    s = escape(" ".join(str(school or "").split())[:80])
    f = FONT
    return (f'<g id="school-header">'
            f'<rect x="20" y="20" width="760" height="90" fill="#FFFFFF" stroke="#000000" stroke-width="2"/>'
            f'<text x="400" y="55" font-family="{f}" font-size="24" font-weight="bold" fill="#000000" '
            f'text-anchor="middle">{s}</text>'
            f'<text x="40" y="90" font-family="{f}" font-size="16" fill="#000000">Name: ________________________</text>'
            f'<text x="550" y="90" font-family="{f}" font-size="16" fill="#000000">Date: ______________</text></g>')


def set_header(svg: str, school: str) -> str:
    """Drop any school-header the model drew (and any leftover placeholder text) and put ours on top."""
    ET.register_namespace("", SVG_NS)
    root = ET.fromstring(svg)

    def drop(el):
        for c in list(el):
            if c.get("id") == "school-header" or (_local(c.tag) in ("text", "tspan")
                                                  and SCHOOL_PLACEHOLDER in "".join(c.itertext())):
                el.remove(c)
            else:
                drop(c)
    drop(root)
    hdr = ET.fromstring(f'<svg xmlns="{SVG_NS}">{header_group(school)}</svg>')[0]
    root.append(hdr)        # last = drawn on top of anything the model put there
    return ET.tostring(root, encoding="unicode")


def texts(svg: str) -> list[str]:
    root = ET.fromstring(svg)
    return [" ".join("".join(e.itertext()).split()) for e in root.iter() if _local(e.tag) == "text"]


def map_text(svg: str, fn) -> str:
    """Apply fn to every text run (names scrubbed out of what the model wrote)."""
    ET.register_namespace("", SVG_NS)
    root = ET.fromstring(svg)
    for e in root.iter():
        if _local(e.tag) in ("text", "tspan", "title", "desc"):
            if e.text:
                e.text = fn(e.text)
            if e.tail and e.tail.strip():
                e.tail = fn(e.tail)
    return ET.tostring(root, encoding="unicode")


def _fonts() -> list[str]:
    import reportlab
    d = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
    return [os.path.join(d, f) for f in ("Vera.ttf", "VeraBd.ttf", "VeraIt.ttf", "VeraBI.ttf") if os.path.exists(os.path.join(d, f))]


def render_png(svg: str, width: int = 1600) -> bytes:
    """Server-side render (resvg): bundled fonts only, white background."""
    import resvg_py
    return bytes(resvg_py.svg_to_bytes(svg_string=svg, width=width, background="#ffffff",
                                       skip_system_fonts=True, font_files=_fonts(), font_family=FONT,
                                       sans_serif_family=FONT, serif_family=FONT))


def user_prompt(request: str, kind: str, n: int, focus: str, of: int = 3, approach: str | None = None) -> str:
    """The per-draft prompt, in David's shape. approach: the class's domain and core methodology
    (Domains tab, tasks#103), e.g. "Mathematics: Concrete-Pictorial-Abstract (CPA) ..."; no student data."""
    req = " ".join(str(request or "").replace('"', "'").split())
    line = f"Teaching Approach: {' '.join(str(approach).split())}\n" if approach and str(approach).strip() else ""
    return (f'Teacher Request: "{req}"\n'
            f"Activity Type: {ACTIVITY[kind]}\n"
            f"{line}"
            f"Draft Variation: {n} of {of} ({' '.join(str(focus or '').split())})")
