from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from nypd_weekly_archive.net import USER_AGENT, Fetcher

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


def make_fetcher(handler: Callable[[httpx.Request], httpx.Response], sleeps: list[float] | None = None) -> Fetcher:
    """A real Fetcher over httpx.MockTransport, with sleeping recorded instead of done."""
    rec = sleeps if sleeps is not None else []
    client = httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": USER_AGENT})
    clock = iter(range(0, 10**9, 10))  # each request 10 s apart, so pacing never sleeps
    return Fetcher(client=client, sleep=rec.append, clock=lambda: float(next(clock)))
