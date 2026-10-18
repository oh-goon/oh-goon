"""AGENT-01B3E: a single captured-file read through the original Fleet supervisor."""
from __future__ import annotations

import hashlib

import pytest

from oh_goon.core.agent_model_selection import (
    LocalModelToolAction, parse_local_model_tool_action,
    parse_local_model_tool_review_decision,
)
from oh_goon.core.errors import FleetPlanInvalid
from tests.integration.test_agent01b2b_original_fleet import _lock
from tests.integration.test_agent01b3b_original_fleet_model_observation import _run
from tests.integration.test_agent01b3d_original_fleet_tool_selection import _tools_case


def _files_case(tmp_path, monkeypatch, *, model_replies, **kwargs):
    plan, repo, opts = _tools_case(
        tmp_path, monkeypatch, model_replies=model_replies, **kwargs,
    )
    goon = tmp_path / "goons" / "agent" / "search" / "goon.yml"
    goon.write_text(goon.read_text().replace("local-model-tools", "local-model-files"))
    return plan, repo, opts


def test_exact_frozen_file_read_then_evaluation_persists(tmp_path, monkeypatch):
    plan, repo, opts = _files_case(tmp_path, monkeypatch, model_replies=[
        '{"tool":"read_file","path":"repo/code.txt"}',
        '{"decision":"finish","summary":"Examined frozen code"}',
    ])
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3e-file")
    assert detail.game_tape.status.value == "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "2"
    prompt = (tmp_path / "prompt-2.txt").read_text()
    assert '"path": "repo/code.txt"' in prompt
    assert 'needle from real original Fleet' in prompt
    assert 'needle from real original Fleet' not in (tmp_path / "prompt-1.txt").read_text()
    assert detail.operations[0].evidence.plan.steps[0].returncode == 0


def test_list_then_read_exact_path_and_final_evaluation(tmp_path, monkeypatch):
    plan, repo, opts = _files_case(tmp_path, monkeypatch, model_replies=[
        '{"tool":"list_paths"}',
        '{"decision":"tool","tool":"read_file","path":"repo/code.txt"}',
        '{"decision":"finish","summary":"Confirmed captured file"}',
    ])
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3e-two")
    assert detail.game_tape.status.value == "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "3"
    assert 'repo/code.txt' in (tmp_path / 'prompt-2.txt').read_text()
    assert 'needle from real original Fleet' in (tmp_path / 'prompt-3.txt').read_text()


@pytest.mark.parametrize("payload", [
    '{"tool":"read_file","path":"/etc/passwd"}',
    '{"tool":"read_file","path":"repo/../skill/SKILL.md"}',
    '{"tool":"read_file","path":"repo//code.txt"}',
    '{"tool":"read_file","path":"repo/./code.txt"}',
    '{"tool":"read_file","path":"repo/missing.txt"}',
    '{"tool":"read_file","path":"repo/code*.txt"}',
    '{"tool":"read_file","path":"repo/code.txt","command":"sh"}',
    '{"tool":"read_file","path":"repo\\\\code.txt"}',
    '{"tool":"read_file","path":"repo/code.txt","path":"repo/other.txt"}',
])
def test_invalid_read_does_not_dispatch_or_pass(tmp_path, monkeypatch, payload):
    plan, repo, opts = _files_case(tmp_path, monkeypatch, model_replies=[payload])
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3e-invalid")
    assert detail.game_tape.status.value != "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "1"


def test_legacy_harness_does_not_accept_new_tool():
    with pytest.raises(FleetPlanInvalid):
        parse_local_model_tool_action('{"tool":"read_file","path":"repo/code.txt"}')
    with pytest.raises(FleetPlanInvalid):
        parse_local_model_tool_review_decision(
            '{"decision":"tool","tool":"read_file","path":"repo/code.txt"}',
            can_continue=True,
        )
    assert parse_local_model_tool_action(
        '{"tool":"read_file","path":"repo/code.txt"}', allow_file=True,
    ) == LocalModelToolAction(tool="read_file", path="repo/code.txt")


def test_duplicate_file_action_rejected_before_third_tool(tmp_path, monkeypatch):
    plan, repo, opts = _files_case(tmp_path, monkeypatch, model_replies=[
        '{"tool":"read_file","path":"repo/code.txt"}',
        '{"decision":"tool","tool":"read_file","path":"repo/code.txt"}',
    ])
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3e-repeat")
    assert detail.game_tape.status.value != "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "2"


def test_third_file_action_rejected(tmp_path, monkeypatch):
    plan, repo, opts = _files_case(tmp_path, monkeypatch, model_replies=[
        '{"tool":"list_paths"}',
        '{"decision":"tool","tool":"read_file","path":"repo/code.txt"}',
        '{"decision":"tool","tool":"search_text","query":"x"}',
    ])
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3e-third")
    assert detail.game_tape.status.value != "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "3"


def test_file_read_output_is_physically_bounded_before_model_disclosure(tmp_path, monkeypatch):
    plan, repo, opts = _files_case(tmp_path, monkeypatch, model_replies=[
        '{"tool":"read_file","path":"repo/code.txt"}',
        '{"decision":"finish","summary":"Read bounded source"}',
    ])
    # Extend the real committed source BEFORE frozen capture; the supervisor may
    # read only the first 2 KiB, not capture an unbounded file for later trimming.
    code = repo / 'code.txt'
    code.write_text('A' * 5000 + 'TAIL_SENTINEL_NOT_DISCLOSED')
    from subprocess import run
    run(['git', '-C', str(repo), 'add', 'code.txt'], check=True)
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id='agent01b3e-bounded')
    assert detail.game_tape.status.value == 'PASSED'
    prompt = (tmp_path / 'prompt-2.txt').read_text()
    assert 'A' * 2048 in prompt
    assert 'A' * 2049 not in prompt
    assert 'TAIL_SENTINEL_NOT_DISCLOSED' not in prompt
    assert '"stdout_truncated": false' in prompt
