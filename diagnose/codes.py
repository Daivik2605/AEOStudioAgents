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
    # ---- sitemap (PLAN.md step 25) ------------------------------------------
    "SITEMAP_MISSING": "medium",              # no sitemap at /sitemap.xml or in robots.txt
    "SITEMAP_INVALID": "medium",              # found, but not valid XML or it lists no pages
    "SITEMAP_NOT_IN_ROBOTS": "low",           # a working sitemap that robots.txt does not mention
    "SITEMAP_HOST_MISMATCH": "medium",        # the sitemap lists pages on the other of www / non-www
    # ---- which address the site lives at (www or not, http or https) ---------------
    "HOST_DUPLICATE": "medium",               # www and non-www both answer with the site and neither points to the other
    "HOST_REDIRECT_NOT_PERMANENT": "low",     # the www / non-www redirect is a 302 or 307, not a 301 or 308
    "HOST_WWW_UNREACHABLE": "low",            # the other host exists but does not answer
    "HTTP_NOT_REDIRECTED": "medium",          # plain http does not end up at https
    # ---- old addresses (only when `aeo diagnose --old-urls` is used) -----------------
    "OLD_URL_404": "high",                    # an old address ends at a page that is not there
    "OLD_URL_NOT_PERMANENT": "medium",        # an old address redirects with a 302 or 307
    "OLD_URL_CHAIN": "low",                   # an old address goes through more than one redirect
    "OLD_URL_TO_HOME": "medium",              # an old address sends everything to the homepage
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
    # step 25: a sitemap that is not there can no longer be "invalid", unlisted, or on the wrong host
    "SITEMAP_INVALID": {"subject": "sitemap", "gone_when": ("SITEMAP_MISSING",)},
    "SITEMAP_NOT_IN_ROBOTS": {"subject": "sitemap", "gone_when": ("SITEMAP_MISSING", "SITEMAP_INVALID")},
    "SITEMAP_HOST_MISMATCH": {"subject": "sitemap", "gone_when": ("SITEMAP_MISSING", "SITEMAP_INVALID")},
    # step 25: the second address stopped answering altogether, so its duplicate or its redirect is no longer there to fix
    "HOST_DUPLICATE": {"subject": "second_host", "gone_when": ("HOST_WWW_UNREACHABLE",)},
    "HOST_REDIRECT_NOT_PERMANENT": {"subject": "second_host", "gone_when": ("HOST_WWW_UNREACHABLE",)},
}

# ---- the step 25 checks ---------------------------------------------------------------------------------------
# A finding that is absent only means "fixed" if the check that could have raised it actually ran. `--old-urls` is
# optional, and diagnoses made before step 25 did not have the sitemap or host checks at all. diagnoses.site_checks
# records which checks ran; compare uses this table to tell "fixed" from "not checked this time".
CHECK_FOR_CODE: dict[str, str] = {
    "SITEMAP_MISSING": "sitemap", "SITEMAP_INVALID": "sitemap", "SITEMAP_NOT_IN_ROBOTS": "sitemap",
    "SITEMAP_HOST_MISMATCH": "sitemap",
    "HOST_DUPLICATE": "hosts", "HOST_REDIRECT_NOT_PERMANENT": "hosts", "HOST_WWW_UNREACHABLE": "hosts",
    "HTTP_NOT_REDIRECTED": "hosts",
    "OLD_URL_404": "old_urls", "OLD_URL_NOT_PERMANENT": "old_urls", "OLD_URL_CHAIN": "old_urls", "OLD_URL_TO_HOME": "old_urls",
}

# Plain-language explanation of each step 25 code: what it is, and why it matters for AI visibility. The finding's own
# `what` states the facts of one site; this says what the code means. Shown in the diagnosis report.
EXPLANATIONS: dict[str, str] = {
    "SITEMAP_MISSING": "A sitemap is a list of a site's pages that crawlers can read in one go. Without one, an AI search "
                       "crawler has to find pages by following links, so new or deep pages can be missed or found late. "
                       "A page the crawler never finds cannot be used in an answer.",
    "SITEMAP_INVALID": "The site has a sitemap address, but what it serves is not a usable list: it is not valid XML, or it "
                       "lists no pages. A crawler will skip it, so it does the same job as having no sitemap.",
    "SITEMAP_NOT_IN_ROBOTS": "The sitemap works, but the site's robots.txt does not say where it is. Crawlers look there "
                             "first, and many only find a sitemap that is listed. Low risk, one line to add.",
    "SITEMAP_HOST_MISMATCH": "The sitemap lists pages under one form of the address (with www or without) while the site is "
                             "served under the other. Crawlers may treat the listed pages as a different site, or skip them.",
    "HOST_DUPLICATE": "The site answers on both the www and the non-www address, and neither sends visitors to the other. "
                      "Search and AI systems then see two copies of every page and have to guess which one counts, which "
                      "splits the signals that would otherwise add up to one site.",
    "HOST_REDIRECT_NOT_PERMANENT": "One address sends visitors to the other, but with a temporary redirect (302 or 307) "
                                   "instead of a permanent one (301 or 308). A temporary redirect tells crawlers to keep "
                                   "the old address, so the move is never fully recognised.",
    "HOST_WWW_UNREACHABLE": "The other form of the address (with or without www) exists, but does not answer. People or "
                            "crawlers using it get an error instead of the site. Low risk, but it is a dead end for "
                            "anyone who links to that form.",
    "HTTP_NOT_REDIRECTED": "Opening the site over plain http does not end up on https. Crawlers and links that use the old "
                           "http address get an insecure or duplicate copy, and some systems will not trust it.",
    "OLD_URL_404": "Addresses from the old site end at a page that is not there. AI crawlers still ask for these addresses "
                   "(they remember them), get an error, and drop whatever the old page was known for. A redirect to the "
                   "matching new page keeps that standing.",
    "OLD_URL_NOT_PERMANENT": "Old addresses are redirected with a temporary redirect (302 or 307). Crawlers read that as "
                             "'this may come back', so they keep the old address and the new page does not inherit its standing.",
    "OLD_URL_CHAIN": "Old addresses reach the new page only after more than one redirect in a row. Each extra step slows a "
                     "crawler down, and some stop following after a few steps.",
    "OLD_URL_TO_HOME": "Old addresses are all sent to the homepage instead of to their matching new page. To a crawler that "
                       "looks like the old page was removed, so what it was known for is lost.",
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
