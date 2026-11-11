#!/usr/bin/env python3
"""Merge review-web's conditional roles/storage.objectViewer bindings on the demo bucket into as few
bindings as IAM allows (<= 12 logical operators per condition), optionally adding object prefixes.

GCS allows at most 20 bindings with the same role + member and different conditions; review-web hit
that limit (one binding per setup_*.sh feature). The merged bindings grant exactly the union of the
old conditions (+ any --add-read prefixes). Unknown condition shapes abort; nothing is changed then.

  consolidate_review_iam.py BUCKET MEMBER [--add-read media/ ...] [--dry-run]
Applies with set-iam-policy, which carries the policy etag (a concurrent change makes it fail).
"""
import json, re, subprocess, sys, tempfile

ROLE = "roles/storage.objectViewer"
MAX_OPS = 12
LISTP = 'api.getAttribute("storage.googleapis.com/objectListPrefix", "").startsWith("%s")'
TYPE = 'resource.type == "storage.googleapis.com/Bucket"'


def split_top(expr, op):
    out, depth, cur, i = [], 0, "", 0
    while i < len(expr):
        c = expr[i]
        if c == '"':
            j = expr.index('"', i + 1); cur += expr[i:j + 1]; i = j + 1; continue
        depth += c == "("; depth -= c == ")"
        if depth == 0 and expr.startswith(op, i):
            out.append(cur.strip()); cur = ""; i += len(op); continue
        cur += c; i += 1
    out.append(cur.strip())
    return out


def unwrap(e):
    e = e.strip()
    while e.startswith("(") and e.endswith(")"):
        d = 0
        for k, c in enumerate(e):
            d += c == "("; d -= c == ")"
            if d == 0 and k < len(e) - 1:
                return e
        e = e[1:-1].strip()
    return e


def classify(expr, objbase, reads, exacts, lists, others):
    for t in split_top(unwrap(expr), "||"):
        t = unwrap(t)
        m = re.fullmatch(r'resource\.name\.startsWith\("%s([^"]*)"\)' % re.escape(objbase), t)
        if m: reads.add(m.group(1)); continue
        m = re.fullmatch(r'resource\.name == "%s([^"]*)"' % re.escape(objbase), t)
        if m: exacts.add(m.group(1)); continue
        parts = [unwrap(p) for p in split_top(t, "&&")]
        if len(parts) == 2 and parts[0] == TYPE:
            for p in split_top(unwrap(parts[1]), "||"):
                m = re.fullmatch(re.escape(LISTP).replace("%s", '([^"]*)'), unwrap(p))
                if not m: raise SystemExit("unknown list term: " + p)
                lists.add(m.group(1))
            continue
        if len(parts) == 2 and all(p.startswith("resource.name.") for p in parts):
            others.add("(" + " && ".join(parts) + ")"); continue
        raise SystemExit("unknown condition term, nothing changed: " + t)


def chunks(terms, ops_each, joiner_ops=1, base_ops=0):
    out, cur, ops = [], [], base_ops
    for t, o in terms:
        extra = o + (joiner_ops if cur else 0)
        if cur and ops + extra > MAX_OPS:
            out.append(cur); cur, ops = [], base_ops; extra = o
        cur.append(t); ops += extra
    if cur: out.append(cur)
    return out


def main():
    a = sys.argv[1:]
    dry = "--dry-run" in a; a = [x for x in a if x != "--dry-run"]
    add = []
    while "--add-read" in a:
        i = a.index("--add-read"); add.append(a[i + 1]); del a[i:i + 2]
    bucket, member = a
    objbase = "projects/_/buckets/%s/objects/" % bucket
    pol = json.loads(subprocess.check_output(["gcloud", "storage", "buckets", "get-iam-policy", "gs://" + bucket, "--format=json"]))
    mine = [b for b in pol["bindings"] if b["role"] == ROLE and "condition" in b and member in b["members"]]
    reads, exacts, lists, others, titles = set(add), set(), set(), set(), []
    for b in mine:
        classify(b["condition"]["expression"], objbase, reads, exacts, lists, others)
        titles.append(b["condition"]["title"])
    terms = [('resource.name.startsWith("%s%s")' % (objbase, p), 0) for p in sorted(reads)]
    terms += [('resource.name == "%s%s"' % (objbase, p), 0) for p in sorted(exacts)]
    terms += [(o, o.count("&&") + o.count("||")) for o in sorted(others)]
    new = []
    for n, c in enumerate(chunks(terms, None), 1):
        new.append(("review-read-merged-%d" % n, " || ".join(c)))
    for n, c in enumerate(chunks([(LISTP % p, 0) for p in sorted(lists)], None, base_ops=1), 1):
        new.append(("review-list-merged-%d" % n, "%s && (%s)" % (TYPE, " || ".join(c))))
    desc = "merged by consolidate_review_iam.py; read prefixes: %s; list prefixes: %s" % (",".join(sorted(reads)), ",".join(sorted(lists)))
    # remove member from old bindings (drop binding if empty), add merged ones
    keep = []
    for b in pol["bindings"]:
        if b in mine:
            rest = [m for m in b["members"] if m != member]
            if rest: keep.append(dict(b, members=rest))
        else:
            keep.append(b)
    for t, e in new:
        keep.append({"role": ROLE, "members": [member], "condition": {"title": t, "description": desc[:256], "expression": e}})
    pol["bindings"] = keep; pol["version"] = 3
    print("before: %d conditional viewer bindings (%s)" % (len(mine), ", ".join(titles)))
    print("after:  %d" % len(new))
    for t, e in new: print("  %s [%d ops]: %s" % (t, e.count("&&") + e.count("||"), e))
    if dry: return
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(pol, f)
    subprocess.check_call(["gcloud", "storage", "buckets", "set-iam-policy", "gs://" + bucket, f.name, "--format=none"])
    print("applied")


main()
