"""Remote selections consume valid frozen pins before native acquisition."""

import subprocess
from unittest.mock import Mock

import pytest
import yaml

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.engine.git import GitService
from oh_goon.services.execution_lock_persistence import write_execution_lock
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.run_configuration import (
    capture_run_configuration_source,
    load_run_configuration,
)


def _case(tmp_path, monkeypatch, *, complete_example=False):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    source = tmp_path / "upstream"
    package = source / "goons/general/check"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# general/check\n\nObserve the selected command.\n")
    (package / "goon.yml").write_text("steps:\n- run: echo selected\n")
    (source / "plan.yml").write_text("goons:\n- import: general/check\n  from: ./goons\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "source",
        ],
        check=True,
    )
    target = tmp_path / "target"
    target.mkdir()
    repository = RepositoryIdentity(path=target)
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    coordinates = {"git": str(source), "ref": "main", "path": "goons/general/check"}
    (workflow / "plan.yml").write_text(
        yaml.safe_dump(
            {
                "goons": [{"import": "general/check", "from": coordinates}],
            }
        )
    )
    selection = {"kind": "file-plan", "path": "plan.yml"}
    if complete_example:
        selection = {
            "kind": "git-plan",
            "git": str(source),
            "ref": "main",
            "path": ".",
            "plan": "plan.yml",
        }
    path = workflow / "run.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "source": selection,
                "target": {"repository_id": repository.repository_id},
            }
        )
    )
    snapshot = load_run_configuration(path)
    resolver = FleetPlanResolver()
    normalized, graph = capture_run_configuration_source(
        snapshot,
        (repository,),
        resolver,
        acquire=True,
        allow_network=True,
    )
    resolved = resolver.resolve_source_graph(graph)
    assert len(resolved.goon_sources) == 1
    write_execution_lock(snapshot.lock_path, build_execution_lock(resolved))
    return snapshot, repository, resolver


@pytest.mark.parametrize("complete_example", [False, True])
@pytest.mark.parametrize("invalid", ["malformed", "tampered", "dangling-symlink"])
def test_unavailable_or_invalid_pin_authority_never_reaches_git(
    tmp_path, monkeypatch, complete_example, invalid
):
    snapshot, repository, resolver = _case(tmp_path, monkeypatch, complete_example=complete_example)
    if invalid == "malformed":
        snapshot.lock_path.write_text("invalid: [\n")
    elif invalid == "tampered":
        payload = yaml.safe_load(snapshot.lock_path.read_text())
        payload["goon_sources"][0]["commit"] = "a" * 40
        snapshot.lock_path.write_text(yaml.safe_dump(payload))
    else:
        snapshot.lock_path.unlink()
        snapshot.lock_path.symlink_to(tmp_path / "missing-lock")
    run = Mock(side_effect=AssertionError("Invalid source pins reached Git"))
    monkeypatch.setattr(GitService, "run", run)
    for acquire, allow_network in ((False, False), (False, True), (True, True)):
        with pytest.raises(FleetPlanInvalid, match="goon.lock"):
            capture_run_configuration_source(
                snapshot,
                (repository,),
                resolver,
                acquire=acquire,
                allow_network=allow_network,
            )
    run.assert_not_called()


@pytest.mark.parametrize("complete_example", [False, True])
def test_missing_pin_authority_requires_lock_without_hidden_fetch(
    tmp_path, monkeypatch, complete_example
):
    snapshot, repository, resolver = _case(tmp_path, monkeypatch, complete_example=complete_example)
    snapshot.lock_path.unlink()
    run = Mock(side_effect=AssertionError("Missing source pins reached Git"))
    monkeypatch.setattr(GitService, "run", run)
    for allow_network in (False, True):
        with pytest.raises(FleetPlanInvalid, match="acquisition"):
            capture_run_configuration_source(
                snapshot,
                (repository,),
                resolver,
                allow_network=allow_network,
            )
    run.assert_not_called()


def test_invalid_unselected_lock_does_not_prevent_local_source_inspection(tmp_path, monkeypatch):
    snapshot, repository, resolver = _case(tmp_path, monkeypatch)
    local = snapshot.root / "goons/general/local"
    local.mkdir(parents=True)
    (local / "README.md").write_text("# general/local\n")
    (local / "goon.yml").write_text("steps:\n- run: echo local\n")
    (snapshot.root / "plan.yml").write_text("goons:\n- import: general/local\n  from: ./goons\n")
    snapshot.lock_path.write_text("invalid: [\n")
    run = Mock(side_effect=AssertionError("Local inspection invoked Git"))
    monkeypatch.setattr(GitService, "run", run)
    _, graph = capture_run_configuration_source(snapshot, (repository,), resolver)
    assert graph.root.path == snapshot.root / "plan.yml"
    run.assert_not_called()
