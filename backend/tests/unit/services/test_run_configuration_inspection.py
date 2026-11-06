"""Current-target observations preserve their captured authority and disclosure limits."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from oh_goon.cli import main
from oh_goon.core.errors import FleetPlanInvalid, FleetPlanSourceStale
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
from oh_goon.integrations.fleet_plan_preview import preview_plan
from oh_goon.services import run_configuration_inspection as subject
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plan_source_graph import FleetPlanSourceGraph
from oh_goon.services.run_configuration import (
    NormalizedRunConfiguration,
    capture_run_configuration_source,
    load_run_configuration,
    resolve_run_configuration_targets,
)


@dataclass(frozen=True)
class InspectionCase:
    configuration: Path
    repository: Path
    plan_path: Path
    resolver: FleetPlanResolver
    normalized: NormalizedRunConfiguration
    graph: FleetPlanSourceGraph


def _case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    goon: dict[str, Any] | None = None,
    inputs: dict[str, str] | None = None,
    strict: bool = False,
    public_inputs: list[str] | None = None,
) -> InspectionCase:
    repository = tmp_path / "repository"
    repository.mkdir()
    preferences = tmp_path / "league.yml"
    preferences.write_text(yaml.safe_dump({"version": 1, "folders": [str(repository)]}))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(preferences))
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "must-not-create.sqlite"))
    root = tmp_path / "workflow"
    package = root / "goons/general/check"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# general/check\n")
    (package / "goon.yml").write_text(
        yaml.safe_dump(
            goon
            or {
                "steps": [
                    {"id": "ready", "run": "true"},
                    {
                        "id": "missing",
                        "run": "true",
                        "when": {"kind": "path_exists", "path": "absent.txt"},
                    },
                    {
                        "id": "dependent",
                        "run": "true",
                        "when": {
                            "kind": "step_status",
                            "step_id": "ready",
                            "statuses": ["COMPLETED"],
                        },
                    },
                ]
            }
        )
    )
    plan = {"goons": [{"import": "general/check", "from": "goons"}]}
    if strict:
        plan["execution"] = {"purpose": "cicd"}
    if public_inputs is not None:
        plan["inputs"] = public_inputs
    plan_path = root / "plan.yml"
    plan_path.write_text(yaml.safe_dump(plan))
    configuration = root / "run.yml"
    selection: dict[str, Any] = {
        "version": 2,
        "source": {"kind": "file-plan", "path": "plan.yml"},
        "target": {"repository": str(repository)},
    }
    if inputs:
        selection["inputs"] = inputs
    configuration.write_text(yaml.safe_dump(selection))
    snapshot = load_run_configuration(configuration)
    targets = resolve_run_configuration_targets(snapshot)
    resolver = FleetPlanResolver()
    normalized, graph = capture_run_configuration_source(
        snapshot, (), resolver, targets=targets, allow_source_materialization=False
    )
    return InspectionCase(configuration, repository, plan_path, resolver, normalized, graph)


def _observe(case: InspectionCase) -> dict[str, Any]:
    return subject.inspect_run_configuration(
        case.normalized, case.resolver, source_graph=case.graph, preview_targets=True
    )


def test_preview_reuses_one_resolved_captured_cut_without_reparsing_current_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path, monkeypatch)
    source_sha256 = hashlib.sha256(case.plan_path.read_bytes()).hexdigest()
    resolve = case.resolver.resolve_source_graph
    resolved: list[ResolvedFleetPlan] = []
    previews = []

    def resolve_captured(graph, **kwargs):
        assert graph is case.graph
        plan = resolve(graph, **kwargs)
        resolved.append(plan)
        return plan

    def observe_then_edit(repository, plan):
        assert plan is resolved[0]
        preview = preview_plan(repository, plan)
        previews.append(preview)
        case.plan_path.write_text(case.plan_path.read_text() + "\n# later source revision\n")
        return preview

    monkeypatch.setattr(case.resolver, "resolve_source_graph", resolve_captured)
    monkeypatch.setattr(
        case.resolver, "analyze_source", lambda *_a, **_k: pytest.fail("captured graph was reread")
    )
    monkeypatch.setattr(subject, "preview_plan", observe_then_edit)
    report = _observe(case)
    assert len(resolved) == len(previews) == 1
    target = report["current_target_preview"]["targets"][0]
    assert target["state"] == "OBSERVED"
    assert target["preview"] == previews[0].model_dump(mode="json")
    assert [step["disposition"] for step in target["preview"]["steps"]] == [
        "READY",
        "SKIPPED",
        "RUNTIME_DEPENDENT",
    ]
    assert report["source"]["sha256"] == source_sha256
    assert hashlib.sha256(case.plan_path.read_bytes()).hexdigest() != source_sha256
    assert report["execution_authorized"] is False
    assert not case.configuration.with_name("goon.lock").exists()
    assert not (tmp_path / "must-not-create.sqlite").exists()


@pytest.mark.parametrize("mutation", ["repository", "configuration"])
def test_preview_refuses_authority_changed_inside_original_repository_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    case = _case(tmp_path, monkeypatch)
    baseline = _observe(case)
    assert baseline["current_target_preview"]["targets"][0]["state"] == "OBSERVED"

    def observe_then_replace(repository, plan):
        preview = preview_plan(repository, plan)
        if mutation == "repository":
            repository.rename(tmp_path / "original-repository")
            repository.mkdir()
        else:
            case.configuration.write_bytes(case.normalized.snapshot.content + b"\n# changed\n")
        return preview

    monkeypatch.setattr(subject, "preview_plan", observe_then_replace)
    error = FleetPlanSourceStale if mutation == "repository" else FleetPlanInvalid
    with pytest.raises(error, match="changed"):
        _observe(case)
    assert not case.configuration.with_name("goon.lock").exists()
    assert not (tmp_path / "must-not-create.sqlite").exists()


def test_plural_preview_rechecks_earlier_target_after_later_target_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path, monkeypatch)
    second = tmp_path / "second-repository"
    second.mkdir()
    selection = yaml.safe_load(case.configuration.read_text())
    selection["target"] = {"repositories": [str(case.repository), str(second)]}
    case.configuration.write_text(yaml.safe_dump(selection))
    (tmp_path / "league.yml").write_text(
        yaml.safe_dump({"version": 1, "folders": [str(case.repository), str(second)]})
    )
    snapshot = load_run_configuration(case.configuration)
    targets = resolve_run_configuration_targets(snapshot)
    selected, graph = capture_run_configuration_source(
        snapshot, (), case.resolver, targets=targets, allow_source_materialization=False
    )
    baseline = subject.inspect_run_configuration(
        selected, case.resolver, source_graph=graph, preview_targets=True
    )
    assert [item["state"] for item in baseline["current_target_preview"]["targets"]] == [
        "OBSERVED",
        "OBSERVED",
    ]

    def observe_later_then_replace_earlier(repository, plan):
        preview = preview_plan(repository, plan)
        if repository == second:
            case.repository.rename(tmp_path / "original-first-repository")
            case.repository.mkdir()
        return preview

    monkeypatch.setattr(subject, "preview_plan", observe_later_then_replace_earlier)
    with pytest.raises(FleetPlanSourceStale, match="changed"):
        subject.inspect_run_configuration(
            selected, case.resolver, source_graph=graph, preview_targets=True
        )
    assert not case.configuration.with_name("goon.lock").exists()
    assert not (tmp_path / "must-not-create.sqlite").exists()


@pytest.mark.parametrize("detail", ["condition", "directory", "retained-cache", "private-default"])
def test_preview_withholds_private_resolved_strings_in_json_and_human_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], detail: str
) -> None:
    canary = "ref-1234567890abcdef1234567890abcdef"
    step: dict[str, Any] = {"id": "check", "run": "true"}
    if detail in {"condition", "private-default"}:
        step["when"] = {"kind": "path_exists", "path": "${{ inputs.private }}"}
    elif detail == "directory":
        step["working_directory"] = "${{ inputs.private }}"
    elif detail == "retained-cache":
        step["cache"] = {"source": {"kind": "retained", "reference_id": "${{ inputs.private }}"}}
    goon = {"inputs": {"private": {"type": "string"}}, "steps": [step]}
    if detail == "private-default":
        goon["inputs"]["private"]["default"] = canary
        goon["inputs"]["public"] = {"type": "boolean", "default": True}
    case = _case(
        tmp_path,
        monkeypatch,
        goon=goon,
        inputs=None if detail == "private-default" else {"private": canary},
        strict=detail == "retained-cache",
        public_inputs=["public"] if detail == "private-default" else None,
    )
    plan = case.resolver.resolve_source_graph(
        case.graph,
        inputs=case.normalized.inputs,
        repository=case.repository,
        observe_environment=False,
    )
    if detail == "directory":
        with pytest.raises(FleetPlanInvalid) as original:
            preview_plan(case.repository, plan)
        assert canary in str(original.value)
        expected_state, expected_disposition = "BLOCKED", None
    else:
        original = preview_plan(case.repository, plan)
        assert canary in original.model_dump_json()
        expected_state = "OBSERVED"
        expected_disposition = "READY" if detail == "retained-cache" else "SKIPPED"
    for arguments in (("--json",), ()):
        capsys.readouterr()
        assert main(["workflow", "preview", str(case.configuration), *arguments]) == 0
        captured = capsys.readouterr()
        assert not captured.err
        assert canary not in captured.out
        if arguments:
            report = json.loads(captured.out)
            if detail == "private-default":
                assert [item["name"] for item in report["inputs"]] == ["public"]
            observation = report["current_target_preview"]
            assert report["execution_authorized"] is False
            assert observation["proof_limits"]["resolved_string_details"] == "REDACTED"
            target = observation["targets"][0]
            assert target["state"] == expected_state
            if expected_disposition is not None:
                assert target["preview"]["steps"][0]["disposition"] == expected_disposition
            else:
                assert target["preview"] is None
                assert target["diagnostic"]["state"] == "BLOCKED"
