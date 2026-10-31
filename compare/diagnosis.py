"""The diagnosis comparison: findings by code, with four outcomes (PLAN.md section 8, "what is deterministic", and step 22c):
  fixed             in A, not in B, and what it was about still exists
  no longer applies in A, not in B, but what it was about was removed (the old structured data, for example)
  still open        in both
  new               in B, not in A
Same input, same answer, no sampling, so no range is needed or shown."""

from __future__ import annotations

from compare.errors import CompareError
from diagnose.codes import check_for, subject_removed_by
from diagnose.not_checked import CONTENT_CHECKS, content_checks_ran, site_checks_ran

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
COLUMNS = "id, checkpoint, checked_at, platform, platform_plan, verdict, findings, url, business_id, site_checks"


def _row(r) -> dict:
    # Which checks this diagnosis ran. One made before step 25 ran none of the sitemap / address checks: they are absent there, not
    # "unknown". One made from step 25c on records every check it ran, and leaves out those it could not (blocked, or the
    # page did not load).
    ran, content = site_checks_ran(r[9]), content_checks_ran(r[9])
    return {"id": str(r[0]), "checkpoint": r[1], "checked_at": r[2], "platform": r[3], "plan": r[4], "verdict": r[5],
            "findings": r[6] or [], "url": r[7], "checks_ran": ran, "content_ran": content}


def pick_diagnosis(conn, business_id: str, label: str, given: str | None) -> dict | None:
    """The diagnosis asked for, or the latest one at this checkpoint (None if there is none)."""
    with conn.cursor() as cur:
        if given:
            cur.execute(f"SELECT {COLUMNS} FROM diagnoses WHERE id = %s", (given,))
            r = cur.fetchone()
            if r is None or str(r[8]) != business_id:
                raise CompareError(f"diagnosis {given} does not exist for this business")
            return _row(r)
        cur.execute(f"SELECT {COLUMNS} FROM diagnoses WHERE business_id = %s AND checkpoint = %s "
                    "ORDER BY checked_at DESC, created_at DESC, id LIMIT 1", (business_id, label))
        r = cur.fetchone()
    return _row(r) if r else None


def _by_code(findings: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in findings:
        out.setdefault(f["code"], f)
    return out


def _sorted(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda i: (SEVERITY_RANK.get(i["severity"], 9), i["code"]))


def headline_text(fixed: int, gone: int, still: int, new: int, total: int) -> str:
    """'2 fixed · 6 no longer apply · 1 still open · 1 new'. All four are always shown, so nothing is hidden."""
    if total == 0:
        return f"A had no findings, so there was nothing to fix · {new} new"
    verb = "no longer applies" if gone == 1 else "no longer apply"
    return f"{fixed} fixed · {gone} {verb} · {still} still open · {new} new"


def compare_diagnoses(a: dict, b: dict, work: list[dict], until: str) -> dict:
    """`work` are the work-log entries ({date, operator, text, finding, ...}); `until` is the last day (ISO) a
    work entry can count toward B."""
    fa, fb = _by_code(a["findings"]), _by_code(b["findings"])

    def work_for(code: str) -> list[dict]:
        return [{"date": w["date"], "operator": w["operator"], "text": w["text"]}
                for w in work if w["finding"] == code and w["date"] <= until]

    # A finding that is missing from B is only "fixed" if the check that could have raised it ran in B; one that is in B
    # is only "new" if that check ran in A. Otherwise it is listed as not compared, never as fixed or new.
    # (`checks_ran` is absent on a diagnosis passed in as plain data: then everything is compared, as before.)
    def not_run(code: str, side: dict) -> bool:
        check, ran = check_for(code), side.get("checks_ran")
        if check is None or ran is None:
            return False
        return check not in [*ran, *side.get("content_ran", CONTENT_CHECKS)]
    not_rechecked = sorted(c for c in fa if c not in fb and not_run(c, b))
    not_compared = sorted(c for c in fb if c not in fa and not_run(c, a))

    later_codes = set(fb)
    fixed, gone = [], []
    for c in fa:
        if c in fb or c in not_rechecked:
            continue
        item = {"code": c, "severity": fa[c]["severity"], "what": fa[c]["what"], "work": work_for(c)}
        removed_by = subject_removed_by(c, later_codes)
        if removed_by:      # its subject is gone: say what replaced it, and do not call it fixed
            gone.append({**item, "outcome": "no_longer_applies",
                         "replaced_by": [{"code": r, "severity": fb[r]["severity"], "what": fb[r]["what"]} for r in removed_by]})
        else:
            fixed.append({**item, "outcome": "fixed"})
    fixed, gone = _sorted(fixed), _sorted(gone)
    still = _sorted([{"code": c, "severity": fb[c]["severity"], "severity_in_a": fa[c]["severity"],
                      "what": fb[c]["what"], "work": work_for(c)} for c in fa if c in fb])
    new = _sorted([{"code": c, "severity": fb[c]["severity"], "what": fb[c]["what"]}
                   for c in fb if c not in fa and c not in not_compared])

    def change(field: str) -> dict:
        return {"a": a[field], "b": b[field], "changed": a[field] != b[field]}

    caveats = []
    for code in ("CRAWLER_TEST_INCONCLUSIVE", "ROBOTS_TXT_UNREACHABLE"):
        if code in fb and code not in fa:
            caveats.append(f"B has {code}: its crawler and robots.txt checks did not complete, "
                           "so findings from those checks that are missing from B cannot be called fixed.")
    if not_rechecked:
        caveats.append(f"B did not run the check behind {', '.join(not_rechecked)}, so {'this finding' if len(not_rechecked) == 1 else 'these findings'} "
                       "from A cannot be called fixed. Run that check again (for the old addresses: `aeo diagnose … --old-urls <file>`).")
    if not_compared:
        caveats.append(f"A did not run the check behind {', '.join(not_compared)} (it was made before that check existed, or without it), "
                       f"so {'this finding' if len(not_compared) == 1 else 'these findings'} in B cannot be called new: it may have been there all along.")
    total = len(fa)
    return {
        "headline": headline_text(len(fixed), len(gone), len(still), len(new), total),
        "fixed_count": len(fixed), "no_longer_applies_count": len(gone), "still_open_count": len(still), "new_count": len(new),
        "closed_count": len(fixed), "a_total": total, "b_total": len(fb),
        # `closed` is the same list as `fixed`. The name is kept so older readers and saved files still work; it no longer
        # contains findings whose subject was removed.
        "fixed": fixed, "closed": fixed, "no_longer_applies": gone, "still_open": still, "new": new,
        "platform": change("platform"), "plan": change("plan"), "verdict": change("verdict"),
        "caveats": caveats, "not_rechecked": not_rechecked, "not_compared": not_compared,
    }
