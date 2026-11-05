"""HTTP transport preserves ecosystem and scoped package identity."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from oh_goon.api.dependencies import dependency_fleet_service
from oh_goon.api.routes.dependencies import router


class _RecordingService:
    def __getattr__(self, method: str) -> Any:
        def invoke(*args: Any, **kwargs: Any) -> JSONResponse:
            return JSONResponse({"method": method, "arguments": args, "parameters": kwargs})

        return invoke


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[dependency_fleet_service] = _RecordingService
    with TestClient(app) as selected:
        yield selected


@pytest.mark.parametrize("name", ["library", "advisory", "repositories", "coverage-repositories"])
def test_scoped_package_name_cannot_be_mistaken_for_an_endpoint_suffix(
    client: TestClient, name: str
) -> None:
    package = "@team/" + name
    result = client.get("/dependencies/" + quote(package, safe=""), params={"ecosystem": "npm"})
    assert result.status_code == 200
    assert result.json() == {
        "method": "overview",
        "arguments": [package],
        "parameters": {"ecosystem": "npm"},
    }


@pytest.mark.parametrize(
    ("suffix", "method", "extra"),
    [
        ("", "overview", {}),
        ("/repositories", "repositories", {}),
        ("/coverage-repositories", "coverage_repositories", {"coverage": "no_current_evidence"}),
        ("/advisory", "advisory", {"advisory_id": "notice", "affected_specifier": "<2"}),
    ],
)
def test_package_routes_deliver_the_exact_identifier_and_explicit_ecosystem(
    client: TestClient, suffix: str, method: str, extra: dict[str, str]
) -> None:
    package = "@Team/Library_Name"
    response = client.get(
        "/dependencies/" + quote(package, safe="") + suffix,
        params={"ecosystem": "npm", **extra},
    )
    assert response.status_code == 200
    observed = response.json()
    assert observed["method"] == method
    assert observed["arguments"] == [package]
    assert observed["parameters"]["ecosystem"] == "npm"


def test_plain_package_defaults_and_mixed_search_are_preserved(client: TestClient) -> None:
    assert client.get("/dependencies/MLFlow").json() == {
        "method": "overview",
        "arguments": ["MLFlow"],
        "parameters": {"ecosystem": "pypi"},
    }
    search = client.get("/dependencies/search", params={"q": "same"}).json()
    assert search["method"] == "search" and search["parameters"]["ecosystem"] is None
    coverage = client.get("/dependencies/coverage", params={"ecosystem": "npm"}).json()
    assert coverage == {
        "method": "fleet_coverage",
        "arguments": [],
        "parameters": {"ecosystem": "npm"},
    }


def test_unsupported_ecosystem_is_rejected_by_the_public_contract(client: TestClient) -> None:
    response = client.get("/dependencies/package", params={"ecosystem": "unqualified"})
    assert response.status_code == 422


def test_search_transports_a_full_scoped_npm_identifier(client: TestClient) -> None:
    package = "@" + "s" * 100 + "/" + "p" * 112
    response = client.get("/dependencies/search", params={"q": package, "ecosystem": "npm"})
    assert response.status_code == 200
    assert response.json()["arguments"] == [package]


@pytest.mark.parametrize(
    ("suffix", "field", "extra"),
    [
        ("/repositories", "resolved_version", {}),
        ("/repositories", "version_prefix", {}),
        ("/repositories", "version_specifier", {}),
        ("/advisory", "affected_specifier", {"advisory_id": "notice"}),
    ],
)
def test_version_transport_accepts_native_text_and_bounds_it_before_service_work(
    client: TestClient, suffix: str, field: str, extra: dict[str, str]
) -> None:
    version = "1.0.0+" + "a" * 250
    path = "/dependencies/@scope/package" + suffix
    accepted = client.get(path, params={"ecosystem": "npm", field: version, **extra})
    assert accepted.status_code == 200
    assert accepted.json()["parameters"][field] == version
    rejected = client.get(path, params={"ecosystem": "npm", field: "a" * 4097, **extra})
    assert rejected.status_code == 422
