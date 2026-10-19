"""The dossier as markdown and as one self-contained HTML file, both made from the same data (the .json file).

The markdown is the plain record. The HTML is the briefing: a cover page of status tiles, a linked index, chips, cards and
tables. It changes how the data is SHOWN and adds nothing new: every tile, chip and list on it is computed from the section
data below it (see `cover_tiles`). Nothing external: no scripts, links to other sites, fonts or images. Deterministic: the
same data gives the same bytes.
"""

from __future__ import annotations

import re
from html import escape as E

INTERNAL_TEXT = "INTERNAL: not for the client"
KEEP_TABLE_ROWS = 15       # a table this short is kept on one page; a longer one may break (never forced, it may not fit)

# ---- markdown ----------------------------------------------------------------------------------------------------------------


def md_cell(text) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def render_md(data: dict) -> str:
    b = data["business"]
    out = [f"# Dossier: {b['name'] or b['domain']}", "", f"**{INTERNAL_TEXT}.** Version {data['version']} · {data['generated_on']} · {b['domain']} · status {b['status']}", ""]
    for w in data["notes_warnings"]:
        out += [f"> **Notes file:** {w}", ""]
    for n, s in enumerate(data["sections"], start=1):
        out += [f"## {n}. {s['title']}", "", f"**{s['answer']}**", ""]
        for blk in s["blocks"]:
            t = blk["t"]
            if t == "p":
                out += [blk["text"], ""]
            elif t == "h":
                out += [f"### {blk['text']}", ""]
            elif t == "warn":
                out += [f"> **Warning:** {blk['text']}", ""]
            elif t == "ul":
                out += [f"- {x}" for x in blk["items"]] + [""]
            elif t == "findings":
                out += [blk["title"], ""] + ([f"- {x}" for x in blk["lines"]] + [""] if blk["lines"] else [])
            elif t == "table":
                out += ["| " + " | ".join(md_cell(h) for h in blk["headers"]) + " |", "|" + "---|" * len(blk["headers"])]
                out += ["| " + " | ".join(md_cell(c) for c in row) + " |" for row in blk["rows"]] + [""]
        out += ["### Our notes", "", s["notes"] if s["notes"].strip() else "_No notes yet._", ""]
    return "\n".join(out).rstrip() + "\n"


# ---- small HTML helpers -------------------------------------------------------------------------------------------------------

def inline(text) -> str:
    """Escapes the text, then turns **bold** and `code` into real formatting, so no markdown characters reach the page."""
    out = E(str(text))
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", out)


# A chip is a small labelled pill. The colour says the same thing as the label (never instead of it), and each kind also
# differs in border style and glyph, so it still reads when printed in greyscale.
CHIP_KINDS = {
    "done": ("good", "✓"), "confirmed": ("good", "✓"), "fixed": ("good", "✓"), "frozen": ("good", "✓"), "received": ("good", "✓"),
    "not yet": ("wait", "○"), "unconfirmed": ("wait", "○"), "draft": ("wait", "○"), "open": ("wait", "○"), "not applicable": ("quiet", "–"),
    "critical": ("bad", "●"), "high": ("bad", "●"), "medium": ("mid", "◐"), "low": ("quiet", "◔"), "info": ("quiet", "◔"),
    "no longer applies": ("mid", "↷"), "new": ("info", "+"),
}


def chip(label: str, kind: str | None = None) -> str:
    """<span class='chip c-good'>✓ done</span>. `kind` defaults to the label's own entry in CHIP_KINDS, else a neutral chip."""
    colour, glyph = CHIP_KINDS.get(kind or label, ("plain", ""))
    text = f"{glyph} {label}".strip() if glyph else label
    return f"<span class='chip c-{colour}'>{E(text)}</span>"


STATUS_COLUMNS = {"Status", "Importance"}
LABEL_CHIP_COLUMNS = {"Type", "Length"}
FLAG_COLUMNS = {"Target": "target", "Spot check": "spot check"}
NUMBER = re.compile(r"^-?\d[\d,]*(\.\d+)?%?$")


def cell_html(header: str, value) -> str:
    text = str(value)
    if header in STATUS_COLUMNS and text.lower() in CHIP_KINDS:
        return chip(text.lower())
    if header == "Kind" and text:
        return chip(text, "plain")
    if header in LABEL_CHIP_COLUMNS and text:
        return chip(text, "plain")
    if header in FLAG_COLUMNS:
        return chip(FLAG_COLUMNS[header], "plain") if text == "yes" else "<span class='muted'>no</span>"
    return inline(text)


def table_html(blk: dict) -> str:
    heads, rows = blk["headers"], blk["rows"]
    numeric = [bool(rows) and all(NUMBER.match(str(r[i]).strip()) for r in rows) for i in range(len(heads))]
    head = "".join(f"<th scope='col'{' class=\"num\"' if numeric[i] else ''}>{inline(h)}</th>" for i, h in enumerate(heads))
    body = "".join("<tr>" + "".join(f"<td{' class=\"num\"' if numeric[i] else ''}>{cell_html(heads[i], c)}</td>" for i, c in enumerate(row)) + "</tr>"
                   for row in rows)
    keep = " class='keep'" if len(rows) <= KEEP_TABLE_ROWS else ""
    return f"<table{keep}><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def findings_html(blk: dict) -> str:
    """Outcome chip · plain sentence · code (small, muted). What replaced a removed finding is shown under it."""
    if not blk["rows"]:
        return f"<p class='sub-note'>{inline(blk['title'])}</p>"
    rows = []
    for r in blk["rows"]:
        extra = "".join(f"<div class='replaced'>replaced by: {inline(x['what'])} <span class='code'>{E(x['code'])}</span></div>" for x in r["replaced_by"])
        rows.append(f"<tr><td class='outcome'>{chip(r['outcome'])}</td><td>{inline(r['what'])}{extra}</td><td><span class='code'>{E(r['code'])}</span></td></tr>")
    head = "<th scope='col'>Outcome</th><th scope='col'>What it says</th><th scope='col'>Code</th>"
    keep = " class='keep'" if len(rows) <= KEEP_TABLE_ROWS else ""
    return f"<p class='sub-note'>{inline(blk['title'])}</p><table{keep}><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


# ---- the cover: every number comes from the section data -------------------------------------------------------------------------

def section_of(data: dict, key: str) -> dict:
    return next(s for s in data["sections"] if s["key"] == key)


def anchor(key: str) -> str:
    return f"s-{key}"


def cover_tiles(data: dict) -> list[dict]:
    """The six status tiles. Each: key, label, value (plain text), chips [(label, kind)], link (section key)."""
    ai = section_of(data, "how_ai_sees")["data"]
    stage = {i["item"]: i for i in section_of(data, "stage")["data"]["items"]}
    snap = section_of(data, "snapshot")["data"]
    qset = section_of(data, "questions")["data"]
    crawler = section_of(data, "crawler")["data"]

    if ai.get("steps"):
        c = ai["steps"][-1]["counts"]
        health = {"value": f"{c['fixed_count']} fixed · {c['no_longer_applies_count']} no longer apply · {c['still_open_count']} open · {c['new_count']} new",
                  "chips": [("fixed", None), ("no longer applies", None), ("open", None), ("new", None)],
                  "numbers": [c["fixed_count"], c["no_longer_applies_count"], c["still_open_count"], c["new_count"]]}
    elif ai.get("columns"):
        v = ai["columns"][-1]
        health = {"value": f"{v['findings']} finding(s) at one checkpoint; nothing to compare yet", "chips": [], "numbers": [v["findings"]]}
    else:
        health = {"value": "no diagnosis yet", "chips": [("not yet", None)], "numbers": []}

    bots = []
    if ai.get("columns"):
        bots = [v for k, v in ai["columns"][-1]["rows"].items() if k.endswith("bot)")]
    tested = [v for v in bots if v != "not tested"]
    blocked = [v for v in tested if v != "allowed"]
    if not ai.get("columns") or not tested:
        crawl_value, crawl_chip = "not tested", ("not yet", None)
    elif blocked:
        crawl_value, crawl_chip = f"{len(blocked)} of {len(tested)} AI bots blocked", ("high", None)
    else:
        crawl_value, crawl_chip = f"all {len(tested)} AI bots allowed", ("done", None)
    n_reports = len(crawler["reports"])
    crawl_value += f" · {n_reports} crawler report(s) on file"

    spot = stage["Spot check run"]
    facts = f"{snap['confirmed']} of {snap['total']} confirmed" if snap["total"] else "no truth document yet"
    state = (qset.get("set") or {}).get("state") or "none"
    audits = [i for i in stage.values() if i["item"].startswith("Audit: ")]
    done_audits = [i for i in audits if i["status"] == "done"]
    return [
        {"key": "website_health", "label": "Website health", "link": "how_ai_sees", **health},
        {"key": "crawler", "label": "AI crawler status", "link": "how_ai_sees", "value": crawl_value, "chips": [crawl_chip], "numbers": [n_reports]},
        {"key": "spot_check", "label": "Spot check", "link": "ai_says", "value": spot["detail"], "chips": [(spot["status"], None)], "numbers": []},
        {"key": "facts", "label": "Facts confirmed", "link": "snapshot", "value": facts,
         "chips": [("done" if snap["total"] and snap["confirmed"] == snap["total"] else "not yet", None)], "numbers": [snap["confirmed"], snap["total"]]},
        {"key": "question_set", "label": "Question set", "link": "questions",
         "value": (qset["set"]["version"] if qset.get("set") else "no question set yet"),
         "chips": [(state, None)] if state in ("draft", "frozen") else [("not yet", None)], "numbers": []},
        {"key": "audits", "label": "Audits", "link": "stage", "value": f"{len(done_audits)} done" if audits else "none yet",
         "chips": [("done" if done_audits else "not yet", None)], "numbers": [len(done_audits)]},
    ]


def top_problems(data: dict) -> list[dict]:
    return section_of(data, "problems")["data"]["items"][:3]


def next_step(data: dict) -> dict | None:
    steps = section_of(data, "next")["data"]["steps"]
    return steps[0] if steps else None


def engagement_of(data: dict) -> str:
    return section_of(data, "stage")["data"]["engagement"]["engagement_type"] or "not set"


def cover_html(data: dict) -> str:
    b = data["business"]
    tiles = "".join(
        f"<a class='tile' data-tile='{t['key']}' href='#{anchor(t['link'])}'><span class='tile-label'>{E(t['label'])}</span>"
        f"<span class='tile-value'>{E(t['value'])}</span><span class='tile-chips'>{''.join(chip(l, k) for l, k in t['chips'])}</span></a>"
        for t in cover_tiles(data))
    problems = top_problems(data)
    plist = ("<ol class='top3'>" + "".join(
        f"<li>{chip(i['severity'])} {chip(i['kind'], 'plain')} <span>{inline(i['text'])}</span></li>" for i in problems) + "</ol>") if problems \
        else "<p class='muted'>No problems found yet.</p>"
    step = next_step(data)
    nxt = (f"<p class='next'><strong>{E(step['step'])}</strong></p><p>{inline(step['why'])}</p>" if step
           else "<p class='next'><strong>Nothing is blocking.</strong></p><p>The baseline is complete.</p>")
    return (f"<section class='cover' id='cover'><div class='eyebrow'>Internal dossier · version {data['version']}</div>"
            f"<h1>{E(b['name'] or b['domain'])}</h1>"
            f"<p class='meta'>{E(b['domain'])} · status {chip(b['status'], 'plain')} · engagement {E(engagement_of(data))} · {E(data['generated_on'])} · v{data['version']}</p>"
            f"<div class='tiles'>{tiles}</div>"
            f"<div class='cover-cols'><div class='card'><h2 class='card-title'>Top 3 problems <a class='more' href='#{anchor('problems')}'>all problems</a></h2>{plist}</div>"
            f"<div class='card'><h2 class='card-title'>Next step <a class='more' href='#{anchor('next')}'>all steps</a></h2>{nxt}</div></div></section>")


# ---- index and sections ------------------------------------------------------------------------------------------------------------

def sub_anchor(key: str, n: int) -> str:
    return f"{anchor(key)}-{n}"


def subsections(s: dict) -> list[tuple[str, str]]:
    """(anchor, title) for each sub-section of a section: its "###" headings, then "Our notes"."""
    out = [(sub_anchor(s["key"], n), blk["text"]) for n, blk in enumerate((b for b in s["blocks"] if b["t"] == "h"), start=1)]
    return out + [(sub_anchor(s["key"], 0), "Our notes")]


def index_html(data: dict) -> str:
    items = []
    for n, s in enumerate(data["sections"], start=1):
        subs = "".join(f"<li><a href='#{a}'>{E(t)}</a></li>" for a, t in subsections(s))
        items.append(f"<li><a class='sec-link' href='#{anchor(s['key'])}'><span class='num'>{n}</span>{E(s['title'])}</a><ul>{subs}</ul></li>")
    return f"<nav id='index' class='index'><h2>Index</h2><ol>{''.join(items)}</ol></nav>"


def section_html(n: int, s: dict) -> str:
    parts, sub = [], 0
    for blk in s["blocks"]:
        t = blk["t"]
        if t == "h":
            sub += 1
            parts.append(f"<h3 id='{sub_anchor(s['key'], sub)}'>{inline(blk['text'])}</h3>")
        elif t == "p":
            parts.append(f"<p>{inline(blk['text'])}</p>")
        elif t == "warn":
            parts.append(f"<p class='warn'>{inline(blk['text'])}</p>")
        elif t == "ul":
            parts.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in blk["items"]) + "</ul>")
        elif t == "findings":
            parts.append(findings_html(blk))
        elif t == "table":
            parts.append(table_html(blk))
    text = s["notes"]
    notes = (f"<aside class='notes' id='{sub_anchor(s['key'], 0)}'><div class='notes-label'>Our notes <span>written by us, not data</span></div>"
             + (f"<div class='notes-text'>{E(text)}</div>" if text.strip() else "<div class='notes-text'><em>No notes yet.</em></div>") + "</aside>")
    return (f"<section class='sec' id='{anchor(s['key'])}'><h2><span class='num'>{n}</span>{E(s['title'])}"
            f"<a class='back' href='#index'>↑ index</a></h2><p class='answer'>{inline(s['answer'])}</p>{''.join(parts)}{notes}</section>")


CSS = """
:root{--accent:#17496b;--accent-soft:#e6eef4;--ink:#1c2430;--mid:#5b6573;--line:#d9dee5;--soft:#f4f6f9;--paper:#fff;--note:#fdf6e3;--note-line:#b8860b;
--t-title:30px;--t-section:21px;--t-sub:15px;--t-body:14px;--t-small:12px}
*{box-sizing:border-box}
body{margin:0;background:var(--soft);color:var(--ink);font:var(--t-body)/1.55 system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.page{max-width:900px;margin:0 auto;padding:28px 40px 40px;background:var(--paper)}
.internal{background:var(--ink);color:#fff;text-align:center;font-weight:700;letter-spacing:.12em;font-size:var(--t-small);padding:6px 10px;margin:0 0 22px}
.internal.running{display:none}
h1{font-size:var(--t-title);line-height:1.15;margin:4px 0 6px;color:var(--accent)}
h2{font-size:var(--t-section);line-height:1.25;margin:0 0 10px;color:var(--ink)}
h3{font-size:var(--t-sub);margin:22px 0 8px;color:var(--accent)}
p{margin:8px 0}
a{color:var(--accent)}
code{font:12px ui-monospace,Menlo,Consolas,monospace;background:var(--soft);padding:1px 4px;border-radius:3px}
.small,.muted{font-size:var(--t-small);color:var(--mid)}
.eyebrow{font-size:var(--t-small);letter-spacing:.14em;text-transform:uppercase;font-weight:700;color:var(--mid)}
.meta{color:var(--mid);margin:0 0 18px}
.cover{padding-bottom:22px}
.tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:0 0 18px}
.tile{display:flex;flex-direction:column;gap:6px;border:1px solid var(--line);border-top:4px solid var(--accent);border-radius:6px;padding:10px 12px;text-decoration:none;color:var(--ink);background:var(--paper)}
.tile-label{font-size:var(--t-small);text-transform:uppercase;letter-spacing:.08em;font-weight:700;color:var(--mid)}
.tile-value{font-size:15px;font-weight:600;line-height:1.35}
.tile-chips{display:flex;flex-wrap:wrap;gap:4px}
.cover-cols{display:grid;grid-template-columns:3fr 2fr;gap:12px}
.card{border:1px solid var(--line);border-radius:6px;padding:12px 16px;background:var(--paper)}
.card-title{font-size:var(--t-sub);margin:0 0 8px;display:flex;justify-content:space-between;align-items:baseline}
.more{font-size:var(--t-small);font-weight:400}
.top3{margin:0;padding-left:20px}.top3 li{margin:6px 0}
.next{font-size:16px}
.index{border:1px solid var(--line);border-radius:6px;padding:14px 22px;margin:22px 0 8px;background:var(--soft)}
.index ol{margin:0;padding-left:0;list-style:none;columns:2;column-gap:36px}
.index>ol>li{margin:0 0 10px;break-inside:avoid}
.index ul{margin:2px 0 0;padding-left:34px;font-size:var(--t-small)}
.index a{text-decoration:none}.index a:hover{text-decoration:underline}
.sec-link{font-weight:700}
.num{display:inline-block;min-width:1.9em;color:var(--accent);font-weight:700}
.sec{margin-top:34px;padding-top:14px;border-top:3px solid var(--accent)}
.sec h2 .num{font-size:var(--t-section)}
.back{float:right;font-size:var(--t-small);font-weight:400;text-decoration:none;color:var(--mid)}
.answer{background:var(--accent-soft);border-left:5px solid var(--accent);border-radius:0 6px 6px 0;padding:10px 16px;font-size:16px;font-weight:600;margin:6px 0 12px}
.sub-note{font-weight:600;margin:14px 0 6px}
.warn{border:2px solid var(--ink);border-left-width:8px;padding:8px 12px;margin:10px 0;font-weight:600;background:var(--paper)}
table{border-collapse:collapse;width:100%;margin:8px 0 14px;font-size:13px}
th,td{border-bottom:1px solid var(--line);padding:6px 9px;text-align:left;vertical-align:top;white-space:pre-wrap}
th{background:var(--accent);color:#fff;font-weight:700;border-bottom:0}
tbody tr:nth-child(even){background:var(--soft)}
td.num,th.num{text-align:right;white-space:nowrap}
td.outcome{white-space:nowrap}
.code{font:11.5px ui-monospace,Menlo,Consolas,monospace;color:var(--mid)}
.replaced{font-size:var(--t-small);color:var(--mid);margin-top:3px}
ul{margin:8px 0;padding-left:22px}li{margin:3px 0}
.chip{display:inline-block;padding:0 8px;border-radius:999px;font-size:11.5px;font-weight:700;line-height:1.7;border:1.5px solid;white-space:nowrap}
.c-good{color:#14532d;background:#dcfce7;border-color:#14532d}
.c-wait{color:#78350f;background:#fef3c7;border-color:#78350f;border-style:dashed}
.c-bad{color:#7f1d1d;background:#fee2e2;border-color:#7f1d1d;border-width:2.5px}
.c-mid{color:#7c2d12;background:#ffedd5;border-color:#7c2d12;border-style:dotted;border-width:2px}
.c-info{color:#1e3a8a;background:#dbeafe;border-color:#1e3a8a}
.c-quiet{color:var(--mid);background:var(--soft);border-color:var(--mid);font-weight:600}
.c-plain{color:var(--ink);background:var(--paper);border-color:var(--line)}
.notes{border-left:6px solid var(--note-line);background:var(--note);padding:8px 16px 10px;margin:18px 0 4px;border-radius:0 6px 6px 0}
.notes-label{font-size:var(--t-small);font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--note-line)}
.notes-label span{font-weight:400;text-transform:none;letter-spacing:0;color:var(--mid);margin-left:8px}
.notes-text{white-space:pre-wrap;margin-top:4px}
footer{margin-top:34px;padding-top:12px;border-top:1px solid var(--line);font-size:var(--t-small);color:var(--mid)}
@media (max-width:700px){.tiles{grid-template-columns:1fr 1fr}.cover-cols{grid-template-columns:1fr}.index ol{columns:1}.page{padding:20px 16px}}
@media print{
 body{background:#fff;font-size:10.5pt}.page{max-width:none;padding:0}
 h1,h2,h3{break-after:avoid;page-break-after:avoid}
 .answer{break-after:avoid;page-break-after:avoid}
 p.sub-note{break-after:avoid;page-break-after:avoid}
 .card,.tile,.warn,.notes,.index>ol>li,li{break-inside:avoid;page-break-inside:avoid}
 table{break-inside:auto;page-break-inside:auto}
 table.keep{break-inside:avoid;page-break-inside:avoid}
 tr{break-inside:avoid;page-break-inside:avoid}
 thead{display:table-header-group}
 .internal.running{display:block;position:fixed;top:-9mm;left:0;right:0;margin:0;padding:2px;font-size:8.5pt}
 .internal.banner{display:none}
 .back{display:none}
 .tile{border-top-width:4px}
 *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
}
"""


def css_string(text: str) -> str:
    """A value safe to put inside a CSS "..." string."""
    return '"' + re.sub(r"[\\\"\n\r]", " ", str(text)) + '"'


def page_rule(data: dict) -> str:
    """Print header and footer (page margin boxes): client name and version at the top; INTERNAL, the version and the page
    number at the bottom. Browsers that do not draw margin boxes still show the INTERNAL bar and the headings."""
    b = data["business"]
    name = b["name"] or b["domain"]
    return ("@page{size:A4;margin:24mm 14mm 16mm;"
            f"@top-left{{vertical-align:top;content:{css_string(name + ' · dossier')};font:9pt system-ui,sans-serif;color:#5b6573}}"
            f"@top-right{{vertical-align:top;content:{css_string('v' + str(data['version']) + ' · ' + data['generated_on'])};font:9pt system-ui,sans-serif;color:#5b6573}}"
            f"@bottom-left{{content:{css_string(INTERNAL_TEXT + ' · v' + str(data['version']))};font:bold 9pt system-ui,sans-serif;color:#1c2430}}"
            "@bottom-right{content:\"Page \" counter(page) \" of \" counter(pages);font:9pt system-ui,sans-serif;color:#5b6573}}")


def render_html(data: dict) -> str:
    b = data["business"]
    title = f"{b['name'] or b['domain']}: dossier v{data['version']}"
    warnings = "".join(f"<p class='warn'>Notes file: {E(w)}</p>" for w in data["notes_warnings"])
    sections = "".join(section_html(n, s) for n, s in enumerate(data["sections"], start=1))
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{E(title)}</title><style>{CSS}{page_rule(data)}</style></head><body>"
            f"<div class=\"internal running\">{E(INTERNAL_TEXT)}</div><div class=\"page\"><div class=\"internal banner\">{E(INTERNAL_TEXT)}</div>"
            f"{cover_html(data)}{warnings}{index_html(data)}{sections}"
            "<footer><p>Internal baseline document. Made from the database, the client's files and dossier_notes.md. Never send this to the client.</p></footer>"
            "</div></body></html>\n")
