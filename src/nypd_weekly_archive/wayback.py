"""Optional: ask the Internet Archive's Save Page Now to capture each new file.

Interface from IA's "Save Page Now 2 (SPN2) Public API Docs"
(https://docs.google.com/document/d/1Nsv52MvSjbLb2PCpHlat0gkzw0EvtSgpKHu4mk0MnrA,
read 2026-11-21):
- Capture with GET https://web.archive.org/save/<url>.
- Anonymous limits: 3 captures per minute, 200 per day, 5 per URL per day.
- 429 means too many requests; IA then delays captures to that host.

So we send anonymous GETs no closer than 21 s apart, at most 150 per run
(under the 200/day cap), and stop at the first 429. Off unless
--save-to-wayback is passed. Not exercised against the live service during
the build.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from .log import log
from .net import USER_AGENT

SPN_PREFIX = "https://web.archive.org/save/"
MIN_INTERVAL = 21.0
MAX_PER_RUN = 150


def save_to_wayback(
    urls: list[str],
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    max_per_run: int = MAX_PER_RUN,
) -> dict[str, int]:
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120.0)
    stats = {"submitted": 0, "errors": 0, "skipped": 0}
    for i, url in enumerate(urls):
        if stats["submitted"] + stats["errors"] >= max_per_run:
            stats["skipped"] = len(urls) - i
            log("wayback_cap_reached", remaining=stats["skipped"])
            break
        if i:
            sleep(MIN_INTERVAL)
        try:
            resp = client.get(SPN_PREFIX + url)
        except httpx.TransportError as exc:
            stats["errors"] += 1
            log("wayback_error", url=url, error=str(exc))
            continue
        if resp.status_code == 429:
            stats["skipped"] = len(urls) - i
            log("wayback_rate_limited", url=url, remaining=stats["skipped"])
            break
        if resp.status_code >= 400:
            stats["errors"] += 1
            log("wayback_error", url=url, status=resp.status_code)
            continue
        stats["submitted"] += 1
        log("wayback_submitted", url=url, status=resp.status_code)
    return stats
