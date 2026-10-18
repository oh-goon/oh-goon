from fastapi.testclient import TestClient

from oh_goon.api.app import create_app
from oh_goon.api.local_access import (
    LOCAL_ACCESS_COOKIE,
    LOCAL_ACCESS_HEADER,
    LOCAL_ACCESS_EXCHANGE_PATH,
)

TOKEN = "local-access-test-token"


def test_liveness_is_public_but_domain_api_is_fail_closed() -> None:
    app = create_app(local_access_token=TOKEN)
    client = TestClient(app)

    assert client.get("/healthz").status_code == 204

    unauthorized = client.get("/api/does-not-exist")
    assert unauthorized.status_code == 401
    assert unauthorized.json()["code"] == "LOCAL_ACCESS_REQUIRED"

    wrong = client.get(
        "/api/does-not-exist", headers={LOCAL_ACCESS_HEADER: "wrong-token"}
    )
    assert wrong.status_code == 401

    authorized = client.get(
        "/api/does-not-exist", headers={LOCAL_ACCESS_HEADER: TOKEN}
    )
    assert authorized.status_code == 404


def test_browser_exchange_creates_http_only_strict_api_cookie() -> None:
    app = create_app(local_access_token=TOKEN)
    client = TestClient(app)

    assert client.get(LOCAL_ACCESS_EXCHANGE_PATH).status_code == 401
    assert client.post(
        LOCAL_ACCESS_EXCHANGE_PATH,
        headers={LOCAL_ACCESS_HEADER: "wrong-token"},
    ).status_code == 401

    exchange = client.post(
        LOCAL_ACCESS_EXCHANGE_PATH,
        headers={LOCAL_ACCESS_HEADER: TOKEN},
    )
    assert exchange.status_code == 204
    cookie = exchange.headers["set-cookie"]
    assert f"{LOCAL_ACCESS_COOKIE}=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/api" in cookie

    assert client.get(LOCAL_ACCESS_EXCHANGE_PATH).status_code == 204
    assert client.get("/api/does-not-exist").status_code == 404
