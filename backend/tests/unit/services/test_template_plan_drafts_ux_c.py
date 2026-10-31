"""UX-C plans remain in-memory proposals; original Fleet owns every semantic fact."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

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
        "version: 2\nsource: {kind: file-plan, path: plan.yml}\n"
        "target: {repository: .}\n" + result.values_yaml,
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


def initialize_draft(key, output, **kwargs):
    from oh_goon.services.template_plan_drafts import initialize_run_configuration

    return initialize_run_configuration(key, output, draft=True, **kwargs)


@pytest.mark.parametrize("kind", ["plan", "goon"])
def test_reviewer_authoring_preserves_order_companions_and_compiler_contract(tmp_path, kind):
    import yaml

    from tests.factories.agent_review import agent_review_case

    requested = (
        ("consumer", "local:skills/canonical-consumer-contract-review"),
        ("authority", "local:skills/single-source-of-truth-review"),
    )
    result = initialize_draft(
        "agents/agentic-review-repair-loop",
        tmp_path / "workflow",
        reviewers=requested,
        kind=kind,
    )
    output = result.path.parent
    selected = yaml.safe_load(result.path.read_text())
    assert selected["source"] == {"kind": "file-plan", "path": "plan.yml"}
    assert "target" not in selected
    assert "model_profile" not in selected["agent"]
    assert "write_roots" not in selected["repair"]
    assert {"target.repository", "agent.model_profile", "repair.write_roots"} <= set(
        result.required_fields
    )
    assert result.execution_digest is None and not (output / "goon.lock").exists()
    authored = yaml.safe_load((output / "goons/agents/review-repair/goon.yml").read_text())
    assert [(edge["as"], edge["with"]["skill"]) for edge in authored["goons"][:2]] == list(
        requested
    )
    original = yaml.safe_load(
        (
            PlanTemplateStore().root.parent / "goons/agents/agentic-review-repair-loop/goon.yml"
        ).read_text()
    )
    assert authored["goons"][2:] == [
        edge for edge in original["goons"] if edge.get("phase") != "review"
    ]
    assert not (output / ".goonskills.yml").exists()
    for relative in ("skills/adjudicate/SKILL.md", "skills/repair/SKILL.md"):
        assert (output / relative).is_file()
    # The generated authored sources still pass the original real phased resolver.
    resolver, _, _, options = agent_review_case(tmp_path / "fixture")
    resolved, _, _ = resolver.resolve_frozen_agents(output / "plan.yml", **options)
    from oh_goon.core.agent_review_plan import agent_review_phases

    assert [len(phase) for phase in agent_review_phases(resolved).values()] == [2, 1, 2, 1, 1]
    assert [item.import_id for item in resolved.imports if item.phase == "review"] == [
        "agents-review-repair.consumer",
        "agents-review-repair.authority",
    ]


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
    output = tmp_path / "workflow"
    with pytest.raises(FleetPlanInvalid):
        initialize_draft(
            "agents/agentic-review-repair-loop", output, reviewers=reviewers, kind="plan"
        )
    assert not output.exists()


def test_reviewer_authoring_refuses_other_plans_and_existing_destination(tmp_path):
    selected = (("review", "local:skills/review"),)
    with pytest.raises(FleetPlanInvalid, match="Reviewer authoring"):
        initialize_draft("python/quality", tmp_path / "new", reviewers=selected, kind="plan")
    output = tmp_path / "existing"
    output.mkdir()
    (output / "user.lock").write_text("preserve")
    with pytest.raises(FleetPlanInvalid, match="already exists"):
        initialize_draft(
            "agents/agentic-review-repair-loop", output, reviewers=selected, kind="plan"
        )
    assert (output / "user.lock").read_text() == "preserve"


@pytest.mark.parametrize(
    "key,native,repair",
    [
        ("agents/native-review", True, False),
        ("agents/native-coding", True, False),
        ("agents/agentic-review-repair-loop", False, True),
    ],
)
@pytest.mark.parametrize("kind", ["plan", "goon"])
def test_draft_agent_defaults_follow_compiled_harness_without_fabricating_required_values(
    tmp_path, key, native, repair, kind
):
    import yaml

    result = initialize_draft(key, tmp_path / "workflow", kind=kind)
    selected = yaml.safe_load(result.path.read_text())
    assert selected["source"]["kind"] == ("catalog-plan" if kind == "plan" else "file-plan")
    assert (selected["agent"].get("implementation") == "codex") is native
    assert (selected["agent"].get("profile") == "isolated-managed") is native
    assert "model_profile" not in selected["agent"]
    assert ("repair" in selected) is repair
    assert "target.repository" in result.required_fields
    with pytest.raises(FleetPlanInvalid):
        load_run_configuration(result.path)


@pytest.mark.parametrize("kind", ["plan", "goon"])
@pytest.mark.parametrize("acceptance", [None, "native"], ids=["compiled-default", "caller-choice"])
def test_complete_native_draft_preserves_one_acceptance_selection_through_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["plan", "goon"],
    acceptance: str | None,
) -> None:
    import argparse

    import yaml

    from oh_goon.cli_configuration import prepare_configuration
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
    from oh_goon.services.run_configuration_inspection import inspect_run_configuration
    from oh_goon.services.template_plan_drafts import initialize_run_configuration

    repository = tmp_path / "target"
    repository.mkdir()
    league = tmp_path / "league.yml"
    league.write_text(yaml.safe_dump({"version": 1, "folders": [str(repository)]}))
    initial_league = league.read_bytes()
    database = tmp_path / "state.sqlite"
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(league))
    monkeypatch.setenv("OH_GOON_DB", str(database))
    inputs = {"repo": "local:target", "skill": "local:skills/review"}
    if acceptance is not None:
        inputs["acceptance"] = acceptance
    result = initialize_run_configuration(
        "agents/native-review",
        tmp_path / "workflow",
        kind=kind,
        repositories=(repository,),
        inputs=inputs,
        draft=True,
    )
    selected = yaml.safe_load(result.path.read_text())
    expected = acceptance or "agentic-review"
    assert selected["agent"]["acceptance"] == expected
    assert "acceptance" not in selected["inputs"]
    assert result.required_fields == ()
    snapshot = load_run_configuration(result.path)
    assert snapshot.inputs["acceptance"] == expected
    args = argparse.Namespace(
        command="workflow",
        workflow_command="prepare",
        configuration=result.path,
        register_target=False,
    )
    prepare_configuration(args, snapshot=snapshot)
    report = inspect_run_configuration(
        args._run_configuration, FleetPlanResolver(), source_graph=args._source_graph
    )
    assert args._run_configuration.inputs["acceptance"] == expected
    assert report["status"] == "inputs-valid" and report["diagnostic"] is None
    assert report["proof"]["admission"] == "not-started"
    assert league.read_bytes() == initial_league
    assert not database.exists() and not result.path.with_name("goon.lock").exists()


@pytest.mark.parametrize(
    "native_invalid", (False, True), ids=("ready-control", "owned-native-argv")
)
def test_ready_initializer_rejects_native_arguments_before_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    native_invalid: bool,
) -> None:
    from unittest.mock import Mock

    from oh_goon import cli_ai
    from oh_goon.application import ApplicationRuntime
    from oh_goon.cli import main
    from oh_goon.engine.git import GitService
    from oh_goon.runtime.agent_providers import load_agent_providers
    from oh_goon.runtime.league_folders import LeagueFolderStore
    from oh_goon.services.agent_authoring_sources import capture_agent_sources
    from oh_goon.services.execution_lock_persistence import (
        load_execution_lock,
        write_execution_lock,
    )

    repository = tmp_path / "target"
    repository.mkdir()
    assert GitService().run("init", "-q", str(repository)).returncode == 0
    league = tmp_path / "league.yml"
    database = tmp_path / "state.sqlite"
    providers = tmp_path / "providers.json"
    providers.write_text(
        json.dumps(
            {
                "native_agents": {
                    "codex": {
                        "executable": str(tmp_path / "unavailable-codex"),
                        "arguments": ["--remote=PRIVATE_NATIVE_CONTROL"],
                    }
                }
            }
        )
    )
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(league))
    monkeypatch.setenv("OH_GOON_DB", str(database))
    monkeypatch.setenv("OH_GOON_PROVIDERS_FILE", str(providers))
    build_registration = Mock(wraps=ApplicationRuntime.build_registration_service)
    load = Mock(wraps=load_agent_providers)
    capture = Mock(wraps=capture_agent_sources)
    publish = Mock(wraps=write_execution_lock)
    monkeypatch.setattr(ApplicationRuntime, "build_registration_service", build_registration)
    monkeypatch.setattr(cli_ai, "load_agent_providers", load)
    monkeypatch.setattr("oh_goon.services.agent_authoring_sources.capture_agent_sources", capture)
    monkeypatch.setattr("oh_goon.services.execution_lock_persistence.write_execution_lock", publish)
    output = tmp_path / "workflow"
    arguments = [
        "init",
        "--template",
        "agents/native-review" if native_invalid else "python/quality",
        "--repository",
        str(repository),
        "--output",
        str(output),
        "--no-input",
        "--json",
    ]
    if native_invalid:
        arguments += ["--input", "repo=local:target", "--input", "skill=local:skills/review"]
    assert main(arguments) == (2 if native_invalid else 0)
    rendered = capsys.readouterr()
    report = json.loads(rendered.out)
    assert "PRIVATE_NATIVE_CONTROL" not in rendered.out + rendered.err
    if native_invalid:
        load.assert_called_once_with(providers)
        build_registration.assert_not_called()
        capture.assert_not_called()
        publish.assert_not_called()
        assert report["publication"] == "not_published"
        assert "target_registration" not in report
        assert not league.exists() and not database.exists() and not output.exists()
    else:
        load.assert_not_called()
        build_registration.assert_called_once_with()
        publish.assert_called_once()
        assert report["status"] == "ready"
        assert report["target_registration"]["status"] == "registered"
        assert LeagueFolderStore(league).list() == (repository,)
        assert database.exists()
        assert load_execution_lock(output / "goon.lock").digest == report["execution_digest"]
    assert not list(tmp_path.glob(".oh-goon-init-*"))


def test_agent_draft_copies_exact_bundled_companions_and_missing_bundle_refuses(
    tmp_path, monkeypatch
):
    from oh_goon.services import template_plan_drafts
    from oh_goon.services.agent_skill_registry import read_goonskills

    result = initialize_draft(
        "agents/agentic-review-repair-loop", tmp_path / "workflow", kind="plan"
    )
    example = (
        Path(__file__).resolve().parents[4] / "examples" / template_plan_drafts.AGENT_REVIEW_EXAMPLE
    )
    for relative in template_plan_drafts.AGENT_REVIEW_COMPANION_PATHS:
        assert (result.path.parent / relative).read_bytes() == (example / relative).read_bytes()
    assert len(read_goonskills(result.path.parent / ".goonskills.yml").aliases) == 4
    read = template_plan_drafts.agent_review_companion_files
    monkeypatch.setattr(
        template_plan_drafts, "agent_review_companion_files", lambda _: read(tmp_path / "absent")
    )
    with pytest.raises(FleetPlanInvalid):
        initialize_draft("agents/agentic-review-repair-loop", tmp_path / "refused", kind="plan")
    assert not (tmp_path / "refused").exists()


def test_infrastructure_draft_retains_original_plan_execution_policy(tmp_path):
    import yaml

    from oh_goon.core.models.fleet_plan_execution_policy import ExecutionPurpose
    from oh_goon.integrations.fleet_plan_compilation import (
        compile_plan_document,
        parse_fleet_yaml_document,
    )

    result = initialize_draft("oci/image", tmp_path / "workflow", kind="goon")
    plan = result.path.parent / "plan.yml"
    document = parse_fleet_yaml_document(plan.read_text(), source=str(plan))
    assert document.payload == {"goons": ["oci/image"]}
    compiled = compile_plan_document(document, source=str(plan))
    assert compiled.execution.purpose is ExecutionPurpose.VERIFICATION
    selected = yaml.safe_load(result.path.read_text())
    assert "inputs" not in selected
    assert "inputs.base_registry_hostname" in result.required_fields


def test_plural_agent_initialization_refuses_before_publishing_a_draft(tmp_path):
    roots = (tmp_path / "one", tmp_path / "two")
    for root in roots:
        root.mkdir()
    output = tmp_path / "workflow"
    with pytest.raises(FleetPlanInvalid, match="single repository"):
        initialize_draft("agents/native-review", output, kind="plan", repositories=roots)
    assert not output.exists()
    assert not tuple(tmp_path.glob(".oh-goon-init-*"))


def test_initialization_analyzes_each_imported_goon_once_and_preserves_root_snapshot(
    tmp_path, monkeypatch
):
    from collections import Counter

    from oh_goon.runtime.goon_templates import GoonTemplateStore
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    reads = Counter()
    original = GoonTemplateStore.source

    def source(store, key):
        reads[key] += 1
        return original(store, key)

    monkeypatch.setattr(GoonTemplateStore, "source", source)
    initialize_draft("python/quality", tmp_path / "workflow", kind="goon")
    assert reads == {"python/quality": 1, "python/ruff": 1, "python/typecheck": 1, "python/test": 1}
    root = PlanTemplateStore().source("python/quality")
    graph = FleetPlanResolver(goon_templates=GoonTemplateStore()).analyze_source(
        root.path, root_snapshot=root
    )
    assert graph.root.source_sha256 == root.sha256
    assert graph.root.compiled == root.compiled
    assert graph.root.step_positions == root.step_positions


@pytest.fixture
def typed_graph(tmp_path):
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    package = tmp_path / "goons/general/typed"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# general/typed\n\nTyped input fixture.\n")
    (package / "goon.yml").write_text(
        "inputs:\n"
        "  text: {type: string}\n"
        "  enabled: {type: boolean, default: true}\n"
        "  count: {type: integer, min: 0, max: 2, default: 1}\n"
        "  optional: {type: string, optional: true}\n"
        "  pick: {type: choice, choices: [a, b], default: a}\n"
        "steps:\n- run: python --version\n"
    )
    plan = tmp_path / "plan.yml"
    plan.write_text("goons:\n- import: general/typed\n  from: goons\n")
    return FleetPlanResolver().analyze_source(plan)


def test_initialization_uses_native_types_defaults_and_explicit_null(typed_graph):
    from oh_goon.services.template_plan_drafts import bind_initialization_inputs

    assert bind_initialization_inputs(
        typed_graph,
        {
            "text": "null=日本語",
            "enabled": "false",
            "count": "0",
            "optional": None,
        },
    ) == {"text": "null=日本語", "enabled": False, "count": 0, "optional": None, "pick": "a"}


@pytest.mark.parametrize(
    "values,code",
    [
        ({"text": "ok", "count": "3"}, "INVALID_INPUT_VALUE"),
        ({"text": "ok", "enabled": "yes"}, "INVALID_INPUT_VALUE"),
        ({"text": None}, "INVALID_INPUT_VALUE"),
        ({"text": "ok", "pick": "c"}, "INVALID_INPUT_VALUE"),
        ({"unknown": "private"}, "UNKNOWN_INPUT"),
        ({}, "MISSING_REQUIRED_INPUT"),
    ],
)
def test_invalid_or_missing_initialization_values_fail_before_prompt(typed_graph, values, code):
    from oh_goon.core.errors import FleetPlanInputRejected
    from oh_goon.services.template_plan_drafts import bind_initialization_inputs

    with pytest.raises(FleetPlanInputRejected) as rejected:
        bind_initialization_inputs(typed_graph, values)
    assert rejected.value.diagnostic_code == code


def test_initialization_prompts_only_compiler_required_names(typed_graph):
    from oh_goon.services.template_plan_drafts import bind_initialization_inputs

    prompted = []

    def prompt(item):
        prompted.append(item.definition.name)
        return "日本語"

    assert bind_initialization_inputs(typed_graph, {}, prompt=prompt)["text"] == "日本語"
    assert prompted == ["text"]
