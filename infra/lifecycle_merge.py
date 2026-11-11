#!/usr/bin/env python3
"""Bucket lifecycle: add ONE Delete rule, keeping every existing rule exactly as it is.

  python3 infra/lifecycle_merge.py merge  CURRENT.json PREFIX DAYS > NEW.json   # {"rule": [...]}
  python3 infra/lifecycle_merge.py show   CURRENT.json [PREFIX DAYS]           # one line per rule

CURRENT.json is `gcloud storage buckets describe gs://B --format='json(lifecycle_config)'`
(or a plain {"rule": [...]}). merge refuses (exit 3) if the result would drop or change any existing
rule; it adds nothing when the rule is already there. show marks the wanted rule "<- parent_email"
style and exits 1 if it is missing (for --check).
"""
from __future__ import annotations

import copy
import json
import sys


def rules_of(doc) -> list[dict]:
    doc = doc or {}
    cfg = doc.get("lifecycle_config") if "lifecycle_config" in doc else doc.get("lifecycle", doc)
    return list((cfg or {}).get("rule") or [])


def wanted(prefix: str, days: int) -> dict:
    return {"action": {"type": "Delete"}, "condition": {"age": days, "matchesPrefix": [prefix]}}


def is_wanted(rule: dict, prefix: str, days: int) -> bool:
    c = rule.get("condition") or {}
    return ((rule.get("action") or {}).get("type") == "Delete" and c.get("age") == days
            and c.get("matchesPrefix") == [prefix] and set(c) == {"age", "matchesPrefix"})


def merge(current: list[dict], prefix: str, days: int) -> list[dict]:
    out = copy.deepcopy(current)
    if not any(is_wanted(r, prefix, days) for r in out):
        out.append(wanted(prefix, days))
    if out[:len(current)] != current:                 # belt and braces: existing rules untouched, same order
        raise SystemExit(3)
    return out


def describe(rule: dict) -> str:
    c = dict(rule.get("condition") or {})
    pre = ",".join(c.pop("matchesPrefix", []) or []) or "(whole bucket)"
    cond = " ".join(f"{k}={v}" for k, v in sorted(c.items()))
    return f"{(rule.get('action') or {}).get('type', '?')} {pre} {cond}"


def main(argv: list[str]) -> int:
    cmd, path = argv[1], argv[2]
    current = rules_of(json.load(open(path)))
    if cmd == "merge":
        print(json.dumps({"rule": merge(current, argv[3], int(argv[4]))}))
        return 0
    if cmd == "show":
        found = False
        for r in current:
            mark = ""
            if len(argv) > 4 and is_wanted(r, argv[3], int(argv[4])):
                found, mark = True, "   <- 12-month parent_email/ rule"
            print(f"  {describe(r)}{mark}")
        if len(argv) > 4:
            print(f"  parent_email/ {argv[4]}-day rule: {'yes' if found else 'NO'}")
            return 0 if found else 1
        return 0
    raise SystemExit(f"unknown command {cmd}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
