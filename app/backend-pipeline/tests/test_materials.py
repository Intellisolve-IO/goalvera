"""Classroom materials from Teacher Notes (): suggestions, the form, the four templates,
the PDF, email to the teacher's own address only, privacy and the activity journal."""
from __future__ import annotations

import base64
import io
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from review_api import mailer
from review_api import material_ai as mai
from review_api import material_svg as msvg
from review_api.journal import MemoryJournal, Recorder
from review_api.materials import Materials, has_forbidden, teacher_notes
from review_api.notes import ApiError, Notes
from review_api.store import LocalStore
from review_api.bug_reports import never_values

REPO = Path(__file__).resolve().parents[3]
ROSTER = json.loads((REPO / "app/samples/demo_roster.json").read_text())
RID = "rec_dev01_20261009_101500_Room104"
SNOW = "stf_0001"
BELLE = "stf_0002"
DOC = "stu_0001"          # Doc Dwarf, Room 104
REQUEST = ("Teacher note. Create three worksheets that Doc can color tomorrow, based on his past "
           "activities and preferences, about trains, for his grade level and IEP.")


def reviewer(role="teacher", teacher_ids=(SNOW,), uid="uid_snow", email="snowwhite@staff.example"):
    return SimpleNamespace(uid=uid, email=email, role=role, is_admin=role == "admin",
                           teacher_ids=frozenset(teacher_ids), class_keys=frozenset())


ADMIN = reviewer("admin", (), "uid_admin", "admin@staff.example")
SNOW_T = reviewer()
BELLE_T = reviewer(teacher_ids=(BELLE,), uid="uid_belle", email="belle@example.org")
RBT = reviewer("rbt", (), "uid_rbt", "demo-rbt@staff.example")


def seed(store: LocalStore, *, markers=None, roster=None):
    store.write_json("config/demo_roster.json", roster or ROSTER)
    markers = markers if markers is not None else [{"type": "Incident", "offset_seconds": 10.0},
                                                   {"type": "Teacher Notes", "offset_seconds": 40.0}]
    session = {"recording_id": RID, "class_code": "Room 104", "teacher_id": SNOW, "teacher_name": "Snow White",
               "student_id": DOC, "student_first_name": "Doc", "student_last_initial": "D.",
               "duration_seconds": 120, "markers": markers, "start_time": "2026-10-09T10:15:00-04:00"}
    store.write_json(f"drafts/{RID}.json", {"recording_id": RID, "session": session, "created_at": "2026-10-09T14:20:00Z",
                                            "analysis": {"goal_evaluations": [], "incident_analysis": [],
                                                         "general_session_summary": "Doc worked on reading."}})
    store.write_json(f"transcripts/{RID}.json", {"recording_id": RID, "teacher_name": "Snow White", "markers": markers,
                                                 "speaker_turns": [
                                                     {"speaker": "Speaker 1", "start_seconds": 2, "end_seconds": 8,
                                                      "text": "Good morning Doc, let's read about trains."},
                                                     {"speaker": "Speaker 1", "start_seconds": 41, "end_seconds": 55,
                                                      "text": REQUEST},
                                                     {"speaker": "Speaker 2", "start_seconds": 70, "end_seconds": 80,
                                                      "text": "Snow White, Grumpy needs a break."}]})


class Spy:
    """Stands in for the model: records exactly what was sent."""

    def __init__(self):
        self.calls = []
        self.svgs = []
        self.reply = None
        self.pictures = []
        self.picture_reply = None

    def call(self, what, payload):
        self.calls.append((what, json.loads(json.dumps(payload))))
        if what.startswith("params:"):
            return mai.offline_params(what[7:], payload)
        return mai.offline_suggest(payload) if what == "suggest" else mai.offline_brief(payload)

    def picture(self, prompt):
        self.pictures.append(prompt)
        if self.picture_reply:
            return self.picture_reply(prompt)
        return mai.offline_picture(prompt)

    def svg(self, system, prompt):
        self.svgs.append((system, prompt))
        return self.reply(prompt) if self.reply else mai.offline_svg(system, prompt)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MATERIALS_EMAIL_DEFAULT", "on")
    monkeypatch.delenv("MATERIALS_MAIL_DOMAINS", raising=False)
    store = LocalStore(tmp_path)
    seed(store)
    journal = MemoryJournal()
    notes = Notes(store, recorder=Recorder(journal))
    spy = Spy()
    outbox = mailer.Outbox()
    clock = SimpleNamespace(t=1000.0)
    mats = Materials(notes, ai=mai.MaterialAI(call=spy.call, svg=spy.svg, picture=spy.picture), sender=outbox,
                     clock=lambda: clock.t)
    return SimpleNamespace(store=store, notes=notes, mats=mats, spy=spy, outbox=outbox, journal=journal, clock=clock)


def actions(env):
    return [r["action"] for r in env.journal.rows()]


def make(env, actor=SNOW_T, **kw):
    body = {"note_index": 0, "type": "coloring", "count": 1, "topic": "trains", "student_ids": [DOC],
            "due": "2026-10-10", **kw}
    return env.mats.make(RID, actor, body)


# ---------------------------------------------------------------- notes and suggestions

def test_teacher_note_windows_end_at_the_next_marker_or_60_seconds():
    ms = [{"type": "Teacher Notes", "offset_seconds": 10}, {"type": "Incident", "offset_seconds": 30},
          {"type": "Teacher Notes", "offset_seconds": 100}, {"type": "bug", "offset_seconds": 5}]
    assert teacher_notes(ms) == [{"index": 0, "start": 10.0, "end": 30.0}, {"index": 1, "start": 100.0, "end": 160.0}]


def test_suggestions_are_made_once_per_session_and_masked(env):
    v = env.mats.view(RID, SNOW_T)
    assert v["can_make"] is True
    (note,) = v["notes"]
    assert note["is_request"] is True and 1 <= len(note["suggestions"]) <= 3
    assert note["suggestions"][0]["type"] == "coloring" and note["suggestions"][0]["count"] == 3
    assert note["suggestions"][0]["due"] == "tomorrow" and note["suggestions"][0]["topic"] == "trains"
    env.mats.view(RID, SNOW_T)
    env.mats.view(RID, ADMIN)
    assert [w for w, _ in env.spy.calls] == ["suggest"]               # cached: one AI call per session
    sent = json.dumps(env.spy.calls[0][1])
    for bad in ("Doc", "Dwarf", "Snow", "White", "Grumpy", "snowwhite", "stu_0001", "stf_0001"):
        assert bad not in sent, bad
    assert "the student" in sent
    assert "Good morning" not in sent                                  # only the note's own window
    assert actions(env).count("materials_suggested") == 1
    assert v["email"] == {"enabled": True, "to": "s********@staff.example", "has_address": True}


def test_rbt_sees_no_buttons_and_triggers_no_ai(env):
    v = env.mats.view(RID, RBT)
    assert v["can_make"] is False and v["students"] == [] and env.spy.calls == []
    assert v["notes"][0]["pending"] is True and v["email"]["to"] is None


def test_not_a_request_shows_nothing(env):
    seed(env.store, markers=[{"type": "Teacher Notes", "offset_seconds": 70.0}])
    v = env.mats.view(RID, SNOW_T)
    assert v["notes"][0]["is_request"] is False and v["notes"][0]["suggestions"] == []


def test_suggestions_are_capped_and_cleaned():
    reply = {"notes": [{"index": 0, "is_request": True, "suggestions": [
        {"type": "coloring", "count": 99, "topic": "x" * 200, "due": "next week"},
        {"type": "poster", "count": 1, "topic": "nope"},
        {"type": "worksheet", "count": 0, "topic": "a"}, {"type": "labels", "count": 5, "topic": "b"},
        {"type": "flashcards", "count": 5, "topic": "c"}]}]}
    got = mai.clean_suggestions(reply, [0])[0]
    assert [s["type"] for s in got["suggestions"]] == ["coloring", "worksheet", "labels"]
    assert got["suggestions"][0]["count"] == 4 and len(got["suggestions"][0]["topic"]) == 60
    assert got["suggestions"][0]["due"] == "" and "pictures" not in got["suggestions"][0]
    assert got["suggestions"][1]["count"] == 2


# ---------------------------------------------------------------- making

@pytest.mark.parametrize("kind,count", [("coloring", 1), ("worksheet", 5), ("flashcards", 12), ("labels", 8)])
def test_each_type_makes_three_drafts_and_a_printable_pdf(env, kind, count):
    out = make(env, type=kind, count=count)
    m = out["material"]
    assert m["type"] == kind and m["count"] == count and [d["draft"] for d in m["drafts"]] == [1, 2, 3]
    assert all(d["preview_png"] and base64.b64decode(d["preview_png"]).startswith(b"\x89PNG") for d in m["drafts"])
    assert "<svg" not in json.dumps(m) and all(set(d) == {"draft", "variation", "preview_png"} for d in m["drafts"])
    data, fname = env.mats.pdf(RID, m["material_id"], SNOW_T)
    assert fname.endswith(".pdf") and data.startswith(b"%PDF")
    r = PdfReader(io.BytesIO(data))
    assert len(r.pages) == 3
    text = "\n".join(p.extract_text() for p in r.pages)
    assert "Suggestion - review before use" in text and "Draft 3 of 3" in text
    one, fname = env.mats.pdf(RID, m["material_id"], SNOW_T, "2")
    assert len(PdfReader(io.BytesIO(one)).pages) == 1 and fname.endswith("_draft2.pdf")
    assert env.journal.rows()[-1]["details"]["draft_number"] == 2
    with pytest.raises(ApiError) as e:
        env.mats.pdf(RID, m["material_id"], SNOW_T, "4")
    assert e.value.code == "BAD_DRAFT"
    doc = env.store.read_json(f"materials/{RID}/{m['material_id']}.json")
    for d in doc["drafts"]:
        t = msvg.texts(d["svg"])
        assert "Sample Elementary School" in t and not any("INSERT SCHOOL" in x for x in t)
        for bad in ("Doc", "Dwarf", "Snow White"):
            assert not any(bad in x for x in t)


def test_prompts_have_davids_shape_and_only_deidentified_context(env):
    make(env)
    (_, brief), = [c for c in env.spy.calls if c[0] == "brief"]
    sent = json.dumps(brief)
    for bad in ("Doc", "Dwarf", "Snow", "stu_0001", "Autism", "present_levels", "eligibility", "@", "Decodes grade 4"):
        assert bad not in sent, bad
    (st,) = brief["students"]
    assert st["label"] == "the student" and st["grade"] == "5"
    assert st["iep_goals"][0]["goal_id"] == "g1" and "the student will answer" in st["iep_goals"][0]["description"]
    assert st["accommodations"] and brief["teacher_request"] and brief["count"] == 1
    assert len(env.spy.svgs) == 3
    systems = {sy for sy, _ in env.spy.svgs}
    assert systems == {msvg.DEFAULT_INSTRUCTION}
    for n, (_, prompt) in enumerate(sorted(env.spy.svgs, key=lambda x: x[1]), 1):
        lines = prompt.split("\n")
        assert len(lines) == 3 and lines[0].startswith('Teacher Request: "') and lines[0].endswith('"')
        assert lines[1] == "Activity Type: Coloring"
        assert re.fullmatch(rf"Draft Variation: {n} of 3 \(.+\)", lines[2]), lines[2]
        for bad in ("Doc", "Dwarf", "Snow", "stu_"):
            assert bad not in prompt


def test_user_prompt_shape():
    assert msvg.user_prompt('Make "8" farm\n cards', "flashcards", 2, "big bold words") == (
        "Teacher Request: \"Make '8' farm cards\"\nActivity Type: Flashcard\nDraft Variation: 2 of 3 (big bold words)")
    assert msvg.user_prompt("x", "labels", 1, "y").split("\n")[1] == "Activity Type: Labels"
    assert msvg.user_prompt("x", "worksheet", 3, "y").split("\n")[1] == "Activity Type: Worksheet"


def test_several_students_are_numbered(env):
    ids = [s["student_id"] for s in env.mats.view(RID, SNOW_T)["students"]][:3]
    make(env, student_ids=ids, type="worksheet", count=3)
    brief = [c for c in env.spy.calls if c[0] == "brief"][-1][1]
    assert [s["label"] for s in brief["students"]] == ["student 1", "student 2", "student 3"]


def test_names_the_model_returns_are_scrubbed(env):
    def call(what, payload):
        if what == "suggest":
            return mai.offline_suggest(payload)
        return {"title": "Doc's train page", "teacher_request": "Trains for Doc Dwarf, ask Snow White",
                "variations": ["Grumpy style", "b", "c"]}
    env.mats.ai = mai.MaterialAI(call=call, svg=env.spy.svg)
    env.spy.reply = lambda p: ('<svg xmlns="http://www.w3.org/2000/svg"><rect x="1" y="1" width="9" height="9"/>'
                               '<circle cx="5" cy="5" r="3"/><line x1="0" y1="0" x2="9" y2="9"/>'
                               '<text x="100" y="300">Doc likes trains, a happy train</text></svg>')
    m = make(env, count=1)["material"]
    doc = env.store.read_json(f"materials/{RID}/{m['material_id']}.json")
    blob = json.dumps(doc["brief"]) + "".join(p for _, p in env.spy.svgs)
    for bad in ("Doc", "Dwarf", "Snow", "Grumpy"):
        assert bad not in blob, bad
    t = msvg.texts(doc["drafts"][0]["svg"])
    assert "the student likes trains, a happy train" in t and not any("Doc" in x for x in t)


def test_invalid_svg_is_retried_and_a_bad_draft_left_out(env):
    bad = iter(["Sure! here you go", "<svg><script>x</script></svg>"])
    env.spy.reply = lambda p: next(bad) if "Variation: 2 of" in p else mai.offline_svg("", p)
    m = make(env)["material"]
    assert len(m["drafts"]) == 2                  # one draft failed twice: left out
    env.spy.reply = lambda p: "no svg at all"
    with pytest.raises(ApiError) as e:
        make(env)
    assert e.value.code == "MATERIALS_AI_FAILED"


# ---------------------------------------------------------------- the SVG system instruction (admin)

def _instr(v, key):
    return next(x for x in v["instructions"] if x["key"] == key)


def test_instruction_default_is_davids_text_and_admin_edits_are_logged(env):
    v = env.mats.instruction_setting(SNOW_T)
    assert v["can_edit"] is False and [x["key"] for x in v["instructions"]] == list(mai.INSTRUCTION_KEYS)
    c = _instr(v, "coloring")
    assert c["is_default"] and c["instruction"] == msvg.DEFAULT_INSTRUCTION and c["renderer"] == "svg"
    assert _instr(v, "flashcards_color")["instruction"] == msvg.COLOR_INSTRUCTION
    assert _instr(v, "multiplication_table")["renderer"] == "template"
    assert _instr(v, "flashcards_photo")["renderer"] == "photo"
    assert msvg.DEFAULT_INSTRUCTION.startswith("You are an expert SVG designer creating printable visual activities")
    assert 'viewBox="0 0 800 1000"' in msvg.DEFAULT_INSTRUCTION and "[INSERT SCHOOL NAME HERE]" in msvg.DEFAULT_INSTRUCTION
    for who in (SNOW_T, RBT):
        with pytest.raises(ApiError) as e:
            env.mats.set_instruction(who, {"key": "coloring", "instruction": "x"})
        assert e.value.code == "ADMIN_ONLY"
    with pytest.raises(ApiError):
        env.mats.instruction_setting(RBT)
    out = env.mats.set_instruction(ADMIN, {"key": "coloring", "instruction": "Draw big simple shapes."})
    c = _instr(out, "coloring")
    assert c["instruction"] == "Draw big simple shapes." and c["is_default"] is False and out["can_edit"]
    assert _instr(out, "labels")["is_default"] is True                  # one key at a time
    row = env.journal.rows()[-1]
    assert row["action"] == "settings_changed" and row["actor_id"] == "uid_admin" and row["occurred_at"]
    assert row["details"]["setting"] == "materials_instruction" and row["details"]["reset"] is False
    assert row["details"]["instruction_key"] == "coloring"
    assert row["details"]["chars"] == len("Draw big simple shapes.") and "Draw" not in json.dumps(row)
    env.mats.set_instruction(ADMIN, {"key": "coloring", "instruction": "Draw big simple shapes on a white page."})
    make(env)
    assert {sy for sy, _ in env.spy.svgs} == {"Draw big simple shapes on a white page."}
    env.mats.set_instruction(ADMIN, {"key": "coloring", "reset": True})
    assert _instr(env.mats.instruction_setting(SNOW_T), "coloring")["is_default"] is True
    assert env.journal.rows()[-1]["details"]["reset"] is True
    for body, code in (({"key": "coloring", "instruction": ""}, "BAD_INSTRUCTION"),
                       ({"key": "coloring", "instruction": "x" * 8001}, "INSTRUCTION_TOO_LONG"),
                       ({"key": "coloring", "instruction": "Draw Doc a train"}, "INSTRUCTION_HAS_NAMES"),
                       ({"key": "number_line", "instruction": "Ask Snow White"}, "INSTRUCTION_HAS_NAMES"),
                       ({"key": "nope", "instruction": "x"}, "BAD_INSTRUCTION_KEY"),
                       ({"instruction": "x"}, "BAD_INSTRUCTION_KEY")):
        with pytest.raises(ApiError) as e:
            env.mats.set_instruction(ADMIN, body)
        assert e.value.code == code


def test_color_flashcards_use_their_own_instruction(env):
    env.mats.set_instruction(ADMIN, {"key": "flashcards_line", "instruction": "LINE ART ONLY"})
    m = make(env, type="flashcards", count=8, style="color", topic="farm animals")["material"]
    assert m["style"] == "color" and m["renderer"] == "svg" and len(m["drafts"]) == 3
    assert {sy for sy, _ in env.spy.svgs} == {msvg.COLOR_INSTRUCTION}
    assert all("Activity Type: Flashcard" in p for _, p in env.spy.svgs)
    env.spy.svgs.clear()
    make(env, type="flashcards", count=8, topic="farm animals")                # style defaults to line
    assert {sy for sy, _ in env.spy.svgs} == {"LINE ART ONLY"}
    req = [r for r in env.journal.rows() if r["action"] == "materials_requested"][0]["details"]
    assert req["renderer"] == "svg" and req["style"] == "color" and req["instruction_key"] == "flashcards_color"
    with pytest.raises(ApiError) as e:
        make(env, type="flashcards", count=8, style="watercolor")
    assert e.value.code == "BAD_STYLE"
    make(env, type="coloring", style="photo")                                  # style only applies to flashcards
    assert env.spy.pictures == []


def test_flashcard_style_goes_to_the_planning_step(env):
    make(env, type="flashcards", count=8, style="color", topic="farm animals")
    make(env, type="flashcards", count=8, topic="farm animals")
    make(env, type="coloring", topic="trains")
    briefs = [p for w, p in env.spy.calls if w == "brief"]
    assert briefs[0]["style"] == "color" and "cartoon color" in briefs[0]["style_label"]
    assert briefs[1]["style"] == "line" and "line art" in briefs[1]["style_label"]
    assert "style" not in briefs[2]
    assert 'When style is given (flashcards)' in mai.BRIEF_SYSTEM and mai.BRIEF_VERSION.startswith("materials-brief-v3")
    make(env, type="flashcards", style="photo", count=2, topic="pigs")
    photo = [p for w, p in env.spy.calls if w == "params:flashcards_photo"][-1]
    assert photo["style"] == "photo"


def test_diagram_is_an_svg_type(env):
    m = make(env, type="diagram", count=4, topic="butterfly life cycle")["material"]
    assert m["renderer"] == "svg" and len(m["drafts"]) == 3
    assert all("Activity Type: Diagram" in p for _, p in env.spy.svgs)


# ---------------------------------------------------------------- code templates

def _numbers(svg):
    return [t for t in msvg.texts(svg) if re.fullmatch(r"-?\d+", t)]


def test_multiplication_table_numbers_are_computed_by_code(env):
    def call(what, payload):
        env.spy.calls.append((what, json.loads(json.dumps(payload))))
        if what == "params:multiplication_table":
            return {"title": "Times tables for Doc", "start": 1, "end": 12, "focus": [7, 99]}
        return mai.offline_suggest(payload)
    env.mats.ai = mai.MaterialAI(call=call, svg=env.spy.svg)
    m = make(env, type="multiplication_table", count=12, topic="times tables")["material"]
    assert m["renderer"] == "template" and len(m["drafts"]) == 3 and env.spy.svgs == []   # no SVG from the AI
    doc = env.store.read_json(f"materials/{RID}/{m['material_id']}.json")
    p = doc["brief"]["params"]
    assert p["start"] == 1 and p["end"] == 12 and p["focus"] == [7, 12]                # clamped to the table
    assert "Doc" not in json.dumps(doc["brief"]) and "the student" in p["title"]
    full = doc["drafts"][0]["svg"]
    nums = [int(x) for x in _numbers(full)]
    assert all(i * j in nums for i in range(1, 13) for j in range(1, 13))
    assert 144 in nums and 132 in nums and 49 in nums
    # practice layouts: blanks, but every number still shown is right
    for d in doc["drafts"][1:]:
        assert len(_numbers(d["svg"])) < len(nums)
    t = msvg.texts(full)
    assert "Name: ________________________" in t                      # our header on code-built pages too
    assert [w for w, _ in env.spy.calls if w.startswith("params:")] == ["params:multiplication_table"]
    sent = json.dumps([pl for w, pl in env.spy.calls if w.startswith("params:")])
    for bad in ("Doc", "Dwarf", "Snow", "stu_0001"):
        assert bad not in sent


def test_multiplication_practice_cells_are_right_or_blank():
    p = mai.clean_params("multiplication_table", {"start": 2, "end": 9}, 12, "")
    from review_api import material_templates as mt
    for layout in (1, 2, 3):
        root = ET.fromstring(mt.multiplication_table(p, layout))
        cells = {}
        for e in root.iter():
            if e.tag.endswith("text") and re.fullmatch(r"\d+", e.text or ""):
                cells[(round(float(e.get("x"))), round(float(e.get("y"))))] = int(e.text)
        xs = sorted({x for x, _ in cells}); ys = sorted({y for _, y in cells})
        col = {x: cells.get((x, ys[0])) for x in xs}          # header row
        row = {y: cells.get((xs[0], y)) for y in ys}          # header column
        for (x, y), v in cells.items():
            if x != xs[0] and y != ys[0]:
                assert v == col[x] * row[y]


def test_number_line_and_letter_tracing_params_are_clamped():
    nl = mai.clean_params("number_line", {"start": 0, "end": 1000, "step": 1}, 10, "")
    assert (nl["end"] - nl["start"]) // nl["step"] <= 20 and nl["end"] <= 1000
    nl = mai.clean_params("number_line", {}, 10, "")
    assert nl == {"title": "Number line 0 to 10", "start": 0, "end": 10, "step": 1, "label_every": 1}
    lt = mai.clean_params("letter_tracing", {"lines": ["cat<script>", "a" * 40, "dog", "", "sun", "hat"],
                                            "case": "upper"}, 3, "")
    assert lt["lines"] == ["CATSCRIPT", "A" * 12, "DOG"]
    from review_api import material_templates as mt
    for layout in (1, 2, 3):
        for svg in (mt.number_line(nl, layout), mt.letter_tracing(lt, layout)):
            assert msvg.render_png(svg, 64)


def test_number_line_marks_are_computed(env):
    m = make(env, type="number_line", count=10, topic="counting")["material"]
    doc = env.store.read_json(f"materials/{RID}/{m['material_id']}.json")
    assert [int(x) for x in _numbers(doc["drafts"][0]["svg"])] == list(range(0, 11))
    m = make(env, type="letter_tracing", count=3, topic="cat dog sun")["material"]
    doc = env.store.read_json(f"materials/{RID}/{m['material_id']}.json")
    assert doc["brief"]["params"]["lines"] == ["cat", "dog", "sun"] and len(m["drafts"]) == 3


# ---------------------------------------------------------------- photo-style flashcards (image model, paced)

def test_photo_flashcards_are_made_in_paced_steps_and_emailed_once(env):
    m = make(env, type="flashcards", style="photo", count=3, topic="farm animals")
    assert m["email"] == {"status": "pending"}
    m = m["material"]
    mid = m["material_id"]
    assert m["status"] == "making" and m["pictures_made"] == 0 and m["pictures_total"] == 3 and m["eta_minutes"] >= 1
    assert env.spy.pictures == [] and env.outbox.sent == []
    with pytest.raises(ApiError) as e:
        env.mats.pdf(RID, mid, SNOW_T)
    assert e.value.code == "STILL_MAKING"
    r = env.mats.continue_(RID, mid, SNOW_T)
    assert r["material"]["pictures_made"] == 1 and len(env.spy.pictures) == 1
    r = env.mats.continue_(RID, mid, SNOW_T)                          # too soon: no image call
    assert len(env.spy.pictures) == 1 and r["wait_seconds"] > 0
    env.clock.t += 36
    env.mats.continue_(RID, mid, SNOW_T)
    env.clock.t += 36
    r = env.mats.continue_(RID, mid, SNOW_T)
    assert len(env.spy.pictures) == 3 and r["material"]["status"] == "ready" and r["email"]["status"] == "emailed"
    assert all(p.startswith("Clear photo-style picture of") for p in env.spy.pictures)
    env.clock.t += 36
    r = env.mats.continue_(RID, mid, SNOW_T)                          # finished: nothing more, no second email
    assert r["email"] is None and len(env.spy.pictures) == 3 and len(env.outbox.sent) == 1
    pdf, _ = env.mats.pdf(RID, mid, SNOW_T)
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) == 1 and "Page 1 of 1" in reader.pages[0].extract_text()
    made = [r for r in env.journal.rows() if r["action"] == "materials_made"][-1]["details"]
    assert made["renderer"] == "photo" and made["pictures"] == 3 and made["pictures_missing"] == 0


def test_photo_quota_busy_waits_and_failures_leave_an_empty_frame(env):
    m = make(env, type="flashcards", style="photo", count=2, topic="cows")["material"]
    mid = m["material_id"]
    calls = {"n": 0}

    def busy(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            raise mai.PictureBusy()
        if calls["n"] >= 3:
            raise RuntimeError("bad")
        return mai.offline_picture(prompt)
    env.spy.picture_reply = busy
    r = env.mats.continue_(RID, mid, SNOW_T)
    assert r["material"]["pictures_made"] == 0                        # 429: try again next step
    for _ in range(6):
        env.clock.t += 36
        r = env.mats.continue_(RID, mid, SNOW_T)
    assert r["material"]["status"] == "ready" and r["material"]["pictures_made"] == 1
    assert calls["n"] == 2 + mats_tries()


def mats_tries():
    from review_api import materials
    return materials.PHOTO_TRIES


def test_photo_count_is_capped_and_photo_prompt_uses_admin_style(env):
    with pytest.raises(ApiError) as e:
        make(env, type="flashcards", style="photo", count=12)
    assert e.value.code == "BAD_COUNT"
    env.mats.set_instruction(ADMIN, {"key": "flashcards_photo", "instruction": "Soft pastel photo of {subject}."})
    m = make(env, type="flashcards", style="photo", count=2, topic="pigs")["material"]
    env.mats.continue_(RID, m["material_id"], SNOW_T)
    assert env.spy.pictures == ["Soft pastel photo of a pigs."]


def test_view_lists_types_renderers_and_styles(env):
    v = env.mats.view(RID, SNOW_T)
    types = {t["type"]: t for t in v["types"]}
    assert set(types) == set(mai.TYPES)
    assert types["multiplication_table"]["renderer"] == "template" and types["coloring"]["renderer"] == "svg"
    assert [s["style"] for s in types["flashcards"]["styles"]] == ["line", "color", "photo"]
    assert types["flashcards"]["photo_max"] == mai.PHOTO_MAX


def test_domain_context_hook_goes_into_the_prompt_masked(env):
    seen = []

    def domain(session):
        seen.append(session["recording_id"])
        return "Room 104 uses ABA with visual supports; Doc likes trains; mail snowwhite@staff.example"
    env.mats.domain_context = domain
    make(env)
    brief = [p for w, p in env.spy.calls if w == "brief"][-1]
    assert seen == [RID] and "visual supports" in brief["class_domain"]
    assert "Doc" not in brief["class_domain"] and "@" not in brief["class_domain"]
    env.mats.domain_context = lambda s: (_ for _ in ()).throw(RuntimeError("no domains yet"))
    make(env)
    assert "class_domain" not in [p for w, p in env.spy.calls if w == "brief"][-1]


# ---------------------------------------------------------------- SVG sanitizer and header

GOOD = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100%">'
        '<rect x="60" y="200" width="300" height="200" stroke="#000" stroke-width="5" fill="none"/>'
        '<circle cx="400" cy="500" r="80"/><path d="M100 600 L700 600"/></svg>')


def test_sanitizer_strips_fences_and_forces_the_page_box():
    out = msvg.sanitize("Here it is:\n```svg\n" + GOOD + "\n```\nEnjoy!")
    root = ET.fromstring(out)
    assert root.tag.endswith("svg") and root.get("viewBox") == "0 0 800 1000"
    assert root.get("width") == "800" and root.get("height") == "1000"


@pytest.mark.parametrize("bad", [
    '<script>alert(1)</script>', '<foreignObject><div>x</div></foreignObject>',
    '<image href="https://evil.example/x.png" width="10" height="10"/>',
    '<a href="javascript:alert(1)"><text>x</text></a>', '<use href="https://evil.example/s.svg#a"/>',
    '<rect onclick="alert(1)" onload="x()" x="1" y="1" width="2" height="2"/>',
    '<rect style="fill: url(https://evil.example/x)" x="1" y="1" width="2" height="2"/>',
    '<rect fill="url(https://evil.example/p)" x="1" y="1" width="2" height="2"/>',
    '<style>@import url(https://evil.example/a.css); rect { fill: url(http://x) }</style>',
    '<animate attributeName="href" to="javascript:alert(1)"/>', '<set attributeName="onclick" to="x"/>'])
def test_sanitizer_removes_active_and_external_content(bad):
    out = msvg.sanitize(GOOD.replace("</svg>", bad + "</svg>"))
    low = out.lower()
    for word in ("script", "foreignobject", "<image", "href", "javascript", "onclick", "onload", "url(http",
                 "evil.example", "@import", "animate", "<set"):
        assert word not in low, (word, out)
    msvg.render_png(out, 64)


def test_sanitizer_keeps_local_references_and_plain_css():
    svg = GOOD.replace("</svg>", '<defs><clipPath id="c1"><rect x="0" y="0" width="5" height="5"/></clipPath></defs>'
                                 '<style>.card { stroke: #000; stroke-width: 5; font-family: Comic Sans }</style>'
                                 '<g clip-path="url(#c1)"><rect class="card" x="1" y="1" width="9" height="9"/></g></svg>')
    out = msvg.sanitize(svg)
    assert 'clip-path="url(#c1)"' in out and "stroke-width: 5" in out and "Comic" not in out


@pytest.mark.parametrize("raw,why", [
    ("no svg here", "no svg root"), ("<svg><rect></svg>", "not xml"),
    ('<!DOCTYPE svg [<!ENTITY x "y">]><svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>', "doctype"),
    ('<svg xmlns="http://www.w3.org/2000/svg"><text>hi</text></svg>', "nothing drawn"),
    ("<svg>" + "<rect/>" * 5000 + "</svg>", "too many elements")])
def test_sanitizer_rejects_unusable_replies(raw, why):
    with pytest.raises(msvg.SvgInvalid) as e:
        msvg.sanitize(raw)
    assert why in str(e.value)


def test_header_is_ours_whatever_the_model_wrote():
    model = GOOD.replace("</svg>", '<g id="school-header"><text x="400" y="55">Evrday Heros Acadmy</text>'
                                   '<text x="40" y="90">Name: Doc Dwarf</text></g>'
                                   '<text x="10" y="10">[INSERT SCHOOL NAME HERE]</text></svg>')
    out = msvg.set_header(msvg.sanitize(model), "Sample <School> & Co")
    t = msvg.texts(out)
    assert "Sample <School> & Co" in t
    assert "Name: ________________________" in t and "Date: ______________" in t
    for bad in ("Evrday", "Doc Dwarf", "INSERT SCHOOL"):
        assert not any(bad in x for x in t), bad
    root = ET.fromstring(out)
    assert sum(1 for e in root.iter() if e.get("id") == "school-header") == 1
    assert root[-1].get("id") == "school-header"               # drawn last: on top
    msvg.render_png(out, 64)


@pytest.mark.parametrize("body,code", [
    ({"type": "poster"}, "BAD_MATERIAL_TYPE"), ({"count": 6}, "BAD_COUNT"), ({"count": "3"}, "BAD_COUNT"),
    ({"topic": "x" * 61}, "TOPIC_TOO_LONG"), ({"student_ids": ["stu_9999"]}, "BAD_STUDENTS"),
    ({"student_ids": ["stu_0011"]}, "BAD_STUDENTS"),          # another classroom's student
    ({"due": "tomorrow"}, "BAD_DUE_DATE"), ({"note_index": 5}, "BAD_NOTE_INDEX"),
    ({"regenerate_of": "mat_x"}, "BAD_REGENERATE")])
def test_form_is_validated(env, body, code):
    with pytest.raises(ApiError) as e:
        make(env, **body)
    assert e.value.code == code and env.outbox.sent == []


def test_who_may_make_download_and_resend(env):
    for who in (RBT, BELLE_T):
        with pytest.raises(ApiError) as e:
            make(env, actor=who)
        assert e.value.code == "NOT_SESSION_TEACHER"
    mid = make(env)["material"]["material_id"]
    for who in (RBT, BELLE_T):
        for fn in (lambda: env.mats.pdf(RID, mid, who), lambda: env.mats.resend(RID, mid, who)):
            with pytest.raises(ApiError):
                fn()
    assert env.mats.pdf(RID, mid, ADMIN)[0].startswith(b"%PDF")


# ---------------------------------------------------------------- email

def test_email_goes_only_to_the_teachers_own_address(env):
    out = make(env)
    assert out["email"] == {"status": "emailed", "to": "s********@staff.example"}
    (msg,) = env.outbox.sent
    assert msg["To"] == "snowwhite@staff.example"
    assert msg["From"].endswith("<notifications@staff.example>") and msg["Reply-To"] == "notifications@staff.example"
    body = msg.get_body(("plain",)).get_content()
    assert "for Doc" in body and "#recordings/" + RID in body
    for bad in ("Dwarf", "Doc D.", "stu_0001", "inferential", "Autism", "g1"):
        assert bad not in body and bad not in msg["Subject"], bad
    (att,) = list(msg.iter_attachments())
    assert att.get_content_type() == "application/pdf" and att.get_content().startswith(b"%PDF")
    assert actions(env)[-3:] == ["materials_requested", "materials_made", "materials_emailed"]
    row = env.journal.rows()[-1]
    assert "@" not in json.dumps(row) and row["details"]["email_status"] == "emailed"


def test_admin_click_sends_to_the_sessions_teacher_not_the_admin(env):
    make(env, actor=ADMIN)
    assert [m["To"] for m in env.outbox.sent] == ["snowwhite@staff.example"]


def test_no_staff_email_means_download_only(env):
    r = json.loads(json.dumps(ROSTER))
    r["teachers"][SNOW]["emails"] = []
    env.store.write_json("config/demo_roster.json", r)
    env.notes._roster_at = None
    out = make(env)
    assert out["email"]["status"] == "no_staff_email" and env.outbox.sent == []
    assert actions(env)[-1] == "materials_send_failed"


def test_other_domains_are_refused(env, monkeypatch):
    r = json.loads(json.dumps(ROSTER))
    r["teachers"][SNOW]["emails"] = ["snow@gmail.com"]
    env.store.write_json("config/demo_roster.json", r)
    env.notes._roster_at = None
    assert make(env)["email"]["status"] == "recipient_domain_not_allowed" and env.outbox.sent == []


def test_email_setting_off_and_admin_only(env):
    with pytest.raises(ApiError) as e:
        env.mats.set_email(SNOW_T, {"enabled": False})
    assert e.value.code == "ADMIN_ONLY"
    assert env.mats.set_email(ADMIN, {"enabled": False})["enabled"] is False
    row = env.journal.rows()[-1]
    assert row["action"] == "settings_changed" and row["details"] == {"setting": "materials_email", "enabled": False}
    out = make(env)
    assert out["email"]["status"] == "email_off" and env.outbox.sent == []
    with pytest.raises(ApiError) as e:
        env.mats.resend(RID, out["material"]["material_id"], SNOW_T)
    assert e.value.code == "EMAIL_OFF"
    assert env.mats.set_email(ADMIN, {"enabled": True})["enabled"] is True


def test_resend_and_regenerate(env):
    mid = make(env)["material"]["material_id"]
    out = env.mats.resend(RID, mid, SNOW_T)
    assert out["email"]["status"] == "emailed" and len(env.outbox.sent) == 2
    assert [e["resend"] for e in out["material"]["emails"]] == [False, True]
    again = make(env, regenerate_of=mid)["material"]
    assert again["regenerate_of"] == mid and again["material_id"] != mid
    assert [m["material_id"] for m in env.mats.view(RID, SNOW_T)["materials"]] == [mid, again["material_id"]]
    assert "materials_resent" in actions(env)


def test_check_email_blocks_last_names_and_iep_text(env):
    doc = {"student_ids": [DOC]}
    for text in ("for Doc Dwarf", "for Doc D.", "Given a grade 4 passage and a graphic organizer", "x@y.org", "stu_0001"):
        with pytest.raises(mailer.MailRefused):
            env.mats.check_email("s", text, doc)


def test_mail_not_set_up_keeps_download_working(env):
    env.mats.sender = mailer.GmailSender(read_secret=lambda name: None)
    out = make(env)
    assert out["email"]["status"] == "mail_not_set_up"
    assert env.mats.pdf(RID, out["material"]["material_id"], SNOW_T)[0].startswith(b"%PDF")


# ---------------------------------------------------------------- the Gmail sender

class Http:
    def __init__(self, token_status=200, scope=mailer.SCOPE):
        self.calls, self.token_status, self.scope = [], token_status, scope

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        if url == mailer.TOKEN_URL:
            return SimpleNamespace(status_code=self.token_status, raise_for_status=lambda: None,
                                   json=lambda: {"access_token": "at", "expires_in": 3599, "scope": self.scope})
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {"id": "g123"})


def _secrets(name):
    return {"mailer-oauth-client": json.dumps({"client_id": "cid", "client_secret": "cs"}),
            "mailer-refresh-token": "rt"}.get(name)


def _msg():
    return mailer.build_message(to="snowwhite@staff.example", subject="s", body="b", pdf=b"%PDF-1.4",
                                filename="a.pdf", from_name="Sample Elementary School")


def test_gmail_sender_uses_the_refresh_token_and_caches_the_access_token():
    http = Http()
    g = mailer.GmailSender(read_secret=_secrets, http=http, now=lambda: 1000.0)
    assert g.send(_msg()) == "g123" and g.send(_msg()) == "g123"
    urls = [u for u, _ in http.calls]
    assert urls == [mailer.TOKEN_URL, mailer.SEND_URL, mailer.SEND_URL]
    assert http.calls[0][1]["data"]["refresh_token"] == "rt"
    raw = json.loads(http.calls[1][1]["data"])["raw"]
    sent = base64.urlsafe_b64decode(raw)
    assert b"From: Sample Elementary School <notifications@staff.example>" in sent
    assert http.calls[1][1]["headers"]["Authorization"] == "Bearer at"


def test_revoked_token_or_wrong_scope_needs_reapproval():
    for http in (Http(token_status=400), Http(scope="https://www.googleapis.com/auth/gmail.readonly")):
        with pytest.raises(mailer.MailNotReady) as e:
            mailer.GmailSender(read_secret=_secrets, http=http).send(_msg())
        assert e.value.code == "MAIL_NEEDS_REAPPROVAL"


def test_from_is_pinned_and_recipients_checked(monkeypatch):
    assert _msg()["From"].endswith("<notifications@staff.example>")
    for bad in ("a@b@staff.example", "x@staff.example, y@staff.example", "x@gmail.com", "x\n@staff.example"):
        with pytest.raises(mailer.MailRefused):
            mailer.check_recipient(bad)
    monkeypatch.setenv("MATERIALS_MAIL_DOMAINS", "school.org")
    with pytest.raises(mailer.MailRefused):
        mailer.check_recipient("snowwhite@staff.example")


def test_has_forbidden_catches_names_and_emails():
    never = never_values(ROSTER)
    assert has_forbidden("the student likes trains", never) is None
    assert has_forbidden("Doc likes trains", never) == "name"
    assert has_forbidden("mail snowwhite@staff.example", never) == "email"


def test_live_svg_backs_off_on_quota_429(monkeypatch):
    import sys
    from types import ModuleType

    class Quota(Exception):
        code = 429

    tries, seen = [], []

    class Models:
        def generate_content(self, **kw):
            tries.append(1)
            seen.append(kw)
            if len(tries) < 3:
                raise Quota()
            return SimpleNamespace(text="<svg/>")

    genai = ModuleType("google.genai")
    genai.Client = lambda **kw: SimpleNamespace(models=Models())
    google = sys.modules.get("google") or ModuleType("google")
    monkeypatch.setattr(google, "genai", genai, raising=False)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setenv("MATERIALS_SVG_MODEL", "gemini-test-model")
    waits = []
    monkeypatch.setattr(mai, "_sleep", waits.append)
    assert mai.live_svg("SYS", "PROMPT") == "<svg/>" and waits == [2, 6]
    assert seen[-1]["model"] == "gemini-test-model" and seen[-1]["config"]["system_instruction"] == "SYS"


def test_materials_from_the_picture_model_are_not_shown(env):
    env.store.write_json(f"materials/{RID}/mat_20261010T180000000000_abcdef.json",
                         {"material_id": "mat_20261010T180000000000_abcdef", "type": "coloring", "count": 3,
                          "plan": {"title": "old", "pages": []}, "pictures": {}, "created_at": "2026-10-10T18:00:00+00:00"})
    mid = make(env)["material"]["material_id"]
    assert [m["material_id"] for m in env.mats.view(RID, SNOW_T)["materials"]] == [mid]


def test_photo_cards_print_4_big_cards_per_page(env):
    from review_api import material_templates as mt
    m = make(env, type="flashcards", style="photo", count=6, topic="cow pig horse sheep goat duck")["material"]
    mid = m["material_id"]
    for _ in range(6):
        env.mats.continue_(RID, mid, SNOW_T)
        env.clock.t += 36
    r = env.mats.continue_(RID, mid, SNOW_T)["material"]
    assert r["status"] == "ready" and [(d["page"], d["pages"]) for d in r["drafts"]] == [(1, 2), (2, 2)]
    pdf, _ = env.mats.pdf(RID, mid, SNOW_T)
    pages = PdfReader(io.BytesIO(pdf)).pages
    assert len(pages) == 2 and "Page 2 of 2" in pages[1].extract_text()
    svgs = mt.photo_pages("Farm", [{"word": w, "picture": w} for w in "abcdefg"], {0: mai.offline_picture("x")})
    assert len(svgs) == 2
    first = ET.fromstring(svgs[0])
    imgs = [e for e in first.iter() if e.tag.endswith("image")]
    assert len(imgs) == 1 and float(imgs[0].get("width")) >= 300            # big pictures (was ~105)
    assert "Farm (1 of 2)" in msvg.texts(svgs[0]) and "Farm (2 of 2)" in msvg.texts(svgs[1])
    assert sum(1 for t in msvg.texts(svgs[1]) if t in "abcdefg") == 3


# ---------------------------------------------------------------- lesson plan (planner) materials

WEEK = "2026-10-05"          # the Monday of the session's week (Oct 9)


def plan_body(**kw):
    return {"class_code": "Room 104", "week": WEEK, "lesson": {"day": "tue", "label": "Math block"},
            "type": "multiplication_table", "count": 12, "topic": "times tables", "student_ids": [DOC], **kw}


def _body_text(msg):
    return msg.get_body(preferencelist=("plain",)).get_content()


def test_planner_makes_a_worksheet_for_a_lesson_plan_week(env):
    out = env.mats.plan_make(SNOW_T, plan_body())
    m = out["material"]
    assert m["renderer"] == "template" and len(m["drafts"]) == 3 and m["lesson"] == {"day": "tue", "label": "Math block"}
    names = env.store.list("planner/materials/")
    assert any(n.endswith(f"/{WEEK}/{m['material_id']}.json") for n in names)        # kept with the plan
    assert not any(n.startswith("materials/") and m["material_id"] in n for n in env.store.list("materials/"))
    v = env.mats.plan_view(SNOW_T, "Room 104", f"{WEEK},2026-10-12")
    assert v["can_make"] and [x["material_id"] for x in v["weeks"][WEEK]] == [m["material_id"]]
    assert v["weeks"]["2026-10-12"] == [] and {t["type"] for t in v["types"]} == set(mai.TYPES)
    assert [s["student_id"] for s in v["students"]][:1] and v["email"]["has_address"]
    sent = json.dumps([p for _, p in env.spy.calls])
    for bad in ("Doc", "Dwarf", "Snow", "stu_0001"):
        assert bad not in sent
    assert out["email"]["status"] == "emailed" and "#planner" in _body_text(env.outbox.sent[-1])
    assert "lesson plan" in _body_text(env.outbox.sent[-1])
    req = [r for r in env.journal.rows() if r["action"] == "materials_requested"][-1]
    assert req["record_type"] == "plan" and req["details"]["week"] == WEEK and req["record_id"]
    made = [r for r in env.journal.rows() if r["action"] == "materials_made"][-1]
    assert made["record_type"] == "plan"
    pdf, name = env.mats.plan_action(SNOW_T, m["material_id"], "pdf", {"class_code": "Room 104", "week": WEEK}, "2")
    assert len(PdfReader(io.BytesIO(pdf)).pages) == 1 and name.endswith("_draft2.pdf")


def test_planner_materials_follow_the_planner_permissions(env):
    for who in (BELLE_T, RBT):
        with pytest.raises(ApiError) as e:
            env.mats.plan_make(who, plan_body())
        assert e.value.code == "NOT_YOUR_CLASSROOM"
    assert env.mats.plan_view(BELLE_T, "Room 104", WEEK)["can_make"] is False
    assert env.mats.plan_make(ADMIN, plan_body())["material"]
    for body, code in ((plan_body(week="2026-10-07"), "BAD_WEEK"), (plan_body(lesson={"day": "sat"}), "BAD_LESSON"),
                       (plan_body(class_code="Nowhere"), "UNKNOWN_CLASSROOM"), (plan_body(class_id="cls_x"), "BAD_CLASS"),
                       (plan_body(note_index=0), "BAD_NOTE_INDEX")):
        with pytest.raises(ApiError) as e:
            env.mats.plan_make(SNOW_T, body)
        assert e.value.code == code


def test_planner_regenerate_replaces_and_remove_hides(env):
    where = {"class_code": "Room 104", "week": WEEK}
    a = env.mats.plan_make(SNOW_T, plan_body())["material"]["material_id"]
    b = env.mats.plan_make(SNOW_T, plan_body(regenerate_of=a, count=10))["material"]["material_id"]
    assert [x["material_id"] for x in env.mats.plan_view(SNOW_T, "Room 104", WEEK)["weeks"][WEEK]] == [b]
    with pytest.raises(ApiError) as e:
        env.mats.plan_action(BELLE_T, b, "remove", where)
    assert e.value.code == "NOT_YOUR_CLASSROOM"
    assert env.mats.plan_action(SNOW_T, b, "remove", where) == {"removed": b}
    assert env.mats.plan_view(SNOW_T, "Room 104", WEEK)["weeks"][WEEK] == []
    assert any(n.endswith(f"{b}.json") for n in env.store.list("planner/materials/"))   # kept, not deleted
    assert [r["action"] for r in env.journal.rows()].count("materials_removed") == 2


def test_a_session_material_can_be_added_to_the_lesson_plan(env):
    m = make(env, type="flashcards", count=8, style="color", topic="farm animals")["material"]
    v = env.mats.view(RID, SNOW_T)
    assert v["plan_week"] == WEEK and v["materials"][0]["in_plan_week"] is None
    with pytest.raises(ApiError) as e:
        env.mats.attach(RID, m["material_id"], BELLE_T, {})
    assert e.value.code == "NOT_SESSION_TEACHER"
    out = env.mats.attach(RID, m["material_id"], SNOW_T, {"lesson": {"day": "fri"}})
    assert out["week"] == WEEK and out["material"]["attached_from"] is True
    assert env.mats.view(RID, SNOW_T)["materials"][0]["in_plan_week"] == WEEK
    pv = env.mats.plan_view(SNOW_T, "Room 104", WEEK)["weeks"][WEEK]
    assert [x["material_id"] for x in pv] == [m["material_id"]] and len(pv[0]["drafts"]) == 3
    with pytest.raises(ApiError) as e:
        env.mats.attach(RID, m["material_id"], SNOW_T, {})
    assert e.value.code == "ALREADY_IN_PLAN"
    row = [r for r in env.journal.rows() if r["action"] == "materials_attached"][-1]
    assert row["record_type"] == "plan" and row["details"]["source_recording_id"] == RID


def test_a_photo_set_made_in_the_planner_and_one_attached_from_a_session(env):
    where = {"class_code": "Room 104", "week": WEEK}
    m = env.mats.plan_make(SNOW_T, plan_body(type="flashcards", style="photo", count=2, topic="cows"))["material"]
    emails = []
    for _ in range(3):
        r = env.mats.plan_action(SNOW_T, m["material_id"], "continue", where)
        emails.append(r["email"])
        env.clock.t += 36
    assert r["material"]["status"] == "ready" and [e["status"] for e in emails if e] == ["emailed"]
    s = make(env, type="flashcards", style="photo", count=2, topic="pigs")["material"]
    with pytest.raises(ApiError) as e:
        env.mats.attach(RID, s["material_id"], SNOW_T, {})
    assert e.value.code == "STILL_MAKING"
    for _ in range(3):
        env.mats.continue_(RID, s["material_id"], SNOW_T)
        env.clock.t += 36
    env.mats.attach(RID, s["material_id"], SNOW_T, {})
    pv = {x["material_id"]: x for x in env.mats.plan_view(SNOW_T, "Room 104", WEEK)["weeks"][WEEK]}
    assert pv[s["material_id"]]["status"] == "ready"
    pdf, _ = env.mats.plan_action(SNOW_T, s["material_id"], "pdf", where)
    assert len(PdfReader(io.BytesIO(pdf)).pages) == 1


def test_planner_class_picks_the_domain_context(env):
    env.mats.plan_classes_hook = lambda code: [{"id": "cls_math", "name": "Math 2B", "domain_name": "Mathematics"}]
    env.mats.domain_context = lambda s: "Mathematics: Concrete-Pictorial-Abstract" if s.get("class_id") == "cls_math" else ""
    v = env.mats.plan_view(SNOW_T, "Room 104", WEEK)
    assert v["classes"] == [{"id": "cls_math", "name": "Math 2B", "domain_name": "Mathematics"}]
    env.mats.plan_make(SNOW_T, plan_body(type="worksheet", count=5, class_id="cls_math"))
    brief = [p for w, p in env.spy.calls if w == "brief"][-1]
    assert brief["class_domain"] == "Mathematics: Concrete-Pictorial-Abstract"
    env.mats.plan_make(SNOW_T, plan_body(type="worksheet", count=5))
    assert "class_domain" not in [p for w, p in env.spy.calls if w == "brief"][-1]
