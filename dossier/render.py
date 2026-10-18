"""The dossier as markdown and as one self-contained HTML file, both made from the same data (the .json file).

The HTML uses the client report's look and print rules, plus INTERNAL on every printed page. Nothing external: no scripts,
links, fonts or images. Deterministic: the same data gives the same bytes.
"""

from __future__ import annotations

from html import escape as E

from client_report.render import CSS as CLIENT_CSS

EXTRA_CSS = """
.internal{background:#1b1b1b;color:#fff;text-align:center;font-weight:700;letter-spacing:.12em;padding:6px 10px;margin:0 0 18px}
.internal.running{display:none}
.answer{font-size:16px;font-weight:600;margin:6px 0 10px}
.notes{border-left:5px solid var(--ink);background:var(--soft);padding:6px 14px;margin:12px 0;white-space:pre-wrap}
.notes h3{margin:0 0 4px;font-size:13px;text-transform:uppercase;letter-spacing:.06em}
td,th{white-space:pre-wrap}
@media print{
 .internal.running{display:block;position:fixed;top:0;left:0;right:0;margin:0;padding:3px;font-size:9pt}
 .internal.banner{display:none}
 .page{padding-top:8mm}
}
"""
CSS = CLIENT_CSS + EXTRA_CSS
INTERNAL_TEXT = "INTERNAL: not for the client"


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
            elif t == "table":
                out += ["| " + " | ".join(md_cell(h) for h in blk["headers"]) + " |", "|" + "---|" * len(blk["headers"])]
                out += ["| " + " | ".join(md_cell(c) for c in row) + " |" for row in blk["rows"]] + [""]
        out += ["### Our notes", "", s["notes"] if s["notes"].strip() else "_No notes yet._", ""]
    return "\n".join(out).rstrip() + "\n"


def _html_block(blk: dict) -> str:
    t = blk["t"]
    if t == "p":
        return f"<p>{E(blk['text'])}</p>"
    if t == "h":
        return f"<h3>{E(blk['text'])}</h3>"
    if t == "warn":
        return f"<p class='warn'>{E(blk['text'])}</p>"
    if t == "ul":
        return "<ul>" + "".join(f"<li>{E(x)}</li>" for x in blk["items"]) + "</ul>"
    head = "".join(f"<th>{E(h)}</th>" for h in blk["headers"])
    body = "".join("<tr>" + "".join(f"<td>{E(str(c))}</td>" for c in row) + "</tr>" for row in blk["rows"])
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_html(data: dict) -> str:
    b = data["business"]
    title = f"{b['name'] or b['domain']}: dossier v{data['version']}"
    parts = []
    for n, s in enumerate(data["sections"], start=1):
        notes = (f"<div class='notes'><h3>Our notes</h3>{E(s['notes'])}</div>" if s["notes"].strip()
                 else "<div class='notes'><h3>Our notes</h3><em>No notes yet.</em></div>")
        parts.append(f"<section class='sec' id='{E(s['key'])}'><h2>{n}. {E(s['title'])}</h2><p class='answer'>{E(s['answer'])}</p>"
                     + "".join(_html_block(x) for x in s["blocks"]) + notes + "</section>")
    warnings = "".join(f"<p class='warn'>Notes file: {E(w)}</p>" for w in data["notes_warnings"])
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{E(title)}</title><style>{CSS}</style></head><body><div class=\"internal running\">{E(INTERNAL_TEXT)}</div><div class=\"page\">"
            f"<div class=\"internal banner\">{E(INTERNAL_TEXT)}</div>"
            f"<header class=\"top\"><div class=\"brand\">AEO Studio · dossier</div><h1>{E(b['name'] or b['domain'])}</h1>"
            f"<dl class=\"meta\"><dt>Domain</dt><dt>Status</dt><dt>Date</dt><dt>Version</dt><dd>{E(b['domain'])}</dd><dd>{E(b['status'])}</dd>"
            f"<dd>{E(data['generated_on'])}</dd><dd>v{data['version']}</dd></dl></header>{warnings}" + "".join(parts)
            + "<footer><p>Internal baseline document. Made from the database, the client's files and dossier_notes.md. Never send this to the client.</p></footer>"
            "</div></body></html>\n")
