"""School name and logo shown in the review site's top bar (David, Oct 9 2026; tasks#85).

Admins change them on the Settings tab; everyone else only sees them. Stored in the demo
bucket under branding/, one create-only JSON record per change (same pattern as planner/ and
bug_reports/: review-web can create and read there but never overwrite or delete; set up by
infra/setup_branding.sh). The newest record is the current setting. Reset demo state writes a
record with the defaults.

Logo uploads (PNG, JPEG or SVG, at most 1 MB) are never served as uploaded:
  * SVG is cleaned (no DTD/entities, no <script>/<foreignObject>, no event attributes, no
    external links) and then rendered to a PNG on the server with resvg. The SVG text is
    discarded; only the PNG is kept, so no script in an SVG can ever reach a browser.
  * PNG and JPEG are decoded and re-encoded from raw pixels, which drops every metadata
    chunk (EXIF, GPS, ICC, text). Images are scaled to fit 512 x 512.
  * The logo is served with its real image content type (image/png or image/jpeg) and
    nosniff; the stored type is decided by what the bytes decode as, never by the upload's
    declared type.

School profiles (David, Oct 10 2026; tasks#98): the demo school is Everyday Heroes Academy
(fictional; badge "EH" and a built-in shield logo, assets/everyday-heroes-badge.png). A second
profile, Demo School (badge "DS"), holds branding saved before this product was named Goalvera.
It is not shown by default. SCHOOL_PROFILE (infra/config.sh) picks the profile; each record
carries its profile. Records with no profile, and records still tagged with the old profile id,
belong to Demo School, so anything an admin saved there is still there when that profile is on.

Changes are written to the Activity Log as settings_changed with which field changed
(school_name, school_logo, reset). File contents, names and the new text are not logged.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import io
import os
import re
from pathlib import Path
import uuid
import unicodedata
import xml.etree.ElementTree as ET

from .notes import ApiError, signed_in

PREFIX = "branding/"
ASSETS = Path(__file__).resolve().parent / "assets"
PROFILES = {
    # the demo school (fictional sample data): shown by default
    "everyday-heroes": {"school_name": "Everyday Heroes Academy", "badge_text": "EH",
                        "logo_file": "everyday-heroes-badge.png"},
    # neutral placeholder profile (not shown unless SCHOOL_PROFILE=demo-school)
    "demo-school": {"school_name": "Demo School", "badge_text": "DS", "logo_file": None},
}
DEFAULT_PROFILE = "everyday-heroes"
LEGACY_PROFILE = "demo-school"   # branding records from before Oct 10 have no profile
# Bucket records and SCHOOL_PROFILE values written before the Goalvera rename.
# Reading still maps this id onto demo-school. New writes use demo-school only.
STORED_PROFILE_ALIASES = {"atlanta-prep": "demo-school"}


def canonical_profile(value: str | None) -> str:
    if not value:
        return LEGACY_PROFILE
    return STORED_PROFILE_ALIASES.get(value, value)


def active_profile() -> str:
    p = canonical_profile(os.environ.get("SCHOOL_PROFILE") or DEFAULT_PROFILE)
    return p if p in PROFILES else DEFAULT_PROFILE


def profile() -> dict:
    return PROFILES[active_profile()]


def builtin_logo() -> bytes | None:
    f = profile().get("logo_file")
    return (ASSETS / f).read_bytes() if f and (ASSETS / f).is_file() else None


DEFAULT_NAME = PROFILES[DEFAULT_PROFILE]["school_name"]
DEFAULT_BADGE = PROFILES[DEFAULT_PROFILE]["badge_text"]
MAX_NAME = 80
MAX_UPLOAD = 1024 * 1024            # 1 MB
MAX_SIDE = 512                      # stored logo fits in 512 x 512 (shown at ~28 px tall)
MAX_PIXELS = 40_000_000             # refuse decompression bombs before decoding
MAX_ASPECT = 10.0

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
_DROP_TAGS = {"script", "foreignobject", "iframe", "embed", "object", "audio", "video", "handler", "listener"}
_SAFE_DATA_HREF = re.compile(r"^data:image/(png|jpeg|jpg|gif|webp);base64,[A-Za-z0-9+/=\s]+$")
_NUM = re.compile(r"^\s*([0-9]*\.?[0-9]+)")


class LogoRejected(ApiError):
    def __init__(self, code: str):
        super().__init__(422, code)


# ---------------------------------------------------------------- image cleaning (pure functions)

def sniff(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith(b"<") and b"<svg" in data[:65536].lower():
        return "svg"
    return None


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def clean_svg(text: str) -> str:
    """The SVG minus anything active or external. Raises LogoRejected if it can't be parsed safely."""
    low = text.lower()
    if "<!doctype" in low or "<!entity" in low:
        raise LogoRejected("SVG_DTD_NOT_ALLOWED")      # no entity expansion / XXE
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise LogoRejected("SVG_INVALID") from exc
    if _local(root.tag) != "svg":
        raise LogoRejected("SVG_INVALID")

    def walk(el):
        for child in list(el):
            if _local(child.tag) in _DROP_TAGS or not isinstance(child.tag, str):
                el.remove(child)
                continue
            walk(child)
        for attr in list(el.attrib):
            name, val = _local(attr), el.attrib[attr]
            if name.startswith("on"):
                del el.attrib[attr]                      # onload= etc.
            elif name == "href" or attr == f"{{{XLINK_NS}}}href":
                v = val.strip()
                if not (v.startswith("#") or _SAFE_DATA_HREF.match(v)):
                    del el.attrib[attr]                  # no files, no URLs, no javascript:
            elif name == "style" and ("url(" in val.lower() and "url(#" not in val.lower().replace(" ", "")
                                      or "@import" in val.lower()):
                del el.attrib[attr]
        if _local(el.tag) == "style" and el.text and ("@import" in el.text.lower() or re.search(
                r"url\(\s*['\"]?(?!#)", el.text, re.I)):
            el.text = ""
    walk(root)
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    return ET.tostring(root, encoding="unicode")


def _svg_aspect(root_text: str) -> float:
    root = ET.fromstring(root_text)
    vb = (root.get("viewBox") or "").replace(",", " ").split()
    if len(vb) == 4:
        try:
            w, h = float(vb[2]), float(vb[3])
            if w > 0 and h > 0:
                return w / h
        except ValueError:
            pass
    mw, mh = _NUM.match(root.get("width") or ""), _NUM.match(root.get("height") or "")
    if mw and mh and float(mw.group(1)) > 0 and float(mh.group(1)) > 0:
        return float(mw.group(1)) / float(mh.group(1))
    return 1.0


def _reencode(im, fmt: str) -> tuple[bytes, int, int]:
    """Fresh image from raw pixels (drops EXIF/ICC/text chunks), scaled to fit MAX_SIDE."""
    from PIL import Image
    im.thumbnail((MAX_SIDE, MAX_SIDE))
    mode = "RGBA" if fmt == "png" else "RGB"
    im = im.convert(mode)
    clean = Image.frombytes(mode, im.size, im.tobytes())
    out = io.BytesIO()
    if fmt == "png":
        clean.save(out, format="PNG", optimize=True)
    else:
        clean.save(out, format="JPEG", quality=90)
    return out.getvalue(), clean.width, clean.height


def _open_raster(data: bytes, fmt: str):
    import warnings
    from PIL import Image, ImageOps
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            im = Image.open(io.BytesIO(data))
            if im.format != fmt.upper() or im.width * im.height > MAX_PIXELS:
                raise LogoRejected("IMAGE_INVALID")
            im.load()
        except LogoRejected:
            raise
        except Exception as exc:  # noqa: BLE001 - any decode problem is a bad upload
            raise LogoRejected("IMAGE_INVALID") from exc
    if fmt == "jpeg":
        im = ImageOps.exif_transpose(im)               # keep the photo upright once EXIF is gone
    if im.width < 1 or im.height < 1:
        raise LogoRejected("IMAGE_INVALID")
    return im


def sanitize_logo(data: bytes) -> dict:
    """bytes as uploaded -> {"bytes", "content_type", "width", "height", "source_format"}."""
    if not data:
        raise LogoRejected("LOGO_EMPTY")
    if len(data) > MAX_UPLOAD:
        raise ApiError(413, "LOGO_TOO_LARGE")
    fmt = sniff(data)
    if fmt is None:
        raise LogoRejected("LOGO_TYPE")                 # PNG, JPEG or SVG only
    if fmt == "svg":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise LogoRejected("SVG_INVALID") from exc
        svg = clean_svg(text)
        aspect = _svg_aspect(svg)
        if not (1 / MAX_ASPECT <= aspect <= MAX_ASPECT):
            raise LogoRejected("IMAGE_INVALID")
        import resvg_py
        size = {"width": MAX_SIDE} if aspect >= 1 else {"height": MAX_SIDE}
        try:
            png = bytes(resvg_py.svg_to_bytes(svg_string=svg, **size))
        except Exception as exc:  # noqa: BLE001
            raise LogoRejected("SVG_INVALID") from exc
        im, out_fmt = _open_raster(png, "png"), "png"   # and re-encode that like any PNG
    else:
        im, out_fmt = _open_raster(data, fmt), fmt
    out, w, h = _reencode(im, out_fmt)
    return {"bytes": out, "content_type": f"image/{out_fmt}", "width": w, "height": h, "source_format": fmt}


def clean_name(value) -> str:
    if not isinstance(value, str):
        raise ApiError(400, "BAD_NAME")
    v = unicodedata.normalize("NFC", value)
    v = "".join(" " if unicodedata.category(c).startswith(("C", "Z")) else c for c in v)
    v = re.sub(r"\s+", " ", v).strip()
    if not v or len(v) > MAX_NAME:
        raise ApiError(400, "BAD_NAME")
    return v


# ---------------------------------------------------------------- the stored setting

class Branding:
    def __init__(self, store, recorder):
        self.store, self.rec = store, recorder

    def _raw(self) -> dict:
        """The newest record of the active profile (names sort by time)."""
        want = active_profile()
        for n in sorted((n for n in self.store.list(PREFIX) if n.endswith(".json")), reverse=True):
            r = self.store.read_json(n)
            if isinstance(r, dict) and canonical_profile(r.get("profile")) == want:
                return r
        return {}

    def view(self) -> dict:
        """What the top bar shows. Any signed-in person (and the laptop demo) may read it."""
        r = self._raw()
        prof = profile()
        logo, builtin = None, False
        if r.get("logo_b64") and r.get("logo_type") in ("image/png", "image/jpeg"):
            logo = {"content_type": r["logo_type"], "sha256": r.get("logo_sha256"),
                    "width": r.get("logo_width"), "height": r.get("logo_height"),
                    "url": f"/api/branding/logo?v={(r.get('logo_sha256') or '')[:12]}"}
        elif not r.get("logo_hidden") and (data := builtin_logo()):
            sha = hashlib.sha256(data).hexdigest()
            logo = {"content_type": "image/png", "sha256": sha, "builtin": True,
                    "url": f"/api/branding/logo?v=b{sha[:12]}"}
            builtin = True
        name = r.get("school_name") or prof["school_name"]
        return {"school_name": name, "badge_text": prof["badge_text"], "logo": logo,
                "is_default": name == prof["school_name"] and (builtin or (logo is None and not prof.get("logo_file"))),
                "profile": active_profile(),
                "defaults": {"school_name": prof["school_name"], "badge_text": prof["badge_text"]},
                "updated_at": r.get("updated_at")}

    def logo(self) -> tuple[bytes, str]:
        r = self._raw()
        if r.get("logo_b64") and r.get("logo_type") in ("image/png", "image/jpeg"):
            return base64.b64decode(r["logo_b64"]), r["logo_type"]
        if not r.get("logo_hidden") and (data := builtin_logo()):
            return data, "image/png"                     # the profile's built-in badge
        raise ApiError(404, "NO_LOGO")

    @staticmethod
    def _require_admin(actor):
        if not (signed_in(actor) and actor.is_admin):
            raise ApiError(403, "ADMIN_ONLY")

    def _save(self, actor, r: dict, setting: str, **details) -> dict:
        now = dt.datetime.now(dt.timezone.utc)
        r = {**r, "profile": active_profile(), "updated_at": now.isoformat(timespec="seconds"), "updated_by": actor.uid}
        name = f"{PREFIX}{now.strftime('%Y%m%dT%H%M%S.%fZ')}-{uuid.uuid4().hex[:8]}.json"
        self.store.write_json(name, r, create_only=True)
        self.rec.record(actor, "settings_changed", record_id="school_branding", required=True,
                        setting=setting, **details)
        return self.view()

    def set_name(self, actor, value) -> dict:
        self._require_admin(actor)
        name = clean_name(value)
        r = self._raw()
        if name == profile()["school_name"]:
            r.pop("school_name", None)
        else:
            r["school_name"] = name
        return self._save(actor, r, "school_name")

    def set_logo(self, actor, data: bytes) -> dict:
        self._require_admin(actor)
        img = sanitize_logo(data)                        # raises before anything is stored
        r = {**{k: v for k, v in self._raw().items() if k != "logo_hidden"},
             "logo_b64": base64.b64encode(img["bytes"]).decode(),
             "logo_type": img["content_type"], "logo_sha256": hashlib.sha256(img["bytes"]).hexdigest(),
             "logo_width": img["width"], "logo_height": img["height"]}
        return self._save(actor, r, "school_logo", image_format=img["source_format"])

    def remove_logo(self, actor) -> dict:
        self._require_admin(actor)
        r = {k: v for k, v in self._raw().items() if not k.startswith("logo_")}
        r["logo_hidden"] = True                          # also hides the built-in logo: badge text only
        return self._save(actor, r, "school_logo_removed")

    def reset(self, actor) -> dict:
        """Reset demo state: back to the default name and badge, no uploaded logo."""
        self._require_admin(actor)
        if not {k: v for k, v in self._raw().items() if k not in ("updated_at", "updated_by", "profile")}:
            return self.view()                           # already the defaults: nothing to write
        return self._save(actor, {}, "reset")
