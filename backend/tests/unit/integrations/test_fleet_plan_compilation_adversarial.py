from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import FleetExtensionInvalid, FleetPlanInvalid
from oh_goon.integrations import fleet_plan_compilation as c
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def test_yaml_parser_rejects_non_mapping_duplicates_nested_duplicates_and_bad_yaml() -> None:
    for text in ("- x\n", "x: 1\nx: 2\n", "x:\n  y: 1\n  y: 2\n", "x: [1,\n"):
        with pytest.raises(FleetPlanInvalid):
            c.parse_fleet_yaml_document(text, source="x.yml")
    assert c.parse_yaml_mapping("x: 1\n", source="x.yml") == {"x": 1}


def test_loaders_wrap_io_and_catalog_validation_errors(tmp_path: Path) -> None:
    with pytest.raises(FleetPlanInvalid, match="Cannot read YAML"):
        c.load_plan_template(tmp_path / "missing.yml")
    with pytest.raises(FleetPlanInvalid, match="Cannot read YAML"):
        c.load_yaml_mapping(tmp_path / "missing.yml")
    bad = tmp_path / "fleet.toml"
    bad.write_text("[", encoding="utf-8")
    with pytest.raises(FleetExtensionInvalid, match="Cannot read"):
        c.load_extension_catalog(bad)
    bad.write_text("version=1\n", encoding="utf-8")
    with pytest.raises(FleetExtensionInvalid, match="Invalid"):
        c.load_extension_catalog(bad)


def test_goon_top_level_authoring_rejects_ambiguous_or_redundant_forms() -> None:
    invalid = [
        "version: 1\nname: x\ndescription: x\n",
        "version: 1\nname: x\ndescription: x\nsteps: []\ngoons: [python/ruff]\n",
        "version: 1\nname: x\ndescription: x\nrequirements: []\nsteps: [{run: echo hi}]\n",
        "version: 1\nname: x\ndescription: x\nrequirements: x\nsteps: [{run: echo hi}]\n",
        "version: 1\nname: x\ndescription: x\nunknown: 1\nsteps: [{run: echo hi}]\n",
        "version: 1\nname: x\ndescription: x\ninputs: [a]\nsteps: [{run: echo hi}]\n",
        "version: 1\nname: x\ndescription: x\ninputs: {}\ngoons: [python/ruff]\n",
        "version: 1\nname: x\ndescription: x\ninputs: []\ngoons: [python/ruff]\n",
        "version: 1\nname: x\ndescription: x\ninputs: [a, '']\ngoons: [python/ruff]\n",
        "version: 1\nname: x\ndescription: x\nuv: {group: dev}\ngoons: [python/ruff]\n",
        "version: 1\nname: x\ndescription: x\n"
        "requirements: [{kind: path, path: pyproject.toml}]\n"
        "goons: [python/ruff]\n",
    ]
    for text in invalid:
        with pytest.raises(FleetPlanInvalid):
            c.parse_goon_template(text, source="x.yml")


def test_plan_authoring_rejects_execution_fields_unknowns_missing_goons_and_bad_inputs() -> None:
    for field in ("steps", "run", "command", "script", "imports"):
        text = f"version: 1\n{field}: []\ngoons: [python/ruff]\n"
        with pytest.raises(FleetPlanInvalid, match="Plans cannot declare executable work"):
            c.parse_plan_template(text)
    invalid = [
        "version: 1\nunknown: 1\ngoons: [python/ruff]\n",
        "version: 1\n",
        "version: 1\ninputs: []\ngoons: [python/ruff]\n",
        "version: 1\ninputs: [a, '']\ngoons: [python/ruff]\n",
    ]
    for text in invalid:
        with pytest.raises(FleetPlanInvalid):
            c.parse_plan_template(text)


def test_execution_defaults_to_verification_and_preserves_explicit_strict_policy() -> None:
    ordinary = c.parse_plan_template("version: 1\ngoons: [python/ruff]\n")
    assert ordinary.execution.purpose.value == "verification"
    goon = c.parse_goon_template("version: 1\nsteps: [{run: echo hi}]\n", source="x.yml")
    assert goon.execution.purpose.value == "verification"
    for trusted in (True, False):
        strict = c.parse_plan_template(
            "version: 1\nexecution:\n  purpose: cicd\n"
            f"  trust_repository_code: {str(trusted).lower()}\ngoons: [python/ruff]\n"
        )
        assert strict.execution.purpose.value == "cicd"
        assert strict.execution.trust_repository_code is trusted


def test_goon_import_compiler_validates_shapes_fields_bindings_and_duplicate_occurrences() -> None:
    with pytest.raises(FleetPlanInvalid, match="non-empty"):
        c._compile_goon_imports([], source="x", owner="Plan")
    with pytest.raises(FleetPlanInvalid, match="item 1"):
        c._compile_goon_imports([1], source="x", owner="Plan")
    with pytest.raises(FleetPlanInvalid, match="unsupported fields"):
        c._compile_goon_imports([{"import": "python/ruff", "extra": 1}], source="x", owner="Plan")
    with pytest.raises(FleetPlanInvalid, match="with must be a mapping"):
        c._compile_goon_imports([{"import": "python/ruff", "with": []}], source="x", owner="Plan")
    with pytest.raises(FleetPlanInvalid):
        c._compile_goon_imports(["bad"], source="x", owner="Plan")
    rows = c._compile_goon_imports(
        ["python/ruff", {"import": "python/ruff", "failure_impact": "continue", "with": {"x": 1}}],
        source="x",
        owner="Plan",
    )
    assert rows[0]["step_id"] != rows[1]["step_id"]
    assert rows[1]["failure_impact"] == "continue" and rows[1]["inputs"][0]["value"] == 1


def test_input_definition_binding_source_env_and_output_helpers_fail_closed() -> None:
    assert c._published_output_payload("dist/a.whl") == {"path": "dist/a.whl"}
    assert (
        c._published_output_payload({"path": "x", "retention": "evidence"})["retention"]
        == "evidence"
    )
    with pytest.raises(FleetPlanInvalid):
        c._published_output_payload(1)
    assert c._input_definition_payload(None) == {"required": True}
    assert c._input_definition_payload("x") == {"default": "x"}
    assert c._input_definition_payload({"optional": True}) == {"required": False}
    assert c._input_definition_payload({"type": "integer", "min": 1, "max": 2}) == {
        "input_type": "integer",
        "minimum": 1,
        "maximum": 2,
        "required": True,
    }
    for value in ({"required": True}, {"optional": "yes"}, {"optional": True, "default": 1}, []):
        with pytest.raises(FleetPlanInvalid):
            c._input_definition_payload(value)
    with pytest.raises(FleetPlanInvalid):
        c._input_binding_payload("", 1)
    assert c._input_binding_payload("x", 1) == {"name": "x", "value": 1}
    with pytest.raises(FleetPlanInvalid, match="at least one"):
        c._input_binding_payload("x", {"from": []})
    expected = {
        "env": {"kind": "env", "name": "TOKEN"},
        "yaml": {"kind": "yaml", "path": "x.yml", "key": "a.b"},
        "toml": {"kind": "toml", "path": "x.toml", "key": "a.b"},
        "text": {"kind": "text", "path": "x.txt"},
        "git": {"kind": "git", "field": "commit"},
    }
    specs = {
        "env": "TOKEN",
        "yaml": {"path": "x.yml", "key": "a.b"},
        "toml": {"path": "x.toml", "key": "a.b"},
        "text": {"path": "x.txt"},
        "git": "commit",
    }
    for key, spec in specs.items():
        assert c._input_source_payload({key: spec}, name="x") == expected[key]
    for value in (
        {},
        {"env": ""},
        {"yaml": {"path": "x"}},
        {"toml": []},
        {"text": {"path": "x", "extra": 1}},
        {"bad": 1},
    ):
        with pytest.raises(FleetPlanInvalid):
            c._input_source_payload(value, name="x")
    assert c._env_source_payload({"from": "host"}) == {"kind": "host"}
    assert c._env_source_payload({"value": True}) == {"kind": "literal", "value": True}
    for value in ("x", {"from": "other"}, {"value": []}):
        with pytest.raises(FleetPlanInvalid):
            c._env_source_payload(value)
    assert c._module_output_source("${{ steps.a.outputs.x }}").startswith("${{")
    assert c._module_output_source({"from": "x"}) == "x"
    with pytest.raises(FleetPlanInvalid):
        c._module_output_source({"from": 1})


def test_authored_step_validation_rejects_internal_unknown_native_git_and_timeout() -> None:
    cases = [
        [{"argv": ["x"]}],
        [{"uses": "unknown"}],
        [{"uses": "git", "operation": "native"}],
        [{"run": "echo hi", "timeout_seconds": 1}],
        [{"run": "echo hi", "capabilities": {"tools": ["echo"]}}],
    ]
    for steps in cases:
        with pytest.raises(FleetPlanInvalid):
            c._validate_authored_steps(steps, source="x")
    c._validate_authored_steps(["x", {"uses": "git", "args": ["status"]}], source="x")


def test_run_parser_rejects_shell_programs_punctuation_substitution_multiline_and_bad_quotes() -> (
    None
):
    for value in (
        None,
        "",
        "echo\nhi",
        "echo $(id)",
        "echo `id`",
        "echo x | cat",
        "echo x && true",
        "python -c 'print(1)'",
        "uv run python -c 'print(1)'",
        "'unterminated",
    ):
        with pytest.raises(FleetPlanInvalid):
            c._parse_run_argv(value)
    assert c._parse_run_argv("uv run ruff check '${{ inputs.path }}'") == [
        "uv",
        "run",
        "ruff",
        "check",
        "${{ inputs.path }}",
    ]
    assert c._parse_run_argv('echo "python -c"') == ["echo", "python -c"]


def test_uv_authoring_injects_locked_group_and_rejects_ambiguous_environment(tmp_path) -> None:
    path = tmp_path / "goon.yml"
    path.write_text(
        "version: 1\nuv: {group: dev}\nsteps:\n- run: uv run release-metadata --project demo --group app\n"
    )
    plan = FleetPlanResolver().resolve(path)
    step = plan.steps[0]
    assert step.argv == (
        "uv",
        "run",
        "--frozen",
        "--no-default-groups",
        "--project",
        ".",
        "--group",
        "dev",
        "release-metadata",
        "--project",
        "demo",
        "--group",
        "app",
    )
    assert plan.requirements[0].kind == "uv_project"
    assert plan.requirements[0].group == "dev"
    for body in (
        "uv: []\nsteps: [{run: 'uv run pytest'}]",
        "uv: {group: 'bad space'}\nsteps: [{run: 'uv run pytest'}]",
        "uv: {group: dev}\nsteps: []",
        "uv: {group: dev}\nsteps: [{uses: git, args: [status]}]",
        "uv: {group: dev}\nsteps: [{run: 'uv run pytest', cache: {}}]",
        "uv: {group: dev}\nsteps: [{run: pytest}]",
        "uv: {group: dev}\nsteps: [{run: 'uv run --frozen pytest'}]",
        "uv: {group: dev, project: ../backend}\nsteps: [{run: 'uv run pytest'}]",
    ):
        path.write_text("version: 1\n" + body + "\n")
        with pytest.raises(FleetPlanInvalid):
            FleetPlanResolver().resolve(path)


def test_step_normalization_handles_ids_run_uses_outputs_workspace_and_git() -> None:
    assert c._normalize_step_mapping({"id": "a", "run": "echo hi"})["step_id"] == "a"
    with pytest.raises(FleetPlanInvalid, match="cannot be combined"):
        c._normalize_step_mapping({"run": "echo hi", "kind": "command"})
    with pytest.raises(FleetPlanInvalid, match="disagree"):
        c._normalize_step_mapping({"uses": "git", "kind": "command"})
    out = c._normalize_step_mapping(
        {
            "uses": "git",
            "args": ["status"],
            "outputs": {"x": "x.txt"},
            "workspace": {"from": "repository"},
        }
    )
    assert out["action"]["operation"] == "native" and out["outputs"][0]["name"] == "x"
    assert out["workspace"]["source"] == {"kind": "repository"}
    step = c._normalize_step_mapping({"run": "echo hi", "workspace": {"from": "step", "step": "a"}})
    assert step["workspace"]["source"]["step_id"] == "a"
    invalid = [
        {"run": "echo hi", "workspace": {"source": {"kind": "repository"}, "from": "repository"}},
        {"run": "echo hi", "workspace": {}},
        {"run": "echo hi", "workspace": {"from": "repository", "step": "a"}},
        {"run": "echo hi", "workspace": {"from": "step"}},
        {"run": "echo hi", "workspace": {"from": "other"}},
    ]
    for value in invalid:
        with pytest.raises(FleetPlanInvalid):
            c._normalize_step_mapping(value)


def test_generated_step_ids_deduplicate_bound_and_include_when_requires_mapping() -> None:
    steps, inclusions = c._normalize_steps(
        [
            {"run": "/usr/bin/ruff check ."},
            {"run": "/usr/bin/ruff format .", "include_when": {"input": "x", "equals": True}},
            {"run": "uv run pytest"},
        ]
    )
    assert steps[0]["step_id"] == "ruff" and steps[1]["step_id"] == "ruff-2"
    assert steps[2]["step_id"] == "pytest" and inclusions[0]["step_id"] == "ruff-2"
    with pytest.raises(FleetPlanInvalid, match="include_when must be"):
        c._normalize_steps([{"run": "echo hi", "include_when": True}])
    assert c._generated_step_base({"kind": "git"}, index=1) == "git"
    assert c._generated_step_base({}, index=9) == "step-9"
    assert len(c._generated_step_id({"kind": "x" * 100}, index=1, used=set())) <= 80
    with pytest.raises(FleetPlanInvalid, match="exceeds 80"):
        c.qualified_step_id("x" * 80, "y")
