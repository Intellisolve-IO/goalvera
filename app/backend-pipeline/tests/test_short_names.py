"""Short roster names are masked before every AI call too.

"Li" in "Shang Li" (and any 1-2 letter name token) is masked as a capitalized whole word, case-
sensitively, so ordinary words ("li" in "split", "Lila", "a", "I") are not over-masked. Longer names
keep the case-blind whole-word match.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from common.namematch import name_pattern
from gemini_analyzer.prompt import build_user_prompt, redact_first_name
from review_api import planner_ai
from review_api.bug_reports import check_issue, never_values, scrub_note, BugReportBlocked
from review_api.incident_names import _mask as incident_mask
from review_api.materials import _scrub_out, _scrub_text, has_forbidden, has_forbidden_name_cased

ROSTER = json.loads((Path(__file__).resolve().parents[2] / "samples/demo_roster.json").read_text())
ORDINARY = ("I think a cat is in the classroom; it is a lot of fun to split the list and read a library book "
            "at 10 AM. Lila and Jojo went to the li-brary. Oh, OK, go on, so do it at 9 or 10.")


@pytest.mark.parametrize("name,text,hit", [
    ("Li", "Shang Li said hi", True), ("Li", "LI was here", True), ("Li", "Li's turn", True),
    ("Li", "li was here", False), ("Li", "Lila came", False), ("Li", "split the list", False),
    ("Li", "Li2 is a code", False), ("li", "Li said", True),
    ("Jo", "Jo read", True), ("Jo", "Jojo read", False), ("Jo", "jog", False),
    ("J", "J. Rivera", True), ("J", "Plan J now", True), ("J", "jump", False),
    ("A", "A. Ruiz", True), ("A", "A cat and a dog", False), ("I", "I think", False), ("I", "I. Ortiz", True),
    ("Marcus", "MARCUS and marcus", True), ("Marcus", "Marcusville", False),
])
def test_name_pattern(name, text, hit):
    assert bool(name_pattern(name).search(text)) is hit


def test_every_roster_name_token_is_in_the_never_list_whatever_its_length():
    staff = set(never_values(ROSTER)["staff"])
    assert {"Shang", "Li", "Shang Li", "Mulan", "Fa"} <= staff
    for rec in [*ROSTER["staff"].values()]:
        assert set(rec["name"].split()) <= staff, rec["name"]
    for rec in ROSTER["teachers"].values():
        assert set(rec["teacher_name"].split()) <= staff, rec["teacher_name"]
    r = {"students": {"s1": {"first_name": "Jo", "last_name": "Ng", "last_initial": "N."}}}
    assert {"Jo", "Ng"} <= set(never_values(r)["student"])


def test_every_scrub_masks_li_and_leaves_ordinary_words():
    never = never_values(ROSTER)
    text = "Shang Li helped. Then Li said the list was split, and LI waved."
    for out in (scrub_note(text, never), incident_mask(text, never), _scrub_text(text, never), _scrub_out(text, never)):
        assert "Li " not in out and "Li." not in out and "LI" not in out and "Shang" not in out, out
        assert "list" in out and "split" in out, out
    assert has_forbidden("ask Li", never) == "name" and has_forbidden_name_cased("ask Li", never) == "name"
    with pytest.raises(BugReportBlocked):
        check_issue("t", "Li needs help", never)


def test_ordinary_text_is_not_over_masked():
    never = never_values(ROSTER)
    for f in (scrub_note, incident_mask, _scrub_text, _scrub_out):
        out = f(ORDINARY, never)
        assert " ".join(out.split()) == " ".join(ORDINARY.split()), (f.__name__, out)
    assert has_forbidden(ORDINARY, never) is None and has_forbidden_name_cased(ORDINARY, never) is None
    check_issue("Recorder froze", ORDINARY, never)                 # not blocked


def test_planner_mask_hides_short_names_and_keeps_words():
    m = planner_ai.Mask(["Jo"], ["Shang Li", "Ms. Fa"])
    out = m.hide("Jo and Li read with Ms. Fa; Jojo split the list, jo-jo, I and a cat")
    assert out == "the student and the teacher read with the teacher; Jojo split the list, jo-jo, I and a cat"
    assert m.show("the student read") == "Jo read"


def test_pipeline_prompt_hides_a_short_first_name():
    assert redact_first_name("Jo read. Jojo and a jog. JO!", "Jo") == "the student read. Jojo and a jog. the student!"
    p = build_user_prompt({"student_first_name": "Jo", "iep_goals": [{"description": "Jo reads 3 words"}],
                           "speaker_turns": [{"speaker": "Speaker 1", "start_seconds": 0, "text": "Good job Jo, jog on"}],
                           "incident_flags": []})
    assert "Jo," not in p and "Jo reads" not in p and "jog on" in p
