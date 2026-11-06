"""Remote selections consume valid frozen pins before native acquisition."""

import hashlib
import shlex
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from oh_goon.core.errors import FleetPlanInvalid, FleetPlanSourceAcquisitionRequired
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.fleet_plan import ResolvedCommandStep
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.engine.git import GitService
from oh_goon.services.execution_lock_persistence import write_execution_lock
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.run_configuration import (
    RunConfigurationSnapshot,
    capture_run_configuration_source,
    load_run_configuration,
)


def _case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    complete_example: bool = False,
    extra_package: bool = False,
    application_source: bool = False,
) -> tuple[RunConfigurationSnapshot, RepositoryIdentity, FleetPlanResolver]:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    source = tmp_path / "upstream"
    package = source / "goons/general/check"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# general/check\n\nObserve the selected command.\n")
    (package / "goon.yml").write_text("steps:\n- run: echo selected\n")
    if application_source:
        application = source / "application"
        application.mkdir()
        (application / "app.py").write_text("VALUE = 42\n")
    if extra_package:
        second = source / "goons/general/second"
        second.mkdir()
        (second / "README.md").write_text("# general/second\n")
        (second / "goon.yml").write_text("steps:\n- run: echo secondary\n")
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
    if application_source:
        local = workflow / "goons/general/check"
        local.mkdir(parents=True)
        (local / "README.md").write_text("# general/check\n")
        (local / "goon.yml").write_text(
            yaml.safe_dump(
                {
                    "application": {"git": str(source), "ref": "main", "path": "application"},
                    "steps": [{"run": "python app.py"}],
                }
            )
        )
        (workflow / "plan.yml").write_text("goons:\n- import: general/check\n  from: ./goons\n")
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
                "version": 2,
                "source": selection,
                "target": {"repository": str(repository.path)},
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
    assert len(resolved.execution_sources) == 1
    write_execution_lock(
        snapshot.lock_path,
        build_execution_lock(resolved, external_artifacts=snapshot.external_artifacts()),
    )
    return snapshot, repository, resolver


def _filesystem_observation(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_file()
        else "directory"
        for path in root.rglob("*")
    }


@pytest.mark.parametrize(
    "captured_graph", [False, True], ids=["source-owner-fallback", "captured-source"]
)
def test_passive_preview_never_runs_repository_git_input_fsmonitor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_graph: bool
) -> None:
    from oh_goon.services.run_configuration_inspection import inspect_run_configuration

    target = tmp_path / "target"
    target.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(target)], check=True)
    (target / "tracked.txt").write_text("source\n")
    subprocess.run(["git", "-C", str(target), "add", "."], check=True)
    marker = tmp_path / "git-fsmonitor-executed"
    hook = tmp_path / "fsmonitor.sh"
    hook.write_text(
        "#!/bin/sh\nprintf invoked > " + shlex.quote(str(marker)) + '\nprintf "token\\000/\\000"\n'
    )
    hook.chmod(0o700)
    subprocess.run(["git", "-C", str(target), "config", "core.fsmonitor", str(hook)], check=True)
    workflow = tmp_path / "workflow"
    package = workflow / "goons/general/check"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# general/check\n")
    (package / "goon.yml").write_text(
        "inputs:\n  branch: {type: string}\nsteps:\n- run: echo ${{ inputs.branch }}\n"
    )
    (workflow / "plan.yml").write_text(
        "goons:\n- import: general/check\n  from: ./goons\n"
        "  with:\n    branch: {from: {git: branch}}\n"
    )
    configuration = workflow / "run.yml"
    configuration.write_text(
        "version: 2\nsource: {kind: file-plan, path: plan.yml}\ntarget: {repository: ../target}\n"
    )
    repository = RepositoryIdentity(path=target)
    resolver = FleetPlanResolver()
    selected, graph = capture_run_configuration_source(
        load_run_configuration(configuration),
        (repository,),
        resolver,
        allow_source_materialization=False,
    )
    before = _filesystem_observation(tmp_path)
    with monkeypatch.context() as passive:
        observe = Mock(side_effect=AssertionError("Passive preview contacted native Git"))
        passive.setattr(GitService, "observe", observe)
        report = inspect_run_configuration(
            selected,
            resolver,
            source_graph=graph if captured_graph else None,
            preview_targets=True,
        )
        observe.assert_not_called()
    assert report["diagnostic"]["code"] == "EXTERNAL_INPUT_NOT_OBSERVED"
    assert report["current_target_preview"]["targets"][0]["state"] == "UNVERIFIED"
    assert report["execution_authorized"] is False
    assert _filesystem_observation(tmp_path) == before
    # This is a live native boundary, not a dormant hook: ordinary resolution
    # keeps native Git authority and demonstrably invokes the configured hook.
    resolved = resolver.resolve_source_graph(graph, repository=target)
    assert resolved.steps[0].argv == ("echo", "main")
    assert marker.read_text() == "invoked"


@pytest.mark.parametrize("source_kind", ["plan", "goon", "application"])
@pytest.mark.parametrize("cached", [False, True])
def test_passive_source_observation_refuses_git_before_materialization_even_with_valid_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_kind: str, cached: bool
) -> None:
    from oh_goon.integrations import git_source_preparation

    snapshot, repository, resolver = _case(
        tmp_path,
        monkeypatch,
        complete_example=source_kind == "plan",
        application_source=source_kind == "application",
    )
    if not cached:
        shutil.rmtree(tmp_path / "cache")
    before = _filesystem_observation(tmp_path)
    with monkeypatch.context() as passive:
        materialize = Mock(side_effect=AssertionError("Passive source reached Git materialization"))
        passive.setattr(git_source_preparation, "prepare_git_source", materialize)
        with pytest.raises(FleetPlanSourceAcquisitionRequired, match="Passive preview"):
            capture_run_configuration_source(
                snapshot,
                (repository,),
                resolver,
                allow_source_materialization=False,
            )
        materialize.assert_not_called()
    assert _filesystem_observation(tmp_path) == before
    # The ordinary source owner still acquires exactly this valid authored
    # selection; a passive refusal must not replace normal preparation semantics.
    selected, graph = capture_run_configuration_source(
        snapshot, (repository,), resolver, acquire=True, allow_network=True
    )
    assert selected.repositories == (repository,)
    assert [pin.kind for pin in graph.execution_sources] == [source_kind]
    assert len(resolver.resolve_source_graph(graph).steps) == 1


@pytest.mark.parametrize("lock_state", ["absent", "unavailable", "captured", "unverified-digest"])
def test_local_source_transports_one_existing_lock_observation_without_requiring_freshness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lock_state: str
) -> None:
    from oh_goon.services import run_configuration
    from oh_goon.services.execution_lock_persistence import load_execution_lock

    snapshot, repository, resolver = _case(tmp_path, monkeypatch)
    local = snapshot.root / "goons/general/local"
    local.mkdir(parents=True)
    (local / "README.md").write_text("# general/local\n")
    (local / "goon.yml").write_text("steps:\n- run: echo local\n")
    (snapshot.root / "plan.yml").write_text("goons:\n- import: general/local\n  from: ./goons\n")
    expected = load_execution_lock(snapshot.lock_path)
    if lock_state == "absent":
        snapshot.lock_path.unlink()
    elif lock_state == "unavailable":
        snapshot.lock_path.write_text("invalid: [\n")
    elif lock_state == "unverified-digest":
        expected = expected.model_copy(update={"digest": "sha256:" + "0" * 64})
        write_execution_lock(snapshot.lock_path, expected)
    read = Mock(wraps=run_configuration.read_caller_file_bytes)
    monkeypatch.setattr(run_configuration, "read_caller_file_bytes", read)
    selected, graph = capture_run_configuration_source(
        snapshot, (repository,), resolver, allow_source_materialization=False
    )
    observation = selected.lock_observation
    captured = lock_state in {"captured", "unverified-digest"}
    assert observation is not None
    assert observation.status == ("captured" if captured else lock_state)
    assert observation.lock == (expected if captured else None)
    assert read.call_count == (0 if lock_state == "absent" else 1)
    # The old valid envelope belongs to a different source graph. Capturing it
    # makes its records observable without asserting that it admits this Plan.
    assert resolver.resolve_source_graph(graph).steps[0].argv == ("echo", "local")


def test_git_plan_plural_capture_keeps_one_source_graph_and_exact_target_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, repository, resolver = _case(tmp_path, monkeypatch, complete_example=True)
    second = tmp_path / "second-application"
    second.mkdir()
    repositories = (repository, RepositoryIdentity.model_validate({"path": second}))
    payload = yaml.safe_load(snapshot.content)
    payload["target"] = {"repositories": [str(item.path) for item in repositories]}
    snapshot.path.write_text(yaml.safe_dump(payload))
    selected = load_run_configuration(snapshot.path)
    analyze = Mock(wraps=resolver.analyze_source)
    monkeypatch.setattr(resolver, "analyze_source", analyze)

    normalized, graph = capture_run_configuration_source(
        selected, repositories, resolver, acquire=True, allow_network=False
    )

    assert normalized.repositories == repositories
    assert normalized.resolution_repository is None
    assert set(normalized.repository_instance_ids or {}) == {
        item.repository_id for item in repositories
    }
    assert analyze.call_count == 1
    resolved = resolver.resolve_source_graph(graph)
    assert isinstance(resolved.steps[0], ResolvedCommandStep)
    assert resolved.steps[0].argv == ("echo", "selected")
    assert tuple(source.kind for source in graph.execution_sources) == ("plan",)


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
        payload["execution_sources"][0]["commit"] = "a" * 40
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


@pytest.mark.parametrize("new_import_first", [False, True])
def test_adding_a_same_selector_package_preserves_existing_pin_in_either_order(
    tmp_path, monkeypatch, new_import_first
):
    snapshot, repository, resolver = _case(tmp_path, monkeypatch, extra_package=True)
    original = yaml.safe_load(snapshot.lock_path.read_text())["execution_sources"][0]["commit"]
    upstream = tmp_path / "upstream"
    for name in ("check", "second"):
        (upstream / f"goons/general/{name}/goon.yml").write_text(
            f"steps:\n- run: echo changed-{name}\n"
        )
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
            "moved selector",
        ],
        check=True,
    )
    current = subprocess.check_output(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True
    ).strip()
    names = ("second", "check") if new_import_first else ("check", "second")
    imports = [
        {
            "import": f"general/{name}",
            "from": {"git": str(upstream), "ref": "main", "path": f"goons/general/{name}"},
        }
        for name in names
    ]
    (snapshot.root / "plan.yml").write_text(yaml.safe_dump({"goons": imports}))
    _, graph = capture_run_configuration_source(
        snapshot,
        (repository,),
        resolver,
        acquire=True,
        allow_network=True,
    )
    pinned = resolver.resolve_source_graph(graph)
    assert [pin.commit for pin in pinned.execution_sources] == [original, original]
    commands = []
    for step in pinned.steps:
        assert isinstance(step, ResolvedCommandStep)
        commands.append(step.argv[-1])
    assert commands == ["secondary" if name == "second" else "selected" for name in names]
    _, refreshed = capture_run_configuration_source(
        snapshot,
        (repository,),
        resolver,
        acquire=True,
        allow_network=True,
        update=True,
    )
    assert [pin.commit for pin in refreshed.execution_sources] == [current, current]


@pytest.mark.parametrize("complete_example", [False, True])
def test_changed_run_configuration_refuses_before_pinned_source_acquisition(
    tmp_path, monkeypatch, complete_example
):
    snapshot, repository, resolver = _case(tmp_path, monkeypatch, complete_example=complete_example)
    snapshot.path.write_bytes(snapshot.content + b"\n")
    changed = load_run_configuration(snapshot.path)
    run = Mock(side_effect=AssertionError("Known stale run.yml reached native Git"))
    monkeypatch.setattr(GitService, "run", run)
    with pytest.raises(FleetPlanInvalid, match="(changed|stale)"):
        capture_run_configuration_source(changed, (repository,), resolver, allow_network=True)
    run.assert_not_called()


def test_switching_to_a_complete_example_retains_the_shared_selector_pin(tmp_path, monkeypatch):
    snapshot, repository, resolver = _case(tmp_path, monkeypatch)
    original = yaml.safe_load(snapshot.lock_path.read_text())["execution_sources"][0]["commit"]
    upstream = tmp_path / "upstream"
    (upstream / "goons/general/check/goon.yml").write_text("steps:\n- run: echo advanced\n")
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
            "advanced main",
        ],
        check=True,
    )
    payload = yaml.safe_load(snapshot.content)
    payload["source"] = {
        "kind": "git-plan",
        "git": str(upstream),
        "ref": "main",
        "path": ".",
        "plan": "plan.yml",
    }
    snapshot.path.write_text(yaml.safe_dump(payload))
    changed = load_run_configuration(snapshot.path)
    _, graph = capture_run_configuration_source(
        changed,
        (repository,),
        resolver,
        acquire=True,
        allow_network=True,
    )
    assert graph.execution_sources[0].kind == "plan"
    assert graph.execution_sources[0].commit == original
    command = resolver.resolve_source_graph(graph).steps[0]
    assert isinstance(command, ResolvedCommandStep)
    assert command.argv[-1] == "selected"


def test_nested_same_selector_import_uses_the_complete_examples_captured_commit(
    tmp_path, monkeypatch
):
    from contextlib import contextmanager

    from oh_goon.integrations import git_source_preparation

    snapshot, repository, resolver = _case(tmp_path, monkeypatch, complete_example=True)
    upstream = tmp_path / "upstream"
    coordinates = {"git": str(upstream), "ref": "main", "path": "goons/general/check"}
    (upstream / "plan.yml").write_text(
        yaml.safe_dump(
            {
                "goons": [{"import": "general/check", "from": coordinates}],
            }
        )
    )

    def commit():
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
                "selected source",
            ],
            check=True,
        )
        return subprocess.check_output(
            ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True
        ).strip()

    selected = commit()
    native = git_source_preparation.prepare_git_source
    prepared = []

    @contextmanager
    def moving_branch(*args, **kwargs):
        with native(*args, **kwargs) as captured:
            prepared.append(captured[1])
            if len(prepared) == 1:
                (upstream / "goons/general/check/goon.yml").write_text(
                    "steps:\n- run: echo advanced\n"
                )
                assert commit() != selected
            yield captured

    monkeypatch.setattr(git_source_preparation, "prepare_git_source", moving_branch)
    _, graph = capture_run_configuration_source(
        snapshot,
        (repository,),
        resolver,
        acquire=True,
        allow_network=True,
        update=True,
    )
    resolved = resolver.resolve_source_graph(graph)
    assert [pin.commit for pin in resolved.execution_sources] == [selected, selected]
    assert isinstance(resolved.steps[0], ResolvedCommandStep)
    assert resolved.steps[0].argv[-1] == "selected"


def test_generated_lock_larger_than_run_yml_bound_round_trips_through_source_capture(
    tmp_path, monkeypatch
):
    from oh_goon.core.execution_lock_identity import verify_execution_lock
    from oh_goon.core.game_tape_step_projection import project_steps
    from oh_goon.core.models.game_tape import GameTapeStepExecution
    from oh_goon.services.execution_lock_persistence import load_execution_lock

    snapshot, repository, resolver = _case(tmp_path, monkeypatch)
    upstream = tmp_path / "upstream"
    imports = []
    origin = "https://example.invalid/" + "repository" * 180 + ".git"
    for index in range(40):
        key = f"general/package-{index}"
        package = upstream / "goons" / key
        package.mkdir()
        (package / "README.md").write_text(f"# {key}\n")
        (package / "goon.yml").write_text("steps:\n- run: echo selected\n")
        imports.append(
            {"import": key, "from": {"git": origin, "ref": "main", "path": "goons/" + key}}
        )
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
            "complete source selection",
        ],
        check=True,
    )
    (snapshot.root / "plan.yml").write_text(yaml.safe_dump({"goons": imports}))
    # Native URL rewriting supplies this explicit origin without an external
    # service; it remains the same public provenance in all 40 selected pins.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "url." + upstream.as_uri() + ".insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", origin)
    _, graph = capture_run_configuration_source(
        snapshot,
        (repository,),
        resolver,
        acquire=True,
        allow_network=True,
    )
    resolved = resolver.resolve_source_graph(graph)
    expected = build_execution_lock(resolved, external_artifacts=snapshot.external_artifacts())
    projected = project_steps(
        "long-source",
        repository.repository_id,
        resolved.steps,
        (),
        step_sources={item.step_id: item for item in resolved.step_sources},
        source_label="plan.yml",
        terminal=False,
        operation_reason_code=None,
    )
    for step, position in zip(projected, resolved.step_sources, strict=True):
        assert len(position.source) > 240
        assert step.why.source_location.source == position.source
        restored = GameTapeStepExecution.model_validate_json(
            step.model_dump_json(exclude_computed_fields=True),
        )
        assert restored.why.source_location.source == position.source
    write_execution_lock(snapshot.lock_path, expected)
    assert snapshot.lock_path.stat().st_size > 65536
    assert load_execution_lock(snapshot.lock_path) == expected
    _, captured = capture_run_configuration_source(
        snapshot,
        (repository,),
        resolver,
        allow_network=True,
    )
    verify_execution_lock(
        expected,
        build_execution_lock(
            resolver.resolve_source_graph(captured),
            external_artifacts=snapshot.external_artifacts(),
        ),
    )


def test_oversized_lock_refuses_before_git_source_acquisition(tmp_path, monkeypatch):
    from oh_goon.services.execution_lock_persistence import MAX_EXECUTION_LOCK_BYTES

    snapshot, repository, resolver = _case(tmp_path, monkeypatch)
    with snapshot.lock_path.open("wb") as stream:
        stream.truncate(MAX_EXECUTION_LOCK_BYTES + 1)
    run = Mock(side_effect=AssertionError("Oversized lock reached source acquisition"))
    monkeypatch.setattr(GitService, "run", run)
    with pytest.raises(FleetPlanInvalid, match="goon.lock"):
        capture_run_configuration_source(snapshot, (repository,), resolver, allow_network=True)
    run.assert_not_called()


@pytest.mark.parametrize("invalid", ["merge", "cycle", "windows-path"])
def test_invalid_authored_plan_refuses_before_git_acquisition(tmp_path, monkeypatch, invalid):
    snapshot, repository, resolver = _case(tmp_path, monkeypatch)
    plan = snapshot.root / "plan.yml"
    source = plan.read_text()
    if invalid == "merge":
        source = "execution:\n  <<: {purpose: verification}\n" + source
    elif invalid == "cycle":
        source += "inputs: &cycle [*cycle]\n"
    else:
        source = source.replace("path: goons/general/check", "path: C:/goons/general/check")
    plan.write_text(source)
    run = Mock(side_effect=AssertionError("Invalid Plan source reached Git acquisition"))
    monkeypatch.setattr(GitService, "run", run)
    with pytest.raises(FleetPlanInvalid):
        capture_run_configuration_source(
            snapshot,
            (repository,),
            resolver,
            acquire=True,
            allow_network=True,
        )
    run.assert_not_called()


def test_repeated_acyclic_source_alias_keeps_written_occurrences_and_one_pin(tmp_path, monkeypatch):
    snapshot, repository, resolver = _case(tmp_path, monkeypatch)
    origin = str(tmp_path / "upstream")
    (snapshot.root / "plan.yml").write_text(
        "goons:\n- import: general/check\n  from: &source\n"
        f"    git: {origin}\n    ref: main\n    path: goons/general/check\n"
        "- import: general/check\n  from: *source\n"
    )
    _, graph = capture_run_configuration_source(
        snapshot,
        (repository,),
        resolver,
        acquire=True,
        allow_network=True,
    )
    plan = resolver.resolve_source_graph(graph)
    assert len(plan.steps) == 2
    assert plan.steps[0].step_id != plan.steps[1].step_id
    assert len(plan.execution_sources) == 1
