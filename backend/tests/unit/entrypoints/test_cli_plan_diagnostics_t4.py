"""T4 independent CLI tests: evidence codes from original owners, zero value echo."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.cli_parser import build_parser
from oh_goon.cli_plan_diagnostics import safe_plan_diagnostic
from oh_goon.cli_runtime_plans import run_plan_command
from oh_goon.core.errors import FleetPlanInputRejected, FleetPlanInvalid
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def _args(command: str, *rest: str):
    return build_parser().parse_args(["plan", command, *rest])


def _runtime():
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    return SimpleNamespace(
        plans=SimpleNamespace(
            resolve=resolver.resolve,
            explain=resolver.explain,
            explain_routing=resolver.explain_routing,
        )
    )


def _plan(name="python/quality") -> str:
    return str(PlanTemplateStore().source(name).path)


@pytest.mark.parametrize(
    "command,tail",
    [
        ("validate", ()),
        ("explain", ()),
        ("explain", ("--routes",)),
    ],
)
def test_typed_violations_are_machine_safe_and_exit_2(command, tail, capsys):
    args = _args(
        command, _plan(), *tail, "--diagnostics", "--json", "--input", "typechecker=invalid"
    )
    assert run_plan_command(_runtime(), args) == 2
    raw = capsys.readouterr().out
    diag = json.loads(raw)
    assert diag == {
        "schema": "oh-goon.plan-validation-diagnostic/v1",
        "status": "rejected",
        "stage": "input_contract",
        "code": "INVALID_INPUT_VALUE",
        "inputs": ["typechecker"],
        "safe_next_action": "Use plan inputs to inspect the owning Goon's type and constraints; correct the caller value.",
        "execution": "not_started_by_diagnostic",
    }
    assert "101" not in raw
    assert "plan_id" not in raw


def test_unknown_input_exact_name_but_not_supplied_secret(capsys):
    secret = "VERY_SECRET_SENTINEL_123"
    assert (
        run_plan_command(
            _runtime(),
            _args(
                "explain",
                _plan(),
                "--routes",
                "--diagnostics",
                "--json",
                "--input",
                f"unknown={secret}",
            ),
        )
        == 2
    )
    rendered = capsys.readouterr().out
    assert json.loads(rendered)["code"] == "UNKNOWN_INPUT"
    assert json.loads(rendered)["inputs"] == ["unknown"]
    assert secret not in rendered


def test_missing_required_value_reports_public_name_only(capsys):
    assert (
        run_plan_command(
            _runtime(),
            _args("explain", _plan("oci/publish-image"), "--routes", "--diagnostics", "--json"),
        )
        == 2
    )
    output = json.loads(capsys.readouterr().out)
    assert output["code"] == "MISSING_REQUIRED_INPUT"
    assert "base_registry_hostname" in output["inputs"]
    assert output["status"] == "rejected"


def test_external_error_never_echoes_untrusted_details(capsys):
    secret = "PRIVATE_TOKEN=do-not-show"
    runtime = SimpleNamespace(
        plans=SimpleNamespace(
            explain_routing=lambda *a, **kw: (
                (_ for _ in ()).throw(FleetPlanInvalid(f"bad file with {secret}"))
            )
        )
    )
    assert (
        run_plan_command(
            runtime, _args("explain", "missing/plan.yml", "--routes", "--diagnostics", "--json")
        )
        == 2
    )
    raw = capsys.readouterr().out
    assert json.loads(raw)["code"] == "PLAN_RESOLUTION_REJECTED"
    assert secret not in raw and "missing/plan.yml" not in raw


def test_bad_cli_input_never_echoes_secret_in_diagnostics(capsys):
    assert (
        run_plan_command(
            _runtime(),
            _args(
                "explain",
                _plan(),
                "--routes",
                "--json",
                "--diagnostics",
                "--input",
                "SECRET_ONLY_NO_EQUALS",
            ),
        )
        == 2
    )
    raw = capsys.readouterr().out
    assert json.loads(raw)["code"] == "PLAN_RESOLUTION_REJECTED"
    assert "SECRET_ONLY_NO_EQUALS" not in raw


def test_cli_override_is_accepted_and_not_misattributed_to_file(tmp_path, monkeypatch, capsys):
    (tmp_path / "params.yml").write_text("inputs:\n  typechecker: pyright\n")
    monkeypatch.chdir(tmp_path)
    assert (
        run_plan_command(
            _runtime(),
            _args(
                "explain",
                _plan(),
                "--routes",
                "--json",
                "--input",
                "typechecker=ty",
                "--inputs-file",
                "params.yml",
            ),
        )
        == 0
    )
    doc = json.loads(capsys.readouterr().out)
    selected = next(item for item in doc["inputs"] if item["name"] == "typechecker")
    assert selected["origin"] == {"kind": "supplied"}
    assert all("value" not in item for item in doc["bindings"])


@pytest.mark.parametrize("command", ["validate", "explain"])
def test_successful_diagnostics_are_value_free_and_preserve_caller_origins(
    command, tmp_path, monkeypatch, capsys
):
    (tmp_path / "params.yml").write_text(
        "inputs:\n  typechecker: pyright\n  project: private-source-path\n"
    )
    monkeypatch.chdir(tmp_path)
    assert (
        run_plan_command(
            _runtime(),
            _args(
                command,
                _plan(),
                "--diagnostics",
                "--json",
                "--inputs-file",
                "params.yml",
                "--input",
                "typechecker=ty",
            ),
        )
        == 0
    )
    raw = capsys.readouterr().out
    report = json.loads(raw)
    assert report["status"] == "accepted"
    assert report["execution"] == "resolved_not_executed"
    assert len(report["configuration"]["caller_file_sha256"]) == 64
    entries = {
        item["name"]: item for item in report["configuration"]["entries"] if item["scope"] is None
    }
    assert entries["typechecker"]["binding"]["kind"] == "cli"
    assert entries["project"]["binding"]["kind"] == "caller_file"
    assert "private-source-path" not in raw and "pyright" not in raw


def test_real_cli_distinguishes_plan_root_from_import_named_root(tmp_path, monkeypatch, capsys):
    from oh_goon.cli import main
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
    path = tmp_path / "plan.yml"
    path.write_text(
        "version: 1\ninputs: [value]\ngoons:\n- import: general/parent\n  as: root\n",
        encoding="utf-8",
    )
    command = [
        "plan",
        "validate",
        str(path),
        "--diagnostics",
        "--input",
        "value=private-root-scope-canary",
    ]
    assert main(command) == 0
    output = capsys.readouterr().out
    lines = output.splitlines()
    assert any(line.startswith("  <root>.input.value: cli ") for line in lines)
    imported = next(line for line in lines if line.startswith("  root.input.value:"))
    nested = next(line for line in lines if line.startswith("  root.leaf.input.value:"))
    assert " <- <root>.input.value " in imported
    assert " <- root.input.value " in nested
    assert "private-root-scope-canary" not in output

    assert main([*command, "--json"]) == 0
    entries = {
        entry["scope"]: entry
        for entry in json.loads(capsys.readouterr().out)["configuration"]["entries"]
    }
    assert set(entries) == {None, "root", "root.leaf"}
    assert entries["root"]["binding"]["parent_scope"] is None
    assert entries["root.leaf"]["binding"]["parent_scope"] == "root"


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


def test_diagnostic_opt_in_does_not_change_normal_success(capsys):
    args = _args("explain", _plan(), "--routes", "--json")
    assert run_plan_command(_runtime(), args) == 0
    original = json.loads(capsys.readouterr().out)
    args = _args("explain", _plan(), "--routes", "--json", "--diagnostics")
    assert run_plan_command(_runtime(), args) == 0
    modified = json.loads(capsys.readouterr().out)
    assert modified == original
    assert modified["schema"] == "oh-goon.plan-input-routing/v1"


def test_non_opted_in_failure_text_remains_legacy(capsys):
    assert (
        run_plan_command(
            _runtime(),
            _args("explain", _plan(), "--routes", "--json", "--input", "typechecker=invalid"),
        )
        == 2
    )
    raw = capsys.readouterr().out
    assert "must be" in raw and "oh-goon.plan-validation-diagnostic/v1" not in raw


def test_human_opt_in_safe_codes_and_action(capsys):
    assert (
        run_plan_command(
            _runtime(),
            _args("validate", _plan(), "--diagnostics", "--input", "typechecker=invalid"),
        )
        == 2
    )
    output = capsys.readouterr().out
    assert "Plan rejected: INVALID_INPUT_VALUE" in output
    assert "Next action:" in output
    assert "101" not in output


def test_projected_input_ambiguity_uses_original_compiler_owner(tmp_path: Path):
    from oh_goon.runtime.goon_templates import GoonTemplateStore

    goons = tmp_path / "goons" / "general" / "leaf"
    goons.mkdir(parents=True)
    (goons / "goon.yml").write_text(
        "version: 1\ninputs:\n  owner:\n    type: string\n    default: safe\nsteps:\n- run: echo ok\n",
        encoding="utf-8",
    )
    (goons / "README.md").write_text("leaf", encoding="utf-8")
    plan = tmp_path / "plan.yml"
    plan.write_text(
        "version: 1\ninputs: [owner]\ngoons:\n- general/leaf\n- general/leaf\n", encoding="utf-8"
    )
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(tmp_path / "goons"))
    with pytest.raises(FleetPlanInputRejected) as error:
        resolver.resolve(plan)
    doc = safe_plan_diagnostic(error.value)
    assert doc["code"] == "AMBIGUOUS_PROJECTED_INPUT"
    assert doc["inputs"] == ["owner"]
    assert doc["status"] == "rejected"
