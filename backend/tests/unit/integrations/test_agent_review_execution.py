import base64
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core import local_access_protocol
from oh_goon.core.agent_review_plan import agent_phase_role
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_providers import (
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.agent_review import AgentReviewRecord, review_scope
from oh_goon.core.models.fleet_observation import AgentReviewObservation, NativeAgentObservation
from oh_goon.engine.command import CommandResult
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome
from oh_goon.integrations.agent_review_execution import (
    accept_agent_phase_output,
    agent_phase_output_contract,
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


def native_phase_case(tmp_path):
    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="isolated",
        definition=NativeAgentDefinition(executable="/native/pi"),
        profile=NativeAgentProfile(backend="bubblewrap"),
        executable_sha256="a" * 64,
    )
    resolver, path, repo, options = agent_review_case(tmp_path, native_agent=selected)
    plan, handoffs, _ = resolver.resolve_frozen_agents(path, **options)
    return plan, handoffs, repo


def review_record(step, handoff):
    member = next(
        member for member in handoff.source.repository.members if member.path == "code.txt"
    )
    return AgentReviewRecord(
        reviewer=step.step_id,
        candidate_identity=handoff.source.repository.content_identity,
        scope_identity=review_scope(handoff),
        skill_identity=handoff.source.skill.content_identity,
        verdict="FINDING",
        summary="Reproduced defect",
        reviewed_paths=tuple("repo/" + member.path for member in handoff.source.repository.members),
        findings=[
            dict(
                finding_id="f1",
                explanation="broken source",
                evidence=[dict(path="repo/code.txt", sha256=member.sha256, line=1)],
            )
        ],
    )


@pytest.mark.parametrize("native", [False, True])
def test_shared_phase_acceptor_preserves_existing_review_contract(tmp_path, native):
    if native:
        plan, handoffs, _ = native_phase_case(tmp_path)
    else:
        resolver, path, _, options = agent_review_case(tmp_path)
        plan, handoffs, _ = resolver.resolve_frozen_agents(path, **options)
    step = plan.steps[0]
    handoff = handoffs[step.step_id]
    record = review_record(step, handoff)
    result = accept_agent_phase_output(
        step,
        handoff,
        StepOutcome(argv=("selected",), returncode=0, stdout=record.model_dump_json()),
    )
    assert result.returncode == 0
    assert isinstance(result.observations[0], AgentReviewObservation)
    assert result.observations[0].record == record
    assert 'schema="oh-goon.agent-review/v1"' in agent_phase_output_contract(step)


@pytest.mark.parametrize(
    "field",
    ["candidate_identity", "scope_identity", "skill_identity", "reviewer", "reviewed_paths"],
)
def test_native_review_rejects_wrong_source_skill_occurrence_and_omission(tmp_path, field):
    plan, handoffs, _ = native_phase_case(tmp_path)
    step = plan.steps[0]
    handoff = handoffs[step.step_id]
    record = review_record(step, handoff).model_copy(
        update={field: () if field == "reviewed_paths" else "other"}
    )
    with pytest.raises(FleetPlanInvalid):
        accept_agent_phase_output(
            step, handoff, StepOutcome(argv=("pi",), returncode=0, stdout=record.model_dump_json())
        )


def test_native_review_appends_typed_claim_without_losing_native_observations(tmp_path):
    plan, handoffs, _ = native_phase_case(tmp_path)
    step = plan.steps[0]
    handoff = handoffs[step.step_id]
    native = NativeAgentObservation(
        agent="pi",
        implementation_identity="pi:sha256:" + "a" * 64,
        profile="isolated",
        input_identity="input",
        occurrence=step.step_id,
        source_identity=handoff.source.repository.content_identity,
        role="review",
        native_event_count=1,
        settled=True,
        failed=False,
    )
    outcome = StepOutcome(
        argv=("pi",),
        returncode=0,
        stdout=review_record(step, handoff).model_dump_json(),
        observations=(native,),
    )
    result = accept_agent_phase_output(step, handoff, outcome)
    assert result.observations[0] is native
    assert len(result.observations) == 2
    assert isinstance(result.observations[1], AgentReviewObservation)


def test_failed_native_process_cannot_publish_a_false_clean_phase_result(tmp_path):
    plan, handoffs, _ = native_phase_case(tmp_path)
    step = plan.steps[0]
    outcome = StepOutcome(
        argv=("pi",),
        returncode=17,
        stdout='{"verdict":"CLEAN"}',
        stderr="native failure",
        physical_execution_id="original",
    )
    assert accept_agent_phase_output(step, handoffs[step.step_id], outcome) is outcome


def test_provider_adapter_still_rejects_untyped_clean_after_zero_exit(tmp_path):
    resolver, path, _, options = agent_review_case(tmp_path)
    plan, handoffs, _ = resolver.resolve_frozen_agents(path, **options)
    step = plan.steps[0]
    scratch = tmp_path / "provider-scratch"
    scratch.mkdir()
    process = SimpleNamespace(
        host_environment={},
        environment={},
        paths=SimpleNamespace(tmp=scratch),
        command_runner=lambda *args, **kwargs: CommandResult(
            0, '{"verdict":"CLEAN"}', "", argv=("provider",)
        ),
    )
    runtime = SimpleNamespace(cancel_requested=None, runtime_supervision=None)
    with pytest.raises(FleetPlanInvalid, match="record schema"):
        execute_provider_agent(
            step,
            handoffs[step.step_id],
            process,
            runtime,
            timeout=1,
            repository_evidence="Producer-qualified evidence",
        )
    assert not list(scratch.iterdir())


@pytest.mark.parametrize("module", ("oh_goon", "json", "sitecustomize"))
def test_provider_worker_ignores_repository_python_imports_and_reports_native_failure(
    tmp_path, module
):
    resolver, path, _, options = agent_review_case(tmp_path)
    plan, handoffs, _ = resolver.resolve_frozen_agents(path, **options)
    step = plan.steps[0]
    scratch = tmp_path / "provider-scratch"
    scratch.mkdir()
    marker = tmp_path / "hostile-import-executed"
    target = scratch / (module + ".py")
    if module == "oh_goon":
        package = scratch / module
        package.mkdir()
        target = package / "__init__.py"
    target.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('repository code ran')\n"
        "raise RuntimeError('hostile repository import')\n"
    )

    def runner(*args, **kwargs):
        # A malformed native request must remain a real worker failure, with no
        # provider connection and no import selected from the request directory.
        Path(args[-1]).write_text("{}")
        completed = subprocess.run(
            args,
            cwd=kwargs["cwd"],
            env={**os.environ, **kwargs["env"], "PYTHONPATH": str(scratch)},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return CommandResult(
            argv=args,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    process = SimpleNamespace(
        host_environment={},
        environment={},
        paths=SimpleNamespace(tmp=scratch),
        command_runner=runner,
    )
    outcome = execute_provider_agent(
        step,
        handoffs[step.step_id],
        process,
        SimpleNamespace(cancel_requested=None, runtime_supervision=None),
        timeout=1,
        repository_evidence="Producer-qualified evidence",
    )
    assert outcome.returncode != 0
    assert "KeyError: 'connection'" in outcome.stderr
    assert not marker.exists()
    assert not (scratch / "agent-provider-request.json").exists()


@pytest.mark.parametrize("attack", [None, "bad_queue", "omitted_finding"])
def test_native_adjudication_uses_original_exact_finding_partition(tmp_path, attack):
    plan, handoffs, _ = native_phase_case(tmp_path)
    review = review_record(plan.steps[0], handoffs[plan.steps[0].step_id])
    step = next(step for step in plan.steps if agent_phase_role(step) == "adjudication")
    step = step.model_copy(
        update={"review_context": json.dumps({"reviews": [review.model_dump(mode="json")]})}
    )
    record = dict(
        schema="oh-goon.agent-adjudication/v1",
        candidate_identity=review.candidate_identity,
        scope_identity=review.scope_identity,
        dispositions=[
            dict(
                reviewer=review.reviewer, finding_id="f1", decision="repair", rationale="one defect"
            )
        ],
        queue=[
            dict(
                queue_id="q1",
                findings=[dict(reviewer=review.reviewer, finding_id="f1")],
                rationale="one causal root",
                regression_expectation="reproduce defect",
            )
        ],
    )
    if attack == "bad_queue":
        record["queue"][0]["findings"] = []
    elif attack == "omitted_finding":
        record["dispositions"] = []
    outcome = StepOutcome(argv=("pi",), returncode=0, stdout=json.dumps(record))
    if attack:
        with pytest.raises(FleetPlanInvalid):
            accept_agent_phase_output(step, handoffs[step.step_id], outcome)
    else:
        result = accept_agent_phase_output(step, handoffs[step.step_id], outcome)
        assert isinstance(result.observations[0], AgentReviewObservation)
        assert result.observations[0].record.queue[0].queue_id == "q1"


@pytest.mark.parametrize(
    "attack", [None, "wrong_candidate", "wrong_preimage", "bad_queue", "omitted_regression"]
)
def test_native_proposal_retains_preimage_and_regression_queue_checks(tmp_path, attack):
    plan, handoffs, repo = native_phase_case(tmp_path)
    step = next(step for step in plan.steps if agent_phase_role(step) == "proposal")
    handoff = handoffs[step.step_id]
    member = next(
        member for member in handoff.source.repository.members if member.path == "code.txt"
    )
    step = step.model_copy(
        update={
            "review_context": json.dumps(
                {"adjudication": {"queue": [{"queue_id": "q1"}, {"queue_id": "q2"}]}}
            )
        }
    )
    record = dict(
        schema="agent-patch-batch-v1",
        candidate_identity=handoff.source.repository.content_identity,
        summary="repair the captured defect",
        operations=[
            dict(
                action="update",
                path="repo/code.txt",
                source_sha256=member.sha256,
                replacement="repaired\n",
            ),
            dict(
                action="create",
                path="repo/regression.txt",
                source_sha256=None,
                replacement="exit 0\n",
            ),
        ],
        regressions=[
            dict(queue_id=queue, path="repo/regression.txt", expectation="reproduce defect")
            for queue in ("q1", "q2")
        ],
    )
    if attack == "wrong_candidate":
        record["candidate_identity"] = "other"
    elif attack == "wrong_preimage":
        record["operations"][0]["source_sha256"] = "f" * 64
    elif attack == "bad_queue":
        record["regressions"][0]["queue_id"] = "other"
    elif attack == "omitted_regression":
        record["regressions"].pop()
    outcome = StepOutcome(argv=("pi",), returncode=0, stdout=json.dumps(record))
    if attack:
        with pytest.raises(FleetPlanInvalid):
            accept_agent_phase_output(step, handoff, outcome)
    else:
        result = accept_agent_phase_output(step, handoff, outcome)
        assert json.loads(result.stdout) == record
    assert (repo / "code.txt").read_text() == "broken\n"
    assert not (repo / "regression.txt").exists()


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
