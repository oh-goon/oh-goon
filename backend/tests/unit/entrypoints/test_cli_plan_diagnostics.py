from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import oh_goon.cli_runtime_plans as plans
from oh_goon.cli_runtime_plans import run_plan_command
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import VerificationExecutionPolicy
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.core.vocabulary import FleetRunOutcome, FleetRunStatus


class Dump(SimpleNamespace):
    def model_dump_json(self, *, indent=None):
        del indent
        return '{"ok":true}'


class Plans:
    def resolve(self, plan, **kwargs):
        return ResolvedFleetPlan.create(
            name="demo",
            description="",
            execution=VerificationExecutionPolicy(),
            steps=(ResolvedCommandStep(timeout_seconds=3600, step_id="one", argv=("true",)),),
        )

    def explain(self, plan, **kwargs):
        entry = SimpleNamespace(
            source="plan.yml",
            step_id="one",
            disposition=SimpleNamespace(value="included"),
            kind="command",
            compiled_step_ids=("one",),
            reason=None,
        )
        value = SimpleNamespace(
            source="plan.yml",
            scope="root",
            namespace=SimpleNamespace(value="env"),
            name="TOKEN",
            origin="host",
            resolved="<host:TOKEN:redacted>",
            declared='{"kind":"host"}',
            contains_host_value=True,
        )
        return Dump(name="demo", plan_id="p1", values=(value,), entries=(entry,))

    def check(self, root, **kwargs):
        return Dump(checked_files=2, valid=True, issues=())

    def diff(self, before, after, **kwargs):
        field = SimpleNamespace(
            field="execution", before={"purpose": "maintenance"}, after={"purpose": "verification"}
        )
        stepfield = SimpleNamespace(field="argv", before=["a"], after=["b"])
        step = SimpleNamespace(
            kind=SimpleNamespace(value="changed"), step_id="one", fields=(stepfield,)
        )
        return Dump(
            before_plan_id="a", after_plan_id="b", changed=True, plan_fields=(field,), steps=(step,)
        )


def _args(command, **kwargs):
    base = dict(
        command="plan", plan_command=command, plan_inputs=[], extensions=None, as_json=False
    )
    base.update(kwargs)
    return Namespace(**base)


def test_plan_diagnostic_cli_human_paths(capsys):
    runtime = SimpleNamespace(plans=Plans())
    assert run_plan_command(runtime, _args("validate", plan="plan.yml")) == 0
    assert "Resolved steps: 1" in capsys.readouterr().out
    assert run_plan_command(runtime, _args("explain", plan="plan.yml")) == 0
    explain_output = capsys.readouterr().out
    assert "included" in explain_output
    assert "Values:" in explain_output
    assert "<host:TOKEN:redacted>" in explain_output
    assert "host-value-redacted=true" in explain_output
    assert run_plan_command(runtime, _args("check", root=".")) == 0
    assert "Plan contracts: valid" in capsys.readouterr().out
    assert run_plan_command(runtime, _args("diff", before="a.yml", after="b.yml")) == 0
    output = capsys.readouterr().out
    assert "execution" in output and "changed: one" in output


def test_plan_run_completed_attention_required_is_nonzero(monkeypatch, capsys):
    detail = SimpleNamespace(
        outcome=FleetRunOutcome.ATTENTION_REQUIRED,
        run=SimpleNamespace(status=FleetRunStatus.COMPLETED),
    )
    admitted = SimpleNamespace(fleet_run_id="run-attention", target=None)
    runtime = SimpleNamespace(
        plans=SimpleNamespace(
            require_cli_plan_preparation=lambda run_id: None,
            run=lambda *args, **kwargs: admitted,
        ),
        runs=SimpleNamespace(),
    )
    monkeypatch.setattr(
        plans,
        "_wait_for_terminal_with_progress",
        lambda runtime, fleet_run_id, *, admitted_run: detail,
    )
    args = Namespace(
        command="plan",
        plan_command="run",
        plan="plan.yml",
        extensions=None,
        repository_ids=None,
        all_repositories=False,
        fleet_run_id=None,
        priority=SchedulingPriority.NORMAL,
        plan_inputs=None,
        lock_file=None,
        wait=True,
        repair_handoff=False,
    )

    assert run_plan_command(runtime, args) == 1
    output = capsys.readouterr().out
    assert "Execution status: COMPLETED" in output
    assert "Outcome: ATTENTION_REQUIRED" in output
