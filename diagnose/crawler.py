"""Crawler reachability test (PLAN.md section 3.1 step 2).

Fetch the page as each AI bot and as a normal browser, then compare. The
browser is the control: if a bot gets a different answer from the browser on
the same URL, that difference is the finding.

Honest limit, stated in every report: this only tests the user-agent layer.
Cloudflare and Vercel now also verify bots by IP range and cryptographic
signature (Web Bot Auth), so a site can let the real OAI-SearchBot in while
refusing our look-alike (PLATFORM_DELIVERY.md §7).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from diagnose.fetch import FetchResult, Fetcher, RequestBudgetExceeded
from diagnose.html_utils import parse_page

# The bots to test. Source: the AI-crawler list maintained by ai-robots-txt/ai.robots.txt
# (MIT licence, safe to bundle). Hardcoded on purpose rather than fetched live at run time.
# `token` is the product token that robots.txt matches on (RFC 9309). `kind` says whether
# the bot fetches pages to answer a question (retrieval) or to train a model (training).
# User-agent version numbers drift; Anthropic does not publish its full strings, so these
# carry the token plus a version and are indicative only.
@dataclass(frozen=True)
class Bot:
    name: str
    token: str
    user_agent: str
    kind: str  # "retrieval" | "training"

    @property
    def code_suffix(self) -> str:
        return self.token.upper().replace("-", "_")


BOTS = [
    Bot("OAI-SearchBot", "OAI-SearchBot", "OAI-SearchBot/1.4", "retrieval"),
    Bot("PerplexityBot", "PerplexityBot", "PerplexityBot/1.0", "retrieval"),
    Bot("Claude-SearchBot", "Claude-SearchBot", "Claude-SearchBot/1.0", "retrieval"),
    Bot("GPTBot", "GPTBot", "GPTBot/1.0", "training"),
    Bot("ClaudeBot", "ClaudeBot", "ClaudeBot/1.0", "training"),
]

# A plain desktop-browser user agent, used as the control.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Words that show up on bot-challenge pages.
_CHALLENGE_MARKERS = (
    "just a moment", "cf-chl", "challenge-platform", "checking your browser",
    "enable javascript and cookies", "captcha", "attention required", "_incapsula_",
    "px-captcha", "datadome", "are you a robot", "verify you are human",
)

THIN_WORDS = 80              # a 200 with fewer words than this gets the challenge check
SOFT_BLOCK_RATIO = 0.5       # bot body under half the browser body = soft block

# Verdicts that mean "the bot was NOT given the page".
FAILING = {
    "challenged", "ua_blocked", "waf_blocked", "payment_required", "rate_limited",
    "not_found_for_bot", "server_error", "soft_block", "connection_error", "http_error",
}


def challenge_reason(result: FetchResult) -> str | None:
    """Does this response look like a bot-challenge page? Returns why, or None.

    We only DETECT challenges. We never try to solve or get around one.
    """
    if "challenge" in result.headers.get("cf-mitigated", "").lower():
        return "cf-mitigated: challenge header"
    if result.status in (200, 202, 403, 429, 503) and result.text and len(result.text) < 150_000:
        low = result.text.lower()
        marker = next((m for m in _CHALLENGE_MARKERS if m in low), None)
        if marker:
            words = parse_page(result.text).word_count_total
            if result.status != 200 or words < THIN_WORDS:
                return f"challenge page text ('{marker}')"
    return None


def _control_state(control: FetchResult) -> str:
    if control.error or control.status is None:
        return "error"
    if challenge_reason(control):
        return "challenged"
    if control.status >= 400:
        return "blocked"
    return "ok"


def classify(bot_result: FetchResult, control: FetchResult) -> tuple[str, str]:
    """Compares one bot's response with the browser control. Returns (verdict, detail)."""
    state = _control_state(control)

    # The browser control itself did not get a clean page: we cannot blame the AI bot.
    if state != "ok":
        if (not bot_result.error and bot_result.status == control.status
                and state == "blocked"):
            return "blanket_blocked", (
                f"HTTP {control.status} for the browser too: this blocks OUR connection, "
                "not AI crawlers specifically"
            )
        return "inconclusive", f"the browser control was {state}, so the comparison means nothing"

    if bot_result.error:
        return "connection_error", bot_result.error
    status = bot_result.status
    reason = challenge_reason(bot_result)
    if reason:
        return "challenged", f"HTTP {status}, {reason}. Not retried: we never get past a challenge"
    if status == 402:
        return "payment_required", "HTTP 402: a deliberate AI-crawler block (Pay Per Crawl)"
    if status == 429:
        return "rate_limited", f"HTTP 429, retry-after: {bot_result.headers.get('retry-after', 'not given')}"
    if status in (401, 403):
        body = bot_result.text.lower()
        if "error 1020" in body:
            return "waf_blocked", f"HTTP {status}, Cloudflare Error 1020 (a firewall rule)"
        if "error 1010" in body:
            return "ua_blocked", f"HTTP {status}, Cloudflare Error 1010 (banned by browser signature)"
        return "ua_blocked", f"HTTP {status} for the bot, {control.status} for the browser"
    if status in (404, 410):
        return "not_found_for_bot", f"HTTP {status} for the bot but {control.status} for the browser (a block disguised as a 404)"
    if status is not None and status >= 500:
        return "server_error", f"HTTP {status} for the bot, {control.status} for the browser"
    if status is not None and status >= 400:
        return "http_error", f"HTTP {status} for the bot, {control.status} for the browser"

    # Status 200 (after redirects). Compare what came back.
    if control.size and bot_result.size < control.size * SOFT_BLOCK_RATIO:
        return "soft_block", (
            f"HTTP 200 but the body is {bot_result.size} bytes against {control.size} for the browser"
        )
    same = bot_result.body_hash == control.body_hash
    return "allowed", "HTTP 200, " + ("identical to the browser" if same else "similar size to the browser")


@dataclass
class CrawlerTest:
    control: FetchResult
    bot_results: dict[str, FetchResult]
    verdicts: dict[str, tuple[str, str]]
    stopped_early: str | None = None   # set if the request cap cut the test short

    def to_json(self) -> dict:
        def row(r: FetchResult) -> dict:
            return {"user_agent": r.user_agent, "status": r.status, "final_url": r.final_url,
                    "size": r.size, "error": r.error}
        bots = {}
        for bot in BOTS:
            r = self.bot_results.get(bot.name)
            if r is None:
                continue
            verdict, detail = self.verdicts[bot.name]
            bots[bot.name] = {**row(r), "kind": bot.kind, "verdict": verdict, "detail": detail,
                              "same_body_as_control": r.body_hash == self.control.body_hash}
        return {"control": row(self.control), "bots": bots, "stopped_early": self.stopped_early}


def run_crawler_test(fetcher: Fetcher, url: str) -> CrawlerTest:
    """Fetches `url` as the browser, then as each bot, one at a time."""
    control = fetcher.get(url, BROWSER_UA)
    results: dict[str, FetchResult] = {}
    stopped = None
    if control.error:
        # No answer at all, even for a browser. Asking five more times would tell us nothing.
        return CrawlerTest(control=control, bot_results={}, verdicts={},
                           stopped_early="the browser control got no answer, so the bots were not tried")
    for bot in BOTS:
        try:
            results[bot.name] = fetcher.get(url, bot.user_agent)
        except RequestBudgetExceeded as exc:
            stopped = str(exc)
            break
    verdicts = {name: classify(r, control) for name, r in results.items()}
    return CrawlerTest(control=control, bot_results=results, verdicts=verdicts, stopped_early=stopped)
