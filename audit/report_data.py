"""Everything the audit report says, worked out as plain data (PLAN.md sections 2, 8 and 10). No AI.

Every score comes from scoring/scoring.py; every range from scoring/bootstrap.py. This file only counts
answers and hands the counts to them. The result is one dictionary that is JSON-safe, so the .json file
is exactly this data and the .md file is made from nothing else (see audit/report_render.py).

Two rules shape it:
  * An answer whose extraction failed has no list of named businesses. It is counted and listed, and
    left out of every score. It is never treated as "named nobody".
  * Nothing here is per-question. Questions are the unit that gets resampled for the ranges, but no
    figure is ever reported for a single question (PLAN.md section 8).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Callable
from uuid import UUID

from audit import store
from audit.fill_in import ENGINE_LABELS
from dossier.questions import brand_terms, classify, length_of
from scoring import bootstrap, scoring

# The before/after design of PLAN.md section 8: 32 frozen questions x 2 engines x 3 runs. Anything else is a snapshot.
BEFORE_AFTER = {"questions": 32, "engines": 2, "repeats": 3}
BEFORE_AFTER_LABEL, SNAPSHOT_LABEL = "Before/after measurement", "Snapshot"

TOP_BUSINESSES = 15          # rows in the share-of-voice table
TOP_REASON_BUSINESSES = 5    # businesses whose reasons are quoted
MAX_REASONS = 8
TOP_DOMAINS = 15
MAX_DESCRIPTORS = 25
MAX_CLIENT_URLS = 10


class ReportError(Exception):
    """The report cannot be made (no such run, run not finished, nothing to score). The message says what to do."""


def design_label(questions: int, engines: int, repeats: int) -> str:
    """"Before/after measurement" only for exactly 32 questions x 2 engines x 3 runs (PLAN.md section 8); else "Snapshot"."""
    if (questions, engines, repeats) == (BEFORE_AFTER["questions"], BEFORE_AFTER["engines"], BEFORE_AFTER["repeats"]):
        return BEFORE_AFTER_LABEL
    return SNAPSHOT_LABEL


def engine_label(engine: str) -> str:
    return ENGINE_LABELS.get(engine, engine.capitalize())


# ---- choosing and loading a run ----------------------------------------------------------------------------------

RUN_COLUMNS = "id, business_id, status, checkpoint, run_type, mode, business_profile_id, started_at, completed_at, created_at"


def _run_dict(r) -> dict:
    return {"id": str(r[0]), "business_id": str(r[1]), "status": r[2], "checkpoint": r[3], "run_type": r[4],
            "mode": r[5], "profile_id": str(r[6]), "started_at": r[7], "completed_at": r[8], "created_at": r[9]}


def select_run(conn, business_id: str, *, run: str | None = None, checkpoint: str | None = None) -> tuple[dict, int]:
    """Returns (the run, how many OTHER runs matched). With --run, exactly that run. Otherwise the latest
    finished run (for the checkpoint, if one is given), or the latest of any status if none is finished."""
    if run and checkpoint:
        raise ReportError("give --run or --checkpoint, not both")
    with conn.cursor() as cur:
        if run:
            try:
                UUID(run)
            except ValueError:
                raise ReportError(f"'{run}' is not a run id") from None
            cur.execute(f"SELECT {RUN_COLUMNS} FROM probe_runs WHERE id = %s", (run,))
            row = cur.fetchone()
            if row is None or str(row[1]) != business_id:
                raise ReportError(f"run {run} does not exist for this business")
            return _run_dict(row), 0
        sql = f"SELECT {RUN_COLUMNS} FROM probe_runs WHERE business_id = %s AND run_type = 'visibility'"
        args: list = [business_id]
        if checkpoint:
            sql += " AND checkpoint = %s"
            args.append(checkpoint)
        cur.execute(sql + " ORDER BY created_at DESC, id", args)
        rows = [_run_dict(r) for r in cur.fetchall()]
    if not rows:
        raise ReportError("this business has no visibility audit run" + (f" at checkpoint '{checkpoint}'" if checkpoint else ""))
    chosen = next((r for r in rows if r["status"] == "complete"), rows[0])
    return chosen, len(rows) - 1


def load_answers(conn, run_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, query_text, engine, repeat_number, raw_response_text, sources_cited, businesses_named, "
            "claims_checked, retrieval_activated, engine_version, logged_in_state, location_context, created_at "
            "FROM probe_results WHERE probe_run_id = %s ORDER BY created_at, id", (run_id,))
        cols = ["id", "question", "engine", "repeat", "text", "sources", "named", "claims", "searched", "mode",
                "logged_in", "location", "created_at"]
        return [dict(zip(cols, r)) | {"id": str(r[0])} for r in cur.fetchall()]


def operators_for(conn, run_id: str, started: dict) -> list[str]:
    """Who did the work. Fill-in sheets record the name typed on the sheet, once per import; a terminal run
    records the person who started it."""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT details->>'operator' FROM client_journal WHERE entry_type = 'note' "
                    "AND details->>'probe_run_id' = %s AND details ? 'operator'", (run_id,))
        names = sorted(r[0] for r in cur.fetchall() if r[0])
    if not names and started.get("started_by"):
        names = [started["started_by"]]
    return names


# ---- counting ------------------------------------------------------------------------------------------------------

def answer_counts(a: dict) -> dict[str, int]:
    """What one scored answer contributes to each score. All plain yes/no counts."""
    named = a["named"]
    client = [b for b in named if b.get("is_client")]
    competitors = [b for b in named if not b.get("is_client")]
    claims = a["claims"] or []
    return {
        "n": 1,
        "named": int(bool(client)),
        "recommended": int(any(b.get("recommended") for b in client)),
        "named_any": int(bool(named)),
        "brand_free": int(not named),
        "gap": int(bool(competitors) and not client),
        "searched": int(a["searched"] is True),
        "searched_known": int(a["searched"] is not None),
        "correct": sum(1 for c in claims if c["verdict"] == "correct"),
        "incorrect": sum(1 for c in claims if c["verdict"] == "incorrect"),
        "unverifiable": sum(1 for c in claims if c["verdict"] == "unverifiable"),
    }


ACCURACY_MEASURES = ("accuracy", "accuracy_brand")


def _rate(fn: Callable[[int, int], float], num: Callable[[dict], int], den: Callable[[dict], int]):
    return lambda c: fn(num(c), den(c)) if den(c) > 0 else None


# name -> (label for the report, statistic computed from summed counts, (numerator, denominator) for display)
MEASURES: dict[str, tuple[Callable[[dict], "float | None"], Callable[[dict], int], Callable[[dict], int]]] = {}
for _name, _fn, _num, _den in [
    ("visibility", scoring.visibility_score, lambda c: c["named"], lambda c: c["n"]),
    ("recommendation", scoring.recommendation_rate, lambda c: c["recommended"], lambda c: c["n"]),
    ("brand_free", scoring.brand_free_rate, lambda c: c["brand_free"], lambda c: c["n"]),
    ("visibility_among_named", scoring.visibility_score, lambda c: c["named"], lambda c: c["named_any"]),
    ("recommendation_among_named", scoring.recommendation_rate, lambda c: c["recommended"], lambda c: c["named_any"]),
    ("gap", scoring.gap_score, lambda c: c["gap"], lambda c: c["n"]),
    ("retrieval", scoring.retrieval_activation_rate, lambda c: c["searched"], lambda c: c["searched_known"]),
    # accuracy_score takes (correct, incorrect), unlike the others which take (numerator, denominator).
    # Unverifiable claims are in neither count, as PLAN.md section 8 says.
    ("accuracy", lambda correct, checked: scoring.accuracy_score(correct, checked - correct),
     lambda c: c["correct"], lambda c: c["correct"] + c["incorrect"]),
    # The same formula, worked out on brand-question answers only (the buyer already knows the name). Never part of a
    # discovery block: _headline_block adds it separately.
    ("accuracy_brand", lambda correct, checked: scoring.accuracy_score(correct, checked - correct),
     lambda c: c["correct"], lambda c: c["correct"] + c["incorrect"]),
]:
    MEASURES[_name] = (_rate(_fn, _num, _den), _num, _den)


# Recognition is measured on brand questions only. It is deliberately not in MEASURES: put into a discovery block it
# would just repeat visibility (PLAN.md section 8: AI always "names" a business you asked about by name).
RECOGNITION = _rate(scoring.recognition_rate, lambda c: c["named"], lambda c: c["n"])


def question_types(planned: list[dict] | None, texts: list[str], business: dict) -> dict[str, dict]:
    """For each question text: its type (discovery / brand), where that came from, its length and its language.
    A run started after step 22d recorded all of this. An older run recorded only intent and target, so the type is
    worked out now from whether the question names the business, and the report says it was derived."""
    terms = brand_terms(business["name"], business["domain"])
    out: dict[str, dict] = {}
    for q in planned or []:
        if q.get("type"):
            kind, how = q["type"], q.get("type_source") or "tag"
        else:
            kind, how = classify(q["text"], None, terms)
        out[q["text"]] = {"type": kind, "type_source": how, "length": q.get("length") or length_of(q["text"]),
                          "language": q.get("language")}
    for t in texts:
        if t not in out:
            kind, how = classify(t, None, terms)
            out[t] = {"type": kind, "type_source": how, "length": length_of(t), "language": None}
    return out


def _headline_block(discovery: list[dict], scored: list[dict], brand: list[dict], *, with_accuracy: bool) -> dict:
    """The headline: every visibility figure from discovery answers only. Accuracy is measured two ways, because a
    wrong fact is wrong wherever it was said:
      accuracy        every answer in which a claim about the client was checked (discovery and brand)
      accuracy_brand  brand-question answers only, where AI was asked about the client by name
    The second is left out when there are no brand answers."""
    block = _block(discovery, with_accuracy=False)
    if with_accuracy:
        everything = _block(scored, with_accuracy=True)
        block["measures"]["accuracy"] = everything["measures"]["accuracy"]
        block["claims"] = everything["claims"]
        if brand:
            block["measures"]["accuracy_brand"] = _accuracy_brand(brand)
    return block


def _accuracy_brand(brand: list[dict]) -> dict:
    """Accuracy on brand-question answers, with its range (questions resampled, as everywhere)."""
    by_question: dict[str, list[dict]] = defaultdict(list)
    for a in brand:
        by_question[a["question"]].append(answer_counts(a))
    units = [bootstrap.add_counts(v) for _, v in sorted(by_question.items())]
    total = bootstrap.add_counts(units)
    stat, num, den = MEASURES["accuracy_brand"]
    rng = bootstrap.ranges(units, {"accuracy_brand": stat})["accuracy_brand"]
    return {"value": stat(total), "low": rng[0] if rng else None, "high": rng[1] if rng else None,
            "num": num(total), "den": den(total)}


def _brand_section(brand: list[dict]) -> dict:
    """Recognition on brand questions, with its range. Counts only, as everywhere: nothing per question."""
    if not brand:
        return {"available": False, "reason": "this run asked no brand questions (questions that name the client)"}
    by_question: dict[str, list[dict]] = defaultdict(list)
    for a in brand:
        by_question[a["question"]].append(answer_counts(a))
    units = [bootstrap.add_counts(v) for _, v in sorted(by_question.items())]
    total = bootstrap.add_counts(units)
    rng = bootstrap.ranges(units, {"recognition": RECOGNITION})["recognition"]
    return {"available": True, "reason": None, "questions": len(units), "answers": total.get("n", 0),
            "recognition": {"value": RECOGNITION(total), "low": rng[0] if rng else None,
                            "high": rng[1] if rng else None, "num": total.get("named", 0), "den": total.get("n", 0)}}


def _by_group(discovery: list[dict], group_of: Callable[[dict], "str | None"], order: list[str] | None) -> dict:
    """The discovery headline split by a tag (question length or language). A question with no value for the tag is
    left out of every group and counted, never guessed."""
    groups: dict[str, list[dict]] = defaultdict(list)
    untagged = {a["question"] for a in discovery if group_of(a) is None}
    for a in discovery:
        value = group_of(a)
        if value is not None:
            groups[value].append(a)
    keys = [k for k in (order or sorted(groups)) if k in groups]
    return {"groups": [{"group": k, **_block(groups[k], with_accuracy=False)} for k in keys],
            "questions_without_tag": len(untagged)}


def _block(answers: list[dict], *, with_accuracy: bool) -> dict:
    """The headline numbers for one group of answers, each with its range."""
    by_question: dict[str, list[dict]] = defaultdict(list)
    for a in answers:
        by_question[a["question"]].append(answer_counts(a))
    units = [bootstrap.add_counts(v) for _, v in sorted(by_question.items())]
    total = bootstrap.add_counts(units)
    names = [m for m in MEASURES if m not in ACCURACY_MEASURES or (m == "accuracy" and with_accuracy)]
    found = bootstrap.ranges(units, {m: MEASURES[m][0] for m in names})
    out: dict = {"answers": total.get("n", 0), "questions": len(units), "measures": {}}
    for m in names:
        stat, num, den = MEASURES[m]
        rng = found[m]
        out["measures"][m] = {"value": stat(total), "low": rng[0] if rng else None, "high": rng[1] if rng else None,
                              "num": num(total), "den": den(total)}
    vis = out["measures"]["visibility"]
    out["tier"] = scoring.visibility_tier(vis["value"]) if vis["value"] is not None else None
    out["tier_low"] = scoring.visibility_tier(max(0.0, min(100.0, vis["low"]))) if vis["low"] is not None else None
    out["tier_high"] = scoring.visibility_tier(max(0.0, min(100.0, vis["high"]))) if vis["high"] is not None else None
    out["mentioned_only"] = total.get("named", 0) - total.get("recommended", 0)
    out["retrieval_unknown"] = total.get("n", 0) - total.get("searched_known", 0)
    out["claims"] = {k: total.get(k, 0) for k in ("correct", "incorrect", "unverifiable")}
    return out


# ---- the other sections ----------------------------------------------------------------------------------------------

def _key(name: str) -> str:
    return " ".join(name.lower().split())


def _share_of_voice(scored: list[dict]) -> dict:
    mentions: dict[str, dict] = {}
    spellings: dict[str, Counter] = defaultdict(Counter)
    for a in scored:
        best: dict[str, dict] = {}
        for b in a["named"]:
            k = _key(b["name"])
            spellings[k][b["name"]] += 1
            seen = best.setdefault(k, {"position": b.get("position"), "recommended": False, "is_client": False,
                                       "reasons": [], "descriptors": []})
            pos = b.get("position")
            if pos is not None and (seen["position"] is None or pos < seen["position"]):
                seen["position"] = pos
            seen["recommended"] = seen["recommended"] or bool(b.get("recommended"))
            seen["is_client"] = seen["is_client"] or bool(b.get("is_client"))
            if b.get("reason"):
                seen["reasons"].append(b["reason"])
            seen["descriptors"] += [d for d in (b.get("descriptors") or []) if d]
        for k, seen in best.items():
            m = mentions.setdefault(k, {"answers": 0, "recommended": 0, "positions": [], "is_client": False,
                                        "reasons": Counter(), "reason_text": {}, "descriptors": Counter()})
            m["answers"] += 1
            m["recommended"] += int(seen["recommended"])
            m["is_client"] = m["is_client"] or seen["is_client"]
            if seen["position"] is not None:
                m["positions"].append(seen["position"])
            for reason in set(" ".join(r.split()) for r in seen["reasons"]):
                m["reasons"][reason.casefold()] += 1
                m["reason_text"].setdefault(reason.casefold(), reason)
            for d in set(" ".join(x.lower().split()) for x in seen["descriptors"]):
                m["descriptors"][d] += 1

    def display(k: str) -> str:
        ranked = sorted(spellings[k].items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked[0][0]

    rows = []
    for k, m in mentions.items():
        rows.append({"name": display(k), "is_client": m["is_client"], "answers": m["answers"],
                     "recommended": m["recommended"],
                     "average_position": (sum(m["positions"]) / len(m["positions"])) if m["positions"] else None,
                     "_key": k})
    rows.sort(key=lambda r: (-r["answers"], -r["recommended"], r["name"].casefold()))
    for rank, r in enumerate(rows, start=1):
        r["rank"] = rank
    top = rows[:TOP_BUSINESSES]
    client_outside = [r for r in rows[TOP_BUSINESSES:] if r["is_client"]]
    rest = [r for r in rows[TOP_BUSINESSES:] if not r["is_client"]]

    reasons = []
    for r in [x for x in rows[:TOP_REASON_BUSINESSES]] + [x for x in rows[TOP_REASON_BUSINESSES:] if x["is_client"]]:
        m = mentions[r["_key"]]
        quoted = sorted(((m["reason_text"][k], n) for k, n in m["reasons"].items()), key=lambda kv: (-kv[1], kv[0].casefold()))
        reasons.append({"name": r["name"], "is_client": r["is_client"], "answers": r["answers"],
                        "reasons": [{"quote": q, "answers": n} for q, n in quoted[:MAX_REASONS]],
                        "reasons_not_shown": max(0, len(quoted) - MAX_REASONS)})

    client_rows = [r for r in rows if r["is_client"]]
    descriptors: list[tuple[str, int]] = []
    for r in client_rows:
        descriptors += list(mentions[r["_key"]]["descriptors"].items())
    merged: Counter = Counter()
    for d, n in descriptors:
        merged[d] += n
    ranked = sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))

    for r in rows:
        r.pop("_key")
    return {"businesses_named": len(rows), "rows": top, "client_outside_top": client_outside,
            "others": {"businesses": len(rest), "mentions": sum(r["answers"] for r in rest)},
            "reasons": reasons,
            "descriptors": {"words": [{"word": w, "answers": n} for w, n in ranked[:MAX_DESCRIPTORS]],
                            "not_shown": max(0, len(ranked) - MAX_DESCRIPTORS),
                            "client_named_in": sum(r["answers"] for r in client_rows)}}


def _wrong_facts(scored: list[dict], order: Callable[[dict], tuple], facts: list[dict]) -> dict:
    by_label = {f["field"].casefold(): f["value"] for f in facts}
    incorrect, totals = [], Counter()
    for a in sorted(scored, key=order):
        for c in a["claims"] or []:
            totals[c["verdict"]] += 1
            if c["verdict"] == "incorrect":
                field = c.get("field")
                incorrect.append({"claim": c["claim"], "note": c.get("note"), "field": field,
                                  "confirmed_fact": by_label.get((field or "").casefold()),
                                  "engine": a["engine"], "repeat": a["repeat"], "question": a["question"]})
    return {"confirmed_facts": len(facts), "correct": totals["correct"], "incorrect": totals["incorrect"],
            "unverifiable": totals["unverifiable"], "wrong": incorrect}


def _sources(answers: list[dict], client_domain: str) -> dict:
    per_domain: Counter = Counter()
    client_urls: Counter = Counter()
    with_sources = client_answers = 0
    for a in answers:
        links = a["sources"] or []
        if links:
            with_sources += 1
        domains = {l["domain"] for l in links if l.get("domain")}
        per_domain.update(domains)
        mine = {l["url"] for l in links if l.get("domain") and (l["domain"] == client_domain or l["domain"].endswith("." + client_domain))}
        if mine:
            client_answers += 1
            client_urls.update(mine)
    ranked = sorted(per_domain.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"answers": len(answers), "answers_with_sources": with_sources,
            "top": [{"domain": d, "answers": n} for d, n in ranked[:TOP_DOMAINS]],
            "other_domains": max(0, len(ranked) - TOP_DOMAINS),
            "client_domain": client_domain, "client_cited_in": client_answers,
            "client_urls": [{"url": u, "answers": n} for u, n in
                            sorted(client_urls.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_CLIENT_URLS]]}


# ---- putting it together --------------------------------------------------------------------------------------------------

def build_report(conn, business: dict, run: dict, *, partial: bool) -> dict:
    started = store.started_details(conn, run["id"])
    if started is None:
        raise ReportError(f"run {run['id']} has no start record in the journal")
    if run["run_type"] != "visibility":
        raise ReportError(f"this report covers visibility runs; run {run['id']} is a {run['run_type']} run")
    answers = load_answers(conn, run["id"])
    expected = int(started.get("answers_expected") or len(answers))
    complete = run["status"] == "complete"
    if not complete and not partial:
        raise ReportError(
            f"run {run['id']} is not finished (status {run['status']}: {len(answers)} of {expected} answers saved). "
            "Finish it first (`aeo audit import` the rest), or add --partial to make a report that says it is incomplete.")
    if not answers:
        raise ReportError("this run has no answers yet")
    scored = [a for a in answers if a["named"] is not None]
    if not scored:
        raise ReportError("none of this run's answers has been read by the model yet, so there is nothing to score")

    profile = store.load_business_and_profile(conn, business["id"], run["profile_id"])
    facts = profile["facts"] if profile else []

    # Questions in the order they were asked, with the tags they had when the run started.
    planned = started.get("questions")
    if planned:
        questions = [{"number": i, "text": q["text"], "intent": q.get("intent"), "is_target": bool(q.get("is_target"))}
                     for i, q in enumerate(planned, start=1)]
        tags_recorded = True
    else:
        texts = list(dict.fromkeys(a["question"] for a in answers))
        questions = [{"number": i, "text": t, "intent": None, "is_target": False} for i, t in enumerate(texts, start=1)]
        tags_recorded = False
    known = {q["text"] for q in questions}
    for t in dict.fromkeys(a["question"] for a in answers):
        if t not in known:
            questions.append({"number": len(questions) + 1, "text": t, "intent": None, "is_target": False})
    number = {q["text"]: q["number"] for q in questions}
    target = {q["text"]: q["is_target"] for q in questions}

    # Discovery or brand, short/medium/long, language. A run started before step 22d did not record these; they are
    # worked out now and the report says so.
    types = question_types(planned, [q["text"] for q in questions], business)
    for q in questions:
        q.update(types[q["text"]])
    discovery = [a for a in scored if types[a["question"]]["type"] == "discovery"]
    brand = [a for a in scored if types[a["question"]]["type"] == "brand"]
    if not discovery:
        raise ReportError(
            "none of this run's questions is a discovery question (one that does not name the client), so there is "
            "nothing to measure visibility on. Brand questions measure recognition and accuracy only (PLAN.md section 8).")
    derived_types = sum(1 for q in questions if q["type_source"] != "tag")

    engines = list(started.get("engines") or [])
    engines += [e for e in dict.fromkeys(a["engine"] for a in answers) if e not in engines]
    repeats = int(started.get("repeats") or 1)
    label = design_label(len(questions), len(engines), repeats)
    is_before_after = label == BEFORE_AFTER_LABEL

    order = lambda a: (number[a["question"]], engines.index(a["engine"]), a["repeat"], a["id"])
    with_accuracy = bool(facts)

    data: dict = {
        "tool": {"name": "aeo audit report", "formula_version": scoring.formula_version(),
                 "bootstrap": dict(scoring._config()["bootstrap"])},
        "design": {"label": label, "questions": len(questions), "engines": engines, "repeats": repeats,
                   "before_after_design": BEFORE_AFTER},
        "incomplete": None if complete else {"status": run["status"], "answers_saved": len(answers), "answers_expected": expected},
        "header": {
            "business": {"id": business["id"], "name": business["name"], "domain": business["domain"]},
            "checkpoint": run["checkpoint"], "run_id": run["id"], "run_status": run["status"],
            "question_set_version": started.get("question_set_version"),
            "engines": [{"engine": e, "label": engine_label(e),
                         "modes": sorted({a["mode"] for a in answers if a["engine"] == e and a["mode"]})} for e in engines],
            "repeats": repeats, "answers_saved": len(answers), "answers_expected": expected,
            "started_at": run["started_at"].isoformat() if run["started_at"] else None,
            "completed_at": run["completed_at"].isoformat() if run["completed_at"] else None,
            "first_answer_on": min(a["created_at"] for a in answers).date().isoformat(),
            "last_answer_on": max(a["created_at"] for a in answers).date().isoformat(),
            "operators": operators_for(conn, run["id"], started),
            "logged_in_states": sorted({a["logged_in"] for a in answers if a["logged_in"]}),
            "locations": sorted({a["location"] for a in answers if a["location"]}),
            "profile_version": profile["profile_version"] if profile else None,
            "confirmed_facts": len(facts),
        },
        "extraction": {"answers": len(answers), "scored": len(scored),
                       "failed": [{"question_number": number[a["question"]], "question": a["question"],
                                   "engine": a["engine"], "repeat": a["repeat"]}
                                  for a in sorted((a for a in answers if a["named"] is None), key=order)]},
        "tags_recorded": tags_recorded,
        # Visibility, recommendation, gap and the rest are measured on discovery questions only (PLAN.md section 8).
        "question_mix": {"discovery_questions": sum(1 for q in questions if q["type"] == "discovery"),
                         "brand_questions": sum(1 for q in questions if q["type"] == "brand"),
                         "discovery_answers": len(discovery), "brand_answers": len(brand),
                         "types_derived_from_text": derived_types},
        "headline": _headline_block(discovery, scored, brand, with_accuracy=with_accuracy),
        "brand": _brand_section(brand),
    }

    if is_before_after:
        data["by_engine"] = [{"engine": e, "label": engine_label(e),
                              **_block([a for a in discovery if a["engine"] == e], with_accuracy=False)}
                             for e in engines if any(a["engine"] == e for a in discovery)]
        data["by_length"] = _by_group(discovery, lambda a: types[a["question"]]["length"],
                                      ["short", "medium", "long"])
        data["by_language"] = _by_group(discovery, lambda a: types[a["question"]]["language"], None)
        targets = [a for a in discovery if target[a["question"]]]
        control = [a for a in discovery if not target[a["question"]]]
        if targets and control:
            data["targets_vs_control"] = {"available": True, "reason": None,
                                          "targets": _block(targets, with_accuracy=False),
                                          "control": _block(control, with_accuracy=False)}
        else:
            why = ("no question in this run is marked as a target" if not targets
                   else "every question is marked as a target, so there is no control")
            if not tags_recorded:
                why = "this run did not record which questions are targets"
            data["targets_vs_control"] = {"available": False, "reason": why}
    else:
        data["by_engine"] = None
        data["by_length"] = None
        data["by_language"] = None
        data["targets_vs_control"] = None

    # Share of voice and reasons: discovery only, or the client would look present in every brand answer.
    # Descriptors use brand questions only: that is where AI is asked what the client is.
    sov = _share_of_voice(discovery)
    data["share_of_voice"] = {k: sov[k] for k in ("businesses_named", "rows", "client_outside_top", "others")}
    data["reasons"] = sov["reasons"]
    data["descriptors"] = _share_of_voice(brand)["descriptors"]
    data["wrong_facts"] = _wrong_facts(scored, order, facts)
    data["sources"] = _sources([a for a in answers if types[a["question"]]["type"] == "discovery"], business["domain"])
    data["appendix"] = [
        {"number": q["number"], "question": q["text"], "is_target": q["is_target"], "type": q["type"],
         "type_source": q["type_source"], "length": q["length"], "language": q["language"], "answers": [
            {"engine": a["engine"], "label": engine_label(a["engine"]), "repeat": a["repeat"], "mode": a["mode"],
             "searched": a["searched"], "logged_in": a["logged_in"], "location": a["location"],
             "links": [l["url"] for l in (a["sources"] or [])], "read_by_model": a["named"] is not None,
             "text": a["text"]}
            for a in sorted((a for a in answers if a["question"] == q["text"]), key=order)]}
        for q in questions]
    return data
