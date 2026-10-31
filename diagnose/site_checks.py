"""The checks that apply to any website, not to one platform or one client (PLAN.md step 25):

  1. the sitemap                 is there one, is it valid, does robots.txt list it, is it on the right host
  2. www / non-www, http / https do the addresses agree, and are the redirects permanent
  3. old addresses (optional)    where do addresses from an earlier site end up

This file only measures. It makes the requests and records what came back; diagnose/findings.py turns the result into
findings. A failure to connect is recorded as a result, never raised.

Politeness, for every request made here: a clearly named user agent, a 10 second timeout, at most 5 redirect hops, and
no more than 2 requests per second (the Fetcher spaces them). The old-address check never follows more than 5 hops and
never makes more than MAX_OLD_URL_REQUESTS requests.
"""

from __future__ import annotations

import gzip
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from diagnose.crawler import looks_like_bot_check
from diagnose.fetch import FetchResult, Fetcher, MAX_REDIRECT_HOPS, RequestBudgetExceeded

SITE_CHECK_UA = "AEO-Studio-Site-Check/1.0 (a site health check; at most 2 requests per second)"
SITE_CHECK_INTERVAL = 0.5               # seconds between requests: 2 per second
MAX_CHILD_SITEMAPS = 10
MAX_ROBOTS_SITEMAPS = 5                 # sitemaps named in robots.txt that we are willing to fetch
SITE_CHECK_REQUESTS = 40                # sitemap + hosts, all together
OLD_URL_CAP = 200
MAX_OLD_URL_REQUESTS = OLD_URL_CAP * (MAX_REDIRECT_HOPS + 1)
EXAMPLES_SHOWN = 10
MIN_CONTENT_CHARS = 100                 # a 200 with less than this is not "the site"
TEMPORARY = (302, 307)
REDIRECTS = (301, 302, 303, 307, 308)


class SiteCheckError(Exception):
    """The old-address file cannot be used. The message says what to fix."""


def bare_host(host: str) -> str:
    return host.lower().removeprefix("www.")


def _norm(url: str) -> str:
    parts = urlsplit(url.strip())
    return f"{parts.scheme.lower()}://{(parts.hostname or '').lower()}{parts.path.rstrip('/')}"


# ---- 1. the sitemap -----------------------------------------------------------------------------------------------------

def parse_sitemap(body: bytes) -> tuple[str, list[str]]:
    """('urlset' | 'index' | 'not_xml' | 'other_xml', the <loc> addresses). A gzipped file is unpacked first."""
    if body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except (OSError, EOFError):
            return "not_xml", []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return "not_xml", []
    tag = root.tag.rsplit("}", 1)[-1].lower()
    if tag not in ("urlset", "sitemapindex"):
        return "other_xml", []
    locs = [(el.text or "").strip() for el in root.iter() if el.tag.rsplit("}", 1)[-1].lower() == "loc"]
    return ("urlset" if tag == "urlset" else "index"), [l for l in locs if l]


def robots_sitemaps(robots_raw: str | None, origin: str) -> list[str]:
    """The `Sitemap:` lines of a robots.txt, made absolute, in order, without repeats."""
    found = []
    for match in re.finditer(r"(?im)^\s*sitemap\s*:\s*(\S+)", robots_raw or ""):
        url = urljoin(origin + "/", match.group(1))
        if url not in found:
            found.append(url)
    return found


@dataclass
class SitemapCheck:
    canonical_host: str
    robots_checked: bool                           # False when robots.txt was not readable, so "listed or not" is unknown
    robots_listed: list[str] = field(default_factory=list)
    fetched: list[dict] = field(default_factory=list)        # one row per sitemap file requested
    reachable: bool = False                        # some candidate answered 200
    valid: bool = False                            # some candidate was valid XML with at least one address
    urls: int = 0
    in_robots: bool | None = None                  # a valid sitemap is named in robots.txt (None = not checked)
    hosts_in_sitemap: dict[str, int] = field(default_factory=dict)
    mismatched_urls: int = 0                       # addresses on the other of www / non-www
    mismatch_examples: list[str] = field(default_factory=list)
    children_followed: int = 0
    children_not_followed: int = 0
    stopped_early: str | None = None
    blocked: str | None = None                     # why a request came back as a bot check: then "missing" means nothing

    @property
    def found(self) -> bool:
        return self.reachable

    def to_json(self) -> dict:
        return {"canonical_host": self.canonical_host, "found": self.found, "valid": self.valid, "urls": self.urls,
                "robots_checked": self.robots_checked, "robots_listed": self.robots_listed, "in_robots": self.in_robots,
                "host_matches_canonical": (self.mismatched_urls == 0) if self.valid else None,
                "hosts_in_sitemap": self.hosts_in_sitemap, "mismatched_urls": self.mismatched_urls,
                "mismatch_examples": self.mismatch_examples, "children_followed": self.children_followed,
                "children_not_followed": self.children_not_followed, "fetched": self.fetched,
                "stopped_early": self.stopped_early, "blocked": self.blocked}


def check_sitemap(fetcher: Fetcher, origin: str, robots_raw: str | None, robots_ok: bool) -> SitemapCheck:
    """Looks at /sitemap.xml and at every sitemap robots.txt names (at most MAX_ROBOTS_SITEMAPS). A sitemap index is
    followed into its child sitemaps, MAX_CHILD_SITEMAPS in all."""
    canonical = urlsplit(origin).hostname or ""
    listed = robots_sitemaps(robots_raw, origin) if robots_ok else []
    result = SitemapCheck(canonical_host=canonical, robots_checked=robots_ok, robots_listed=listed)
    candidates = [f"{origin}/sitemap.xml"] + [u for u in listed[:MAX_ROBOTS_SITEMAPS] if _norm(u) != _norm(f"{origin}/sitemap.xml")]
    valid_roots: list[str] = []
    all_addresses: list[str] = []

    def fetch(url: str, *, child: bool) -> tuple[str, list[str], FetchResult]:
        got = fetcher.get(url, SITE_CHECK_UA)
        kind, locs = "not_xml", []
        if result.blocked is None and (why := looks_like_bot_check(got)):
            result.blocked = f"{url}: {why}"
        if got.status == 200:
            result.reachable = True
            kind, locs = parse_sitemap(got.body)
        row = {"url": url, "status": got.status, "error": got.error, "kind": kind if got.status == 200 else None,
               "addresses": len(locs) if kind == "urlset" else None, "child": child,
               "content_type": got.headers.get("content-type")}
        result.fetched.append(row)
        return kind, locs, got

    try:
        for url in candidates:
            kind, locs, got = fetch(url, child=False)
            if got.status != 200:
                continue
            if kind == "urlset" and locs:
                valid_roots.append(url)
                all_addresses += locs
            elif kind == "index" and locs:
                found_urls = 0
                for child_url in locs:
                    if result.children_followed >= MAX_CHILD_SITEMAPS:
                        result.children_not_followed += 1
                        continue
                    result.children_followed += 1
                    ckind, clocs, cgot = fetch(child_url, child=True)
                    if cgot.status == 200 and ckind == "urlset" and clocs:
                        found_urls += len(clocs)
                        all_addresses += clocs
                if found_urls:
                    valid_roots.append(url)
    except RequestBudgetExceeded as exc:
        result.stopped_early = str(exc)

    result.valid = bool(valid_roots)
    result.urls = len(all_addresses) if result.valid else 0
    if result.valid and robots_ok:
        listed_norm = {_norm(u) for u in listed}
        result.in_robots = any(_norm(root) in listed_norm for root in valid_roots)
    canon_bare = bare_host(canonical)
    for address in all_addresses:
        host = (urlsplit(address).hostname or "").lower()
        result.hosts_in_sitemap[host] = result.hosts_in_sitemap.get(host, 0) + 1
        if host != canonical.lower() and bare_host(host) == canon_bare:
            result.mismatched_urls += 1
            if len(result.mismatch_examples) < EXAMPLES_SHOWN:
                result.mismatch_examples.append(address)
    return result


# ---- 2. www / non-www and http / https --------------------------------------------------------------------------------------

@dataclass
class HostRow:
    host: str
    status: int | None = None            # the first answer, redirects not followed
    location: str | None = None
    redirects_to_host: str | None = None
    error: str | None = None
    dns_failure: bool = False            # the name does not exist, as opposed to the server not answering
    has_content: bool = False
    followed_status: int | None = None   # where the redirect ends up (only asked when there was a redirect)
    followed_url: str | None = None
    blocked: str | None = None           # the first answer looks like a bot check

    @property
    def answered_200(self) -> bool:
        return self.status == 200 and self.has_content

    @property
    def redirects(self) -> bool:
        return self.status in REDIRECTS and bool(self.location)

    @property
    def failed_to_connect(self) -> bool:
        return self.status is None

    def to_json(self) -> dict:
        return {"host": self.host, "status": self.status, "location": self.location,
                "redirects_to_host": self.redirects_to_host, "error": self.error, "dns_failure": self.dns_failure,
                "has_content": self.has_content, "followed_status": self.followed_status, "followed_url": self.followed_url,
                "blocked": self.blocked}


@dataclass
class HostsCheck:
    canonical_host: str
    apex: HostRow
    www: HostRow
    http_status: int | None = None
    http_final_url: str | None = None
    http_error: str | None = None
    stopped_early: str | None = None
    http_blocked: str | None = None

    @property
    def blocked(self) -> str | None:
        """Why any of these requests looked like a bot check, if one did. Then nothing about the addresses can be claimed."""
        return self.apex.blocked or self.www.blocked or self.http_blocked

    @property
    def http_ends_at_https(self) -> bool | None:
        """None when plain http did not answer at all (many sites do not listen on port 80)."""
        if self.http_status is None or not self.http_final_url:
            return None
        return urlsplit(self.http_final_url).scheme == "https"

    def to_json(self) -> dict:
        return {"canonical_host": self.canonical_host, "apex": self.apex.to_json(), "www": self.www.to_json(),
                "http": {"status": self.http_status, "final_url": self.http_final_url, "error": self.http_error,
                         "ends_at_https": self.http_ends_at_https},
                "stopped_early": self.stopped_early, "blocked": self.blocked}


def _row(fetcher: Fetcher, host: str, scheme: str = "https") -> HostRow:
    row = HostRow(host=host)
    first = fetcher.get(f"{scheme}://{host}/", SITE_CHECK_UA, follow_redirects=False)
    row.status, row.error, row.dns_failure = first.status, first.error, first.dns_failure
    if why := looks_like_bot_check(first):
        row.blocked = f"{host}: {why}"
    row.has_content = first.status == 200 and len(first.text.strip()) >= MIN_CONTENT_CHARS
    location = first.headers.get("location")
    if first.status in REDIRECTS and location:
        row.location = urljoin(first.url, location)
        row.redirects_to_host = (urlsplit(row.location).hostname or "").lower()
        # And once more following the redirect all the way (the client stops after MAX_REDIRECT_HOPS), to see where it ends.
        end = fetcher.get(first.url, SITE_CHECK_UA)
        row.followed_status, row.followed_url = end.status, end.final_url or None
    return row


def check_hosts(fetcher: Fetcher, canonical_host: str) -> HostsCheck:
    bare = bare_host(canonical_host)
    apex, www = HostRow(host=bare), HostRow(host=f"www.{bare}")
    result = HostsCheck(canonical_host=canonical_host.lower(), apex=apex, www=www)
    try:
        result.apex = _row(fetcher, bare)
        result.www = _row(fetcher, f"www.{bare}")
        plain = fetcher.get(f"http://{canonical_host}/", SITE_CHECK_UA)
        result.http_status, result.http_final_url, result.http_error = plain.status, plain.final_url or None, plain.error
        if why := looks_like_bot_check(plain):
            result.http_blocked = f"http://{canonical_host}/: {why}"
    except RequestBudgetExceeded as exc:
        result.stopped_early = str(exc)
    return result


# ---- 3. old addresses ---------------------------------------------------------------------------------------------------------

def load_old_urls(path: str | Path, site_origin: str) -> tuple[list[str], dict]:
    """(the addresses to check, what was left out). One per line; blank lines and # comments are ignored. A line may be a
    full address or a path starting with / (taken to be on the site being diagnosed). Anything else is left out and
    counted. More than OLD_URL_CAP: only the first OLD_URL_CAP are used."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SiteCheckError(f"cannot read the old-address file {path}: {exc}") from exc
    urls, ignored = [], []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if re.match(r"^https?://", line, re.I):
            urls.append(line)
        elif line.startswith("/"):
            urls.append(site_origin + line)
        else:
            ignored.append(line)
    urls = list(dict.fromkeys(urls))
    over = max(0, len(urls) - OLD_URL_CAP)
    return urls[:OLD_URL_CAP], {"total_in_file": len(urls), "over_cap": over, "ignored_lines": ignored}


@dataclass
class OldUrlsCheck:
    file_info: dict
    results: list[dict] = field(default_factory=list)
    stopped_early: str | None = None

    def with_issue(self, code: str) -> list[dict]:
        return [r for r in self.results if code in r["issues"]]

    def to_json(self, full: bool = False) -> dict:
        """full=True is the evidence file: every address and what happened to it. Otherwise (the database row and the
        report) it is the counts and up to EXAMPLES_SHOWN examples of each problem."""
        counts = {code: len(self.with_issue(code)) for code in OLD_URL_CODES}
        out = {**self.file_info, "checked": len(self.results), "counts": counts,
               "unreachable": sum(1 for r in self.results if r["error"]), "stopped_early": self.stopped_early}
        if full:
            out["results"] = self.results
        else:
            out["examples"] = {code: [example_line(r) for r in self.with_issue(code)[:EXAMPLES_SHOWN]]
                               for code in OLD_URL_CODES if counts[code]}
        return out


OLD_URL_CODES = ("OLD_URL_404", "OLD_URL_NOT_PERMANENT", "OLD_URL_CHAIN", "OLD_URL_TO_HOME")


def _is_homepage(url: str) -> bool:
    return urlsplit(url).path in ("", "/")


def check_one_old_url(fetcher: Fetcher, url: str) -> dict:
    """Asks for the address with redirects NOT followed, then follows them one at a time (at most MAX_REDIRECT_HOPS), so
    every step is seen."""
    hops: list[dict] = []
    current = url
    final: FetchResult | None = None
    too_many = False
    for step in range(MAX_REDIRECT_HOPS + 1):
        got = fetcher.get(current, SITE_CHECK_UA, follow_redirects=False)
        location = got.headers.get("location")
        if got.status in REDIRECTS and location:
            if step == MAX_REDIRECT_HOPS:
                too_many, final = True, got
                break
            nxt = urljoin(current, location)
            hops.append({"status": got.status, "from": current, "to": nxt})
            current = nxt
            continue
        final = got
        break
    assert final is not None
    issues = []
    if final.status in (404, 410):
        issues.append("OLD_URL_404")
    if any(h["status"] in TEMPORARY for h in hops):
        issues.append("OLD_URL_NOT_PERMANENT")
    if len(hops) > 1:
        issues.append("OLD_URL_CHAIN")
    if hops and not _is_homepage(url) and _is_homepage(current) and final.status is not None and final.status < 400:
        issues.append("OLD_URL_TO_HOME")
    return {"url": url, "first_status": hops[0]["status"] if hops else final.status, "hops": hops,
            "final_url": current, "final_status": final.status, "error": final.error,
            "too_many_redirects": too_many, "issues": issues}


def check_old_urls(fetcher: Fetcher, urls: list[str], file_info: dict) -> OldUrlsCheck:
    result = OldUrlsCheck(file_info=file_info)
    try:
        for url in urls:
            result.results.append(check_one_old_url(fetcher, url))
    except RequestBudgetExceeded as exc:
        result.stopped_early = str(exc)
    return result


def example_line(r: dict) -> str:
    """'https://old/x -> https://new/y (301, then 200)' for one old address."""
    if r["error"]:
        return f"{r['url']} -> no answer ({r['error']})"
    steps = [str(h["status"]) for h in r["hops"]] + [str(r["final_status"])]
    where = r["final_url"] if r["hops"] else "no redirect"
    return f"{r['url']} -> {where} ({', then '.join(steps)})"


# ---- all of it ---------------------------------------------------------------------------------------------------------------------

@dataclass
class SiteChecks:
    sitemap: SitemapCheck | None = None
    hosts: HostsCheck | None = None
    old_urls: OldUrlsCheck | None = None
    requests_made: int = 0             # how many requests these checks made, for the report's total
    # The checks that read the page itself (render, structured_data, llms), when they ran. Kept here so one list says
    # which checks ran, and compare can tell "fixed" from "not checked this time".
    content_ran: list[str] = field(default_factory=list)
    # [{"check", "label", "reason", "codes"}]: checks that did NOT run, or were blocked, and why (step 25c).
    # A finding that depends on one of these is never raised, and compare never calls it fixed or new.
    not_checked: list[dict] = field(default_factory=list)

    @property
    def checks_ran(self) -> list[str]:
        """The step 25 checks that ran and were not blocked. The page checks are in `content_ran`."""
        return [name for name, value in (("sitemap", self.sitemap), ("hosts", self.hosts),
                                         ("old_urls", self.old_urls)) if value is not None]

    def to_json(self) -> dict:
        return {"checks_ran": self.checks_ran, "content_ran": self.content_ran, "not_checked": self.not_checked,
                "sitemap": self.sitemap.to_json() if self.sitemap else None,
                "hosts": self.hosts.to_json() if self.hosts else None,
                "old_urls": self.old_urls.to_json() if self.old_urls else None}

