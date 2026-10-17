"""Turns the report data into ONE self-contained HTML file (PLAN.md step 22).

  * inline CSS, no external fonts, scripts, images or links: screenshots are embedded as data, charts are inline SVG;
  * a print stylesheet, so Print > Save as PDF has sensible page breaks and still reads in greyscale;
  * every sentence comes from client_report/strings.py, in the language named in the data;
  * deterministic: the same data gives the same bytes. No clock, no randomness.
"""

from __future__ import annotations

from html import escape as E

from client_report import charts
from compare.audit import DETECTION_FLOOR_POINTS
from client_report.strings import t
from client_report.words import Words

CSS = """
:root{--ink:#1b1b1b;--mid:#555;--rule:#c9c9c9;--soft:#f3f3f3;--paper:#fff}
*{box-sizing:border-box}
body{margin:0;background:var(--soft);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.page{max-width:860px;margin:0 auto;padding:32px 36px;background:var(--paper)}
header.top{border-bottom:3px solid var(--ink);padding-bottom:14px;margin-bottom:22px}
.brand{font-size:13px;letter-spacing:.14em;text-transform:uppercase;font-weight:700}
h1{font-size:28px;line-height:1.2;margin:6px 0 10px}
dl.meta{display:grid;grid-template-columns:repeat(4,auto);gap:4px 28px;margin:0;font-size:13px;justify-content:start}
dl.meta dt{color:var(--mid)}dl.meta dd{margin:0;font-weight:600}
h2{font-size:21px;margin:30px 0 8px;padding-top:6px;border-top:1px solid var(--rule)}
h3{font-size:16px;margin:18px 0 6px}
p{margin:6px 0}
.small{font-size:12.5px;color:var(--mid)}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13.5px}
th,td{border:1px solid var(--rule);padding:5px 8px;text-align:left;vertical-align:top}
th{background:var(--soft);font-weight:700}
td.num{text-align:right;white-space:nowrap}
.summary{border-left:5px solid var(--ink);background:var(--soft);padding:8px 14px;margin:10px 0}
.summary h3{margin:0 0 4px;font-size:14px;text-transform:uppercase;letter-spacing:.06em}
.box{border:2px solid var(--ink);padding:8px 12px;margin:10px 0}
.warn{border:2px solid var(--ink);border-left-width:8px;padding:8px 12px;margin:10px 0;font-weight:600}
blockquote{margin:12px 0;padding:10px 16px;border-left:5px solid var(--ink);background:var(--soft);font-size:16px}
code{font:12px ui-monospace,Menlo,Consolas,monospace;background:var(--soft);padding:1px 4px}
.tag{display:inline-block;border:1px solid var(--ink);padding:0 6px;font-size:11.5px;font-weight:700;margin-right:6px}
.tag.event{background:var(--ink);color:var(--paper)}
.real{font-weight:700}.noise{font-style:italic}
figure{margin:12px 0}figure img{max-width:100%;border:1px solid var(--rule)}figcaption{font-size:12.5px;color:var(--mid)}
ul{margin:6px 0;padding-left:22px}li{margin:3px 0}
svg.chart{display:block;max-width:100%;height:auto;margin:6px 0}
footer{margin-top:34px;padding-top:12px;border-top:3px solid var(--ink);font-size:12.5px;color:var(--mid)}
@page{size:A4;margin:16mm 14mm}
@media print{
 body{background:#fff;font-size:10.5pt}.page{max-width:none;padding:0}
 h2,h3{break-after:avoid;page-break-after:avoid}
 table,ul,ol,dl,li,figure,.summary,.box,.warn,blockquote,svg.chart,.keep,tr{break-inside:avoid;page-break-inside:avoid}
 thead{display:table-header-group}
 *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
}
"""


# ---- tiny helpers ---------------------------------------------------------------------------------------------------------

def pct(v) -> str:
    return "n/a" if v is None else f"{v:.1f}%"


def with_range(m: dict) -> tuple[str, str]:
    """(figure, range) for one measure. A figure never appears without its range or the reason there is none."""
    return pct(m["value"]), (f"{m['low']:.1f}–{m['high']:.1f}" if m.get("low") is not None else None)


def change_text(c: dict) -> str:
    """'+12.5 points (+3.0 to +20.0) · real change'. The label is exactly the one `aeo compare` gave."""
    if c["value"] is None:
        return E(c["label"])
    rng = f" ({c['low']:+.1f} to {c['high']:+.1f})" if c.get("low") is not None else ""
    cls = "real" if c["label"] == "real change" else "noise" if c["label"] == "within the noise" else ""
    return f'{c["value"]:+.1f} points{rng} · <span class="{cls}">{E(c["label"])}</span>'


def table(headers: list[str], rows: list[list[str]], num_cols: tuple[int, ...] = ()) -> str:
    head = "".join(f"<th>{E(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f'<td class="num">{c}</td>' if i in num_cols else f"<td>{c}</td>" for i, c in enumerate(r)) + "</tr>"
                   for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def bullet(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>" if items else ""


class Ctx:
    def __init__(self, report: dict):
        self.report = report
        self.lang = report["tool"]["language"]
        self.client = report["business"]["name"] or report["business"]["domain"]
        self.labels = report["labels"]
        self.w = Words(report)                  # the client's words for labels, platforms, findings and fixes

    def t(self, key: str, **kw) -> str:
        return t(key, self.lang, **kw)


# ---- diagnosis / health -----------------------------------------------------------------------------------------------------

def _finding_text(c: Ctx, f: dict, platform: str | None) -> str:
    """The sentence the client reads for one finding. The internal wording (f["what"]) is never shown."""
    return c.w.finding(f, f.get("platform") or platform)


def _findings_table(c: Ctx, findings: list[dict], platform: str | None) -> str:
    if not findings:
        return f"<p>{E(c.t('health.no_findings'))}</p>"
    return table([c.t("health.col.severity"), c.t("health.col.what")],
                 [[E(c.t(f"severity.{f['severity']}")), E(_finding_text(c, f, platform))] for f in findings])


def _health_comparison(c: Ctx, d: dict) -> str:
    la, lb = c.w.label(d["a"]["checkpoint"], d["a"]["date"]), c.w.label(d["b"]["checkpoint"], d["b"]["date"])
    out = [f"<h3>{E(c.t('health.compared', a=la, b=lb))}</h3>", f"<p class='box'><strong>{E(c.w.headline(d))}</strong></p>",
           f"<p class='small'>{E(c.t('health.deterministic'))}</p>"]
    if d.get("caveats"):         # one fixed sentence; the internal caveat text names finding codes
        out.append(f"<p class='warn'>{E(c.t('health.caution'))}: {E(c.t('health.caveat'))}</p>")
    changed = f" <span class='tag'>{E(c.t('health.changed'))}</span>"
    rows = [[E(c.t("health.row.platform")), E(c.w.platform(d["platform"]["a"])),
             E(c.w.platform(d["platform"]["b"])) + (changed if d["platform"]["changed"] else "")],
            [E(c.t("health.row.verdict")), E(c.w.verdict(d["verdict"]["a"])),
             E(c.w.verdict(d["verdict"]["b"])) + (changed if d["verdict"]["changed"] else "")]]
    out.append(table(["", la[:1].upper() + la[1:], lb[:1].upper() + lb[1:]], rows))

    def items(group: list[dict], show_work: bool) -> str:
        lis = []
        for f in group:
            line = f"<strong>{E(c.t('severity.' + f['severity']))}.</strong> {E(_finding_text(c, f, None))}"
            work = f.get("work") or [] if show_work else []
            line += "".join(f"<br><span class='small'>{E(c.t('health.work_logged', text=w['text'], date=w['date'], who=w['operator']))}</span>" for w in work)
            # Only a logged piece of work, or a replaced website, explains a closed finding. With neither: say nothing.
            if show_work and not work and f.get("site_replaced"):
                line += f"<br><span class='small'>{E(c.t('health.fixed_by_new_site'))}</span>"
            lis.append(line)
        return bullet(lis) or f"<p>{E(c.t('health.none_listed'))}</p>"
    out += [f"<h3>{E(c.t('health.closed', n=len(d['closed'])))}</h3>", items(d["closed"], True),
            f"<h3>{E(c.t('health.still', n=len(d['still_open'])))}</h3>", items(d["still_open"], True),
            f"<h3>{E(c.t('health.new', n=len(d['new'])))}</h3>", items(d["new"], False)]
    return "".join(out)


def r_health(c: Ctx, d: dict) -> str:
    if not d.get("available"):
        return f"<p>{E(c.t('health.none'))}</p>"
    return _health_comparison(c, d)


def health(c: Ctx, d: dict) -> str:
    if not d.get("available"):
        return f"<p>{E(c.t('health.none'))}</p>"
    L = d["latest"]
    checked = (c.t("health.checked_label", date=L["date"], label=c.w.label(L["checkpoint"], L["date"])) if L["checkpoint"]
               else c.t("health.checked", date=L["date"]))
    out = [f"<p>{E(checked)} "
           + E(c.t("health.platform", platform=c.w.platform(L["platform"])) if L["platform"] else c.t("health.platform_unknown")) + "</p>",
           f"<p><strong>{E(c.t('health.verdict.' + L['verdict']))}</strong></p>",
           f"<h3>{E(c.t('health.findings'))}</h3>", _findings_table(c, L["findings"], L["platform"])]
    if d.get("comparison"):
        out.append(_health_comparison(c, d["comparison"]))
    return "".join(out)


# ---- the AI measurement -----------------------------------------------------------------------------------------------------

def _design_banner(c: Ctx, design: dict) -> str:
    engines = len(design["engines"])
    if design["label"] == "Snapshot":
        return f"<p class='box'><strong>{E(c.t('ai.snapshot', questions=design['questions'], engines=engines, repeats=design['repeats']))}</strong></p>"
    return f"<p class='box'>{E(c.t('ai.before_after', questions=design['questions'], engines=engines, repeats=design['repeats']))}</p>"


MAIN_CHART = ("visibility", "recommendation", "gap", "brand_free", "accuracy")


def _headline_table(c: Ctx, h: dict) -> str:
    rows = []
    for key, m in h["measures"].items():
        figure, rng = with_range(m)
        rows.append([E(c.t(f"measure.{key}")), figure, E(rng) if rng else E(c.t("ai.no_range")), E(c.t("ai.a_answers", n=m["den"]))])
    return table([c.t("ai.col.measure"), c.t("ai.col.value"), c.t("ai.col.range"), c.t("ai.col.answers")], rows, num_cols=(1, 2, 3))


def _tv_now(c: Ctx, tv: dict | None) -> str:
    if not tv:
        return ""
    if not tv.get("available"):
        return f"<h3>{E(c.t('tv.now'))}</h3><p>{E(c.t('tv.unavailable', reason=tv['reason']))}</p>"
    rows = []
    for name, block in ((c.t("tv.target"), tv["targets"]), (c.t("tv.control"), tv["control"])):
        for key in ("visibility", "recommendation"):
            figure, rng = with_range(block["measures"][key])
            rows.append([E(name), E(c.t(f"measure.{key}")), figure, E(rng or c.t("ai.no_range"))])
    return f"<h3>{E(c.t('tv.now'))}</h3>" + table([c.t("tv.col.group"), c.t("ai.col.measure"), c.t("ai.col.value"), c.t("ai.col.range")], rows, num_cols=(2, 3))


def _sov(c: Ctx, sov: dict) -> str:
    rows = sov["rows"] + sov["client_outside_top"]
    if not rows:
        return ""
    chart = charts.share_of_voice_svg(rows, alt=c.t("chart.alt_sov"), prefix="sov")
    tbl = table([c.t("sov.col.rank"), c.t("sov.col.business"), c.t("sov.col.answers"), c.t("sov.col.rec")],
                [[str(r["rank"]), E(r["name"]) + (" ★" if r["is_client"] else ""), str(r["answers"]), str(r["recommended"])] for r in rows],
                num_cols=(0, 2, 3))
    return (f"<h3>{E(c.t('sov.title'))}</h3><p class='small'>{E(c.t('sov.client', client=c.client))} {E(c.t('sov.counts_only'))}</p>"
            f"<div class='keep'>{chart}</div>{tbl}")


def _wrong_facts(c: Ctx, wf: dict) -> str:
    if not wf["confirmed_facts"]:
        return f"<p>{E(c.t('wf.none_checked'))}</p>"
    if not wf["incorrect"]:
        return f"<p>{E(c.t('wf.none'))}</p>"
    seen, rows = set(), []
    for w in wf["wrong"]:
        key = (w["claim"], w["field"])
        if key in seen:
            continue
        seen.add(key)
        rows.append([E(w["claim"]), E(w["confirmed_fact"] or "-"), E(w["field"] or "-")])
    return f"<p>{E(c.t('wf.intro', n=wf['incorrect']))}</p>" + table([c.t("wf.col.said"), c.t("wf.col.fact"), c.t("wf.col.about")], rows)


def ai_today(c: Ctx, d: dict) -> str:
    if not d.get("available"):
        return f"<p>{E(c.t('ai.none'))}</p>"
    h, hd, ex = d["headline"], d["header"], d["extraction"]
    out = [_design_banner(c, d["design"]),
           f"<p class='small'>{E(c.t('ai.measured', first=hd['first_answer_on'], last=hd['last_answer_on'], answers=ex['scored'], failed=ex['failed']))}</p>",
           f"<p><strong>{E(c.t('ai.tier', tier=h['tier']))}</strong></p>",
           f"<p class='small'>{E(c.t('ai.ranges'))}</p>"]
    if d["design"]["label"] != "Snapshot":
        out.append(f"<p class='small'>{E(c.t('ai.floor', points=DETECTION_FLOOR_POINTS))}</p>")
    rows = [{"label": c.t(f"measure.{k}"), "series": [{"value": h["measures"][k]["value"], "low": h["measures"][k]["low"], "high": h["measures"][k]["high"]}]}
            for k in MAIN_CHART if k in h["measures"]]
    out.append(f"<div class='keep'>{charts.change_bars_svg(rows, series_labels=[c.t('chart.this')], alt=c.t('chart.alt_change'), prefix='now')}</div>")
    out.append(f"<p class='small'>{E(c.t('chart.whisker'))}</p>")
    out.append(_headline_table(c, h))
    out.append(f"<p class='small'>{E(c.t('ai.denominators', mentioned=h['mentioned_only'], unknown=h['retrieval_unknown']))}</p>")
    if "accuracy" not in h["measures"]:
        out.append(f"<p class='small'>{E(c.t('ai.accuracy_missing'))}</p>")
    out.append(_tv_now(c, d["targets_vs_control"]))
    out.append(_sov(c, d["share_of_voice"]))
    out.append(f"<h3>{E(c.t('h.r_facts'))}</h3>" + _wrong_facts(c, d["wrong_facts"]))
    return "".join(out)


def r_ai(c: Ctx, d: dict) -> str:
    if not d.get("available"):
        return f"<p>{E(c.t('ai.none'))}: {E(d.get('reason') or '')}</p>"
    h, a, b = d["headline"], d["a"], d["b"]
    la, lb = c.w.side("a"), c.w.side("b")
    out = [_design_banner(c, d["design"]),
           f"<p class='warn'>{E(c.t('ai.floor', points=d['detection_floor']['points']))}</p>",
           f"<p><strong>{E(c.t('ai.tier_change', a=h['tier_a'], b=h['tier_b']))}</strong></p>",
           f"<p class='small'>{E(c.t('ai.ranges'))} {E(c.t('ai.change_note'))}</p>"]
    f = d["failed_extractions"]
    out.append(f"<p class='small'>{E(c.t('ai.measured', first=(a['completed_at'] or '')[:10], last=(b['completed_at'] or '')[:10], answers=a['scored'] + b['scored'], failed=f['a'] + f['b']))}</p>")
    rows = [{"label": c.t(f"measure.{k}"), "series": [{"value": m["a"]["value"], "low": m["a"]["low"], "high": m["a"]["high"]},
                                                      {"value": m["b"]["value"], "low": m["b"]["low"], "high": m["b"]["high"]}]}
            for k, m in h["measures"].items() if k in MAIN_CHART]
    out.append("<div class='keep'>" + charts.change_bars_svg(
        rows, series_labels=[c.t("chart.legend_a", label=la), c.t("chart.legend_b", label=lb)], alt=c.t("chart.alt_change"), prefix="chg") + "</div>")
    out.append(f"<p class='small'>{E(c.t('chart.whisker'))}</p>")
    trs = []
    for k, m in h["measures"].items():
        fa, ra = with_range(m["a"])
        fb, rb = with_range(m["b"])
        trs.append([E(c.t(f"measure.{k}")), f"{fa}<br><span class='small'>{E(ra or c.t('ai.no_range'))}</span>",
                    f"{fb}<br><span class='small'>{E(rb or c.t('ai.no_range'))}</span>", change_text(m["change"])])
    out.append(table([c.t("ai.col.measure"), c.t("ai.col.a", label=la), c.t("ai.col.b", label=lb), c.t("ai.col.change")], trs))
    out.append(f"<p class='small'>{E(c.t('ai.denominators', mentioned=h['mentioned_only']['b'], unknown=h['retrieval_unknown']['b']))}</p>")
    if not d["accuracy_compared"]:
        out.append(f"<p class='small'>{E(c.t('ai.accuracy_missing'))}</p>")
    return "".join(out)


def r_targets(c: Ctx, d: dict) -> str:
    if not d.get("available"):
        return f"<p>{E(c.t('tv.unavailable', reason=d.get('reason') or ''))}</p>"
    rows = [[E(c.t(f"measure.{k}")), change_text(p["target"]), change_text(p["control"]), change_text(p["difference"])] for k, p in d["measures"].items()]
    return (f"<p>{E(c.t('tv.intro', t=d['target_questions'], c=d['control_questions']))}</p>"
            + table([c.t("tv.col.measure"), c.t("tv.col.target"), c.t("tv.col.control"), c.t("tv.col.difference")], rows)
            + f"<p class='small'>{E(c.t('tv.note'))}</p>")


def questions(c: Ctx, d: dict) -> str:
    if not d.get("available"):
        return f"<p>{E(c.t('ai.none'))}</p>"
    before_after = d["design"] != "Snapshot"
    rows = []
    for q in d["items"]:
        if not d["tags_recorded"]:
            role = c.t("q.untagged")
        elif q["is_target"]:
            role = c.t("q.target")
        else:
            role = c.t("q.control") if before_after else c.t("q.not_target")
        rows.append([str(q["number"]), E(q["text"]), E(role)])
    note = "" if d["tags_recorded"] else f"<p class='small'>{E(c.t('q.untagged_note'))}</p>"
    return (f"<p>{E(c.t('q.intro', n=len(d['items'])))} <span class='small'>{E(c.t('q.version', version=d.get('version') or '-'))}</span></p>"
            + note + table([c.t("q.col.n"), c.t("q.col.question"), c.t("q.col.role")], rows, num_cols=(0,)))


def r_voice(c: Ctx, d: dict) -> str:
    if not d.get("available"):
        return f"<p>{E(c.t('ai.none'))}</p>"

    def where(row):
        return c.t("sov.client_absent") if row is None else c.t("sov.client_text", rank=row["rank"], of=row["of_businesses"], answers=row["answers"], rec=row["recommended"])
    out = bullet([E(c.t("sov.client_a", client=c.client, label=c.w.side("a"), text=where(d["client"]["a"]))),
                  E(c.t("sov.client_a", client=c.client, label=c.w.side("b"), text=where(d["client"]["b"])))])
    for title, key in (("sov.gained", "gained"), ("sov.lost", "lost")):
        out += f"<h3>{E(c.t(title))}</h3>" + (bullet([E(c.t("sov.move", name=m["name"], a=m["a"], b=m["b"])) for m in d[key]]) or f"<p>{E(c.t('health.none_listed'))}</p>")
    return out + f"<p class='small'>{E(c.t('sov.counts_only'))} {E(c.t('sov.small'))}</p>"


def r_facts(c: Ctx, d: dict) -> str:
    if not d.get("available"):
        return f"<p>{E(c.t('wf.unavailable', reason=d.get('reason') or ''))}</p>"
    out = []
    if d["different_truth_documents"]:
        out.append(f"<p class='warn'>{E(c.t('wf.versions'))}</p>")
    for key, name in (("fixed", "wf.fixed"), ("still", "wf.still"), ("new", "wf.new")):
        out.append(f"<h3>{E(c.t(name, n=len(d[key])))}</h3>")
        out.append(bullet([E(r["fact"]) + (f": “{E((r['claims_b'] or r['claims_a'])[0])}”" if r["field"] else "") for r in d[key]]) or f"<p>{E(c.t('health.none_listed'))}</p>")
    return "".join(out)


def r_sources(c: Ctx, d: dict) -> str:
    if not d.get("available"):
        return f"<p>{E(c.t('ai.none'))}</p>"
    return f"<p>{E(c.t('src.client', a_label=c.w.side('a'), a=d['a']['client_cited_in'], a_n=d['a']['answers'], b_label=c.w.side('b'), b=d['b']['client_cited_in'], b_n=d['b']['answers']))}</p>"


def r_between(c: Ctx, d: dict) -> str:
    out = [f"<p>{E(c.t('btw.period', a=d['from'], b=d['to']))}</p>"]
    if d.get("note"):
        out.append(f"<p class='small'>{E(c.t('btw.note_prefix'))}: {E(d['note'])}</p>")
    if d["events"]:
        out.append(f"<p class='warn'>{E(c.t('btw.event_warning', n=d['events']))}</p>")
    lis = []
    for e in d["entries"]:
        tag = (f"<span class='tag event'>{E(c.t('btw.event'))}</span>" if e["outside_our_control"] else f"<span class='tag'>{E(c.t('btw.work'))}</span>")
        lis.append(f"{tag}{E(e['date'])}: {E(e['text'])} <span class='small'>({E(e['operator'])})</span>")
    out.append(bullet(lis) or f"<p>{E(c.t('btw.none'))}</p>")
    return "".join(out)


# ---- recommend, evidence, work, promise ----------------------------------------------------------------------------------------

def recommend(c: Ctx, d: dict) -> str:
    if not d.get("available"):
        return f"<p>{E(c.t('rec.none'))}</p>"
    intro = c.t("rec.intro") if d["prioritised_by_audit"] else c.t("rec.intro.no_audit")
    out = [f"<p>{E(intro)} <span class='small'>{E(c.t('rec.prepared', date=d['date']))}</span></p>"]
    for section in ("access", "wrong_fact", "target_question", "finding", "hygiene"):
        items = [f for f in d["fixes"] if f["section"] == section]
        if items:
            out.append(f"<h3>{E(c.t('rec.section.' + section))}</h3>" + bullet(
                [E(c.w.fix_title(f, d.get("platform"))) + (f" <span class='small'>({E(c.t('rec.cannot'))})</span>" if f["cannot_do"] else "") for f in items]))
    return "".join(out)


def _source(c: Ctx, e: dict) -> str:
    key = "ev.source." + (e["source"] or "")
    return c.t(key) if e["source"] and c.w.has(key) else (e["source"] or "")


def evidence(c: Ctx, d: dict) -> str:
    items = d["items"]
    if not items:
        return f"<p>{E(c.t('ev.none'))}</p>"
    out = [f"<p class='small'>{E(c.t('ev.intro'))}</p>"]
    for e in items:
        if e["embedded"]:
            cap = f"{e['captured_on']}: {_source(c, e)} {e['note'] or ''}".strip()
            out.append(f"<figure><img alt=\"{E(e['note'] or c.w.evidence_type(e['type']))}\" src=\"{e['data_uri']}\"><figcaption>{E(cap)}</figcaption></figure>")
    listed = [e for e in items if not e["embedded"]]
    if listed:
        rows = []
        for e in listed:
            period = f"<br><span class='small'>{E(c.t('ev.period', start=e['period_start'], end=e['period_end']))}</span>" if e["period_start"] else ""
            missing = f"<br><span class='small'>{E(c.t('ev.not_embedded'))}</span>" if e["kind"] == "image" else ""
            rows.append([E(e["captured_on"]), E(c.w.evidence_type(e["type"])), E(_source(c, e) or "-"), E(e["note"] or "-") + period + missing])
        out.append(f"<h3>{E(c.t('ev.listed'))}</h3>" + table([c.t("ev.col.date"), c.t("ev.col.type"), c.t("ev.col.source"), c.t("ev.col.note")], rows))
    return "".join(out)


def work(c: Ctx, d: dict) -> str:
    items = d["items"]
    if not items:
        return f"<p>{E(c.t('work.none'))}</p>"
    rows = []
    for w in items:
        if w["finding_code"] and w.get("finding"):
            closes = E(c.w.finding({"code": w["finding_code"], "evidence": w.get("finding_evidence")}, w.get("finding_platform")))
        elif w["finding_code"]:
            closes = E(c.t("finding.UNKNOWN"))
        else:
            closes = E(c.t("work.general"))
        rows.append([E(w["date"]), E(w["text"]), closes, E(w["operator"])])
    return table([c.t("work.col.date"), c.t("work.col.work"), c.t("work.col.closes"), c.t("work.col.by")], rows)


def promise(c: Ctx, d: dict) -> str:
    """The sentence we use says what AI says about the client, so it needs a finished audit behind it (PLAN.md section 6).
    Without one, only the plain statement of what comes next."""
    sentence = c.t("promise.sentence") if d.get("audit") else c.t("promise.next")
    return f"<blockquote>{E(sentence)}</blockquote><p>{E(c.t('promise.cannot'))}</p>"


def ai_next(c: Ctx, d: dict) -> str:
    return f"<p>{E(c.t('ai_next.body'))}</p>"


RENDERERS = {"ai_next": ai_next, "health": health, "ai_today": ai_today, "questions": questions, "recommend": recommend, "evidence": evidence,
             "promise": promise, "work": work, "r_health": r_health, "r_ai": r_ai, "r_targets": r_targets,
             "r_questions": questions, "r_voice": r_voice, "r_facts": r_facts, "r_sources": r_sources, "r_between": r_between}
HEADINGS = {"ai_next": "h.ai_next", "health": "h.health", "ai_today": "h.ai_today", "questions": "h.questions", "recommend": "h.recommend",
            "evidence": "h.evidence", "promise": "h.promise", "work": "h.work", "r_health": "h.r_health", "r_ai": "h.r_ai",
            "r_targets": "h.r_targets", "r_questions": "h.r_questions", "r_voice": "h.r_voice", "r_facts": "h.r_facts",
            "r_sources": "h.r_sources", "r_between": "h.r_between"}


def render_html(report: dict) -> str:
    c = Ctx(report)
    stage = c.t("stage." + report["stage"])
    body = []
    for s in report["sections"]:
        summary = ""
        if s.get("summary"):
            summary = (f"<div class='summary'><h3>{E(c.t('h.summary'))}</h3><p>{E(s['summary'])}</p>"
                       f"<p class='small'>{E(c.t('ai.summary_label'))}</p></div>")
        body.append(f"<section class='sec' id='{E(s['key'])}'><h2>{E(c.t(HEADINGS[s['key']]))}</h2>{summary}{RENDERERS[s['key']](c, s['data'])}</section>")
    intro_key = "intro." + report["stage"]
    if report["stage"] == "starting_point" and not any(s["key"] == "promise" and s["data"].get("audit") for s in report["sections"]):
        intro_key += ".no_audit"        # do not say "what AI says about you" when nothing has been measured
    intro = c.t(intro_key, a=c.w.side("a") if c.labels["a"] else "", b=c.w.side("b") if c.labels["b"] else "")
    return (
        "<!doctype html>\n<html lang=\"" + E(c.lang) + "\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{E(c.t('doc_title', client=c.client, stage=stage))}</title><style>{CSS}</style></head><body><div class=\"page\">"
        f"<header class=\"top\"><div class=\"brand\">{E(c.t('app_name'))}</div><h1>{E(c.client)}</h1>"
        f"<dl class=\"meta\"><dt>{E(c.t('header.client'))}</dt><dt>{E(c.t('header.stage'))}</dt><dt>{E(c.t('header.date'))}</dt><dt>{E(c.t('header.version'))}</dt>"
        f"<dd>{E(c.client)}</dd><dd>{E(stage)}</dd><dd>{E(report['generated_on'])}</dd><dd>v{report['version']}</dd></dl></header>"
        f"<p>{E(intro)}</p>" + "".join(body)
        + f"<footer><p>{E(c.t('footer.honesty'))}</p><p>{E(c.t('footer.draft'))}</p></footer></div></body></html>\n")
