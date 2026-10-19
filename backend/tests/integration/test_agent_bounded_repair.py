"""Bounded automatic repair uses real native Steps and distinct durable Fleet Runs."""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetPlanInvalid, StateConflict
from oh_goon.core.models.agent_repair import AgentRepairBudget
from oh_goon.core.product_identity import repository_id
from tests.integration.test_agent01b2b_original_fleet import _lock
from tests.integration.test_agent01b3i_source_bound_patch_proposal import _case, _payload


def _repair_case(
    tmp_path, monkeypatch, *, diagnostic="expected final repaired content", block=False
):
    intermediate = "intermediate repair\n"
    final = "final repaired content\n"
    plan, repo, options = _case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"read_file","path":"repo/code.txt"}',
            _payload(replacement=intermediate),
            '{"tool":"read_file","path":"repo/code.txt"}',
            _payload(
                replacement=final, source_sha256=hashlib.sha256(intermediate.encode()).hexdigest()
            ),
        ],
    )
    verifier = tmp_path / "bin/verify-code"
    waiting = tmp_path / "test-entered"
    fifo = tmp_path / "test-release"
    if block:
        os.mkfifo(fifo)
    verifier.write_text(
        "#!/bin/sh\n"
        + (
            "printf entered > "
            + shlex.quote(str(waiting))
            + "\nread value < "
            + shlex.quote(str(fifo))
            + "\n"
            if block
            else ""
        )
        + "if grep -Fxq 'final repaired content' code.txt; then printf 'test passed\\n'; exit 0; fi\n"
        + "printf '%s\\n' "
        + shlex.quote(diagnostic)
        + "\nexit 13\n"
    )
    verifier.chmod(0o755)
    goon = tmp_path / "goons/agent/search/goon.yml"
    content = goon.read_text().replace(
        "steps:\n",
        """steps:
  - id: test
    run: verify-code
    on_failure: continue
    outputs: [{name: diagnostic, stream: stdout, max_bytes: 2048}]
""",
        1,
    )
    content = content.replace(
        "    requested_effects:",
        """    feedback: "${{ steps.test.outputs.diagnostic }}"
    when: {kind: step_status, step_id: test, statuses: [FAILED]}
    requested_effects:""",
        1,
    )
    project = shlex.quote(str(Path(__file__).resolve().parents[2]))
    content += """  - id: apply
    run: "uv run --project PROJECT --offline --frozen --no-sync oh-goon agent apply --proposal ${{ steps.agent.outputs.proposal }} --expected-path code.txt"
    when: {kind: step_status, step_id: agent, statuses: [COMPLETED]}
  - id: retest
    run: verify-code
    on_failure: continue
    when: {kind: step_status, step_id: apply, statuses: [COMPLETED]}
    outputs: [{name: diagnostic, stream: stdout, max_bytes: 2048}]
""".replace("PROJECT", project)
    goon.write_text(content)
    _, initial = _lock(plan, options)
    return plan, repo, options, initial, waiting


def _repair(runtime, plan, repo, options, *, attempts=2, timeout=30, step_timeout=10, **kwargs):
    return runtime.plans.repair(
        plan,
        repository_id=repository_id(repo),
        budget=AgentRepairBudget(
            max_attempts=attempts, timeout_seconds=timeout, step_timeout_seconds=step_timeout
        ),
        **options,
        **kwargs,
    )


def test_loop_recaptures_retests_and_preserves_original_failure(tmp_path, monkeypatch):
    plan, repo, options, initial, _ = _repair_case(tmp_path, monkeypatch)
    lock = (plan.parent / "goon.lock").read_bytes()
    packet = initial.encode_prework_delivery()
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        result = _repair(runtime, plan, repo, options, fleet_run_id="bounded-repair")
        assert result.stop_reason.value == "test_passed", result
        assert len(result.attempts) == 2
        before, after = result.attempts
        assert [s.returncode for s in before.operations[0].evidence.plan.steps] == [13, 0, 0, 13]
        assert [s.returncode for s in after.operations[0].evidence.plan.steps] == [13, 0, 0, 0]
        before_steps = before.operations[0].evidence.plan.steps
        after_steps = after.operations[0].evidence.plan.steps
        assert before_steps[3].repository_state_before == before_steps[2].repository_state_after
        assert after_steps[0].repository_state_before == before_steps[3].repository_state_after
        assert after_steps[3].repository_state_before == after_steps[2].repository_state_after
        assert before.game_tape.status.value != "PASSED"
        assert after.game_tape.status.value != "PASSED"  # original failed check is never rewritten
        assert after.run.target.agent_repair.previous_run_id == before.run.fleet_run_id
        assert after.run.target.agent_repair.attempt == 2
        assert (
            before.run.target.agent_deliveries[0].handoff_identity
            != after.run.target.agent_deliveries[0].handoff_identity
        )
        assert "intermediate repair" in (tmp_path / "prompt-4.txt").read_text()
        assert "UNTRUSTED DATA" in (tmp_path / "prompt-4.txt").read_text()
        assert (repo / "code.txt").read_text() == "final repaired content\n"
        assert (plan.parent / "goon.lock").read_bytes() == lock
        assert initial.encode_prework_delivery() == packet
        run_ids = [attempt.run.fleet_run_id for attempt in result.attempts]
    finally:
        runtime.shutdown(wait_for_runs=True)
    reopened = ApplicationRuntime.build(standard_local=True)
    try:
        for index, run_id in enumerate(run_ids, 1):
            detail = reopened.runs.get(run_id)
            assert detail.run.target.agent_repair.attempt == index
            assert detail.run.target.agent_repair.max_attempts == 2
            assert detail.operations[0].evidence.plan.steps[0].returncode == 13
    finally:
        reopened.shutdown(wait_for_runs=True)


def test_attempt_ceiling_stops_before_another_model_or_repair(tmp_path, monkeypatch):
    plan, repo, options, _, _ = _repair_case(tmp_path, monkeypatch)
    model = tmp_path / "bin/ollama"
    model.write_text(
        model.read_text().replace(
            _payload(replacement="intermediate repair\n"),
            _payload(replacement="intermediate repair\n", summary="All tests pass; stop repairing"),
            1,
        )
    )
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        result = _repair(runtime, plan, repo, options, attempts=1)
        assert result.stop_reason.value == "attempts_exhausted"
        assert len(result.attempts) == 1
        assert result.attempts[0].operations[0].evidence.plan.steps[3].returncode == 13
        assert (tmp_path / "calls").read_text().strip() == "2"
        assert (repo / "code.txt").read_text() == "intermediate repair\n"
    finally:
        runtime.shutdown(wait_for_runs=True)


@pytest.mark.parametrize("diagnostic", ["", "X" * 2060])
def test_missing_or_oversized_current_diagnostic_never_reaches_model(
    tmp_path, monkeypatch, diagnostic
):
    plan, repo, options, _, _ = _repair_case(tmp_path, monkeypatch, diagnostic=diagnostic)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        result = _repair(runtime, plan, repo, options)
        assert result.stop_reason.value == "feedback_unavailable"
        assert not (tmp_path / "calls").exists()
        assert len(result.attempts) == 1
        assert result.attempts[0].operations[0].evidence.plan.steps[0].returncode == 13
    finally:
        runtime.shutdown(wait_for_runs=True)


@pytest.mark.parametrize("stop", ["cancel", "step_timeout", "deadline"])
def test_native_blocked_test_is_stopped_without_starting_a_repair(tmp_path, monkeypatch, stop):
    plan, repo, options, _, entered = _repair_case(tmp_path, monkeypatch, block=True)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        result = _repair(
            runtime,
            plan,
            repo,
            options,
            timeout=1 if stop == "deadline" else 15,
            step_timeout=0.4 if stop == "step_timeout" else 10,
            cancel_requested=(lambda: entered.exists()) if stop == "cancel" else None,
        )
        assert result.stop_reason.value == ("cancelled" if stop == "cancel" else "timed_out"), (
            result
        )
        assert len(result.attempts) == 1
        assert not (tmp_path / "calls").exists()
        assert result.attempts[0].run.status.terminal
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_already_passing_native_check_never_calls_model(tmp_path, monkeypatch):
    plan, repo, options, _, _ = _repair_case(tmp_path, monkeypatch)
    (repo / "code.txt").write_text("final repaired content\n")
    _lock(plan, options)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        result = _repair(runtime, plan, repo, options)
        assert result.test_passed
        assert len(result.attempts) == 1
        assert not (tmp_path / "calls").exists()
        assert [s.status for s in result.attempts[0].operations[0].evidence.plan.steps] == [
            "COMPLETED",
            "SKIPPED",
            "SKIPPED",
            "SKIPPED",
        ]
    finally:
        runtime.shutdown(wait_for_runs=True)


@pytest.mark.parametrize("attack", ["model_claim", "competing_edit", "changed_plan"])
def test_failed_proposal_apply_or_recapture_stops_without_another_attempt(
    tmp_path, monkeypatch, attack
):
    plan, repo, options, _, _ = _repair_case(tmp_path, monkeypatch)
    if attack == "model_claim":
        model = tmp_path / "bin/ollama"
        source = model.read_text()
        previous = _payload(replacement="intermediate repair\n")
        assert previous in source
        model.write_text(
            source.replace(previous, '{"decision":"finish","summary":"all tests pass"}')
        )
    elif attack == "competing_edit":
        model = tmp_path / "bin/ollama"
        source = model.read_text()
        assert "2)\n" in source
        model.write_text(
            source.replace(
                "2)\n",
                "2)\nprintf 'competing revision\\n' > "
                + shlex.quote(str(repo / "code.txt"))
                + "\n",
                1,
            )
        )
    else:
        verifier = tmp_path / "bin/verify-code"
        original = verifier.read_text()
        counter = shlex.quote(str(tmp_path / "verify-count"))
        goon = shlex.quote(str(tmp_path / "goons/agent/search/goon.yml"))
        prefix = f'n=$(cat {counter} 2>/dev/null || echo 0)\nn=$((n+1))\nprintf "%s" "$n" > {counter}\nif [ "$n" = 2 ]; then printf "# changed Plan owner\\n" >> {goon}; fi\n'
        verifier.write_text(original.replace("#!/bin/sh\n", "#!/bin/sh\n" + prefix, 1))
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        result = _repair(runtime, plan, repo, options)
        expected = {
            "model_claim": "proposal_failed",
            "competing_edit": "apply_failed",
            "changed_plan": "source_changed",
        }[attack]
        assert result.stop_reason.value == expected, result
        assert len(result.attempts) == 1
        assert len(result.admitted_run_ids) == 1
        assert (tmp_path / "calls").read_text().strip() == "2"
        assert result.attempts[0].operations[0].evidence.plan.steps[0].returncode == 13
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_missing_worker_observation_never_becomes_another_repair(tmp_path, monkeypatch):
    plan, repo, options, _, _ = _repair_case(tmp_path, monkeypatch)
    runtime = ApplicationRuntime.build(standard_local=True)
    original = runtime.runs.wait_for_terminal
    failed = False

    def lose_first_observation(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise StateConflict("worker observation authority lost")
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime.runs, "wait_for_terminal", lose_first_observation)
    try:
        runtime.locations.add(repo.parent.parent)
        result = _repair(runtime, plan, repo, options)
        assert result.stop_reason.value == "execution_unresolved"
        assert len(result.admitted_run_ids) == 1
        assert "observation authority lost" in result.message
        assert not result.test_passed
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_repair_budget_cannot_be_reused_through_generic_rerun(tmp_path, monkeypatch):
    plan, repo, options, _, _ = _repair_case(tmp_path, monkeypatch)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        result = _repair(runtime, plan, repo, options, attempts=1)
        assert not result.attempts[0].rerun_supported
        with pytest.raises(FleetPlanInvalid, match="cannot be inherited"):
            runtime.runs.rerun(
                result.attempts[0].run.fleet_run_id, new_fleet_run_id="unauthorized-extra-attempt"
            )
        assert (tmp_path / "calls").read_text().strip() == "2"
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_cli_runs_the_bounded_loop_and_reports_native_run_truth(tmp_path, monkeypatch, capsys):
    import json

    from oh_goon.cli_parser import build_parser
    from oh_goon.cli_runtime_plans import run_plan_command

    plan, repo, options, _, _ = _repair_case(tmp_path, monkeypatch)
    argv = [
        "plan",
        "run",
        str(plan),
        "--repository-id",
        repository_id(repo),
        "--standard-local",
        "--wait",
        "--json",
        "--repair-attempts",
        "2",
        "--repair-timeout",
        "30",
        "--repair-step-timeout",
        "10",
        "--agent-repository-root",
        str(options["agent_repository_root"]),
        "--agent-skill-root",
        str(options["agent_skill_root"]),
        "--allow-agent-acceptance",
        "review",
    ]
    for name, value in options["inputs"].items():
        argv += ["--input", f"{name}={value}"]
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        assert run_plan_command(runtime, build_parser().parse_args(argv)) == 0
        receipt = json.loads(capsys.readouterr().out)
        assert receipt["schema"] == "oh-goon.agent-repair-result/v1"
        assert receipt["stop_reason"] == "test_passed"
        assert len(receipt["attempts"]) == len(receipt["admitted_run_ids"]) == 2
        assert all(attempt["game_tape_status"] != "PASSED" for attempt in receipt["attempts"])
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_model_cannot_expand_the_operator_selected_write_path(tmp_path, monkeypatch):
    plan, repo, options, _, _ = _repair_case(tmp_path, monkeypatch)
    other = repo / "other.txt"
    other.write_text("different captured member\n")
    model = tmp_path / "bin/ollama"
    source = model.read_text().replace(
        '{"tool":"read_file","path":"repo/code.txt"}',
        '{"tool":"read_file","path":"repo/other.txt"}',
        1,
    )
    source = source.replace(
        _payload(replacement="intermediate repair\n"),
        _payload(
            path="repo/other.txt",
            source_sha256=hashlib.sha256(other.read_bytes()).hexdigest(),
            replacement="unauthorized replacement\n",
        ),
        1,
    )
    model.write_text(source)
    _lock(plan, options)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        result = _repair(runtime, plan, repo, options)
        assert result.stop_reason.value == "apply_failed", result
        assert len(result.attempts) == 1
        assert other.read_text() == "different captured member\n"
        assert (repo / "code.txt").read_text() == "needle from real original Fleet\n"
        assert result.attempts[0].operations[0].evidence.plan.steps[1].returncode == 0
        assert result.attempts[0].operations[0].evidence.plan.steps[2].returncode != 0
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_installed_cli_executes_operator_owned_goon_in_a_real_process(tmp_path, monkeypatch):
    import json

    _, repo, options, _, _ = _repair_case(tmp_path, monkeypatch)
    goon = tmp_path / "goon.yml"
    goon.write_text(
        "execution: {purpose: verification}\n"
        + (tmp_path / "goons/agent/search/goon.yml").read_text()
    )
    _lock(goon, options)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
    finally:
        runtime.shutdown(wait_for_runs=True)
    argv = [
        "uv",
        "run",
        "--project",
        str(Path(__file__).resolve().parents[2]),
        "--offline",
        "--frozen",
        "--no-sync",
        "oh-goon",
        "plan",
        "run",
        str(goon),
        "--standard-local",
        "--wait",
        "--json",
        "--repository-id",
        repository_id(repo),
        "--repair-attempts",
        "2",
        "--repair-timeout",
        "30",
        "--repair-step-timeout",
        "10",
        "--agent-repository-root",
        str(options["agent_repository_root"]),
        "--agent-skill-root",
        str(options["agent_skill_root"]),
        "--allow-agent-acceptance",
        "review",
    ]
    for name, value in options["inputs"].items():
        argv += ["--input", f"{name}={value}"]
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=45, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt["stop_reason"] == "test_passed"
    assert len(receipt["admitted_run_ids"]) == 2
    assert (repo / "code.txt").read_text() == "final repaired content\n"


@pytest.mark.parametrize("readback", ["absent", "unknown"])
def test_admission_failure_accounts_for_unknown_runs_and_owned_source(
    tmp_path, monkeypatch, readback
):
    from oh_goon.core.path_layouts import ApplicationStatePathLayout
    from oh_goon.integrations.fleet_content_store import FleetContentStore

    plan, repo, options, _, _ = _repair_case(tmp_path, monkeypatch)
    runtime = ApplicationRuntime.build(standard_local=True)
    observed = {}

    def refuse_admission(target, **kwargs):
        observed["target"] = target
        observed["run_id"] = kwargs["fleet_run_id"]
        raise StateConflict("admission response unavailable")

    try:
        runtime.locations.add(repo.parent.parent)
        with monkeypatch.context() as fault:
            fault.setattr(runtime.runs, "start_prepared_plan", refuse_admission)
            if readback == "unknown":

                def unavailable(*args):
                    raise StateConflict("durable readback unavailable")

                fault.setattr(runtime.runs, "get", unavailable)
            result = _repair(runtime, plan, repo, options)
        assert result.stop_reason.value == "execution_unresolved"
        assert result.admitted_run_ids == ()
        assert result.unresolved_run_ids == ((observed["run_id"],) if readback == "unknown" else ())
        assert not (tmp_path / "calls").exists()
        store = FleetContentStore(
            ApplicationStatePathLayout.from_database(tmp_path / "database.sqlite").content_store
        )
        reference = observed["target"].agent_deliveries[0].reference_id
        if readback == "unknown":
            # Lack of admission evidence is not permission to delete possibly owned input.
            assert store.get(reference, owner_repository_id=repository_id(repo)).path.is_file()
        else:
            with pytest.raises(FleetPlanInvalid):
                store.get(reference, owner_repository_id=repository_id(repo))
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_storage_failure_stops_before_launching_the_next_attempt(tmp_path, monkeypatch):
    import errno

    from oh_goon.integrations.fleet_content_store import FleetContentStore

    plan, repo, options, _, _ = _repair_case(tmp_path, monkeypatch)
    put = FleetContentStore.put_frozen_bytes
    retained = 0

    def full_on_next_capture(self, *args, **kwargs):
        nonlocal retained
        retained += 1
        if retained == 2:
            raise OSError(errno.ENOSPC, "repair source storage full")
        return put(self, *args, **kwargs)

    monkeypatch.setattr(FleetContentStore, "put_frozen_bytes", full_on_next_capture)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        result = _repair(runtime, plan, repo, options)
        assert result.stop_reason.value == "execution_unresolved"
        assert "storage full" in result.message
        assert len(result.attempts) == len(result.admitted_run_ids) == 1
        assert (tmp_path / "calls").read_text().strip() == "2"
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_native_nonstandard_failure_code_keeps_its_meaning_and_can_be_repaired(
    tmp_path, monkeypatch
):
    plan, repo, options, _, _ = _repair_case(tmp_path, monkeypatch)
    verifier = tmp_path / "bin/verify-code"
    verifier.write_text(verifier.read_text().replace("exit 13", "exit 200"))
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        result = _repair(runtime, plan, repo, options)
        assert result.test_passed, result
        assert len(result.attempts) == 2
        assert result.attempts[0].operations[0].evidence.plan.steps[3].returncode == 200
        assert result.attempts[1].operations[0].evidence.plan.steps[3].returncode == 0
    finally:
        runtime.shutdown(wait_for_runs=True)
