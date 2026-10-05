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
    size: int = 0                  # body bytes actually read
    body_hash: str = ""            # sha256 of the body with whitespace collapsed
    error: str | None = None
    truncated: bool = False

    @property
    def host(self) -> str:
        return urlsplit(self.final_url or self.url).hostname or ""


def make_client() -> httpx.Client:
    return httpx.Client(follow_redirects=True, max_redirects=5, timeout=httpx.Timeout(15.0))


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

    def get(self, url: str, user_agent: str) -> FetchResult:
        result = self._once(url, user_agent)
        if result.status == 429:
            wait = _retry_after_seconds(result.headers.get("retry-after"))
            if wait is not None and wait <= MAX_RETRY_AFTER_SECONDS:
                self._sleep(wait)
                result = self._once(url, user_agent)
        return result

    def _once(self, url: str, user_agent: str) -> FetchResult:
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
            with self.client.stream("GET", url, headers=headers) as response:
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
                result.text = body.decode(response.charset_encoding or "utf-8", errors="replace")
                result.size = len(body)
                result.body_hash = hashlib.sha256(" ".join(result.text.split()).encode()).hexdigest()
        except httpx.HTTPError as exc:
            result.error = f"{type(exc).__name__}: {exc}"
        return result


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
