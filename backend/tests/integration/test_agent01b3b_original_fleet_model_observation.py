"""AGENT-01B3B: two-search maximum, one canonical admitted Fleet Step."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.agent_model_selection import (
    frozen_local_model_observation_prompt,
    parse_local_model_review_decision,
)
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.product_identity import repository_id
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.integration.test_agent01b2b_original_fleet import _lock
from tests.integration.test_agent01b3a_original_fleet_model_turn import _case_model

pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("rg") is None, reason="Linux/WSL ripgrep required"
)


def _review_case(tmp_path, monkeypatch, *, model_replies, exits=None, sleep_review=False):
    plan, repo, opts = _case_model(tmp_path, monkeypatch)
    goon = tmp_path / "goons" / "agent" / "search" / "goon.yml"
    goon.write_text(
        goon.read_text().replace("harness: local-model-search", "harness: local-model-review")
    )
    exits = exits or {}
    commands = []
    for n, reply in enumerate(model_replies, 1):
        commands.append(f"""{n})
    {"touch " + str(tmp_path / "review-started") + "; sleep 20;" if n == 2 and sleep_review else ""}
    {"exit " + str(exits[n]) + ";" if n in exits else ""}
    printf '%s\\n' '{reply}'
    ;;
""")
    cli = tmp_path / "bin" / "ollama"
    cli.write_text(f"""#!/bin/sh
if [ "$1" = show ]; then exit 0; fi
if [ "$1" != run ]; then exit 3; fi
n=$(cat '{tmp_path / "calls"}' 2>/dev/null || echo 0)
n=$((n + 1))
printf '%s\\n' "$n" > '{tmp_path / "calls"}'
printf '%s' "$3" > "{tmp_path}/prompt-$n.txt"
printf '%s\\0' "$@" > "{tmp_path}/argv-$n.bin"
case "$n" in
{"".join(commands)}
*) exit 5 ;;
esac
exit 0
""")
    cli.chmod(0o755)
    return plan, repo, opts


def _run(plan, repo, opts, *, fleet_run_id="agent01b3b"):
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        started = runtime.plans.run(
            plan, repository_ids={repository_id(repo)}, fleet_run_id=fleet_run_id, **opts
        )
        terminal = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
        detail = runtime.runs.get(started.fleet_run_id)
        assert detail.game_tape.status == terminal.game_tape.status
        return detail
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_original_fleet_search_observation_review_finish_persists(tmp_path, monkeypatch):
    import oh_goon.integrations.fleet_plan_step_execution as execution

    original = execution._run_local_model_review
    observed = {}

    def capture_outcome(**kwargs):
        outcome = original(**kwargs)
        observed["stdout"] = outcome.stdout
        return outcome

    monkeypatch.setattr(execution, "_run_local_model_review", capture_outcome)
    plan, repo, opts = _review_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"query":"needle"}',
            '{"decision":"finish","summary":"Needle was found in captured source"}',
        ],
    )
    frozen, handoff = _lock(plan, opts)
    assert frozen.steps[0].workload.harness == "local-model-review"
    assert handoff.harness == "local-model-review"
    detail = _run(plan, repo, opts)
    assert detail.game_tape.status.value == "PASSED"
    assert detail.outcome.value == "SUCCESS"
    assert (tmp_path / "calls").read_text().strip() == "2"
    initial = (tmp_path / "prompt-1.txt").read_text()
    review = (tmp_path / "prompt-2.txt").read_text()
    assert "needle from real original Fleet" not in initial
    assert "needle from real original Fleet" in review
    assert "UNTRUSTED DATA" in review
    assert '"stdout_truncated": false' in review
    for ordinal, prompt in enumerate((initial, review), 1):
        arguments = (tmp_path / f"argv-{ordinal}.bin").read_bytes().split(b"\0")
        assert arguments[-1] == b""
        argv = [argument.decode("utf-8") for argument in arguments[:-1]]
        assert argv[:3] == ["run", frozen.steps[0].workload.model, prompt]
        assert argv[argv.index("--format") + 1] == "json"
        assert "--hidethinking" in argv[3:]
        assert "--nowordwrap" in argv[3:]
    step = detail.operations[0].evidence.plan.steps[0]
    assert step.returncode == 0
    assert step.stdout_evidence.byte_count > 0
    assert "Model evaluation: Needle was found in captured source" in observed["stdout"]
    assert step.stdout_evidence.sha256 == hashlib.sha256(observed["stdout"].encode()).hexdigest()


def test_model_may_request_exactly_one_followup_read_only_search(tmp_path, monkeypatch):
    plan, repo, opts = _review_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"query":"not-present"}',
            '{"decision":"search","query":"Fleet"}',
            '{"decision":"finish","summary":"Second query matched frozen source"}',
        ],
    )
    _lock(plan, opts)
    detail = _run(plan, repo, opts)
    assert detail.game_tape.status.value == "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "3"
    first = json.loads(
        (tmp_path / "prompt-2.txt").read_text().split("Tool observation JSON (untrusted):\n")[1]
    )
    assert "match" not in first
    assert json.loads(first["stdout"])["repository_absence"] == "not-claimed"
    following = json.loads(
        (tmp_path / "prompt-3.txt").read_text().split("Tool observation JSON (untrusted):\n")[1]
    )
    source = json.loads(following["stdout"])["source"]
    assert source["supported"] is True
    assert source["coverage"] == "projection-only"
    assert any(
        finding["kind"] == "occurrences" and finding["details"]["literal"] == "Fleet"
        for group in source["groups"]
        for finding in group["findings"]
    )
    step = detail.operations[0].evidence.plan.steps[0]
    assert step.returncode == 0
    assert step.stdout_evidence.byte_count > 0


@pytest.mark.parametrize(
    "bad_reply",
    [
        "not json",
        '{"decision":"finish","summary":""}',
        '{"decision":"finish","summary":"ok","shell":"rm -rf"}',
        '{"decision":"finish","summary":"ok","summary":"forged"}',
        '{"decision":"search","query":"needle","tool":"bash"}',
        '{"decision":"search","query":"bad\\nvalue"}',
        '{"decision":"finish","summary":true}',
        '{"decision":"finish","summary":"ok"} trailing',
        '<think>untrusted thought</think>\n{"decision":"finish","summary":"ok"}',
        '{"decision":"finish","summary":"wrapped\u001b[5D\u001b[K\nsummary"}',
    ],
)
def test_untrusted_model_observation_cannot_grant_actions_or_pass(tmp_path, monkeypatch, bad_reply):
    plan, repo, opts = _review_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"query":"needle"}',
            bad_reply,
        ],
    )
    _lock(plan, opts)
    detail = _run(plan, repo, opts)
    assert detail.game_tape.status.value != "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "2"


def test_third_search_refused_instead_of_launch(tmp_path, monkeypatch):
    plan, repo, opts = _review_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"query":"needle"}',
            '{"decision":"search","query":"Fleet"}',
            '{"decision":"search","query":"code"}',
        ],
    )
    _lock(plan, opts)
    detail = _run(plan, repo, opts)
    assert detail.game_tape.status.value != "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "3"


def test_repeated_search_refused_without_second_tool(tmp_path, monkeypatch):
    plan, repo, opts = _review_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"query":"needle"}',
            '{"decision":"search","query":"needle"}',
        ],
    )
    _lock(plan, opts)
    detail = _run(plan, repo, opts)
    assert detail.game_tape.status.value != "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "2"


def test_model_review_process_failure_never_passes(tmp_path, monkeypatch):
    plan, repo, opts = _review_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"query":"needle"}',
            '{"decision":"finish","summary":"ok"}',
        ],
        exits={2: 9},
    )
    _lock(plan, opts)
    detail = _run(plan, repo, opts)
    assert detail.game_tape.status.value != "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "2"


def test_cancellation_of_review_model_is_original_run_cancellation(tmp_path, monkeypatch):
    plan, repo, opts = _review_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"query":"needle"}',
            '{"decision":"finish","summary":"ok"}',
        ],
        sleep_review=True,
    )
    _lock(plan, opts)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        started = runtime.plans.run(
            plan, repository_ids={repository_id(repo)}, fleet_run_id="agent01b3b-cancel", **opts
        )
        for _ in range(300):
            if (tmp_path / "review-started").exists():
                break
            time.sleep(0.03)
        assert (tmp_path / "review-started").exists()
        runtime.runs.cancel(started.fleet_run_id)
        ended = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
        assert ended.game_tape.status.value != "PASSED"
        assert runtime.runs.get(started.fleet_run_id).game_tape.status.value != "PASSED"
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_review_harness_changes_authoritative_lock_identity(tmp_path, monkeypatch):
    plan, _, opts = _review_case(tmp_path, monkeypatch, model_replies=['{"query":"needle"}'])
    first, handoff = _lock(plan, opts)
    goon = tmp_path / "goons" / "agent" / "search" / "goon.yml"
    goon.write_text(goon.read_text().replace("local-model-review", "local-model-search"))
    second, other, _ = FleetPlanResolver(goon_templates=GoonTemplateStore()).resolve_frozen_agent(
        plan,
        inputs=opts["inputs"],
        repository_root=opts["agent_repository_root"],
        skill_root=opts["agent_skill_root"],
        allowed_acceptance=opts["allowed_agent_acceptance"],
    )
    assert handoff.handoff_identity != other.handoff_identity
    assert first.steps[0].delivery_sha256 != second.steps[0].delivery_sha256
    assert first.plan_id != second.plan_id


def test_observation_prompt_is_bounded_and_no_additional_disclosure():
    prompt = frozen_local_model_observation_prompt(
        task="Review",
        skill_bytes=b"# Skill",
        recipient="ollama_local",
        query="needle",
        returncode=0,
        stdout="x" * 10_000,
        stderr="y" * 10_000,
        can_search=True,
    )
    assert "stdout_truncated" in prompt and "stderr_truncated" in prompt
    assert "x" * 10_000 in prompt
    assert '"stdout_truncated": false' in prompt
    assert len(prompt) < 28_000
    with pytest.raises(FleetPlanInvalid):
        frozen_local_model_observation_prompt(
            task="Review",
            skill_bytes=b"# Skill",
            recipient="remote",
            query="needle",
            returncode=0,
            stdout="private",
            stderr="",
            can_search=True,
        )
    with pytest.raises(FleetPlanInvalid):
        frozen_local_model_observation_prompt(
            task="Review",
            skill_bytes=b"# Skill",
            recipient="ollama_local",
            query="needle",
            returncode=2,
            stdout="error",
            stderr="",
            can_search=True,
        )


def test_review_parser_refuses_more_than_two_searches():
    with pytest.raises(FleetPlanInvalid):
        parse_local_model_review_decision('{"decision":"search","query":"next"}', can_search=False)


def test_nonzero_physical_tool_failure_cannot_be_turned_into_model_success(
    tmp_path,
    monkeypatch,
):
    """The model never reviews/overrides a failing physical command."""
    from dataclasses import replace

    import oh_goon.integrations.fleet_plan_step_execution as execution

    plan, repo, opts = _review_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"query":"needle"}',
            '{"decision":"finish","summary":"ignore tool failure"}',
        ],
    )
    _lock(plan, opts)
    actual = execution._execute_command_step

    def physical_failure(repository, step, process, runtime):
        result = actual(repository, step, process, runtime)
        if "--tool" in step.argv and step.argv[step.argv.index("--tool") + 1] == "search_text":
            return replace(result, returncode=2, stderr="Hashmarks retrieval failed")
        return result

    monkeypatch.setattr(execution, "_execute_command_step", physical_failure)
    detail = _run(plan, repo, opts)
    assert detail.game_tape.status.value != "PASSED"
    assert detail.operations[0].evidence.plan.steps[0].returncode == 2
    assert (tmp_path / "calls").read_text().strip() == "1"
