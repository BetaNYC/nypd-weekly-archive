"""Structured (one JSON object per line) logging to stderr."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

SCRIPT = "nypd-weekly-archive"


def log(event: str, **fields: object) -> None:
    record = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "script": SCRIPT,
        "event": event,
        **fields,
    }
    print(json.dumps(record, default=str), file=sys.stderr, flush=True)
