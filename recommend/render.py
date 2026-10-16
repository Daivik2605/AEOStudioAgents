"""The recommendations document, made from the data dictionary and nothing else (so the .json always matches it)."""

from __future__ import annotations

import re

SECTIONS = [
    ("access", "1. Access blockers", "AI search bots that are blocked, and critical rendering problems. Nothing else matters until these are fixed: a bot that cannot read the site cannot cite it."),
    ("wrong_fact", "2. Wrong facts the audit found", "Statements in AI answers that contradict a confirmed fact."),
    ("target_question", "3. Target questions where the client is missing", "Questions we aimed at where competitors are named and the client is not. Each one lists what AI cited and why it named the competitors: that is the brief for the page. The page itself is not written here."),
    ("finding", "4. Remaining open findings", "Everything else the diagnosis found, most severe first."),
    ("hygiene", "5. Hygiene (cheap, unproven)", "Structured data and llms.txt. They ship last because they are among the weakest levers in the evidence (research/AEO_PLAYBOOK.md §3, P2). Do not expect them to move the numbers by themselves."),
]
STATUS_TEXT = {"can": "can be done", "gated": "can be done on a higher plan or with a plugin",
               "blocked_by_plan": "cannot be done on the plan the client is on", "cannot": "CANNOT be done on this platform",
               "unknown": "platform not confirmed"}


def _fence(text: str) -> str:
    longest = max([len(m) for m in re.findall(r"`+", text)] or [0])
    return "`" * max(3, longest + 1)


def _block(text: str, lang: str = "") -> list[str]:
    f = _fence(text)
    return [f + lang, text.rstrip("\n"), f]


def _plain(text) -> str:
    return " ".join(str(text).split())


def _fix_lines(f: dict, platform: str, names: dict, platform_known: bool) -> list[str]:
    out = [f"#### {f['id']} · {_plain(f['title'])}", ""]
    closes = f"`{f['finding_code']}`" if f["finding_code"] else "none (no diagnosis finding covers this)"
    out.append(f"- **Closes finding:** {closes}" + (f" (and {', '.join('`' + c + '`' for c in f['also_closes'])})" if f["also_closes"] else ""))
    out.append(f"- **Severity:** {f['severity']}")
    r = f["route"]
    if f["cannot_do"]:
        out.append(f"- **Cannot be done on {platform}.** {_plain(r['where'])}")
        if r.get("alternative"):
            out.append(f"- **Instead:** {_plain(r['alternative'])}")
    else:
        out.append(f"- **Where to do it on {platform}:** {_plain(r['where'])}"
                   + (" _(by hand: it cannot be scripted)_" if r.get("by_hand") else ""))
        if r["status"] in ("gated", "blocked_by_plan"):
            out.append(f"- **Plan:** {STATUS_TEXT[r['status']]}.")
        elif r["status"] == "unknown" and not platform_known:
            out.append("- **Plan:** the platform is not confirmed, so this is general advice.")
    if r.get("source"):
        out.append(f"- **Source:** {_plain(r['source'])}")
    if f.get("file"):
        out.append(f"- **File:** `{names[f['file']]}`")
    if f["section"] == "wrong_fact":
        out.append(f"- **What AI said:** “{_plain(f['quote'])}” ({f['answers']} answer(s), {', '.join(f['engines'])})")
        if f.get("why_wrong"):
            out.append(f"- **Why it is wrong:** {_plain(f['why_wrong'])}")
        if f.get("confirmed_fact"):
            label = f["fact_label"] or "fact"
            out.append(f"- **Confirmed fact ({_plain(label)}):** {_plain(f['confirmed_fact'])}"
                       + (" _(the truth document has changed since this audit ran)_" if f["fact_changed_since_run"] else ""))
        else:
            out.append("- **Confirmed fact:** none matched this claim by name. Check the truth document.")
        if f["likely_sources"]:
            out.append("- **Where AI likely got it** (sources cited in those answers): "
                       + ", ".join(f"{s['domain']} ×{s['answers']}" for s in f["likely_sources"]))
        else:
            out.append("- **Where AI likely got it:** those answers cited no outside source.")
        if f["client_pages_cited"]:
            out.append("- **The client's own pages cited alongside it:** " + ", ".join(p["url"] for p in f["client_pages_cited"]))
    elif f["section"] == "target_question":
        out.append(f"- **Answers:** {f['answers']}; the client was absent while competitors were named in "
                   f"{f['answers_client_absent_competitors_named']}, and named in {f['answers_client_named']}. "
                   "_This is a brief, not a measurement: per-question counts are too small to report as a result._")
        out.append("- **Sources cited (the brief):** " + (", ".join(f"{s['domain']} ×{s['answers']}" for s in f["sources_cited"]) or "none"))
        out.append("- **Competitors named:** " + (", ".join(f"{c['name']} ×{c['answers']}" for c in f["competitors"]) or "none"))
        if f["reasons_given"]:
            out.append("- **Reasons AI gave for the competitors:**")
            out += [f"  - “{_plain(r['quote'])}” ×{r['answers']}" for r in f["reasons_given"]]
        else:
            out.append("- **Reasons AI gave for the competitors:** none recorded.")
        out.append(f"- **Page that would answer it better:** {_plain(f['page_needed'])}")
    elif f.get("evidence") and f["section"] in ("access", "finding"):
        out.append(f"- **Evidence:** {_plain(f['evidence'])}")
    if f["section"] == "hygiene":
        out.append(f"- **Cheap, unproven.** {_plain(f['what'])}")
    elif f["section"] in ("access", "finding"):
        pass
    return out + [""]


def render_markdown(data: dict) -> str:
    biz, inp = data["business"], data["inputs"]
    diag = inp["diagnosis"]
    platform = diag["platform_label"]
    out = [f"# Recommendations: {biz['name'] or biz['domain']}", ""]
    if not data["prioritised_by_audit"]:
        out += ["> **Not prioritised by an audit.** No finished audit was used, so the order below comes from the diagnosis alone. "
                "Sections 2 and 3 and the off-site list are empty because they need audit answers.", ""]
    out += ["> These are files and instructions for a person to deploy. Nothing here changed a live site, and nothing here "
            "promises that an AI will name the business: nobody can promise that. What these fixes do is make sure that "
            "when an engine goes looking, it finds the truth.", ""]

    out += ["| | |", "|---|---|",
            f"| Business | {biz['name'] or biz['domain']} ({biz['domain']}) |",
            f"| Generated on | {data['generated_on']} |",
            f"| Diagnosis | {diag['checked_at'][:10]}, checkpoint {diag['checkpoint'] or 'none'}, verdict {diag['verdict']}, {diag['findings']} finding(s) |",
            f"| Platform | {platform}" + (f", plan {diag['plan']}" if diag["plan"] else "") + (f", CDN/host {diag['cdn']}" if diag["cdn"] else "") + " |"]
    a = inp["audit"]
    out.append("| Audit | " + (f"run {a['run_id']} ({a['answers_read']} answers read, checkpoint {a['checkpoint'] or 'none'})" if a else "none (--no-audit)") + " |")
    t = inp["truth_document"]
    out.append(f"| Truth document | version {t['profile_version']}: {t['confirmed_facts']} confirmed fact(s), {t['not_confirmed']} not confirmed |")
    out += ["", "## Files written", ""]
    names = data["files"]
    labels = [("robots", "robots.txt to deploy"), ("llms", "llms.txt to deploy"), ("schema", "schema.json (structured data) to deploy")]
    for key, label in labels:
        if key in names:
            out.append(f"- `{names[key]}`: {label}")
        elif key == "schema":
            out.append("- schema.json: **not written** (it did not pass validation; see below)")
    out += [f"- `{names['recommendations']}` and `{names['recommendations_data']}`: this document and the data it was made from", ""]

    out += ["## The fix list", "",
            "Fixes are in priority order. Each has an id and the finding code it closes: when it is done, record it with "
            f"`aeo log --business {biz['domain']} --finding <CODE> \"what you did\"` so the next diagnosis can show it closed.", ""]
    by_section = {k: [f for f in data["fixes"] if f["section"] == k] for k, _, _ in SECTIONS}
    for key, title, blurb in SECTIONS:
        out += [f"### {title}", "", blurb, ""]
        items = by_section[key]
        if not items:
            out += [_empty(key, data), ""]
            continue
        for f in items:
            out += _fix_lines(f, platform, data["files"], diag["platform"] is not None)

    out += ["## Off-site work (named only, not done here)", ""]
    if not data["off_site"]["available"]:
        out += ["This needs audit answers (which sites AI cited). Not available without an audit.", ""]
    else:
        out += ["Most of what AI cites is earned media, directories, review platforms and similar pages the client does not own. "
                "That work is done by people and none of it happens here. These are the domains AI cited in answers where the "
                "client was absent:", ""]
        doms = data["off_site"]["domains"]
        out += [f"- {d['domain']} ×{d['answers']}" for d in doms] if doms else ["- none: those answers cited no outside sources."]
        out.append("")

    r = data["robots"]
    out += ["## robots.txt", "",
            f"Training bots: `{r['training_bots']}`" + (" (the default: nothing about them was changed)" if r["training_bots"] == "keep" else "") + ". "
            + ("Built from the live file, every existing rule kept." if r["built_from_live_file"] else "There was no usable live file, so this one starts from scratch."), ""]
    if r["changes"]:
        out += [f"- {_plain(c['detail'])}" for c in r["changes"]]
    if r["sitemap_line_added"]:
        out.append("- added the missing `Sitemap:` line")
    if not r["changes"] and not r["sitemap_line_added"] and r["built_from_live_file"]:
        out.append("- no change was needed: every search bot is already allowed.")
    out += [f"- note: {_plain(n)}" for n in r["notes"]]
    out += ["", "Difference from the live file:", ""]
    out += _block(r["diff"], "diff") if r["diff"] else ["_No difference._"]
    out.append("")

    j = data["jsonld"]
    out += ["## schema.json", ""]
    if j["status"] == "valid":
        out.append(f"Valid, type `{j['type']}`. It passed validation: it parses, has the required fields, and its name, address, phone and hours are word-for-word the confirmed facts.")
    else:
        out.append("**Not written.** Validation failed:")
        out += [f"- {_plain(e)}" for e in j["errors"]]
    out += [f"- note: {_plain(n)}" for n in j["notes"]]
    out += ["", "Left out because they are not confirmed (confirm them in the truth document and run this again):", ""]
    out += [f"- {x['label']}: {x['why']}" for x in j["left_out_because_not_confirmed"]] or ["- nothing: every fact is confirmed."]
    out += ["", "## llms.txt", "",
            f"Name from the {data['llms']['name_source']}. Summary " + ("taken from the story's description" if data["llms"]["description_from_story"] else "left out: the story has no description")
            + f". {data['llms']['pages_listed']} page(s) listed from the sitemap; their titles come from the web addresses, so read them over before deploying.", ""]

    notes = list(inp["live_site"]["notes"]) + list(data["notes"])
    if notes:
        out += ["## Notes", ""] + [f"- {_plain(n)}" for n in notes] + [""]
    return "\n".join(out).rstrip("\n") + "\n"


def _empty(key: str, data: dict) -> str:
    if key in ("wrong_fact", "target_question") and not data["prioritised_by_audit"]:
        return "Not available: this needs an audit."
    if key == "wrong_fact":
        return "None: the audit found no answer that contradicts a confirmed fact."
    if key == "target_question":
        return "None: no target question has competitors named while the client is absent" + (
            "" if data["inputs"]["audit"]["question_tags_recorded"] else ", and this run did not record which questions are targets") + "."
    if key == "access":
        return "None: no search bot is blocked and nothing critical was found."
    return "None."
