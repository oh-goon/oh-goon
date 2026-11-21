"""Execute the authored shipped regression Goon through original claimed Runs."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.models.fleet_plan import ResolvedCommandStep
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.product_identity import repository_id

ROOT = Path(__file__).resolve().parents[4]
pytestmark = [pytest.mark.process, pytest.mark.no_resume]


def _run(runtime: ApplicationRuntime, plan: Path, repository: Path, run_id: str) -> FleetRunDetail:
    target = runtime.plans.prepare_execution_target(plan, repository=repository)
    selected = target.plan.steps[0]
    assert isinstance(selected, ResolvedCommandStep)
    assert selected.regression_failure is not None
    assert selected.regression_failure.exit_codes == (1,)
    assert selected.on_failure.value == "stop" and selected.success_codes == (0,)
    started = runtime.runs.start_prepared_plan(
        target, repository_ids={repository_id(repository)}, fleet_run_id=run_id
    )
    return runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=60)


def test_shipped_regression_goon_retains_failure_and_preserves_ordinary_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authored = (ROOT / "templates/goons/agents/regression/goon.yml").read_bytes()
    package = tmp_path / "goons/agents/regression"
    package.mkdir(parents=True)
    (package / "goon.yml").write_bytes(authored)
    (package / "README.md").write_text("# agents/regression\n")
    followup = tmp_path / "goons/agents/later-effect"
    followup.mkdir(parents=True)
    (followup / "goon.yml").write_text("steps:\n- id: later\n  run: ./later\n")
    (followup / "README.md").write_text("# agents/later-effect\n")
    plan = tmp_path / "plan.yml"
    plan.write_text(
        "execution: {purpose: verification}\ngoons:\n"
        "- import: agents/regression\n  from: goons\n"
        "  with: {regression_command: check}\n"
        "- import: agents/later-effect\n  from: goons\n"
    )
    repository = tmp_path / "repositories/subject"
    repository.mkdir(parents=True)
    code = repository / "code.txt"
    code.write_text("repaired\n")
    check = repository / "check"
    check.write_text(
        "#!/bin/sh\nset -eu\n[ -f code.txt ] || exit 2\n"
        'if [ "$(cat code.txt)" = repaired ]; then exit 0; fi\n'
        "printf 'Regression assertion failed: expected repaired code\\n'\nexit 1\n"
    )
    check.chmod(0o755)
    later = repository / "later"
    later.write_text("#!/bin/sh\nprintf 'accepted later effect\\n' >> later-effect.txt\n")
    later.chmod(0o755)
    subprocess.run(("git", "init", "-q", str(repository)), check=True, timeout=10)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "control/runtime.sqlite"))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(tmp_path / "league.yml"))
    for name, directory in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"),
        ("XDG_CACHE_HOME", "cache"),
    ):
        monkeypatch.setenv(name, str(tmp_path / directory))
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repository.parent)
        runtime.plans.lock(plan)
        control = _run(runtime, plan, repository, "shipped-regression-control")
        assert control.outcome.value == "SUCCESS" and control.game_tape.status == "PASSED"
        effect = repository / "later-effect.txt"
        assert effect.read_text() == "accepted later effect\n"
        effect.unlink()
        code.write_text("broken\n")
        failed = _run(runtime, plan, repository, "shipped-regression-failure")
        assert failed.outcome.value == "ATTENTION_REQUIRED"
        assert failed.game_tape.status == "ATTENTION_REQUIRED"
        evidence = failed.operations[0].evidence
        assert isinstance(evidence, FleetPlanOperationEvidence) and evidence.plan is not None
        regression, blocked = evidence.plan.steps
        assert regression.status == "FAILED"
        assert regression.settlement.observed_returncode == 1
        assert regression.settlement.cleanup == "VERIFIED"
        assert len(regression.attempts) == 1
        attempt = regression.attempts[0]
        assert attempt.returncode == attempt.observed_returncode == 1
        assert attempt.supervisor is not None
        assert attempt.supervisor.final_process_group_quiescent is True
        assert len(regression.outputs) == 1
        output = regression.outputs[0]
        assert output.name == "regression_feedback" and output.retained_stream is not None
        retained = output.retained_stream
        assert retained.text == "Regression assertion failed: expected repaired code"
        assert retained.source == regression.stdout_evidence == attempt.stdout_evidence
        assert retained.physical_execution_id == attempt.physical_execution_id
        assert retained.physical_execution_id
        assert blocked.status == "SKIPPED" and not effect.exists()
        assert (package / "goon.yml").read_bytes() == authored
        assert runtime.runs.get(control.run.fleet_run_id).outcome.value == "SUCCESS"
    finally:
        runtime.shutdown(wait_for_runs=True)
