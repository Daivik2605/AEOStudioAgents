"""Writes the diagnosis report: diagnosis.md and diagnosis.json under
reports/<business-slug>/<checkpoint>/ (PLAN.md section 10).

Reports are kept forever: every run is written as a numbered, dated copy
(diagnosis_run2_2026-10-05.md), plus an overwritten "latest" copy with no number.
See core/report_files.py.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from core.report_files import write_run_files
from diagnose import RULES_VERSION
from diagnose import platform_data as pd
from diagnose.codes import SEVERITIES
from diagnose.crawler import BOTS
from diagnose.facts import Facts
from diagnose.rules import Verdict

REPORTS_ROOT = Path(__file__).parent.parent / "reports"

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


def slugify_domain(value: str) -> str:
    """'https://www.Example.com/x' -> 'example.com'"""
    host = urlsplit(value if "//" in value else f"//{value}").hostname or value
    host = host.lower().removeprefix("www.")
    return re.sub(r"[^a-z0-9.-]+", "-", host).strip("-.") or "unknown-site"


def safe_checkpoint(checkpoint: str | None) -> str:
    """A folder name from free text. Slashes and dots-only names can never escape reports/."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (checkpoint or "").strip()).strip("-.")
    return cleaned or "unspecified"


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
        counts = {s: sum(1 for f in data["findings"] if f["severity"] == s) for s in SEVERITIES}
        lines += [", ".join(f"{n} {s}" for s, n in counts.items() if n), ""]
        for f in data["findings"]:
            fix = {True: "yes", False: "no", None: "unknown"}[f["fixable_on_platform"]]
            lines += [f"### `{f['code']}` — {f['severity']}", "", f["what"], "",
                      f"- Evidence: {f['evidence']}", f"- Fixable on this platform: {fix}",
                      f"- Where to fix: {f['where_to_fix']}", f"- Source: {f['source']}", ""]
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


def write_report(*, data: dict, slug: str, checkpoint: str | None, day: date, raw_page: bytes | None = None,
                 root: Path = REPORTS_ROOT) -> tuple[Path, int]:
    """Writes diagnosis.md, diagnosis.json and (if there was a page) raw_page.html, numbered and as latest.
    Returns (folder, run number)."""
    folder = root / slug / safe_checkpoint(checkpoint)
    files: dict[str, str | bytes] = {
        "diagnosis.md": build_markdown(data),
        "diagnosis.json": json.dumps(data, indent=2, ensure_ascii=False),
    }
    if raw_page:
        files["raw_page.html"] = raw_page
    return folder, write_run_files(folder, files, anchor="diagnosis.md", day=day)
