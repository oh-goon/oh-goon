from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, nullcontext
from importlib import metadata
from pathlib import Path
from shutil import copytree
from typing import Any, cast

import pytest
from hashmarks import CodeMap

from oh_goon.integrations.hashmarks.dependencies import (
    observe_changed_dependency_consumers,
    require_current_dependency_environment,
    revalidate_changed_dependency_consumers,
)
from oh_goon.integrations.hashmarks.development_verification import nominate_changed_verification

_LOCK_PATH = "backend/uv.lock"
_SOURCE_PATH = "backend/src/app/feature.py"


def _lock(version: str) -> bytes:
    return (
        'version=1\nrevision=1\n[[package]]\nname="fixture-app"\nversion="1"\n'
        'source={editable="."}\ndependencies=[{name="sample-distribution"}]\n'
        f'[[package]]\nname="sample-distribution"\nversion="{version}"\n'
        'source={registry="https://pypi.org/simple"}\n'
    ).encode()


@pytest.fixture
def distribution_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    root = tmp_path / "repository"
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    files = {
        "backend/pyproject.toml": (
            '[project]\nname="fixture-app"\nversion="1"\n'
            '[tool.setuptools.package-dir]\n""="src"\n'
            '[tool.pytest.ini_options]\ntestpaths=["tests"]\n'
        ),
        "backend/src/app/__init__.py": "",
        _SOURCE_PATH: (
            "from different_import.api import answer\ndef selected():\n    return answer()\n"
        ),
        "backend/tests/test_feature.py": (
            "from app.feature import selected\ndef test_selected():\n    assert selected() == 42\n"
        ),
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (root / _LOCK_PATH).write_bytes(_lock("2.0"))
    info = tmp_path / "installed" / "sample_distribution-2.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: sample-distribution\nVersion: 2.0\n"
    )
    (info / "RECORD").write_text(
        "different_import/__init__.py,,\ndifferent_import/api.py,,\n"
        f"{info.name}/METADATA,,\n{info.name}/RECORD,,\n"
    )
    package = info.parent / "different_import"
    package.mkdir()
    (package / "__init__.py").write_text("raise AssertionError('package code must not execute')\n")
    original = metadata.distributions

    def discover(*, name: str) -> Iterator[metadata.Distribution]:
        if name == "sample-distribution":
            yield metadata.Distribution.at(info)
        else:
            yield from original(name=name)

    monkeypatch.setattr(metadata, "distributions", discover)
    return root, info


def _observe(
    native: CodeMap, root: Path, paths: tuple[str, ...] = (_SOURCE_PATH,)
) -> dict[str, Any]:
    return native.reads.observe(
        lambda _scope: observe_changed_dependency_consumers(
            native,
            root=root,
            lock_path=_LOCK_PATH,
            before_lock=_lock("1.0"),
            base_revision="a" * 40,
            source_paths=paths,
        )
    )


def test_native_metadata_joins_distribution_and_different_import_name(
    distribution_repository: tuple[Path, Path], tmp_path: Path
) -> None:
    root, info = distribution_repository
    with CodeMap(root, state_dir=tmp_path / "index") as native:
        result = _observe(native, root)
        assert (
            revalidate_changed_dependency_consumers(native, result, root=root)
            == result["after_assessment"]
        )
    assert result["before_assessment"]["source_scope"]["source_equivalence"] == "mismatch"
    assert result["after_assessment"]["source_scope"]["source_equivalence"] == "proven"
    assert result["delta"]["selections_added"] == result["changed_node_ids"]
    assert (
        result["delta"]["component_selection_transitions"][0]["component_id"]
        == "sample-distribution"
    )
    assert result["metadata"][0]["metadata_path"] == str(info / "METADATA")
    consumer = result["consumer_packets"][0]["consumers"][0]
    assert consumer["source_path"] == _SOURCE_PATH
    assert consumer["import_target"] == "different_import.api.answer"
    assert consumer["distribution_state"] == "resolved-unique"
    assert consumer["distribution_nodes"] == result["changed_node_ids"]
    assert consumer["ownership_completeness"] == "incomplete"
    assert result["consumer_scope"]["completeness"] == "incomplete"
    assert result["consumer_scope"]["negative_evidence"] == "not-admissible"


def test_dependency_nominations_keep_actual_source_owner_after_reopen(
    distribution_repository: tuple[Path, Path],
) -> None:
    root, _info = distribution_repository
    base = {"lock": _lock("1.0"), "base_revision": "a" * 40, "lock_path": _LOCK_PATH}
    cold = nominate_changed_verification(root, (_LOCK_PATH,), dependency_base=base)
    reopened = nominate_changed_verification(root, (_LOCK_PATH,), dependency_base=base)
    for packet in (cold, reopened):
        observation = packet["observations"][0]
        assert observation["state"] == "LINKED", packet.get("dependency_changes")
        assert observation["nominations"][0]["nomination"]["verification_target"] == {
            "path": "backend/tests/test_feature.py",
            "test_symbol": None,
        }
        chain = packet["dependency_changes"]["chains"][0]
        assert chain["source_path"] == _SOURCE_PATH
        assert chain["changed_verification"]["changed_paths"] == [_SOURCE_PATH]
        assert chain["changed_verification"]["negative_evidence_admissible"] is False
        projected = chain["nomination"]
        assert "context" not in projected
        assert projected["relationships"]
        assert all(
            relation in chain["changed_verification"]["changes"][0]["relationships"]
            for relation in projected["relationships"]
        )
        assert packet["dependency_changes"]["consumer_scope"]["completeness"] == "incomplete"
    assert reopened == cold


def test_dependency_source_preparation_reuses_one_inventory_before_composite_read(
    distribution_repository: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _info = distribution_repository
    base = {"lock": _lock("1.0"), "base_revision": "a" * 40, "lock_path": _LOCK_PATH}
    with CodeMap(root) as native:

        def selected_map(workspace: Path) -> AbstractContextManager[CodeMap]:
            assert workspace == root
            return nullcontext(native)

        monkeypatch.setattr("hashmarks.CodeMap", selected_map)
        cold = nominate_changed_verification(root, (_LOCK_PATH,), dependency_base=base)
        assert cold["observations"][0]["state"] == "LINKED"
        (root / _SOURCE_PATH).write_text(
            "from different_import.api import answer\ndef selected():\n    return answer() + 1\n",
            encoding="utf-8",
        )
        assert not native.index.is_current(_SOURCE_PATH)
        inventory = native.files.iter_files
        synchronize = native.index.sync
        observe = native.reads.observe
        inventory_depths: list[int] = []
        prepared: list[tuple[int, tuple[str, ...]]] = []
        observed_current: list[bool] = []

        def track_inventory(prefix: str = "") -> Iterator[Any]:
            if prefix == "backend/src":
                inventory_depths.append(native.reads.depth)
            yield from inventory(prefix)

        def track_synchronization(paths: Any = None, **options: Any) -> Any:
            if paths is not None:
                prepared.append((native.reads.depth, tuple(str(path) for path in paths)))
            return synchronize(paths, **options)

        def require_prepared_read(*arguments: Any, **options: Any) -> Any:
            observed_current.append(native.index.is_current(_SOURCE_PATH))
            assert observed_current[-1]
            return observe(*arguments, **options)

        monkeypatch.setattr(native.files, "iter_files", track_inventory)
        monkeypatch.setattr(native.index, "sync", track_synchronization)
        monkeypatch.setattr(native.reads, "observe", require_prepared_read)
        refreshed = nominate_changed_verification(root, (_LOCK_PATH,), dependency_base=base)
        assert refreshed["observations"][0]["state"] == "LINKED", refreshed["dependency_changes"]
        changes = refreshed["dependency_changes"]
        assert inventory_depths == [0]
        assert all(depth == 0 for depth, _paths in prepared)
        assert observed_current and all(observed_current)
        assert native.index.is_current(_SOURCE_PATH)
        before = {
            member["path"]: member["member_revision"]
            for packet in cold["dependency_changes"]["consumer_packets"]
            for member in packet["source_scope"]["members"]
        }
        after = {
            member["path"]: member["member_revision"]
            for packet in changes["consumer_packets"]
            for member in packet["source_scope"]["members"]
        }
        assert before[_SOURCE_PATH] != after[_SOURCE_PATH]
    assert changes["consumer_scope"]["completeness"] == "incomplete"
    assert changes["consumer_scope"]["negative_evidence"] == "not-admissible"
    assert all(
        packet["source_scope"]["completeness"] == "complete"
        for packet in changes["consumer_packets"]
    )


def test_source_scope_is_chunked_without_losing_native_members(
    distribution_repository: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _info = distribution_repository
    paths = tuple(f"backend/src/consumer_{index}.py" for index in range(65))
    for relative in paths:
        (root / relative).write_text("import different_import.api\n")
    with CodeMap(root, state_dir=tmp_path / "index") as native:
        result = _observe(native, root, paths)
    packets = result["consumer_packets"]
    assert [len(packet["source_scope"]["paths"]) for packet in packets] == [64, 1]
    assert {row["source_path"] for packet in packets for row in packet["consumers"]} == set(paths)
    assert all(packet["source_scope"]["completeness"] == "complete" for packet in packets)
    assert result["consumer_scope"]["paths"] == list(paths)
    assert result["consumer_scope"]["completeness"] == "incomplete"


@pytest.mark.parametrize("failure", ["missing-record", "version-mismatch", "oversized", "symlink"])
def test_distribution_metadata_failure_remains_unavailable(
    distribution_repository: tuple[Path, Path], tmp_path: Path, failure: str
) -> None:
    root, info = distribution_repository
    if failure == "missing-record":
        (info / "RECORD").unlink()
    elif failure == "version-mismatch":
        (info / "METADATA").write_text("Name: sample-distribution\nVersion: 9.0\n")
    elif failure == "oversized":
        with (info / "RECORD").open("r+b") as stream:
            stream.truncate(4 * 1024 * 1024 + 1)
    else:
        (info / "RECORD").rename(info / "original")
        (info / "RECORD").symlink_to(info / "original")
    with CodeMap(root, state_dir=tmp_path / "index") as native:
        with pytest.raises((ValueError, OSError)):
            _observe(native, root)


def test_duplicate_installed_distribution_is_not_silently_selected(
    distribution_repository: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, info = distribution_repository
    monkeypatch.setattr(
        metadata, "distributions", lambda **_kwargs: iter([metadata.Distribution.at(info)] * 2)
    )
    with CodeMap(root, state_dir=tmp_path / "index") as native:
        with pytest.raises(ValueError, match="one installed distribution"):
            _observe(native, root)


def test_record_cannot_redirect_capture_to_another_distribution_metadata_pair(
    distribution_repository: tuple[Path, Path], tmp_path: Path
) -> None:
    root, info = distribution_repository
    other = info.parent / "other-2.0.dist-info"
    other.mkdir()
    (other / "METADATA").write_bytes((info / "METADATA").read_bytes())
    (other / "RECORD").write_text(
        f"different_import/api.py,,\n{other.name}/METADATA,,\n{other.name}/RECORD,,\n"
    )
    (info / "RECORD").write_bytes((other / "RECORD").read_bytes())
    (info / "METADATA").write_text("Name: sample-distribution\nVersion: 1.0\n")
    with CodeMap(root, state_dir=tmp_path / "index") as native:
        with pytest.raises(ValueError, match="redirects metadata origin"):
            _observe(native, root)


def test_new_dependency_component_has_no_replacement_evidence(
    distribution_repository: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _info = distribution_repository
    path = root / _LOCK_PATH
    path.write_bytes(
        path.read_bytes()
        + b'[[package]]\nname="another-distribution"\nversion="1"\n'
        + b'source={registry="https://pypi.org/simple"}\n'
    )
    with CodeMap(root, state_dir=tmp_path / "index") as native:
        with pytest.raises(ValueError, match="not an existing selection replacement"):
            _observe(native, root)


def _required_child_lock(*, scope: str = "required", marker: bool = False) -> bytes:
    entry = (
        '{name="required-child"' + (",marker=\"sys_platform == 'win32'\"" if marker else "") + "}"
    )
    field = {
        "required": "dependencies",
        "optional": "optional-dependencies.feature",
        "development": "dev-dependencies.quality",
    }[scope]
    return (
        _lock("2.0")
        + f"{field}=[{entry}]\n".encode()
        + b'[[package]]\nname="required-child"\nversion="1.0"\n'
        + b'source={registry="https://pypi.org/simple"}\n'
    )


def _install_required_child(info: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    child = info.parent / "required_child-1.0.dist-info"
    child.mkdir()
    (child / "METADATA").write_text("Name: required-child\nVersion: 1.0\n")
    (child / "RECORD").write_text(
        f"child_namespace/__init__.py,,\n{child.name}/METADATA,,\n{child.name}/RECORD,,\n"
    )
    original = metadata.distributions

    def discover(*, name: str) -> Iterator[metadata.Distribution]:
        if name == "required-child":
            yield metadata.Distribution.at(child)
        else:
            yield from original(name=name)

    monkeypatch.setattr(metadata, "distributions", discover)
    return child


def test_existing_dependency_can_introduce_a_native_proven_required_child(
    distribution_repository: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, info = distribution_repository
    child = _install_required_child(info, monkeypatch)
    (root / _LOCK_PATH).write_bytes(_required_child_lock())
    with CodeMap(root, state_dir=tmp_path / "index") as native:
        result = _observe(native, root)
        assert (
            revalidate_changed_dependency_consumers(native, result, root=root)
            == result["after_assessment"]
        )
    introduction = result["dependency_introductions"]
    parent = introduction["parent_node_id"]
    (introduced,) = introduction["node_ids"]
    assert set(result["changed_node_ids"]) == {parent, introduced}
    assert (
        introduction["queries"]["observation_identity"]
        == result["after_observation"]["observation_identity"]
    )
    assert introduction["queries"]["results"][0]["result"] == [[parent, introduced]]
    assert introduction["edges"][0] in result["after_observation"]["relationships"]
    assert introduction["edges"][0]["effective_scope"] == ""
    assert next(row for row in result["metadata"] if row["node_id"] == introduced)[
        "metadata_path"
    ] == str(child / "METADATA")
    consumers = [row for packet in result["consumer_packets"] for row in packet["consumers"]]
    assert consumers and all(row["distribution_nodes"] == [parent] for row in consumers)
    assert result["consumer_scope"]["negative_evidence"] == "not-admissible"


def test_required_child_uses_the_actual_parent_consumer_and_verifier_after_reopen(
    distribution_repository: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, info = distribution_repository
    _install_required_child(info, monkeypatch)
    (root / _LOCK_PATH).write_bytes(_required_child_lock())
    base = {"lock": _lock("1.0"), "base_revision": "a" * 40, "lock_path": _LOCK_PATH}
    cold = nominate_changed_verification(root, (_LOCK_PATH,), dependency_base=base)
    reopened = nominate_changed_verification(root, (_LOCK_PATH,), dependency_base=base)
    assert reopened == cold
    changes = cold["dependency_changes"]
    introduction = changes["dependency_introductions"]
    parent = introduction["parent_node_id"]
    (child,) = introduction["node_ids"]
    chains = {chain["node_id"]: chain for chain in changes["chains"]}
    assert set(chains) == {parent, child}
    assert "consumer_node_id" not in chains[parent]
    assert chains[child]["consumer_node_id"] == parent
    assert chains[child]["consumer"]["distribution_nodes"] == [parent]
    assert chains[child]["consumer"] == chains[parent]["consumer"]
    assert chains[child]["nomination"] == chains[parent]["nomination"]
    assert (
        chains[child]["nomination"]["nomination"]["verification_target"]["path"]
        == "backend/tests/test_feature.py"
    )


def test_introduction_without_an_imported_parent_has_no_verifier_chain(
    distribution_repository: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, info = distribution_repository
    _install_required_child(info, monkeypatch)
    (root / _LOCK_PATH).write_bytes(_required_child_lock())
    (root / _SOURCE_PATH).write_text("def selected():\n    return 42\n")
    evidence = nominate_changed_verification(
        root,
        (_LOCK_PATH,),
        dependency_base={"lock": _lock("1.0"), "base_revision": "a" * 40, "lock_path": _LOCK_PATH},
    )
    assert evidence["observations"][0]["state"] == "UNAVAILABLE"
    assert evidence["dependency_changes"]["chains"] == []


@pytest.mark.parametrize(
    "scope,marker", [("optional", False), ("development", False), ("required", True)]
)
def test_introduced_child_requires_an_unconditional_runtime_edge(
    distribution_repository: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scope: str,
    marker: bool,
) -> None:
    root, info = distribution_repository
    _install_required_child(info, monkeypatch)
    (root / _LOCK_PATH).write_bytes(_required_child_lock(scope=scope, marker=marker))
    with CodeMap(root, state_dir=tmp_path / "index") as native:
        with pytest.raises(ValueError):
            _observe(native, root)


@pytest.mark.parametrize(
    "extra",
    ["direct-root", "new-root", "duplicate-selection", "many-introductions", "two-upgrades"],
)
def test_required_child_introduction_rejects_unaccounted_or_ambiguous_scope(
    distribution_repository: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra: str,
) -> None:
    root, info = distribution_repository
    _install_required_child(info, monkeypatch)
    lock = _required_child_lock()
    expected = "required introduction"
    if extra == "direct-root":
        lock = lock.replace(
            b'dependencies=[{name="sample-distribution"}]',
            b'dependencies=[{name="sample-distribution"},{name="required-child"}]',
        )
    elif extra == "new-root":
        expected = "not an existing selection replacement"
        lock = lock.replace(
            b'name="required-child"\nversion="1.0"\nsource={registry="https://pypi.org/simple"}',
            b'name="required-child"\nversion="1.0"\nsource={editable="."}',
        )
    elif extra == "duplicate-selection":
        expected = "ambiguous or unsupported"
        lock = lock.replace(b'{name="required-child"}', b'{name="required-child",version="1.0"}')
        lock += b'[[package]]\nname="required-child"\nversion="2.0"\nsource={registry="https://pypi.org/simple"}\n'
    elif extra == "many-introductions":
        expected = "one bounded parent"
        for index in range(32):
            lock += f'[[package]]\nname="unrelated-{index}"\nversion="1"\nsource={{registry="https://pypi.org/simple"}}\n'.encode()
    else:
        expected = "one bounded parent"
        before = (
            _lock("1.0")
            + b'[[package]]\nname="another-parent"\nversion="1"\nsource={registry="https://pypi.org/simple"}\n'
        )
        lock += b'[[package]]\nname="another-parent"\nversion="2"\nsource={registry="https://pypi.org/simple"}\n'
    (root / _LOCK_PATH).write_bytes(lock)
    with CodeMap(root, state_dir=tmp_path / "index") as native:
        with pytest.raises(ValueError, match=expected):
            if extra == "two-upgrades":
                native.reads.observe(
                    lambda _scope: observe_changed_dependency_consumers(
                        native,
                        root=root,
                        lock_path=_LOCK_PATH,
                        before_lock=before,
                        base_revision="a" * 40,
                        source_paths=(_SOURCE_PATH,),
                    )
                )
            else:
                _observe(native, root)


@pytest.mark.parametrize(
    "fault", ["incomplete", "omitted", "different-path", "wrong-identity", "boolean-bound"]
)
def test_required_child_keeps_native_query_uncertainty_and_binding(
    distribution_repository: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    root, info = distribution_repository
    _install_required_child(info, monkeypatch)
    (root / _LOCK_PATH).write_bytes(_required_child_lock())
    with CodeMap(root, state_dir=tmp_path / "index") as native:
        original = native.dependencies.queries

        def observe_then_limit(observation: Any, requests: Any) -> dict[str, Any]:
            packet = cast(dict[str, Any], original(observation, requests))
            if requests:
                row = packet["results"][0]
                if fault == "incomplete":
                    row["completeness"] = "incomplete"
                elif fault == "omitted":
                    row["omissions"] = [{"reason": "depth-limit"}]
                elif fault == "different-path":
                    row["result"] = []
                elif fault == "boolean-bound":
                    row["bounds"]["max_depth"] = True
                else:
                    packet["observation_identity"] = "sha256:" + "0" * 64
            return packet

        monkeypatch.setattr(native.dependencies, "queries", observe_then_limit)
        with pytest.raises(ValueError, match="introduction"):
            _observe(native, root)


def test_introduced_distribution_metadata_cannot_drift_during_native_path_observation(
    distribution_repository: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, info = distribution_repository
    child = _install_required_child(info, monkeypatch)
    (root / _LOCK_PATH).write_bytes(_required_child_lock())
    with CodeMap(root, state_dir=tmp_path / "index") as native:
        original = native.dependencies.queries

        def observe_then_change(observation: Any, requests: Any) -> dict[str, Any]:
            packet = cast(dict[str, Any], original(observation, requests))
            if requests:
                (child / "RECORD").write_text(
                    (child / "RECORD").read_text().replace("child_namespace", "changed_namespace")
                )
            return packet

        monkeypatch.setattr(native.dependencies, "queries", observe_then_change)
        with pytest.raises(ValueError, match="metadata changed"):
            _observe(native, root)


def test_retained_external_metadata_is_rechecked_before_execution(
    distribution_repository: tuple[Path, Path], tmp_path: Path
) -> None:
    root, info = distribution_repository
    with CodeMap(root, state_dir=tmp_path / "index") as native:
        result = _observe(native, root)
    require_current_dependency_environment(result)
    (info / "RECORD").write_text((info / "RECORD").read_text().replace("api.py", "new.py"))
    with pytest.raises(ValueError, match="metadata changed"):
        require_current_dependency_environment(result)


def test_metadata_drift_during_consumers_does_not_publish_old_ownership(
    distribution_repository: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, info = distribution_repository
    with CodeMap(root, state_dir=tmp_path / "index") as native:
        original = native.dependencies.consumers

        def observe_then_change(*args: Any, **kwargs: Any) -> dict[str, object]:
            packet = original(*args, **kwargs)
            path = info / "RECORD"
            path.write_text(path.read_text().replace("api.py", "new.py"))
            return packet

        monkeypatch.setattr(native.dependencies, "consumers", observe_then_change)
        with pytest.raises(ValueError, match="metadata changed"):
            _observe(native, root)


def test_earlier_chunk_source_cannot_drift_during_a_later_chunk(
    distribution_repository: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _info = distribution_repository
    paths = tuple(f"backend/src/consumer_{index}.py" for index in range(65))
    for relative in paths:
        (root / relative).write_text("import different_import.api\n")
    with CodeMap(root, state_dir=tmp_path / "index") as native:
        original = native.dependencies.consumers

        def observe_then_change(*args: Any, **kwargs: Any) -> dict[str, object]:
            packet = original(*args, **kwargs)
            if len(kwargs["source_paths"]) == 1:
                (root / paths[0]).write_text("import unrelated_module\n")
            return packet

        monkeypatch.setattr(native.dependencies, "consumers", observe_then_change)
        with pytest.raises(ValueError, match="consumer source changed"):
            _observe(native, root, paths)


def test_retained_lock_and_foreign_workspace_are_rejected(
    distribution_repository: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _info = distribution_repository
    with CodeMap(root, state_dir=tmp_path / "index") as native:
        result = _observe(native, root)
        with pytest.raises(ValueError, match="another repository"):
            revalidate_changed_dependency_consumers(native, result, root=tmp_path)
        (root / _LOCK_PATH).write_bytes(_lock("3.0"))
        with pytest.raises(ValueError, match="lock changed"):
            revalidate_changed_dependency_consumers(native, result, root=root)


def test_identical_lock_does_not_transfer_a_current_observation_to_another_repository(
    distribution_repository: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _info = distribution_repository
    with CodeMap(root, state_dir=tmp_path / "index") as native:
        result = _observe(native, root)
    other = tmp_path / "other-repository"
    copytree(root, other)
    with CodeMap(other, state_dir=tmp_path / "other-index") as native:
        with pytest.raises(ValueError, match="repository binding mismatch"):
            native.reads.observe(
                lambda _scope: revalidate_changed_dependency_consumers(native, result, root=other)
            )


def test_unavailable_source_and_native_truncation_cannot_be_closed(
    distribution_repository: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _info = distribution_repository
    with CodeMap(root, state_dir=tmp_path / "index") as native:
        with pytest.raises(ValueError, match="source members are unavailable"):
            _observe(native, root, ("backend/src/missing.py",))
    (root / _SOURCE_PATH).write_text("import different_import.api\n" * 1001)
    with CodeMap(root, state_dir=tmp_path / "second-index") as native:
        with pytest.raises(ValueError, match="truncated"):
            _observe(native, root)
