from oh_goon.api.app import app as api_app
from tests.integration.api.client import authenticated_api_client


def test_health_endpoint() -> None:
    client = authenticated_api_client(api_app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert "tools" in response.json()
