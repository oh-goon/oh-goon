"""RUN-6: prove one agent can resume RUN-1..5 with original read owners.

These fixtures are explicit owner transport witnesses, not a second reducer,
executor, trust authority, retry decision, or simulated kernel containment.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_plans import run_plan_command
from oh_goon.core.errors import InvalidRequest
from oh_goon.core.vocabulary import FleetRunOutcome, FleetRunStatus


class _Facts:
    def __init__(self, data):
        self.data = data

    def model_dump(self, *, mode):
        assert mode == "json"
        return self.data


class _Tape:
    def __init__(self, run_id, *, stage="ADMISSION"):
        self.fleet_run_id = run_id
        self.plan_id = "plan-one"
        self.status = SimpleNamespace(value="FAILED")
        self.receipt = _Facts({"validation": "canonical-receipt"})
        self.summary = _Facts({"failed_steps": 1})
        self.observability = _Facts({"raw_output_survives_process_restart": False})
        self.issue_focus = (
            _Facts(
                {
                    "scope": "STEP",
                    "step_execution_id": "step-one",
                    "repository_execution_id": "repo-one",
                    "code": "CANONICAL_FAILURE",
                }
            ),
        )
        self.issue_count = 1
        self.hidden_issue_count = 0
        self.why_not = _Facts(
            {
                "stage": stage,
                "retryable": False,
                "safe_next_action": "Inspect original authorization receipt",
            }
        )
        self.enforcer_receipt_available = False
        step = SimpleNamespace(
            step_id="verify",
            order=1,
            step_execution_id="step-one",
            status=SimpleNamespace(value="FAILED"),
            output_names=("receipt",),
            outputs=(_Facts({"name": "receipt", "sha256": "a" * 64}),),
            artifacts=(),
            stdout_evidence=_Facts({"sha256": "b" * 64}),
            stderr_evidence=None,
            model_dump=lambda *, mode: {
                "step_execution_id": "step-one",
                "status": "FAILED",
                "failure": "original",
            },
        )
        repo = SimpleNamespace(
            repository_execution_id="repo-one",
            repository=SimpleNamespace(name="repo", model_dump=lambda *, mode: {"name": "repo"}),
            status=SimpleNamespace(value="FAILED"),
            steps=(step,),
        )
        self.repositories = (repo,)

    def model_dump(self, *, mode):
        assert mode == "json"
        return {
            "fleet_run_id": self.fleet_run_id,
            "repositories": [{"repository_execution_id": "repo-one"}],
            "status": self.status.value,
        }


class _CanonicalRuns:
    def __init__(self, run_id, *, stage="ADMISSION"):
        self.run_id = run_id
        self.calls = []
        self.reject = False
        self.tape = _Tape(run_id, stage=stage)

    def get(self, run_id):
        self.calls.append(("get", run_id))
        if self.reject:
            raise InvalidRequest("original owner: Run unknown")
        assert run_id == self.run_id
        return SimpleNamespace(
            run=SimpleNamespace(
                fleet_run_id=run_id,
                target=SimpleNamespace(plan=SimpleNamespace(plan_id="plan-one")),
                status=FleetRunStatus.FAILED,
                progress=SimpleNamespace(
                    completed=1, total=1, model_dump=lambda *, mode: {"completed": 1, "total": 1}
                ),
            ),
            outcome=FleetRunOutcome.FAILED,
            game_tape=self.tape,
        )

    def events(self, run_id, after=0, *, limit=None):
        self.calls.append(("events", run_id, after, limit))
        assert run_id == self.run_id
        assert limit is not None and 1 <= limit <= 100
        assert after in (0, 4)
        next_sequence = 4 if after == 0 else 8
        has_more = after == 0
        return SimpleNamespace(
            run=SimpleNamespace(fleet_run_id=run_id),
            events=(
                SimpleNamespace(
                    sequence=next_sequence, event_type=SimpleNamespace(value="ORIGINAL_EVENT")
                ),
            ),
            next_sequence=next_sequence,
            has_more=has_more,
            journal_revision=100,
            model_dump=lambda *, mode: {
                "events": [{"sequence": next_sequence, "code": "ORIGINAL_EVENT"}],
                "next_sequence": next_sequence,
                "has_more": has_more,
                "journal_revision": 100,
            },
        )

    def game_tape_live_output(self, run_id, *, after):
        self.calls.append(("live-output", run_id, after))
        assert run_id == self.run_id
        return SimpleNamespace(
            fleet_run_id=run_id,
            state="UNAVAILABLE",
            chunks=(),
            available=False,
            next_sequence=after,
            model_dump=lambda *, mode: {
                "fleet_run_id": run_id,
                "available": False,
                "state": "UNAVAILABLE",
                "next_sequence": after,
            },
        )

    def __getattr__(self, name):
        raise AssertionError(f"Unexpected authority/mutation call: {name}")


class _CanonicalPlans:
    def __init__(self, run_id):
        self.run_id = run_id
        self.calls = []

    def require_cli_plan_preparation(self, run_id):
        self.calls.append(("preflight", run_id))
        assert run_id == self.run_id

    def run(self, path, **kwargs):
        self.calls.append(("run", str(path)))
        assert kwargs["fleet_run_id"] == self.run_id
        return SimpleNamespace(
            fleet_run_id=self.run_id,
            status=FleetRunStatus.QUEUED,
            target=SimpleNamespace(plan=SimpleNamespace(plan_id="plan-one")),
        )

    def __getattr__(self, name):
        raise AssertionError(f"Unexpected second Plan owner: {name}")


def _invoke(runtime, capsys, argv):
    args = build_parser().parse_args(argv)
    code = run_plan_command(runtime, args)
    streams = capsys.readouterr()
    assert streams.err == ""
    assert len(streams.out.strip().splitlines()) == 1, streams.out
    return code, json.loads(streams.out)


def test_full_agent_run_id_resume_and_existing_evidence_views(capsys):
    """Submit once, resume by Run ID, then inspect without a second executor."""
    run_id = "fleet-run6-identity"
    plans = _CanonicalPlans(run_id)
    runs = _CanonicalRuns(run_id)
    runtime = SimpleNamespace(plans=plans, runs=runs)
    code, accepted = _invoke(
        runtime,
        capsys,
        [
            "plan",
            "run",
            "plan.yml",
            "--repository-id",
            "repo-1",
            "--fleet-run-id",
            run_id,
            "--json",
            "--no-wait",
        ],
    )
    assert code == 0
    assert accepted["fleet_run_id"] == run_id
    assert accepted["status"] == "QUEUED" and accepted["outcome"] is None
    assert [c[0] for c in plans.calls] == ["preflight", "run"]

    code, handoff = _invoke(runtime, capsys, ["plan", "handoff", run_id, "--json"])
    assert code == 0
    assert handoff["fleet_run_id"] == run_id
    assert handoff["status"] == "FAILED"
    assert handoff["outcome"] == "FAILED"
    assert handoff["issue_focus"] == [x.model_dump(mode="json") for x in runs.tape.issue_focus]
    assert handoff["why_not"] == runs.tape.why_not.model_dump(mode="json")
    views = handoff["readback"]
    assert set(views) == {"status", "events", "tape", "steps", "outputs", "logs"}
    assert views["logs"]["evidence_kind"] == "transient_live_output"
    assert views["logs"]["availability"] == "not_checked"
    assert runs.calls == [("get", run_id)]  # handoff must NOT probe transient logs

    for name, reference in views.items():
        argv = reference["argv"]
        assert argv[:3] == ["oh-goon", "plan", name]
        assert argv[3] == run_id
        assert argv[-1] == "--json"
        code, response = _invoke(runtime, capsys, argv[1:])
        assert code == 0
        if name == "status":
            assert response["outcome"] == "FAILED"
        elif name == "events":
            assert response["next_sequence"] == 4 and response["has_more"] is True
            # The event page's own cursor (not Game Tape or logs) resumes exactly.
            next_argv = list(argv[1:])
            offset = next_argv.index("--after")
            next_argv[offset + 1] = str(response[reference["resume_cursor_field"]])
            _, resumed = _invoke(runtime, capsys, next_argv)
            assert resumed["next_sequence"] == 8 and resumed["has_more"] is False
        elif name == "tape":
            assert response == runs.tape.model_dump(mode="json")
        elif name == "steps":
            assert response["repositories"][0]["steps"][0]["step_execution_id"] == "step-one"
        elif name == "outputs":
            step = response["repositories"][0]["steps"][0]
            assert step["outputs"] == [{"name": "receipt", "sha256": "a" * 64}]
            assert step["stdout_evidence"] == {"sha256": "b" * 64}
        elif name == "logs":
            assert response["state"] == "UNAVAILABLE"
            assert response["available"] is False
            assert response["next_sequence"] == 0
    assert len(plans.calls) == 2  # all following views are read only
    assert runs.calls == [
        ("get", run_id),  # handoff
        ("events", run_id, 0, 100),
        ("events", run_id, 4, 100),
        ("live-output", run_id, 0),
        ("get", run_id),
        ("get", run_id),
        ("get", run_id),
        ("get", run_id),
    ]


@pytest.mark.parametrize("stage", ["ADMISSION", "CONTAINMENT", "EXECUTION"])
def test_no_repair_policy_or_retry_permission_in_readback_references(capsys, stage):
    run_id = "run-failure"
    runs = _CanonicalRuns(run_id, stage=stage)
    code, handoff = _invoke(
        SimpleNamespace(runs=runs), capsys, ["plan", "handoff", run_id, "--json"]
    )
    assert code == 0
    assert handoff["why_not"]["stage"] == stage
    assert handoff["why_not"]["retryable"] is False
    assert handoff["why_not"]["safe_next_action"] == ("Inspect original authorization receipt")
    nav = json.dumps(handoff["readback"])
    assert "retryable" not in nav and "safe_next_action" not in nav
    assert "repair" not in nav
    assert all(view["argv"][2] != "run" for view in handoff["readback"].values())
    assert runs.calls == [("get", run_id)]


def test_argv_is_not_a_shell_command_even_with_shell_sensitive_run_identity(capsys):
    run_id = "run; echo SHOULD_NOT_EXECUTE"
    runs = _CanonicalRuns(run_id)
    _, handoff = _invoke(SimpleNamespace(runs=runs), capsys, ["plan", "handoff", run_id, "--json"])
    for view in handoff["readback"].values():
        assert view["argv"][3] == run_id
        assert isinstance(view["argv"], list)
        parsed = build_parser().parse_args(view["argv"][1:])
        assert parsed.fleet_run_id == run_id
    assert runs.calls == [("get", run_id)]


def test_unknown_run_does_not_offer_unverified_navigation(capsys):
    runs = _CanonicalRuns("unknown")
    runs.reject = True
    code, result = _invoke(
        SimpleNamespace(runs=runs), capsys, ["plan", "handoff", "unknown", "--json"]
    )
    assert code == 2
    assert result["status"] == "error"
    assert "readback" not in result
    assert runs.calls == [("get", "unknown")]
