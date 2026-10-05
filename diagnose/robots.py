"""robots.txt: fetched AS A BOT, parsed to RFC 9309 with `protego`, plus the
Content-Signal.

Why "as a bot": under RFC 9309 a robots.txt that answers 5xx, or that is
behind a challenge, means "disallow everything" for a compliant crawler, even
if the file itself is permissive. A browser would never see that. So we ask
for it the way a bot does, and ask once as a browser to tell "the site is
down" apart from "the site is refusing bots".

Why protego and not urllib.robotparser: the standard-library parser has no
`$` anchor support and returns the first matching rule instead of the longest
match, so it gives wrong answers on real files. protego follows the RFC.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from protego import Protego

from diagnose.crawler import BOTS, BROWSER_UA, challenge_reason
from diagnose.fetch import FetchResult, Fetcher


@dataclass
class RobotsCheck:
    url: str
    status: int | None
    fetched_with: str
    raw: str | None = None                 # exactly as served (None if nothing usable came back)
    outcome: str = "ok"                    # ok | missing | unreachable | challenged
    browser_status: int | None = None      # what the browser control got, when we asked
    browser_ok: bool | None = None         # None = we did not ask; True = the browser got a clean answer
    served_as_html: bool = False
    content_signal: str | None = None
    content_signal_source: str | None = None   # "robots.txt header" | "robots.txt file" | "page header"
    allowed: dict[str, bool] = field(default_factory=dict)   # product token -> may fetch the page
    notes: list[str] = field(default_factory=list)

    @property
    def total_disallow(self) -> bool:
        """RFC 9309: unreachable (5xx / network error) or challenged = disallow everything."""
        return self.outcome in ("unreachable", "challenged")

    @property
    def bot_fetch_failed_but_browser_ok(self) -> bool:
        return self.total_disallow and self.browser_ok is True

    def to_json(self) -> dict:
        return {
            "url": self.url, "status": self.status, "fetched_with": self.fetched_with,
            "outcome": self.outcome, "browser_status": self.browser_status, "browser_ok": self.browser_ok,
            "served_as_html": self.served_as_html, "content_signal": self.content_signal,
            "content_signal_source": self.content_signal_source,
            "allowed": self.allowed, "notes": self.notes,
        }


def robots_url_for(page_url: str) -> str:
    """robots.txt belongs to one origin: www.example.com and example.com have different files."""
    parts = urlsplit(page_url)
    return f"{parts.scheme}://{parts.netloc}/robots.txt"


def parse_content_signal(text: str) -> dict[str, str]:
    """'search=yes, ai-train=no' -> {'search': 'yes', 'ai-train': 'no'}"""
    out = {}
    for item in text.split(","):
        key, _, value = item.partition("=")
        if key.strip() and value.strip():
            out[key.strip().lower()] = value.strip().lower()
    return out


def check_robots(fetcher: Fetcher, page_url: str, page_headers: dict[str, str]) -> RobotsCheck:
    url = robots_url_for(page_url)
    bot = BOTS[0]  # fetch as the first retrieval bot
    got = fetcher.get(url, bot.user_agent)
    check = RobotsCheck(url=url, status=got.status, fetched_with=bot.user_agent)

    # --- RFC 9309 section 2.3.1: what each kind of answer means ---------------
    if got.error or got.status is None or got.status >= 500:
        check.outcome = "unreachable"
        check.notes.append("RFC 9309: robots.txt unreachable (5xx or no answer) means disallow everything.")
    elif challenge_reason(got):
        check.outcome = "challenged"
        check.notes.append("robots.txt is behind a challenge page: a compliant bot treats that as disallow everything.")
    elif got.status >= 400:
        check.outcome = "missing"   # 4xx = "unavailable": the crawler may access everything
        check.notes.append(f"HTTP {got.status}: no robots.txt, so every crawler is allowed.")
    else:
        check.outcome = "ok"
        check.raw = got.text

    # When the bot's fetch looks wrong, ask once as a browser to see who is at fault.
    if check.outcome in ("unreachable", "challenged") or (check.outcome == "missing" and got.status in (401, 403)):
        control = fetcher.get(url, BROWSER_UA)
        check.browser_status = control.status
        # "ok" means the browser got a real answer: not an error, not a 5xx, not a challenge page.
        check.browser_ok = (control.status is not None and control.status < 500
                            and not challenge_reason(control))
        if check.outcome == "missing" and check.browser_ok and control.status == 200:
            check.notes.append(f"The bot got HTTP {got.status} for robots.txt but the browser got 200: the site treats bots differently.")

    if check.raw is not None:
        head = check.raw.lstrip()[:200].lower()
        if head.startswith("<!doctype") or head.startswith("<html") or "text/html" in got.headers.get("content-type", "").lower():
            check.served_as_html = True
            check.notes.append("robots.txt was served as HTML (common on single-page-app hosts); a crawler may parse it as junk.")

    # --- Content-Signal: a header, or a line inside the file --------------------
    _find_content_signal(check, got, page_headers)

    # --- Which bots may fetch the page? ------------------------------------------
    parser = Protego.parse(check.raw or "") if check.raw is not None and not check.served_as_html else None
    for b in BOTS:
        if check.total_disallow:
            check.allowed[b.token] = False
        elif parser is None:
            check.allowed[b.token] = True
        else:
            check.allowed[b.token] = bool(parser.can_fetch(page_url, b.token))
    return check


def _find_content_signal(check: RobotsCheck, robots_response: FetchResult, page_headers: dict[str, str]) -> None:
    # Content-Signal is NOT access control: `ai-train=no` with `Allow: /` still lets a crawler in.
    if "content-signal" in robots_response.headers:
        check.content_signal = robots_response.headers["content-signal"]
        check.content_signal_source = "robots.txt header"
    elif check.raw:
        m = re.search(r"(?im)^\s*content-signal\s*:\s*(.+)$", check.raw)
        if m:
            check.content_signal = m.group(1).strip()
            check.content_signal_source = "robots.txt file"
    if check.content_signal is None and "content-signal" in page_headers:
        check.content_signal = page_headers["content-signal"]
        check.content_signal_source = "page header"
