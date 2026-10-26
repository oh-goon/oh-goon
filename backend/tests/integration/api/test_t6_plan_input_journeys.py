"""T6 real shipped-source authoring journeys over the actual HTTP adapter.

The only doubles are downstream League/run/diagnostic services that must NOT be
reached by inspection. The Plan store, actual shipped source/GOON tree, compiled
input constraints and HTTP schemas are all first-party production owners.
"""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

import pytest

from oh_goon.api import dependencies
from oh_goon.api.app import app as api_app
from oh_goon.runtime.shared_plans import SharedPlanFileStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import FleetPlanService
from oh_goon.services.shared_plans import SharedPlanService
from tests.factories.shipped_templates import GOON_TEMPLATES, PLAN_TEMPLATES
from tests.integration.api.client import authenticated_api_client

CORPUS_PATH = Path(__file__).with_name("fixtures") / "t6_journeys.json"
CORPUS = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
assert CORPUS["schema"] == "oh-goon.template-t6-journey-corpus/v1"
assert len({item["id"] for item in CORPUS["journeys"]}) == len(CORPUS["journeys"])
API = "/api/league"
SECRET = "T6_PRIVATE_TOKEN_SHOULD_NEVER_APPEAR"


@pytest.fixture
def journey(tmp_path):
    store = SharedPlanFileStore(tmp_path / "plans")
    runs, league, diagnostics = Mock(), Mock(), Mock()
    plans = FleetPlanService(
        FleetPlanResolver(goon_templates=GOON_TEMPLATES), Mock(), Mock(), Mock(), Mock()
    )
    resolve_real = plans.resolve_with_source
    plans.resolve_with_source = Mock(wraps=resolve_real)
    service = SharedPlanService(store, league, plans, runs, PLAN_TEMPLATES)
    api_app.dependency_overrides[dependencies.shared_plan_service] = lambda: service
    try:
        with closing(authenticated_api_client(api_app)) as client:
            yield client, store, plans, runs, league, diagnostics
    finally:
        api_app.dependency_overrides.pop(dependencies.shared_plan_service, None)


def _create_from_shipped(client, template_key):
    catalog = client.get(f"{API}/plan-templates")
    assert catalog.status_code == 200, catalog.text
    template = next(
        item for item in catalog.json()["templates"] if item["template_key"] == template_key
    )
    response = client.post(
        f"{API}/shared-plans/from-template",
        json={
            "template_key": template_key,
            "expected_template_sha256": template["sha256"],
        },
    )
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["sha256"] == template["sha256"]
    assert document["plan_key"] == template_key.replace("/", "-")
    assert (
        client.get(f"{API}/shared-plans/{document['plan_key']}").json()["sha256"]
        == document["sha256"]
    )
    return document


def _inspect(client, document, inputs, revision=None):
    return client.post(
        f"{API}/shared-plan-input-inspections",
        json={
            "plan_key": document["plan_key"],
            "expected_revision": revision or document["sha256"],
            "inputs": inputs,
        },
    )


@pytest.mark.parametrize(
    "inputs,expected_status",
    [
        ({}, 200),
        ({"typechecker": "pyright"}, 200),
        ({"project": ""}, 400),
        ({"dependency_group": None}, 200),
        ({"project": None}, 400),
    ],
)
def test_shared_plan_http_preserves_omitted_equal_empty_and_null_caller_values(
    journey, inputs, expected_status
):
    client, store, plans, runs, _, _ = journey
    document = _create_from_shipped(client, "python/quality")
    response = client.post(
        f"{API}/shared-plan-lock-previews",
        json={
            "plan_key": document["plan_key"],
            "expected_revision": document["sha256"],
            "inputs": inputs,
        },
    )
    assert response.status_code == expected_status, response.text
    assert plans.resolve_with_source.call_args.kwargs["inputs"] == inputs
    plans.resolve_with_source.assert_called_once()
    runs.history.assert_not_called()
    assert not (store.path(document["plan_key"]).parent / "goon.lock").exists()


def _assert_rejected(response, expected_code, expected_input=None):
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "rejected", body
    assert body["schema"] == "oh-goon.shared-plan-input-inspection/v1"
    assert body["execution"] == "not_started_by_inspection"
    assert body["plan_key"] is None and body["sha256"] is None and body["plan_id"] is None
    diagnostic = body["diagnostic"]
    assert diagnostic["schema"] == "oh-goon.plan-validation-diagnostic/v1"
    assert diagnostic["code"] == expected_code
    assert diagnostic["execution"] == "not_started_by_diagnostic"
    assert diagnostic["safe_next_action"]
    if expected_input is not None:
        assert expected_input in diagnostic["inputs"]
    assert SECRET not in response.text


def _assert_accepted(response, document):
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "accepted", body
    assert body["schema"] == "oh-goon.shared-plan-input-inspection/v1"
    assert body["plan_key"] == document["plan_key"]
    assert body["sha256"] == document["sha256"]
    assert body["plan_id"]
    assert body["diagnostic"] is None
    assert body["execution"] == "not_started_by_inspection"
    return body


@pytest.mark.parametrize(
    "spec", CORPUS["journeys"], ids=[item["id"] for item in CORPUS["journeys"]]
)
def test_t6_full_correction_journey_with_real_shipped_plan_and_goons(journey, spec):
    client, store, plans, runs, league, _ = journey
    document = _create_from_shipped(client, spec["template_key"])
    on_disk = store.path(document["plan_key"])
    original_bytes = on_disk.read_bytes()

    prior = plans.resolve_with_source.call_count
    rejected = _inspect(client, document, spec["invalid_inputs"])
    _assert_rejected(rejected, spec["expected_rejection_code"], next(iter(spec["invalid_inputs"])))
    assert plans.resolve_with_source.call_count == prior + 1

    corrected = _inspect(client, document, spec["valid_inputs"])
    accepted = _assert_accepted(corrected, document)
    assert plans.resolve_with_source.call_count == prior + 2
    assert _assert_accepted(_inspect(client, document, spec["valid_inputs"]), document) == accepted
    assert on_disk.read_bytes() == original_bytes, "Inspection mutated saved Plan bytes"
    assert not list(store.root.rglob("goon.lock")), (
        "Inspection unexpectedly authored execution lock"
    )
    runs.assert_not_called()
    league.assert_not_called()


def test_t6_save_edit_requires_revision_and_invalidates_prior_inspection(journey):
    client, store, _, runs, _, _ = journey
    document = _create_from_shipped(client, "python/quality")
    _assert_accepted(_inspect(client, document, {"typechecker": "ty"}), document)
    changed = document["content"] + "\n# second team-owned revision\n"
    saved = client.put(
        f"{API}/shared-plans/{document['plan_key']}",
        json={
            "content": changed,
            "expected_revision": document["sha256"],
        },
    )
    assert saved.status_code == 200, saved.text
    current = saved.json()
    assert current["sha256"] != document["sha256"]
    stale = _inspect(client, document, {"typechecker": "ty"})
    assert stale.status_code == 409 and stale.json()["code"] == "FLEET_PLAN_SOURCE_STALE"
    _assert_accepted(_inspect(client, current, {"typechecker": "ty"}), current)
    old_write = client.put(
        f"{API}/shared-plans/{document['plan_key']}",
        json={
            "content": document["content"],
            "expected_revision": document["sha256"],
        },
    )
    assert old_write.status_code == 409
    assert store.read(document["plan_key"]).sha256 == current["sha256"]
    runs.assert_not_called()


def test_t6_invalid_plan_edit_fails_before_commit(journey):
    client, store, plans, runs, _, _ = journey
    document = _create_from_shipped(client, "python/quality")
    before = store.path(document["plan_key"]).read_bytes()
    response = client.put(
        f"{API}/shared-plans/{document['plan_key']}",
        json={
            "content": "version: 1\nsteps:\n- run: dangerous-should-not-execute\n",
            "expected_revision": document["sha256"],
        },
    )
    assert response.status_code in (400, 422), response.text
    assert store.path(document["plan_key"]).read_bytes() == before
    _assert_accepted(_inspect(client, document, {"typechecker": "ty"}), document)
    runs.assert_not_called()
    assert not list(store.root.rglob("goon.lock"))


def test_t6_unknown_input_repair_does_not_leak_user_value(journey):
    client, _, _, runs, _, _ = journey
    document = _create_from_shipped(client, "python/quality")
    response = _inspect(client, document, {"typechecker": "ty", "unknown": SECRET})
    _assert_rejected(response, "UNKNOWN_INPUT", "unknown")
    assert SECRET not in json.dumps(response.json())
    _assert_accepted(_inspect(client, document, {"typechecker": "ty"}), document)
    runs.assert_not_called()


def test_t6_http_schema_error_redacts_secret(journey):
    client, _, plans, runs, _, _ = journey
    document = _create_from_shipped(client, "python/quality")
    response = client.post(
        f"{API}/shared-plan-input-inspections",
        json={
            "plan_key": document["plan_key"],
            "expected_revision": SECRET,
            "inputs": {"typechecker": [SECRET]},
        },
    )
    assert response.status_code == 422
    assert SECRET not in response.text
    assert response.json()["details"] == {}
    plans.resolve_with_source.assert_not_called()
    runs.assert_not_called()


def test_t6_same_source_input_pair_is_deterministic(journey):
    client, store, plans, runs, _, _ = journey
    doc = _create_from_shipped(client, "python/quality")
    original = store.path(doc["plan_key"]).read_bytes()
    inputs = {"typechecker": "ty"}
    results = [_assert_accepted(_inspect(client, doc, inputs), doc) for _ in range(3)]
    assert results[0] == results[1] == results[2]
    assert plans.resolve_with_source.call_count == 3
    assert store.path(doc["plan_key"]).read_bytes() == original
    runs.assert_not_called()


def test_t6_no_run_admission_or_lock_writes(journey):
    client, store, plans, runs, league, _ = journey
    doc = _create_from_shipped(client, "python/quality")
    for inputs in ({"typechecker": "ty"}, {"typechecker": "101"}, {"wrong": SECRET}):
        response = _inspect(client, doc, inputs)
        assert response.status_code == 200
        assert response.json()["execution"] == "not_started_by_inspection"
    assert plans.resolve_with_source.call_count == 3
    assert sorted(
        path.relative_to(store.root).as_posix() for path in store.root.rglob("*") if path.is_file()
    ) == ["python-quality/plan.yml"]
    runs.assert_not_called()
    league.assert_not_called()
