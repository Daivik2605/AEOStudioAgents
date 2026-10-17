"""The audit comparison (PLAN.md section 8). Every score comes from scoring/scoring.py through the audit report's
own counting (audit/report_data.py); every range from scoring/bootstrap.py. No AI.

Two runs are only comparable if they asked the same questions, in the same design, and both finished. Anything else
is refused, not softened: a different question changes the answers more than re-asking does.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from audit import store
from audit.report_data import (MEASURES, ReportError, _block, _share_of_voice, _sources, answer_counts, design_label,
                               engine_label, load_answers, select_run)
from compare.errors import CompareError
from scoring import bootstrap, scoring

DETECTION_FLOOR_POINTS = 20
MEASURE_ORDER = ["visibility", "recommendation", "brand_free", "visibility_among_named",
                 "recommendation_among_named", "gap", "retrieval", "accuracy"]
TARGET_MEASURES = ["visibility", "recommendation", "gap"]
MOVERS = 5
REAL, NOISE, NO_RANGE = "real change", "within the noise", "no range (too few questions)"


def label_for(low: float | None, high: float | None) -> str:
    """"real change" only if the range of the difference excludes zero."""
    if low is None or high is None:
        return NO_RANGE
    return REAL if (low > 0 or high < 0) else NOISE


# ---- loading and checking a run ---------------------------------------------------------------------------------------------

def load_side(conn, business_id: str, label: str, given: str | None) -> tuple[dict | None, str | None]:
    """(side, None) or (None, why there is none). A run named with --a-run/--b-run that is unfinished is REFUSED;
    when no run is named, an unfinished run is simply not used."""
    try:
        chosen, _ = select_run(conn, business_id, run=given, checkpoint=None if given else label)
    except ReportError as exc:
        if given:
            raise CompareError(str(exc)) from exc
        return None, f"no audit run at checkpoint '{label}'"
    if chosen["status"] != "complete":
        if given:
            raise CompareError(f"run {chosen['id']} is not finished (status {chosen['status']}). A partial run is not "
                               "comparable: its missing answers would look like a change.")
        return None, f"the only audit run at checkpoint '{label}' is not finished"
    if chosen["run_type"] != "visibility":
        raise CompareError(f"run {chosen['id']} is a {chosen['run_type']} run; only visibility runs are compared")
    started = store.started_details(conn, chosen["id"]) or {}
    answers = load_answers(conn, chosen["id"])
    scored = [a for a in answers if a["named"] is not None]
    if not scored:
        raise CompareError(f"run {chosen['id']} has no answers the model has read yet, so there is nothing to compare")
    profile = store.load_business_and_profile(conn, business_id, chosen["profile_id"])
    planned = started.get("questions") or []
    questions = list(dict.fromkeys(a["question"] for a in answers))
    engines = list(started.get("engines") or []) + [e for e in dict.fromkeys(a["engine"] for a in answers)
                                                    if e not in (started.get("engines") or [])]
    return {"run": chosen, "started": started, "answers": answers, "scored": scored, "questions": questions,
            "targets": {q["text"] for q in planned if q.get("is_target")}, "tags_recorded": bool(planned),
            "planned_texts": {q["text"] for q in planned},
            "engines": engines, "repeats": int(started.get("repeats") or 1),
            "facts": profile["facts"] if profile else [], "profile_version": profile["profile_version"] if profile else None,
            }, None


def check_comparable(a: dict, b: dict, label_a: str, label_b: str) -> None:
    """Raises CompareError, with the reason, unless the two runs measured the same thing."""
    sa, sb = a["started"], b["started"]
    va, vb = sa.get("question_set_version"), sb.get("question_set_version")
    ha, hb = sa.get("question_set_sha256"), sb.get("question_set_sha256")
    same_set = (ha == hb) if (ha and hb) else (va == vb and set(a["questions"]) == set(b["questions"]))
    if not same_set:
        raise CompareError(
            f"The two runs used different question sets (A: {va or 'unknown'}, hash {(ha or 'none')[:12]}; "
            f"B: {vb or 'unknown'}, hash {(hb or 'none')[:12]}). The answers are not comparable: even a reworded "
            "question changes the set of answers more than asking it again does (PLAN.md section 8), so a "
            "difference could be the questions and not the client. Compare two runs of the same frozen set.")
    da = (len(a["questions"]), sorted(a["engines"]), a["repeats"])
    db = (len(b["questions"]), sorted(b["engines"]), b["repeats"])
    if da != db:
        def show(d):
            return f"{d[0]} question(s) x {len(d[1])} engine(s) ({', '.join(d[1])}) x {d[2]} run(s)"
        raise CompareError(f"The two runs used different designs (A: {show(da)}; B: {show(db)}). The answers are not "
                           "comparable: the range of a score depends on how many answers stand behind it.")
    if set(a["questions"]) != set(b["questions"]):
        raise CompareError("The two runs have the same question-set hash but were answered for different questions; "
                           "something is wrong with one of them. Check the runs before comparing.")


# ---- the numbers ------------------------------------------------------------------------------------------------------------

def _units(scored: list[dict], questions: list[str]) -> list[dict]:
    by_question: dict[str, list[dict]] = defaultdict(list)
    for a in scored:
        by_question[a["question"]].append(answer_counts(a))
    return [bootstrap.add_counts(by_question.get(q, [])) for q in questions]


def _summary(side: dict) -> dict:
    r = side["run"]
    return {"run_id": r["id"], "checkpoint": r["checkpoint"],
            "started_at": r["started_at"].isoformat() if r["started_at"] else None,
            "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
            "question_set_version": side["started"].get("question_set_version"),
            "profile_version": side["profile_version"], "confirmed_facts": len(side["facts"]),
            "answers": len(side["answers"]), "scored": len(side["scored"]),
            "failed_extractions": len(side["answers"]) - len(side["scored"]),
            "engines": [{"engine": e, "label": engine_label(e)} for e in side["engines"]]}


def _headline(a: dict, b: dict, with_accuracy: bool) -> dict:
    questions = sorted(set(a["questions"]) | set(b["questions"]))
    block_a, block_b = _block(a["scored"], with_accuracy=with_accuracy), _block(b["scored"], with_accuracy=with_accuracy)
    names = [m for m in MEASURE_ORDER if m in block_a["measures"]]
    units_a, units_b = _units(a["scored"], questions), _units(b["scored"], questions)
    ranges = bootstrap.paired_ranges(units_a, units_b, {m: MEASURES[m][0] for m in names})
    measures = {}
    for m in names:
        va, vb = block_a["measures"][m], block_b["measures"][m]
        rng = ranges[m]
        change = (vb["value"] - va["value"]) if va["value"] is not None and vb["value"] is not None else None
        low, high = (rng if rng else (None, None))
        measures[m] = {"a": va, "b": vb,
                       "change": {"value": change, "low": low, "high": high,
                                  "label": label_for(low, high) if change is not None else "not available"}}
    return {"measures": measures, "tier_a": block_a["tier"], "tier_b": block_b["tier"],
            "mentioned_only": {"a": block_a["mentioned_only"], "b": block_b["mentioned_only"]},
            "retrieval_unknown": {"a": block_a["retrieval_unknown"], "b": block_b["retrieval_unknown"]},
            "questions": len(questions)}


def _targets_vs_control(a: dict, b: dict) -> dict:
    if not a["tags_recorded"] or not b["tags_recorded"]:
        who = " and ".join(l for l, s in (("A", a), ("B", b)) if not s["tags_recorded"])
        return {"available": False, "reason": f"run {who} did not record which questions are targets, so targets and control cannot be told apart"}
    if a["targets"] != b["targets"]:
        return {"available": False, "reason": "the two runs mark different questions as targets, so there is no single target group to compare"}
    questions = sorted(set(a["questions"]) | set(b["questions"]))
    target_q = [q for q in questions if q in a["targets"]]
    control_q = [q for q in questions if q not in a["targets"]]
    if not target_q:
        return {"available": False, "reason": "no question is marked as a target"}
    if not control_q:
        return {"available": False, "reason": "every question is marked as a target, so there is no control"}

    def split(side, qs):
        return _units(side["scored"], qs)
    stats = {m: MEASURES[m][0] for m in TARGET_MEASURES}
    found = bootstrap.paired_difference_ranges((split(a, target_q), split(b, target_q)),
                                               (split(a, control_q), split(b, control_q)), stats)
    out = {}
    for m in TARGET_MEASURES:
        fn = MEASURES[m][0]

        def change(qs):
            ua, ub = bootstrap.add_counts(split(a, qs)), bootstrap.add_counts(split(b, qs))
            va, vb = fn(ua), fn(ub)
            return None if va is None or vb is None else vb - va
        ct, cc = change(target_q), change(control_q)
        parts = {}
        for key, value in (("target", ct), ("control", cc), ("difference", None if ct is None or cc is None else ct - cc)):
            rng = found[m][key]
            low, high = rng if rng else (None, None)
            parts[key] = {"value": value, "low": low, "high": high,
                          "label": label_for(low, high) if value is not None else "not available"}
        out[m] = parts
    return {"available": True, "reason": None, "target_questions": len(target_q), "control_questions": len(control_q),
            "measures": out,
            "note": "The difference between the two changes is the honest measure of our work: it removes whatever moved "
                    "every question, such as an engine update. The control group is small, so its range is wide."}


def _business_counts(scored: list[dict]) -> dict:
    out: dict[str, dict] = {}
    spell: dict[str, Counter] = defaultdict(Counter)
    for a in scored:
        seen = {}
        for b in a["named"]:
            k = " ".join(b["name"].lower().split())
            spell[k][b["name"]] += 1
            s = seen.setdefault(k, {"recommended": False, "is_client": False})
            s["recommended"] = s["recommended"] or bool(b.get("recommended"))
            s["is_client"] = s["is_client"] or bool(b.get("is_client"))
        for k, s in seen.items():
            row = out.setdefault(k, {"answers": 0, "recommended": 0, "is_client": False})
            row["answers"] += 1
            row["recommended"] += int(s["recommended"])
            row["is_client"] = row["is_client"] or s["is_client"]
    for k, row in out.items():
        row["name"] = sorted(spell[k].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return out


def _client_row(scored: list[dict]) -> dict | None:
    sov = _share_of_voice(scored)
    rows = [r for r in sov["rows"] + sov["client_outside_top"] if r["is_client"]]
    if not rows:
        return None
    r = rows[0]
    return {"name": r["name"], "rank": r["rank"], "answers": r["answers"], "recommended": r["recommended"],
            "of_businesses": sov["businesses_named"]}


def _share_of_voice_change(a: dict, b: dict) -> dict:
    ca, cb = _business_counts(a["scored"]), _business_counts(b["scored"])
    moves = []
    for k in set(ca) | set(cb):
        ra, rb = ca.get(k), cb.get(k)
        if (ra or rb)["is_client"]:
            continue
        before, after = (ra["answers"] if ra else 0), (rb["answers"] if rb else 0)
        if before != after:
            moves.append({"name": (rb or ra)["name"], "a": before, "b": after, "change": after - before})
    gained = sorted([m for m in moves if m["change"] > 0], key=lambda m: (-m["change"], m["name"].casefold()))[:MOVERS]
    lost = sorted([m for m in moves if m["change"] < 0], key=lambda m: (m["change"], m["name"].casefold()))[:MOVERS]
    return {"client": {"a": _client_row(a["scored"]), "b": _client_row(b["scored"])},
            "scored_answers": {"a": len(a["scored"]), "b": len(b["scored"])},
            "gained": gained, "lost": lost,
            "note": "Counts of answers, not rates. A move of a few answers is within the noise."}


def _wrong_keys(side: dict) -> dict[str, dict]:
    """One entry per wrong fact. The key is the fact's label when the claim check named one, else the claim's own words."""
    out: dict[str, dict] = {}
    for a in side["scored"]:
        for c in a["claims"] or []:
            if c["verdict"] != "incorrect":
                continue
            field = (c.get("field") or "").strip()
            key = ("field:" + field.casefold()) if field else ("claim:" + " ".join(c["claim"].lower().split()))
            e = out.setdefault(key, {"field": field or None, "claims": [], "answers": 0})
            e["answers"] += 1
            if c["claim"] not in e["claims"]:
                e["claims"].append(c["claim"])
    return out


def _wrong_facts(a: dict, b: dict) -> dict:
    if not a["facts"] or not b["facts"]:
        who = " and ".join(l for l, s in (("A", a), ("B", b)) if not s["facts"])
        return {"available": False, "reason": f"the truth document had no confirmed facts when run {who} was judged, so no claim "
                                              "was checked and wrong facts cannot be compared"}
    wa, wb = _wrong_keys(a), _wrong_keys(b)

    def row(k, src_a, src_b):
        e = src_b[k] if k in src_b else src_a[k]
        return {"fact": e["field"] or e["claims"][0], "field": e["field"], "claims_a": (src_a[k]["claims"] if k in src_a else []),
                "claims_b": (src_b[k]["claims"] if k in src_b else []),
                "answers_a": src_a[k]["answers"] if k in src_a else 0, "answers_b": src_b[k]["answers"] if k in src_b else 0}
    order = lambda r: r["fact"].casefold()
    return {"available": True, "reason": None,
            "fixed": sorted([row(k, wa, wb) for k in wa if k not in wb], key=order),
            "still": sorted([row(k, wa, wb) for k in wa if k in wb], key=order),
            "new": sorted([row(k, wa, wb) for k in wb if k not in wa], key=order),
            "profile_versions": {"a": a["profile_version"], "b": b["profile_version"]},
            "different_truth_documents": a["profile_version"] != b["profile_version"]}


def _question_list(side: dict) -> dict:
    """The whole question set, in the order it was asked, with the tags recorded when the run started."""
    planned = side["started"].get("questions") or []
    if planned:
        return {"tags_recorded": True, "items": [{"number": i, "text": q["text"], "is_target": bool(q.get("is_target"))}
                                                 for i, q in enumerate(planned, start=1)]}
    return {"tags_recorded": False, "items": [{"number": i, "text": t, "is_target": False}
                                              for i, t in enumerate(side["questions"], start=1)]}


def compare_audits(a: dict, b: dict, domain: str, label_a: str, label_b: str) -> dict:
    check_comparable(a, b, label_a, label_b)
    with_accuracy = bool(a["facts"]) and bool(b["facts"])
    label = design_label(len(a["questions"]), len(a["engines"]), a["repeats"])
    return {
        "available": True, "reason": None,
        "design": {"label": label, "questions": len(a["questions"]), "engines": a["engines"], "repeats": a["repeats"]},
        "questions": _question_list(a),
        "question_set": {"version": a["started"].get("question_set_version"),
                         "sha256": a["started"].get("question_set_sha256")},
        "a": _summary(a), "b": _summary(b),
        "headline": _headline(a, b, with_accuracy),
        "accuracy_compared": with_accuracy,
        "targets_vs_control": _targets_vs_control(a, b),
        "share_of_voice": _share_of_voice_change(a, b),
        "wrong_facts": _wrong_facts(a, b),
        "sources": {"client_domain": domain,
                    "a": {k: v for k, v in _sources(a["answers"], domain).items() if k in ("answers", "answers_with_sources", "client_cited_in")},
                    "b": {k: v for k, v in _sources(b["answers"], domain).items() if k in ("answers", "answers_with_sources", "client_cited_in")}},
        "failed_extractions": {"a": len(a["answers"]) - len(a["scored"]), "b": len(b["answers"]) - len(b["scored"])},
    }
