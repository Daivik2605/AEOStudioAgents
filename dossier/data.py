"""Everything the dossier says, as one JSON-safe dictionary (PLAN.md section 10, "The baseline dossier"; step 22e).

No AI. Built from the database, the client's files and the notes file. Every section starts with ONE sentence that is
worked out from the data, then the detail, then the person's own notes word for word.

Section shape: {key, title, answer, data, blocks, notes}
  data    the numbers and lists the sentence and blocks were made from (tests read this)
  blocks  what is printed: {"t": "p"|"h"|"warn", "text"} · {"t": "ul", "items"} · {"t": "table", "headers", "rows"}
"""

from __future__ import annotations

import json
import re
from html import unescape
from collections import Counter
from datetime import date, timezone
from pathlib import Path

from audit.report_data import design_label, engine_label, load_answers
from compare.diagnosis import compare_diagnoses
from core import client_files
from core.businesses import get_engagement
from core.checkpoints import CHECKPOINTS
from core.evidence import list_evidence
from core.profiles import CONFIRMED, latest_profile
from core.timeline import collect_work
from diagnose.crawler import BOTS
from diagnose.html_utils import parse_page
from diagnose.structured_data import ORG_TYPES, _types, _walk
from dossier import notes as notes_mod
from dossier import questions as q

WEB_DEV = ("web_dev_aeo", "web_dev_only")
PAID = ("client", "delivered")
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# The access items we ask a client for, and the evidence type that shows each one arrived.
ACCESS_ITEMS = [("search_console", "Google Search Console"), ("analytics", "Analytics (current site)"),
                ("analytics_old", "Analytics (old site)"), ("gbp", "Google Business Profile")]
# The fixed list of AI visibility signals: where each comes from, what it needs, and the evidence type that shows we have it.
SIGNALS = [
    ("Copilot citations", "bing_wmt", "Bing Webmaster Tools, AI Performance report", "the site verified in Bing Webmaster Tools (DNS, meta tag or Search Console import)"),
    ("Visits from AI assistants", "analytics", "Analytics referrals (chatgpt.com, perplexity.ai, copilot, gemini)", "the client granting access to Analytics, or an export"),
    ("AI crawler visits", "cdn_report", "Cloudflare AI Crawl Control, or the host's logs", "hosting or CDN access, or a screenshot from the client"),
]
# The questions we always ask a client when building the question set.
STANDARD_ASKS = [
    "Who are your priority customers (who do you most want to be found by)?",
    "Which regions do you serve, including places outside your home city?",
    "Who do you see as your competitors?",
    "What do real customers actually ask you before they buy? (emails, calls, forms: the exact words)",
]


class DossierError(Exception):
    """The dossier cannot be made. The message says what to do."""


def _day(dt) -> str:
    return dt.astimezone(timezone.utc).date().isoformat()


def p(text: str) -> dict:
    return {"t": "p", "text": text}


def h(text: str) -> dict:
    return {"t": "h", "text": text}


def warn(text: str) -> dict:
    return {"t": "warn", "text": text}


def ul(items: list[str]) -> dict:
    return {"t": "ul", "items": items}


def table(headers: list[str], rows: list[list[str]]) -> dict:
    return {"t": "table", "headers": headers, "rows": rows}


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


# ---- loading --------------------------------------------------------------------------------------------------------------

def load(conn, business: dict) -> dict:
    bid = business["id"]
    with conn.cursor() as cur:
        cur.execute("SELECT name, domain, website_url, status FROM businesses WHERE id = %s", (bid,))
        name, domain, url, status = cur.fetchone()
        cur.execute("SELECT id, checkpoint, checked_at, platform, platform_plan, verdict, findings, render, crawler_access, "
                    "existing_jsonld, url FROM diagnoses WHERE business_id = %s ORDER BY checked_at, created_at, id", (bid,))
        diagnoses = [{"id": str(r[0]), "checkpoint": r[1], "checked_at": r[2], "platform": r[3], "plan": r[4], "verdict": r[5],
                      "findings": r[6] or [], "render": r[7], "crawler": r[8] or {}, "jsonld": r[9] or [], "url": r[10]}
                     for r in cur.fetchall()]
        cur.execute("SELECT id, checkpoint, run_type, status, COALESCE(completed_at, created_at) FROM probe_runs "
                    "WHERE business_id = %s ORDER BY COALESCE(completed_at, created_at), id", (bid,))
        runs = [{"id": str(r[0]), "checkpoint": r[1], "run_type": r[2], "status": r[3], "when": r[4]} for r in cur.fetchall()]
        cur.execute("SELECT details->>'question_set_version' FROM client_journal WHERE business_id = %s "
                    "AND entry_type = 'probe_started' AND details ? 'question_set_version' ORDER BY created_at, id", (bid,))
        used_versions = [r[0] for r in cur.fetchall() if r[0]]
        cur.execute("SELECT count(*) FROM recommendations WHERE business_id = %s", (bid,))
        recommendations = cur.fetchone()[0]
        cur.execute("SELECT alternate_names FROM business_profiles WHERE business_id = %s ORDER BY version DESC LIMIT 1", (bid,))
        row = cur.fetchone()
        alternates = list(row[0] or []) if row else []
    return {"name": name, "domain": domain, "url": url, "status": status, "diagnoses": diagnoses, "runs": runs,
            "used_versions": used_versions, "recommendations": recommendations, "alternates": alternates,
            "evidence": list_evidence(conn, bid), "profile": latest_profile(conn, bid),
            "work": collect_work(conn, bid), "engagement": get_engagement(conn, bid)}


def latest_per_checkpoint(diagnoses: list[dict]) -> list[dict]:
    """The newest diagnosis of each checkpoint label, in label order; the newest unlabelled one last."""
    out = []
    for label in (*CHECKPOINTS, None):
        mine = [d for d in diagnoses if d["checkpoint"] == label]
        if mine:
            out.append(mine[-1])
    return out


def evidence_of(ctx: dict, kind: str, checkpoint: str | None = None) -> list[dict]:
    return [e for e in ctx["evidence"] if e["evidence_type"] == kind and (checkpoint is None or e["checkpoint"] == checkpoint)]


def latest_date(rows: list[dict]) -> str | None:
    days = [(e["captured_on"] or e["created_at"].astimezone(timezone.utc).date()).isoformat() for e in rows]
    return max(days) if days else None


def access_status(ctx: dict) -> list[dict]:
    """Each access item: received or not, from the evidence types. 'Old-site analytics' only applies to a client whose
    site is being replaced (a web-development engagement); for them the 'before' checkpoint is the old site."""
    web_dev = ctx["engagement"]["engagement_type"] in WEB_DEV
    out = []
    for key, label in ACCESS_ITEMS:
        if key == "analytics_old":
            if not web_dev:
                out.append({"key": key, "label": label, "applies": False, "received": False, "latest": None})
                continue
            rows = evidence_of(ctx, "analytics", "before")
        elif key == "analytics" and web_dev:
            rows = [e for e in evidence_of(ctx, "analytics") if e["checkpoint"] != "before"]
        else:
            rows = evidence_of(ctx, key)
        out.append({"key": key, "label": label, "applies": True, "received": bool(rows), "latest": latest_date(rows)})
    return out


# ---- the latest saved page ---------------------------------------------------------------------------------------------------

def latest_raw_page(domain: str, root: Path | None) -> dict | None:
    folder = client_files.evidence_dir(domain, root)
    found = []
    for f in folder.glob("*_raw_page_run*.html") if folder.is_dir() else []:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})_raw_page_run(\d+)", f.name)
        if m:
            found.append(((m.group(1), int(m.group(2))), f))
    if not found:
        return None
    _, path = max(found, key=lambda x: x[0])
    html = path.read_text(encoding="utf-8", errors="replace")
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    h1s = [t for t in parse_page(html).h1_texts if t]
    clean = lambda text: " ".join(unescape(text).split())        # noqa: E731 - O&#x27;land -> O'land
    return {"file": path.name, "title": clean(title.group(1)) if title else None, "main_heading": clean(h1s[0]) if h1s else None}


# ---- structured data, read from the raw blocks a diagnosis stored -----------------------------------------------------------------

def jsonld_facts(raw_blocks: list[str]) -> dict:
    blocks, names, area, hours = [], [], False, "missing"
    nodes = []
    for raw in raw_blocks:
        try:
            data = json.loads(raw)
        except ValueError:
            blocks.append({"valid": False, "types": []})
            continue
        found = list(_walk(data))
        blocks.append({"valid": True, "types": sorted({t for n in found for t in _types(n)})})
        nodes += found
    for n in nodes:
        if set(_types(n)) & ORG_TYPES and not set(n) <= {"@id", "@type"}:
            if isinstance(n.get("name"), str) and n["name"].strip():
                names.append(n["name"].strip())
            area = area or bool(n.get("areaServed"))
    for n in nodes:
        for key in ("openingHours", "openingHoursSpecification"):
            if key in n:
                v = n[key]
                items = v if isinstance(v, list) else [v]
                real = any((isinstance(i, str) and re.sub(r"[\s,]", "", i)) or isinstance(i, dict) for i in items)
                if real:
                    hours = "present"
                elif hours != "present":
                    hours = "empty"
    return {"blocks": blocks, "names": list(dict.fromkeys(names)), "area_served": area, "opening_hours": hours}


def one_line(text) -> str:
    return " ".join(str(text).split())


COLUMN_NAMES = {"before": ("Old site", "Before our work"), "after_site": ("New site", "New site"),
                "after_aeo": ("After AI work", "After AI work"), "check": ("Check", "Check"), "no label": ("No label", "No label")}


def column_name(label: str, date: str, web_dev: bool) -> str:
    """'Old site · before · 2026-10-05': what it is called for this client, the checkpoint label, and the date."""
    web, other = COLUMN_NAMES.get(label, (label, label))
    return f"{web if web_dev else other} · {label} · {date}"


def diagnosis_column(d: dict) -> dict:
    codes = {f["code"] for f in d["findings"]}
    bots = (d["crawler"] or {}).get("bots") or {}
    rows: dict[str, str] = {}
    for bot in BOTS:
        label = f"{bot.name} ({bot.kind} bot)"
        if not d["crawler"]:
            rows[label] = "not tested"
            continue
        reasons = []
        if f"CRAWLER_BLOCKED_{bot.code_suffix}" in codes:
            reasons.append(f"blocked: {bots.get(bot.name, {}).get('verdict', 'not given the page')}")
        if f"ROBOTS_DISALLOWS_{bot.code_suffix}" in codes:
            reasons.append("robots.txt disallows it")
        rows[label] = "; ".join(reasons) if reasons else "allowed"
    r = d["render"]
    rows["Readable words (no JavaScript)"] = str(r["word_count"]) if r else "not tested"
    rows["Render test"] = r["verdict"].replace("_", " ") if r else "not tested"
    if r:
        empty = sum(1 for t in r["h1_texts"] if not t)
        rows["Main headings (h1)"] = str(len(r["h1_texts"])) + (f" ({empty} empty)" if empty else "")
    else:
        rows["Main headings (h1)"] = "not tested"
    j = jsonld_facts(d["jsonld"])
    rows["Structured data blocks"] = (", ".join(f"{'/'.join(b['types']) or 'no type'}" + ("" if b["valid"] else " (invalid)") for b in j["blocks"])
                                      if j["blocks"] else "none found")
    rows["Name in the structured data"] = "; ".join(j["names"]) if j["names"] else ("no name found" if j["blocks"] else "no structured data")
    rows["Areas served"] = ("given" if j["area_served"] else "not given") if j["blocks"] else "no structured data"
    rows["Opening hours"] = j["opening_hours"] if j["blocks"] else "no structured data"
    rows["llms.txt"] = "missing" if "LLMS_TXT_MISSING" in codes else "present (no finding raised)"
    rows["Sitemap and host consistency"] = ("see findings" if any(c.startswith(("SITEMAP_", "HOST_CONSISTENCY")) for c in codes)
                                           else "not checked yet")
    return rows


# ---- the sections ---------------------------------------------------------------------------------------------------------------

def counts_of(ctx: dict) -> dict:
    facts = (ctx["profile"] or {}).get("facts") or []
    return {"diagnoses": len(ctx["diagnoses"]), "evidence": len(ctx["evidence"]),
            "facts_total": len(facts), "facts_confirmed": sum(1 for f in facts if f.get("status") == CONFIRMED),
            "audits": len(ctx["runs"]), "work": len(ctx["work"])}


def sec_how_to_use(ctx: dict, version: int, today: date, previous: dict | None) -> dict:
    now = counts_of(ctx)
    changes = []
    if previous:
        before = previous.get("counts") or {}
        labels = {"diagnoses": "diagnosis", "evidence": "evidence file", "facts_confirmed": "confirmed fact", "audits": "audit run", "work": "work item"}
        for key, word in labels.items():
            diff = now[key] - before.get(key, 0)
            if diff > 0:
                changes.append(f"{plural(diff, 'new ' + word, 'new ' + word + 's')}")
    if previous is None:
        answer = f"This is version {version}, the first dossier for {ctx['name'] or ctx['domain']}, made on {today.isoformat()}."
    elif changes:
        answer = f"Version {version} ({today.isoformat()}) has {len(changes)} kind(s) of change since version {version - 1}: " + "; ".join(changes) + "."
    else:
        answer = f"Version {version} ({today.isoformat()}) has nothing new since version {version - 1}."
    blocks = [p("This is an INTERNAL document. It is rebuilt from the database, the client's files and dossier_notes.md each time "
                "`aeo dossier` runs: run it again after any new evidence, diagnosis, audit or fact. Write your own comments in "
                "dossier_notes.md under the matching heading; never in this file."),
              h("What is new since the last version")]
    blocks.append(ul(changes) if changes else p("First version." if previous is None else "Nothing new."))
    return {"data": {"version": version, "date": today.isoformat(), "new_since_last": changes, "counts": now,
                     "previous_version": version - 1 if previous else None},
            "answer": answer, "blocks": blocks}


def sec_snapshot(ctx: dict, page: dict | None) -> dict:
    facts = (ctx["profile"] or {}).get("facts") or []
    confirmed = [f for f in facts if f.get("status") == CONFIRMED]
    if not facts:      # `aeo add` makes an empty first profile; no facts means no truth document has been imported
        answer = f"No truth document has been imported for {ctx['name'] or ctx['domain']}, so there are no confirmed facts yet."
    else:
        answer = f"{len(confirmed)} of {len(facts)} facts are confirmed (profile version {ctx['profile']['version']})."
    blocks = []
    if facts:
        blocks.append(table(["Fact", "Value", "Status", "Confirmed by", "Confirmed on"],
                            [[f["label"], str(f.get("value") or ""), f.get("status") or "", f.get("confirmed_by") or "", str(f.get("confirmed_on") or "")]
                             for f in facts]))
    else:
        blocks.append(p("Not collected yet: no facts in a truth document."))
    blocks.append(h("The latest saved page"))
    if page:
        blocks.append(table(["Item", "Value"], [["Page title", page["title"] or "(none)"], ["Main heading", page["main_heading"] or "(none)"],
                                      ["From file", page["file"]]]))
    else:
        blocks.append(p("Not collected yet: no saved page. Run `aeo diagnose` with --business."))
    return {"data": {"facts": facts, "confirmed": len(confirmed), "total": len(facts), "page": page,
                     "profile_version": (ctx["profile"] or {}).get("version")}, "answer": answer, "blocks": blocks}


def spot_check_run(ctx: dict, conn) -> dict | None:
    """The latest FINISHED run that is a snapshot (not the 32 x 2 x 3 before/after design)."""
    for run in reversed(ctx["runs"]):
        if run["status"] != "complete":
            continue
        answers = load_answers(conn, run["id"])
        if not answers:
            continue
        design = design_label(len({a["question"] for a in answers}), len({a["engine"] for a in answers}), max(a["repeat"] for a in answers))
        if design == "Snapshot":
            return {**run, "answers": answers}
    return None


def question_set_state(ctx: dict, proposed: dict | None) -> dict:
    """Draft or frozen. A version is frozen once an audit used it (PLAN.md section 8: a used version is never edited)."""
    if proposed is None:
        return {"state": "none", "version": None}
    return {"state": "frozen" if proposed["version"] in ctx["used_versions"] else "draft", "version": proposed["version"]}


def site_replaced(ctx: dict) -> bool:
    """The new site is live: there is a diagnosis of it (after_site), or the platform changed between the first and the latest."""
    diagnoses = ctx["diagnoses"]
    if any(d["checkpoint"] == "after_site" for d in diagnoses):
        return True
    platforms = [d["platform"] for d in diagnoses if d["platform"]]
    return len(set(platforms)) > 1


def sec_stage(ctx: dict, conn, qstate: dict, spot: dict | None) -> dict:
    eng = ctx["engagement"]
    web_dev = eng["engagement_type"] in WEB_DEV
    per_label = Counter(d["checkpoint"] or "no label" for d in ctx["diagnoses"])
    facts = counts_of(ctx)
    rows: list[list[str]] = []     # item, status, detail
    rows.append(["Diagnoses", "done" if ctx["diagnoses"] else "not yet",
                 ", ".join(f"{k}: {v}" for k, v in per_label.items()) or "none"])
    moves = evidence_of(ctx, "site_move")
    if moves:
        move_detail = f"{len(moves)} file(s), latest {latest_date(moves)}"
    elif web_dev and site_replaced(ctx):
        move_detail = "site replaced; move not finished (no site_move evidence)"
    elif web_dev:
        move_detail = "the site is being replaced; nothing logged"
    else:
        move_detail = f"engagement: {eng['engagement_type'] or 'not set'}"
    rows.append(["Site-move evidence logged", ("done" if moves else "not yet") if web_dev else "not applicable", move_detail])
    rows.append(["Spot check run", "done" if spot else "not yet", f"{_day(spot['when'])} ({spot['checkpoint'] or 'no label'})" if spot else "no finished snapshot run"])
    rows.append(["Facts confirmed", "done" if facts["facts_total"] and facts["facts_confirmed"] == facts["facts_total"] else "not yet",
                 f"{facts['facts_confirmed']} of {facts['facts_total']}" if facts["facts_total"] else "no truth document yet"])
    rows.append(["Question set", "done" if qstate["state"] == "frozen" else "not yet",
                 f"{qstate['version']}: {qstate['state']}" if qstate["version"] else "none yet"])
    access = access_status(ctx)
    for a in access:
        if not a["applies"]:
            rows.append([f"Access: {a['label']}", "not applicable", "the site is not being replaced"])
        else:
            rows.append([f"Access: {a['label']}", "done" if a["received"] else "not yet", f"latest {a['latest']}" if a["received"] else "no evidence"])
    audit_rows = [[f"Audit: {r['checkpoint'] or 'no label'}", "done" if r["status"] == "complete" else "not yet",
                   f"{r['run_type']} run, {r['status']}, {_day(r['when'])}"] for r in ctx["runs"]]
    rows += audit_rows or [["Audits", "not yet", "none"]]
    rows.append(["Work items logged", "done" if ctx["work"] else "not yet", str(len(ctx["work"]))])
    applicable = [r for r in rows if r[1] != "not applicable"]
    done = sum(1 for r in applicable if r[1] == "done")
    answer = (f"Engagement: {eng['engagement_type'] or 'not set'}; access: {eng['access_level'] or 'not set'}. "
              f"{done} of {len(applicable)} stage items are done.")
    return {"data": {"engagement": eng, "items": [{"item": r[0], "status": r[1], "detail": r[2]} for r in rows],
                     "done": done, "applicable": len(applicable), "access": access},
            "answer": answer, "blocks": [table(["Item", "Status", "Detail"], rows)]}


def sec_how_ai_sees(ctx: dict) -> dict:
    cols = latest_per_checkpoint(ctx["diagnoses"])
    if not cols:
        return {"data": {"columns": []}, "answer": "No diagnosis yet: nothing is known about how AI sees the site.",
                "blocks": [p("Not collected yet. Run `aeo diagnose <url> --business <b> --checkpoint before`.")]}
    web_dev = ctx["engagement"]["engagement_type"] in WEB_DEV
    views = [{"label": d["checkpoint"] or "no label", "date": _day(d["checked_at"]), "platform": d["platform"], "verdict": d["verdict"],
              "findings": len(d["findings"]), "rows": diagnosis_column(d)} for d in cols]
    for v in views:
        v["name"] = column_name(v["label"], v["date"], web_dev)
    row_names = list(views[0]["rows"])
    head = ["What we looked at", *[v["name"] for v in views]]
    tbl = [["Platform / verdict", *[f"{v['platform'] or 'not recognised'} / {v['verdict']}" for v in views]]]
    tbl += [[name, *[v["rows"][name] for v in views]] for name in row_names]
    steps = []
    for earlier, later in zip(cols, cols[1:]):
        d = compare_diagnoses(earlier, later, [], _day(later["checked_at"]))
        # The plain sentence first, then the code in brackets.
        def say(c):
            return f"{one_line(c['what'])} [{c['code']}]"
        lines = [f"Fixed: {say(c)}" for c in d["fixed"]]
        lines += [f"No longer applies: {say(c)}; replaced by: {'; '.join(say(r) for r in c['replaced_by'])}. Not fixed, still to be redone"
                  for c in d["no_longer_applies"]]
        lines += [f"Still open: {say(c)}" for c in d["still_open"]] + [f"New: {say(c)}" for c in d["new"]]
        steps.append({"from": earlier["checkpoint"], "to": later["checkpoint"],
                      "title": f"{earlier['checkpoint'] or 'no label'} → {later['checkpoint'] or 'no label'}: {d['headline']}.",
                      "headline": d["headline"], "lines": lines,
                      "counts": {k: d[k] for k in ("fixed_count", "no_longer_applies_count", "still_open_count", "new_count")}})
    latest = views[-1]
    answer = (f"{plural(len(views), 'diagnosis column')}; the latest ({latest['label']}, {latest['date']}) has verdict {latest['verdict']} "
              f"and {plural(latest['findings'], 'finding')}.")
    blocks = [table(head, tbl), h("What happened to the findings between checkpoints")]
    if not steps:
        blocks.append(p("Only one checkpoint so far: nothing to compare."))
    for s in steps:
        blocks += [p(s["title"]), ul(s["lines"])] if s["lines"] else [p(s["title"])]
    return {"data": {"columns": views, "steps": steps}, "answer": answer, "blocks": blocks}


def sec_crawler(ctx: dict) -> dict:
    reports = evidence_of(ctx, "cdn_report")
    rows = [[(e["captured_on"] or e["created_at"].astimezone(timezone.utc).date()).isoformat(),
             f"{e['period_start']} to {e['period_end']}" if e["period_start"] else "not stated", e["checkpoint"],
             e["note"] or "", "yes" if e["client_visible"] else "internal"] for e in reports]
    answer = (f"{plural(len(reports), 'AI crawler report')} on file, the latest from {latest_date(reports)}." if reports
              else "No AI crawler report yet (Cloudflare AI Crawl Control or the host's logs).")
    blocks = [table(["Date", "Period covered", "Checkpoint", "What we understood from it", "Client can see"], rows)] if rows else \
        [p("Not collected yet. Needs hosting access, or a screenshot from the client.")]
    blocks.append(p("Numbers inside PDF reports are not read by the tool. Write what they say in the note, or under \"Our notes\"."))
    return {"data": {"reports": [{"date": r[0], "period": r[1], "checkpoint": r[2], "note": r[3]} for r in rows]},
            "answer": answer, "blocks": blocks}


def sec_ai_says(ctx: dict, conn, spot: dict | None, proposed: dict | None, terms: list[str], question_dir: Path | None,
                spot_set: dict | None) -> dict:
    wanted = {i["text"] for i in spot_set["items"]} if spot_set else None        # None: no spot-check set was named
    if spot is None:
        source = spot_set["items"] if spot_set else (proposed["items"] if proposed else [])
        listed = [i["text"] for i in source]
        other = [r for r in ctx["runs"] if r["status"] == "complete"]
        extra = " A full before/after measurement exists, but it is not a spot check." if other else ""
        blocks = [p("Not run yet." + extra), h("Questions that would be asked")]
        blocks.append(ul(listed) if listed else p("No question set yet."))
        return {"data": {"run": None, "questions_to_ask": listed, "spot_set": spot_set and spot_set["source"]},
                "answer": "The spot check has not been run yet." + extra, "blocks": blocks}
    answers = spot["answers"]
    if wanted is not None:        # only the spot-check questions are listed and counted; the run may hold more
        answers = [a for a in answers if a["question"] in wanted]
    version = next((a.get("question_set_version") for a in answers if a.get("question_set_version")), None)
    tags: dict[str, str | None] = {}
    qfile = q.find_question_file(version, question_dir) if version else None
    if qfile:
        tags = {i["text"]: i["type"] for i in q.read_questions(qfile)["items"]}
    kind: dict[str, tuple[str, str]] = {t: q.classify(t, tags.get(t), terms) for t in {a["question"] for a in answers}}
    rows, per_pair = [], {}
    for a in answers:
        per_pair.setdefault((a["question"], a["engine"]), []).append(a)
    unread = 0
    for (question, engine), group in sorted(per_pair.items(), key=lambda kv: (min(a["created_at"] for a in kv[1]), kv[0])):
        read = [a for a in group if a["named"] is not None]
        unread += len(group) - len(read)
        named = sum(1 for a in read if any(b.get("is_client") for b in a["named"]))
        wrong = sum(1 for a in group for c in (a["claims"] or []) if c.get("verdict") == "incorrect")
        others = sorted({b["name"] for a in read for b in a["named"] if not b.get("is_client")})
        rows.append({"question": question, "engine": engine_label(engine), "type": kind[question][0], "answers": len(group), "read": len(read),
                     "client_named": named, "wrong_facts": wrong, "others": len(others), "other_names": others})
    def cell(r):
        if r["read"] == 0:
            return "not read"
        if r["read"] == 1:
            return "yes" if r["client_named"] else "no"
        return f"{r['client_named']} of {r['read']}"
    tbl = [[r["question"], r["engine"], r["type"], cell(r), str(r["wrong_facts"]),
            f"{r['others']}" + (f": {', '.join(r['other_names'][:4])}" + ("…" if r["others"] > 4 else "") if r["others"] else "")] for r in rows]

    def tally(which: str) -> dict:
        mine = [a for a in answers if kind[a["question"]][0] == which and a["named"] is not None]
        return {"answers": len(mine), "client_named": sum(1 for a in mine if any(b.get("is_client") for b in a["named"]))}
    brand, disc = tally("brand"), tally("discovery")
    rivals: Counter = Counter()
    for a in answers:
        if a["named"] is not None:
            rivals.update({b["name"] for b in a["named"] if not b.get("is_client")})
    top = rivals.most_common(5)
    answer = (f"Snapshot of {_day(spot['when'])}: the client was named in {brand['client_named']} of {brand['answers']} brand answers "
              f"and {disc['client_named']} of {disc['answers']} discovery answers.")
    note = (f"Only the {len(wanted)} spot-check question(s) from {spot_set['source']} are listed." if wanted is not None
            else "No spot-check set was named (use --spot-check <file>, or mark questions spot_check: true), so every question in the run is listed.")
    blocks = [p(note), warn("This is a SNAPSHOT, not a measurement: no range, and it says nothing reliable about any single question. "
                   "The per-question rows are for our own reading only and never go into a client report."),
              table(["Question", "Engine", "Type", "Client named", "Wrong facts", "Other businesses named"], tbl),
              ul([f"Brand answers that knew the client: {brand['client_named']} of {brand['answers']}.",
                  f"Discovery answers that named the client: {disc['client_named']} of {disc['answers']}.",
                  "Most-named competitors: " + (", ".join(f"{n} ({c})" for n, c in top) if top else "none") + "."]
                 + ([f"{unread} answer(s) could not be read by the extraction and are left out."] if unread else []))]
    return {"data": {"run": {"id": spot["id"], "checkpoint": spot["checkpoint"], "date": _day(spot["when"]), "question_set_version": version},
                     "rows": rows, "brand": brand, "discovery": disc, "competitors": [{"name": n, "answers": c} for n, c in top],
                     "unread": unread, "types_from_tags": bool(tags), "spot_set": spot_set and spot_set["source"]}, "answer": answer, "blocks": blocks}


def finding_kind(code: str) -> str:
    if code.startswith(("CRAWLER_", "ROBOTS_", "CONTENT_SIGNAL")):
        return "access"
    if code.startswith(("SCHEMA_", "LLMS_", "AUDIT_")):
        return "AEO"
    return "site"


def sec_problems(ctx: dict, conn) -> dict:
    items: list[dict] = []
    if ctx["diagnoses"]:
        latest = ctx["diagnoses"][-1]
        for f in latest["findings"]:
            items.append({"kind": finding_kind(f["code"]), "severity": f["severity"], "text": f["what"], "source": f"diagnosis {_day(latest['checked_at'])}: {f['code']}", "order": 0})
    seen = set()
    for run in ctx["runs"]:
        if run["status"] != "complete":
            continue
        for a in load_answers(conn, run["id"]):
            for c in a["claims"] or []:
                key = ((c.get("field") or "").casefold(), " ".join(c["claim"].lower().split())) if c.get("verdict") == "incorrect" else None
                if key and key not in seen:
                    seen.add(key)
                    items.append({"kind": "AEO", "severity": "high", "text": f"AI says: \"{c['claim']}\"" + (f" (about: {c['field']})" if c.get("field") else ""),
                                  "source": f"audit {run['checkpoint'] or 'no label'}, {_day(run['when'])}: wrong fact", "order": 1})
    facts = (ctx["profile"] or {}).get("facts") or []
    for f in facts:
        if f.get("status") != CONFIRMED:
            items.append({"kind": "facts", "severity": "medium", "text": f"Fact not confirmed: {f['label']}", "source": "truth document", "order": 2})
    if ctx["engagement"]["engagement_type"] in WEB_DEV and not evidence_of(ctx, "site_move"):
        items.append({"kind": "site", "severity": "medium", "text": "Finish the site move: no site_move evidence is logged"
                      + (" (the site is already replaced)" if site_replaced(ctx) else ""), "source": "stage", "order": 2})
    for a in access_status(ctx):
        if a["applies"] and not a["received"]:
            items.append({"kind": "off-site" if a["key"] == "gbp" else "access", "severity": "medium", "text": f"Not collected yet: {a['label']}",
                          "source": "evidence catalogue", "order": 3})
    for label, key, source, _ in SIGNALS:
        # (Analytics is already listed above as an access item, so it is not repeated here.)
        if key != "analytics" and not evidence_of(ctx, key):
            items.append({"kind": "access", "severity": "low", "text": f"Not collected yet: {label} ({source})", "source": "evidence catalogue", "order": 3})
    items.sort(key=lambda i: (SEVERITY_RANK.get(i["severity"], 9), i["order"]))
    for n, i in enumerate(items, start=1):
        i["rank"] = n
    by_kind = Counter(i["kind"] for i in items)
    answer = (f"{plural(len(items), 'problem')} found" + (": " + ", ".join(f"{n} {k}" for k, n in sorted(by_kind.items())) if items else "") + ".") if items \
        else "No problems found yet (this may only mean little has been collected)."
    blocks = [table(["#", "Importance", "Kind", "Problem", "Where it comes from"], [[str(i["rank"]), i["severity"], i["kind"], i["text"], i["source"]] for i in items])] \
        if items else [p("Nothing to rank yet.")]
    return {"data": {"items": items, "by_kind": dict(by_kind)}, "answer": answer, "blocks": blocks}


def sec_questions(ctx: dict, proposed: dict | None, qstate: dict, terms: list[str]) -> dict:
    if proposed is None:
        return {"data": {"set": None}, "answer": "There is no question set yet: give one with --questions, or run an audit with one.",
                "blocks": [p("Not collected yet.")]}
    items = []
    for i in proposed["items"]:
        qtype, how = q.classify(i["text"], i["type"], terms)
        found = q.contains_brand(i["text"], terms)
        items.append({**i, "effective_type": qtype, "type_from": how, "brand_found": found})
    controls = [i for i in items if i["control"]]
    warnings = []
    for i in items:
        # A discovery question must not name the brand. An untagged question that names it is flagged too: it is either a
        # brand question that needs its tag, or a discovery question that needs the brand taken out.
        if i["brand_found"] and i["type"] != "brand":
            tag = "tagged discovery" if i["type"] == "discovery" else "has no type tag"
            warnings.append(f"Discovery question contains the brand (\"{i['brand_found']}\"; {tag}): \"{i['text']}\"")
    counts = {"short": 0, "medium": 0, "long": 0}
    for i in items:
        counts[i["length"]] += 1
    langs = Counter(i["language"] or "not tagged" for i in items)
    targets = sum(1 for i in items if i["is_target"])
    answer = (f"{proposed['version']} is {qstate['state']}: {plural(len(items), 'question')} ("
              f"{counts['short']} short, {counts['medium']} medium, {counts['long']} long), {plural(targets, 'target')}"
              + (f", {plural(len(warnings), 'warning')}" if warnings else "") + ".")
    blocks = [ul([f"Set {proposed['version']} ({proposed['file']}): **{qstate['state']}**.", f"Length: {counts['short']} short · {counts['medium']} medium · {counts['long']} long.",
                  "Language: " + ", ".join(f"{k} {v}" for k, v in langs.items()) + ".", f"Targets: {targets}. Controls: {len(controls)}. Marked for the spot check: {sum(1 for i in items if i['spot_check'])}."])]
    blocks += [warn(w) for w in warnings]
    headers = ["Question", "Type", "Intent", "Length", "Language", "Target", "Spot check"]

    def row(i: dict) -> list[str]:
        # Every cell holds a VALUE. Where the tag was missing, the value is still shown and says how we got it.
        return [i["text"], i["effective_type"] + ("" if i["type_from"] == "tag" else " (derived)"), i["intent"] or "not tagged",
                i["length"] + ("" if i["length_from"] == "tag" else " (derived)"), i["language"] or "not tagged",
                "yes" if i["is_target"] else "no", "yes" if i["spot_check"] else "no"]
    for qtype in ("discovery", "brand"):
        for length in q.LENGTHS:
            group = [i for i in items if i["effective_type"] == qtype and i["length"] == length and not i["control"]]
            if group:
                blocks += [h(f"{qtype.capitalize()} · {length}"), table(headers, [row(i) for i in group])]
    if controls:
        blocks += [h("Controls (informational; we do not work on these)"), table(headers, [row(i) for i in controls])]
    return {"data": {"set": {"version": proposed["version"], "file": proposed["file"], "state": qstate["state"]}, "items": items, "counts": counts,
                     "languages": dict(langs), "targets": targets, "controls": len(controls),
                     "spot_check": sum(1 for i in items if i["spot_check"]), "warnings": warnings}, "answer": answer, "blocks": blocks}


def sec_signals(ctx: dict) -> dict:
    rows, data = [], []
    for label, key, source, needs in SIGNALS:
        have = evidence_of(ctx, key)
        status = f"received, latest {latest_date(have)}" if have else "not collected"
        rows.append([label, source, needs, status])
        data.append({"signal": label, "evidence_type": key, "received": bool(have), "latest": latest_date(have)})
    got = sum(1 for d in data if d["received"])
    return {"data": {"signals": data}, "answer": f"{got} of {len(data)} AI visibility signals have evidence on file.",
            "blocks": [table(["Signal", "Where it comes from", "What is needed", "Status"], rows),
                       p("No engine publishes how many people asked about a business. These signals show where AI touched the site; none of them is a count of questions.")]}


def sec_client_needs(ctx: dict) -> dict:
    access = [a["label"] for a in access_status(ctx) if a["applies"] and not a["received"]]
    unconfirmed = [f["label"] for f in (ctx["profile"] or {}).get("facts", []) if f.get("status") != CONFIRMED]
    no_doc = not (ctx["profile"] or {}).get("facts")
    answer = f"{plural(len(access), 'access item')} and {plural(len(unconfirmed), 'fact')} still needed from the client, plus the standard question-set asks."
    blocks = [h("Access we do not have yet"), ul(access) if access else p("None: everything we ask for has arrived."),
              h("Facts to confirm"),
              ul(unconfirmed) if unconfirmed else p("Send the truth document: there is no profile with facts yet." if no_doc else "None: every fact is confirmed."),
              h("For the question set"), ul(STANDARD_ASKS)]
    return {"data": {"access": access, "unconfirmed_facts": unconfirmed, "no_truth_document": no_doc, "standard_asks": STANDARD_ASKS},
            "answer": answer, "blocks": blocks}


def sec_next(ctx: dict, conn, spot: dict | None, qstate: dict, proposed: dict | None) -> dict:
    steps: list[dict] = []

    def add(step: str, why: str) -> None:
        steps.append({"step": step, "why": why})

    eng = ctx["engagement"]["engagement_type"]
    facts = counts_of(ctx)
    if not ctx["diagnoses"]:
        add("Run the diagnosis", "no diagnosis yet: `aeo diagnose <url> --business <b> --checkpoint before`.")
    if eng in WEB_DEV and not evidence_of(ctx, "site_move"):
        add("Finish the site move", "the site is being replaced and no site-move evidence is logged yet (evidence type site_move).")
    if spot is None:
        add("Run the spot check", "no finished snapshot run: ask the brand questions once on each engine and keep the full answers.")
    if facts["facts_total"] == 0:
        add("Send the truth document", "no confirmed facts: `aeo truth draft`, then `aeo truth import`.")
    elif facts["facts_confirmed"] < facts["facts_total"]:
        add("Get the remaining facts confirmed", f"{facts['facts_total'] - facts['facts_confirmed']} fact(s) are still unconfirmed.")
    if qstate["state"] == "none":
        add("Write the question set", "there is no proposed or used question set.")
    elif qstate["state"] == "draft":
        add("Freeze the questions", f"{qstate['version']} is still a draft: nothing is measured against it until it is frozen.")
    missing = [a["label"] for a in access_status(ctx) if a["applies"] and not a["received"]]
    if missing:
        add("Ask the client for access", "still missing: " + ", ".join(missing) + ".")
    if ctx["status"] in PAID and not any(r["status"] == "complete" for r in ctx["runs"]):
        add("Audit 1", "the client has paid and no audit has been done.")
    if ctx["diagnoses"] and ctx["diagnoses"][-1]["findings"] and ctx["recommendations"] == 0:
        add("Run `aeo recommend`", "there are open findings and no recommendations yet.")
    answer = ("Next: " + steps[0]["step"] + ".") if steps else "Nothing is blocking: the baseline is complete."
    blocks = [ul([f"**{s['step']}**: {s['why']}" for s in steps])] if steps else [p("No open steps from the rules.")]
    return {"data": {"steps": steps}, "answer": answer, "blocks": blocks}


# ---- the whole dossier -------------------------------------------------------------------------------------------------------------

def previous_dossier(folder: Path) -> dict | None:
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2}_dossier_v(\d+)(?:_\d+)?\.json$")
    found = [(int(m.group(1)), f) for f in folder.glob("*.json") if (m := pattern.match(f.name))]
    if not found:
        return None
    try:
        return json.loads(max(found)[1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def collect(conn, business: dict, *, version: int, today: date, notes: dict[str, str], notes_warnings: list[str],
            questions_path: Path | None = None, clients_root: Path | None = None, question_dir: Path | None = None,
            spot_check_path: Path | None = None) -> dict:
    ctx = load(conn, business)
    terms = q.brand_terms(ctx["name"], ctx["domain"], ctx["alternates"])
    folder = client_files.generated_dir(business["domain"], clients_root)
    previous = previous_dossier(folder)
    # The question set: the file given, else the latest one an audit used.
    try:
        proposed = q.read_questions(questions_path) if questions_path else None
    except q.DossierQuestionsError as exc:
        raise DossierError(str(exc)) from exc
    if proposed is None and ctx["used_versions"]:
        used_file = q.find_question_file(ctx["used_versions"][-1], question_dir)
        if used_file:
            proposed = q.read_questions(used_file)
    qstate = question_set_state(ctx, proposed)
    # The spot-check set: a separate small file, or the questions marked spot_check: true in the proposed set.
    spot_set = None
    try:
        if spot_check_path:
            small = q.read_questions(spot_check_path)
            spot_set = {"source": small["file"], "items": small["items"]}
        elif proposed and any(i["spot_check"] for i in proposed["items"]):
            spot_set = {"source": f"the questions marked spot_check in {proposed['file']}", "items": [i for i in proposed["items"] if i["spot_check"]]}
    except q.DossierQuestionsError as exc:
        raise DossierError(str(exc)) from exc
    spot = spot_check_run(ctx, conn)
    page = latest_raw_page(business["domain"], clients_root)

    built = {
        "how_to_use": sec_how_to_use(ctx, version, today, previous), "snapshot": sec_snapshot(ctx, page),
        "stage": sec_stage(ctx, conn, qstate, spot), "how_ai_sees": sec_how_ai_sees(ctx), "crawler": sec_crawler(ctx),
        "ai_says": sec_ai_says(ctx, conn, spot, proposed, terms, question_dir, spot_set), "problems": sec_problems(ctx, conn),
        "questions": sec_questions(ctx, proposed, qstate, terms), "signals": sec_signals(ctx), "client_needs": sec_client_needs(ctx),
        "next": sec_next(ctx, conn, spot, qstate, proposed),
    }
    sections = [{"key": k, "title": notes_mod.TITLES[k], **built[k], "notes": notes.get(k, "")} for k, _ in notes_mod.SECTIONS]
    return {"tool": {"name": "aeo dossier"}, "version": version, "generated_on": today.isoformat(),
            "business": {"id": business["id"], "name": ctx["name"], "domain": ctx["domain"], "status": ctx["status"]},
            "counts": counts_of(ctx), "notes_warnings": notes_warnings, "sections": sections}
