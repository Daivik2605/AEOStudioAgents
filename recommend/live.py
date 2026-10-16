"""Reads the client's live robots.txt and sitemap. Read-only: these are plain GET requests through the same
polite fetcher `aeo diagnose` uses (one at a time, a hard cap on requests). Nothing is ever written to the site.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from diagnose.crawler import BOTS, BROWSER_UA, challenge_reason
from diagnose.fetch import Fetcher, RequestBudgetExceeded

MAX_SITEMAP_FILES = 4          # the index itself plus up to three child sitemaps
MAX_SITEMAP_URLS = 2000
MAIN_PAGES = 10                # pages named in llms.txt and used to test robots.txt rules
_FILE_EXT = re.compile(r"\.(jpe?g|png|gif|webp|svg|pdf|zip|xml|css|js|mp4|mp3)$", re.I)
_LOC = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</loc>", re.I | re.S)


@dataclass
class LiveSite:
    origin: str
    robots_text: str | None = None
    robots_source: str = "none"          # live | diagnosis | none
    sitemap_urls: list[str] = field(default_factory=list)
    sitemap_url: str | None = None       # the one to put in robots.txt (first one found)
    notes: list[str] = field(default_factory=list)


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _usable_robots(got) -> bool:
    return (got.status == 200 and not got.error and not challenge_reason(got)
            and not got.text.lstrip()[:200].lower().startswith(("<!doctype", "<html"))
            and "text/html" not in got.headers.get("content-type", "").lower())


def read_live_site(fetcher: Fetcher, site_url: str, diagnosis_robots: str | None) -> LiveSite:
    """robots.txt (as a bot, then as a browser, then the copy the diagnosis saved) and the sitemap."""
    origin = origin_of(site_url)
    live = LiveSite(origin=origin)
    try:
        got = fetcher.get(f"{origin}/robots.txt", BOTS[0].user_agent)
        if not _usable_robots(got):
            got = fetcher.get(f"{origin}/robots.txt", BROWSER_UA)
        if _usable_robots(got):
            live.robots_text, live.robots_source = got.text, "live"
        elif diagnosis_robots and diagnosis_robots.strip():
            live.robots_text, live.robots_source = diagnosis_robots, "diagnosis"
            live.notes.append("The live robots.txt could not be read just now, so the copy saved by the diagnosis was used.")
        else:
            live.notes.append("There is no readable robots.txt on the site, so a new one was started from scratch.")

        candidates = []
        if live.robots_text:
            candidates = [m.group(1) for l in live.robots_text.splitlines()
                          if (m := re.match(r"^\s*sitemap\s*:\s*(\S+)", l, re.I))]
        known_in_robots = bool(candidates)
        if not candidates:
            candidates = [f"{origin}/sitemap.xml"]
        live.sitemap_urls = _read_sitemaps(fetcher, candidates, origin, live)
        if live.sitemap_urls:
            live.sitemap_url = candidates[0] if not known_in_robots else None
        elif not known_in_robots:
            live.notes.append("No sitemap was found at /sitemap.xml, so no Sitemap line was added and llms.txt has no page list.")
    except RequestBudgetExceeded as exc:
        live.notes.append(f"Stopped reading the site early: {exc}.")
    return live


def _read_sitemaps(fetcher: Fetcher, start: list[str], origin: str, live: LiveSite) -> list[str]:
    """Page URLs from the sitemap(s). An index is followed one level. Only addresses on the client's own host count."""
    host = urlsplit(origin).hostname
    queue, seen, pages = list(start), set(), []
    files = 0
    while queue and files < MAX_SITEMAP_FILES:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        got = fetcher.get(url, BROWSER_UA)
        files += 1
        if got.status != 200 or got.error or challenge_reason(got):
            live.notes.append(f"Could not read the sitemap {url} (HTTP {got.status or 'no answer'}).")
            continue
        locs = _LOC.findall(got.text)
        if re.search(r"<sitemapindex", got.text, re.I):
            queue += [l for l in locs if urlsplit(l).hostname == host]
        else:
            pages += [l for l in locs if urlsplit(l).hostname == host]
        if len(pages) >= MAX_SITEMAP_URLS:
            break
    return list(dict.fromkeys(pages))[:MAX_SITEMAP_URLS]


def main_pages(origin: str, sitemap_urls: list[str], limit: int = MAIN_PAGES) -> list[str]:
    """The home page, then the pages nearest the top of the site (fewest path parts, then alphabetical)."""
    home = origin + "/"
    candidates = []
    for url in sitemap_urls:
        parts = urlsplit(url)
        path = parts.path or "/"
        if parts.query or _FILE_EXT.search(path) or path == "/":
            continue
        candidates.append((len([p for p in path.split("/") if p]), path.lower(), url))
    candidates.sort()
    return [home] + [u for _, _, u in candidates[: limit - 1]]
