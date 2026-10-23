"""Native-service source capture rejects private state before opening its bytes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_source import FrozenSourceTree
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.agent_authoring_sources import _snapshot_authorized_tree
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def source_case(tmp_path: Path) -> tuple[FleetPlanResolver, Path, dict[str, Any], Path, Path]:
    package = tmp_path / "goons" / "agents" / "source-privacy"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# agents/source-privacy\n")
    (package / "goon.yml").write_text(
        "inputs:\n"
        "  repo: {type: repository_reference}\n"
        "  skill: {type: skill_reference}\n"
        "  acceptance: {type: acceptance_profile}\n"
        "steps:\n- run: echo not-executed\n"
    )
    plan = tmp_path / "plan.yml"
    plan.write_text("inputs: [repo, skill, acceptance]\ngoons: [agents/source-privacy]\n")
    repository = tmp_path / "repositories" / "team" / "app"
    repository.mkdir(parents=True)
    (repository / "main.py").write_text("SOURCE")
    skill = tmp_path / "skills" / "team" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Source privacy review\n")
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(tmp_path / "goons"))
    _, source_sha = resolver.input_definitions_with_source(plan)
    options: dict[str, Any] = {
        "inputs": {"repo": "local:team/app", "skill": "local:team/review", "acceptance": "review"},
        "repository_root": tmp_path / "repositories",
        "skill_root": tmp_path / "skills",
        "allowed_acceptance": ("review",),
        "expected_source_sha256": source_sha,
    }
    return resolver, plan, options, repository, skill


@pytest.mark.parametrize("entrypoint", ["capture_agent_sources", "explain_agent_sources"])
@pytest.mark.parametrize("owner", ["repository", "skill"])
@pytest.mark.parametrize(
    "native_path",
    [".codex/config.toml", ".opencode/auth.json", "opencode.json", "nested/opencode.jsonc"],
)
def test_native_project_state_rejects_before_its_content_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entrypoint: str, owner: str, native_path: str
) -> None:
    resolver, plan, options, repository, skill = source_case(tmp_path)
    selected_root = repository if owner == "repository" else skill
    sentinel = selected_root / native_path
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("SYNTHETIC_NATIVE_PRIVATE_CONFIGURATION")
    native_home = tmp_path / "native-owned-home"
    native_home.mkdir()
    original_open = os.open
    attempted_private: list[object] = []

    def guarded_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if not flags & os.O_DIRECTORY and Path(path).name == sentinel.name:
            attempted_private.append(path)
            pytest.fail("Native source privacy opened private configuration content")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded_open)
    with pytest.raises(FleetPlanInvalid, match="native authentication/configuration state"):
        getattr(resolver, entrypoint)(plan, private_native_roots=(native_home,), **options)
    assert attempted_private == []


@pytest.mark.parametrize("owner", ["repository", "skill"])
@pytest.mark.parametrize("relationship", ["inside_native_home", "contains_native_home"])
def test_both_source_roots_reject_native_home_overlap_before_any_source_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner: str, relationship: str
) -> None:
    resolver, plan, options, repository, skill = source_case(tmp_path)
    selected_root = repository if owner == "repository" else skill
    native_home = (
        selected_root.parent
        if relationship == "inside_native_home"
        else selected_root / "native-owned"
    )
    native_home.mkdir(exist_ok=True)
    opens: list[object] = []

    def forbidden_open(*args: Any, **_kwargs: Any) -> int:
        opens.append(args[0])
        pytest.fail("Native-home overlap reached source fd acquisition")

    monkeypatch.setattr(os, "open", forbidden_open)
    with pytest.raises(FleetPlanInvalid, match="source scope overlaps"):
        resolver.capture_agent_sources(plan, private_native_roots=(native_home,), **options)
    assert opens == []


def test_cached_source_members_cannot_bypass_new_native_privacy_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, selected = tmp_path / "sources", tmp_path / "sources" / "team" / "app"
    (selected / ".codex").mkdir(parents=True)
    (selected / ".codex" / "config.toml").write_text("SYNTHETIC_NATIVE_CONFIGURATION")
    cache: dict[tuple[str, str], FrozenSourceTree] = {}
    _snapshot_authorized_tree(
        root, "local:team/app", label="repository", max_bytes=4096, captured={}, tree_cache=cache
    )

    def forbidden_open(*_args: Any, **_kwargs: Any) -> int:
        pytest.fail("Cached native configuration was reacquired")

    monkeypatch.setattr(os, "open", forbidden_open)
    with pytest.raises(FleetPlanInvalid, match="native authentication/configuration state"):
        _snapshot_authorized_tree(
            root,
            "local:team/app",
            label="repository",
            max_bytes=4096,
            captured={},
            tree_cache=cache,
            private_native_roots=(tmp_path / "native-home",),
        )


def test_cached_source_scope_cannot_overlap_a_native_home_before_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, selected = tmp_path / "sources", tmp_path / "sources" / "team" / "app"
    selected.mkdir(parents=True)
    (selected / "main.py").write_text("SOURCE")
    cache: dict[tuple[str, str], FrozenSourceTree] = {}
    _snapshot_authorized_tree(
        root, "local:team/app", label="repository", max_bytes=4096, captured={}, tree_cache=cache
    )

    def forbidden_open(*_args: Any, **_kwargs: Any) -> int:
        pytest.fail("Cached native-home scope was reacquired")

    monkeypatch.setattr(os, "open", forbidden_open)
    with pytest.raises(FleetPlanInvalid, match="source scope overlaps"):
        _snapshot_authorized_tree(
            root,
            "local:team/app",
            label="repository",
            max_bytes=4096,
            captured={},
            tree_cache=cache,
            private_native_roots=(selected,),
        )


def test_empty_native_directories_preserve_exact_source_members_on_repeat_capture(
    tmp_path: Path,
) -> None:
    resolver, plan, options, repository, skill = source_case(tmp_path)
    roots = (tmp_path / "native-owned-home",)
    before = resolver.capture_agent_sources(plan, private_native_roots=roots, **options)
    (repository / ".codex").mkdir()
    (skill / "empty-native" / ".opencode").mkdir(parents=True)
    after = resolver.capture_agent_sources(plan, private_native_roots=roots, **options)
    assert after.repository == before.repository
    assert after.skill == before.skill
    assert (repository / ".codex").is_dir()
    assert (skill / "empty-native" / ".opencode").is_dir()


def test_cached_source_members_survive_empty_native_artifacts_without_reacquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, selected = tmp_path / "sources", tmp_path / "sources" / "team" / "app"
    selected.mkdir(parents=True)
    (selected / "main.py").write_text("SOURCE")
    cache: dict[tuple[str, str], FrozenSourceTree] = {}
    scopes = (tmp_path / "native-owned-home",)
    before = _snapshot_authorized_tree(
        root,
        "local:team/app",
        label="repository",
        max_bytes=4096,
        captured={},
        tree_cache=cache,
        private_native_roots=scopes,
    )
    (selected / ".codex").mkdir()

    def forbidden_open(*_args: Any, **_kwargs: Any) -> int:
        pytest.fail("Frozen cache membership was reacquired")

    monkeypatch.setattr(os, "open", forbidden_open)
    after = _snapshot_authorized_tree(
        root,
        "local:team/app",
        label="repository",
        max_bytes=4096,
        captured={},
        tree_cache=cache,
        private_native_roots=scopes,
    )
    assert after["members"] == before["members"]
    assert after["content_identity"] == before["content_identity"]


@pytest.mark.parametrize("state_kind", ["regular_file", "directory_alias"])
def test_reserved_native_state_files_and_aliases_remain_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state_kind: str
) -> None:
    resolver, plan, options, repository, _skill = source_case(tmp_path)
    native_home = tmp_path / "native-owned-home"
    native_home.mkdir()
    private = native_home / "auth.json"
    private.write_text("SYNTHETIC_PRIVATE_AUTHENTICATION")
    reserved = repository / ".codex"
    if state_kind == "regular_file":
        reserved.write_text("SYNTHETIC_PRIVATE_NATIVE_STATE")
    else:
        reserved.symlink_to(native_home, target_is_directory=True)
    original_open = os.open

    def guarded_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if not flags & os.O_DIRECTORY and Path(path).name in {".codex", "auth.json"}:
            pytest.fail("Reserved private native state was opened")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded_open)
    with pytest.raises(FleetPlanInvalid, match="native authentication/configuration state"):
        resolver.capture_agent_sources(plan, private_native_roots=(native_home,), **options)
