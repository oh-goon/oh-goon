"""RUN-2: original Fleet projections, not a second status/log/journal owner."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_plans import run_plan_command
from oh_goon.core.errors import InvalidRequest
from oh_goon.core.vocabulary import FleetRunOutcome, FleetRunStatus


class _Dump:
    def __init__(self, value):
        self.value = value

    def model_dump(self, *, mode):
        assert mode == "json"
        return self.value


class _OriginalRuns:
    def __init__(self):
        self.calls = []
        self.reject = False
        self.non_plan_target = False

    def get(self, run_id):
        self.calls.append(("get", run_id))
        if self.reject:
            raise InvalidRequest("unknown Fleet Run")
        return SimpleNamespace(
            run=SimpleNamespace(
                fleet_run_id=run_id,
                target=(
                    SimpleNamespace(kind="recipe")
                    if self.non_plan_target
                    else SimpleNamespace(plan=SimpleNamespace(plan_id="p"))
                ),
                status=FleetRunStatus.COMPLETED,
                progress=SimpleNamespace(
                    model_dump=lambda *, mode: {"total": 2, "succeeded": 2},
                    completed=2,
                    total=2,
                ),
            ),
            outcome=FleetRunOutcome.SUCCESS,
        )

    def agent_campaign(self, run_id):
        self.calls.append(("campaign", run_id))
        if self.reject:
            raise InvalidRequest("Selected Run is not part of a review-repair campaign")
        return {
            "schema": "oh-goon.agent-campaign-result/v1",
            "campaign_id": "first-run",
            "result": "incomplete",
            "controller_result": {"availability": "unavailable", "stop_reason": None},
            "incomplete_reasons": ["The controller stop receipt is not retained"],
            "runs": [
                {
                    "fleet_run_id": run_id,
                    "phase": "repair",
                    "review_pass": 1,
                    "status": "CANCELLED",
                    "outcome": "CANCELLED",
                }
            ],
            "reviews": [],
            "findings": [],
        }

    def events(self, run_id, after, *, limit=None):
        self.calls.append(("events", run_id, after, limit))
        return SimpleNamespace(
            run=SimpleNamespace(fleet_run_id=run_id),
            events=(
                SimpleNamespace(sequence=7, event_type=SimpleNamespace(value="RUN_COMPLETED")),
            ),
            next_sequence=7,
            journal_revision=80,
            has_more=False,
            model_dump=lambda *, mode: {
                "run": {"fleet_run_id": run_id},
                "events": [{"sequence": 7}],
                "next_sequence": 7,
                "journal_revision": 80,
                "has_more": False,
            },
        )

    def game_tape_live_output(self, run_id, *, after):
        self.calls.append(("live-output", run_id, after))
        return SimpleNamespace(
            fleet_run_id=run_id,
            state="UNAVAILABLE",
            available=False,
            chunks=(),
            next_sequence=after,
            model_dump=lambda *, mode: {
                "fleet_run_id": run_id,
                "state": "UNAVAILABLE",
                "available": False,
                "next_sequence": after,
            },
        )


def _invoke(runs, *tail):
    args = build_parser().parse_args(["plan", *tail])
    return run_plan_command(SimpleNamespace(runs=runs), args)


def test_status_projects_existing_owner_outcome_and_identity(capsys):
    runs = _OriginalRuns()
    assert _invoke(runs, "status", "run-x", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "schema": "oh-goon.plan-run-status/v1",
        "fleet_run_id": "run-x",
        "plan_id": "p",
        "status": "COMPLETED",
        "outcome": "SUCCESS",
        "progress": {"total": 2, "succeeded": 2},
    }
    assert runs.calls == [("get", "run-x")]


def test_status_text_keeps_same_projection_without_an_extra_read(capsys):
    runs = _OriginalRuns()
    assert _invoke(runs, "status", "run-x") == 0
    assert "Progress: 2/2" in capsys.readouterr().out
    assert runs.calls == [("get", "run-x")]


@pytest.mark.parametrize("as_json", [False, True])
def test_campaign_readback_preserves_original_result_and_interrupt_uncertainty(capsys, as_json):
    runs = _OriginalRuns()
    assert _invoke(runs, "campaign", "linked-run", *(["--json"] if as_json else [])) == 0
    output = capsys.readouterr().out
    if as_json:
        data = json.loads(output)
        assert data["campaign_id"] == "first-run" and data["result"] == "incomplete"
        assert data["controller_result"]["availability"] == "unavailable"
        assert data["runs"][0]["status"] == "CANCELLED"
    else:
        assert "Result: incomplete" in output and "controller stop receipt" in output
    assert runs.calls == [("campaign", "linked-run")]


def test_campaign_readback_rejects_a_run_without_campaign_authority(capsys):
    runs = _OriginalRuns()
    runs.reject = True
    assert _invoke(runs, "campaign", "ordinary-run", "--json") == 2
    assert "not part of" in json.loads(capsys.readouterr().out)["message"]


def test_campaign_invocation_output_preserves_cancelled_before_admission(capsys):
    from oh_goon.cli_runtime_plans import _print_agent_repair_result
    from oh_goon.core.models.agent_repair import (
        AgentRepairBudget,
        AgentRepairResult,
        AgentRepairStopReason,
    )

    receipt = AgentRepairResult(
        repair_id="cancelled-before-admission",
        budget=AgentRepairBudget(max_attempts=2, timeout_seconds=60, step_timeout_seconds=10),
        stop_reason=AgentRepairStopReason.CANCELLED,
        attempts=(),
    )
    assert _print_agent_repair_result(receipt, as_json=True, campaign=True) == 130
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == "oh-goon.agent-repair-result/v1"
    assert result["campaign"]["schema"] == "oh-goon.agent-campaign-result/v2"
    assert result["campaign"]["result"] == "incomplete"
    assert result["stop_reason"] == "cancelled"
    assert result["campaign"]["controller_result"]["stop_reason"] is None
    assert result["campaign"]["controller_result"]["availability"] == "unavailable"
    assert result["campaign"]["runs"] == []


def test_events_preserves_original_sequence_and_revision_without_recomputation(capsys):
    runs = _OriginalRuns()
    assert _invoke(runs, "events", "run-x", "--after", "6", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["next_sequence"] == 7 and data["journal_revision"] == 80
    assert runs.calls == [("events", "run-x", 6, 100)]


def test_events_text_preserves_same_cursor(capsys):
    runs = _OriginalRuns()
    assert _invoke(runs, "events", "run-x") == 0
    assert "Next sequence: 7" in capsys.readouterr().out
    assert runs.calls == [("events", "run-x", 0, 100)]


def test_logs_unavailable_is_not_falsely_presented_as_empty_durable_history(capsys):
    runs = _OriginalRuns()
    assert _invoke(runs, "logs", "run-x", "--after", "9", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["state"] == "UNAVAILABLE" and data["available"] is False
    assert runs.calls == [("live-output", "run-x", 9)]


def test_logs_text_explains_transient_retention(capsys):
    runs = _OriginalRuns()
    assert _invoke(runs, "logs", "run-x") == 0
    assert "Transient output unavailable" in capsys.readouterr().out


def test_unknown_id_returns_error_without_disclosing_false_no_effects(capsys):
    runs = _OriginalRuns()
    runs.reject = True
    assert _invoke(runs, "status", "unknown", "--json") == 2
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "error" and data["fleet_run_id"] == "unknown"
    assert "no_effects" not in data


@pytest.mark.parametrize("command", ["logs", "events"])
def test_invalid_cursor_rejected_by_cli_before_runtime(monkeypatch, command):
    runs = _OriginalRuns()
    with pytest.raises(SystemExit) as exc:
        _invoke(runs, command, "run-x", "--after", "-1")
    assert exc.value.code == 2
    assert runs.calls == []


def test_readback_never_starts_plan_or_executor(capsys):
    runs = _OriginalRuns()
    runtime = SimpleNamespace(runs=runs, plans=SimpleNamespace())
    args = build_parser().parse_args(["plan", "status", "run-x", "--json"])
    assert run_plan_command(runtime, args) == 0
    assert json.loads(capsys.readouterr().out)["fleet_run_id"] == "run-x"


def test_status_other_fleet_run_target_has_no_plan_but_reuses_owner(capsys):
    runs = _OriginalRuns()
    runs.non_plan_target = True
    assert _invoke(runs, "status", "recipe-run", "--json") == 0
    value = json.loads(capsys.readouterr().out)
    assert value["plan_id"] is None
    assert value["fleet_run_id"] == "recipe-run"
    assert runs.calls == [("get", "recipe-run")]


def test_event_cursor_replay_is_exact_not_auto_advanced(capsys):
    runs = _OriginalRuns()
    assert _invoke(runs, "events", "run-x", "--after", "7", "--json") == 0
    assert json.loads(capsys.readouterr().out)["next_sequence"] == 7
    assert runs.calls == [("events", "run-x", 7, 100)]
