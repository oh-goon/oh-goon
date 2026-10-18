"""SKILL-3A harness-neutral data protocol; never opens a process or network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oh_goon.core.enforcer_lace_trust import canonical_bytes
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.agent_harness_contract import (
    decode_agent_harness_submission,
    prepare_agent_harness_input,
)
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def _handoff(tmp_path: Path):
    source = tmp_path / "goons" / "agent" / "review"
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
    repository = tmp_path / "repos" / "team" / "project"
    repository.mkdir(parents=True)
    (repository / "main.py").write_bytes(b"print('hello')\n")
    skill = tmp_path / "skills" / "team" / "audit"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Review\nReport accurate findings.\n")
    (skill / "scripts" / "inspect.sh").write_bytes(b"#!/bin/sh\necho review\n")
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


def _report(identity: str, *, verdict: str = "CLEAN", findings=None, summary="Review finished"):
    return canonical_bytes(
        {
            "schema": "oh-goon.agent-harness-submission/v1",
            "handoff_identity": identity,
            "verdict": verdict,
            "findings": [] if findings is None else findings,
            "summary": summary,
        }
    )


def test_original_frozen_packet_projects_actual_task_and_full_skill_resources_without_disk_reads(
    tmp_path, monkeypatch
):
    handoff, skill, repository = _handoff(tmp_path)
    packet = handoff.encode_prework_delivery()
    (skill / "SKILL.md").unlink()
    (repository / "main.py").unlink()
    import os

    monkeypatch.setattr(os, "open", lambda *a, **kw: pytest.fail("SKILL-3A reopened source"))
    projected = prepare_agent_harness_input(
        packet, expected_handoff_identity=handoff.handoff_identity
    )
    assert projected.harness == "opencode"
    assert projected.task == "inspect source"
    assert projected.skill_instructions == "# Review\nReport accurate findings.\n"
    assert {member.path for member in projected.skill_resources} == {
        "SKILL.md",
        "scripts/inspect.sh",
    }
    assert projected.repository_resources[0].data == b"print('hello')\n"
    assert projected.source_identity == handoff.source.capture_identity
    assert projected.handoff.authority == "PREWORK_NOT_ADMITTED"
    assert projected.handoff.effect_permission == "NONE"


def test_frozen_harness_input_rejects_tampered_original_packet_and_wrong_step(tmp_path):
    handoff, _, _ = _handoff(tmp_path)
    packet = handoff.encode_prework_delivery()
    with pytest.raises(FleetPlanInvalid):
        prepare_agent_harness_input(
            packet, expected_handoff_identity="agent-step-prework-handoff-v1-" + "0" * 64
        )
    modified = json.loads(packet)
    modified["step"]["task"] = "do something else"
    with pytest.raises(FleetPlanInvalid):
        prepare_agent_harness_input(
            canonical_bytes(modified), expected_handoff_identity=handoff.handoff_identity
        )


def test_harness_clean_is_untrusted_claim_not_execution_or_verification(tmp_path):
    handoff, _, _ = _handoff(tmp_path)
    result = decode_agent_harness_submission(
        _report(handoff.handoff_identity), expected_handoff_identity=handoff.handoff_identity
    )
    assert result.verdict == "CLEAN"
    assert result.authority == "UNTRUSTED_HARNESS_CLAIM"
    assert result.submission_identity.startswith("agent-harness-submission-v1-")
    assert not hasattr(result, "authorized")
    assert not hasattr(result, "execution_completed")


def test_harness_findings_and_inconclusive_have_distinct_identities(tmp_path):
    handoff, _, _ = _handoff(tmp_path)
    ident = handoff.handoff_identity
    f = [{"location": "main.py:1", "explanation": "Sample issue"}]
    result = decode_agent_harness_submission(
        _report(ident, verdict="FINDINGS", findings=f), expected_handoff_identity=ident
    )
    assert result.findings[0].location == "main.py:1"
    other = decode_agent_harness_submission(
        _report(ident, verdict="INCONCLUSIVE"), expected_handoff_identity=ident
    )
    assert result.submission_identity != other.submission_identity
    assert other.authority == "UNTRUSTED_HARNESS_CLAIM"


@pytest.mark.parametrize(
    "change",
    [
        "wrong-identity",
        "extra-authority",
        "extra-tool",
        "duplicate",
        "non-canonical",
        "clean-findings",
        "empty-findings",
        "no-summary",
        "long-summary",
        "too-many",
        "invalid-finding",
        "wrong-verdict",
        "invalid-json",
        "non-finite",
        "not-bytes",
    ],
)
def test_harness_result_fails_closed_on_forgery_broken_contract_or_overbudget(tmp_path, change):
    handoff, _, _ = _handoff(tmp_path)
    expected = handoff.handoff_identity
    obj = json.loads(_report(expected))
    if change == "wrong-identity":
        obj["handoff_identity"] = "agent-step-prework-handoff-v1-" + "a" * 64
    elif change == "extra-authority":
        obj["effect_permission"] = "EXECUTE"
    elif change == "extra-tool":
        obj["tool_calls"] = [{"name": "shell", "argv": ["id"]}]
    elif change == "duplicate":
        payload = _report(expected)[:-1] + b',"summary":"override"}'
    elif change == "non-canonical":
        payload = json.dumps(obj, indent=2).encode()
    elif change == "clean-findings":
        obj["findings"] = [{"location": "x", "explanation": "y"}]
    elif change == "empty-findings":
        obj["verdict"] = "FINDINGS"
    elif change == "no-summary":
        obj["summary"] = ""
    elif change == "long-summary":
        obj["summary"] = "X" * 4001
    elif change == "too-many":
        obj["findings"] = [{"location": "x", "explanation": "y"}] * 129
    elif change == "invalid-finding":
        obj["findings"] = [{"location": "", "explanation": "y"}]
    elif change == "wrong-verdict":
        obj["verdict"] = "SUCCESS"
    elif change == "invalid-json":
        payload = b"not JSON"
    elif change == "non-finite":
        payload = b'{"summary":NaN}'
    elif change == "not-bytes":
        payload = "not bytes"
    if change not in {"duplicate", "non-canonical", "invalid-json", "non-finite", "not-bytes"}:
        payload = canonical_bytes(obj)
    with pytest.raises(FleetPlanInvalid):
        decode_agent_harness_submission(payload, expected_handoff_identity=expected)
