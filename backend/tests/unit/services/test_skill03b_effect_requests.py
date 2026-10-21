"""SKILL-3B original Agent Step intent is source-bound, never executable authority."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.agent_harness_contract import (
    AgentHarnessInput,
    AgentHarnessIntent,
    AgentHarnessSelection,
    decode_agent_harness_submission,
    prepare_agent_harness_input,
)
from oh_goon.core.enforcer_lace_trust import canonical_bytes
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
from oh_goon.core.models.execution_identity import ExecutionIdentity
from oh_goon.core.models.fleet_plan import AgentPlanStep, AgentStepRequestedEffects
from oh_goon.core.models.fleet_plan_execution_policy import VerificationExecutionPolicy
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def _make_step(tmp_path: Path, *, effect_block: str = "") -> FrozenAgentStepHandoff:
    goon = tmp_path / "goons" / "agent" / "review"
    goon.mkdir(parents=True)
    (goon / "README.md").write_text("# Agent review\n")
    (goon / "goon.yml").write_text(
        """inputs:
  repo: {type: repository_reference}
  skill: {type: skill_reference}
  acceptance: {type: acceptance_profile}
steps:
- uses: agent
  harness: opencode
  task: Review these changes
"""
        + effect_block
    )
    (tmp_path / "plan.yml").write_text("inputs: [repo, skill, acceptance]\ngoons: [agent/review]\n")
    repo = tmp_path / "repos" / "team" / "project"
    repo.mkdir(parents=True)
    (repo / "main.py").write_text("print('source')\n")
    skill = tmp_path / "skills" / "team" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Source review\n")
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(tmp_path / "goons"))
    _, source_sha = resolver.input_definitions_with_source(tmp_path / "plan.yml")
    return resolver.prepare_agent_step_handoff(
        tmp_path / "plan.yml",
        inputs={"repo": "local:team/project", "skill": "local:team/review", "acceptance": "review"},
        repository_root=tmp_path / "repos",
        skill_root=tmp_path / "skills",
        allowed_acceptance=("review",),
        expected_source_sha256=source_sha,
    )


def _prepare(handoff: FrozenAgentStepHandoff) -> AgentHarnessInput:
    return prepare_agent_harness_input(
        handoff.encode_prework_delivery(),
        expected_handoff_identity=handoff.handoff_identity,
        selection=AgentHarnessSelection(
            harness=handoff.harness,
            implementation_identity="sha256:" + "1" * 64,
            capabilities=("frozen_source", "hashmarks_evidence", "report"),
        ),
        execution=AgentHarnessIntent(
            execution_identity=ExecutionIdentity(digest="sha256:" + "2" * 64),
            backend_id="standard-local",
            policy=VerificationExecutionPolicy(),
            disclosure_recipients=handoff.requested_effects.recipients,
        ),
        occurrence="review.first",
    )


EFFECTS = """  requested_effects:
    repository_access: write
    recipients: [internal_model]
    capabilities:
      tools: [pytest, ruff]
      egress:
        mode: cidr_allowlist
        rules:
        - cidr: 10.0.0.0/8
          protocol: tcp
          ports: [443]
"""


def test_explicit_authored_effects_are_bound_to_frozen_original_handoff(tmp_path):
    handoff = _make_step(tmp_path, effect_block=EFFECTS)
    effects = handoff.requested_effects
    assert effects.repository_access == "write"
    assert effects.recipients == ("internal_model",)
    assert effects.capabilities.tools == ("pytest", "ruff")
    assert effects.capabilities.egress.mode == "cidr_allowlist"
    assert handoff.effect_permission == "NONE"
    assert handoff.authority == "PREWORK_NOT_ADMITTED"
    packet = handoff.encode_prework_delivery()
    frozen = _prepare(handoff)
    assert frozen.requested_effects == effects
    assert frozen.handoff.effect_permission == "NONE"
    assert json.loads(packet)["step"]["requested_effects"]["repository_access"] == "write"


def test_effect_requests_change_handoff_identity_not_source_capture_identity(tmp_path):
    regular = _make_step(tmp_path / "default")
    expanded = _make_step(tmp_path / "explicit", effect_block=EFFECTS)
    assert regular.source.repository.content_identity == expanded.source.repository.content_identity
    assert regular.source.skill.content_identity == expanded.source.skill.content_identity
    assert regular.handoff_identity != expanded.handoff_identity


def test_unconfigured_existing_agent_packet_stays_original_byte_schema(tmp_path):
    handoff = _make_step(tmp_path)
    obj = json.loads(handoff.encode_prework_delivery())
    assert set(obj["step"]) == {
        "owner_source_sha256",
        "step_id",
        "harness",
        "task",
        "timeout_seconds",
    }
    assert handoff.requested_effects == AgentStepRequestedEffects()
    assert (
        FrozenAgentStepHandoff.decode_prework_delivery(
            handoff.encode_prework_delivery(),
            expected_handoff_identity=handoff.handoff_identity,
        ).requested_effects
        == AgentStepRequestedEffects()
    )


@pytest.mark.parametrize(
    "invalid",
    [
        {"repository_access": "admin"},
        {"recipients": ["external", "external"]},
        {"recipients": ["z", "a"]},
        {"recipients": ["https://example.com"]},
        {"capabilities": {"tools": ["shell;rm"]}},
        {"capabilities": {"tools": ["ruff", "pytest"]}},
        {"capabilities": {"egress": {"mode": "host_unrestricted"}}},
        {"capabilities": {"nested_strict_execution": True}},
        {"is_admitted": True},
    ],
)
def test_authored_effects_reject_ambient_or_noncanonical_requests(invalid):
    with pytest.raises(ValidationError):
        AgentPlanStep.model_validate(
            {
                "kind": "agent",
                "step_id": "review",
                "harness": "opencode",
                "task": "review",
                "requested_effects": invalid,
            }
        )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda step: step["requested_effects"].update({"repository_access": "read_only"}),
        lambda step: step["requested_effects"].update({"effect_permission": "EXECUTE"}),
        lambda step: step["requested_effects"]["capabilities"].update(
            {"nested_strict_execution": True}
        ),
        lambda step: step["requested_effects"].update({"recipients": ["other_model"]}),
        lambda step: step.pop("requested_effects"),
    ],
)
def test_transport_tampering_cannot_expand_or_remove_requested_effects(tmp_path, mutator):
    handoff = _make_step(tmp_path, effect_block=EFFECTS)
    obj = json.loads(handoff.encode_prework_delivery())
    mutator(obj["step"])
    with pytest.raises(FleetPlanInvalid):
        FrozenAgentStepHandoff.decode_prework_delivery(
            canonical_bytes(obj),
            expected_handoff_identity=handoff.handoff_identity,
        )


def test_clean_result_is_still_only_untrusted_claim_with_write_request(tmp_path):
    handoff = _make_step(tmp_path, effect_block=EFFECTS)
    prepared = _prepare(handoff)
    payload = canonical_bytes(
        {
            "schema": "oh-goon.agent-harness-submission/v2",
            "input_identity": prepared.input_identity,
            "handoff_identity": handoff.handoff_identity,
            "status": "COMPLETED",
            "verdict": "CLEAN",
            "findings": [],
            "summary": "looks good",
            "record": None,
            "observations": [],
            "artifacts": [],
        }
    )
    result = decode_agent_harness_submission(payload, expected_input=prepared)
    assert result.authority == "UNTRUSTED_HARNESS_CLAIM"
    assert not hasattr(result, "effect_permission")


def test_explicit_requested_write_and_egress_cannot_bypass_production_resolver(tmp_path):
    handoff = _make_step(tmp_path, effect_block=EFFECTS)
    assert handoff.requested_effects.repository_access == "write"
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(tmp_path / "goons"))
    with pytest.raises(FleetPlanInvalid, match="preview-only"):
        resolver.resolve(tmp_path / "plan.yml")
