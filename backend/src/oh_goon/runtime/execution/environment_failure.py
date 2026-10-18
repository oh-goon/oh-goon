from __future__ import annotations

_ENVIRONMENT_FAILURE_HINTS = (
    "BlockingIOError: [Errno 11] Resource temporarily unavailable",
    "Resource temporarily unavailable",
    "RuntimeError: can't start new thread",
    "can't start new thread",
    "No space left on device",
    "Too many open files",
)


def has_environment_failure_hint(rendered: str) -> bool:
    """Return True only for bounded host/runtime resource failures.

    This classifier deliberately does not treat assertion failures, generic non-zero
    exits, dependency errors, or timeouts as environment failures. Retry policy owns
    whether a classified environment failure is retryable.
    """

    return any(hint in rendered for hint in _ENVIRONMENT_FAILURE_HINTS)
