"""How a roster name token is matched in text before any AI call ( follow-up, Oct 10 2026).

Every roster name token is masked, whatever its length ("Li" in "Shang Li", "Jo", "Al"):
  * 3+ characters: whole word, any case ("Marcus", "MARCUS", "marcus").
  * 1-2 characters: whole word, **case-sensitive, capitalized** only: the name as written in the
    roster with a capital first letter, or all caps ("Li", "LI"), never inside another word
    ("Lila", "split") and never lower case ("li"), so ordinary words aren't over-masked.
  * single letters that are also English words ("A", "I", "O") only as an initial with a period
    ("A." / "I."), so "I" and "a" in a sentence stay.
Whole word = not touching a letter or digit on either side. possessive=True also takes "'s".
"""
from __future__ import annotations

import re

SHORT = 2
_WORD_LETTERS = {"A", "I", "O"}


def name_pattern(name: str, *, cased: bool = False, possessive: bool = False) -> re.Pattern | None:
    """The regex for one roster name (or name token). cased=True: as written (capitalized) for every
    length, used where the text isn't transcript-derived. None only for an empty name."""
    n = " ".join(str(name or "").split())
    if not n:
        return None
    tail = r"(?:'s)?" if possessive else ""
    lb, rb = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"
    core = n.rstrip(".") or n
    if len(core) > SHORT:
        return re.compile(rf"{lb}{re.escape(n)}{tail}{rb}", 0 if cased else re.IGNORECASE)
    # short: capitalized whole word, case-sensitive
    cap = core[:1].upper() + core[1:]
    forms = sorted({cap, core.upper()}, key=len, reverse=True)
    alt = "|".join(re.escape(f) for f in forms)
    if len(core) == 1 and cap in _WORD_LETTERS:
        return re.compile(rf"{lb}(?:{alt})\.")            # initial only: "A." ("I" and "a" stay)
    return re.compile(rf"{lb}(?:{alt}){tail}{rb}")
