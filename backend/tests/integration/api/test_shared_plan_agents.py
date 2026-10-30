"""Shared Agent selections retain saved-source, reviewed execution and Team authority."""

from __future__ import annotations

import json
import subprocess
from contextlib import closing

import pytest

from oh_goon.api import dependencies
from oh_goon.api.app import create_app
from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_providers import AgentModel, AgentProvider, AgentProvidersConfig
from oh_goon.core.path_layouts import ContentStorePathLayout
from oh_goon.core.product_identity import repository_id
from tests.factories.agent_review import agent_review_case
from tests.integration.api.client import authenticated_api_client


@pytest.fixture
def shared_agent(tmp_path, monkeypatch):
    _, plan_path, repository, options = agent_review_case(tmp_path)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "journal.sqlite"))
    config = AgentProvidersConfig(
        default_model="review",
        providers={
            "test": AgentProvider(api="openai-chat-completions", base_url="http://127.0.0.1:1")
        },
        models={"review": AgentModel(provider="test", model="synthetic/reviewer")},
    )
    provider_path = tmp_path / "providers.json"
    provider_path.write_text(config.model_dump_json())
    monkeypatch.setenv("OH_GOON_PROVIDERS_FILE", str(provider_path))
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repository.parent)
        document = runtime.shared_plan.save("reviewed-campaign", plan_path.read_text())
        app = create_app(local_access_token="shared-agent-authority-test-token")
        app.dependency_overrides[dependencies.shared_plan_service] = lambda: runtime.shared_plan
        app.dependency_overrides[dependencies.fleet_run_control_plane] = lambda: runtime.runs
        with closing(authenticated_api_client(app)) as client:
            selected = dict(
                plan_key=document.plan_key,
                expected_revision=document.sha256,
                repository_ids=[repository_id(repository)],
                inputs=options["inputs"],
                agent=dict(
                    model_profile="review",
                    acceptance="explicit-reviewed-profile",
                    repository_root=str(options["repository_root"]),
                    skill_root=str(tmp_path),
                ),
            )
            response = client.post("/api/league/shared-plan-previews", json=selected)
            assert response.status_code == 200, response.text
            preview = response.json()
            request = {key: value for key, value in selected.items() if key != "expected_revision"}
            request.update(
                expected_plan_revision=document.sha256,
                expected_plan_id=preview["plan_id"],
                expected_execution_digest=preview["execution_digest"],
                repository_instance_ids={
                    row["repository"]["repository_id"]: row["repository_instance_id"]
                    for row in preview["teams"]
                },
                fleet_run_id="reviewed-shared-campaign",
                authorize_repair=True,
                repair=dict(
                    review_passes=2,
                    write_roots=["code.txt", "regression.txt"],
                    timeout_seconds=60,
                    step_timeout_seconds=10,
                ),
            )
            yield client, runtime, selected, request, tmp_path
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_agent_inspection_lock_and_preview_share_one_resolved_meaning(shared_agent):
    client, runtime, selected, request, _ = shared_agent
    inspection = client.post("/api/league/shared-plan-input-inspections", json=selected)
    assert inspection.status_code == 200 and inspection.json()["status"] == "accepted"
    lock = client.post("/api/league/shared-plan-lock-previews", json=selected)
    assert lock.status_code == 200, lock.text
    assert lock.json()["plan_id"] == inspection.json()["plan_id"] == request["expected_plan_id"]
    assert lock.json()["execution_digest"] == request["expected_execution_digest"]
    assert not runtime.runs.history().items
    assert not list(runtime.shared_plan.root.rglob("goon.lock"))


@pytest.mark.parametrize("role", ["coding", "review"])
def test_shared_native_preflight_uses_authored_effects_and_preserves_native_read_only_policy(
    shared_agent, role
):
    client, runtime, selected, _, root = shared_agent
    marker = root / "native-started"
    program = root / "public/bin/native-agent"
    program.parent.mkdir(parents=True)
    program.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 99\n")
    program.chmod(0o700)
    home = root / "native-home"
    home.mkdir()
    catalog = root / "providers.json"
    catalog.write_text(
        json.dumps(
            {
                "native_agents": {"codex": {"executable": str(program)}},
                "native_profiles": {
                    "login-fixture": {
                        "backend": "local",
                        "codex_login": {
                            "home": str(home),
                            "model": "native-model",
                            "reasoning_effort": "low",
                        },
                    }
                },
            }
        )
    )
    document = runtime.shared_plan.save(
        selected["plan_key"],
        f"goons:\n- agents/native-{role}\n",
        expected_revision=selected["expected_revision"],
    )
    selected["expected_revision"] = document.sha256
    selected["inputs"] = {"repo": "local:repo", "skill": "local:skills/repair"}
    selected["agent"].pop("model_profile")
    selected["agent"].update(implementation="codex", profile="login-fixture")
    response = client.post("/api/league/shared-plan-previews", json=selected)
    if role == "coding":
        assert response.status_code == 200, response.text
        assert response.json()["plan_id"]
    else:
        assert response.status_code == 400, response.text
    assert not marker.exists()
    assert not runtime.runs.history().items


@pytest.mark.parametrize(
    "case", ["expired-before-capture", "expired-during-capture", "cancelled-during-capture"]
)
def test_shared_repair_capture_uses_original_deadline_and_reports_no_unwritten_journal(
    shared_agent, monkeypatch, case
):
    import time

    from oh_goon.engine.errors import OperationCancelledError

    client, runtime, _, request, _ = shared_agent

    def unexpected(*args, **kwargs):
        pytest.fail(
            "Expired or cancelled initial capture must not publish source or campaign intent"
        )

    monkeypatch.setattr(runtime.plans, "_agent_target", unexpected)
    monkeypatch.setattr(runtime.runs, "begin_agent_campaign", unexpected)
    monkeypatch.setattr(runtime.runs, "start_prepared_plan", unexpected)
    captures = []
    if case == "expired-before-capture":
        request["repair"]["timeout_seconds"] = 1e-9
        monkeypatch.setattr(runtime.plans, "prepare_agent_plan", unexpected)
    else:
        capture = runtime.plans._resolver.resolve_frozen_agents

        def delayed_or_cancelled(*args, **kwargs):
            captures.append(True)
            cancelled = kwargs["cancel_requested"]
            assert callable(cancelled), "Initial capture must receive the original campaign budget"
            if case == "cancelled-during-capture":
                raise OperationCancelledError("capture interrupted")
            time.sleep(0.6)
            assert cancelled(), "Capture must consume the campaign deadline rather than precede it"
            return capture(*args, **kwargs)

        if case == "expired-during-capture":
            request["repair"]["timeout_seconds"] = 0.5
        monkeypatch.setattr(runtime.plans._resolver, "resolve_frozen_agents", delayed_or_cancelled)
    response = client.post("/api/league/shared-plan-repairs", json=request)
    assert response.status_code == 200, response.text
    assert len(captures) == (0 if case == "expired-before-capture" else 1)
    result = response.json()
    assert result["result"] == "incomplete"
    assert result["journal_revision"] is None and result["intent"] is None
    assert result["controller_result"]["availability"] == "unavailable"
    invocation = result["invocation_result"]
    assert invocation["stop_reason"] == (
        "cancelled" if case == "cancelled-during-capture" else "timed_out"
    )
    assert invocation["budget"]["timeout_seconds"] == request["repair"]["timeout_seconds"]
    assert invocation["authority"] == "invocation-observation;not-a-committed-controller-decision"
    assert result["complete"] is False and result["runs"] == []
    assert not runtime.runs.history().items
    assert client.get("/api/fleet/runs/campaigns/reviewed-shared-campaign").status_code == 404


@pytest.mark.parametrize(
    "change,status",
    [
        ("revision", 409),
        ("instance", 409),
        ("digest", 400),
        ("skill", 400),
        ("provider", 400),
        ("authorization", 422),
        ("authorization_number", 422),
        ("missing_model", 400),
        ("unknown_selection", 422),
        ("duplicate_acceptance", 400),
        ("budget", 422),
    ],
)
def test_changed_shared_agent_authority_refuses_before_delivery_or_admission(
    shared_agent, monkeypatch, change, status
):
    client, runtime, _, request, root = shared_agent

    def unexpected(*args, **kwargs):
        pytest.fail("Refused incoming authority must not store a delivery or admit work")

    monkeypatch.setattr(runtime.plans, "_agent_target", unexpected)
    monkeypatch.setattr(runtime.runs, "start_prepared_plan", unexpected)
    if change == "revision":
        request["expected_plan_revision"] = "0" * 64
    elif change == "instance":
        request["repository_instance_ids"] = {
            next(iter(request["repository_instance_ids"])): "stale-instance"
        }
    elif change == "digest":
        request["expected_execution_digest"] = "sha256:" + "0" * 64
    elif change == "skill":
        (root / "skills/repair/SKILL.md").write_text("# Changed Skill\n")
    elif change == "provider":
        path = root / "providers.json"
        payload = json.loads(path.read_text())
        payload["models"]["review"]["model"] = "changed-model"
        path.write_text(json.dumps(payload))
    elif change == "authorization":
        request["authorize_repair"] = False
    elif change == "authorization_number":
        request["authorize_repair"] = 1
    elif change == "missing_model":
        request["agent"].pop("model_profile")
    elif change == "unknown_selection":
        request["unrecognized_selection"] = True
    elif change == "duplicate_acceptance":
        request["inputs"]["acceptance"] = "explicit-reviewed-profile"
    else:
        request["repair"]["review_passes"] = 0
    if change in (
        "revision",
        "instance",
        "authorization",
        "authorization_number",
        "unknown_selection",
        "budget",
    ):
        monkeypatch.setattr(runtime.plans, "prepare_agent_plan", unexpected)
    response = client.post("/api/league/shared-plan-repairs", json=request)
    assert response.status_code == status, response.text
    assert not runtime.runs.history().items
    assert client.get("/api/fleet/runs/campaigns/reviewed-shared-campaign").status_code == 404


def test_phase_classified_plan_cannot_bypass_explicit_repair_front_door(shared_agent, monkeypatch):
    client, runtime, _, request, _ = shared_agent

    def unexpected(*args, **kwargs):
        pytest.fail("Unbudgeted phase Plan must not store or admit work")

    monkeypatch.setattr(runtime.plans, "_agent_target", unexpected)
    for name in ("repair", "authorize_repair"):
        request.pop(name)
    response = client.post("/api/league/shared-plan-runs", json=request)
    assert response.status_code == 400, response.text
    assert not runtime.runs.history().items


def test_agent_selection_refuses_multiple_teams_and_relative_roots(shared_agent):
    client, _, selected, _, _ = shared_agent
    selected["repository_ids"].append("another-team")
    response = client.post("/api/league/shared-plan-previews", json=selected)
    assert response.status_code == 400
    selected["repository_ids"].pop()
    selected["agent"]["skill_root"] = "."
    response = client.post("/api/league/shared-plan-previews", json=selected)
    assert response.status_code == 400
    assert "absolute directories" in json.dumps(response.json())


@pytest.fixture
def shared_single_agent(shared_agent, monkeypatch):
    client, runtime, selected, _, root = shared_agent
    document = runtime.shared_plan.save(
        selected["plan_key"],
        "goons:\n- agents/review\n",
        expected_revision=selected["expected_revision"],
    )
    selected = {
        **selected,
        "expected_revision": document.sha256,
        "inputs": {"repo": "local:repo", "skill": "local:skills/repair"},
    }
    monkeypatch.setattr(runtime.runs, "_submit", lambda *_args: None)

    def reviewed_request(selection):
        response = client.post("/api/league/shared-plan-previews", json=selection)
        assert response.status_code == 200, response.text
        preview = response.json()
        request = {key: value for key, value in selection.items() if key != "expected_revision"}
        request.update(
            expected_plan_revision=selection["expected_revision"],
            expected_plan_id=preview["plan_id"],
            expected_execution_digest=preview["execution_digest"],
            repository_instance_ids={
                row["repository"]["repository_id"]: row["repository_instance_id"]
                for row in preview["teams"]
            },
            fleet_run_id="shared-single-agent",
        )
        return request

    store = runtime.plans._content_store
    assert store is not None
    references = ContentStorePathLayout.from_root(store.root).references

    def retained_references():
        return {path: path.read_bytes() for path in references.rglob("*.json")}

    return client, runtime, selected, reviewed_request, retained_references, root


@pytest.mark.parametrize("lost_ack", [False, True])
def test_shared_single_agent_retry_reuses_committed_source_custody(
    shared_single_agent,
    monkeypatch,
    lost_ack,
):
    client, runtime, selected, reviewed_request, retained_references, _ = shared_single_agent
    request = reviewed_request(selected)
    if lost_ack:
        original = runtime.runs._lifecycle.admit

        def admitted_without_ack(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("lost admission acknowledgement")

        monkeypatch.setattr(runtime.runs._lifecycle, "admit", admitted_without_ack)
        with pytest.raises(OSError, match="lost admission acknowledgement"):
            client.post("/api/league/shared-plan-runs", json=request)
        monkeypatch.setattr(runtime.runs._lifecycle, "admit", original)
    else:
        response = client.post("/api/league/shared-plan-runs", json=request)
        assert response.status_code == 202, response.text
    original = runtime.runs.get(request["fleet_run_id"])
    before = retained_references()
    assert len(before) == 1
    repeated = client.post("/api/league/shared-plan-runs", json=request)
    assert repeated.status_code == 202, repeated.text
    assert repeated.json()["target"] == original.run.target.model_dump(mode="json")
    assert retained_references() == before
    assert len(runtime.runs.history().items) == 1
    target = original.run.target
    reference = target.agent_deliveries[0]
    store = runtime.plans._content_store
    assert store is not None
    stored = store.get(reference.reference_id, owner_repository_id=request["repository_ids"][0])
    assert stored.path.is_file()


def test_shared_single_agent_refused_admission_releases_new_delivery(
    shared_single_agent, monkeypatch
):
    client, runtime, selected, reviewed_request, retained_references, _ = shared_single_agent
    request = reviewed_request(selected)
    before = retained_references()

    def refused(*_args, **_kwargs):
        raise FleetPlanInvalid("native planning refused")

    monkeypatch.setattr(runtime.runs._admission, "plan", refused)
    response = client.post("/api/league/shared-plan-runs", json=request)
    assert response.status_code == 400, response.text
    assert retained_references() == before
    assert not runtime.runs.history().items


@pytest.mark.parametrize("change", ["inputs", "source", "team"])
def test_shared_single_agent_bound_run_does_not_reuse_changed_authority(
    shared_single_agent,
    change,
):
    client, runtime, selected, reviewed_request, retained_references, root = shared_single_agent
    request = reviewed_request(selected)
    response = client.post("/api/league/shared-plan-runs", json=request)
    assert response.status_code == 202, response.text
    original = response.json()
    before = retained_references()
    if change == "inputs":
        selected["inputs"]["goal"] = "a different reviewed task"
    elif change == "source":
        document = runtime.shared_plan.save(
            selected["plan_key"],
            "# different authored source\ngoons:\n- agents/review\n",
            expected_revision=selected["expected_revision"],
        )
        selected["expected_revision"] = document.sha256
    else:
        other = root / "repos/other"
        other.mkdir()
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        (other / "code.txt").write_text("different Team\n")
        selected["repository_ids"] = [repository_id(other)]
        selected["inputs"]["repo"] = "local:other"
    changed = reviewed_request(selected)
    refused = client.post("/api/league/shared-plan-runs", json=changed)
    assert refused.status_code == 409, refused.text
    assert retained_references() == before
    assert runtime.runs.get(request["fleet_run_id"]).run.model_dump(mode="json") == original


def test_shared_single_agent_unavailable_admission_readback_retains_source(
    shared_single_agent,
    monkeypatch,
):
    client, runtime, selected, reviewed_request, retained_references, _ = shared_single_agent
    request = reviewed_request(selected)
    journal = runtime.runs._lifecycle._journal
    original_read_view = journal.read_view
    unavailable = False

    def read_view():
        if unavailable:
            raise OSError("admission readback unavailable")
        return original_read_view()

    def refused(*_args, **_kwargs):
        nonlocal unavailable
        unavailable = True
        raise FleetPlanInvalid("native planning refused")

    monkeypatch.setattr(journal, "read_view", read_view)
    monkeypatch.setattr(runtime.runs._admission, "plan", refused)
    response = client.post("/api/league/shared-plan-runs", json=request)
    unavailable = False
    assert response.status_code == 400, response.text
    assert len(retained_references()) == 1
    assert not runtime.runs.history().items


def test_shared_single_agent_cleanup_failure_is_reported_without_changing_result(
    shared_single_agent,
    monkeypatch,
    capsys,
):
    client, runtime, selected, reviewed_request, retained_references, _ = shared_single_agent
    request = reviewed_request(selected)
    response = client.post("/api/league/shared-plan-runs", json=request)
    assert response.status_code == 202, response.text
    before = retained_references()
    store = runtime.plans._content_store
    assert store is not None

    def failed(*_args, **_kwargs):
        raise PermissionError("source reference cleanup denied")

    monkeypatch.setattr(store, "release_reference", failed)
    monkeypatch.setattr(runtime.runs, "_diagnostics", None)
    repeated = client.post("/api/league/shared-plan-runs", json=request)
    assert repeated.status_code == 202, repeated.text
    assert repeated.json() == response.json()
    assert len(retained_references()) == len(before) + 1
    assert "Unadmitted Agent source cleanup failed" in capsys.readouterr().err


def test_shared_agent_uses_selected_skill_root_lock_and_recovers_exact_git_pin(
    shared_single_agent,
    monkeypatch,
    capsys,
):
    import shutil

    import yaml

    from oh_goon.cli import main
    from oh_goon.services.execution_lock_persistence import load_execution_lock

    client, runtime, selected, reviewed_request, retained_references, root = shared_single_agent
    upstream = root / "skill-upstream"
    skill = upstream / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Pinned native fixture\nInspect the captured source.\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(upstream)], check=True)

    def commit():
        subprocess.run(["git", "-C", str(upstream), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(upstream),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "Skill",
            ],
            check=True,
        )
        return subprocess.check_output(
            ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True
        ).strip()

    original = commit()
    registry = {"sources": {"team": {"source": str(upstream), "ref": "main", "skills": ["review"]}}}
    registry_path = root / ".goonskills.yml"
    registry_path.write_text(yaml.safe_dump(registry))
    selected["inputs"]["skill"] = "team/review"
    monkeypatch.setenv("XDG_CACHE_HOME", str(root / "cache"))
    missing = client.post("/api/league/shared-plan-previews", json=selected)
    assert missing.status_code == 400, missing.text
    assert not retained_references()
    (root / "plan.yml").write_text("goons:\n- agents/review\n")
    configuration = root / "run.yml"
    configuration.write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "source": {"kind": "file-plan", "path": "plan.yml"},
                "target": {"repository": str(root / "repos/repo")},
                "inputs": selected["inputs"],
                "agent": {**selected["agent"], "skill_root": "."},
            }
        )
    )
    assert main(["workflow", "prepare", str(configuration)]) == 0
    capsys.readouterr()
    lock_path = root / "goon.lock"
    lock_bytes = lock_path.read_bytes()
    assert load_execution_lock(lock_path).skill_sources[0].commit == original

    def inspected_lock():
        response = client.post("/api/league/shared-plan-lock-previews", json=selected)
        assert response.status_code == 200, response.text
        reported = response.json()
        assert yaml.safe_load(reported["content"])["skill_sources"][0]["commit"] == original
        return reported

    before = inspected_lock()
    (skill / "SKILL.md").write_text("# A moved branch must not change this selection\n")
    assert commit() != original
    shutil.rmtree(root / "cache/oh-goon/skill-sources")
    recovered = inspected_lock()
    assert recovered["execution_digest"] == before["execution_digest"]
    assert lock_path.read_bytes() == lock_bytes
    assert not runtime.runs.history().items
    request = reviewed_request(selected)
    admitted = client.post("/api/league/shared-plan-runs", json=request)
    assert admitted.status_code == 202, admitted.text
    assert admitted.json()["target"]["plan"]["skill_sources"][0]["commit"] == original
    retained = retained_references()
    assert len(retained) == 1

    registry["sources"]["team"]["ref"] = "a-different-ref"
    registry_path.write_text(yaml.safe_dump(registry))

    def unexpected(*_args, **_kwargs):
        pytest.fail("Changed Skill registry must refuse before Git acquisition")

    monkeypatch.setattr("oh_goon.services.agent_skill_registry.prepare_git_source", unexpected)
    changed = client.post("/api/league/shared-plan-previews", json=selected)
    assert changed.status_code == 400, changed.text
    assert retained_references() == retained
