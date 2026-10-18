from __future__ import annotations

from collections.abc import Mapping
import urllib.request

from oh_goon.api.local_access import LOCAL_ACCESS_HEADER, new_local_access_token


def new_certification_access_token() -> str:
    """Return one ephemeral capability shared only by a spawned certification server/client."""

    return new_local_access_token()


def authorized_request(
    url: str,
    *,
    access_token: str,
    data: bytes | None = None,
    method: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> urllib.request.Request:
    merged = dict(headers or {})
    merged[LOCAL_ACCESS_HEADER] = access_token
    return urllib.request.Request(url, data=data, method=method, headers=merged)
