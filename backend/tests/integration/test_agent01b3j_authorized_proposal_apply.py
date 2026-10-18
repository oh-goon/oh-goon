"""AGENT-01B3J: original output dataflow -> explicitly selected native apply -> test."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedCommandStep
from oh_goon.integrations._fleet_plan_step_dataflow import bind_step_outputs
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome
from oh_goon.integrations.agent_patch_apply import (
    apply_selected_proposal,
)
from oh_goon.integrations.fleet_plan_step_publication import publish_direct_attempt_outputs
from tests.integration.test_agent01b2b_original_fleet import _lock
from tests.integration.test_agent01b3i_source_bound_patch_proposal import _ORIGINAL, _case, _payload


def _receipt(original=_ORIGINAL, replacement="changed through explicitly authorized Step\n", **kw):
    value = dict(
        schema="agent-patch-proposal-v1",
        handoff_identity="captured-owner-123",
        path="repo/code.txt",
        source_sha256=hashlib.sha256(original).hexdigest(),
        replacement=replacement,
        summary="Fix the text",
    )
    value.update(kw)
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "code.txt").write_bytes(_ORIGINAL)
    return root


def test_proposal_published_only_as_original_successful_agent_attempt(tmp_path, monkeypatch):
    plan, _, opts = _case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"read_file","path":"repo/code.txt"}',
            _payload(),
        ],
    )
    frozen, handoff = _lock(plan, opts)
    step = frozen.steps[0]
    receipt = _receipt(
        replacement="revised content proposed, not applied\n",
        handoff_identity=handoff.handoff_identity,
    )
    stdout = (
        "tool transcript\nAgent proposal only; no repository mutation or tests executed\n"
        + receipt
        + "\n"
    )
    publication = publish_direct_attempt_outputs(
        step, StepOutcome(argv=("rg",), returncode=0, stdout=stdout), tmp_path
    )
    assert publication.outcome.returncode == 0
    assert publication.outputs["proposal"] == receipt
    assert len(publication.output_evidence) == 1
    assert publication.output_evidence[0].sha256 == hashlib.sha256(receipt.encode()).hexdigest()
    bound = bind_step_outputs(
        ResolvedCommandStep(
            step_id="apply",
            argv=(
                "oh-goon",
                "agent",
                "apply",
                "--proposal",
                "${{ steps." + step.step_id + ".outputs.proposal }}",
                "--expected-path",
                "code.txt",
            ),
        ),
        {step.step_id: publication.outputs},
    )
    assert bound.argv[4] == receipt
    assert bound.argv[6] == "code.txt"
    assert bound.argv[:3] == ("oh-goon", "agent", "apply")


def test_failed_or_nonproposal_agent_cannot_publish_apply_input(tmp_path, monkeypatch):
    plan, _, opts = _case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"read_file","path":"repo/code.txt"}',
            _payload(),
        ],
    )
    frozen, handoff = _lock(plan, opts)
    step = frozen.steps[0]
    stdout = "Agent proposal only; no repository mutation or tests executed\n" + _receipt() + "\n"
    bad = publish_direct_attempt_outputs(
        step, StepOutcome(argv=("rg",), returncode=0, stdout=stdout), tmp_path
    )
    assert bad.outcome.returncode == 125  # mismatched frozen handoff
    assert bad.outputs == {}
    with pytest.raises(FleetPlanInvalid, match="unavailable output"):
        bind_step_outputs(
            ResolvedCommandStep(
                step_id="apply", argv=("cmd", "${{ steps." + step.step_id + ".outputs.proposal }}")
            ),
            {step.step_id: bad.outputs},
        )


def test_an_authorized_step_can_apply_then_real_test_sees_changed_bytes(tmp_path):
    workspace = _workspace(tmp_path)
    path, digest = apply_selected_proposal(
        _receipt(),
        workspace=workspace,
        expected_path="code.txt",
        expected_handoff_identity="captured-owner-123",
    )
    assert path == "code.txt"
    assert digest == hashlib.sha256(b"changed through explicitly authorized Step\n").hexdigest()
    verified = subprocess.run(
        ["grep", "-Fxq", "changed through explicitly authorized Step", "code.txt"],
        cwd=workspace,
        check=False,
    )
    assert verified.returncode == 0
    with pytest.raises(FleetPlanInvalid, match="stale"):
        apply_selected_proposal(_receipt(), workspace=workspace, expected_path="code.txt")


@pytest.mark.parametrize(
    "bad",
    [
        {"path": "repo/../outside"},
        {"path": "repo/missing.txt"},
        {"source_sha256": "0" * 64},
        {"schema": "unknown"},
        {"replacement": "x" * 2049},
        {"replacement": ""},
        {"summary": "bad\nheader"},
        {"unexpected": "shell"},
        {"handoff_identity": "forged"},
    ],
)
def test_untrusted_or_stale_proposal_cannot_change_source(tmp_path, bad):
    workspace = _workspace(tmp_path)
    receipt = _receipt(**bad)
    with pytest.raises(FleetPlanInvalid):
        apply_selected_proposal(
            receipt,
            workspace=workspace,
            expected_path="code.txt",
            expected_handoff_identity="captured-owner-123",
        )
    assert (workspace / "code.txt").read_bytes() == _ORIGINAL


def test_expected_path_is_operator_selected_not_model_selected(tmp_path):
    workspace = _workspace(tmp_path)
    with pytest.raises(FleetPlanInvalid, match="select"):
        apply_selected_proposal(_receipt(), workspace=workspace, expected_path="other.txt")
    assert (workspace / "code.txt").read_bytes() == _ORIGINAL


def test_symlink_target_is_not_followed(tmp_path):
    workspace = _workspace(tmp_path)
    (workspace / "alias.txt").symlink_to(workspace / "code.txt")
    with pytest.raises(FleetPlanInvalid):
        apply_selected_proposal(
            _receipt(path="repo/alias.txt"), workspace=workspace, expected_path="alias.txt"
        )
    assert (workspace / "code.txt").read_bytes() == _ORIGINAL


def test_duplicate_json_keys_rejected_before_mutation(tmp_path):
    workspace = _workspace(tmp_path)
    bad = _receipt()[:-1] + ',"path":"repo/evil"}'
    with pytest.raises(FleetPlanInvalid, match="duplicate"):
        apply_selected_proposal(bad, workspace=workspace, expected_path="code.txt")
    assert (workspace / "code.txt").read_bytes() == _ORIGINAL


def test_native_apply_command_has_real_process_exit_status(tmp_path):
    workspace = _workspace(tmp_path)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    executable = [
        "uv",
        "run",
        "--no-project",
        "--active",
        "--offline",
        "python",
        "-c",
        "import sys; from oh_goon.cli import main; sys.exit(main())",
        "agent",
        "apply",
    ]
    changed = subprocess.run(
        executable + ["--proposal", _receipt(), "--expected-path", "code.txt"],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
    )
    assert changed.returncode == 0, changed.stderr
    assert changed.stdout.startswith("APPLIED code.txt sha256:")
    again = subprocess.run(
        executable + ["--proposal", _receipt(), "--expected-path", "code.txt"],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
    )
    assert again.returncode != 0 and "stale" in again.stderr


def _sequential_case(tmp_path, monkeypatch, *, insert_stale=False):
    from tests.integration.test_agent01b3b_original_fleet_model_observation import _run

    plan, repo, opts = _case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"read_file","path":"repo/code.txt"}',
            _payload(),
        ],
    )
    # This test shim only exposes the installed CLI module under a process name.
    # Fleet itself still launches/supervises a real external operating-system
    # process; no alternative application runner is introduced.
    executable = tmp_path / "bin" / "oh-goon"
    executable.write_text(
        "#!/bin/sh\n"
        f"PYTHONPATH={Path(__file__).resolve().parents[2] / 'src'} "
        "exec uv run --no-project --active --offline python -c "
        "'import sys; from oh_goon.cli import main; sys.exit(main())' \"$@\"\n"
    )
    executable.chmod(0o755)
    goon = tmp_path / "goons" / "agent" / "search" / "goon.yml"
    if insert_stale:
        stale = tmp_path / "bin" / "stale-original-file"
        stale.write_text('#!/bin/sh\nprintf "%s\\n" "competing source revision" > code.txt\n')
        stale.chmod(0o755)
        goon.write_text(goon.read_text() + '  - id: drift\n    run: "stale-original-file"\n')
    with goon.open("a") as out:
        out.write("""  - id: apply
    run: "oh-goon agent apply --proposal ${{ steps.agent.outputs.proposal }} --expected-path code.txt"
  - id: test
    run: "rg --fixed-strings revised code.txt"
  - id: evaluate
    run: "git diff --check"
""")
    frozen, handoff = _lock(plan, opts)
    assert len(frozen.steps) == (5 if insert_stale else 4)
    return plan, repo, opts, frozen, handoff, _run


def test_real_sequential_propose_apply_test_and_reopen_original_game_tape(tmp_path, monkeypatch):
    from oh_goon.application import ApplicationRuntime

    plan, repo, opts, frozen, handoff, run = _sequential_case(tmp_path, monkeypatch)
    result = run(plan, repo, opts, fleet_run_id="agent01b3j-e2e")
    assert result.game_tape.status.value == "PASSED"
    assert result.outcome.value == "SUCCESS"
    steps = result.operations[0].evidence.plan.steps
    assert [(s.kind, s.returncode) for s in steps] == [
        ("agent", 0),
        ("command", 0),
        ("command", 0),
        ("command", 0),
    ]
    assert len(steps[0].outputs) == 1
    assert steps[0].outputs[0].name == "proposal"
    assert (repo / "code.txt").read_text() == "revised content proposed, not applied\n"
    assert (tmp_path / "calls").read_text().strip() == "2"
    reopened = ApplicationRuntime.build(standard_local=True)
    try:
        durable = reopened.runs.get("agent01b3j-e2e")
        assert durable.game_tape.status.value == "PASSED"
        assert len(durable.operations[0].evidence.plan.steps) == 4
        assert durable.operations[0].evidence.plan.steps[1].returncode == 0
    finally:
        reopened.shutdown(wait_for_runs=True)


def test_stale_source_fails_native_apply_and_skips_following_test(tmp_path, monkeypatch):
    plan, repo, opts, frozen, handoff, run = _sequential_case(
        tmp_path, monkeypatch, insert_stale=True
    )
    result = run(plan, repo, opts, fleet_run_id="agent01b3j-stale")
    assert result.game_tape.status.value != "PASSED"
    steps = result.operations[0].evidence.plan.steps
    assert steps[0].returncode == 0  # proposal was safely published
    assert steps[1].returncode == 0  # separate competing edit
    assert steps[2].returncode != 0  # preimage CAS refused the proposal
    assert steps[3].status == "SKIPPED"  # tests cannot claim success
    assert steps[4].status == "SKIPPED"  # evaluation cannot claim success
    assert (repo / "code.txt").read_text() == "competing source revision\n"
