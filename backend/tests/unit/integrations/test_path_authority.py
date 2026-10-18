from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_step_contract import PlanStepCapabilities
from oh_goon.core.path_authority import (
    ContainedPathSpace,
    RepositoryPathSpace,
    StrictHostPathAuthority,
    repository_relative_parts,
    storage_component,
)
from oh_goon.core.path_layouts import (
    SANDBOX_PATHS,
    ApplicationStatePathLayout,
    ContentStorePathLayout,
    RunPathLayout,
)


def test_repository_paths_reject_absolute_traversal_and_platform_aliases(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    space = RepositoryPathSpace.from_root(repository)

    for value in (
        "/etc/passwd",
        "../other-repo/secret",
        "sub/../../secret",
        r"..\other-repo\secret",
    ):
        with pytest.raises(FleetPlanInvalid):
            space.resolve(value, label="test path")


def test_contained_authoring_navigation_allows_sibling_only_inside_declared_root(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "plans"
    nested = workspace / "jobs"
    modules = workspace / "modules"
    nested.mkdir(parents=True)
    modules.mkdir()
    target = modules / "quality.yml"
    target.write_text("name: quality", encoding="utf-8")

    space = ContainedPathSpace.from_root(workspace)
    assert (
        space.resolve_from(nested, "../modules/quality.yml", label="module", require_exists=True)
        == target.resolve()
    )

    with pytest.raises(FleetPlanInvalid, match="escapes declared path root"):
        space.resolve_from(nested, "../../outside.yml", label="module")


def test_repository_path_symlink_cannot_escape_into_sibling_repository(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (second / "secret.txt").write_text("private", encoding="utf-8")
    try:
        (first / "escape").symlink_to(second, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")

    with pytest.raises(FleetPlanInvalid, match="escapes repository boundary"):
        RepositoryPathSpace.from_root(first).resolve("escape/secret.txt", label="artifact path")


def test_strict_repository_roots_must_not_overlap(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)

    with pytest.raises(FleetPlanInvalid, match="must not overlap"):
        RepositoryPathSpace.from_root(parent).require_isolated_source(
            other_repository_roots=(child,)
        )


def test_strict_repository_roots_reject_canonical_aliases(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    alias = tmp_path / "repo-alias"
    try:
        alias.symlink_to(repository, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")

    with pytest.raises(FleetPlanInvalid, match="must not overlap"):
        RepositoryPathSpace.from_root(repository).require_isolated_source(
            other_repository_roots=(alias,)
        )


def test_strict_repository_rejects_nested_git_root(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    nested = repository / "vendor" / "nested"
    nested.mkdir(parents=True)
    (repository / ".git").mkdir()
    (nested / ".git").mkdir()

    with pytest.raises(FleetPlanInvalid, match="nested Git repository"):
        RepositoryPathSpace.from_root(repository).require_isolated_source()


def test_strict_repository_rejects_external_hard_link(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    outside = tmp_path / "repo-b-secret"
    outside.write_text("secret", encoding="utf-8")
    try:
        (repository / "linked-secret").hardlink_to(outside)
    except OSError:
        pytest.skip("hard links are unavailable on this filesystem")

    with pytest.raises(FleetPlanInvalid, match="hard-link ownership"):
        RepositoryPathSpace.from_root(repository).require_isolated_source()


def test_strict_repository_rejects_hard_links_even_when_both_names_are_inside(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    first = repository / "first"
    first.write_text("same inode", encoding="utf-8")
    try:
        (repository / "second").hardlink_to(first)
    except OSError:
        pytest.skip("hard links are unavailable on this filesystem")

    with pytest.raises(FleetPlanInvalid, match="hard-link ownership"):
        RepositoryPathSpace.from_root(repository).require_isolated_source()


def test_run_storage_keys_do_not_collide_after_human_readable_sanitization(tmp_path: Path) -> None:
    assert storage_component("a/b") != storage_component("a_b")
    layout = RunPathLayout.from_root(tmp_path / "run")
    assert layout.attempt_root("a/b", 1) != layout.attempt_root("a_b", 1)
    assert layout.published_cache("a/b") != layout.published_cache("a_b")


def test_repository_relative_syntax_is_posix_and_canonical() -> None:
    assert (
        str(repository_relative_parts("ci/checks/report.json", label="path"))
        == "ci/checks/report.json"
    )
    with pytest.raises(FleetPlanInvalid, match="'/' separators"):
        repository_relative_parts(r"ci\checks\report.json", label="path")
    for alias in ("./ci/report.json", "ci//report.json", "ci/report.json/", "C:/repo/report.json"):
        with pytest.raises(FleetPlanInvalid):
            repository_relative_parts(alias, label="path")


def test_pipeline_cannot_request_arbitrary_host_filesystem_or_host_docker_authority() -> None:
    for payload in (
        {"read_only_host_paths": ["/srv/another-repository"]},
        {"docker_socket": "/var/run/docker.sock"},
        {"docker_config": "/home/operator/.docker"},
    ):
        with pytest.raises(ValidationError):
            PlanStepCapabilities.model_validate(payload)


def test_sandbox_mount_namespace_has_one_central_owner() -> None:
    assert str(SANDBOX_PATHS.workspace) == "/workspace"
    assert str(SANDBOX_PATHS.home) == "/home/fleet"
    assert str(SANDBOX_PATHS.toolchain) == "/toolchain/bin"


def test_strict_host_authority_resolves_only_from_admitted_search_paths(
    tmp_path: Path,
) -> None:
    trusted = tmp_path / "trusted"
    tools = trusted / "bin"
    rogue = tmp_path / "repo-b"
    tools.mkdir(parents=True)
    rogue.mkdir()
    inspector = trusted / "ldd"
    tool = tools / "git"
    other_tool = rogue / "git"
    for path in (inspector, tool, other_tool):
        path.write_text("tool", encoding="utf-8")
        path.chmod(0o755)

    authority = StrictHostPathAuthority.create(
        trusted_roots=(trusted,),
        tool_search_paths=(tools,),
        runtime_inspector=inspector,
    )
    assert authority.tool("git") == tool.resolve()
    with pytest.raises(FleetPlanInvalid, match="missing required tool"):
        authority.tool("uv")

    with pytest.raises(FleetPlanInvalid, match="outside deployment trusted_host_roots"):
        StrictHostPathAuthority.create(
            trusted_roots=(trusted,),
            tool_search_paths=(rogue,),
            runtime_inspector=inspector,
        )
    with pytest.raises(FleetPlanInvalid, match="must not be the filesystem root"):
        StrictHostPathAuthority.create(
            trusted_roots=(Path("/"),),
            tool_search_paths=(tools,),
            runtime_inspector=inspector,
        )


def test_strict_host_authority_accepts_admitted_nvm_style_node_cli_symlink(
    tmp_path: Path,
) -> None:
    node_root = tmp_path / "node-v24"
    tools = node_root / "bin"
    package_bin = node_root / "lib" / "node_modules" / "npm" / "bin"
    tools.mkdir(parents=True)
    package_bin.mkdir(parents=True)
    inspector = tools / "ldd"
    inspector.write_text("inspector", encoding="utf-8")
    inspector.chmod(0o755)
    npm_entry = package_bin / "npm-cli.js"
    npm_entry.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    npm_entry.chmod(0o755)
    (tools / "npm").symlink_to(npm_entry)

    authority = StrictHostPathAuthority.create(
        trusted_roots=(tools,),
        tool_search_paths=(tools,),
        runtime_inspector=inspector,
    )

    assert authority.tool("npm") == npm_entry.resolve()


def test_strict_host_authority_does_not_admit_non_executable_symlink_target(
    tmp_path: Path,
) -> None:
    trusted = tmp_path / "trusted"
    tools = trusted / "bin"
    target = tmp_path / "package" / "bin" / "npm-cli.js"
    tools.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    inspector = tools / "ldd"
    inspector.write_text("inspector", encoding="utf-8")
    inspector.chmod(0o755)
    target.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    (tools / "npm").symlink_to(target)

    authority = StrictHostPathAuthority.create(
        trusted_roots=(tools,),
        tool_search_paths=(tools,),
        runtime_inspector=inspector,
    )

    with pytest.raises(FleetPlanInvalid, match="missing required tool"):
        authority.tool("npm")


def test_content_store_reference_paths_are_repository_scoped(tmp_path: Path) -> None:
    layout = ContentStorePathLayout.from_root(tmp_path / "store")
    first = layout.reference("repo-" + "1" * 20, "ref-" + "a" * 32)
    second = layout.reference("repo-" + "2" * 20, "ref-" + "a" * 32)
    assert first != second
    assert first.parent != second.parent


def test_application_managed_state_has_one_database_adjacent_owner(tmp_path: Path) -> None:
    database = tmp_path / "state" / "oh-goon.db"
    layout = ApplicationStatePathLayout.from_database(database)

    assert layout.root == database.parent.resolve()
    assert layout.extensions.parent == layout.root
    assert layout.plan_tools.parent == layout.root
    assert layout.content_store.parent == layout.root
    assert layout.run_workspaces.parent == layout.root
    assert layout.local_launches.parent == layout.root
    assert (
        len(
            {
                layout.extensions,
                layout.plan_tools,
                layout.content_store,
                layout.run_workspaces,
                layout.local_launches,
            }
        )
        == 5
    )


def test_strict_repository_rejects_same_filesystem_nested_mount_topology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    mounted = repository / "foreign"
    mounted.mkdir(parents=True)
    monkeypatch.setattr(
        "oh_goon.core.repository_isolation._visible_mount_points",
        lambda: (repository.resolve(), mounted.resolve()),
    )

    with pytest.raises(FleetPlanInvalid, match="nested mount point"):
        RepositoryPathSpace.from_root(repository).require_isolated_source()


def test_strict_repository_ignores_escaping_symlink_inside_transient_backend_venv(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    venv_bin = repository / "backend" / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    outside = tmp_path / "python"
    outside.write_text("host interpreter", encoding="utf-8")
    try:
        (venv_bin / "python").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")

    RepositoryPathSpace.from_root(repository).require_isolated_source()


def test_strict_repository_still_rejects_escaping_symlink_outside_transient_source(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    outside = tmp_path / "secret"
    outside.write_text("secret", encoding="utf-8")
    try:
        (repository / "source-link").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")

    with pytest.raises(FleetPlanInvalid, match="escaping symlink"):
        RepositoryPathSpace.from_root(repository).require_isolated_source()


def test_selected_repository_root_set_uses_indexed_proof_not_pair_enumeration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = tuple(tmp_path / f"repo-{index}" for index in range(8))
    for root in roots:
        root.mkdir()

    def forbidden_pair(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("selected-root set proof must not enumerate unordered pairs")

    monkeypatch.setattr(
        "oh_goon.core.repository_isolation._require_disjoint_repository_pair",
        forbidden_pair,
    )

    assert RepositoryPathSpace.require_disjoint_sources(roots) == tuple(
        root.resolve() for root in roots
    )


def test_selected_repository_root_proof_captures_filesystem_identity_once_per_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = tuple(tmp_path / f"repo-{index}" for index in range(5))
    for root in roots:
        root.mkdir()
    observed: list[Path] = []

    def observe_identity(root: Path) -> tuple[int, int]:
        observed.append(root)
        index = int(root.name.rsplit("-", 1)[1])
        return 7, index + 100

    monkeypatch.setattr(
        "oh_goon.core.repository_isolation._repository_directory_identity",
        observe_identity,
    )

    resolved = RepositoryPathSpace.require_disjoint_sources(roots)

    assert observed == list(resolved)
    assert len(observed) == len(roots)


def test_selected_repository_root_proof_rejects_distinct_paths_with_same_filesystem_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    monkeypatch.setattr(
        "oh_goon.core.repository_isolation._repository_directory_identity",
        lambda _root: (11, 29),
    )

    with pytest.raises(FleetPlanInvalid, match="alias the same filesystem directory"):
        RepositoryPathSpace.require_disjoint_sources((first, second))


def test_selected_repository_root_proof_does_not_call_path_samefile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = tuple(tmp_path / f"repo-{index}" for index in range(4))
    for root in roots:
        root.mkdir()

    def forbidden_samefile(_self: Path, _other: Path) -> bool:
        raise AssertionError("selected-root proof must not issue pairwise samefile calls")

    monkeypatch.setattr(Path, "samefile", forbidden_samefile)

    assert RepositoryPathSpace.require_disjoint_sources(roots) == tuple(
        root.resolve() for root in roots
    )


def test_selected_repository_root_proof_rejects_duplicate_entries_by_position(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()

    with pytest.raises(FleetPlanInvalid, match="must not overlap"):
        RepositoryPathSpace.require_disjoint_sources((repository, repository))


def test_selected_repository_root_index_preserves_nested_loop_first_conflict_order(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    second_nested = second / "nested"
    first_nested = first / "nested"
    for root in (second_nested, first_nested):
        root.mkdir(parents=True)

    # A streaming-by-right-index implementation would encounter (1, 2) first.
    # Historical deterministic pair order is (0, 3), which must remain authoritative.
    with pytest.raises(FleetPlanInvalid) as exc:
        RepositoryPathSpace.require_disjoint_sources((first, second, second_nested, first_nested))

    message = str(exc.value)
    assert str(first.resolve()) in message
    assert str(first_nested.resolve()) in message
    assert str(second.resolve()) not in message


def test_selected_repository_root_index_preserves_first_pair_across_alias_and_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    second_nested = second / "nested"
    alias = tmp_path / "alias"
    for root in (first, second_nested, alias):
        root.mkdir(parents=True, exist_ok=True)

    first_resolved = first.resolve()
    alias_resolved = alias.resolve()

    def identity(root: Path) -> tuple[int, int]:
        if root in (first_resolved, alias_resolved):
            return 31, 41
        return 31, hash(root.parts) & 0x7FFFFFFF

    monkeypatch.setattr(
        "oh_goon.core.repository_isolation._repository_directory_identity",
        identity,
    )

    # (1, 2) overlaps and is discovered earlier by right index, but historical
    # nested-loop authority reaches the alias pair (0, 3) first.
    with pytest.raises(FleetPlanInvalid, match="alias the same filesystem directory") as exc:
        RepositoryPathSpace.require_disjoint_sources((first, second, second_nested, alias))

    message = str(exc.value)
    assert str(first_resolved) in message
    assert str(alias_resolved) in message
    assert str(second.resolve()) not in message


def test_selected_repository_root_proof_has_deterministic_first_conflict(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    nested = first / "nested"
    later = tmp_path / "later"
    later_nested = later / "nested"
    for root in (nested, later_nested):
        root.mkdir(parents=True)

    with pytest.raises(FleetPlanInvalid) as exc:
        RepositoryPathSpace.require_disjoint_sources((first, nested, later, later_nested))

    message = str(exc.value)
    assert str(first.resolve()) in message
    assert str(nested.resolve()) in message
    assert str(later.resolve()) not in message
