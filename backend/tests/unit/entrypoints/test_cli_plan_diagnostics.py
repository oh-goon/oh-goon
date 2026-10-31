from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import oh_goon.cli_runtime_plans as plans
from oh_goon.cli_parser import build_parser
from oh_goon.cli_plan_summary import resolved_plan_summary
from oh_goon.cli_runtime_plans import run_plan_command
from oh_goon.core.models.fleet_plan import (
    MissingConfigPolicy,
    ResolvedCommandStep,
    ResolvedFleetPlan,
    ResolvedPreCommitStep,
)
from oh_goon.core.models.fleet_plan_execution_policy import VerificationExecutionPolicy
from oh_goon.core.models.fleet_plan_step_primitives import PlanStreamOutput
from oh_goon.core.models.game_tape import GameTapeSummary
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
    assert run_plan_command(runtime, _args("check", root=".")) == 0
    assert "Plan contracts: valid" in capsys.readouterr().out
    assert run_plan_command(runtime, _args("diff", before="a.yml", after="b.yml")) == 0
    output = capsys.readouterr().out
    assert "execution" in output and "changed: one" in output


def test_plan_run_completed_attention_required_is_nonzero(monkeypatch, capsys):
    detail = SimpleNamespace(
        outcome=FleetRunOutcome.ATTENTION_REQUIRED,
        run=SimpleNamespace(status=FleetRunStatus.COMPLETED, fleet_run_id="run-attention"),
        game_tape=SimpleNamespace(
            why_not=None,
            repositories=(),
            summary=GameTapeSummary(
                repositories=0,
                passed=0,
                failed=0,
                attention_required=0,
                not_run=0,
                planned_steps=0,
                executed_steps=0,
                passed_steps=0,
                failed_steps=0,
                skipped_steps=0,
                not_run_steps=0,
            ),
        ),
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
    args = build_parser().parse_args(
        [
            "dev",
            "_run-plan",
            "plan.yml",
            "--repository-id",
            "selected-repository",
            "--wait",
            "--lock-file",
            "goon.lock",
        ]
    )

    args.command, args.plan_command = "plan", "run"
    assert run_plan_command(runtime, args) == 1
    output = capsys.readouterr().out
    assert "Execution status: COMPLETED" in output
    assert "Outcome: ATTENTION_REQUIRED" in output


def test_resolved_plan_summary_supports_native_precommit_and_stream_outputs() -> None:
    plan = ResolvedFleetPlan.create(
        name="native-summary",
        description="",
        execution=VerificationExecutionPolicy(),
        steps=(
            ResolvedPreCommitStep(
                step_id="hooks",
                if_missing=MissingConfigPolicy.SKIP,
                timeout_seconds=30,
                runtime_id="managed-hooks",
                runtime_pyproject_sha256="a" * 64,
                runtime_uv_lock_sha256="b" * 64,
            ),
            ResolvedCommandStep(
                step_id="diagnostics",
                argv=("verify",),
                timeout_seconds=30,
                outputs=(PlanStreamOutput(name="diagnostic", stream="stderr", max_bytes=100),),
            ),
        ),
    )
    output = "\n".join(resolved_plan_summary(plan))
    assert "pre_commit: if_missing=skip" in output
    assert "runtime_id=managed-hooks" in output
    assert "outputs=diagnostic=stderr(100B)" in output
