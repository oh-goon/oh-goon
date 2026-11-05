from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from oh_goon.repository_tooling import local_dev_launch
from oh_goon.runtime.local_launch_plan import contributor_dev_plan

ROOT = Path(__file__).resolve().parents[2]


def test_shell_frontdoor_reaches_native_entrypoint_parser_once(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout with spaces"
    script = checkout / "scripts/dev.sh"
    script.parent.mkdir(parents=True)
    script.write_bytes((ROOT / "scripts/dev.sh").read_bytes())
    entrypoint = checkout / "backend/.venv/bin/oh-goon-local-dev-launch"
    entrypoint.parent.mkdir(parents=True)
    probe = (
        "import json\n"
        "from importlib.metadata import distribution\n"
        "from pathlib import Path\n"
        "from oh_goon.repository_tooling import local_dev_launch\n"
        "calls = []\n"
        "def launch(workspace):\n"
        "    calls.append({'workspace': str(workspace), 'cwd': str(Path.cwd())})\n"
        "    return 17\n"
        "local_dev_launch.run = launch\n"
        "entrypoint = next(item for item in distribution('oh-goon').entry_points "
        "if item.group == 'console_scripts' and item.name == 'oh-goon-local-dev-launch')\n"
        "status = entrypoint.load()()\n"
        "print(json.dumps(calls))\n"
        "raise SystemExit(status)\n"
    )
    entrypoint.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -c {shlex.quote(probe)} "$@"\n',
        encoding="utf-8",
    )
    entrypoint.chmod(0o700)

    result = subprocess.run(
        ("bash", str(script)),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 17, result.stderr
    assert json.loads(result.stdout) == [
        {"workspace": str(checkout.resolve()), "cwd": str(checkout.resolve())}
    ]


def test_contributor_plan_is_exact_spawn_and_readiness_authority(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    plan = contributor_dev_plan(root)
    backend, frontend = plan.services
    assert plan.startup_timeout_seconds == 30.0
    assert backend.command == (
        str(root / "backend/.venv/bin/oh-goon-local-dev-backend"),
        "--host",
        "127.0.0.1",
        "--port",
        "8787",
        "--reload",
        "--reload-dir",
        "backend/src",
    )
    assert (backend.readiness_url, backend.readiness_status) == (
        "http://127.0.0.1:8787/healthz",
        204,
    )
    assert frontend.command == ("npm", "run", "dev")
    assert frontend.cwd == root / "frontend"
    assert (frontend.readiness_url, frontend.readiness_status) == (
        "http://127.0.0.1:5173/",
        200,
    )
    assert plan.intent().services[0].command == backend.command


def test_dev_frontdoor_preflight_runs_before_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def preflight() -> int:
        calls.append("preflight")
        return 2

    monkeypatch.setattr(
        local_dev_launch,
        "check_local_state_for_startup",
        preflight,
    )
    monkeypatch.setattr(
        local_dev_launch, "start_contributor_launch", lambda **_: calls.append("launch")
    )
    assert local_dev_launch.run(tmp_path) == 2
    assert calls == ["preflight"]


def test_dev_frontdoor_reports_structured_startup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from oh_goon.runtime.local_launch_supervisor import LocalLaunchStartupError

    monkeypatch.setattr(local_dev_launch, "check_local_state_for_startup", lambda: 0)

    def fail(**_: object) -> None:
        raise LocalLaunchStartupError("frontend exited before readiness (status 1)")

    monkeypatch.setattr(local_dev_launch, "start_contributor_launch", fail)
    assert local_dev_launch.run(tmp_path) == 1
    assert "OH-GOON DEV STARTUP FAILED" in capsys.readouterr().err


def test_contributor_backend_uses_project_owned_entrypoint_that_survives_workspace_normalization() -> (
    None
):
    root = Path(__file__).resolve().parents[2]
    plan = contributor_dev_plan(root)
    assert plan.services[0].command[0] == str(
        root / "backend/.venv/bin/oh-goon-local-dev-backend"
    )
    pyproject = (root / "backend/pyproject.toml").read_text(encoding="utf-8")
    assert (
        'oh-goon-local-dev-backend = "oh_goon.repository_tooling.local_dev_backend:main"'
        in pyproject
    )
