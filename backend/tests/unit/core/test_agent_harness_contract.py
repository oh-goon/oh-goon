"""Frozen harness handoff and untrusted output, without native adapter execution."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from oh_goon.core.agent_harness_contract import (
    AgentHarnessInput,
    AgentHarnessIntent,
    AgentHarnessOutputLimits,
    AgentHarnessSelection,
    decode_agent_harness_submission,
    prepare_agent_harness_input,
)
from oh_goon.core.enforcer_lace_trust import canonical_bytes
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_review import accept_review, review_scope
from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
from oh_goon.core.models.execution_identity import ExecutionIdentity
from oh_goon.core.models.fleet_plan_execution_policy import (
    FleetPlanRunLimits,
    InspectionExecutionPolicy,
    VerificationExecutionPolicy,
)
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def _handoff(
    tmp_path: Path, *, skill_bytes: bytes = b"# Review\nReport accurate findings.\n"
) -> tuple[FrozenAgentStepHandoff, Path, Path]:
    source = tmp_path / "goons/agent/review"
    source.mkdir(parents=True)
    (source / "README.md").write_text("# Agent Review\n")
    (source / "goon.yml").write_text("""inputs:
  repo: {type: repository_reference}
  skill: {type: skill_reference}
  acceptance: {type: acceptance_profile}
steps:
- uses: agent
  harness: opencode
  task: inspect source
""")
    (tmp_path / "plan.yml").write_text("inputs: [repo, skill, acceptance]\ngoons: [agent/review]\n")
    repository = tmp_path / "repos/team/project"
    repository.mkdir(parents=True)
    (repository / "main.py").write_bytes(b"print('hello')\n")
    skill = tmp_path / "skills/team/audit"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(skill_bytes)
    (skill / "scripts/inspect.sh").write_bytes(b"#!/bin/sh\necho review\n")
    (skill / "reference.bin").write_bytes(b"\xff\x00binary resource")
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(tmp_path / "goons"))
    _, sha = resolver.input_definitions_with_source(tmp_path / "plan.yml")
    handoff = resolver.prepare_agent_step_handoff(
        tmp_path / "plan.yml",
        inputs={"repo": "local:team/project", "skill": "local:team/audit", "acceptance": "review"},
        repository_root=tmp_path / "repos",
        skill_root=tmp_path / "skills",
        allowed_acceptance=("review",),
        expected_source_sha256=sha,
    )
    return handoff, skill, repository


def _selection(**changes: Any) -> AgentHarnessSelection:
    return AgentHarnessSelection(
        **{
            "harness": "opencode",
            "implementation_identity": "sha256:" + "1" * 64,
            "capabilities": ("frozen_source", "hashmarks_evidence", "report"),
            **changes,
        }
    )


def _execution(**changes: Any) -> AgentHarnessIntent:
    return AgentHarnessIntent(
        **{
            "execution_identity": ExecutionIdentity(digest="sha256:" + "2" * 64),
            "backend_id": "standard-local",
            "policy": VerificationExecutionPolicy(),
            **changes,
        }
    )


def _prepare(handoff: FrozenAgentStepHandoff, **changes: Any) -> AgentHarnessInput:
    return prepare_agent_harness_input(
        handoff.encode_prework_delivery(),
        **{
            "expected_handoff_identity": handoff.handoff_identity,
            "selection": _selection(),
            "execution": _execution(),
            "occurrence": "review.first",
            **changes,
        },
    )


def _report(prepared: AgentHarnessInput, **changes: Any) -> dict[str, Any]:
    return {
        "schema": "oh-goon.agent-harness-submission/v2",
        "input_identity": prepared.input_identity,
        "handoff_identity": prepared.handoff_identity,
        "status": "COMPLETED",
        "verdict": "CLEAN",
        "findings": [],
        "summary": "Review finished",
        "record": None,
        "observations": [],
        "artifacts": [],
        **changes,
    }


def test_frozen_input_preserves_actual_task_and_all_resources_without_source_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handoff, skill, repository = _handoff(tmp_path)
    packet = handoff.encode_prework_delivery()
    (skill / "SKILL.md").unlink()
    (repository / "main.py").unlink()
    monkeypatch.setattr(os, "open", lambda *a, **kw: pytest.fail("harness reopened source"))
    prepared = prepare_agent_harness_input(
        packet,
        expected_handoff_identity=handoff.handoff_identity,
        selection=_selection(),
        execution=_execution(),
        occurrence="review.first",
    )
    assert prepared.harness == "opencode"
    assert prepared.task == "inspect source"
    assert prepared.skill_instructions == "# Review\nReport accurate findings.\n"
    assert {member.path for member in prepared.skill_resources} == {
        "SKILL.md",
        "scripts/inspect.sh",
        "reference.bin",
    }
    assert next(m for m in prepared.skill_resources if m.path == "reference.bin").data == (
        b"\xff\x00binary resource"
    )
    assert prepared.repository_resources[0].data == b"print('hello')\n"
    assert prepared.source_identity == handoff.source.capture_identity
    assert prepared.candidate_identity == handoff.source.repository.content_identity
    assert prepared.scope_identity == review_scope(handoff)
    assert prepared.skill_identity == handoff.source.skill.content_identity
    assert prepared.occurrence == "review.first" != handoff.step_id
    assert prepared.handoff.authority == "PREWORK_NOT_ADMITTED"
    assert prepared.handoff.effect_permission == "NONE"


@pytest.mark.parametrize("attack", ["identity", "task", "harness", "recipients"])
def test_changed_source_binding_or_execution_selection_is_rejected(
    tmp_path: Path, attack: str
) -> None:
    handoff, _, _ = _handoff(tmp_path)
    packet = handoff.encode_prework_delivery()
    options: dict[str, Any] = dict(
        expected_handoff_identity=handoff.handoff_identity,
        selection=_selection(),
        execution=_execution(),
        occurrence="review.first",
    )
    if attack == "identity":
        options["expected_handoff_identity"] = "agent-step-prework-handoff-v1-" + "0" * 64
    elif attack == "task":
        modified = json.loads(packet)
        modified["step"]["task"] = "do something else"
        packet = canonical_bytes(modified)
    elif attack == "harness":
        options["selection"] = _selection(harness="another")
    else:
        options["execution"] = _execution(disclosure_recipients=("other_model",))
    with pytest.raises(FleetPlanInvalid):
        prepare_agent_harness_input(packet, **options)


@pytest.mark.parametrize("skill_bytes", [b"\xff", b" \n\t"])
def test_text_input_requires_nonblank_utf8_skill_instructions(
    tmp_path: Path, skill_bytes: bytes
) -> None:
    handoff, _, _ = _handoff(tmp_path, skill_bytes=skill_bytes)
    with pytest.raises(FleetPlanInvalid):
        _prepare(handoff)


@pytest.mark.parametrize(
    "changes",
    [
        {"selection": _selection(implementation_identity="sha256:" + "3" * 64)},
        {"selection": _selection(native_configuration_identity="sha256:" + "4" * 64)},
        {
            "execution": _execution(
                execution_identity=ExecutionIdentity(digest="sha256:" + "5" * 64)
            )
        },
        {"execution": _execution(backend_id="another-selected-backend")},
        {"execution": _execution(policy=InspectionExecutionPolicy())},
        {
            "execution": _execution(
                limits=FleetPlanRunLimits(
                    deadline_at=datetime(2030, 1, 1, tzinfo=UTC), step_timeout_seconds=15.0
                )
            )
        },
        {"occurrence": "review.second"},
        {"output_limits": AgentHarnessOutputLimits(max_submission_bytes=4096)},
    ],
)
def test_input_identity_binds_selected_execution_and_output_contract(
    tmp_path: Path, changes: dict[str, Any]
) -> None:
    handoff, _, _ = _handoff(tmp_path)
    prepared = _prepare(handoff)
    changed = _prepare(handoff, **changes)
    assert _prepare(handoff).input_identity == prepared.input_identity
    assert changed.input_identity != prepared.input_identity
    assert changed.handoff_identity == prepared.handoff_identity


def test_optional_capabilities_enable_only_the_supported_requested_intersection(
    tmp_path: Path,
) -> None:
    handoff, _, _ = _handoff(tmp_path)
    prepared = _prepare(
        handoff,
        selection=_selection(
            capabilities=(
                "artifacts",
                "frozen_source",
                "hashmarks_evidence",
                "observations",
                "report",
            )
        ),
        optional_capabilities=("observations", "proposal"),
    )
    assert set(prepared.enabled_capabilities) == {
        "frozen_source",
        "hashmarks_evidence",
        "observations",
        "report",
    }
    assert prepared.unavailable_optional_capabilities == ("proposal",)
    assert prepared.harness == handoff.harness
    assert prepared.requested_effects == handoff.requested_effects


@pytest.mark.parametrize(
    "changes",
    [
        {"selection": {"capabilities": ("hashmarks_evidence", "report")}},
        {"selection": {"capabilities": ("frozen_source", "report")}},
        {"selection": {"capabilities": ("frozen_source", "hashmarks_evidence")}},
        {"required_capabilities": ("proposal",)},
        {"required_capabilities": ("unknown",)},
        {"required_capabilities": ("report", "report")},
        {"optional_capabilities": ("unknown",)},
        {"optional_capabilities": ("report", "observations")},
        {"required_capabilities": ("report",), "optional_capabilities": ("report",)},
        {"selection": {"capabilities": ("frozen_source", "hashmarks_evidence", "unknown")}},
        {"selection": {"capabilities": ("report", "frozen_source", "hashmarks_evidence")}},
        {
            "selection": {
                "capabilities": ("frozen_source", "hashmarks_evidence", "report", "report")
            }
        },
    ],
)
def test_required_or_noncanonical_capabilities_fail_without_fallback(
    tmp_path: Path, changes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    handoff, _, _ = _handoff(tmp_path)
    monkeypatch.setattr(
        os, "open", lambda *a, **kw: pytest.fail("capability negotiation read source")
    )
    with pytest.raises((FleetPlanInvalid, ValidationError)):
        selected = dict(changes)
        if "selection" in selected:
            selected["selection"] = _selection(**selected["selection"])
        _prepare(handoff, **selected)


def test_completed_generic_claims_keep_distinct_untrusted_results(tmp_path: Path) -> None:
    handoff, _, _ = _handoff(tmp_path)
    prepared = _prepare(handoff)
    records = []
    for verdict, findings in (
        ("CLEAN", []),
        ("FINDINGS", [{"location": "main.py:1", "explanation": "Sample issue"}]),
        ("INCONCLUSIVE", []),
        ("INCONCLUSIVE", [{"location": "main.py:1", "explanation": "Partial finding"}]),
    ):
        payload = canonical_bytes(_report(prepared, verdict=verdict, findings=findings))
        result = decode_agent_harness_submission(payload, expected_input=prepared)
        assert result.verdict == verdict
        assert result.authority == "UNTRUSTED_HARNESS_CLAIM"
        assert result.submission_identity.startswith("agent-harness-submission-v2-")
        records.append(result)
        with pytest.raises(FleetPlanInvalid):
            accept_review(payload.decode(), handoff, prepared.occurrence)
    assert len({result.submission_identity for result in records}) == 4
    assert records[1].findings[0].location == "main.py:1"


@pytest.mark.parametrize("role", ["review", "adjudication", "proposal"])
def test_phase_output_requires_a_separate_bounded_record_not_a_generic_verdict(
    tmp_path: Path, role: str
) -> None:
    handoff, _, _ = _handoff(tmp_path)
    prepared = _prepare(
        handoff,
        role=role,
        selection=_selection(
            capabilities=tuple(sorted(("frozen_source", "hashmarks_evidence", role)))
        ),
    )
    payload = canonical_bytes(_report(prepared, verdict=None, record="untrusted phase record"))
    result = decode_agent_harness_submission(payload, expected_input=prepared)
    assert result.record == "untrusted phase record"
    assert result.authority == "UNTRUSTED_HARNESS_CLAIM"
    with pytest.raises(FleetPlanInvalid):
        decode_agent_harness_submission(canonical_bytes(_report(prepared)), expected_input=prepared)
    with pytest.raises(FleetPlanInvalid):
        decode_agent_harness_submission(
            canonical_bytes(_report(prepared, verdict=None, record="é" * 32769)),
            expected_input=prepared,
        )


def test_phase_record_still_needs_original_source_and_occurrence_acceptance(tmp_path: Path) -> None:
    handoff, _, _ = _handoff(tmp_path)
    prepared = _prepare(
        handoff,
        role="review",
        selection=_selection(capabilities=("frozen_source", "hashmarks_evidence", "review")),
    )
    record = dict(
        schema="oh-goon.agent-review/v1",
        reviewer=prepared.occurrence,
        candidate_identity=prepared.candidate_identity,
        scope_identity=prepared.scope_identity,
        skill_identity=prepared.skill_identity,
        verdict="CLEAN",
        summary="Complete scoped review",
        reviewed_paths=["repo/" + member.path for member in handoff.source.repository.members],
        findings=[],
    )
    for candidate in (prepared.candidate_identity, "stale-candidate"):
        record["candidate_identity"] = candidate
        result = decode_agent_harness_submission(
            canonical_bytes(_report(prepared, verdict=None, record=json.dumps(record))),
            expected_input=prepared,
        )
        assert isinstance(result.record, str)
        if candidate == prepared.candidate_identity:
            assert accept_review(result.record, handoff, prepared.occurrence).verdict == "CLEAN"
        else:
            with pytest.raises(FleetPlanInvalid):
                accept_review(result.record, handoff, prepared.occurrence)


@pytest.mark.parametrize("status", ["FAILED", "INCOMPLETE"])
def test_noncompleted_claim_cannot_carry_success_or_role_record(
    tmp_path: Path, status: str
) -> None:
    handoff, _, _ = _handoff(tmp_path)
    prepared = _prepare(handoff)
    result = decode_agent_harness_submission(
        canonical_bytes(_report(prepared, status=status, verdict=None)), expected_input=prepared
    )
    assert result.status == status
    assert result.authority == "UNTRUSTED_HARNESS_CLAIM"
    for claim in (
        {"verdict": "CLEAN"},
        {"findings": [{"location": "main.py:1", "explanation": "Claim"}]},
        {"record": "claimed verification"},
    ):
        with pytest.raises(FleetPlanInvalid):
            decode_agent_harness_submission(
                canonical_bytes(_report(prepared, **{"status": status, "verdict": None, **claim})),
                expected_input=prepared,
            )


@pytest.mark.parametrize(
    "attack",
    [
        "input-identity",
        "handoff-identity",
        "extra-authority",
        "extra-tool",
        "duplicate",
        "noncanonical",
        "clean-findings",
        "empty-findings",
        "no-summary",
        "long-summary",
        "too-many",
        "invalid-finding",
        "finding-extra",
        "wrong-verdict",
        "wrong-status",
        "invalid-json",
        "nonfinite",
        "not-bytes",
        "old-schema",
        "missing-field",
    ],
)
def test_result_forgery_malformed_wire_and_report_bounds_fail(tmp_path: Path, attack: str) -> None:
    handoff, _, _ = _handoff(tmp_path)
    prepared = _prepare(handoff)
    obj = _report(prepared)
    if attack == "input-identity":
        obj["input_identity"] = "another-input"
    elif attack == "handoff-identity":
        obj["handoff_identity"] = "agent-step-prework-handoff-v1-" + "a" * 64
    elif attack == "extra-authority":
        obj["effect_permission"] = "EXECUTE"
    elif attack == "extra-tool":
        obj["tool_calls"] = [{"name": "shell", "argv": ["id"]}]
    elif attack == "clean-findings":
        obj["findings"] = [{"location": "x", "explanation": "y"}]
    elif attack == "empty-findings":
        obj["verdict"] = "FINDINGS"
    elif attack == "no-summary":
        obj["summary"] = " "
    elif attack == "long-summary":
        obj["summary"] = "X" * 4001
    elif attack == "too-many":
        obj.update(verdict="FINDINGS", findings=[{"location": "x", "explanation": "y"}] * 129)
    elif attack == "invalid-finding":
        obj.update(verdict="FINDINGS", findings=[{"location": "", "explanation": "y"}])
    elif attack == "finding-extra":
        obj.update(
            verdict="FINDINGS", findings=[{"location": "x", "explanation": "y", "verified": True}]
        )
    elif attack == "wrong-verdict":
        obj["verdict"] = "SUCCESS"
    elif attack == "wrong-status":
        obj["status"] = "VERIFIED"
    elif attack == "old-schema":
        obj["schema"] = "oh-goon.agent-harness-submission/v1"
    elif attack == "missing-field":
        obj.pop("artifacts")
    payload: Any = canonical_bytes(obj)
    if attack == "duplicate":
        payload = payload[:-1] + b',"summary":"override"}'
    elif attack == "noncanonical":
        payload = json.dumps(obj, indent=2).encode()
    elif attack == "invalid-json":
        payload = b"not JSON"
    elif attack == "nonfinite":
        payload = b'{"summary":NaN}'
    elif attack == "not-bytes":
        payload = "not bytes"
    with pytest.raises(FleetPlanInvalid):
        decode_agent_harness_submission(payload, expected_input=prepared)


def test_request_byte_budget_binds_output_and_counts_encoded_bytes(tmp_path: Path) -> None:
    handoff, _, _ = _handoff(tmp_path)
    prepared = _prepare(handoff, output_limits=AgentHarnessOutputLimits(max_submission_bytes=2048))
    small = canonical_bytes(_report(prepared))
    assert len(small) < 2048
    assert decode_agent_harness_submission(small, expected_input=prepared).verdict == "CLEAN"
    large = canonical_bytes(_report(prepared, summary="é" * 1000))
    assert len(large) > 2048
    with pytest.raises(FleetPlanInvalid):
        decode_agent_harness_submission(large, expected_input=prepared)
    default = _prepare(handoff)
    oversized = canonical_bytes(
        _report(
            default,
            verdict="FINDINGS",
            findings=[{"location": "é" * 4000, "explanation": "é" * 4000}] * 128,
        )
    )
    assert len(oversized) > 1024 * 1024
    with pytest.raises(FleetPlanInvalid):
        decode_agent_harness_submission(oversized, expected_input=default)


def _artifact(name: str = "review-log", *, byte_count: int = 3) -> dict[str, Any]:
    return {
        "name": name,
        "content": {"kind": "file", "sha256": "a" * 64, "byte_count": byte_count, "entry_count": 1},
    }


def test_optional_observations_and_artifacts_are_bounded_claims_not_storage_authority(
    tmp_path: Path,
) -> None:
    handoff, _, _ = _handoff(tmp_path)
    prepared = _prepare(
        handoff,
        selection=_selection(
            capabilities=(
                "artifacts",
                "frozen_source",
                "hashmarks_evidence",
                "observations",
                "report",
            )
        ),
        optional_capabilities=("artifacts", "observations"),
        output_limits=AgentHarnessOutputLimits(
            max_observations=1, max_artifacts=1, max_artifact_bytes=3
        ),
    )
    obj = _report(
        prepared,
        observations=[
            {"kind": "tool", "summary": "Observed tool result", "native_event_id": "turn-1"}
        ],
        artifacts=[_artifact()],
    )
    result = decode_agent_harness_submission(canonical_bytes(obj), expected_input=prepared)
    assert result.observations[0].summary == "Observed tool result"
    assert result.artifacts[0].content.byte_count == 3
    assert result.artifacts[0].content.object_id == "file-sha256-" + "a" * 64
    assert result.authority == "UNTRUSTED_HARNESS_CLAIM"
    with pytest.raises(FleetPlanInvalid):
        decode_agent_harness_submission(
            canonical_bytes(_report(_prepare(handoff), artifacts=[_artifact()])),
            expected_input=_prepare(handoff),
        )
    without_features = _prepare(handoff)
    with pytest.raises(FleetPlanInvalid):
        decode_agent_harness_submission(
            canonical_bytes(_report(without_features, observations=obj["observations"])),
            expected_input=without_features,
        )


def test_artifact_byte_budget_counts_all_references(tmp_path: Path) -> None:
    handoff, _, _ = _handoff(tmp_path)
    prepared = _prepare(
        handoff,
        selection=_selection(
            capabilities=("artifacts", "frozen_source", "hashmarks_evidence", "report")
        ),
        optional_capabilities=("artifacts",),
        output_limits=AgentHarnessOutputLimits(max_artifacts=2, max_artifact_bytes=3),
    )
    with pytest.raises(FleetPlanInvalid):
        decode_agent_harness_submission(
            canonical_bytes(
                _report(
                    prepared,
                    artifacts=[_artifact("first", byte_count=2), _artifact("second", byte_count=2)],
                )
            ),
            expected_input=prepared,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"observations": [{"kind": "agent", "summary": "claim", "native_event_id": None}] * 2},
        {"observations": [{"kind": "agent", "summary": " ", "native_event_id": None}]},
        {
            "observations": [
                {"kind": "agent", "summary": "claim", "native_event_id": None, "authorized": True}
            ]
        },
        {"observations": [{"kind": "context", "summary": "claim", "native_event_id": "x" * 161}]},
        {"artifacts": [_artifact(), _artifact("another")]},
        {"artifacts": [_artifact(byte_count=4)]},
        {"artifacts": [{**_artifact(), "path": "/host/secret"}]},
        {"artifacts": [{**_artifact(), "url": "https://example.test/artifact"}]},
        {
            "artifacts": [
                {
                    "name": "review",
                    "content": {**_artifact()["content"], "object_id": "file-sha256-" + "a" * 64},
                }
            ]
        },
        {
            "artifacts": [
                {
                    "name": "review",
                    "content": {**_artifact()["content"], "reference_id": "ref-" + "a" * 32},
                }
            ]
        },
    ],
)
def test_observation_and_artifact_limits_reject_expanded_output_authority(
    tmp_path: Path, changes: dict[str, Any]
) -> None:
    handoff, _, _ = _handoff(tmp_path)
    prepared = _prepare(
        handoff,
        selection=_selection(
            capabilities=(
                "artifacts",
                "frozen_source",
                "hashmarks_evidence",
                "observations",
                "report",
            )
        ),
        optional_capabilities=("artifacts", "observations"),
        output_limits=AgentHarnessOutputLimits(
            max_observations=1, max_artifacts=1, max_artifact_bytes=3
        ),
    )
    with pytest.raises(FleetPlanInvalid):
        decode_agent_harness_submission(
            canonical_bytes(_report(prepared, **changes)), expected_input=prepared
        )


@pytest.mark.parametrize(
    "limit",
    [
        {"max_submission_bytes": 1024 * 1024 + 1},
        {"max_observations": 129},
        {"max_artifacts": 33},
        {"max_artifact_bytes": 64 * 1024 * 1024 + 1},
    ],
)
def test_request_limits_cannot_expand_contract_hard_bounds(limit: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        AgentHarnessOutputLimits(**limit)
