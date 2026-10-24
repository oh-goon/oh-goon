"""UX-C plans remain in-memory proposals; original Fleet owns every semantic fact."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_compilation import parse_plan_template
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.run_configuration import load_run_configuration
from oh_goon.services.template_plan_drafts import propose_plan


@pytest.fixture
def origin() -> tuple[str, str]:
    return "python/standard", PlanTemplateStore().source("python/standard").sha256


def draft(origin, **changes):
    key, sha = origin
    return propose_plan(key=key, expected_sha256=sha, **changes)


def test_noop_is_read_only_and_uses_original_occurrences(origin):
    original = PlanTemplateStore().source(origin[0])
    result = draft(origin)
    assert result.report["status"] == "DRAFT_ONLY_NOT_ADMITTED"
    assert result.report["execution_permission"] == "NONE"
    assert result.report["closure_digest"] is None
    assert not result.report["ordering_changed"]
    assert original.content == PlanTemplateStore().source(origin[0]).content
    assert [s.step_id for s in parse_plan_template(result.plan_yaml).steps] == [
        s.step_id for s in original.compiled.steps
    ]
    assert "revision" in result.report["missing_required_inputs"]


def test_move_preserves_normal_compiler_and_references(origin):
    result = draft(origin, edits=[{"op": "move", "from": 1, "to": 0}])
    assert result.report["ordering_changed"]
    assert [s.goon for s in parse_plan_template(result.plan_yaml).steps] == [
        "python/quality",
        "python/release-metadata",
    ]
    assert result.report["before"][0]["goon"] == "python/release-metadata"


def test_remove_updates_automatic_input_projection(origin):
    result = draft(origin, edits=[{"op": "remove", "at": 0}])
    assert [s.goon for s in parse_plan_template(result.plan_yaml).steps] == ["python/quality"]
    assert result.report["missing_required_inputs"] == []


def test_add_repeated_import_with_alias_and_remove(origin):
    result = draft(
        origin,
        edits=[{"op": "add", "at": 2, "import": {"import": "python/ruff", "as": "lint-again"}}],
    )
    assert result.report["after"][-1] == {"id": "lint-again", "goon": "python/ruff"}
    assert len(result.report["after"]) == 3


def test_values_are_canonical_and_validate_with_actual_caller_file_reader(origin, tmp_path: Path):
    selected = {
        "release_channel": "beta",
        "revision": "abcdef1234567890abcdef1234567890abcdef12",
        "typechecker": "ty",
    }
    result = draft(origin, values=selected)
    assert not result.report["missing_required_inputs"]
    assert result.report["preview_ready"]
    assert "abcdef1234567890abcdef1234567890abcdef12" not in json.dumps(result.report)
    path = tmp_path / "run.yml"
    path.write_text(
        "version: 1\nsource: {kind: file-plan, path: plan.yml}\n"
        "target: {repository_id: selected-repository}\n" + result.values_yaml,
        encoding="utf-8",
    )
    snap = load_run_configuration(path)
    assert snap.inputs == selected


@pytest.mark.parametrize(
    "values",
    [
        {"mystery": 1},
        {"typechecker": True},
        {"release_channel": "not-an-option"},
        {"revision": "${{ env.SECRET }}"},
        {"revision": ["malformed"]},
    ],
)
def test_invalid_values_fail_early(origin, values):
    with pytest.raises(FleetPlanInvalid):
        draft(origin, values=values)


@pytest.mark.parametrize(
    "edits",
    [
        [{"op": "remove", "at": 100}],
        [{"op": "remove", "at": True}],
        [{"op": "move", "from": 0, "to": 200}],
        [{"op": "add", "at": 0, "import": "general/hello-world"}],
        [{"op": "add", "at": 0, "import": "unknown/missing"}],
        [{"op": "add", "at": 0, "import": {"import": "python/ruff", "run": "echo surprise"}}],
        [
            {
                "op": "add",
                "at": 0,
                "import": {"import": "python/ruff", "as": "python-release-metadata"},
            }
        ],
        [{"op": "replace", "at": 1, "import": "security/trivy-audit", "shell": "bash"}],
    ],
)
def test_rejected_mutations_do_not_modify_source(origin, edits):
    original = PlanTemplateStore().source(origin[0]).content
    with pytest.raises(FleetPlanInvalid):
        draft(origin, edits=edits)
    assert PlanTemplateStore().source(origin[0]).content == original


def test_stale_revision_fails_before_analyzing_edits(origin):
    with pytest.raises(FleetPlanInvalid, match="stale"):
        propose_plan(
            key=origin[0],
            expected_sha256="0" * 64,
            edits=[{"op": "add", "at": 0, "import": "not-real"}],
        )


def test_repeated_calls_are_deterministic_and_side_effect_free(origin):
    a = draft(origin, values={"release_channel": "beta"})
    b = draft(origin, values={"release_channel": "beta"})
    assert a == b
    assert a.report["original_root_sha256"] == origin[1]
    assert a.report["proposed_root_sha256"] == origin[1]  # unchanged YAML is byte-stable


def test_export_refuses_plan_execution_steps(origin):
    with pytest.raises(FleetPlanInvalid):
        draft(origin, edits=[{"op": "add", "at": 0, "import": {"steps": ["run"]}}])


def test_resolved_compilation_projects_exact_steps_without_effects(origin):
    draft_values = {
        "release_channel": "production",
        "revision": "abcdef1234567890abcdef1234567890abcdef12",
        "typechecker": "pyright",
    }
    result = draft(origin, edits=[{"op": "move", "from": 1, "to": 0}], values=draft_values)
    resolved = result.report["resolved_comparison"]
    assert resolved["status"] == "RESOLVED_NOT_EXECUTED"
    assert resolved["step_order_changed"]
    assert resolved["step_count_before"] == resolved["step_count_after"] == 5
    assert resolved["before"][0]["id"] == "python-release-metadata.compute"
    assert resolved["after"][0]["id"].startswith("python-quality")
    assert all("capabilities_sha256" in s for s in resolved["after"])
    assert "abcdef1234567890abcdef1234567890abcdef12" not in json.dumps(result.report)


def test_values_from_removed_projection_are_rejected(origin):
    with pytest.raises(FleetPlanInvalid, match="unknown caller value"):
        draft(
            origin,
            edits=[{"op": "remove", "at": 0}],
            projected_inputs=["project", "dependency_group", "typechecker"],
            values={"revision": "abcdef1234567890abcdef1234567890abcdef12"},
        )


def test_no_source_write_in_catalog_and_draft_cli(origin, monkeypatch, tmp_path, capsys):
    import sys

    from oh_goon.repository_tooling.template_ux_c_draft import main

    monkeypatch.setattr(sys, "argv", ["ux-c", "--catalog"])
    assert main() == 0
    assert (
        '"template_key": "python/quality"' in capsys.readouterr().out
        or '"template_key": "python/ruff"' in capsys.readouterr().out
    )
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"edits": [{"op": "move", "from": 1, "to": 0}]}))
    monkeypatch.setattr(
        sys,
        "argv",
        ["ux-c", "--plan", origin[0], "--expect-sha256", origin[1], "--request", str(request)],
    )
    assert main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["report"]["ordering_changed"]
    assert result["report"]["execution_permission"] == "NONE"


@pytest.mark.parametrize(
    "key", ["python/standard", "oci/image", "kubernetes/set-image", "oh-goon/self-verification"]
)
def test_real_shipped_domains_use_canonical_source_graph(key):
    source = PlanTemplateStore().source(key)
    result = propose_plan(key=key, expected_sha256=source.sha256)
    assert result.report["before"] == result.report["after"]
    assert result.report["original_root_sha256"] == source.sha256
    assert result.report["execution_permission"] == "NONE"
    assert parse_plan_template(result.plan_yaml).steps == source.compiled.steps


@pytest.mark.parametrize("key", PlanTemplateStore().keys())
def test_all_shipped_plans_generate_canonical_readonly_proposals(key):
    source = PlanTemplateStore().source(key)
    proposal = propose_plan(key=key, expected_sha256=source.sha256)
    assert proposal.report["before"] == proposal.report["after"]
    assert proposal.report["status"] == "DRAFT_ONLY_NOT_ADMITTED"
    assert proposal.report["closure_digest"] is None
    assert parse_plan_template(proposal.plan_yaml).steps == source.compiled.steps


@pytest.mark.parametrize("kind", ["plan", "goon"])
def test_reviewer_authoring_generates_a_normal_ordered_composite(tmp_path, monkeypatch, kind):
    from collections import Counter

    import yaml

    from oh_goon.runtime.goon_templates import GoonTemplateStore
    from oh_goon.services.template_plan_drafts import initialize_run_configuration
    from tests.factories.agent_review import agent_review_case

    reads = Counter()
    read_source = GoonTemplateStore.source

    def source(store, key):
        reads[key] += 1
        return read_source(store, key)

    monkeypatch.setattr(GoonTemplateStore, "source", source)
    output = tmp_path / "workflow"
    requested = (
        ("consumer", "local:skills/canonical-consumer-contract-review"),
        ("authority", "local:skills/single-source-of-truth-review"),
    )
    path = initialize_run_configuration(
        "agents/agentic-review-repair-loop", output, reviewers=requested, kind=kind
    )
    assert reads["agents/agentic-review-repair-loop"] == 1
    assert all(count == 1 for count in reads.values())
    snapshot = load_run_configuration(path)
    assert snapshot.selection.source.kind == "file-plan"
    assert snapshot.selection.agent.implementation is None
    assert snapshot.selection.agent.model_profile == "REQUIRED_MODEL_PROFILE"
    assert snapshot.selection.repair.write_roots == ("REQUIRED_WRITE_ROOT",)
    assert "reviews" not in yaml.safe_load(path.read_text())
    composite = output / "goons/agents/review-repair/goon.yml"
    authored = yaml.safe_load(composite.read_text())
    assert [(edge["as"], edge["with"]["skill"]) for edge in authored["goons"][:2]] == list(
        requested
    )
    original = yaml.safe_load(
        (
            PlanTemplateStore().root.parent / "goons/agents/agentic-review-repair-loop/goon.yml"
        ).read_text()
    )
    assert authored["goons"][2:] == [
        edge for edge in original["goons"] if edge["phase"] != "review"
    ]
    assert not (output / ".goonskills.yml").exists()
    assert not list(output.rglob("SKILL.md"))
    # Real source capture and resolver prove the generated authoring format runs
    # through the existing graph, binder and phased source owners.
    resolver, _, _, options = agent_review_case(tmp_path / "fixture")
    resolved, _, _ = resolver.resolve_frozen_agents(snapshot.source_path(), **options)
    from oh_goon.core.agent_review_plan import agent_review_phases

    phases = agent_review_phases(resolved)
    assert [len(phases[name]) for name in phases] == [2, 1, 2, 1, 1]
    assert [item.import_id for item in resolved.imports if item.phase == "review"] == [
        "agents-review-repair.consumer",
        "agents-review-repair.authority",
    ]
    assert resolver.analyze_source(snapshot.source_path()).has_agent_source_contract()


@pytest.mark.parametrize(
    "reviewers",
    [
        (("", "local:skills/review"),),
        (("same", "local:skills/review"), ("same", "local:skills/other")),
        (("../escape", "local:skills/review"),),
        (("review", ""),),
        (("review", "../outside"),),
        (("agents-adjudicate", "local:skills/review"),),
    ],
)
def test_invalid_reviewer_intent_fails_before_publication(tmp_path, reviewers):
    from oh_goon.services.template_plan_drafts import initialize_run_configuration

    output = tmp_path / "workflow"
    with pytest.raises(FleetPlanInvalid):
        initialize_run_configuration(
            "agents/agentic-review-repair-loop", output, reviewers=reviewers, kind="plan"
        )
    assert not output.exists()


def test_reviewer_authoring_refuses_other_plans_and_any_existing_destination(tmp_path):
    from oh_goon.services.template_plan_drafts import initialize_run_configuration

    selected = (("review", "local:skills/review"),)
    with pytest.raises(FleetPlanInvalid, match="Reviewer authoring"):
        initialize_run_configuration(
            "python/quality", tmp_path / "new", reviewers=selected, kind="plan"
        )
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(FleetPlanInvalid, match="already exists"):
        initialize_run_configuration(
            "agents/agentic-review-repair-loop", output, reviewers=selected, kind="plan"
        )
    assert list(output.iterdir()) == []


def test_failed_companion_publication_has_no_run_selection_and_keeps_foreign_files(
    tmp_path, monkeypatch
):
    import os

    from oh_goon.services.template_plan_drafts import initialize_run_configuration

    output = tmp_path / "workflow"
    link = os.link

    def conflict(source, target, **kwargs):
        if target == "goon.yml":
            (output / "goons/agents/review-repair/goon.yml").write_text(
                "concurrently authored user data\n"
            )
        return link(source, target, **kwargs)

    monkeypatch.setattr(os, "link", conflict)
    with pytest.raises(FileExistsError):
        initialize_run_configuration(
            "agents/agentic-review-repair-loop",
            output,
            reviewers=(("review", "local:skills/review"),),
            kind="plan",
        )
    assert not (output / "run.yml").exists()
    assert (output / "goons/agents/review-repair/goon.yml").read_text() == (
        "concurrently authored user data\n"
    )
    assert not list(tmp_path.glob(".workflow.*"))


@pytest.mark.parametrize(
    ("key", "native", "repair"),
    [
        ("agents/native-review", True, False),
        ("agents/native-coding", True, False),
        ("agents/agentic-review-repair-loop", False, True),
    ],
)
@pytest.mark.parametrize("kind", ["plan", "goon"])
def test_default_agent_selections_follow_the_compiled_harness_and_phases(
    tmp_path, key, native, repair, kind
):
    from oh_goon.services.template_plan_drafts import initialize_run_configuration

    path = initialize_run_configuration(key, tmp_path / "workflow", kind=kind)
    selected = load_run_configuration(path).selection
    assert selected.source.kind == ("catalog-plan" if kind == "plan" else "file-plan")
    assert (selected.agent.implementation == "pi") is native
    assert (selected.agent.model_profile == "REQUIRED_MODEL_PROFILE") is not native
    assert (selected.repair is not None) is repair
    assert sorted(item.name for item in path.parent.iterdir()) == (
        ["run.yml"] if kind == "plan" else ["plan.yml", "run.yml"]
    )


def test_infrastructure_goon_initialization_leaves_policy_with_the_authored_plan(tmp_path):
    from oh_goon.core.models.fleet_plan_execution_policy import ExecutionPurpose
    from oh_goon.integrations.fleet_plan_compilation import (
        compile_plan_document,
        parse_fleet_yaml_document,
    )
    from oh_goon.services.template_plan_drafts import initialize_run_configuration

    path = initialize_run_configuration("oci/image", tmp_path / "workflow", kind="goon")
    plan_path = path.parent / "plan.yml"
    document = parse_fleet_yaml_document(plan_path.read_text(), source=str(plan_path))
    assert document.payload == {"goons": ["oci/image"]}
    compiled = compile_plan_document(document, source=str(plan_path))
    assert compiled.execution is not None
    assert compiled.execution.purpose is ExecutionPurpose.VERIFICATION
    selected = load_run_configuration(path).selection
    assert selected.source.kind == "file-plan"
    assert dict(selected.inputs) == {"base_registry_hostname": "REQUIRED_base_registry_hostname"}
    assert selected.agent is None and selected.repair is None
    assert sorted(item.name for item in path.parent.iterdir()) == ["plan.yml", "run.yml"]


def test_goon_initialization_reads_each_imported_catalog_source_once(tmp_path, monkeypatch):
    from collections import Counter

    from oh_goon.runtime.goon_templates import GoonTemplateStore
    from oh_goon.services.template_plan_drafts import initialize_run_configuration

    reads = Counter()
    original = GoonTemplateStore.source

    def source(store, key):
        reads[key] += 1
        return original(store, key)

    monkeypatch.setattr(GoonTemplateStore, "source", source)
    initialize_run_configuration("python/quality", tmp_path / "workflow", kind="goon")
    assert reads == {"python/quality": 1, "python/ruff": 1, "python/typecheck": 1, "python/test": 1}


def test_parent_retarget_cannot_redirect_workflow_publication(tmp_path, monkeypatch):
    import tempfile

    from oh_goon.services.template_plan_drafts import initialize_run_configuration

    selected_parent = tmp_path / "selected"
    selected_parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    original = tempfile.TemporaryDirectory

    def retarget(*args, **kwargs):
        selected_parent.rename(tmp_path / "original-parent")
        selected_parent.symlink_to(outside, target_is_directory=True)
        return original(*args, **kwargs)

    monkeypatch.setattr(tempfile, "TemporaryDirectory", retarget)
    with pytest.raises(OSError):
        initialize_run_configuration("python/quality", selected_parent / "workflow", kind="plan")
    assert not list(outside.iterdir())
    assert not list((tmp_path / "original-parent").iterdir())


def test_parent_retarget_after_pinning_refuses_completion_and_never_follows_symlink(
    tmp_path, monkeypatch
):
    import os

    from oh_goon.services.template_plan_drafts import initialize_run_configuration

    selected_parent = tmp_path / "selected"
    selected_parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = os.link

    def retarget(source, target, **kwargs):
        if target == "plan.yml":
            selected_parent.rename(tmp_path / "original-parent")
            selected_parent.symlink_to(outside, target_is_directory=True)
        return linked(source, target, **kwargs)

    monkeypatch.setattr(os, "link", retarget)
    with pytest.raises(OSError, match="directory changed"):
        initialize_run_configuration(
            "agents/agentic-review-repair-loop",
            selected_parent / "workflow",
            reviewers=(("review", "local:skills/review"),),
            kind="plan",
        )
    assert not list(outside.iterdir())
    assert not (tmp_path / "original-parent/workflow/run.yml").exists()
