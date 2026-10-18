from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.repository_tooling import dev_runtime_acceptance
from oh_goon.runtime.local_launch_plan import contributor_dev_plan


def test_dev_has_one_python_frontdoor_and_plan_owns_exact_commands() -> None:
    root = Path(__file__).resolve().parents[2]
    dev = (root / "scripts/dev.sh").read_text(encoding="utf-8")
    plan = contributor_dev_plan(root)
    assert "oh-goon-local-dev-launch" in dev
    assert "uvicorn" not in dev
    assert "npm run dev" not in dev
    assert "observe-ready" not in dev
    assert "--reload-dir" in plan.services[0].command
    assert plan.intent().services[0].command == plan.services[0].command


def test_acceptance_refuses_to_compete_for_owned_runtime_port(
    monkeypatch, tmp_path: Path
) -> None:
    vite = tmp_path / "frontend" / "node_modules" / ".bin" / "vite"
    vite.parent.mkdir(parents=True)
    vite.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        dev_runtime_acceptance,
        "_port_is_free",
        lambda port: port != dev_runtime_acceptance.FRONTEND_PORT,
    )
    assert dev_runtime_acceptance.accept_dev_runtime(tmp_path) == (
        "local-access runtime acceptance requires free loopback ports before startup: 5173",
    )


def test_acceptance_delegates_process_start_failure_to_native_launch_owner(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        dev_runtime_acceptance,
        "prepare_local_runtime_environment",
        lambda **_kwargs: ({}, False, None),
    )

    def fail_native_launch(*_args, **_kwargs):
        raise FileNotFoundError("native launch executable unavailable")

    monkeypatch.setattr(dev_runtime_acceptance, "_launch_dev", fail_native_launch)
    with pytest.raises(FileNotFoundError, match="native launch executable unavailable"):
        dev_runtime_acceptance.accept_dev_runtime(tmp_path)
