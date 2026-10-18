"""The comparison document, made from the data dictionary and nothing else (so the .json always matches it)."""

from __future__ import annotations

from compare.audit import NOISE, REAL

MEASURE_NAMES = {
    "visibility": "Visibility (answers naming the client)",
    "recommendation": "Recommendation rate (client proposed, not just listed)",
    "brand_free": "Brand-free rate (answers naming nobody)",
    "visibility_among_named": "Visibility, among answers naming at least one business",
    "recommendation_among_named": "Recommendation rate, among answers naming at least one business",
    "gap": "Gap (competitor named, client not)",
    "retrieval": "Retrieval activation (engine searched)",
    "accuracy": "Accuracy (correct ÷ correct + incorrect)",
}


def _pct(v) -> str:
    return "n/a" if v is None else f"{v:.1f}%"


def _pts(v) -> str:
    return "n/a" if v is None else f"{v:+.1f} points"


def _range(m: dict) -> str:
    return f"{_pct(m['value'])} ({m['low']:.1f}–{m['high']:.1f})" if m.get("low") is not None else f"{_pct(m['value'])} (no range)"


def _change(c: dict) -> str:
    rng = f" ({c['low']:+.1f} to {c['high']:+.1f})" if c.get("low") is not None else ""
    return f"{_pts(c['value'])}{rng} · **{c['label']}**"


def _oneline(text) -> str:
    return " ".join(str(text).split())


def _render_diagnosis(d: dict, la: str, lb: str) -> list[str]:
    out = ["## 1. Diagnosis comparison", ""]
    if not d["available"]:
        return out + [f"Not available: {d['reason']}.", ""]
    out += ["Findings are matched by their code. This part is deterministic: the same input gives the same answer every time, "
            "so it needs no range.", "",
            f"**{d['headline']}.** ({d['b_total']} finding(s) in {lb}.)", ""]
    for c in d["caveats"]:
        out += [f"> **Caution:** {_oneline(c)}", ""]
    out += ["| | A: " + la + " | B: " + lb + " |", "|---|---|---|",
            f"| Diagnosis | {d['a']['date']} ({d['a']['findings']} findings) | {d['b']['date']} ({d['b']['findings']} findings) |",
            f"| Platform | {d['platform']['a'] or 'none'} | {d['platform']['b'] or 'none'}" + (" **changed**" if d["platform"]["changed"] else "") + " |",
            f"| Plan | {d['plan']['a'] or 'unknown'} | {d['plan']['b'] or 'unknown'}" + (" **changed**" if d["plan"]["changed"] else "") + " |",
            f"| Verdict | {d['verdict']['a']} | {d['verdict']['b']}" + (" **changed**" if d["verdict"]["changed"] else "") + " |", ""]

    def finding_lines(items, show_was=False, none_logged=None):
        lines = []
        for c in items:
            was = f", was {c['severity_in_a']}" if show_was and c["severity"] != c["severity_in_a"] else ""
            lines.append(f"- `{c['code']}` ({c['severity']}{was}): {_oneline(c['what'])}")
            for w in c.get("work", []):
                lines.append(f"  - work logged: {_oneline(w['text'])} ({w['date']}, by {w['operator']})")
            if none_logged and not c.get("work"):
                lines.append(f"  - {none_logged}")
        return lines or ["None."]

    out += [f"### Fixed ({len(d['fixed'])})", "", "In A, not in B, and what the finding was about still exists in B.", ""]
    out += finding_lines(d["fixed"], none_logged="no work-log entry carries this code (it may have been fixed on its own, or the work was not logged)")
    out += ["", f"### No longer applies ({len(d['no_longer_applies'])})", "",
            "In A, not in B, but what the finding was about was removed. These were NOT fixed: they went away with it, "
            "and what replaced it is shown under each.", ""]
    gone_lines = []
    for c in d["no_longer_applies"]:
        gone_lines.append(f"- `{c['code']}` ({c['severity']}): {_oneline(c['what'])}")
        gone_lines += [f"  - replaced by `{r['code']}` ({r['severity']}) in B: {_oneline(r['what'])}" for r in c["replaced_by"]]
        gone_lines += [f"  - work logged: {_oneline(w['text'])} ({w['date']}, by {w['operator']})" for w in c.get("work", [])]
    out += gone_lines or ["None."]
    out += ["", f"### Still open ({len(d['still_open'])})", "", "In both. Any work logged against them is shown: logged as done, but still open.", ""]
    out += finding_lines(d["still_open"], show_was=True)
    out += ["", f"### New ({len(d['new'])})", "", "In B, not in A.", ""]
    out += finding_lines(d["new"])
    return out + [""]


def _render_audit(a: dict, la: str, lb: str, floor: dict) -> list[str]:
    out = ["## 2. Audit comparison", ""]
    if not a["available"]:
        return out + [f"Not available: {a['reason']}.", ""]
    d = a["design"]
    out += [f"**{d['label']}**: {d['questions']} question(s) × {len(d['engines'])} engine(s) × {d['repeats']} run(s) in each. "
            f"Question set {a['question_set']['version']}, the same in both (hash {(a['question_set']['sha256'] or 'none')[:12]}).", "",
            f"> **{floor['text']}** A change smaller than that is within what this design can tell apart from noise"
            + ("." if d["label"] == "Before/after measurement" else
               ", and this is a snapshot with fewer answers than the before/after design, so read every label with extra care."), ""]
    sa, sb = a["a"], a["b"]
    out += ["| | A: " + la + " | B: " + lb + " |", "|---|---|---|",
            f"| Run | {sa['run_id']} | {sb['run_id']} |",
            f"| Finished | {(sa['completed_at'] or '')[:10]} | {(sb['completed_at'] or '')[:10]} |",
            f"| Answers (read by the model) | {sa['answers']} ({sa['scored']}) | {sb['answers']} ({sb['scored']}) |",
            f"| Truth document version | {sa['profile_version']} | {sb['profile_version']} |", ""]
    f = a["failed_extractions"]
    if f["a"] or f["b"]:
        out += [f"> Answers the model could not read are left out of every score: {f['a']} in A, {f['b']} in B.", ""]

    h = a["headline"]
    out += ["### Headline numbers", "",
            f"Each value is shown with its 95% range. The change is B minus A; its range comes from resampling the same questions in both runs. "
            f"**\"{REAL}\"** means the range of the change excludes zero; otherwise **\"{NOISE}\"**.", "",
            f"Tier: {h['tier_a']} → {h['tier_b']}", "",
            "| Measure | A | B | Change (95% range) |", "|---|---|---|---|"]
    for key, m in h["measures"].items():
        out.append(f"| {MEASURE_NAMES[key]} | {_range(m['a'])} | {_range(m['b'])} | {_change(m['change'])} |")
    out.append("")
    if not a["accuracy_compared"]:
        out += ["Accuracy is not compared: it needs confirmed facts in the truth document at both checkpoints.", ""]
    out += [f"Denominators: the first four measures use all scored answers or only those naming at least one business, as labelled. "
            f"Answers where the client was named but not recommended: A {h['mentioned_only']['a']}, B {h['mentioned_only']['b']}. "
            f"Answers where we could not tell whether the engine searched: A {h['retrieval_unknown']['a']}, B {h['retrieval_unknown']['b']}.", ""]

    out += ["### Targets vs control", ""]
    t = a["targets_vs_control"]
    if not t["available"]:
        out += [f"Not available: {t['reason']}.", ""]
    else:
        out += [f"{t['target_questions']} target question(s) and {t['control_questions']} control question(s). "
                "The last column is the change on targets minus the change on control: the honest measure of our work.", "",
                "| Measure | Change on targets | Change on control | Difference |", "|---|---|---|---|"]
        for key, p in t["measures"].items():
            out.append(f"| {MEASURE_NAMES[key]} | {_change(p['target'])} | {_change(p['control'])} | {_change(p['difference'])} |")
        out += ["", _oneline(t["note"]), ""]

    s = a["share_of_voice"]
    out += ["### Share of voice", ""]

    def client_text(c):
        return "not named in any answer" if c is None else f"rank {c['rank']} of {c['of_businesses']} named, in {c['answers']} answer(s), recommended in {c['recommended']}"
    out += [f"- Client in A: {client_text(s['client']['a'])}", f"- Client in B: {client_text(s['client']['b'])}", ""]
    out += ["Competitors that gained:"] + ([f"- {m['name']}: {m['a']} → {m['b']} ({m['change']:+d})" for m in s["gained"]] or ["- none"])
    out += ["", "Competitors that lost:"] + ([f"- {m['name']}: {m['a']} → {m['b']} ({m['change']:+d})" for m in s["lost"]] or ["- none"])
    out += ["", _oneline(s["note"]) + f" Scored answers: A {s['scored_answers']['a']}, B {s['scored_answers']['b']}.", ""]

    w = a["wrong_facts"]
    out += ["### Wrong facts", ""]
    if not w["available"]:
        out += [f"Not available: {w['reason']}.", ""]
    else:
        if w["different_truth_documents"]:
            out += [f"> The two runs were judged against different truth-document versions (A: {w['profile_versions']['a']}, B: {w['profile_versions']['b']}), "
                    "so a fact can look fixed or new because the document changed.", ""]
        for title, key in (("Fixed (stated in A, not in B)", "fixed"), ("Still stated", "still"), ("New (stated in B, not in A)", "new")):
            out += [f"**{title}: {len(w[key])}**", ""]
            out += [f"- {_oneline(r['fact'])}" + (f" (answers: A {r['answers_a']}, B {r['answers_b']})" if key == "still" else "")
                    + (f": “{_oneline((r['claims_b'] or r['claims_a'])[0])}”" if r["field"] else "") for r in w[key]] or ["- none"]
            out.append("")
        out += ["_Wrong facts are matched by the fact they are about when the check named one, otherwise by the exact claim._", ""]

    so = a["sources"]
    out += ["### Sources", "",
            f"Answers citing the client's own domain ({so['client_domain']}): A {so['a']['client_cited_in']} of {so['a']['answers']}, "
            f"B {so['b']['client_cited_in']} of {so['b']['answers']}. Answers with any source: A {so['a']['answers_with_sources']}, B {so['b']['answers_with_sources']}.", ""]
    return out


def render_markdown(data: dict) -> str:
    biz, la, lb = data["business"], data["labels"]["a"], data["labels"]["b"]
    out = [f"# Comparison: {biz['name'] or biz['domain']}, {la} vs {lb}", "",
           f"Generated {data['generated_on']}. Nothing here promises an outcome: it shows what changed between two measurements and how sure we can be.", ""]
    out += _render_diagnosis(data["diagnosis"], la, lb)
    out += _render_audit(data["audit"], la, lb, data["detection_floor"])
    b = data["between"]
    out += ["## 3. What happened in between", "", f"From {b['from']} to {b['to']}.", ""]
    if b["note"]:
        out += [f"> {b['note']}", ""]
    if b["events"]:
        out += [f"> **{b['events']} event(s) outside our control happened in this period.** They can move the numbers too, so a change above may not be ours.", ""]
    for e in b["entries"]:
        if e["outside_our_control"]:
            out.append(f"- **OUTSIDE OUR CONTROL: event, {e['date']}:** {_oneline(e['text'])} (recorded by {e['operator']})")
        else:
            out.append(f"- Our work, {e['date']}: {_oneline(e['text'])} (by {e['operator']})" + (f" [{e['finding']}]" if e["finding"] else ""))
    if not b["entries"]:
        out.append("Nothing was logged in this period. If work was done, it was not recorded with `aeo log`.")
    return "\n".join(out).rstrip("\n") + "\n"
