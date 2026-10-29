"""The audit report as markdown, made from the report data and nothing else (PLAN.md sections 2, 8 and 10).

`render_markdown(data)` takes the dictionary from audit/report_data.py (or the same thing read back from the
.json file) and always returns the same text for the same data. There is no "generated on" stamp and no
run number inside the text, so re-running a report gives a file with identical content.

There is no fix plan anywhere in the report. It says where the business stands; what to change is the job
of `aeo recommend`.
"""

from __future__ import annotations

import re
from typing import Callable

DASH = "–"

# (key in the data, what the row is called). The order of the headline table.
MEASURE_LABELS = [
    ("visibility", "Visibility score"),
    ("recommendation", "Recommendation rate (proposed as the answer)"),
    ("brand_free", "Brand-free answers (named no business at all)"),
    ("visibility_among_named", "Visibility, counting only answers that named at least one business"),
    ("recommendation_among_named", "Recommendation rate, counting only answers that named at least one business"),
    ("gap", "Gap score (named a competitor but not the client)"),
    ("retrieval", "Retrieval activation (the engine searched the web)"),
    ("accuracy", "Accuracy, overall: every answer that named the client (correct ÷ correct + incorrect)"),
    ("accuracy_brand", "Accuracy, brand questions only (correct ÷ correct + incorrect)"),
]


def pct(v: float | None) -> str:
    return "not available" if v is None else f"{v:.1f}%"


def range_text(m: dict) -> str:
    if m["low"] is None:
        return "not available"
    return f"{m['low']:.1f}%{DASH}{m['high']:.1f}%"


def based_on(m: dict) -> str:
    return f"{m['num']} of {m['den']}" if m["den"] else "no answers to base it on"


def fence(text: str) -> str:
    """A code fence longer than any run of backticks inside the text, so an answer can never end its own box."""
    longest = max((len(r) for r in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def measures_table(block: dict) -> list[str]:
    lines = ["| Measure | Value | 95% range | Based on |", "|---|---|---|---|"]
    for key, label in MEASURE_LABELS:
        m = block["measures"].get(key)
        if m is not None:
            lines.append(f"| {label} | **{pct(m['value'])}** | {range_text(m)} | {based_on(m)} |")
    return lines


def tier_line(block: dict) -> str:
    if block["tier"] is None:
        return "Tier: not available."
    text = f"Tier: **{block['tier']}**."
    if block["tier_low"] != block["tier_high"]:
        text += f" The range reaches from {block['tier_low']} to {block['tier_high']}, so the tier is not certain."
    return text


def block_notes(block: dict) -> list[str]:
    out = [f"Of the {block['answers']} answers, {block['measures']['visibility']['num']} named the client; "
           f"{block['mentioned_only']} of those only mentioned it without proposing it as the answer."]
    if block["retrieval_unknown"]:
        out.append(f"The operator could not tell whether the engine searched on {block['retrieval_unknown']} answer(s); "
                   "those are left out of the retrieval figure.")
    return out


def group_tables(title: str, intro: str, section: dict | None, label: Callable[[str], str]) -> list[str]:
    """One small headline table per group (question length, language). Shown only for the before/after design."""
    out = [f"## {title}", ""]
    if section is None:
        return out + ["Not shown for a snapshot: a snapshot reports overall figures only (PLAN.md §8).", ""]
    if not section["groups"]:
        return out + ["No discovery question carries this tag, so there is nothing to split.", ""]
    out += [intro, "", "| Group | Questions | Answers | Visibility | 95% range | Recommendation rate | 95% range |",
            "|---|---|---|---|---|---|---|"]
    for g in section["groups"]:
        v, r = g["measures"]["visibility"], g["measures"]["recommendation"]
        out.append(f"| {label(g['group'])} | {g['questions']} | {g['answers']} | {pct(v['value'])} | {range_text(v)} | "
                   f"{pct(r['value'])} | {range_text(r)} |")
    if section["questions_without_tag"]:
        out += ["", f"{section['questions_without_tag']} discovery question(s) carry no value for this tag and are left "
                    "out of the table. They are still in the headline."]
    return out + [""]


def render_markdown(data: dict) -> str:
    h, d, ex = data["header"], data["design"], data["extraction"]
    name = h["business"]["name"] or h["business"]["domain"]
    numbers = {a["question"]: a["number"] for a in data["appendix"]}
    out: list[str] = []

    if data["incomplete"]:
        i = data["incomplete"]
        out += [f"> **INCOMPLETE.** This run is not finished (status: {i['status']}; {i['answers_saved']} of "
                f"{i['answers_expected']} expected answers saved). Every figure below is based on the answers that exist so far "
                "and may change when the rest are added. Do not send this report to a client.", ""]

    out += [f"# Audit report: {name}", "", f"**{d['label']}.** "]
    if d["label"] == "Before/after measurement":
        out[-1] += (f"{d['questions']} questions × {len(d['engines'])} engines × {d['repeats']} runs, the design in PLAN.md §8. "
                    "This design detects roughly a 20-point change, and not less. Smaller movements are inside the noise.")
    else:
        out[-1] += (f"{d['questions']} question(s) × {len(d['engines'])} engine(s) × {d['repeats']} run(s). "
                    "A snapshot gives one overall figure per measure with its range. It makes no claim about any single "
                    "question, and it is not a before/after measurement.")
    out.append("")

    # ---- header ----
    engines = "; ".join(e["label"] + (f" ({', '.join(e['modes'])})" if e["modes"] else "") for e in h["engines"])
    rows = [
        ("Business", f"{name} ({h['business']['domain']})"),
        ("Checkpoint", h["checkpoint"] or "none"),
        ("Question set", h["question_set_version"] or "not recorded"),
        ("Engines", engines),
        ("Runs of each question", str(h["repeats"])),
        ("Answers", f"{h['answers_saved']} saved of {h['answers_expected']} expected"),
        ("Run started", h["started_at"] or "not recorded"),
        ("Run finished", h["completed_at"] or "not finished"),
        ("Answers collected on", h["first_answer_on"] if h["first_answer_on"] == h["last_answer_on"]
         else f"{h['first_answer_on']} to {h['last_answer_on']}"),
        ("Operator", ", ".join(h["operators"]) or "not recorded"),
        ("Logged in or out", ", ".join(x.replace("_", " ") for x in h["logged_in_states"]) or "not recorded"),
        ("Asked from", ", ".join(h["locations"]) or "not recorded"),
        ("Facts used for accuracy", (f"{h['confirmed_facts']} confirmed fact(s), profile version {h['profile_version']}"
                                     if h["confirmed_facts"] else "none confirmed when the run started")),
        ("Run id", h["run_id"]),
    ]
    out += ["## About this report", "", "| | |", "|---|---|"] + [f"| {k} | {v} |" for k, v in rows] + [""]

    # ---- the two kinds of question ----
    mix = data["question_mix"]
    out += ["## Two kinds of question", "",
            f"**{mix['discovery_questions']} discovery question(s)** ({mix['discovery_answers']} answers read): the buyer has "
            "never heard of the client, so the client's name never appears. Only these measure visibility, recommendation, "
            f"gap and share of voice. **{mix['brand_questions']} brand question(s)** ({mix['brand_answers']} answers read): "
            "the buyer already knows the name. These measure recognition and accuracy, and are kept out of visibility, "
            "because AI always names a business it was asked about by name (PLAN.md §8)."]
    if mix["types_derived_from_text"]:
        out.append(f"{mix['types_derived_from_text']} question(s) did not say which kind they were, so it was worked out from "
                   "whether the question names the client.")
    out.append("")

    # ---- headline ----
    hl = data["headline"]
    out += ["## Headline (discovery questions)", "", "Accuracy is the exception, measured two ways: overall (every answer in which a claim "
            "about the client could be checked) and on brand questions only.", ""] + measures_table(hl) + ["", tier_line(hl)] + [""] + block_notes(hl) + [""]
    if ex["failed"]:
        out += [f"**{len(ex['failed'])} answer(s) could not be read by the model and are left out of every figure above.** "
                "They are counted here and kept in the appendix; they are not treated as answers that named nobody.", ""]
        out += [f"- Q{f['question_number']} · {f['engine']} · run {f['repeat']}" for f in ex["failed"]] + [""]
    if not h["confirmed_facts"]:
        out += ["Accuracy is not scored: no facts had been confirmed (no truth document imported) when this run started.", ""]
    elif hl["measures"]["accuracy"]["den"] == 0:
        out += ["Accuracy is not scored: the answers made no claim that could be checked against a confirmed fact.", ""]
    out += [f"Ranges are {int(data['tool']['bootstrap']['level'] * 100)}% bootstrap ranges that resample the questions, not the answers "
            f"({data['tool']['bootstrap']['resamples']:,} resamples, fixed seed {data['tool']['bootstrap']['seed']}), "
            f"so the same data always gives the same range. Scores use formula version {data['tool']['formula_version']}.", ""]

    # ---- brand questions ----
    br = data["brand"]
    out += ["## Brand questions: does AI know the client?", ""]
    if not br["available"]:
        out += [f"Not available: {br['reason']}.", ""]
    else:
        r = br["recognition"]
        out += [f"Recognition is the share of brand-question answers in which AI knew the business at all: "
                f"**{pct(r['value'])}** ({range_text(r)}), {based_on(r)} answers, from {br['questions']} question(s). "
                "It says nothing about visibility: these questions gave AI the name.", ""]

    # ---- by engine ----
    if data["by_engine"] is not None:
        out += ["## By engine", ""]
        for e in data["by_engine"]:
            out += [f"### {e['label']}", ""] + measures_table(e) + ["", tier_line(e), ""]
    else:
        out += ["## By engine", "", "Not shown for a snapshot: a snapshot reports overall figures only (PLAN.md §8).", ""]

    # ---- by length and language ----
    out += group_tables("By question length",
                        "Short questions are search-box style, long ones describe a real situation. Small specialists often "
                        "win or lose on the long ones (PLAN.md §8). Each group is small, so read the ranges, not the middle figure.",
                        data.get("by_length"), lambda g: g)
    out += group_tables("By language", "Discovery questions only. Quebec buyers ask in French and can get different answers.",
                        data.get("by_language"), lambda g: {"en": "English", "fr": "French"}.get(g, g))

    # ---- targets vs control ----
    out += ["## Targets vs control", ""]
    tvc = data["targets_vs_control"]
    if tvc is None:
        out += ["Not shown for a snapshot: a snapshot reports overall figures only (PLAN.md §8).", ""]
    elif not tvc["available"]:
        out += [f"Not available: {tvc['reason']}.", ""]
    else:
        out += ["Target questions are the ones we are optimising for. The questions we leave untouched are the control: "
                "if a target moves and the control does not, the change is more likely ours (PLAN.md §8).", "",
                f"### Targets ({tvc['targets']['questions']} questions)", ""] + measures_table(tvc["targets"]) + [""]
        out += [f"### Control ({tvc['control']['questions']} questions)", ""] + measures_table(tvc["control"]) + [""]

    # ---- share of voice ----
    sov = data["share_of_voice"]
    out += ["## Share of voice", "",
            f"{sov['businesses_named']} business(es) were named across the answers that were read. Counts only: a single "
            "business's rate from one day's answers carries a very wide range (about ±63 percentage points), so no rate is given.", ""]
    if sov["rows"]:
        out += ["| # | Business | Answers naming it | Answers recommending it | Average position |", "|---|---|---|---|---|"]
        for r in sov["rows"] + sov["client_outside_top"]:
            pos = f"{r['average_position']:.1f}" if r["average_position"] is not None else "none given"
            who = f"**{r['name']} ← the client**" if r["is_client"] else r["name"]
            out.append(f"| {r['rank']} | {who} | {r['answers']} | {r['recommended']} | {pos} |")
        if sov["others"]["businesses"]:
            out += ["", f"{sov['others']['businesses']} other business(es), named in {sov['others']['mentions']} answer(s) in total, are not listed."]
        if not any(r["is_client"] for r in sov["rows"] + sov["client_outside_top"]):
            out += ["", "The client was not named in any answer."]
    else:
        out.append("No business was named in any answer.")
    out.append("")

    # ---- reasons ----
    out += ["## Why AI recommends businesses", "",
            "The reasons the answers themselves gave, quoted, with the number of answers that gave each. The model only "
            "extracted them; it did not judge them.", ""]
    if not data["reasons"]:
        out += ["No answer gave a reason.", ""]
    for b in data["reasons"]:
        out += [f"### {b['name']}" + (" (the client)" if b["is_client"] else "") + f" — named in {b['answers']} answer(s)", ""]
        if b["reasons"]:
            out += [f"- “{r['quote']}” — {r['answers']} answer(s)" for r in b["reasons"]]
            if b["reasons_not_shown"]:
                out.append(f"- …and {b['reasons_not_shown']} more reason(s) not shown")
        else:
            out.append("- No reason was given for this business.")
        out.append("")

    # ---- descriptors ----
    ds = data["descriptors"]
    out += ["## How AI describes the client", "",
            "From brand questions only, where AI was asked about the client by name.", ""]
    if ds["words"]:
        out += [f"Words the answers used for the client (it was named in {ds['client_named_in']} brand answer(s)):", ""]
        out += [f"- {w['word']} — {w['answers']} answer(s)" for w in ds["words"]]
        if ds["not_shown"]:
            out.append(f"- …and {ds['not_shown']} more word(s) not shown")
    else:
        out.append("No descriptive words were recorded for the client"
                   + ("." if data["brand"]["available"] else ", because this run asked no brand questions."))
    out.append("")

    # ---- wrong facts ----
    wf = data["wrong_facts"]
    out += ["## Wrong facts", ""]
    if not wf["confirmed_facts"]:
        out += ["No facts had been confirmed when this run started, so no claim was checked.", ""]
    else:
        out += [f"Claims the answers made about the client, checked against the {wf['confirmed_facts']} confirmed fact(s): "
                f"{wf['correct']} correct, **{wf['incorrect']} incorrect**, {wf['unverifiable']} unverifiable.", "",
                "Unverifiable means no confirmed fact covers the claim. It is not counted as wrong, and not as right.", ""]
        if not wf["wrong"]:
            out += ["No incorrect claim was found.", ""]
        for w in wf["wrong"]:
            where = f"{w['engine']} · run {w['repeat']} · Q{numbers.get(w['question'], '?')}: “{w['question']}”"
            fact = (f"{w['field']}: {w['confirmed_fact']}" if w["confirmed_fact"] is not None
                    else f"no confirmed fact is labelled “{w['field']}”" if w["field"] else "none named")
            out += [f"- **“{w['claim']}”**", f"  - Said by: {where}", f"  - The confirmed fact it contradicts: {fact}"]
            if w["note"]:
                out.append(f"  - Why it was marked wrong: {w['note']}")
        out.append("")

    # ---- sources ----
    s = data["sources"]
    out += ["## Sources", "",
            f"{s['answers_with_sources']} of {s['answers']} answers showed or contained links."]
    if s["top"]:
        out += ["", "| Domain | Answers citing it |", "|---|---|"] + [f"| {t['domain']} | {t['answers']} |" for t in s["top"]]
        if s["other_domains"]:
            out += ["", f"{s['other_domains']} other domain(s) are not listed."]
    out += ["", f"The client's own domain, {s['client_domain']}, was cited in **{s['client_cited_in']}** answer(s)."]
    out += [f"- {u['url']} — {u['answers']} answer(s)" for u in s["client_urls"]]
    out.append("")

    out += ["## What this report does not contain", "",
            "It contains no fix plan. It says where the business stands, not what to change; the fix list comes from "
            "`aeo recommend`, ordered by what this audit showed.", ""]

    # ---- appendix ----
    out += ["## Appendix: every question and every answer (raw answers, not findings)", "",
            "Exactly as collected. Nothing below is a score, and no conclusion should be drawn from a single answer.", ""]
    for q in data["appendix"]:
        kind = f" [{q['type']}]" if q.get("type") else ""
        out += [f"### Q{q['number']}{' (target)' if q['is_target'] else ''}{kind}: {q['question']}", ""]
        if not q["answers"]:
            out += ["No answer has been collected for this question.", ""]
        for a in q["answers"]:
            searched = {True: "yes", False: "no", None: "unclear"}[a["searched"]]
            bits = [f"mode: {a['mode'] or 'not stated'}", f"searched the web: {searched}",
                    f"{(a['logged_in'] or 'login not stated').replace('_', ' ')}"]
            if a["location"]:
                bits.append(f"asked from: {a['location']}")
            if not a["read_by_model"]:
                bits.append("NOT READ by the model: left out of the scores")
            text = a["text"] or ""
            f = fence(text)
            out += [f"#### {a['label']} · run {a['repeat']}", "", " · ".join(bits), "", f, text, f, ""]
            if a["links"]:
                out += ["Links it showed:"] + [f"- {u}" for u in a["links"]] + [""]
    return "\n".join(out).rstrip("\n") + "\n"
