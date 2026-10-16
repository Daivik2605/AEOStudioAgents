"""The diagnosis comparison: findings by code, closed / still open / new (PLAN.md section 8, "what is deterministic").
Same input, same answer, no sampling, so no range is needed or shown."""

from __future__ import annotations

from compare.errors import CompareError

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
COLUMNS = "id, checkpoint, checked_at, platform, platform_plan, verdict, findings, url, business_id"


def _row(r) -> dict:
    return {"id": str(r[0]), "checkpoint": r[1], "checked_at": r[2], "platform": r[3], "plan": r[4], "verdict": r[5],
            "findings": r[6] or [], "url": r[7]}


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


def compare_diagnoses(a: dict, b: dict, work: list[dict], until: str) -> dict:
    """`work` are the work-log entries ({date, operator, text, finding, ...}); `until` is the last day (ISO) a
    work entry can count toward B."""
    fa, fb = _by_code(a["findings"]), _by_code(b["findings"])

    def work_for(code: str) -> list[dict]:
        return [{"date": w["date"], "operator": w["operator"], "text": w["text"]}
                for w in work if w["finding"] == code and w["date"] <= until]

    closed = _sorted([{"code": c, "severity": fa[c]["severity"], "what": fa[c]["what"], "work": work_for(c)}
                      for c in fa if c not in fb])
    still = _sorted([{"code": c, "severity": fb[c]["severity"], "severity_in_a": fa[c]["severity"],
                      "what": fb[c]["what"], "work": work_for(c)} for c in fa if c in fb])
    new = _sorted([{"code": c, "severity": fb[c]["severity"], "what": fb[c]["what"]} for c in fb if c not in fa])

    def change(field: str) -> dict:
        return {"a": a[field], "b": b[field], "changed": a[field] != b[field]}

    caveats = []
    for code in ("CRAWLER_TEST_INCONCLUSIVE", "ROBOTS_TXT_UNREACHABLE"):
        if code in fb and code not in fa:
            caveats.append(f"B has {code}: its crawler and robots.txt checks did not complete, "
                           "so findings from those checks that are missing from B cannot be called closed.")
    total = len(fa)
    return {
        "headline": (f"{len(closed)} of {total} findings closed" if total else "A had no findings, so nothing could close"),
        "closed_count": len(closed), "a_total": total, "b_total": len(fb),
        "closed": closed, "still_open": still, "new": new,
        "platform": change("platform"), "plan": change("plan"), "verdict": change("verdict"),
        "caveats": caveats,
    }
