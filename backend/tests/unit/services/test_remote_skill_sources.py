"""Real native Git acquisition, locked reuse, complete resources and cleanup."""

import shutil
import subprocess

import pytest
import yaml

from oh_goon.cli_configuration import prepare_configuration
from oh_goon.cli_lock import run_stateless_lock
from oh_goon.cli_parser import build_parser
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock, verify_execution_lock
from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.engine.git import GitService
from oh_goon.integrations.agent_skill_sources import prepare_skill_source, skill_source_cache
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.execution_lock_persistence import load_execution_lock, write_execution_lock
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def git(root, *args):
    return subprocess.run(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()


def case(tmp_path, monkeypatch, *, object_format="sha1"):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    remote = tmp_path / "collection"
    remote.mkdir()
    git(remote, "init", "-q", "-b", "main", "--object-format=" + object_format)
    for name in ("review", "design"):
        selected = remote / "skills" / name
        (selected / "references").mkdir(parents=True)
        (selected / "coverage").mkdir()
        (selected / "SKILL.md").write_text(
            f"# {name}\nSee references/guide.md and coverage/reference.json.\n"
        )
        (selected / "references/guide.md").write_text("Keep the complete resource directory.\n")
        (selected / "coverage/reference.json").write_text('{"example": true}\n')
    git(remote, "add", ".")
    git(remote, "commit", "-qm", "initial")
    commit = git(remote, "rev-parse", "HEAD")
    root = tmp_path / "workflow"
    owner = root / "goons/agent/review"
    owner.mkdir(parents=True)
    (owner / "README.md").write_text("# agent/review\n")
    (owner / "goon.yml").write_text("""inputs:
  repo: {type: repository_reference}
  skill: {type: skill_reference}
  acceptance: {type: acceptance_profile}
steps:
- uses: agent
  harness: local-search
  task: 'search: hi'
""")
    path = root / "plan.yml"
    path.write_text("""inputs: [repo, acceptance]
goons:
- import: agent/review
  with: {skill: team/review}
  pass: [repo, acceptance]
- import: agent/review
  with: {skill: team/design}
  pass: [repo, acceptance]
""")
    registry = root / ".goonskills.yml"
    registry.write_text(
        yaml.safe_dump(
            {
                "sources": {
                    "team": {
                        "source": remote.as_uri(),
                        "ref": "main",
                        "skills": ["skills/review", "skills/design"],
                    }
                }
            }
        )
    )
    product = tmp_path / "repos/product"
    product.mkdir(parents=True)
    (product / "app.py").write_text("print('hi')\n")
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(root / "goons"))
    options = dict(
        inputs={"repo": "local:product", "acceptance": "review"},
        repository_root=tmp_path / "repos",
        skill_root=root,
        allowed_acceptance=("review",),
    )
    return resolver, path, remote, commit, options


def test_one_collection_prepares_once_and_transports_every_selected_resource(tmp_path, monkeypatch):
    resolver, path, remote, commit, options = case(tmp_path, monkeypatch)
    path.write_text(
        path.read_text()
        + "- import: agent/review\n  pass: [repo, acceptance]\n  with: {skill: team/review}\n"
    )
    calls = []
    native = GitService.run

    def observe(self, *args, **kwargs):
        calls.append(args)
        return native(self, *args, **kwargs)

    monkeypatch.setattr(GitService, "run", observe)
    plan, handoffs, _ = resolver.resolve_frozen_agents(path, **options)
    assert len(plan.skill_sources) == 1
    assert plan.skill_sources[0].commit == commit
    assert plan.skill_sources[0].source == remote.as_uri()
    assert sum("fetch" in args for args in calls) == 1
    assert sum("worktree" in args and "add" in args for args in calls) == 1
    assert len(handoffs) == 3
    reviews = [h for h in handoffs.values() if h.source.authored_skill_reference == "team/review"]
    assert reviews[0].source.skill is reviews[1].source.skill
    assert len({id(h.source.repository) for h in handoffs.values()}) == 1
    for handoff in handoffs.values():
        assert {item.path for item in handoff.source.skill.members} == {
            "SKILL.md",
            "references/guide.md",
            "coverage/reference.json",
        }
        assert commit in handoff.source.resolved_skill_reference
        restored = FrozenAgentStepHandoff.decode_prework_delivery(
            handoff.encode_prework_delivery(),
            expected_handoff_identity=handoff.handoff_identity,
        )
        assert restored.source.skill == handoff.source.skill
    repository = next(skill_source_cache().glob("*.git"))
    assert not (repository / "worktrees").exists()
    assert not (path.parent / ".gitmodules").exists()
    assert not (path.parent / "libs").exists()


@pytest.mark.parametrize("form", ["path", "file_uri", "escaped_uri", "symlink"])
def test_native_local_skill_acquisition_rejects_agent_editable_source_before_git_or_cache(
    tmp_path, monkeypatch, form
):
    from unittest.mock import Mock

    workspace = tmp_path / "workspace"
    source = workspace / "collection"
    source.mkdir(parents=True)
    selected = str(source)
    if form == "file_uri":
        selected = source.as_uri()
    elif form == "escaped_uri":
        selected = source.as_uri().replace("collection", "%63ollection")
    elif form == "symlink":
        alias = tmp_path / "outside-alias"
        alias.symlink_to(source, target_is_directory=True)
        selected = str(alias)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    run = Mock(side_effect=AssertionError("Agent-editable Skill source reached host Git"))
    monkeypatch.setattr(GitService, "run", run)
    with pytest.raises(FleetPlanInvalid, match="agent-editable workspace"):
        with prepare_skill_source(selected, "main", commit="a" * 40, native_workspace=workspace):
            pytest.fail("Unsupported source was prepared")
    run.assert_not_called()
    assert not skill_source_cache().exists()


def test_native_external_locked_skill_source_keeps_native_git_semantics(tmp_path, monkeypatch):
    _, _, remote, commit, options = case(tmp_path, monkeypatch)
    workspace = options["repository_root"] / "product"
    with prepare_skill_source(
        remote.as_uri(), "main", commit=commit, native_workspace=workspace
    ) as (root, selected):
        assert selected == commit
        assert (
            root / "skills/review/references/guide.md"
        ).read_text() == "Keep the complete resource directory.\n"


def test_native_registry_transports_workspace_fence_before_pinned_acquisition(
    tmp_path, monkeypatch
):
    from unittest.mock import Mock

    from oh_goon.core.models.agent_providers import (
        NativeAgentDefinition,
        NativeAgentProfile,
        ResolvedNativeAgent,
    )

    resolver, path, remote, _, options = case(tmp_path, monkeypatch)
    goon = path.parent / "goons/agent/review/goon.yml"
    goon.write_text(
        goon.read_text().replace("harness: local-search", "harness: native\n  role: review")
    )
    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="isolated",
        definition=NativeAgentDefinition(executable="/native/pi"),
        profile=NativeAgentProfile(backend="bubblewrap"),
        executable_sha256="a" * 64,
    )
    run = Mock(side_effect=AssertionError("Native capture prepared an agent-editable Git source"))
    monkeypatch.setattr(GitService, "run", run)
    with pytest.raises(FleetPlanInvalid, match="agent-editable workspace"):
        resolver.resolve_frozen_agents(path, native_agent=selected, repository=remote, **options)
    run.assert_not_called()
    assert not skill_source_cache().exists()


def test_branch_advances_only_on_explicit_update_and_cache_miss_keeps_pin(tmp_path, monkeypatch):
    resolver, path, remote, commit, options = case(tmp_path, monkeypatch)
    plan, handoffs, _ = resolver.resolve_frozen_agents(path, **options)
    original = build_execution_lock(plan)
    write_execution_lock(path.parent / "goon.lock", original)
    (remote / "skills/review/SKILL.md").write_text("# A new revision\n")
    git(remote, "add", ".")
    git(remote, "commit", "-qm", "advance main")
    for cache_miss in (False, True):
        if cache_miss:
            shutil.rmtree(skill_source_cache())
        current, captured, _ = resolver.resolve_frozen_agents(
            path, locked_skills=original.skill_sources, **options
        )
        verify_execution_lock(original, build_execution_lock(current))
        assert current.skill_sources[0].commit == commit
        assert captured == handoffs
    updated, changed, _ = resolver.resolve_frozen_agents(path, update_skills=True, **options)
    assert updated.skill_sources[0].commit == git(remote, "rev-parse", "HEAD")
    assert updated.skill_sources[0].commit != commit
    assert build_execution_lock(updated).digest != original.digest
    assert changed != handoffs
    assert load_execution_lock(path.parent / "goon.lock") == original


@pytest.mark.parametrize("selector", ["tag", "commit"])
@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_native_git_tag_and_exact_commit_selectors(tmp_path, monkeypatch, selector, object_format):
    resolver, path, remote, commit, options = case(
        tmp_path, monkeypatch, object_format=object_format
    )
    git(remote, "tag", "-am", "release", "v1.0.0")
    registry = path.parent / ".goonskills.yml"
    data = yaml.safe_load(registry.read_text())
    data["sources"]["team"]["ref"] = "v1.0.0" if selector == "tag" else commit
    registry.write_text(yaml.safe_dump(data))
    plan, _, _ = resolver.resolve_frozen_agents(path, **options)
    assert plan.skill_sources[0].commit == commit


@pytest.mark.parametrize("change", ["ref", "source", "skills", "missing"])
def test_changed_declaration_fails_before_acquisition_with_existing_lock(
    tmp_path, monkeypatch, change
):
    resolver, path, _, _, options = case(tmp_path, monkeypatch)
    plan, _, _ = resolver.resolve_frozen_agents(path, **options)
    write_execution_lock(path.parent / "goon.lock", build_execution_lock(plan))
    registry = path.parent / ".goonskills.yml"
    data = yaml.safe_load(registry.read_text())
    if change == "missing":
        registry.unlink()
    else:
        data["sources"]["team"][change] = {
            "ref": "other",
            "source": "https://example.test/other.git",
            "skills": ["skills/review"],
        }[change]
        registry.write_text(yaml.safe_dump(data))

    def unavailable(*args, **kwargs):
        raise AssertionError("stale declaration reached Git acquisition")

    monkeypatch.setattr(GitService, "run", unavailable)
    with pytest.raises(FleetPlanInvalid, match="Skill"):
        resolver.resolve_frozen_agents(path, locked_skills=plan.skill_sources, **options)


def test_failed_refresh_preserves_lock_and_cleans_partial_checkout(tmp_path, monkeypatch, capsys):
    _, path, remote, _, options = case(tmp_path, monkeypatch)
    # The CLI uses the shipped catalogue; keep the same authored Goon locally.
    path.write_text("goons:\n- import: agent/review\n  from: goons\n  with: {skill: team/review}\n")
    from oh_goon.core.models.repository import RepositoryIdentity

    product = options["repository_root"] / "product"
    (product / ".git").mkdir()
    league = tmp_path / "league.yml"
    league.write_text(yaml.safe_dump({"version": 1, "folders": [str(product)]}))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(league))
    selection = path.parent / "run.yml"
    selection.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "source": {"kind": "file-plan", "path": "plan.yml"},
                "target": {"repository_id": RepositoryIdentity(path=product).repository_id},
                "inputs": {"repo": "local:product"},
                "agent": {
                    "model_profile": "fixture",
                    "acceptance": "review",
                    "repository_root": str(options["repository_root"]),
                    "skill_root": ".",
                },
            }
        )
    )
    flags = ["lock", str(selection)]
    parser = build_parser()
    args = parser.parse_args(flags)
    prepare_configuration(args)
    assert run_stateless_lock(args) == 0
    lock = (path.parent / "goon.lock").read_bytes()
    git(remote, "branch", "-m", "moved")
    args = parser.parse_args(flags + ["--update-skills"])
    prepare_configuration(args)
    assert run_stateless_lock(args) == 2
    assert (path.parent / "goon.lock").read_bytes() == lock
    diagnostic = capsys.readouterr().out
    assert "PLAN_RESOLUTION_REJECTED" in diagnostic
    assert str(remote) not in diagnostic
    repository = next(skill_source_cache().glob("*.git"))
    assert not (repository / "worktrees").exists()


def test_locked_commit_unavailable_never_falls_back_to_current_branch(tmp_path, monkeypatch):
    resolver, path, remote, _, options = case(tmp_path, monkeypatch)
    plan, _, _ = resolver.resolve_frozen_agents(path, **options)
    write_execution_lock(path.parent / "goon.lock", build_execution_lock(plan))
    shutil.rmtree(skill_source_cache())
    shutil.rmtree(remote / ".git")
    git(remote, "init", "-q", "-b", "main")
    (remote / "skills/review/SKILL.md").write_text("# Unrelated replacement\n")
    git(remote, "add", ".")
    git(remote, "commit", "-qm", "replacement")
    with pytest.raises(FleetPlanInvalid, match="Git"):
        resolver.resolve_frozen_agents(path, locked_skills=plan.skill_sources, **options)
    assert not tuple(skill_source_cache().glob("*.git"))


def test_two_versions_of_one_repository_use_distinct_worktrees(tmp_path, monkeypatch):
    resolver, path, remote, commit, options = case(tmp_path, monkeypatch)
    git(remote, "tag", "v1")
    (remote / "skills/review/SKILL.md").write_text("# New review\n")
    git(remote, "add", ".")
    git(remote, "commit", "-qm", "next")
    registry = path.parent / ".goonskills.yml"
    registry.write_text(
        yaml.safe_dump(
            {
                "sources": {
                    "team": {"source": remote.as_uri(), "ref": "main", "skills": ["skills/review"]},
                    "old": {"source": remote.as_uri(), "ref": "v1", "skills": ["skills/design"]},
                }
            }
        )
    )
    path.write_text(path.read_text().replace("team/design", "old/design"))
    plan, handoffs, _ = resolver.resolve_frozen_agents(path, **options)
    assert {pin.name: pin.commit for pin in plan.skill_sources} == {
        "old": commit,
        "team": git(remote, "rev-parse", "HEAD"),
    }
    assert len(handoffs) == 2
    assert len(tuple(skill_source_cache().glob("*.git"))) == 1


def test_frozen_handoff_consumes_resolved_pins_without_reopening_registry_or_git(
    tmp_path, monkeypatch
):
    resolver, path, _, _, options = case(tmp_path, monkeypatch)
    path.write_text("inputs: [repo, skill, acceptance]\ngoons: [agent/review]\n")
    options["inputs"]["skill"] = "team/review"
    plan, handoffs, _ = resolver.resolve_frozen_agents(path, **options)
    handoff = next(iter(handoffs.values()))
    (path.parent / ".goonskills.yml").unlink()
    shutil.rmtree(skill_source_cache())

    def unavailable(*args, **kwargs):
        raise AssertionError("frozen source delivery reacquired Git source")

    monkeypatch.setattr(GitService, "run", unavailable)
    restored, delivery, _ = resolver.resolve_frozen_agent(
        path,
        handoff=handoff,
        locked_skills=plan.skill_sources,
        **options,
    )
    assert delivery is handoff
    verify_execution_lock(build_execution_lock(plan), build_execution_lock(restored))


@pytest.mark.parametrize("failure", ["missing-skill", "cancelled"])
def test_failed_capture_closes_native_worktree(tmp_path, monkeypatch, failure):
    resolver, path, remote, _, options = case(tmp_path, monkeypatch)
    if failure == "missing-skill":
        (remote / "skills/review/SKILL.md").unlink()
        git(remote, "add", ".")
        git(remote, "commit", "-qm", "missing Skill")
        error = FleetPlanInvalid
    else:

        def cancelled(*args, **kwargs):
            raise OperationCancelledError("capture cancelled")

        monkeypatch.setattr(
            "oh_goon.services.agent_authoring_sources._snapshot_authorized_tree", cancelled
        )
        error = OperationCancelledError
    with pytest.raises(error):
        resolver.resolve_frozen_agents(path, **options)
    repository = next(skill_source_cache().glob("*.git"))
    assert not (repository / "worktrees").exists()
    assert not (path.parent / "goon.lock").exists()


def test_explicit_skill_root_does_not_discover_an_unselected_lock(tmp_path, monkeypatch):
    resolver, path, _, commit, options = case(tmp_path, monkeypatch)
    (options["skill_root"] / "goon.lock").write_text("unselected ambient lock bytes\n")
    plan, _, _ = resolver.resolve_frozen_agents(path, **options)
    assert plan.skill_sources[0].commit == commit


@pytest.mark.parametrize("invalid", ["missing", "invalid", "unknown"])
def test_caller_rejections_precede_remote_skill_acquisition(tmp_path, monkeypatch, invalid):
    import oh_goon.services.agent_skill_registry as registry

    resolver, path, _, _, options = case(tmp_path, monkeypatch)
    # A real valid Git/registry/source-capture baseline proves the rejected
    # mutations are the caller contract rather than an unusable fixture.
    baseline, deliveries, _ = resolver.resolve_frozen_agents(path, **options)
    assert len(baseline.skill_sources) == 1
    assert len(deliveries) == 2
    inputs = dict(options["inputs"])
    if invalid == "missing":
        inputs.pop("repo")
    elif invalid == "invalid":
        inputs["acceptance"] = True
    else:
        inputs["unknown_caller"] = "not-selected"
    options["inputs"] = inputs

    def acquire(*args, **kwargs):
        pytest.fail("invalid caller inputs reached remote Skill acquisition")

    monkeypatch.setattr(registry, "prepare_skill_source", acquire)
    with pytest.raises(FleetPlanInvalid):
        resolver.resolve_frozen_agents(path, **options)
