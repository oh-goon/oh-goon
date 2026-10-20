import base64
import json
from types import SimpleNamespace

import pytest

from oh_goon.core import local_access_protocol
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome
from oh_goon.integrations.agent_review_execution import (
    execute_provider_agent,
    provider_agent_request,
)
from oh_goon.integrations.fleet_plan_step_publication import publish_direct_attempt_outputs
from tests.factories.agent_review import agent_review_case


@pytest.mark.parametrize("candidate", ["admitted-candidate", "other-candidate"])
def test_proposal_publication_binds_to_the_admitted_candidate(tmp_path, candidate):
    resolver, path, repo, options = agent_review_case(tmp_path)
    plan, _, _ = resolver.resolve_frozen_agents(path, **options)
    step = next(s for s in plan.steps if getattr(s, "harness", None) == "provider-proposal")
    step = step.model_copy(
        update={"review_context": json.dumps({"candidate_identity": "admitted-candidate"})}
    )
    proposal = json.dumps(
        dict(
            schema="agent-patch-batch-v1",
            candidate_identity=candidate,
            summary="Scoped repair",
            operations=[
                dict(action="create", path="repo/new.txt", source_sha256=None, replacement="new")
            ],
        )
    )
    published = publish_direct_attempt_outputs(
        step, StepOutcome(argv=("provider",), returncode=0, stdout=proposal), repo
    )
    if candidate == "admitted-candidate":
        assert published.outcome.returncode == 0
        assert json.loads(published.outputs["proposal"])["candidate_identity"] == candidate
    else:
        assert published.outcome.returncode != 0
        assert published.outputs == {}
    assert not (repo / "new.txt").exists()


def test_provider_receives_readable_skill_text_and_the_complete_resource_closure(tmp_path):
    resolver, path, _, options = agent_review_case(tmp_path)
    resource = tmp_path / "skills/single-source-of-truth-review/data.bin"
    resource.write_bytes(b"\xff\x00\x01")
    plan, deliveries, _ = resolver.resolve_frozen_agents(path, **options)
    step = plan.steps[0]
    payload = provider_agent_request(
        step,
        deliveries[step.step_id],
        repository_evidence="Producer-qualified repository orientation",
    )
    source = json.loads(payload["messages"][1]["content"])
    assert next(iter(source)) == "repository_evidence"
    assert source["repository_evidence"] == "Producer-qualified repository orientation"
    members = {item["path"]: item for item in source["skill"]}
    assert members["skill/SKILL.md"]["encoding"] == "utf-8"
    assert "Inspect code.txt" in members["skill/SKILL.md"]["content"]
    assert base64.b64decode(members["skill/data.bin"]["content"]) == resource.read_bytes()
    assert set(members) == {
        "skill/" + member.path for member in deliveries[step.step_id].source.skill.members
    }


def test_oversized_complete_source_fails_before_provider_process_or_scratch_payload(tmp_path):
    resolver, path, _, options = agent_review_case(tmp_path)
    plan, deliveries, _ = resolver.resolve_frozen_agents(path, **options)
    step = plan.steps[0]
    connection = step.connection.model_copy(
        update={"provider": step.connection.provider.model_copy(update={"max_request_bytes": 1024})}
    )
    step = step.model_copy(update={"connection": connection})
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    process = SimpleNamespace(
        host_environment={},
        paths=SimpleNamespace(tmp=scratch),
        command_runner=lambda *a, **kw: pytest.fail("oversize source launched a model"),
    )
    with pytest.raises(FleetPlanInvalid, match="complete Agent source"):
        execute_provider_agent(
            step,
            deliveries[step.step_id],
            process,
            SimpleNamespace(),
            timeout=1,
            repository_evidence="Producer-qualified repository orientation",
        )
    assert not list(scratch.iterdir())


@pytest.mark.parametrize("field", ["api_key_env", "headers_from_env"])
def test_provider_credentials_cannot_export_process_local_control_plane_authority(tmp_path, field):
    resolver, path, _, options = agent_review_case(tmp_path)
    plan, deliveries, _ = resolver.resolve_frozen_agents(path, **options)
    step = plan.steps[0]
    variable = local_access_protocol.LOCAL_ACCESS_ENV
    value = variable if field == "api_key_env" else {"x-auth": variable}
    provider = step.connection.provider.model_copy(update={field: value})
    step = step.model_copy(
        update={"connection": step.connection.model_copy(update={"provider": provider})}
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    process = SimpleNamespace(
        host_environment={variable: "private-control-authority"},
        paths=SimpleNamespace(tmp=scratch),
        command_runner=lambda *a, **kw: pytest.fail("control-plane authority reached a child"),
    )
    with pytest.raises(FleetPlanInvalid, match="control-plane"):
        execute_provider_agent(
            step,
            deliveries[step.step_id],
            process,
            SimpleNamespace(),
            timeout=1,
            repository_evidence="Producer-qualified repository orientation",
        )
    assert not list(scratch.iterdir())
