"""Native lock and run retain Git Agent source identity after moving selectors."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from oh_goon.services.execution_lock_persistence import load_execution_lock

ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize("source_kind", ["git-plan", "git-goon"])
def test_native_git_agent_lock_runs_same_commit_after_branch_advance_and_cache_loss(
    tmp_path: Path, source_kind: str
) -> None:
    workflow = tmp_path / "workflow"
    shutil.copytree(ROOT / "examples/agent-skill-local-search", workflow)
    repository = workflow / "repos/team/app"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    upstream = tmp_path / "upstream"
    authoring = upstream / "workflows/search"
    authoring.mkdir(parents=True)
    shutil.move(str(workflow / "goons"), authoring / "goons")
    (authoring / "plan.yml").write_bytes((workflow / "plan.yml").read_bytes())
    subprocess.run(["git", "init", "-q", "-b", "main", str(upstream)], check=True)

    def commit() -> str:
        subprocess.run(["git", "-C", str(upstream), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(upstream),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "Selected source",
            ],
            check=True,
        )
        return subprocess.check_output(
            ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True
        ).strip()

    pinned = commit()
    configuration = workflow / "run.yml"
    selected = yaml.safe_load(configuration.read_text())
    selected["agent"]["repository_root"] = str(workflow / "repos")
    if source_kind == "git-plan":
        selected["source"] = {
            "kind": "git-plan",
            "git": str(upstream),
            "ref": "main",
            "path": "workflows/search",
            "plan": "plan.yml",
        }
        (workflow / "plan.yml").unlink()
    else:
        (workflow / "plan.yml").write_text(
            yaml.safe_dump(
                {
                    "goons": [
                        {
                            "import": "agent/search",
                            "from": {
                                "git": str(upstream),
                                "ref": "main",
                                "path": "workflows/search/goons/agent/search",
                            },
                        }
                    ]
                }
            )
        )
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("OH_GOON_")
    }
    environment.update(
        OH_GOON_DB=str(tmp_path / "state.sqlite"),
        OH_GOON_LEAGUE_FILE=str(tmp_path / "league.yml"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
    )

    def cli(*args: str) -> str:
        completed = subprocess.run(
            [str(Path(sys.executable).with_name("oh-goon")), *args],
            cwd=tmp_path,
            env=environment,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        return completed.stdout

    cli("league", "add-folder", str(workflow / "repos"))
    scan = json.loads(cli("league", "scan", "--json"))
    target = next(
        team["repository"]
        for team in scan["teams"]
        if Path(team["repository"]["path"]) == repository
    )
    selected["target"] = {"repository_id": target["repository_id"]}
    configuration.write_text(yaml.safe_dump(selected, sort_keys=False))
    cli("lock", str(configuration))
    lock_path = workflow / "goon.lock"
    lock_bytes = lock_path.read_bytes()
    lock = load_execution_lock(lock_path)
    assert len(lock.goon_sources) == 1 and lock.goon_sources[0].commit == pinned

    for advance in (False, True):
        if advance:
            (authoring / "goons/agent/search/goon.yml").write_text("steps:\n- run: exit 92\n")
            assert commit() != pinned
            shutil.rmtree(tmp_path / "cache/oh-goon/skill-sources")
        result = json.loads(cli("run", str(configuration), "--json"))
        assert result["status"] == "COMPLETED" and result["outcome"] == "SUCCESS"
        tape = json.loads(cli("plan", "tape", result["fleet_run_id"], "--json"))
        assert tape["status"] == "PASSED"
        steps = tape["repositories"][0]["steps"]
        assert len(steps) == 1 and steps[0]["status"] == "PASSED"
        assert steps[0]["attempts"][0]["returncode"] == 0
        assert lock_path.read_bytes() == lock_bytes


def test_native_git_plan_proposal_repair_recaptures_before_its_second_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.application import ApplicationRuntime
    from oh_goon.core.execution_lock_identity import build_execution_lock
    from oh_goon.core.models.git_sources import LockedGoonSource
    from oh_goon.integrations.git_source_preparation import prepare_git_source
    from oh_goon.integrations.hashmarks import identity as external_identity
    from oh_goon.runtime.goon_templates import GoonTemplateStore
    from oh_goon.services.execution_lock_persistence import write_execution_lock
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
    from tests.integration.test_agent_bounded_repair import _repair, _repair_case

    identify = external_identity._identify_one
    local, repository, options, _, _ = _repair_case(tmp_path, monkeypatch)
    monkeypatch.setattr(external_identity, "_identify_one", identify)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    shutil.copytree(tmp_path / "goons", upstream / "goons")
    (upstream / "plan.yml").write_text(
        "inputs: [repo, skill, acceptance]\nexecution: {purpose: verification}\n"
        "goons:\n- import: agent/search\n  from: goons\n"
    )
    subprocess.run(["git", "init", "-q", "-b", "main", str(upstream)], check=True)
    subprocess.run(["git", "-C", str(upstream), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(upstream),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "Repair Plan",
        ],
        check=True,
    )
    pin = LockedGoonSource(
        git=str(upstream),
        ref="main",
        path=".",
        kind="plan",
        plan="plan.yml",
        commit=subprocess.check_output(
            ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True
        ).strip(),
    )
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    with prepare_git_source(pin.git, pin.ref, commit=pin.commit) as (checkout, _):
        graph = resolver.analyze_source(
            checkout / "plan.yml",
            containment_root=checkout,
            plan_source=pin,
            locked_goons=(pin,),
        )
    path = graph.root.path
    assert not path.exists()
    frozen, _, _ = resolver.resolve_frozen_agent(
        path,
        source_graph=graph,
        inputs=options["inputs"],
        repository_root=options["agent_repository_root"],
        skill_root=options["agent_skill_root"],
        allowed_acceptance=options["allowed_agent_acceptance"],
    )
    lock_path = local.parent / "goon.lock"
    write_execution_lock(lock_path, build_execution_lock(frozen))
    lock = lock_path.read_bytes()
    (upstream / "goons/agent/search/goon.yml").write_text("steps:\n- run: exit 92\n")
    subprocess.run(["git", "-C", str(upstream), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(upstream),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "Advance main",
        ],
        check=True,
    )
    shutil.rmtree(tmp_path / "cache/oh-goon/skill-sources")
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repository.parent.parent)
        result = _repair(
            runtime,
            path,
            repository,
            options,
            source_graph=graph,
            lock_path=lock_path,
            fleet_run_id="git-proposal-repair",
        )
        assert result.stop_reason.value == "test_passed", result
        assert len(result.attempts) == 2
        assert all(detail.run.target.plan.goon_sources == (pin,) for detail in result.attempts)
        assert (repository / "code.txt").read_text() == "final repaired content\n"
        assert lock_path.read_bytes() == lock
    finally:
        runtime.shutdown(wait_for_runs=True)
