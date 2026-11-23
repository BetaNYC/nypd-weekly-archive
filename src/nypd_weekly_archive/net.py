"""Polite HTTP: one request at a time, a minimum gap between requests,
capped exponential backoff, and Retry-After honored.

nyc.gov (Akamai) answers 403 to library-default user agents such as
``python-httpx/x`` and ``Python-urllib/x`` but 200 to a descriptive one
(tested 2026-11-21). We send an honest, descriptive UA; we do not pose as a
browser. robots.txt at https://www.nyc.gov/robots.txt disallows only
``/html/misc/`` (checked 2026-11-21).
"""

from __future__ import annotations

import email.utils
import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from . import __version__
from .log import log

USER_AGENT = f"nypd-weekly-archive/{__version__} (+https://beta.nyc)"
RETRY_STATUS = {429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    """A request failed after all retries. Callers must not swallow this."""


def parse_retry_after(value: str | None, now: float | None = None) -> float | None:
    """Retry-After is either delta-seconds or an HTTP-date (RFC 9110 §10.2.3)."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    now = time.time() if now is None else now
    return max(0.0, when.timestamp() - now)


class Fetcher:
    def __init__(
        self,
        min_interval: float = 1.0,
        max_retries: int = 4,
        max_backoff: float = 60.0,
        timeout: float = 60.0,
        retry_floor: float = 0.0,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if min_interval < 1.0:
            raise ValueError("min_interval must be at least 1 second (politeness floor)")
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.max_backoff = max_backoff
        # Minimum wait after a 429 or a 5xx (the Wayback backfill uses 60 s).
        self.retry_floor = retry_floor
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
        )

    def _pace(self) -> None:
        if self._last is not None:
            wait = self.min_interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()

    def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        """GET with retries. Returns 200 or 304 responses; raises FetchError otherwise."""
        return self.request("GET", url, headers=headers)

    def post_json(self, url: str, body: object) -> httpx.Response:
        return self.request("POST", url, json=body)

    def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        attempt = 0
        while True:
            self._pace()
            try:
                resp = self.client.request(method, url, **kwargs)  # type: ignore[arg-type]
            except httpx.TransportError as exc:
                resp = None
                err: str = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code in (200, 304):
                    return resp
                err = f"HTTP {resp.status_code}"
                if resp.status_code not in RETRY_STATUS:
                    raise FetchError(f"{url}: {err}")
            attempt += 1
            if attempt > self.max_retries:
                raise FetchError(f"{url}: {err} after {self.max_retries} retries")
            delay = min(self.max_backoff, 2.0**attempt)
            if resp is not None and resp.status_code in RETRY_STATUS:
                delay = max(delay, self.retry_floor)
            if resp is not None:
                ra = parse_retry_after(resp.headers.get("Retry-After"))
                if ra is not None:
                    delay = min(self.max_backoff * 5, max(delay, ra))
            log("retry", url=url, error=err, attempt=attempt, sleep_s=round(delay, 1))
            self._sleep(delay)


def http_date_to_iso(value: str | None) -> str:
    if not value:
        return ""
    try:
        return email.utils.parsedate_to_datetime(value).astimezone(UTC).isoformat()
    except (TypeError, ValueError):
        return ""


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
