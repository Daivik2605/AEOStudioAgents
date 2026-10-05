"""Shared helpers for the `aeo diagnose` tests. Nothing here touches the network:
every page comes from a fake httpx transport."""

from __future__ import annotations

from typing import Callable

import httpx

from diagnose.fetch import Fetcher

BOT_TOKENS = ("OAI-SearchBot", "PerplexityBot", "Claude-SearchBot", "GPTBot", "ClaudeBot")


def page(body_words: int = 500, *, title: str = "Home", extra_head: str = "", extra_body: str = "",
         h1: str = "Welcome to Acme Water", jsonld: str = "") -> str:
    """A plain server-rendered page with `body_words` words of text."""
    text = " ".join(["word"] * body_words)
    ld = f'<script type="application/ld+json">{jsonld}</script>' if jsonld else ""
    return (f"<!doctype html><html><head><title>{title}</title>{extra_head}{ld}</head>"
            f"<body><main><h1>{h1}</h1><p>{text}</p>{extra_body}</main></body></html>")


def response(status: int = 200, body: str = "", headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, text=body, headers={"content-type": "text/html", **(headers or {})})


def is_bot(request: httpx.Request) -> bool:
    ua = request.headers.get("user-agent", "")
    return any(t in ua for t in BOT_TOKENS)


def make_fetcher(handler: Callable[[httpx.Request], httpx.Response], *, max_requests: int = 15,
                 sleeps: list | None = None) -> Fetcher:
    """A Fetcher wired to `handler`. No waiting between requests."""
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    record = sleeps if sleeps is not None else []
    return Fetcher(client, max_requests=max_requests, min_interval=0, sleep=record.append)


def site(pages: dict[str, Callable[[httpx.Request], httpx.Response] | httpx.Response | str]):
    """Routes by URL path. Anything not listed is a 404. Values may be a Response, a body
    string (served as 200 HTML), or a function of the request."""
    def handler(request: httpx.Request) -> httpx.Response:
        item = pages.get(request.url.path)
        if item is None:
            return response(404, "not found")
        if isinstance(item, str):
            return response(200, item)
        if callable(item):
            return item(request)
        return item
    return handler


# ---- Hand-built facts, for testing the decision rules without any HTTP ---------------------

from diagnose.crawler import BOTS, CrawlerTest  # noqa: E402
from diagnose.facts import Facts  # noqa: E402
from diagnose.fetch import FetchResult  # noqa: E402
from diagnose.html_utils import parse_page  # noqa: E402
from diagnose.platform_detect import Detection  # noqa: E402
from diagnose.render import check_render  # noqa: E402
from diagnose.robots import RobotsCheck  # noqa: E402
from diagnose.structured_data import analyse  # noqa: E402


def make_facts(*, platform=None, plan=None, bot_verdicts=None, hosting=(), html=None, business=None,
               robots_allowed=None, robots_outcome="ok", browser_ok=None, reachable=True, jsonld=None):
    """Facts for one site. `bot_verdicts` maps bot name -> verdict (others are 'allowed')."""
    html = html if html is not None else page(500)
    parsed = parse_page(html)
    control = FetchResult(url="https://example.com/", user_agent="browser", status=200,
                          final_url="https://example.com/", text=html, size=len(html), body_hash="x")
    bot_verdicts = bot_verdicts or {}
    crawler = CrawlerTest(
        control=control,
        bot_results={b.name: control for b in BOTS},
        verdicts={b.name: (bot_verdicts.get(b.name, "allowed"), f"detail for {b.name}") for b in BOTS},
    )
    robots = RobotsCheck(url="https://example.com/robots.txt", status=200, fetched_with="OAI-SearchBot/1.4",
                         raw="User-agent: *\nAllow: /", outcome=robots_outcome, browser_ok=browser_ok,
                         allowed={b.token: (robots_allowed or {}).get(b.token, True) for b in BOTS})
    names = [business["name"]] if business else None
    facts = Facts(
        url="https://example.com/", page=control, detection=Detection(platform=platform, plan=plan, hosting=list(hosting)),
        business=business, crawler=crawler, robots=robots, site_reachable=reachable,
        render=check_render(parsed, html, names=names, phone=(business or {}).get("phone"),
                            address=(business or {}).get("address")),
        jsonld_raw=parsed.jsonld_raw,
    )
    facts.schema = analyse(parsed.jsonld_raw, facts.business_names)
    return facts


SPA_HTML = ('<!doctype html><html><head><title>App</title>' + '<script src="/bundle.js"></script>' * 1
            + '</head><body><div id="root"></div>' + '<script>' + 'x' * 25000 + '</script></body></html>')
