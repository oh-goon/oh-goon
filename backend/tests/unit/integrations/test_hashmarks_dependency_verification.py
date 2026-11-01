from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.integrations.hashmarks import dependency_verification as owner

_PREFIX = "backend/src/oh_goon/integrations/hashmarks"


def _lock(version: str, *, other_version: str = "1") -> bytes:
    return f'''version = 1
revision = 3
[[package]]
name = "fixture-application"
version = "1"
source = {{ editable = "." }}
dependencies = [{{ name = "hashmarks" }}, {{ name = "other-dependency" }}]
[[package]]
name = "hashmarks"
version = "{version}"
source = {{ registry = "https://example.invalid/simple" }}
[[package]]
name = "other-dependency"
version = "{other_version}"
source = {{ registry = "https://example.invalid/simple" }}
'''.encode()


def _consumer(root: Path, name: str, *, with_test: bool = True) -> None:
    source = root / _PREFIX / f"{name}.py"
    source.write_text(
        "from hashmarks import hash_bytes\n"
        f"def {name}(value: bytes) -> str:\n    return hash_bytes(value).hash\n",
        encoding="utf-8",
    )
    if with_test:
        (root / f"backend/tests/test_{name}.py").write_text(
            f"from oh_goon.integrations.hashmarks.{name} import {name}\n"
            f"def test_{name}():\n    assert {name}(b'hello')\n",
            encoding="utf-8",
        )


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / _PREFIX).mkdir(parents=True)
    (tmp_path / "backend/tests").mkdir()
    for package in ("oh_goon", "oh_goon/integrations", "oh_goon/integrations/hashmarks"):
        (tmp_path / "backend/src" / package / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "backend/pyproject.toml").write_text(
        '[project]\nname = "fixture-application"\nversion = "1"\n'
        '[tool.hatch.build.targets.wheel]\npackages = ["src/oh_goon"]\n'
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\n',
        encoding="utf-8",
    )
    (tmp_path / "backend/uv.lock").write_bytes(_lock("2"))
    _consumer(tmp_path, "bridge")
    # Captured installation input is the boundary under test; native dependency,
    # source, relationship and verification owners remain real throughout.
    artifacts = owner._DistributionMetadata(
        package_root=tmp_path / "installed/hashmarks",
        metadata_path=tmp_path / "installed/hashmarks.dist-info/METADATA",
        record_path=tmp_path / "installed/hashmarks.dist-info/RECORD",
        metadata=b"Metadata-Version: 2.1\nName: hashmarks\nVersion: 2\n\n",
        record=b"hashmarks/__init__.py,,\nhashmarks/digest.py,,\n",
    )
    monkeypatch.setattr(owner, "_installed_hashmarks_metadata", lambda: artifacts)
    return tmp_path


def test_native_dependency_chain_keeps_scoped_facts_and_actual_verifier(repository: Path) -> None:
    result = owner.observe_hashmarks_dependency_change(repository, before_lock=_lock("1"))

    assert result["before_observation"]["repository_inputs"] == []
    assert result["after_observation"]["repository_inputs"][0]["path"] == "backend/uv.lock"
    delta = result["delta"]
    assert delta["comparability"] == "comparable"
    assert [row["component_id"] for row in delta["component_selection_transitions"]] == [
        "hashmarks"
    ]
    assert delta["change_axes"]["adapter_semantics"] == "unchanged"
    projection = result["package_projection"]
    assert projection["assessment"]["source_scope"]["source_equivalence"] == "proven"
    assert projection["assessment"]["resolved_inventory"]["completeness"] == "complete"
    assert projection["consumer_evidence"]["state"] == "observed"
    consumers = projection["consumer_evidence"]["observation"]
    assert consumers["source_scope"]["completeness"] == "complete"
    assert {row["source_path"] for row in consumers["consumers"]} == {f"{_PREFIX}/bridge.py"}
    assert consumers["consumer_scope"]["completeness"] == "incomplete"
    assert consumers["consumer_scope"]["negative_evidence"] == "not-admissible"
    assert projection["negative_evidence"] == "not-admissible"
    assert result["source_discovery"]["coverage"]["repository_wide_completeness"] == "not-claimed"
    verifier = result["consumer_verification"][0]
    assert verifier["changed_path"] == f"{_PREFIX}/bridge.py"
    assert verifier["state"] == "LINKED"
    assert any(
        row["nomination"]["verification_target"]["path"] == "backend/tests/test_bridge.py"
        and row["nomination"]["available"] is True
        for row in verifier["nominations"]
    )
    assert all(
        row["relationship"] in {"direct-reference", "bounded-indirect-reference"}
        for row in result["required_checks"]
    )


def test_unrelated_dependency_transition_is_retained_for_admission(repository: Path) -> None:
    (repository / "backend/uv.lock").write_bytes(_lock("2", other_version="2"))
    result = owner.observe_hashmarks_dependency_change(repository, before_lock=_lock("1"))
    assert {row["component_id"] for row in result["delta"]["component_selection_transitions"]} == {
        "hashmarks",
        "other-dependency",
    }


def test_new_native_consumer_is_discovered_and_receives_its_own_verifier(repository: Path) -> None:
    first = owner.observe_hashmarks_dependency_change(repository, before_lock=_lock("1"))
    _consumer(repository, "new_consumer")
    second = owner.observe_hashmarks_dependency_change(repository, before_lock=_lock("1"))
    assert f"{_PREFIX}/new_consumer.py" not in first["source_discovery"]["paths"]
    assert f"{_PREFIX}/new_consumer.py" in second["source_discovery"]["paths"]
    selected = next(
        row
        for row in second["consumer_verification"]
        if row["changed_path"] == f"{_PREFIX}/new_consumer.py"
    )
    assert any(
        row["nomination"]["verification_target"]["path"] == "backend/tests/test_new_consumer.py"
        for row in selected["nominations"]
    )


def test_untested_consumer_cannot_borrow_an_unrelated_policy_test(repository: Path) -> None:
    _consumer(repository, "unproved", with_test=False)
    result = owner.observe_hashmarks_dependency_change(repository, before_lock=_lock("1"))
    selected = next(
        row
        for row in result["consumer_verification"]
        if row["changed_path"] == f"{_PREFIX}/unproved.py"
    )
    assert selected["state"] == "UNAVAILABLE"
    assert selected["nominations"] == []


def test_unparseable_native_source_remains_incomplete(repository: Path) -> None:
    (repository / _PREFIX / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    result = owner.observe_hashmarks_dependency_change(repository, before_lock=_lock("1"))
    consumers = result["package_projection"]["consumer_evidence"]["observation"]
    assert consumers["source_scope"]["completeness"] == "incomplete"
    assert f"{_PREFIX}/broken.py" in consumers["source_scope"]["unavailable"]


def test_distribution_metadata_must_match_native_lock_selection(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = owner._installed_hashmarks_metadata()
    monkeypatch.setattr(
        owner,
        "_installed_hashmarks_metadata",
        lambda: replace(
            installed, metadata=installed.metadata.replace(b"Version: 2", b"Version: 9")
        ),
    )
    with pytest.raises(ValueError, match="does not match dependency selection"):
        owner.observe_hashmarks_dependency_change(repository, before_lock=_lock("1"))


def test_shadowed_installed_distribution_is_not_used_as_module_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        owner,
        "distribution",
        lambda _name: SimpleNamespace(locate_file=lambda name: tmp_path / name),
    )
    with pytest.raises(ValueError, match="does not belong to the loaded package"):
        owner._installed_hashmarks_metadata()


@pytest.mark.parametrize("restriction", ["index=false", 'visibility="outline"'])
def test_denied_current_lock_is_not_captured(repository: Path, restriction: str) -> None:
    (repository / ".hashmarks-context.toml").write_text(
        f'[[rule]]\npattern="backend/uv.lock"\n{restriction}\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="not an admitted readable source"):
        owner.observe_hashmarks_dependency_change(repository, before_lock=_lock("1"))
