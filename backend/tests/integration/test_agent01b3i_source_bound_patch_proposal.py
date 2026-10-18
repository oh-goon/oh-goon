"""AGENT-01B3I: immutable proposal evidence, never repository mutation."""

from __future__ import annotations

import hashlib
import json

import pytest

from tests.integration.test_agent01b2b_original_fleet import _lock
from tests.integration.test_agent01b3b_original_fleet_model_observation import _run
from tests.integration.test_agent01b3g_bounded_skill_loop import _loop_case

_ORIGINAL = b"needle from real original Fleet\n"
_DIGEST = hashlib.sha256(_ORIGINAL).hexdigest()


def _payload(**overrides):
    value = {
        "decision": "propose",
        "path": "repo/code.txt",
        "source_sha256": _DIGEST,
        "replacement": "revised content proposed, not applied\n",
        "summary": "Suggest changing code.txt",
    }
    value.update(overrides)
    return json.dumps(value)


def _case(tmp_path, monkeypatch, *, model_replies):
    plan, repo, opts, _ = _loop_case(tmp_path, monkeypatch, model_replies=model_replies)
    path = tmp_path / "goons" / "agent" / "search" / "goon.yml"
    path.write_text(path.read_text().replace("local-model-skill-loop", "local-model-proposal"))
    return plan, repo, opts


def test_proposal_is_durable_evidence_but_repository_stays_unchanged(tmp_path, monkeypatch):
    import oh_goon.integrations.fleet_plan_step_execution as execution

    recorded = {}
    real = execution._run_local_model_review

    def record(**kwargs):
        outcome = real(**kwargs)
        recorded["stdout"] = outcome.stdout
        return outcome

    monkeypatch.setattr(execution, "_run_local_model_review", record)
    plan, repo, opts = _case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"read_file","path":"repo/code.txt"}',
            _payload(),
        ],
    )
    frozen, handoff = _lock(plan, opts)
    assert frozen.steps[0].harness == handoff.harness == "local-model-proposal"
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3i-proposal")
    assert detail.game_tape.status.value == "PASSED"
    assert (repo / "code.txt").read_bytes() == _ORIGINAL
    assert (tmp_path / "calls").read_text().strip() == "2"
    assert "propose" in (tmp_path / "prompt-2.txt").read_text()
    assert "Agent proposal only; no repository mutation" in recorded["stdout"]
    receipt = json.loads(recorded["stdout"].splitlines()[-1])
    assert receipt == {
        "schema": "agent-patch-proposal-v1",
        "handoff_identity": handoff.handoff_identity,
        "path": "repo/code.txt",
        "source_sha256": _DIGEST,
        "replacement": "revised content proposed, not applied\n",
        "summary": "Suggest changing code.txt",
    }
    assert (
        detail.operations[0].evidence.plan.steps[0].stdout_evidence.sha256
        == hashlib.sha256(recorded["stdout"].encode()).hexdigest()
    )


@pytest.mark.parametrize(
    "reply",
    [
        _payload(source_sha256="0" * 64),
        _payload(path="repo/missing.txt"),
        _payload(path="/etc/passwd"),
        _payload(replacement=""),
        _payload(replacement="needle from real original Fleet\n"),
        _payload(replacement="x" * 2050),
        _payload(summary="line\nbreak"),
        _payload(command="rm -rf repo"),
        '{"decision":"propose","path":"repo/code.txt","path":"repo/other","source_sha256":"'
        + _DIGEST
        + '","replacement":"abc","summary":"ok"}',
    ],
)
def test_invalid_model_proposal_never_becomes_pass_or_mutation(tmp_path, monkeypatch, reply):
    plan, repo, opts = _case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"read_file","path":"repo/code.txt"}',
            reply,
        ],
    )
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3i-invalid")
    assert detail.game_tape.status.value != "PASSED"
    assert (repo / "code.txt").read_bytes() == _ORIGINAL
    assert (tmp_path / "calls").read_text().strip() == "2"


def test_cannot_propose_unread_file_after_listing(tmp_path, monkeypatch):
    plan, repo, opts = _case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"list_paths"}',
            _payload(),
        ],
    )
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3i-unread")
    assert detail.game_tape.status.value != "PASSED"
    assert (repo / "code.txt").read_bytes() == _ORIGINAL


def test_other_harness_retains_previous_model_protocol(tmp_path, monkeypatch):
    plan, repo, opts, _ = _loop_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"read_file","path":"repo/code.txt"}',
            _payload(),
        ],
    )
    _lock(plan, opts)
    detail = _run(plan, repo, opts, fleet_run_id="agent01b3i-older")
    assert detail.game_tape.status.value != "PASSED"
    assert (repo / "code.txt").read_bytes() == _ORIGINAL
