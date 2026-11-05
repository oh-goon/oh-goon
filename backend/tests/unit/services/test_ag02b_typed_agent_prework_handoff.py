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
    (goon / "goon.yml").write_text(
        """inputs:
  repo: {type: repository_reference}
  skill: {type: skill_reference}
  acceptance: {type: acceptance_profile}
steps:
"""
        + step
    )
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
        repository_root=tmp_path / "repos",
        skill_root=tmp_path / "skills",
        allowed_acceptance=("review",),
        expected_source_sha256=sha,
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


@pytest.mark.parametrize(
    "step",
    [
        "- uses: agent\n  harness: opencode\n  task: check\n  run: echo forbidden\n",
        "- uses: agent\n  harness: opencode\n  task: check\n  argv: [sh, -c, id]\n",
        "- uses: agent\n  harness: opencode\n  task: check\n  model_endpoint: https://evil.invalid\n",
        "- uses: agent\n  harness: opencode\n  task: check\n  credentials: secret\n",
        "- uses: agent\n  harness: bad/name\n  task: check\n",
        "- uses: agent\n  harness: opencode\n",
    ],
)
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
    spec.write_text(
        text.replace(
            "- uses: agent\n  harness: opencode\n  task: review-only", "- run: echo command-only"
        )
    )
    with pytest.raises(FleetPlanInvalid, match="exactly one typed agent Step"):
        resolver.prepare_agent_step_handoff(plan, **kw)
    spec.write_text(text + "- uses: agent\n  harness: codex\n  task: other\n")
    with pytest.raises(FleetPlanInvalid, match="exactly one typed agent Step"):
        resolver.prepare_agent_step_handoff(plan, **kw)


def test_handoff_rejects_stale_root_wrong_authority_and_tampered_bytes(tmp_path):
    resolver, plan, kw, _, _ = _case(tmp_path)
    with pytest.raises(FleetPlanInvalid, match="revision changed"):
        resolver.prepare_agent_step_handoff(plan, **{**kw, "expected_source_sha256": "a" * 64})
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
    monkeypatch.setattr(
        os, "open", lambda *a, **k: pytest.fail("transport attempted a source reread")
    )
    received = FrozenAgentStepHandoff.decode_prework_delivery(
        packet, expected_handoff_identity=handoff.handoff_identity
    )
    assert received.handoff_identity == handoff.handoff_identity
    assert received.source.repository.members[0].data == b"private source\n"
    assert received.effect_permission == "NONE"
    assert received.authority == "PREWORK_NOT_ADMITTED"


@pytest.mark.parametrize(
    "private", ("repository-member", "skill-member", "repository-reference", "skill-reference")
)
def test_retained_identity_valid_grader_delivery_cannot_be_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, private: str
) -> None:
    from dataclasses import replace

    from oh_goon.core.models import agent_source
    from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
    from oh_goon.core.product_identity import stable_id

    resolver, plan, options, _, _ = _case(tmp_path)
    safe = resolver.prepare_agent_step_handoff(plan, **options)
    role = "repository" if private.startswith("repository") else "skill"
    if private.endswith("member"):
        tree = getattr(safe.source, role)
        private_member = replace(tree.members[0], path="docs/certification/answers/synthetic.txt")
        members = tuple(sorted((*tree.members, private_member), key=lambda m: m.path))
        private_tree = replace(
            tree,
            members=members,
            content_identity=stable_id(
                "agent-source-closure-v1",
                {
                    "members": [
                        {"path": m.path, "sha256": m.sha256, "bytes": len(m.data)} for m in members
                    ]
                },
            ),
        )
        source = replace(safe.source, **{role: private_tree})
    else:
        field = "repository_reference" if role == "repository" else "resolved_skill_reference"
        source = replace(safe.source, **{field: "local:docs/certification/answers"})
    unsafe = replace(safe, source=source)
    # Construct a fully identity-valid retained delivery under the old privacy
    # rules. The current decoder must reject it even when all digests match.
    with monkeypatch.context() as old_policy:
        old_policy.setattr(agent_source, "is_grader_private_source_path", lambda _path: False)
        packet = unsafe.encode_prework_delivery()
        expected = unsafe.handoff_identity
        assert (
            FrozenAgentStepHandoff.decode_prework_delivery(
                packet, expected_handoff_identity=expected
            ).handoff_identity
            == expected
        )
    with pytest.raises(FleetPlanInvalid, match="grader-only"):
        FrozenAgentStepHandoff.decode_prework_delivery(packet, expected_handoff_identity=expected)
    with pytest.raises(FleetPlanInvalid, match="grader-only"):
        unsafe.verify()


@pytest.mark.parametrize("private_workspace", (False, True), ids=("blind-source", "grader-source"))
def test_native_resolver_requires_current_source_without_grader_namespace_before_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, private_workspace: bool
) -> None:
    import os
    import subprocess
    from typing import Any

    from oh_goon.core.models.agent_providers import (
        AgentConnection,
        AgentModel,
        AgentProvider,
        AgentProvidersConfig,
        NativeAgentDefinition,
        NativeAgentProfile,
        ResolvedNativeAgent,
    )

    resolver, source, options, _, repository = _case(
        tmp_path,
        "- uses: agent\n  harness: native\n  task: Inspect the supplied source\n"
        "  requested_effects:\n    recipients: [configured_model]\n",
    )
    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="native-source-prework",
        definition=NativeAgentDefinition(executable=str(tmp_path / "never-executed-native")),
        profile=NativeAgentProfile(backend="bubblewrap", inference_profile="fixture"),
        executable_sha256="a" * 64,
        connection=AgentConnection.select(
            AgentProvidersConfig(
                default_model="fixture",
                providers={
                    "fixture": AgentProvider(
                        api="openai-chat-completions", base_url="http://127.0.0.1:12345"
                    )
                },
                models={"fixture": AgentModel(provider="fixture", model="fixture")},
            )
        ),
    )
    if private_workspace:
        private = repository / "docs/certification/answers"
        private.mkdir(parents=True)
        (private / "synthetic.json").write_text("SYNTHETIC_GRADER_ONLY_MATERIAL")
    native_stat = os.stat

    def no_private_stat(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        assert str(path) != "answers", "native source privacy entered the grader subtree"
        return native_stat(path, *args, **kwargs)

    def no_process(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("native source prework launched a provider, worker or workload")

    monkeypatch.setattr(os, "stat", no_private_stat)
    monkeypatch.setattr(subprocess, "Popen", no_process)
    options = {key: value for key, value in options.items() if key != "expected_source_sha256"}
    # This is original native resolver prework, with its real workspace binding;
    # it is not installed-harness or filesystem-denial qualification. The scope
    # is the current reserved namespace, not arbitrary copies or Git history.
    if private_workspace:
        with pytest.raises(FleetPlanInvalid, match="Native Agent workspace contains grader-only"):
            resolver.resolve_frozen_agents(
                source, native_agent=selected, repository=repository, **options
            )
    else:
        plan, deliveries, _ = resolver.resolve_frozen_agents(
            source, native_agent=selected, repository=repository, **options
        )
        assert deliveries
        assert all(handoff.harness == "native" for handoff in deliveries.values())
        assert all(
            step.kind == "agent" and step.workload.harness == "native" for step in plan.steps
        )
    assert not (tmp_path / "state.sqlite").exists()


def test_ag02b2a_packet_tampering_and_forged_authority_fail_before_work(tmp_path, monkeypatch):
    import json

    from oh_goon.core.enforcer_lace_trust import canonical_bytes
    from oh_goon.core.models.agent_source import FrozenAgentStepHandoff

    resolver, plan, kw, _, _ = _case(tmp_path)
    handoff = resolver.prepare_agent_step_handoff(plan, **kw)
    packet = handoff.encode_prework_delivery()
    expected = handoff.handoff_identity

    def decode(b):
        return FrozenAgentStepHandoff.decode_prework_delivery(b, expected_handoff_identity=expected)

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
            packet, expected_handoff_identity="agent-step-prework-handoff-v1-" + "a" * 64
        )
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
            second.encode_prework_delivery(), expected_handoff_identity=first.handoff_identity
        )


def test_ag02b2a_transport_rejects_inconsistent_source_tree_before_encoding(tmp_path):
    from dataclasses import replace

    resolver, plan, kw, _, _ = _case(tmp_path)
    handoff = resolver.prepare_agent_step_handoff(plan, **kw)
    tree = handoff.source.repository
    changed = replace(tree, members=(tree.members[0], tree.members[0]))
    with pytest.raises(FleetPlanInvalid, match="duplicate/unordered"):
        replace(
            handoff, source=replace(handoff.source, repository=changed)
        ).encode_prework_delivery()


def test_public_capture_delivery_and_native_evidence_keep_grader_material_out(
    tmp_path: Path,
) -> None:
    import json

    from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
    from oh_goon.integrations.hashmarks import (
        prepare_agent_evidence,
        prompt_observation,
        repository_observation,
    )

    resolver, plan, options, _, repository = _case(tmp_path)
    public_marker = "public_scope_canary_42e8b71"
    private_marker = "grader_scope_canary_763f1da"
    (repository / "public.py").write_text(f'PUBLIC_VALUE = "{public_marker}"\n')
    private = repository / "docs/certification/answers/canary.yml"
    private.parent.mkdir(parents=True)
    private.write_text(f"expected_claims:\n- {private_marker}\n")

    handoff = resolver.prepare_agent_step_handoff(plan, **options)
    received = FrozenAgentStepHandoff.decode_prework_delivery(
        handoff.encode_prework_delivery(), expected_handoff_identity=handoff.handoff_identity
    )
    assert received.handoff_identity == handoff.handoff_identity
    assert all(
        member.path != "docs/certification/answers/canary.yml"
        for member in received.source.repository.members
    )
    with prepare_agent_evidence(received) as evidence:
        assert not (evidence.root / "repo/docs/certification/answers").exists()
        for query in ("expected_claims", public_marker):
            packet = repository_observation(
                evidence.root,
                tool="search_text",
                query=query,
                capture_identity=received.source.capture_identity,
            )
            raw = json.dumps(packet)
            prompt = prompt_observation(
                raw, expected_capture_identity=received.source.capture_identity
            )
            assert private_marker not in raw
            assert private_marker not in prompt
            assert packet["repository_absence"] == "not-claimed"
            if query == public_marker:
                assert any(row["path"] == "public.py" for row in packet["retrieval"]["results"])
                assert public_marker in json.dumps(packet["source"])
                assert public_marker in prompt
