from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from oh_goon.repository_tooling import local_dev_launch
from oh_goon.runtime.local_launch_plan import contributor_dev_plan


def test_contributor_plan_is_exact_spawn_and_readiness_authority(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    plan = contributor_dev_plan(root)
    backend, frontend = plan.services
    assert plan.startup_timeout_seconds == 30.0
    assert backend.command == (
        str(root / "backend/.venv/bin/oh-goon-local-dev-backend"), "--host", "127.0.0.1",
        "--port", "8787", "--reload", "--reload-dir", "backend/src",
    )
    assert (backend.readiness_url, backend.readiness_status) == ("http://127.0.0.1:8787/healthz", 204)
    assert frontend.command == ("npm", "run", "dev")
    assert frontend.cwd == root / "frontend"
    assert (frontend.readiness_url, frontend.readiness_status) == ("http://127.0.0.1:5173/", 200)
    assert plan.intent().services[0].command == backend.command


def test_dev_frontdoor_preflight_runs_before_supervisor(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(local_dev_launch, "check_local_state_for_startup", lambda: calls.append("preflight") or 2)
    monkeypatch.setattr(local_dev_launch, "start_contributor_launch", lambda **_: calls.append("launch"))
    assert local_dev_launch.run(tmp_path) == 2
    assert calls == ["preflight"]


def test_dev_frontdoor_reports_structured_startup_failure(tmp_path: Path, monkeypatch, capsys) -> None:
    from oh_goon.runtime.local_launch_supervisor import LocalLaunchStartupError
    monkeypatch.setattr(local_dev_launch, "check_local_state_for_startup", lambda: 0)
    def fail(**_):
        raise LocalLaunchStartupError("frontend exited before readiness (status 1)")
    monkeypatch.setattr(local_dev_launch, "start_contributor_launch", fail)
    assert local_dev_launch.run(tmp_path) == 1
    assert "OH-GOON DEV STARTUP FAILED" in capsys.readouterr().err


def test_contributor_backend_uses_project_owned_entrypoint_that_survives_workspace_normalization() -> None:
    root = Path(__file__).resolve().parents[4]
    plan = contributor_dev_plan(root)
    assert plan.services[0].command[0] == str(root / "backend/.venv/bin/oh-goon-local-dev-backend")
    pyproject = (root / "backend/pyproject.toml").read_text(encoding="utf-8")
    assert 'oh-goon-local-dev-backend = "oh_goon.repository_tooling.local_dev_backend:main"' in pyproject
