"""Canonical config diagnostics retain owner codes without unsafe value echo."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from oh_goon.cli import main
from oh_goon.cli_plan_diagnostics import safe_plan_diagnostic
from oh_goon.core.errors import FleetPlanInputRejected, FleetPlanInvalid
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.run_configuration import write_run_configuration


@pytest.mark.parametrize(
    "source,values,code,names",
    [
        (
            "python/quality",
            {"typechecker": "PRIVATE_TOKEN_SENTINEL"},
            "INVALID_INPUT_VALUE",
            ["typechecker"],
        ),
        ("python/quality", {"unknown": "PRIVATE_TOKEN_SENTINEL"}, "UNKNOWN_INPUT", ["unknown"]),
        ("oci/publish-image", {}, "MISSING_REQUIRED_INPUT", ["base_registry_hostname"]),
    ],
)
def test_typed_violations_keep_original_codes_without_value_echo(
    tmp_path, monkeypatch, capsys, source, values, code, names
):
    config = write_run_configuration(
        tmp_path, monkeypatch, source={"kind": "catalog-plan", "name": source}, inputs=values
    )
    assert main(["config", "validate", str(config), "--json"]) == 2
    raw = capsys.readouterr().out
    report = json.loads(raw)
    diagnostic = report["diagnostic"]
    assert diagnostic["code"] == code
    assert all(name in diagnostic["inputs"] for name in names)
    assert diagnostic["execution"] == "not_started_by_diagnostic"
    action = diagnostic["safe_next_action"].casefold()
    assert action
    assert all(
        command not in action
        for command in ("run.yml", "config inspect", "config validate", "plan inputs")
    )
    assert report["proof"]["admission"] == "not-started"
    assert "PRIVATE_TOKEN_SENTINEL" not in raw
    assert not config.with_name("goon.lock").exists()


def test_external_error_never_echoes_untrusted_detail(tmp_path, monkeypatch, capsys):
    import oh_goon.cli_configuration as commands

    config = write_run_configuration(tmp_path, monkeypatch)

    def reject(*args, **kwargs):
        raise FleetPlanInvalid("PRIVATE_TOKEN_SENTINEL /private/native/config")

    monkeypatch.setattr(commands, "inspect_run_configuration", reject)
    assert main(["config", "validate", str(config), "--json"]) == 2
    raw = capsys.readouterr().out
    assert json.loads(raw)["code"] == "PLAN_RESOLUTION_REJECTED"
    assert "PRIVATE_TOKEN_SENTINEL" not in raw and "/private/native/config" not in raw


@pytest.mark.parametrize("implementation", ["pi", "opencode", "codex"])
def test_native_inspection_does_not_read_private_native_state_or_launch(
    tmp_path, monkeypatch, capsys, implementation
):
    import subprocess

    config = write_run_configuration(
        tmp_path,
        monkeypatch,
        source={"kind": "catalog-plan", "name": "agents/native-review"},
        inputs={"repo": "local:repository", "skill": "local:review"},
        agent=True,
    )
    selection = yaml.safe_load(config.read_text())
    selection["agent"].pop("model_profile")
    selection["agent"].update(implementation=implementation, profile="isolated-managed")
    config.write_text(yaml.safe_dump(selection))
    private = tmp_path / "private-native-home"
    private.mkdir()
    (private / "auth.json").write_text('{"token": "PRIVATE_NATIVE_SENTINEL"}')
    monkeypatch.setenv("HOME", str(private))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(private))
    original_open = Path.open

    def public_source_only(path, *args, **kwargs):
        assert not path.is_relative_to(private), "inspection read native private state"
        return original_open(path, *args, **kwargs)

    def no_process(*args, **kwargs):
        pytest.fail("configuration inspection launched a native or network process")

    monkeypatch.setattr(Path, "open", public_source_only)
    monkeypatch.setattr(subprocess, "Popen", no_process)
    assert main(["config", "inspect", str(config), "--json"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["proof"]["native_availability"] == "unknown"
    assert report["proof"]["skill_closure"] == "not-captured"
    assert "PRIVATE_NATIVE_SENTINEL" not in output
    assert not config.with_name("goon.lock").exists()


def test_configuration_lineage_distinguishes_root_and_import_named_root(
    tmp_path, monkeypatch, capsys
):
    from oh_goon.runtime import goon_templates

    sources = {
        "parent": """version: 1
inputs: [value]
goons:
- import: general/leaf
  as: leaf
  with: {value: '${{ inputs.value }}'}
""",
        "leaf": """version: 1
inputs:
  value: {type: string}
steps:
- run: echo ok
""",
    }
    catalog = tmp_path / "goons"
    for name, source in sources.items():
        package = catalog / "general" / name
        package.mkdir(parents=True)
        (package / "goon.yml").write_text(source, encoding="utf-8")
        (package / "README.md").write_text(f"# general/{name}\n", encoding="utf-8")
    monkeypatch.setattr(goon_templates, "default_goon_templates_root", lambda: catalog)
    config = write_run_configuration(
        tmp_path,
        monkeypatch,
        plan_content="version: 1\ninputs: [value]\ngoons:\n- import: general/parent\n  as: root\n",
        inputs={"value": "chosen-public-value"},
    )
    assert main(["config", "validate", str(config), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    entries = {entry["scope"]: entry for entry in report["configuration_evidence"]["entries"]}
    assert set(entries) == {None, "root", "root.leaf"}
    assert entries["root"]["binding"]["parent_scope"] is None
    assert entries["root.leaf"]["binding"]["parent_scope"] == "root"
    assert [entry["occurrence"] for entry in report["occurrences"]] == [["root"], ["root", "leaf"]]


def test_unsafe_input_name_and_exception_are_not_serialized():
    error = FleetPlanInputRejected(
        "raw caller value = EXPOSE_ME",
        diagnostic_code="UNKNOWN_INPUT",
        input_names=("valid", "SECRET=value", "x\nlocation"),
    )
    result = safe_plan_diagnostic(error)
    assert result["inputs"] == ["valid"]
    assert "EXPOSE_ME" not in json.dumps(result)
    assert safe_plan_diagnostic(ValueError("private path /tmp/private"))["inputs"] == []


def test_projected_ambiguity_retains_compiler_owner(tmp_path):
    goons = tmp_path / "goons" / "general" / "leaf"
    goons.mkdir(parents=True)
    (goons / "goon.yml").write_text(
        "version: 1\ninputs:\n  owner:\n    type: string\n    default: safe\nsteps:\n- run: echo ok\n",
        encoding="utf-8",
    )
    (goons / "README.md").write_text("leaf", encoding="utf-8")
    plan = tmp_path / "plan.yml"
    plan.write_text("version: 1\ninputs: [owner]\ngoons:\n- general/leaf\n- general/leaf\n")
    with pytest.raises(FleetPlanInputRejected) as error:
        FleetPlanResolver(goon_templates=GoonTemplateStore(tmp_path / "goons")).resolve(plan)
    assert safe_plan_diagnostic(error.value)["code"] == "AMBIGUOUS_PROJECTED_INPUT"


def test_environment_backed_values_are_unobserved_during_inspection(tmp_path, monkeypatch, capsys):
    config = write_run_configuration(
        tmp_path,
        monkeypatch,
        plan_content="""version: 1
goons:
- import: python/typecheck
  with:
    typechecker:
      from:
      - env: PRIVATE_CHECKER_SELECTION
""",
    )
    monkeypatch.setenv("PRIVATE_CHECKER_SELECTION", "PRIVATE_INVALID_VALUE")
    for command, code in (("inspect", 0), ("validate", 2)):
        assert main(["config", command, str(config), "--json"]) == code
        output = capsys.readouterr().out
        report = json.loads(output)
        assert report["status"] == "contract-inspected"
        assert report["diagnostic"]["code"] == "EXTERNAL_INPUT_NOT_OBSERVED"
        assert report["diagnostic"]["inputs"] == ["typechecker"]
        assert "PRIVATE_INVALID_VALUE" not in output
        assert report["proof"]["execution"] == "not-started"
    assert main(["lock", str(config)]) == 2
    assert "PRIVATE_INVALID_VALUE" not in capsys.readouterr().out
    assert not config.with_name("goon.lock").exists()


def test_defined_repository_source_prevents_unneeded_environment_observation(
    tmp_path, monkeypatch, capsys
):
    config = write_run_configuration(
        tmp_path,
        monkeypatch,
        plan_content="""version: 1
goons:
- import: python/typecheck
  with:
    typechecker:
      from:
      - yaml: {path: selected-checker.yml, key: checker}
      - env: PRIVATE_UNUSED_CHECKER
""",
    )
    (tmp_path / "repository" / "selected-checker.yml").write_text("checker: ty\n")
    monkeypatch.setenv("PRIVATE_UNUSED_CHECKER", "PRIVATE_INVALID_VALUE")
    assert main(["config", "validate", str(config), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "inputs-valid"
    assert report["diagnostic"] is None
    assert main(["lock", str(config)]) == 0
    assert config.with_name("goon.lock").is_file()
