from __future__ import annotations

import subprocess
from pathlib import Path

from oh_goon import workflow


def test_run_plan_transports_exact_bootstrap_lock_into_locked_execution(
    tmp_path: Path, monkeypatch
) -> None:
    backend_calls: list[list[str]] = []
    streamed: list[list[str]] = []

    def fake_backend_cli(args: list[str], *, env: dict[str, str]):
        backend_calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="")

    def fake_stream(args: list[str], *, env: dict[str, str]):
        streamed.append(list(args))
        return 0, ""

    monkeypatch.setattr(workflow, "_backend_cli", fake_backend_cli)
    monkeypatch.setattr(workflow, "_stream", fake_stream)
    monkeypatch.setattr(workflow, "_backend_cli_executable", lambda: "oh-goon")

    plan = "templates/plans/oh-goon/app-certification/plan.yml"
    rc, _tail = workflow._run_plan(
        plan=plan,
        repo_id="repo-1",
        env={},
        lock_root=tmp_path / "locks",
        inputs={"source": "exact"},
    )
    assert rc == 0
    lock_command = backend_calls[1]
    assert lock_command[:2] == ["lock", plan]
    produced = Path(lock_command[lock_command.index("--output") + 1])
    execution = streamed[0]
    consumed = Path(execution[execution.index("--lock-file") + 1])
    assert consumed == produced


def test_workflow_architecture_is_invariant_to_private_lock_variable_names(tmp_path: Path) -> None:
    from oh_goon.repository_tooling.architecture._structural_authority_workflow import (
        check_workflow_runner_boundary,
    )

    source = Path(workflow.__file__).read_text(encoding="utf-8")
    renamed = (
        source.replace("lock_root", "certification_state_root")
        .replace("lock_path", "transported_lock")
        .replace("bootstrap_commands", "preflight_commands")
    )
    target = tmp_path / "backend/src/oh_goon/workflow.py"
    target.parent.mkdir(parents=True)
    target.write_text(renamed, encoding="utf-8")
    assert check_workflow_runner_boundary(tmp_path) == []
