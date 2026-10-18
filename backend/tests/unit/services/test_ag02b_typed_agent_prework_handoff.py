"""AG-02B: typed Agent Step uses original Goon/compiler and prework capture; no effects."""
from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import AgentPlanStep
from oh_goon.integrations.fleet_plan_compilation import parse_goon_template
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def _case(tmp_path: Path, step: str = "- uses: agent\n  harness: opencode\n  task: review-only\n"):
    goon = tmp_path / "goons" / "agent" / "audit"
    goon.mkdir(parents=True)
    (goon / "README.md").write_text("# Audit\n")
    (goon / "goon.yml").write_text("""inputs:
  repo: {type: repository_reference}
  skill: {type: skill_reference}
  acceptance: {type: acceptance_profile}
steps:
""" + step)
    plan = tmp_path / "plan.yml"
    plan.write_text("inputs: [repo, skill, acceptance]\ngoons: [agent/audit]\n")
    repo = tmp_path / "repos" / "team" / "repo"
    repo.mkdir(parents=True)
    (repo / "secret.txt").write_bytes(b"private source\n")
    skill = tmp_path / "skills" / "team" / "check"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"# Review\n")
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(tmp_path / "goons"))
    _, sha = resolver.input_definitions_with_source(plan)
    kw = dict(
        inputs={"repo": "local:team/repo", "skill": "local:team/check", "acceptance": "review"},
        repository_root=tmp_path / "repos", skill_root=tmp_path / "skills",
        allowed_acceptance=("review",), expected_source_sha256=sha,
    )
    return resolver, plan, kw, goon, repo


def test_original_atomic_goon_compiles_explicit_typed_agent_step_without_shell():
    compiled = parse_goon_template("""steps:
- uses: agent
  harness: opencode
  task: check the patch
""")
    step = compiled.steps[0]
    assert isinstance(step, AgentPlanStep)
    assert step.kind == "agent"
    assert step.harness == "opencode"
    assert not hasattr(step, "argv")
    assert not hasattr(step, "command")


@pytest.mark.parametrize("step", [
    "- uses: agent\n  harness: opencode\n  task: check\n  run: echo forbidden\n",
    "- uses: agent\n  harness: opencode\n  task: check\n  argv: [sh, -c, id]\n",
    "- uses: agent\n  harness: opencode\n  task: check\n  model_endpoint: https://evil.invalid\n",
    "- uses: agent\n  harness: opencode\n  task: check\n  credentials: secret\n",
    "- uses: agent\n  harness: bad/name\n  task: check\n",
    "- uses: agent\n  harness: opencode\n",
])
def test_unapproved_fields_or_incomplete_agent_declaration_rejected(step):
    with pytest.raises((FleetPlanInvalid, ValueError)):
        parse_goon_template("steps:\n" + step)


def test_original_resolver_binds_handoff_and_rejects_all_effect_paths(tmp_path):
    resolver, plan, kw, _, repo = _case(tmp_path)
    handoff = resolver.prepare_agent_step_handoff(plan, **kw)
    assert handoff.authority == "PREWORK_NOT_ADMITTED"
    assert handoff.effect_permission == "NONE"
    assert handoff.step_id == "agent"
    assert handoff.harness == "opencode"
    assert handoff.source.repository.members[0].data == b"private source\n"
    old = handoff.handoff_identity
    (repo / "secret.txt").write_bytes(b"source changed after capture")
    handoff.verify()
    assert handoff.handoff_identity == old
    assert resolver.prepare_agent_step_handoff(plan, **kw).handoff_identity != old
    with pytest.raises(FleetPlanInvalid, match="preview-only"):
        resolver.resolve(plan, inputs=kw["inputs"])
    with pytest.raises(FleetPlanInvalid, match="preview-only"):
        resolver.resolve_source_graph(resolver.analyze_source(plan), inputs=kw["inputs"])


def test_original_graph_referenced_step_source_changes_handoff(tmp_path):
    resolver, plan, kw, goon, _ = _case(tmp_path)
    first = resolver.prepare_agent_step_handoff(plan, **kw)
    spec = goon / "goon.yml"
    spec.write_text(spec.read_text().replace("review-only", "review-diff"))
    second = resolver.prepare_agent_step_handoff(plan, **kw)
    assert second.handoff_identity != first.handoff_identity
    assert first.source.plan_source_sha256 == second.source.plan_source_sha256
    assert first.source.plan_graph_identity != second.source.plan_graph_identity


def test_prework_rejects_zero_or_multiple_agent_steps(tmp_path):
    resolver, plan, kw, goon, _ = _case(tmp_path)
    spec = goon / "goon.yml"
    text = spec.read_text()
    spec.write_text(text.replace("- uses: agent\n  harness: opencode\n  task: review-only", "- run: echo command-only"))
    with pytest.raises(FleetPlanInvalid, match="exactly one typed agent Step"):
        resolver.prepare_agent_step_handoff(plan, **kw)
    spec.write_text(text + "- uses: agent\n  harness: codex\n  task: other\n")
    with pytest.raises(FleetPlanInvalid, match="exactly one typed agent Step"):
        resolver.prepare_agent_step_handoff(plan, **kw)


def test_handoff_rejects_stale_root_wrong_authority_and_tampered_bytes(tmp_path):
    resolver, plan, kw, _, _ = _case(tmp_path)
    with pytest.raises(FleetPlanInvalid, match="revision changed"):
        resolver.prepare_agent_step_handoff(plan, **{**kw, "expected_source_sha256": "a"*64})
    result = resolver.prepare_agent_step_handoff(plan, **kw)
    import dataclasses
    with pytest.raises(FleetPlanInvalid, match="cannot grant execution"):
        dataclasses.replace(result, effect_permission="EXECUTE").verify()
    object.__setattr__(result.source.repository.members[0], "data", b"modified")
    with pytest.raises(FleetPlanInvalid, match="digest mismatch"):
        result.verify()


def test_canonical_resolver_rejects_agent_without_typed_inputs_before_run(tmp_path):
    goon = tmp_path / "goon.yml"
    goon.write_text("steps:\n- uses: agent\n  harness: codex\n  task: test\n")
    resolver = FleetPlanResolver()
    with pytest.raises(FleetPlanInvalid, match="preview-only"):
        resolver.resolve(goon)


def test_repeated_import_occurrences_are_not_collapsed_into_one_handoff(tmp_path):
    resolver, plan, kw, _, _ = _case(tmp_path)
    plan.write_text("inputs: [repo, skill, acceptance]\ngoons: [agent/audit, agent/audit]\n")
    graph = resolver.analyze_source(plan)
    assert len(graph.agent_step_sources()) == 2
    sha = graph.root.source_sha256
    with pytest.raises(FleetPlanInvalid, match="exactly one typed agent Step"):
        resolver.prepare_agent_step_handoff(plan, **{**kw, "expected_source_sha256": sha})


def test_ag02b2a_lossless_prework_delivery_never_rereads_disk(tmp_path, monkeypatch):
    import os

    from oh_goon.core.models.agent_source import FrozenAgentStepHandoff

    resolver, plan, kw, goon, repo = _case(tmp_path)
    handoff = resolver.prepare_agent_step_handoff(plan, **kw)
    packet = handoff.encode_prework_delivery()
    assert packet == handoff.encode_prework_delivery()
    # The sender source locations are no longer accessible: the recipient must
    # consume ONLY the transported bytes, not host paths, cache or aliases.
    (repo / "secret.txt").unlink()
    (goon / "goon.yml").unlink()
    monkeypatch.setattr(os, "open", lambda *a, **k: pytest.fail("transport attempted a source reread"))
    received = FrozenAgentStepHandoff.decode_prework_delivery(
        packet, expected_handoff_identity=handoff.handoff_identity
    )
    assert received.handoff_identity == handoff.handoff_identity
    assert received.source.repository.members[0].data == b"private source\n"
    assert received.effect_permission == "NONE"
    assert received.authority == "PREWORK_NOT_ADMITTED"


def test_ag02b2a_packet_tampering_and_forged_authority_fail_before_work(tmp_path, monkeypatch):
    import json

    from oh_goon.core.enforcer_lace_trust import canonical_bytes
    from oh_goon.core.models.agent_source import FrozenAgentStepHandoff

    resolver, plan, kw, _, _ = _case(tmp_path)
    handoff = resolver.prepare_agent_step_handoff(plan, **kw)
    packet = handoff.encode_prework_delivery()
    expected = handoff.handoff_identity
    decode = lambda b: FrozenAgentStepHandoff.decode_prework_delivery(
        b, expected_handoff_identity=expected)
    assert decode(packet).handoff_identity == expected
    original = json.loads(packet)
    import copy
    for change in ("authority", "step", "source", "payload", "path", "encoding", "duplicate"):
        x = copy.deepcopy(original)
        if change == "authority":
            x["effect_permission"] = "EXECUTE"
        elif change == "step":
            x["step"]["task"] = "exfiltrate"
        elif change == "source":
            x["source"]["capture_identity"] = "another capture"
        elif change == "payload":
            x["source"]["repository"]["members"][0]["data_b64"] = "ZXZpbA=="
        elif change == "path":
            x["source"]["skill"]["members"][0]["path"] = "../outside"
        elif change == "encoding":
            x["source"]["skill"]["members"][0]["data_b64"] = "???"
        elif change == "duplicate":
            x["source"]["skill"]["members"].append(x["source"]["skill"]["members"][0])
        with pytest.raises(FleetPlanInvalid):
            decode(canonical_bytes(x))
    with pytest.raises(FleetPlanInvalid, match="identity changed"):
        FrozenAgentStepHandoff.decode_prework_delivery(
            packet, expected_handoff_identity="agent-step-prework-handoff-v1-" + "a" * 64)
    with pytest.raises(FleetPlanInvalid, match="duplicate JSON"):
        decode(packet.replace(b'"schema":', b'"schema":"other","schema":', 1))
    with pytest.raises(FleetPlanInvalid, match="not canonical"):
        decode(packet + b" ")
    from oh_goon.core.models import agent_source
    monkeypatch.setattr(agent_source, "MAX_PREWORK_PACKET_BYTES", len(packet) - 1)
    with pytest.raises(FleetPlanInvalid, match="bounded immutable bytes"):
        decode(packet)
    with pytest.raises(FleetPlanInvalid):
        decode("not-bytes")


def test_ag02b2a_cannot_swap_a_valid_whole_handoff_under_same_binding(tmp_path):
    from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
    resolver, plan, kw, goon, _ = _case(tmp_path)
    first = resolver.prepare_agent_step_handoff(plan, **kw)
    spec = goon / "goon.yml"
    spec.write_text(spec.read_text().replace("review-only", "changed-task"))
    second = resolver.prepare_agent_step_handoff(plan, **kw)
    assert first.handoff_identity != second.handoff_identity
    with pytest.raises(FleetPlanInvalid, match="identity changed"):
        FrozenAgentStepHandoff.decode_prework_delivery(
            second.encode_prework_delivery(), expected_handoff_identity=first.handoff_identity)


def test_ag02b2a_transport_rejects_inconsistent_source_tree_before_encoding(tmp_path):
    from dataclasses import replace
    resolver, plan, kw, _, _ = _case(tmp_path)
    handoff = resolver.prepare_agent_step_handoff(plan, **kw)
    tree = handoff.source.repository
    changed = replace(tree, members=(tree.members[0], tree.members[0]))
    with pytest.raises(FleetPlanInvalid, match="duplicate/unordered"):
        replace(handoff, source=replace(handoff.source, repository=changed)).encode_prework_delivery()
