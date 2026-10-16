"""The fix list, in the order PLAN.md step 19 sets. The order comes from the data, not from instinct:

  1. Access blockers      a search bot blocked (robots.txt or the CDN), critical rendering problems
  2. Wrong facts          what the audit found AI saying that contradicts a confirmed fact
  3. Target questions     questions we aimed at where competitors are named and the client is not
  4. Remaining findings    every other open finding of the diagnosis, by severity
  5. Hygiene              schema.json and llms.txt, last, labelled "cheap, unproven"

and, outside the list, the off-site domains AI cited where the client was absent.
No AI. The page for a target question is NOT written here; the fix only says what it has to beat.
"""

from __future__ import annotations

from collections import Counter

from diagnose.crawler import BOTS
from recommend import routing

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

RETRIEVAL_SUFFIXES = {b.code_suffix for b in BOTS if b.kind == "retrieval"}
ALWAYS_ACCESS = {"ROBOTS_TXT_BOT_FETCH_FAILED", "CONTENT_SIGNAL_AI_INPUT_NO", "RENDER_CONTENT_MISSING"}
HYGIENE_SOURCE = "research/AEO_PLAYBOOK.md §3 (P2): the weakest levers in the evidence"
TOP_SOURCES, TOP_COMPETITORS, TOP_REASONS, TOP_OFFSITE = 8, 5, 6, 15


def cdn_from_findings(findings: list[dict]) -> str | None:
    """The diagnosis does not store the CDN as its own field; its findings say it. Returns 'wp_engine', 'cloudflare' or None."""
    codes = {f["code"] for f in findings}
    if "HOST_WP_ENGINE_DETECTED" in codes:
        return "wp_engine"
    if any(str(f.get("where_to_fix", "")).startswith("Cloudflare dashboard") for f in findings):
        return "cloudflare"
    return None


def is_access_blocker(f: dict) -> bool:
    code = f["code"]
    for prefix in ("CRAWLER_BLOCKED_", "ROBOTS_DISALLOWS_"):
        if code.startswith(prefix) and code[len(prefix):] in RETRIEVAL_SUFFIXES:
            return True
    return code in ALWAYS_ACCESS or f["severity"] == "critical"


def _route_from_finding(f: dict) -> dict:
    """For a finding with no routing of its own: what the diagnosis already said about where it is fixed."""
    fixable = f.get("fixable_on_platform")
    status = routing.CAN if fixable is True else routing.CANNOT if fixable is False else routing.UNKNOWN
    return {"status": status, "where": f.get("where_to_fix") or "", "by_hand": False,
            "alternative": f.get("where_to_fix") if fixable is False else None,
            "source": f.get("source") or ""}


def _route_access(f: dict, platform: str | None, plan: str | None, cdn: str | None) -> tuple[dict, str | None]:
    """(route, generated file the fix uses)."""
    code = f["code"]
    if code.startswith("ROBOTS_DISALLOWS_") or code == "CONTENT_SIGNAL_AI_INPUT_NO":
        return routing.route(platform, plan, "robots"), "robots"
    if code.startswith("CRAWLER_BLOCKED_") or code == "ROBOTS_TXT_BOT_FETCH_FAILED":
        return routing.edge_route(cdn), None
    return _route_from_finding(f), None


def _fix(section: str, title: str, *, finding_code: str | None, severity: str, what: str, route: dict,
         file: str | None = None, **extra) -> dict:
    return {"id": None, "section": section, "title": title, "finding_code": finding_code, "also_closes": [],
            "severity": severity, "what": what, "route": route, "cannot_do": route["status"] == routing.CANNOT,
            "file": file, **extra}


def _by_severity(findings: list[dict]) -> list[dict]:
    return sorted(findings, key=lambda f: (SEVERITY_RANK.get(f["severity"], 9), f["code"]))


# ---- 1 and 4: from the diagnosis ------------------------------------------------------------------------------------------

def access_fixes(diagnosis: dict, cdn: str | None) -> list[dict]:
    out = []
    for f in _by_severity([f for f in diagnosis["findings"] if is_access_blocker(f)]):
        route, file = _route_access(f, diagnosis["platform"], diagnosis["platform_plan"], cdn)
        out.append(_fix("access", f["what"], finding_code=f["code"], severity=f["severity"], what=f["what"],
                        route=route, file=file, evidence=f.get("evidence")))
    return out


def remaining_fixes(diagnosis: dict, covered: set[str]) -> list[dict]:
    out = []
    for f in _by_severity([f for f in diagnosis["findings"] if f["code"] not in covered and not is_access_blocker(f)]):
        out.append(_fix("finding", f["what"], finding_code=f["code"], severity=f["severity"], what=f["what"],
                        route=_route_from_finding(f), evidence=f.get("evidence")))
    return out


# ---- 2, 3 and the off-site list: from the audit ---------------------------------------------------------------------------

def _domains(answer: dict) -> set[str]:
    return {l["domain"] for l in (answer["sources"] or []) if l.get("domain")}


def _is_client_domain(domain: str, client: str) -> bool:
    return domain == client or domain.endswith("." + client)


def wrong_fact_fixes(audit: dict, current_facts: list[dict], client_domain: str, route: dict) -> list[dict]:
    """One fix per distinct wrong statement, most often repeated first."""
    now = {f["field"].casefold(): f["value"] for f in current_facts}
    then = {f["field"].casefold(): f["value"] for f in audit["run_facts"]}
    groups: dict[tuple, dict] = {}
    order = {a["id"]: i for i, a in enumerate(sorted(audit["answers"], key=lambda a: (a["question"], a["engine"], a["repeat"], a["id"])))}
    for a in sorted(audit["answers"], key=lambda a: order[a["id"]]):
        for c in a["claims"] or []:
            if c["verdict"] != "incorrect":
                continue
            key = ((c.get("field") or "").casefold(), " ".join(c["claim"].lower().split()))
            g = groups.setdefault(key, {"claim": c["claim"], "note": c.get("note"), "field": c.get("field"),
                                        "answers": 0, "engines": set(), "sources": Counter(), "client_urls": Counter()})
            g["answers"] += 1
            g["engines"].add(a["engine"])
            for l in a["sources"] or []:
                if l.get("domain"):
                    g["sources"][l["domain"]] += 1
                    if _is_client_domain(l["domain"], client_domain):
                        g["client_urls"][l["url"]] += 1
    ranked = sorted(groups.values(), key=lambda g: (-g["answers"], (g["field"] or "").casefold(), g["claim"].casefold()))
    out = []
    for g in ranked:
        fk = (g["field"] or "").casefold()
        confirmed = now.get(fk) or then.get(fk)
        third_party = [(d, n) for d, n in g["sources"].items() if not _is_client_domain(d, client_domain)]
        third_party.sort(key=lambda kv: (-kv[1], kv[0]))
        out.append(_fix(
            "wrong_fact", f"AI says: \"{g['claim']}\"", finding_code="AUDIT_WRONG_FACT", severity="high",
            what="An AI answer states something that contradicts a confirmed fact.", route=route,
            quote=g["claim"], why_wrong=g["note"], fact_label=g["field"], confirmed_fact=confirmed,
            fact_changed_since_run=bool(fk in now and fk in then and now[fk] != then[fk]),
            answers=g["answers"], engines=sorted(g["engines"]),
            likely_sources=[{"domain": d, "answers": n} for d, n in third_party[:TOP_SOURCES]],
            client_pages_cited=[{"url": u, "answers": n} for u, n in
                                sorted(g["client_urls"].items(), key=lambda kv: (-kv[1], kv[0]))[:5]]))
    return out


def target_question_fixes(audit: dict, client_domain: str, route: dict) -> list[dict]:
    out = []
    for q in audit["targets"]:
        mine = [a for a in audit["answers"] if a["question"] == q]
        gap = [a for a in mine if any(not b.get("is_client") for b in a["named"]) and not any(b.get("is_client") for b in a["named"])]
        if not gap:
            continue
        sources, rivals, reasons, client_urls = Counter(), Counter(), Counter(), Counter()
        reason_text: dict[str, str] = {}
        for a in mine:
            for d in _domains(a):
                if not _is_client_domain(d, client_domain):
                    sources[d] += 1
            for l in a["sources"] or []:
                if l.get("domain") and _is_client_domain(l["domain"], client_domain):
                    client_urls[l["url"]] += 1
            seen_rivals, seen_reasons = set(), set()
            for b in a["named"]:
                if b.get("is_client"):
                    continue
                seen_rivals.add(b["name"])
                if b.get("reason"):
                    r = " ".join(b["reason"].split())
                    seen_reasons.add(r.casefold())
                    reason_text.setdefault(r.casefold(), r)
            rivals.update(seen_rivals)
            reasons.update(seen_reasons)
        named = sum(1 for a in mine if any(b.get("is_client") for b in a["named"]))
        page_hint = (f"A page on {client_domain} that answers “{q}” in its first paragraph, names the business in the "
                     "first sentence, and says something the cited sources do not.")
        if client_urls:
            page_hint = ("Improve the page already being cited (" + ", ".join(sorted(client_urls)[:3]) + "). " + page_hint)
        out.append(_fix(
            "target_question", f"Target question: “{q}”", finding_code="AUDIT_TARGET_QUESTION_GAP", severity="medium",
            what="Competitors are named for this question and the client is not.", route=route, question=q,
            answers=len(mine), answers_client_named=named, answers_client_absent_competitors_named=len(gap),
            sources_cited=[{"domain": d, "answers": n} for d, n in sorted(sources.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_SOURCES]],
            competitors=[{"name": n, "answers": c} for n, c in sorted(rivals.items(), key=lambda kv: (-kv[1], kv[0].casefold()))[:TOP_COMPETITORS]],
            reasons_given=[{"quote": reason_text[k], "answers": c} for k, c in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_REASONS]],
            client_pages_cited=[{"url": u, "answers": n} for u, n in sorted(client_urls.items(), key=lambda kv: (-kv[1], kv[0]))[:5]],
            page_needed=page_hint))
    # Worst gap first (share of the question's answers), then question order as asked.
    position = {q: i for i, q in enumerate(audit["targets"])}
    out.sort(key=lambda f: (-f["answers_client_absent_competitors_named"] / f["answers"], position[f["question"]]))
    return out


def off_site_domains(audit: dict, client_domain: str) -> list[dict]:
    """Domains AI cited in answers where the client was NOT named."""
    count: Counter = Counter()
    for a in audit["answers"]:
        if any(b.get("is_client") for b in a["named"]):
            continue
        for d in _domains(a):
            if not _is_client_domain(d, client_domain):
                count[d] += 1
    return [{"domain": d, "answers": n} for d, n in sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_OFFSITE]]


# ---- 5: hygiene --------------------------------------------------------------------------------------------------------------

def hygiene_fixes(diagnosis: dict, *, schema_status: str, schema_errors: list[str], llms_written: bool) -> list[dict]:
    codes = {f["code"] for f in diagnosis["findings"]}
    platform, plan = diagnosis["platform"], diagnosis["platform_plan"]
    out = []
    if schema_status == "valid":
        out.append(_fix("hygiene", "Deploy schema.json (structured data)",
                        finding_code="SCHEMA_NONE_FOUND" if "SCHEMA_NONE_FOUND" in codes else None, severity="low",
                        what="Structured data is one of the weakest levers in the evidence. "
                             "If the site already has structured data, compare before replacing it.",
                        route=routing.route(platform, plan, "jsonld"), file="schema", source=HYGIENE_SOURCE))
    else:
        out.append(_fix("hygiene", "Confirm the missing facts so schema.json can be built",
                        finding_code="SCHEMA_NONE_FOUND" if "SCHEMA_NONE_FOUND" in codes else None, severity="low",
                        what="schema.json was not written: " + " ".join(schema_errors),
                        route={"status": routing.CAN, "where": "Fill in and import the truth document (`aeo truth draft`, then `aeo truth import`).",
                               "by_hand": True, "alternative": None, "source": "PLAN.md §16"},
                        source=HYGIENE_SOURCE))
    if "LLMS_TXT_MISSING" in codes:
        out.append(_fix("hygiene", "Deploy llms.txt", finding_code="LLMS_TXT_MISSING", severity="low",
                        what="There is no evidence yet that AI engines read llms.txt.",
                        route=routing.route(platform, plan, "llms"), file="llms" if llms_written else None, source=HYGIENE_SOURCE))
    return out


def number(fixes: list[dict]) -> list[dict]:
    """R1, R2, ... in list order. The order is part of the content: change the order and the ids move."""
    for i, f in enumerate(fixes, start=1):
        f["id"] = f"R{i}"
    return fixes
