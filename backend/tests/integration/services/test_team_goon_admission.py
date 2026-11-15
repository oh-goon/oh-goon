from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import RunNotFound, TargetSelectionError
from oh_goon.core.models.league import ConfiguredTeamPipeline
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import repository_subject_instance_id

pytestmark = [
    pytest.mark.process,
    pytest.mark.skipif(os.name != "posix", reason="native POSIX worker required"),
]


@pytest.mark.parametrize("replace_repository", [False, True], ids=["unchanged", "replaced"])
def test_team_run_keeps_repository_instance_through_plan_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace_repository: bool
) -> None:
    repository = tmp_path / "repos" / "team"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True, capture_output=True)
    (repository / "effect.py").write_text(
        "from pathlib import Path\n\nPath('team-effect.txt').write_text('ran', encoding='utf-8')\n",
        encoding="utf-8",
    )
    goon = repository / "goon.yml"
    goon.write_text(
        yaml.safe_dump(
            {"version": 1, "steps": [{"run": f"{shlex.quote(sys.executable)} effect.py"}]}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(tmp_path / "league.yml"))
    for directory in ("CONFIG", "DATA", "STATE", "CACHE"):
        monkeypatch.setenv(f"XDG_{directory}_HOME", str(tmp_path / directory.lower()))
    runtime = ApplicationRuntime.build(standard_local=True, startup_purpose="workflow")
    run_id = "team-subject-admission"
    effect = repository / "team-effect.txt"
    try:
        runtime.locations.add(repository.parent)
        runtime.plans.lock(goon)
        team = runtime.league.configured_team(repository)
        assert isinstance(team.pipeline, ConfiguredTeamPipeline)
        original_bytes = goon.read_bytes()
        prepare = runtime.plans.prepare_execution_target
        prepared = False

        def prepare_then_replace(*args: Any, **kwargs: Any) -> FleetPlanTarget:
            nonlocal prepared
            target = prepare(*args, **kwargs)
            prepared = True
            if replace_repository:
                retired = tmp_path / "retired-team"
                repository.rename(retired)
                shutil.copytree(retired, repository)
                assert goon.read_bytes() == original_bytes
                assert repository_subject_instance_id(repository) != team.repository_instance_id
            return target

        monkeypatch.setattr(runtime.plans, "prepare_execution_target", prepare_then_replace)

        def run() -> str:
            admitted = runtime.goon.run(
                repository,
                expected_pipeline_sha256=team.pipeline.sha256,
                expected_repository_instance_id=team.repository_instance_id,
                fleet_run_id=run_id,
            )
            return admitted.fleet_run_id

        if replace_repository:
            with pytest.raises(TargetSelectionError, match="Repository instance changed"):
                run()
            with pytest.raises(RunNotFound):
                runtime.runs.get(run_id)
            assert not effect.exists()
        else:
            detail = runtime.runs.wait_for_terminal(run(), timeout=20)
            assert detail.outcome.value == "SUCCESS"
            assert effect.read_text(encoding="utf-8") == "ran"
        assert prepared
    finally:
        runtime.shutdown(wait_for_runs=True)
