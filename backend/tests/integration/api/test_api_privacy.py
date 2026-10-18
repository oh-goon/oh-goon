from tests.integration.api.client import authenticated_api_client

from oh_goon.api.app import app as api_app


def test_privacy_endpoint_reports_durable_local_state() -> None:
    client = authenticated_api_client(api_app)
    response = client.get("/api/privacy")

    assert response.status_code == 200
    assert response.json() == {
        "mode": "LOCAL_ONLY",
        "loopback_only": True,
        "browser_preferences": True,
        "persistent_repository_state": True,
        "local_database_storage": True,
        "local_database_file": response.json()["local_database_file"],
        "telemetry": False,
        "source_upload": False,
    }


def test_api_adds_local_only_security_headers() -> None:
    client = authenticated_api_client(api_app)
    response = client.get("/api/privacy")

    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert "connect-src 'self'" in response.headers["content-security-policy"]
