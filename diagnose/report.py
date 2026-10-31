"""Writes the diagnosis report (PLAN.md section 10):

    clients/<domain>/generated/<date>_diagnosis_run<N>.md and .json     the report (tool output)
    clients/<domain>/evidence/<date>_raw_page_run<N>.html               the page as fetched (came in, kept forever)
    clients/<domain>/evidence/<date>_old_urls_run<N>.json               every old address checked, only when --old-urls was used

Every run gets its own numbered, dated files. There is no "latest" copy. See core/client_files.py.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from core import client_files
from core.client_files import slugify_domain  # noqa: F401 - re-exported: callers import it from here
from diagnose import RULES_VERSION
from diagnose import platform_data as pd
from diagnose.codes import SEVERITIES, explanation_for
from diagnose.crawler import BOTS
from diagnose.display import group_findings
from diagnose.facts import Facts
from diagnose.rules import Verdict

# What this check can and cannot tell you. Printed in every report.
LIMITS = [
    "The crawler test only probes the user-agent layer. Cloudflare and Vercel also verify real bots by IP "
    "address and signature (Web Bot Auth), so a site can admit the real OAI-SearchBot and refuse our look-alike. "
    "Read these results as indicative, and cross-check them against robots.txt.",
    "The render check reads the raw HTML without running JavaScript. 'Not in the server HTML' is what it "
    "measures, not 'no AI can read this': Google renders JavaScript, so Google's AI surfaces may see more.",
    "A plan tier (for example Squarespace Basic vs Core) usually cannot be seen from outside. Where we could "
    "not see it, the report says so instead of guessing.",
]


def decline_pitch_outline(label: str, reason: str) -> list[str]:
    """What a migration pitch WOULD cover. Printed only; the pitch itself is never written
    (a person types `yes` first, PLAN.md sections 5 and 12). Every point comes from PLAN.md."""
    return [
        f"Why the current site cannot carry the work: {reason}",
        "What moving would involve: choosing a platform that can (PLAN.md §5 lists them), rebuilding the pages, "
        "redirecting every old address, and keeping the domain.",
        "Most of these fixes also help ordinary search ranking, which widens the case for moving (PLAN.md §2).",
        "Cost and timing: to be scoped and priced separately. Nothing is quoted here.",
        "What we cannot promise: that any AI assistant will name the business. Nobody can (PLAN.md §6).",
        "Before anything changes: record the old site's state now, because once the new site is live it is gone (PLAN.md §10).",
    ]


def build_json(*, facts: Facts, verdict: Verdict, findings: list[dict], readiness: float | None,
               checkpoint: str | None, checked_at: datetime, ai_explanation: str | None,
               requests_made: int) -> dict:
    det = facts.detection
    return {
        "tool": {"name": "aeo diagnose", "rules_version": RULES_VERSION},
        "url": facts.url,
        "final_url": facts.page.final_url,
        "business": ({"id": facts.business["id"], "name": facts.business["name"], "domain": facts.business["domain"]}
                     if facts.business else None),
        "checkpoint": checkpoint,
        "checked_at": checked_at.isoformat(),
        "verdict": verdict.verdict,
        "verdict_reason": verdict.reason,
        "readiness_score": readiness,
        "readiness_basis": pd.READINESS_SOURCE,
        "ai_explanation": ai_explanation,
        "platform": {
            "name": det.platform, "label": pd.PLATFORM_LABELS.get(det.platform or ""),
            "version": det.version, "plan": det.plan, "plan_basis": det.plan_basis,
            "signals": det.hits, "candidates": det.candidates, "hosting": det.hosting, "notes": det.notes,
        },
        # Information, not findings: what is in front of and under the site, from the response headers (step 25).
        "infrastructure": facts.infrastructure.to_json() if facts.infrastructure else None,
        "site_checks": facts.site_checks.to_json() if facts.site_checks else None,
        "crawler_access": facts.crawler.to_json() if facts.crawler else None,
        "robots": facts.robots.to_json() if facts.robots else None,
        "robots_txt_raw": facts.robots.raw if facts.robots else None,
        "llms_txt": ({"status": facts.llms.status, "final_url": facts.llms.final_url} if facts.llms else None),
        "render": facts.render.to_json() if facts.render else None,
        "existing_jsonld": facts.jsonld_raw,
        "schema_blocks": facts.schema.blocks if facts.schema else None,
        "findings": findings,
        "requests_made": requests_made,
        "limits": LIMITS,
    }


def build_markdown(data: dict) -> str:
    v = data["verdict"]
    banner = {"serve": "SERVE", "conditional": "CONDITIONAL", "decline": "DECLINE"}[v]
    lines = [f"# Diagnosis: {data['url']}", ""]
    meta = [f"Checked {data['checked_at']}"]
    if data["checkpoint"]:
        meta.append(f"checkpoint `{data['checkpoint']}`")
    if data["business"]:
        meta.append(f"business: {data['business']['name'] or data['business']['domain']}")
    lines += [" · ".join(meta), ""]
    lines += [f"## Verdict: {banner}", "", data["verdict_reason"], ""]
    if data["ai_explanation"]:
        lines += ["### In plain words", "", data["ai_explanation"],
                  "", "_Written by a model from the verdict above. It did not decide the verdict._", ""]
    else:
        lines += ["_No model-written summary (one is added only when a business is given). Read the findings below._", ""]
    if data["readiness_score"] is not None:
        lines += [f"Readiness: **{data['readiness_score']:g}%** of a standard fix list can be delivered on this platform "
                  f"({data['readiness_basis']}).", ""]

    p = data["platform"]
    lines += ["## Platform", ""]
    if p["name"]:
        extra = ", ".join(x for x in [f"version {p['version']}" if p["version"] else "",
                                      f"plan {p['plan']}" if p["plan"] else ""] if x)
        lines.append(f"**{p['label']}**" + (f" ({extra})" if extra else ""))
        if p["plan_basis"]:
            lines.append(f"- Plan: {p['plan_basis']}")
        for s in p["signals"]:
            lines.append(f"- Tier {s['tier']}: {s['signal']} — {s['evidence']}")
    else:
        lines.append("No platform recognised.")
    if p["hosting"]:
        lines.append(f"- Hosting / edge layers seen: {', '.join(p['hosting'])}")
    lines += [f"- Note: {n}" for n in p["notes"]]
    lines.append("")

    infra = data.get("infrastructure")
    lines += ["## Site setup (from the response headers)", ""]
    if infra:
        for kind, label in (("cdn", "CDN"), ("host", "Host"), ("framework", "Framework")):
            lines.append(f"- {label}: {infra[kind] or 'unknown'}" + (f" ({infra['evidence'][kind]})" if infra[kind] else ""))
        lines.append("- This is information, not a finding. It says where a fix would be made.")
    else:
        lines.append("Not run: the site did not return a page.")
    lines.append("")

    lines += site_checks_markdown(data.get("site_checks"))
    lines += not_checked_markdown(data.get("site_checks"))

    c = data["crawler_access"]
    lines += ["## Can AI crawlers reach the page?", ""]
    if c:
        ctl = c["control"]
        lines += [f"Browser control: HTTP {ctl['status']}, {ctl['size']:,} bytes.", "",
                  "| Bot | Kind | HTTP | Result | Detail |", "|---|---|---|---|---|"]
        for name, b in c["bots"].items():
            lines.append(f"| {name} | {b['kind']} | {b['status']} | **{b['verdict'].replace('_', ' ')}** | {b['detail']} |")
        if c["stopped_early"]:
            lines.append(f"\nThe test stopped early: {c['stopped_early']}.")
    else:
        lines.append("Not run: the site did not return a page.")
    lines.append("")

    r = data["robots"]
    lines += ["## robots.txt", ""]
    if r:
        lines.append(f"Fetched as a bot from {r['url']}: HTTP {r['status']} ({r['outcome']}).")
        if r["browser_status"] is not None:
            lines.append(f"Asked again as a browser: HTTP {r['browser_status']}.")
        if r["outcome"] in ("ok", "missing", "unreachable", "challenged"):
            lines.append("May fetch the page: " + ", ".join(f"{t} {'yes' if ok else 'NO'}" for t, ok in r["allowed"].items()))
        if r["content_signal"]:
            lines.append(f"Content-Signal: `{r['content_signal']}` (from the {r['content_signal_source']}). "
                         "This is a preference, not access control.")
        lines += [f"- {n}" for n in r["notes"]]
    lines.append("")

    rc = data["render"]
    lines += ["## Is the content in the no-JavaScript HTML?", ""]
    if rc:
        lines += [f"- Readable words (main content): {rc['word_count']} ({rc['word_count_total']} on the whole page)",
                  f"- `<h1>` headings: {rc['h1_texts']!r}",
                  f"- JSON-LD blocks: {rc['jsonld_block_count']}",
                  f"- Client-rendering test: **{rc['verdict']}**" + (f" — {'; '.join(rc['reasons'])}" if rc["reasons"] else "")]
        for key, d in rc["details"].items():
            if d.get("checked"):
                where = "yes" if d["in_readable_text"] else ("raw HTML only" if d["in_raw_html"] else "NO")
                lines.append(f"- {key} in readable text: {where}")
            else:
                lines.append(f"- {key}: not checked (not known for this business)")
    else:
        lines.append("Not run: the site did not return a page.")
    lines.append("")

    lines += ["## Findings", ""]
    if data["findings"]:
        grouped = group_findings(data["findings"])      # one entry per code; the stored findings are not changed
        counts = {s: sum(1 for g in grouped if g["severity"] == s) for s in SEVERITIES}
        repeats = len(data["findings"]) - len(grouped)
        lines += [", ".join(f"{n} {s}" for s, n in counts.items() if n)
                  + (f" ({len(data['findings'])} counting repeats)" if repeats else ""), ""]
        for g in grouped:
            f = g["findings"][0]
            fix = {True: "yes", False: "no", None: "unknown"}[f["fixable_on_platform"]]
            lines += [f"### `{g['code']}` — {g['severity']}" + (" — unconfirmed" if g["unconfirmed"] else ""), "", g["what"], ""]
            lines += [f"- Example: {e}" for e in g["examples"]] + ([""] if g["examples"] else [])
            if explanation_for(f["code"]):
                lines += [f"Why it matters: {explanation_for(f['code'])}", ""]
            lines += ([] if g["examples"] else [f"- Evidence: {f['evidence']}"]) + [
                      f"- Fixable on this platform: {fix}", f"- Where to fix: {f['where_to_fix']}", f"- Source: {f['source']}", ""]
    else:
        lines += ["No findings.", ""]

    if v == "decline":
        label = p["label"] or "this platform"
        lines += ["## If a migration pitch is approved, it would cover", ""]
        lines += [f"- {x}" for x in decline_pitch_outline(label, data["verdict_reason"])]
        lines += ["", "No pitch has been written. A person must type `yes` first.", ""]

    lines += ["## What this check cannot tell you", ""] + [f"- {x}" for x in data["limits"]]
    lines += ["", f"_{data['requests_made']} requests made. Rules version {data['tool']['rules_version']}._", ""]
    return "\n".join(lines)


def site_checks_markdown(sc: dict | None) -> list[str]:
    """The sitemap, www / http and old-address results, as the plain facts that were recorded."""
    lines = ["## Sitemap and addresses", ""]
    if not sc:
        return lines + ["Not run: the site did not return a page.", ""]
    s, h, o = sc.get("sitemap"), sc.get("hosts"), sc.get("old_urls")
    if s:
        state = ("valid, " + f"{s['urls']} address(es)") if s["valid"] else ("found but not usable" if s["found"] else "not found")
        listed = {True: "listed in robots.txt", False: "NOT listed in robots.txt", None: "robots.txt not checked"}[s["in_robots"]] \
            if s["valid"] else None
        lines.append(f"- Sitemap: {state}" + (f"; {listed}" if listed else ""))
        for r in s["fetched"]:
            lines.append(f"  - {'child: ' if r['child'] else ''}{r['url']} → " + (f"HTTP {r['status']}" if r["status"] else f"no answer ({r['error']})")
                         + (f", {r['kind'].replace('_', ' ')}" if r["kind"] else ""))
        if s["valid"]:
            lines.append(f"- Addresses in the sitemap use: {', '.join(f'{h_} ({n})' for h_, n in s['hosts_in_sitemap'].items())}"
                         + ("" if not s["mismatched_urls"] else f" — {s['mismatched_urls']} on a different form than {s['canonical_host']}"))
        if s["children_not_followed"]:
            lines.append(f"- {s['children_not_followed']} child sitemap(s) were not followed (limit of 10).")
    if h:
        for key in ("apex", "www"):
            r = h[key]
            what = (f"HTTP {r['status']}" + (f" → {r['location']}" if r["location"] else "")) if r["status"] else \
                   ("no such host" if r["dns_failure"] else f"no answer ({r['error']})")
            lines.append(f"- {r['host']}: {what}")
        ht = h["http"]
        lines.append("- Plain http: " + (f"ends at {ht['final_url']} (HTTP {ht['status']})" if ht["status"] else
                                         f"no answer ({ht['error']})"))
    if o:
        lines.append(f"- Old addresses: {o['checked']} checked; " + ", ".join(f"{n} {c.lower().replace('_', ' ')}" for c, n in o["counts"].items()))
    lines.append("")
    return lines


def not_checked_markdown(sc: dict | None) -> list[str]:
    """What we did NOT check and why. Findings that depend on these are never raised, so their absence means 'unknown'."""
    items = (sc or {}).get("not_checked") or []
    if not items:
        return []
    lines = ["## Not checked", "",
             "These could not be checked, so no finding about them is raised. Their absence below means unknown, not fine.", ""]
    for n in items:
        lines.append(f"- **{n['label']}**: {n['reason']}.")
    lines.append("")
    return lines


def write_report(*, data: dict, slug: str, day: date, raw_page: bytes | None = None,
                 root: Path | None = None, old_urls: dict | None = None) -> tuple[Path, int, Path]:
    """Writes the report (md + json) to generated/ and, if there was a page, raw_page to evidence/.
    Returns (generated folder, run number, path of the .md report).

    The raw page takes the same run number as the report, so the two can be matched by eye."""
    generated = client_files.generated_dir(slug, root)
    run, paths = client_files.write_generated(
        generated, kind="diagnosis", anchor_ext="md", day=day,
        files={"md": build_markdown(data), "json": json.dumps(data, indent=2, ensure_ascii=False)})
    if raw_page:
        client_files.write_new(client_files.evidence_dir(slug, root),
                               client_files.dated_name(day, "raw_page", "html", run), raw_page)
    if old_urls is not None:      # every address and what happened to it; the report and database hold only examples
        client_files.write_new(client_files.evidence_dir(slug, root),
                               client_files.dated_name(day, "old_urls", "json", run),
                               json.dumps(old_urls, indent=2, ensure_ascii=False).encode("utf-8"))
    return generated, run, paths["md"]
