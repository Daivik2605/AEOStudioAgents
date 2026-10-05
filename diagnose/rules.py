"""The decision rules (PLAN.md section 5): facts in, verdict out.

This is a lookup, not a judgement. The same facts always give the same
verdict, and every verdict carries the source it came from. A model may later
turn the result into a friendly paragraph (diagnose/explain.py) but it never
decides anything.

Order matters:
  1. DECLINE  - the platform is on the decline table. The work cannot be done.
  2. CONDITIONAL - one specific thing must change first. Listed in the order
     we would tell the client to fix them: the live crawler test first
     (PLAN.md section 5: "whatever the platform is, if the crawler test fails,
     that is the finding"), then robots.txt, then a plan upgrade, then a
     rendering project.
  3. SERVE - everything else.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from diagnose import platform_data as pd
from diagnose.crawler import BOTS, FAILING
from diagnose.facts import Facts

# A training bot refused with a plain "no" is usually the owner's choice and does
# not stop AI search. These failures are NOT deliberate-looking, so they count
# even for training bots.
NEVER_DELIBERATE = {"challenged", "rate_limited", "server_error", "connection_error",
                     "soft_block", "not_found_for_bot"}


@dataclass
class Reason:
    code: str          # the finding code this reason is tied to
    blocker: str       # what is wrong
    fix: str           # the one thing that must change
    source: str


@dataclass
class Verdict:
    verdict: str                       # serve | conditional | decline
    reason: str                        # one plain-English line, source included
    reasons: list[Reason] = field(default_factory=list)


def blocking_bots(facts: Facts) -> list[tuple[str, str, str]]:
    """(bot name, verdict, detail) for every bot whose failure counts against the site."""
    if not facts.crawler:
        return []
    kinds = {b.name: b.kind for b in BOTS}
    out = []
    for name, (verdict, detail) in facts.crawler.verdicts.items():
        if verdict not in FAILING:
            continue
        if kinds[name] == "retrieval" or verdict in NEVER_DELIBERATE:
            out.append((name, verdict, detail))
    return out


def crawl_inconclusive(facts: Facts) -> bool:
    if not facts.site_reachable:
        return True
    return bool(facts.crawler) and any(
        v in ("blanket_blocked", "inconclusive") for v, _ in facts.crawler.verdicts.values()
    )


def spa_conditional(facts: Facts) -> bool:
    """Is the page client-rendered in a way that blocks the work?

    The structural test is gated on platform: Wix, Squarespace, Shopify and the
    like all server-render, so a 'content missing' result on them is our
    heuristic misfiring (PLATFORM_DELIVERY.md §7). The fact check (name AND
    phone both missing from the HTML) is the headline test and needs both facts
    to be known.
    """
    r = facts.render
    if r is None or facts.platform in pd.SERVER_RENDERING_PLATFORMS:
        return False
    name, phone = r.details.get("name", {}), r.details.get("phone", {})
    facts_missing = (
        name.get("checked") and phone.get("checked")
        and not name["in_readable_text"] and not phone["in_readable_text"]
        and r.verdict != "fine"
    )
    return r.verdict == "content_missing" or bool(facts_missing)


def _reason_text(r: Reason) -> str:
    return f"{r.blocker} What has to change: {r.fix} (Source: {r.source})"


def decide(facts: Facts) -> Verdict:
    platform = facts.platform

    # 1. DECLINE -------------------------------------------------------------
    if platform in pd.DECLINE_TABLE:
        row = pd.DECLINE_TABLE[platform]
        label = pd.PLATFORM_LABELS[platform]
        return Verdict(
            "decline",
            f"{label} cannot carry this work. {row['why']} (Source: {row['source']})",
            [Reason("PLATFORM_UNSERVEABLE", row["why"], "Move to a platform that can carry it (needs a person's approval first).", row["source"])],
        )

    # 2. CONDITIONAL ----------------------------------------------------------
    reasons: list[Reason] = []

    blocked = blocking_bots(facts)
    if blocked:
        bots = ", ".join(f"{n} ({v.replace('_', ' ')})" for n, v, _ in blocked)
        code = "CRAWLER_BLOCKED_" + next(b.code_suffix for b in BOTS if b.name == blocked[0][0])
        if "wp_engine" in facts.detection.hosting and any(v == "rate_limited" for _, v, _ in blocked):
            w = pd.CONDITIONAL_WP_ENGINE
            reasons.append(Reason(code, f"{w['blocker']} Our test: {bots}.", w["fix"], w["source"]))
        else:
            c = pd.CONDITIONAL_CRAWLER
            reasons.append(Reason(code, f"{c['blocker']} Our test: {bots}.", c["fix"], c["source"]))

    robots = facts.robots
    if robots:
        if robots.bot_fetch_failed_but_browser_ok:
            reasons.append(Reason(
                "ROBOTS_TXT_BOT_FETCH_FAILED",
                f"robots.txt fails for bots ({robots.outcome}) but works for a browser. Under RFC 9309 that means every AI crawler must treat the whole site as disallowed.",
                "Make robots.txt answer bots normally (a CDN/firewall setting).",
                "RFC 9309 §2.3.1; research/PLATFORM_DELIVERY.md §3",
            ))
        else:
            for bot in BOTS:
                if bot.kind == "retrieval" and not robots.allowed.get(bot.token, True) and not robots.total_disallow:
                    reasons.append(Reason(
                        f"ROBOTS_DISALLOWS_{bot.code_suffix}",
                        f"robots.txt tells {bot.token}, a bot that fetches pages to answer questions, not to crawl the site.",
                        "Allow it in robots.txt (this is the owner's decision).",
                        "RFC 9309; research/PLATFORM_DELIVERY.md §6 (search uses OAI-SearchBot, not GPTBot)",
                    ))
                    break

    gate = pd.PLAN_GATES.get(platform or "")
    if gate and facts.detection.plan in gate["plans"]:
        reasons.append(Reason("PLATFORM_PLAN_TOO_LOW", gate["blocker"], gate["fix"], gate["source"]))

    if spa_conditional(facts):
        s = pd.CONDITIONAL_SPA
        reasons.append(Reason("RENDER_CONTENT_MISSING", s["blocker"], s["fix"], s["source"]))

    if not reasons and crawl_inconclusive(facts):
        reasons.append(Reason(
            "CRAWLER_TEST_INCONCLUSIVE",
            "The check could not be completed from our connection: the browser control was refused or failed too, so we cannot tell what AI crawlers get.",
            "Re-run from another network, or ask the owner to let our test through.",
            "research/PLATFORM_DELIVERY.md §7 ('403 for both user agents is our IP, not an AI block')",
        ))

    if reasons:
        first = reasons[0]
        text = f"Possible after one thing changes. {_reason_text(first)}"
        if len(reasons) > 1:
            text += " Also: " + " ".join(f"{r.blocker} {r.fix}" for r in reasons[1:])
        return Verdict("conditional", text, reasons)

    # 3. SERVE ----------------------------------------------------------------
    if platform:
        label = pd.PLATFORM_LABELS.get(platform, platform)
        text = f"{label} can carry the work and AI crawlers reach the page. (Source: {pd.DELIVERY_SOURCE})"
    else:
        text = ("No known platform recognised, and AI crawlers reach a page that has its content in the HTML. "
                "Treated as a custom site that server-renders. (Source: PLAN.md §5, Serve)")
    return Verdict("serve", text, [])
