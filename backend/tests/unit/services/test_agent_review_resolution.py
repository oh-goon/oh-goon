from dataclasses import replace

import pytest

from oh_goon.core.agent_review_plan import agent_review_phases
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.agent_review import (
    AgentAdjudicationRecord,
    AgentReviewRecord,
    accept_review,
    review_scope,
)
from oh_goon.services.fleet_plan_repair import AgentReviewRepairCampaign
from tests.factories.agent_review import agent_review_case


def test_shipped_loop_freezes_occurrences_and_shares_one_candidate(tmp_path):
    resolver, path, _, options = agent_review_case(tmp_path)
    plan, deliveries, _ = resolver.resolve_frozen_agents(path, **options)
    assert len(plan.steps) == 9
    assert len(deliveries) == 6
    assert len(agent_review_phases(plan)["review"]) == 4
    assert len({id(h.source.repository) for h in deliveries.values()}) == 1
    assert {h.requested_effects.recipients for h in deliveries.values()} == {("configured_model",)}
    before = build_execution_lock(plan)
    edges = tuple(edge.model_copy(update={"phase": None}) for edge in plan.imports)
    assert build_execution_lock(plan.model_copy(update={"imports": edges})).digest != before.digest
    campaign = AgentReviewRepairCampaign(
        plan,
        deliveries,
        resolver=resolver,
        plan_path=path,
        capture_options=options,
        passes=5,
        write_roots=("code.txt",),
    )
    assert len(campaign.plan.steps) == 4
    assert all(s.harness == "provider-review" for s in campaign.plan.steps)


def test_bound_skills_and_public_inputs_follow_native_projection(tmp_path):
    resolver, path, _, options = agent_review_case(tmp_path)
    names = {item.name for item in resolver.input_definitions(path)}
    assert names == {
        "repo",
        "goal",
        "acceptance",
        "adjudication_skill",
        "repair_skill",
        "regression_command",
        "verification_command",
    }
    connection = options.pop("connection")
    selections = []

    def select_connection():
        selections.append(connection)
        return connection

    plan, deliveries, _ = resolver.resolve_frozen_agents(
        path, connection=select_connection, **options
    )
    assert selections == [connection]
    assert len({h.source.authored_skill_reference for h in deliveries.values()}) == 6
    assert {s.connection.profile for s in plan.steps if s.kind == "agent"} == {"review"}


def test_native_apply_argument_bounds_are_checked_before_the_first_phase(tmp_path):
    resolver, path, _, options = agent_review_case(tmp_path)
    plan, deliveries, _ = resolver.resolve_frozen_agents(path, **options)
    with pytest.raises(FleetPlanInvalid, match="command argument contract"):
        AgentReviewRepairCampaign(
            plan,
            deliveries,
            resolver=resolver,
            plan_path=path,
            capture_options=options,
            passes=5,
            write_roots=tuple(f"src/root-{i}" for i in range(32)),
        )


@pytest.mark.parametrize("attack", ["missing", "overlap", "order", "wrong_role"])
def test_invalid_phase_membership_is_refused_before_execution(tmp_path, attack):
    resolver, path, _, options = agent_review_case(tmp_path)
    plan, _, _ = resolver.resolve_frozen_agents(path, **options)
    edges = list(plan.imports)
    if attack == "missing":
        edges[-1] = edges[-1].model_copy(update={"phase": None})
    elif attack == "overlap":
        edges[0] = edges[0].model_copy(update={"phase": "review"})
    elif attack == "order":
        edges[-1] = edges[-1].model_copy(update={"phase": "regression"})
        edges[-2] = edges[-2].model_copy(update={"phase": "verification"})
    else:
        edges[-1] = edges[-1].model_copy(update={"phase": "review"})
    with pytest.raises(FleetPlanInvalid):
        agent_review_phases(plan.model_copy(update={"imports": tuple(edges)}))


def test_review_record_requires_source_bound_evidence_and_exact_occurrence(tmp_path):
    resolver, path, _, options = agent_review_case(tmp_path)
    _, deliveries, _ = resolver.resolve_frozen_agents(path, **options)
    occurrence, handoff = next(iter(deliveries.items()))
    member = next(m for m in handoff.source.repository.members if m.path == "code.txt")
    record = AgentReviewRecord(
        reviewer=occurrence,
        candidate_identity=handoff.source.repository.content_identity,
        reviewed_paths=["repo/" + member.path for member in handoff.source.repository.members],
        scope_identity=review_scope(handoff),
        skill_identity=handoff.source.skill.content_identity,
        verdict="FINDING",
        summary="broken source",
        findings=[
            dict(
                finding_id="f1",
                explanation="Repair the broken source",
                evidence=[dict(path="repo/code.txt", sha256=member.sha256, line=1)],
            )
        ],
    )
    assert accept_review(record.model_dump_json(), handoff, occurrence) == record
    with pytest.raises(FleetPlanInvalid):
        record.verify(handoff, "another-occurrence")
    with pytest.raises(FleetPlanInvalid):
        record.verify(
            replace(handoff, source=replace(handoff.source, repository_reference="local:other")),
            occurrence,
        )
    for attack in (
        dict(candidate_identity="stale"),
        dict(scope_identity="smaller"),
        dict(skill_identity="other"),
    ):
        with pytest.raises(FleetPlanInvalid):
            record.model_copy(update=attack).verify(handoff, occurrence)
    with pytest.raises(FleetPlanInvalid):
        accept_review(record.model_dump_json()[:-1] + ',"verdict":"CLEAN"}', handoff, occurrence)
    adjudication = AgentAdjudicationRecord(
        candidate_identity=record.candidate_identity,
        scope_identity=record.scope_identity,
        dispositions=[
            dict(
                reviewer=occurrence,
                finding_id="f1",
                decision="repair",
                rationale="fix within scope",
            )
        ],
        queue=[
            dict(
                queue_id="q1",
                findings=[dict(reviewer=occurrence, finding_id="f1")],
                rationale="one root",
                regression_expectation="reproduces defect",
            )
        ],
    )
    adjudication.verify((record,))
    with pytest.raises(FleetPlanInvalid):
        adjudication.model_copy(update={"dispositions": ()}).verify((record,))
    # Distinct findings survive identical titles and retain separate queue entries.
    second = record.findings[0].model_copy(update={"finding_id": "f2"})
    multiple = record.model_copy(update={"findings": (*record.findings, second)})
    disposition = adjudication.dispositions[0].model_copy(update={"finding_id": "f2"})
    group = adjudication.queue[0].model_copy(
        update={
            "queue_id": "q2",
            "findings": (
                adjudication.queue[0].findings[0].model_copy(update={"finding_id": "f2"}),
            ),
        }
    )
    queued = adjudication.model_copy(
        update={
            "dispositions": (*adjudication.dispositions, disposition),
            "queue": (*adjudication.queue, group),
        }
    )
    queued.verify((multiple,))
    with pytest.raises(FleetPlanInvalid, match="partition"):
        queued.model_copy(update={"queue": adjudication.queue}).verify((multiple,))
