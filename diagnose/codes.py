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
    # ---- audit-based fixes (written by `aeo recommend`, not by the diagnosis) -----------
    "AUDIT_WRONG_FACT": "high",               # AI states something that contradicts a confirmed fact
    "AUDIT_TARGET_QUESTION_GAP": "medium",    # a target question where competitors are named and the client is not
}

# ---- what a finding is ABOUT (PLAN.md step 22c) -------------------------------------------------------------
# Some findings describe the content of something that can disappear. When the old structured data is gone and the
# new site has none at all, "name is the legal entity" is not FIXED: there is no name left to be wrong. It no longer
# applies, and what replaced it ("no structured data at all") still needs doing.
#   subject    a short key for the thing the finding is about
#   gone_when  finding codes which, if present in the LATER diagnosis, say that the subject no longer exists
# A code that is not listed here has no such dependency: when it disappears, it is fixed.
DEPENDS_ON: dict[str, dict[str, Any]] = {
    # every code that reads the CONTENT of JSON-LD needs JSON-LD to exist (SCHEMA_NONE_FOUND says it does not)
    "SCHEMA_INVALID_JSON": {"subject": "structured_data", "gone_when": ("SCHEMA_NONE_FOUND",)},
    "SCHEMA_NAME_IS_LEGAL_ENTITY": {"subject": "structured_data", "gone_when": ("SCHEMA_NONE_FOUND",)},
    "SCHEMA_ORG_MISSING_NAME": {"subject": "structured_data", "gone_when": ("SCHEMA_NONE_FOUND",)},
    "SCHEMA_LOCALBUSINESS_NO_AREASERVED": {"subject": "structured_data", "gone_when": ("SCHEMA_NONE_FOUND",)},
    "SCHEMA_OPENING_HOURS_EMPTY": {"subject": "structured_data", "gone_when": ("SCHEMA_NONE_FOUND",)},
    "SCHEMA_SAMEAS_HAS_TRACKING_PARAMS": {"subject": "structured_data", "gone_when": ("SCHEMA_NONE_FOUND",)},
    "SCHEMA_ENTITIES_UNLINKED": {"subject": "structured_data", "gone_when": ("SCHEMA_NONE_FOUND",)},
    # "empty" and "more than one" main heading are about headings that exist; "no main heading at all" removes them
    "RENDER_H1_EMPTY": {"subject": "main_heading", "gone_when": ("RENDER_H1_MISSING",)},
    "RENDER_H1_MULTIPLE": {"subject": "main_heading", "gone_when": ("RENDER_H1_MISSING",)},
}

# Codes that inspect the content of JSON-LD (every SCHEMA_ code except the one that says there is none).
# A test fails if one of these is missing from DEPENDS_ON.
JSONLD_CONTENT_CODES = tuple(c for c in REGISTRY if c.startswith("SCHEMA_") and c != "SCHEMA_NONE_FOUND")


def subject_removed_by(code: str, later_codes: set[str]) -> list[str]:
    """The codes in the later diagnosis that show this finding's subject no longer exists ([] if it still does,
    or if the code has no declared dependency)."""
    dep = DEPENDS_ON.get(code)
    return [c for c in dep["gone_when"] if c in later_codes] if dep else []


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
