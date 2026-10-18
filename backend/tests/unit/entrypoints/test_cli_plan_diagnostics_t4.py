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
        command, _plan(), *tail, "--diagnostics", "--json", "--input", "coverage_threshold=101"
    )
    assert run_plan_command(_runtime(), args) == 2
    raw = capsys.readouterr().out
    diag = json.loads(raw)
    assert diag == {
        "schema": "oh-goon.plan-validation-diagnostic/v1",
        "status": "rejected",
        "stage": "input_contract",
        "code": "INVALID_INPUT_VALUE",
        "inputs": ["coverage_threshold"],
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


def test_conflicting_cli_file_source_has_typed_code_and_no_values(
    tmp_path: Path, monkeypatch, capsys
):
    (tmp_path / "params.yml").write_text("inputs:\n  coverage_threshold: 90\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
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
                "coverage_threshold=80",
                "--inputs-file",
                "params.yml",
            ),
        )
        == 2
    )
    doc = json.loads(capsys.readouterr().out)
    assert doc["code"] == "CONFLICTING_INPUT_SOURCES"
    assert doc["inputs"] == ["coverage_threshold"]
    assert "80" not in json.dumps(doc) and "90" not in json.dumps(doc)


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
            _args("explain", _plan(), "--routes", "--json", "--input", "coverage_threshold=101"),
        )
        == 2
    )
    raw = capsys.readouterr().out
    assert "must be" in raw and "oh-goon.plan-validation-diagnostic/v1" not in raw


def test_human_opt_in_safe_codes_and_action(capsys):
    assert (
        run_plan_command(
            _runtime(),
            _args("validate", _plan(), "--diagnostics", "--input", "coverage_threshold=101"),
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
