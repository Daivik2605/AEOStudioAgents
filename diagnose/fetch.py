"""All outbound HTTP for `aeo diagnose` goes through here.

Rules baked in (PLATFORM_DELIVERY.md §7, "Ethics and rate limits"):
  - requests are one at a time, with at least `min_interval` seconds between
    them (default 1 second per origin)
  - a hard cap on the total number of requests per run (default 15)
  - a 429 is retried once, after Retry-After, and only if that wait is short
  - we never try to get past a challenge page: the caller records
    "challenged" and moves on

Tests pass in an httpx client wired to a fake transport, so no test ever
touches the network.
"""

from __future__ import annotations

import hashlib
import socket
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit

import httpx

MAX_BODY_BYTES = 5_000_000   # Wix homepages are ~3 MB; anything bigger is cut off
MAX_RETRY_AFTER_SECONDS = 10  # a longer Retry-After than this is reported, not waited out


class RequestBudgetExceeded(Exception):
    """Raised when a run tries to make more requests than its cap allows."""


@dataclass
class FetchResult:
    url: str                       # what we asked for
    user_agent: str
    status: int | None = None      # None if the request failed before any response
    final_url: str = ""            # after redirects
    headers: dict[str, str] = field(default_factory=dict)  # lowercase names
    text: str = ""
    body: bytes = field(default=b"", repr=False)   # the exact bytes read, for saving the raw page
    size: int = 0                  # body bytes actually read
    body_hash: str = ""            # sha256 of the body with whitespace collapsed
    error: str | None = None
    dns_failure: bool = False      # the host name does not exist (as opposed to a refused or timed-out connection)
    truncated: bool = False

    @property
    def host(self) -> str:
        return urlsplit(self.final_url or self.url).hostname or ""


REQUEST_TIMEOUT_SECONDS = 10
MAX_REDIRECT_HOPS = 5


def make_client() -> httpx.Client:
    return httpx.Client(follow_redirects=True, max_redirects=MAX_REDIRECT_HOPS,
                        timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS))


class Fetcher:
    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        max_requests: int = 15,
        min_interval: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client or make_client()
        self.max_requests = max_requests
        self.min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._last_request_at: float | None = None
        self.requests_made = 0

    def sibling(self, *, max_requests: int, min_interval: float | None = None) -> "Fetcher":
        """A second Fetcher with its own request count and cap that uses the SAME HTTP client, so anything wired to a
        fake transport (every test) stays on it. The site checks of step 25 use one, so they do not eat into the budget
        of the main diagnosis."""
        return Fetcher(self.client, max_requests=max_requests,
                       min_interval=self.min_interval if min_interval is None else min_interval,
                       sleep=self._sleep, clock=self._clock)

    def get(self, url: str, user_agent: str, *, follow_redirects: bool | None = None) -> FetchResult:
        """follow_redirects=None uses the client's setting (follow, at most MAX_REDIRECT_HOPS). False returns the
        first response as it is, so a 301 or 302 can be seen."""
        result = self._once(url, user_agent, follow_redirects)
        if result.status == 429:
            wait = _retry_after_seconds(result.headers.get("retry-after"))
            if wait is not None and wait <= MAX_RETRY_AFTER_SECONDS:
                self._sleep(wait)
                result = self._once(url, user_agent, follow_redirects)
        return result

    def _once(self, url: str, user_agent: str, follow_redirects: bool | None = None) -> FetchResult:
        if self.requests_made >= self.max_requests:
            raise RequestBudgetExceeded(f"request cap of {self.max_requests} reached")

        # Space requests out.
        if self._last_request_at is not None:
            gap = self._clock() - self._last_request_at
            if gap < self.min_interval:
                self._sleep(self.min_interval - gap)
        self._last_request_at = self._clock()
        self.requests_made += 1

        result = FetchResult(url=url, user_agent=user_agent)
        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
            "Accept-Language": "en",
        }
        try:
            extra = {} if follow_redirects is None else {"follow_redirects": follow_redirects}
            with self.client.stream("GET", url, headers=headers, **extra) as response:
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= MAX_BODY_BYTES:
                        result.truncated = True
                        break
                body = b"".join(chunks)
                result.status = response.status_code
                result.final_url = str(response.url)
                result.headers = _headers_to_dict(response.headers)
                result.body = body
                result.text = body.decode(response.charset_encoding or "utf-8", errors="replace")
                result.size = len(body)
                result.body_hash = hashlib.sha256(" ".join(result.text.split()).encode()).hexdigest()
        except httpx.HTTPError as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            result.dns_failure = _is_dns_failure(exc)
        return result


_DNS_MESSAGES = ("name or service not known", "nodename nor servname", "no address associated", "name resolution",
                 "getaddrinfo failed", "errno -2]", "errno -5]", "errno 8]", "nxdomain")


def _is_dns_failure(exc: BaseException) -> bool:
    """True when the failure is 'this host name does not exist', not 'the server did not answer'."""
    seen = exc
    while seen is not None:
        if isinstance(seen, socket.gaierror):
            return True
        seen = seen.__cause__ or seen.__context__
    return any(m in str(exc).lower() for m in _DNS_MESSAGES)


def _headers_to_dict(headers: httpx.Headers) -> dict[str, str]:
    """Lowercase names; repeated headers (like Link) are joined with ', '."""
    out: dict[str, str] = {}
    for name, value in headers.multi_items():
        name = name.lower()
        out[name] = f"{out[name]}, {value}" if name in out else value
    return out


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None  # an HTTP-date; treat as "too long to wait"
