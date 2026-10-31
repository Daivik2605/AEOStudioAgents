"""Findings we must not raise because we could not read the site (PLAN.md step 25c).

A finding such as "no sitemap", "no structured data" or "the content is client-rendered" says something about the site's
pages. If our own request was turned away as a bot (a 403, 429 or 503, a challenge or CAPTCHA page, or a robots.txt that
fails for bots but not for a browser), the honest answer is "we could not look", not "it is not there".

This file decides when that is the case, and what the report says about it. The checks themselves are skipped, so no
finding that depends on them can be raised. Each skipped check is recorded in `site_checks.not_checked`, and left out of
`checks_ran`, which is what compare reads to avoid calling such a finding fixed or new.
"""

from __future__ import annotations

from diagnose.codes import CHECK_LABELS, codes_for_check
from diagnose.crawler import BOTS, CrawlerTest, looks_like_bot_check
from diagnose.fetch import FetchResult
from diagnose.robots import RobotsCheck

BLOCKED = "we were blocked, so we couldn't read the page"
CONTENT_CHECKS = ("render", "structured_data", "llms")
EVERY_CHECK = (*CONTENT_CHECKS, "sitemap", "hosts", "old_urls")


def assess_block(control: FetchResult, crawler: CrawlerTest | None, robots: RobotsCheck | None) -> str | None:
    """Why we were blocked, in a few words, or None. Any one of three signs is enough:

      1. the page we fetched as a browser came back as a bot check (403/429/503, or a challenge page);
      2. robots.txt fails for bots but works for a browser (RFC 9309 then treats the whole site as disallowed), or it
         is behind a challenge;
      3. every one of the bots was turned away the same way (a refusal status, a challenge, or a tiny body unlike the
         browser's), which means a bot check in front of the whole site, not a rule about one crawler.
    """
    why = looks_like_bot_check(control)
    if why:
        return f"the page itself came back as a bot check ({why})"
    if robots is not None and (robots.bot_fetch_failed_but_browser_ok or robots.outcome == "challenged"):
        return f"robots.txt could not be fetched as a bot ({robots.outcome}, HTTP {robots.status}) although a browser could"
    if crawler and len(crawler.bot_results) == len(BOTS):
        turned_away = [looks_like_bot_check(r) or (crawler.verdicts[n][0] == "soft_block" and "a tiny body unlike the browser's")
                       for n, r in crawler.bot_results.items()]
        if all(turned_away):
            return "every crawler test was turned away the same way (" + str(turned_away[0]) + ")"
    return None


def entry(check: str, reason: str) -> dict:
    """One line of the "Not checked" list: what was not checked, why, and which findings that rules out."""
    return {"check": check, "label": CHECK_LABELS[check], "reason": reason, "codes": codes_for_check(check)}


def blocked_entry(check: str, why: str) -> dict:
    return entry(check, f"{BLOCKED} ({why})")


def site_checks_ran(site_checks: dict | None) -> list[str]:
    """The sitemap / address / old-address checks a stored diagnosis ran (step 25). One made before step 25 ran none."""
    return list((site_checks or {}).get("checks_ran", []))


def content_checks_ran(site_checks: dict | None) -> list[str]:
    """The page checks (render, structured data, llms) a stored diagnosis ran. A diagnosis made from step 25c on records
    this exactly, including what it could not do. An older one did not, and nothing then said a page check was skipped,
    so its page checks are taken to have run."""
    sc = site_checks or {}
    return list(sc.get("content_ran", [])) if "not_checked" in sc else list(CONTENT_CHECKS)
