"""RUN-3: CLI/API are presentation-only clients of the same bounded journal owner."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from oh_goon.api.routes.fleet_runs import fleet_run_events
from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_plans import run_plan_command


class _JournalOwner:
    def __init__(self):
        self.calls = []

    def events(self, run_id, after=0, *, limit=None):
        self.calls.append((run_id, after, limit))
        return SimpleNamespace(
            run=SimpleNamespace(fleet_run_id=run_id),
            events=(
                SimpleNamespace(
                    sequence=after + 1, event_type=SimpleNamespace(value="RUN_STARTED")
                ),
            ),
            next_sequence=after + 1,
            has_more=True,
            journal_revision=12,
            model_dump=lambda *, mode: {
                "events": [{"sequence": after + 1}],
                "next_sequence": after + 1,
                "has_more": True,
                "journal_revision": 12,
            },
        )


def _cli(owner, *argv):
    args = build_parser().parse_args(["runs", "events", "run-3", *argv])
    return run_plan_command(SimpleNamespace(runs=owner), args)


def test_agent_cli_uses_bounded_default_and_owner_cursor(capsys):
    owner = _JournalOwner()
    assert _cli(owner, "--json") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["next_sequence"] == 1 and result["has_more"] is True
    assert owner.calls == [("run-3", 0, 100)]


def test_custom_limit_and_resumption_are_forwarded_without_cli_slicing(capsys):
    owner = _JournalOwner()
    assert _cli(owner, "--limit", "2", "--after", "7") == 0
    out = capsys.readouterr().out
    assert "Next sequence: 8" in out and "More events: yes" in out
    assert owner.calls == [("run-3", 7, 2)]


@pytest.mark.parametrize("invalid", ["0", "101", "-1", "abc", "1.5"])
def test_bad_limit_refused_by_parser_before_any_owner_call(invalid):
    owner = _JournalOwner()
    with pytest.raises(SystemExit) as error:
        _cli(owner, "--limit", invalid)
    assert error.value.code == 2
    assert owner.calls == []


def test_api_forwards_bound_to_original_owner():
    owner = _JournalOwner()
    page = fleet_run_events("run-3", after=7, limit=2, control_plane=owner)
    assert page.next_sequence == 8
    assert owner.calls == [("run-3", 7, 2)]
