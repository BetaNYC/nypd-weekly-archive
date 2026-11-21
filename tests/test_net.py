from __future__ import annotations

import httpx
import pytest

from nypd_weekly_archive.net import USER_AGENT, Fetcher, FetchError, parse_retry_after
from nypd_weekly_archive.wayback import MIN_INTERVAL, SPN_PREFIX, save_to_wayback

from .conftest import make_fetcher


def test_parse_retry_after() -> None:
    assert parse_retry_after("120") == 120.0
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=1445412480.0 - 30) == 30.0
    assert parse_retry_after("soon") is None
    assert parse_retry_after(None) is None


def test_retries_and_honors_retry_after() -> None:
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if len(calls) < 3:
            return httpx.Response(503, headers={"Retry-After": "45"})
        return httpx.Response(200, text="ok")

    sleeps: list[float] = []
    resp = make_fetcher(handler, sleeps).get("https://www.nyc.gov/x")
    assert resp.status_code == 200 and len(calls) == 3
    assert sleeps == [45.0, 45.0]
    assert calls[0].headers["User-Agent"] == USER_AGENT


def test_gives_up_after_max_retries() -> None:
    sleeps: list[float] = []
    f = make_fetcher(lambda r: httpx.Response(500), sleeps)
    with pytest.raises(FetchError, match="after 4 retries"):
        f.get("https://www.nyc.gov/x")
    assert sleeps == [2.0, 4.0, 8.0, 16.0]


def test_403_is_not_retried() -> None:
    with pytest.raises(FetchError, match="HTTP 403"):
        make_fetcher(lambda r: httpx.Response(403)).get("https://www.nyc.gov/x")


def test_pacing_is_at_least_one_second() -> None:
    sleeps: list[float] = []
    clock = iter([0.0, 0.2, 1.2])
    f = Fetcher(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
        sleep=sleeps.append,
        clock=lambda: next(clock),
    )
    f.get("https://www.nyc.gov/a")
    f.get("https://www.nyc.gov/b")
    assert sleeps == [pytest.approx(0.8)]
    with pytest.raises(ValueError):
        Fetcher(min_interval=0.5)


def test_wayback_spacing_and_stop_on_429() -> None:
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(429 if len(seen) == 3 else 200)

    sleeps: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    urls = [f"https://www.nyc.gov/f{i}.xlsx" for i in range(5)]
    stats = save_to_wayback(urls, client=client, sleep=sleeps.append)
    assert seen[0] == SPN_PREFIX + urls[0]
    assert stats == {"submitted": 2, "errors": 0, "skipped": 3}
    assert sleeps == [MIN_INTERVAL, MIN_INTERVAL] and MIN_INTERVAL >= 20


def test_wayback_daily_cap() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    stats = save_to_wayback([f"u{i}" for i in range(5)], client=client, sleep=lambda s: None, max_per_run=2)
    assert stats == {"submitted": 2, "errors": 0, "skipped": 3}
