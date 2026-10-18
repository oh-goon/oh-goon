from __future__ import annotations

import time
from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def monotonic_now() -> float:
    """Return the process monotonic clock through one runtime timing authority."""
    return time.monotonic()


def wait_seconds(delay: float) -> None:
    """Block for a bounded duration through one runtime wait authority."""
    if delay > 0:
        time.sleep(delay)
