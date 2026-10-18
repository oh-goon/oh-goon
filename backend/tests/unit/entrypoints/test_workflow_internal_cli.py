from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

from oh_goon.cli_developer import run_state_free_developer_command
from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_observability import run_observability_command


def test_require_plan_success_delegates_to_backend_evidence_owner(monkeypatch) -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "dev",
            "require-plan-success",
            "--plan",
            "plan.yml",
            "--repository-id",
            "repo-test",
            "--source-root",
            ".",
            "--lock-file",
            "app.goon.lock",
        ]
    )
    calls: list[tuple[Path, str, Path, Path]] = []

    def fake_require(_runtime, *, plan, repository_id, source_root, lock_path):
        calls.append((plan, repository_id, source_root, lock_path))
        return True

    monkeypatch.setattr("oh_goon.cli_runtime_observability.has_exact_plan_success", fake_require)
    runtime = SimpleNamespace()
    assert run_observability_command(runtime, args) == 0
    assert calls == [(Path("plan.yml"), "repo-test", Path("."), Path("app.goon.lock"))]
