"""robots.txt: fetched as a bot, parsed to RFC 9309 (PLAN.md §3.1 step 3)."""

from __future__ import annotations

import httpx

from diagnose.robots import check_robots, parse_content_signal, robots_url_for
from tests.helpers_diagnose import is_bot, make_fetcher, response

PAGE = "https://example.com/"


def robots(body=None, *, bot_status=200, browser_status=200, headers=None, content_type="text/plain"):
    """A site whose /robots.txt answers bots and browsers separately."""
    def handler(request):
        status = bot_status if is_bot(request) else browser_status
        return httpx.Response(status, text=body or "", headers={"content-type": content_type, **(headers or {})})
    return make_fetcher(handler)


def test_robots_is_fetched_as_a_bot_not_as_a_browser():
    seen = []
    def handler(request):
        seen.append(request.headers["user-agent"])
        return response(200, "User-agent: *\nAllow: /")
    check_robots(make_fetcher(handler), PAGE, {})
    assert seen == ["OAI-SearchBot/1.4"]


def test_robots_url_is_per_origin():
    assert robots_url_for("https://www.example.com/a/b?x=1") == "https://www.example.com/robots.txt"


# ---- RFC 9309: a failed bot fetch means disallow everything ---------------------------------

def test_5xx_for_the_bot_but_fine_for_the_browser_is_total_disallow():
    check = check_robots(robots("User-agent: *\nAllow: /", bot_status=503), PAGE, {})
    assert check.outcome == "unreachable"
    assert check.total_disallow
    assert check.bot_fetch_failed_but_browser_ok       # invisible from a browser
    assert not any(check.allowed.values())             # every bot is disallowed, despite the permissive file


def test_a_challenged_robots_txt_is_total_disallow():
    challenge = "<html><body>Just a moment... captcha</body></html>"
    def handler(request):
        if is_bot(request):
            return response(200, challenge, {"cf-mitigated": "challenge"})
        return response(200, "User-agent: *\nAllow: /")
    check = check_robots(make_fetcher(handler), PAGE, {})
    assert check.outcome == "challenged"
    assert check.total_disallow and check.bot_fetch_failed_but_browser_ok


def test_5xx_for_everyone_is_unreachable_but_not_blamed_on_bots():
    check = check_robots(robots(bot_status=500, browser_status=500), PAGE, {})
    assert check.total_disallow
    assert not check.bot_fetch_failed_but_browser_ok


def test_404_means_no_robots_txt_so_everything_is_allowed():
    # RFC 9309: a 4xx is 'unavailable', and the crawler may access everything.
    check = check_robots(robots(bot_status=404, browser_status=404), PAGE, {})
    assert check.outcome == "missing"
    assert not check.total_disallow
    assert all(check.allowed.values())


def test_a_connection_error_is_unreachable():
    def handler(request):
        raise httpx.ConnectError("refused")
    assert check_robots(make_fetcher(handler), PAGE, {}).total_disallow


# ---- Parsing with protego, not urllib.robotparser -------------------------------------------------

def test_the_longest_matching_rule_wins():
    # urllib.robotparser takes the first match in file order and would block this.
    body = "User-agent: GPTBot\nDisallow: /shop\nAllow: /shop/public/\n"
    check = check_robots(robots(body), "https://example.com/shop/public/page", {})
    assert check.allowed["GPTBot"] is True
    assert check_robots(robots(body), "https://example.com/shop/private", {}).allowed["GPTBot"] is False


def test_dollar_anchor_is_honoured():
    body = "User-agent: *\nDisallow: /*.html$\n"
    assert check_robots(robots(body), "https://example.com/a.html", {}).allowed["ClaudeBot"] is False
    assert check_robots(robots(body), "https://example.com/a.html?x=1", {}).allowed["ClaudeBot"] is True


def test_a_specific_group_replaces_the_star_group_entirely():
    body = "User-agent: *\nDisallow: /private\n\nUser-agent: GPTBot\nDisallow: /\n"
    check = check_robots(robots(body), "https://example.com/private/x", {})
    assert check.allowed["GPTBot"] is False
    assert check.allowed["ClaudeBot"] is False   # falls under '*', which blocks /private
    assert check_robots(robots(body), "https://example.com/other", {}).allowed["ClaudeBot"] is True


def test_retrieval_and_training_bots_can_be_treated_differently():
    body = "User-agent: GPTBot\nDisallow: /\nUser-agent: ClaudeBot\nDisallow: /\n"
    allowed = check_robots(robots(body), PAGE, {}).allowed
    assert allowed["GPTBot"] is False and allowed["ClaudeBot"] is False
    assert allowed["OAI-SearchBot"] is True and allowed["PerplexityBot"] is True and allowed["Claude-SearchBot"] is True


def test_the_raw_file_is_kept_exactly_as_served():
    body = "# hello\r\nUser-agent: *\r\nDisallow:\r\n"
    assert check_robots(robots(body), PAGE, {}).raw == body


def test_robots_served_as_html_is_flagged_and_does_not_block_anyone():
    check = check_robots(robots("<!doctype html><html><body>app</body></html>", content_type="text/html"), PAGE, {})
    assert check.served_as_html
    assert all(check.allowed.values())


# ---- Content-Signal -----------------------------------------------------------------------------------------

def test_content_signal_header_is_extracted():
    check = check_robots(robots("User-agent: *\nAllow: /", headers={"Content-Signal": "search=yes, ai-train=no"}), PAGE, {})
    assert check.content_signal == "search=yes, ai-train=no"
    assert check.content_signal_source == "robots.txt header"


def test_content_signal_line_inside_the_file_is_extracted():
    check = check_robots(robots("User-agent: *\nContent-Signal: search=yes, ai-input=no\nAllow: /"), PAGE, {})
    assert check.content_signal == "search=yes, ai-input=no"
    assert check.content_signal_source == "robots.txt file"


def test_content_signal_on_the_page_is_a_fallback():
    check = check_robots(robots("User-agent: *\nAllow: /"), PAGE, {"content-signal": "ai-train=no"})
    assert check.content_signal_source == "page header"


def test_content_signal_is_not_access_control():
    check = check_robots(robots("User-agent: *\nAllow: /", headers={"Content-Signal": "ai-train=no"}), PAGE, {})
    assert all(check.allowed.values())


def test_parse_content_signal():
    assert parse_content_signal("search=yes, ai-train=no") == {"search": "yes", "ai-train": "no"}
