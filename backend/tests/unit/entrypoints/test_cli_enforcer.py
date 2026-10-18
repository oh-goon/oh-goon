from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from oh_goon.cli_enforcer import run_enforcer_command
from oh_goon.cli_parser import build_parser


def _runtime(repository: Path):
    preview = SimpleNamespace(
        plan_id="fleet-plan-test",
        steps=(SimpleNamespace(step_id="check"),),
        model_dump_json=lambda indent=2: json.dumps(
            {"plan_id": "fleet-plan-test", "steps": [{"step_id": "check"}]},
            indent=indent,
        ),
    )
    inspection = SimpleNamespace(
        team=SimpleNamespace(repository=SimpleNamespace(path=repository)),
        preview=preview,
    )
    return SimpleNamespace(goon=SimpleNamespace(inspect=Mock(return_value=inspection)))


def test_enforcer_run_inspects_the_team_through_goon_yml(tmp_path: Path, capsys) -> None:
    repository = tmp_path / "repo"
    args = build_parser().parse_args(["enforcer", "run", str(repository)])
    runtime = _runtime(repository)

    result = run_enforcer_command(runtime, args)

    assert result == 0
    runtime.goon.inspect.assert_called_once_with(repository, inputs={})
    output = capsys.readouterr().out
    assert "Entrypoint: goon.yml" in output
    assert "Plan ID: fleet-plan-test" in output
    assert "Enforcer: valid" in output
    assert "Nothing executed." in output


def test_enforcer_json_is_the_same_goon_preview(tmp_path: Path, capsys) -> None:
    repository = tmp_path / "repo"
    args = build_parser().parse_args(["enforcer", "run", str(repository), "--json"])
    runtime = _runtime(repository)

    assert run_enforcer_command(runtime, args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["plan_id"] == "fleet-plan-test"
    runtime.goon.inspect.assert_called_once_with(repository, inputs={})
