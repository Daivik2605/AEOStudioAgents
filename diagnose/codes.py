"""The registry of finding codes (PLAN.md section 3.4).

A finding's code is its identity. At every checkpoint we re-run the
diagnosis and diff by code: "eleven findings at baseline, eight closed, three
open". That only works if a code NEVER changes meaning.

RULES FOR THIS FILE
  - Never rename a code.
  - Never reuse a retired code for something new.
  - Never change what a code means. If the meaning changes, add a new code.
  - To retire a finding, stop emitting it but leave it listed here.
  - Every code a check can emit must be listed here. make_finding() refuses
    codes that are not in REGISTRY, so a typo cannot slip through.

Severity here is the default; a check may override it for one finding.
"""

from __future__ import annotations

from typing import Any

SEVERITIES = ("critical", "high", "medium", "low", "info")

REGISTRY: dict[str, str] = {
    # ---- platform ---------------------------------------------------------
    "PLATFORM_UNSERVEABLE": "critical",       # platform is on the PLAN.md §5 decline table
    "PLATFORM_PLAN_TOO_LOW": "high",          # plan tier blocks the work (PLAN.md §5 conditional table)
    "PLATFORM_PLAN_UNCONFIRMED": "medium",    # platform has a plan gate and we cannot see the plan
    "PLATFORM_UNDETECTED": "info",            # no platform recognised (custom or unknown)
    "PLATFORM_AMBIGUOUS": "info",             # two platforms matched equally well
    "HOST_WP_ENGINE_DETECTED": "low",         # WP Engine hosting (known bot rate limiting)
    # ---- crawler reachability (one code per bot, so each can close separately)
    "CRAWLER_BLOCKED_OAI_SEARCHBOT": "high",
    "CRAWLER_BLOCKED_PERPLEXITYBOT": "high",
    "CRAWLER_BLOCKED_CLAUDE_SEARCHBOT": "high",
    "CRAWLER_BLOCKED_GPTBOT": "low",          # training bot; blocking it is often deliberate
    "CRAWLER_BLOCKED_CLAUDEBOT": "low",       # training bot; blocking it is often deliberate
    "CRAWLER_TEST_INCONCLUSIVE": "medium",    # the browser control failed too: our network, not an AI block
    # ---- robots.txt ---------------------------------------------------------
    "ROBOTS_TXT_BOT_FETCH_FAILED": "high",    # bot got 5xx/challenge, browser did not: RFC 9309 total disallow
    "ROBOTS_TXT_UNREACHABLE": "medium",       # robots.txt failed for the browser too
    "ROBOTS_TXT_SERVED_AS_HTML": "low",
    "ROBOTS_DISALLOWS_OAI_SEARCHBOT": "high",
    "ROBOTS_DISALLOWS_PERPLEXITYBOT": "high",
    "ROBOTS_DISALLOWS_CLAUDE_SEARCHBOT": "high",
    "ROBOTS_DISALLOWS_GPTBOT": "low",
    "ROBOTS_DISALLOWS_CLAUDEBOT": "low",
    "CONTENT_SIGNAL_AI_INPUT_NO": "high",     # asks AI answer engines not to use the content
    "LLMS_TXT_MISSING": "low",                # unproven hygiene (AEO_PLAYBOOK.md §3 P2)
    # ---- render (is the content in the no-JavaScript HTML?) -------------------
    "RENDER_CONTENT_MISSING": "high",
    "RENDER_CONTENT_LIKELY_MISSING": "medium",
    "RENDER_BUSINESS_NAME_MISSING": "high",
    "RENDER_PHONE_MISSING": "medium",
    "RENDER_ADDRESS_MISSING": "medium",
    "RENDER_H1_MISSING": "medium",            # no <h1> tag at all
    "RENDER_H1_EMPTY": "medium",              # <h1> tags exist but none has text
    "RENDER_H1_MULTIPLE": "low",
    # ---- existing structured data (JSON-LD) -------------------------------
    "SCHEMA_NONE_FOUND": "medium",
    "SCHEMA_INVALID_JSON": "medium",
    "SCHEMA_NAME_IS_LEGAL_ENTITY": "high",
    "SCHEMA_ORG_MISSING_NAME": "high",
    "SCHEMA_LOCALBUSINESS_NO_AREASERVED": "medium",
    "SCHEMA_OPENING_HOURS_EMPTY": "low",
    "SCHEMA_SAMEAS_HAS_TRACKING_PARAMS": "low",
    "SCHEMA_ENTITIES_UNLINKED": "low",
}


def make_finding(
    code: str,
    *,
    what: str,
    evidence: str,
    fixable_on_platform: bool | None,
    where_to_fix: str,
    source: str,
    severity: str | None = None,
) -> dict[str, Any]:
    """Builds one finding in the exact shape PLAN.md section 3.4 shows.

    `fixable_on_platform` is None when we do not know the platform well
    enough to say.
    """
    if code not in REGISTRY:
        raise ValueError(f"finding code '{code}' is not in diagnose/codes.py REGISTRY")
    severity = severity or REGISTRY[code]
    if severity not in SEVERITIES:
        raise ValueError(f"unknown severity '{severity}'")
    return {
        "code": code,
        "severity": severity,
        "what": what,
        "evidence": evidence,
        "fixable_on_platform": fixable_on_platform,
        "where_to_fix": where_to_fix,
        "source": source,
    }
