from __future__ import annotations

import httpx2
import pytest

from oh_goon.integrations.gitlab_ci_component import (
    GitLabComponentTransportError,
    Httpx2GitLabComponentTransport,
)


def test_httpx2_transport_delegates_to_required_client(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, dict[str, str], dict[str, str], float]] = []

    def fake_get(url, *, params, headers, timeout):
        seen.append((url, params, headers, timeout))
        return httpx2.Response(200, text="ok")

    monkeypatch.setattr(httpx2, "get", fake_get)
    response = Httpx2GitLabComponentTransport().get(
        "https://gitlab.example/api",
        params={"ref": "1.2.3"},
        headers={"PRIVATE-TOKEN": "secret"},
        timeout=20.0,
    )
    assert response.status_code == 200
    assert response.text == "ok"
    assert seen == [
        (
            "https://gitlab.example/api",
            {"ref": "1.2.3"},
            {"PRIVATE-TOKEN": "secret"},
            20.0,
        )
    ]


def test_httpx2_transport_normalizes_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args, **kwargs):
        del args, kwargs
        raise httpx2.ConnectError("offline")

    monkeypatch.setattr(httpx2, "get", fail)
    with pytest.raises(GitLabComponentTransportError, match="offline"):
        Httpx2GitLabComponentTransport().get(
            "https://gitlab.example/api",
            params={"ref": "1.2.3"},
            headers={},
            timeout=20.0,
        )
