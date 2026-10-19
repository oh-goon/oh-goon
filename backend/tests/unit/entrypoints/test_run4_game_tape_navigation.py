"""RUN-4: all navigation is transport of one original archived Game Tape."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_plans import run_plan_command
from oh_goon.core.errors import InvalidRequest


class _Fact:
    def __init__(self, **items):
        self.data = items
        for key, value in items.items():
            setattr(self, key, value)

    def model_dump(self, *, mode):
        assert mode == "json"
        return dict(self.data)


class _Tape:
    def __init__(self, run_id):
        self.fleet_run_id = run_id
        self.status = SimpleNamespace(value="ATTENTION_REQUIRED")
        self.issue_focus = (
            _Fact(
                scope="STEP",
                repository_execution_id="repo-run-a",
                step_execution_id="step-run-a",
                code="FAILED_AUTHORITY",
            ),
        )
        step = SimpleNamespace(
            step_execution_id="step-run-a",
            step_id="verify",
            order=2,
            status=SimpleNamespace(value="FAILED"),
            output_names=("build",),
            outputs=(_Fact(name="build", sha256="a" * 64, byte_count=18),),
            artifacts=(_Fact(status="PRESENT", path="build/receipt.txt"),),
            stdout_evidence=_Fact(sha256="b" * 64, byte_count=200, line_count=3),
            stderr_evidence=None,
            model_dump=lambda *, mode: {
                "step_execution_id": "step-run-a",
                "step_id": "verify",
                "status": "FAILED",
                "why": {"reached": True},
                "outputs": [{"name": "build", "sha256": "a" * 64}],
                "failure": {"signature_id": "signed-canonical"},
            },
        )
        untouched = SimpleNamespace(
            step_execution_id="step-run-b",
            step_id="package",
            order=3,
            status=SimpleNamespace(value="NOT_RUN"),
            output_names=(),
            outputs=(),
            artifacts=(),
            stdout_evidence=None,
            stderr_evidence=None,
            model_dump=lambda *, mode: {"step_execution_id": "step-run-b", "status": "NOT_RUN"},
        )
        self.repositories = (
            SimpleNamespace(
                repository_execution_id="repo-run-a",
                repository=_Fact(name="repo", repository_id="repo-123", path="/repo"),
                status=SimpleNamespace(value="FAILED"),
                steps=(step, untouched),
            ),
        )

    def model_dump(self, *, mode):
        assert mode == "json"
        return {
            "fleet_run_id": self.fleet_run_id,
            "status": "ATTENTION_REQUIRED",
            "repositories": [{"repository_execution_id": "repo-run-a"}],
            "issue_focus": [{"code": "FAILED_AUTHORITY"}],
            "storage": {"retention": "canonical"},
        }


class _Runs:
    def __init__(self):
        self.calls = []
        self.reject = False
        self.tape = _Tape("run-a")

    def get(self, run_id):
        self.calls.append(("get", run_id))
        if self.reject:
            raise InvalidRequest("unknown Fleet Run")
        return SimpleNamespace(game_tape=self.tape)


def _run(runs, *args):
    parser = build_parser()
    parsed = parser.parse_args(["plan", *args])
    return run_plan_command(SimpleNamespace(runs=runs), parsed)


def test_tape_returns_the_original_model_without_reprojection(capsys):
    runs = _Runs()
    assert _run(runs, "tape", "run-a", "--json") == 0
    assert json.loads(capsys.readouterr().out) == runs.tape.model_dump(mode="json")
    assert runs.calls == [("get", "run-a")]


def test_steps_preserves_exact_original_why_failure_and_issue_links(capsys):
    runs = _Runs()
    assert _run(runs, "steps", "run-a", "--json") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["fleet_run_id"] == "run-a"
    assert result["issue_focus"] == [f.model_dump(mode="json") for f in runs.tape.issue_focus]
    repo = result["repositories"][0]
    assert repo["repository_execution_id"] == "repo-run-a"
    assert repo["repository"]["repository_id"] == "repo-123"
    assert repo["steps"] == [s.model_dump(mode="json") for s in runs.tape.repositories[0].steps]
    assert runs.calls == [("get", "run-a")]


def test_outputs_preserves_canonical_evidence_but_never_invents_raw_log_bytes(capsys):
    runs = _Runs()
    assert _run(runs, "outputs", "run-a", "--json") == 0
    result = json.loads(capsys.readouterr().out)
    one, two = result["repositories"][0]["steps"]
    assert one == {
        "step_execution_id": "step-run-a",
        "step_id": "verify",
        "output_names": ["build"],
        "outputs": [{"name": "build", "sha256": "a" * 64, "byte_count": 18}],
        "artifacts": [{"status": "PRESENT", "path": "build/receipt.txt"}],
        "stdout_evidence": {"sha256": "b" * 64, "byte_count": 200, "line_count": 3},
        "stderr_evidence": None,
    }
    assert two["outputs"] == [] and two["artifacts"] == []
    assert two["stdout_evidence"] is None
    assert "raw_log" not in json.dumps(result)
    assert runs.calls == [("get", "run-a")]


@pytest.mark.parametrize("command", ["tape", "steps", "outputs"])
def test_text_navigation_preserves_original_execution_ids(command, capsys):
    runs = _Runs()
    assert _run(runs, command, "run-a") == 0
    displayed = capsys.readouterr().out
    assert "run-a" in displayed and "repo-run-a" in displayed
    if command in {"tape", "steps"}:
        assert "step-run-a" in displayed
    if command == "outputs":
        assert "raw output" in displayed
    assert runs.calls == [("get", "run-a")]


@pytest.mark.parametrize("command", ["tape", "steps", "outputs"])
def test_unknown_run_fails_through_original_fleet_error_path(command, capsys):
    runs = _Runs()
    runs.reject = True
    assert _run(runs, command, "unknown", "--json") == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error" and result["fleet_run_id"] == "unknown"
    assert "no_effects" not in result
    assert runs.calls == [("get", "unknown")]


def test_readback_does_not_need_plan_executor_or_second_journal_reader(capsys):
    runs = _Runs()
    for command in ("tape", "steps", "outputs"):
        assert _run(runs, command, "run-a", "--json") == 0
    assert len(capsys.readouterr().out.splitlines()) == 3
    assert runs.calls == [("get", "run-a")] * 3
