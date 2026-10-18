from fastapi import FastAPI
from fastapi.testclient import TestClient

from oh_goon.api.local_access import LOCAL_ACCESS_HEADER


def authenticated_api_client(app: FastAPI) -> TestClient:
    token = str(app.state.local_access_token)
    return TestClient(app, headers={LOCAL_ACCESS_HEADER: token})
