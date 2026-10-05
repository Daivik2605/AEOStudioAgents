"""Crawler reachability: bots vs a browser control (PLAN.md §3.1 step 2)."""

from __future__ import annotations

import httpx
import pytest

from diagnose.crawler import BOTS, BROWSER_UA, run_crawler_test
from diagnose.fetch import Fetcher, RequestBudgetExceeded
from tests.helpers_diagnose import is_bot, make_fetcher, page, response

URL = "https://example.com/"
GOOD = page(500)


def verdicts(handler, **kw):
    test = run_crawler_test(make_fetcher(handler, **kw), URL)
    return {name: v for name, (v, _) in test.verdicts.items()}


def test_all_bots_allowed_when_everyone_gets_the_same_page():
    assert set(verdicts(lambda r: response(200, GOOD)).values()) == {"allowed"}


def test_it_tests_the_five_bots_and_a_browser_control_each_with_their_own_user_agent():
    seen = []
    def handler(request):
        seen.append(request.headers["user-agent"])
        return response(200, GOOD)
    run_crawler_test(make_fetcher(handler), URL)
    assert seen[0] == BROWSER_UA
    assert seen[1:] == ["OAI-SearchBot/1.4", "PerplexityBot/1.0", "Claude-SearchBot/1.0", "GPTBot/1.0", "ClaudeBot/1.0"]
    assert [b.kind for b in BOTS] == ["retrieval", "retrieval", "retrieval", "training", "training"]


def test_403_for_bots_but_200_for_the_browser_is_a_ua_block():
    handler = lambda r: response(403, "Forbidden") if is_bot(r) else response(200, GOOD)
    assert set(verdicts(handler).values()) == {"ua_blocked"}


def test_403_for_the_browser_too_is_our_connection_not_an_ai_block():
    result = verdicts(lambda r: response(403, "Forbidden"))
    assert set(result.values()) == {"blanket_blocked"}


def test_cloudflare_error_codes_are_told_apart():
    def handler(r):
        if not is_bot(r):
            return response(200, GOOD)
        return response(403, "Error 1010: banned") if "OAI" in r.headers["user-agent"] else response(403, "Error 1020: Access denied")
    result = verdicts(handler)
    assert result["OAI-SearchBot"] == "ua_blocked"
    assert result["GPTBot"] == "waf_blocked"


def test_a_200_with_the_cf_mitigated_header_is_challenged_not_allowed():
    handler = lambda r: response(200, GOOD, {"cf-mitigated": "challenge"}) if is_bot(r) else response(200, GOOD)
    assert set(verdicts(handler).values()) == {"challenged"}


def test_a_thin_200_that_looks_like_a_challenge_page_is_challenged():
    challenge = "<html><body><h1>Just a moment...</h1><p>Checking your browser</p></body></html>"
    handler = lambda r: response(200, challenge) if is_bot(r) else response(200, GOOD)
    assert set(verdicts(handler).values()) == {"challenged"}


def test_a_long_page_that_merely_mentions_a_captcha_is_not_a_challenge():
    long_page = page(500, extra_body="<p>Our contact form uses a captcha.</p>")
    assert set(verdicts(lambda r: response(200, long_page)).values()) == {"allowed"}


def test_402_is_a_deliberate_pay_per_crawl_block():
    handler = lambda r: response(402, "pay") if is_bot(r) else response(200, GOOD)
    assert set(verdicts(handler).values()) == {"payment_required"}


def test_404_for_bots_and_200_for_browser_is_a_disguised_block():
    handler = lambda r: response(404, "nope") if is_bot(r) else response(200, GOOD)
    assert set(verdicts(handler).values()) == {"not_found_for_bot"}


def test_a_much_shorter_200_is_a_soft_block():
    handler = lambda r: response(200, page(5)) if is_bot(r) else response(200, GOOD)
    assert set(verdicts(handler).values()) == {"soft_block"}


def test_5xx_for_bots_only_is_a_server_error():
    handler = lambda r: response(503, "down") if is_bot(r) else response(200, GOOD)
    # A 503 whose body has no challenge markers is a plain server error.
    assert set(verdicts(handler).values()) == {"server_error"}


def test_429_is_retried_once_after_retry_after_then_reported():
    sleeps, calls = [], []
    def handler(r):
        if is_bot(r) and "ClaudeBot" in r.headers["user-agent"]:
            calls.append(1)
            return response(429, "slow down", {"retry-after": "3"})
        return response(200, GOOD)
    fetcher = make_fetcher(handler, sleeps=sleeps)
    test = run_crawler_test(fetcher, URL)
    assert len(calls) == 2                   # asked once, waited, asked once more, then gave up
    assert 3.0 in sleeps
    assert test.verdicts["ClaudeBot"][0] == "rate_limited"


def test_a_429_with_a_very_long_retry_after_is_not_waited_out():
    sleeps = []
    handler = lambda r: response(429, "x", {"retry-after": "3600"}) if "GPTBot" in r.headers["user-agent"] else response(200, GOOD)
    run_crawler_test(make_fetcher(handler, sleeps=sleeps), URL)
    assert 3600.0 not in sleeps


def test_a_challenge_is_never_retried():
    calls = []
    def handler(r):
        if is_bot(r):
            calls.append(r.headers["user-agent"])
            return response(403, "<html>Just a moment... captcha</html>", {"cf-mitigated": "challenge"})
        return response(200, GOOD)
    run_crawler_test(make_fetcher(handler), URL)
    assert len(calls) == 5   # once per bot, no retries


def test_a_dead_site_does_not_waste_requests_on_the_bots():
    def handler(r):
        raise httpx.ConnectError("no route")
    fetcher = make_fetcher(handler)
    test = run_crawler_test(fetcher, URL)
    assert fetcher.requests_made == 1
    assert test.bot_results == {} and test.stopped_early


def test_the_request_cap_is_enforced():
    fetcher = make_fetcher(lambda r: response(200, GOOD), max_requests=3)
    test = run_crawler_test(fetcher, URL)
    assert fetcher.requests_made == 3
    assert "cap" in test.stopped_early
    with pytest.raises(RequestBudgetExceeded):
        fetcher.get(URL, BROWSER_UA)


def test_requests_are_spaced_out():
    sleeps, now = [], [0.0]
    client = httpx.Client(transport=httpx.MockTransport(lambda r: response(200, GOOD)))
    fetcher = Fetcher(client, min_interval=1.0, sleep=sleeps.append, clock=lambda: now[0])
    fetcher.get(URL, BROWSER_UA)
    fetcher.get(URL, BROWSER_UA)
    assert sleeps == [1.0]
