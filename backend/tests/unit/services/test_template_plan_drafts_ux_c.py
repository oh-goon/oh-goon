"""UX-C plans remain in-memory proposals; original Fleet owns every semantic fact."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oh_goon.cli_plan_input_files import read_caller_input_file
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_compilation import parse_plan_template
from oh_goon.runtime.plan_templates import PlanTemplateStore
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


def test_remove_requires_updated_input_projection(origin):
    with pytest.raises(FleetPlanInvalid):
        draft(origin, edits=[{"op": "remove", "at": 0}])
    result = draft(
        origin, edits=[{"op": "remove", "at": 0}], projected_inputs=["coverage_threshold"]
    )
    assert [s.goon for s in parse_plan_template(result.plan_yaml).steps] == ["python/quality"]


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
        "coverage_threshold": 85,
    }
    result = draft(origin, values=selected)
    assert not result.report["missing_required_inputs"]
    assert result.report["preview_ready"]
    assert "abcdef1234567890abcdef1234567890abcdef12" not in json.dumps(result.report)
    path = tmp_path / "values.yml"
    path.write_text(result.values_yaml, encoding="utf-8")
    snap = read_caller_input_file(path, root=tmp_path)
    assert snap.values() == selected


@pytest.mark.parametrize(
    "values",
    [
        {"mystery": 1},
        {"coverage_threshold": True},
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
        "coverage_threshold": 90,
    }
    result = draft(origin, edits=[{"op": "move", "from": 1, "to": 0}], values=draft_values)
    resolved = result.report["resolved_comparison"]
    assert resolved["status"] == "RESOLVED_NOT_EXECUTED"
    assert resolved["step_order_changed"]
    assert resolved["step_count_before"] == resolved["step_count_after"] == 6
    assert resolved["before"][0]["id"] == "python-release-metadata.compute"
    assert resolved["after"][0]["id"].startswith("python-quality")
    assert all("capabilities_sha256" in s for s in resolved["after"])
    assert "abcdef1234567890abcdef1234567890abcdef12" not in json.dumps(result.report)


def test_values_from_removed_projection_are_rejected(origin):
    with pytest.raises(FleetPlanInvalid, match="unknown caller value"):
        draft(
            origin,
            edits=[{"op": "remove", "at": 0}],
            projected_inputs=["coverage_threshold"],
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
