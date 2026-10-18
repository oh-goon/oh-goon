from __future__ import annotations

import re

_BEARER_TOKEN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_AUTHORITY_SECRET = re.compile(
    r"([?&](?:token|key|secret|password|authorization)=)[^&#\s]+",
    re.IGNORECASE,
)
_NAMED_SECRET = re.compile(
    r"\b(token|api[_-]?key|secret|password|authorization)\s*([:=])\s*([^\s,;]+)",
    re.IGNORECASE,
)
_WINDOWS_USER_PATH = re.compile(r"\b[A-Za-z]:\\Users\\[^\\\s]+\\[^\s]*")
_UNIX_USER_PATH = re.compile(r"/(?:home|Users)/[^/\s]+/[^\s]*")
_URL_CREDENTIALS = re.compile(r"(https?://)[^/@:\s]+:[^/@\s]+@", re.IGNORECASE)


def redact_diagnostic_text(value: str) -> str:
    """Remove common credentials and local-user path disclosure from diagnostic text."""

    redacted = _BEARER_TOKEN.sub("Bearer [REDACTED]", value)
    redacted = _AUTHORITY_SECRET.sub(r"\1[REDACTED]", redacted)
    redacted = _NAMED_SECRET.sub(r"\1\2[REDACTED]", redacted)
    redacted = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", redacted)
    redacted = _WINDOWS_USER_PATH.sub("[LOCAL_PATH]", redacted)
    return _UNIX_USER_PATH.sub("[LOCAL_PATH]", redacted)
