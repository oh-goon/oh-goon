from __future__ import annotations

import tomllib
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from oh_goon.integrations.hashmarks.dependency_verification import (
    observe_hashmarks_dependency_change,
)
from oh_goon.integrations.hashmarks.runtime_contract import loaded_hashmarks_version
from oh_goon.integrations.native_uv_lock import compare_native_uv_dependency_artifacts
from oh_goon.repository_tooling import development_verification
from oh_goon.repository_tooling.development_dependency_policy import (
    HASHMARKS_DEPENDENCY_OBLIGATION,
    HashmarksUpgradeIntent,
    admit_hashmarks_upgrade,
    hashmarks_upgrade_intent,
)
from oh_goon.repository_tooling.release.package_slim import (
    canonical_source_files,
    canonical_source_identity,
)
from oh_goon.repository_tooling.verification_gate_control import (
    VERIFICATION_POLICY_TESTS,
)

_PREFIX = "backend/src/oh_goon/integrations/hashmarks"
_PREVIOUS_VERSION = "0.32.1"


def _release_url(version: str) -> str:
    return (
        "https://github.com/Hugloss/Hashmarks/releases/download/"
        f"v{version}/hashmarks-{version}-py3-none-any.whl"
    )


def _manifest(version: str) -> str:
    return f"""[project]
name = "fixture-application"
version = "1"
requires-python = ">=3.12"
dependencies = ["hashmarks @ {_release_url(version)}", "other-dependency==1"]
[tool.hatch.build.targets.wheel]
packages = ["src/oh_goon"]
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
"""


def _lock(
    version: str,
    *,
    other_version: str = "1",
    wheel_hash: str = "a" * 64,
    sdist_hash: str = "b" * 64,
) -> bytes:
    return f'''version = 1
revision = 3
requires-python = ">=3.12"
[[package]]
name = "fixture-application"
version = "1"
source = {{ editable = "." }}
dependencies = [{{ name = "hashmarks" }}, {{ name = "other-dependency" }}]
[[package]]
name = "hashmarks"
version = "{version}"
source = {{ url = "{_release_url(version)}" }}
[[package]]
name = "other-dependency"
version = "{other_version}"
source = {{ registry = "https://example.invalid/simple" }}
sdist = {{ url = "https://example.invalid/other_dependency-{other_version}.tar.gz", hash = "sha256:{sdist_hash}", size = 100 }}
wheels = [{{ url = "https://example.invalid/other_dependency-{other_version}-py3-none-any.whl", hash = "sha256:{wheel_hash}", size = 200 }}]
'''.encode()


def _repository(root: Path, version: str, *, other_version: str = "1") -> None:
    (root / _PREFIX).mkdir(parents=True)
    (root / "backend/tests").mkdir()
    for package in (
        "oh_goon",
        "oh_goon/integrations",
        "oh_goon/integrations/hashmarks",
    ):
        (root / "backend/src" / package / "__init__.py").write_text(
            "", encoding="utf-8"
        )
    (root / "backend/pyproject.toml").write_text(_manifest(version), encoding="utf-8")
    (root / "backend/uv.lock").write_bytes(_lock(version, other_version=other_version))
    for name in ("bridge", "secondary"):
        (root / _PREFIX / f"{name}.py").write_text(
            "from hashmarks import hash_bytes\n"
            f"def {name}(value: bytes) -> str:\n    return hash_bytes(value).hash\n",
            encoding="utf-8",
        )
        (root / "backend/tests" / f"test_{name}.py").write_text(
            f"from oh_goon.integrations.hashmarks.{name} import {name}\n"
            f"def test_{name}():\n    assert {name}(b'hello')\n",
            encoding="utf-8",
        )


@dataclass(frozen=True)
class _Upgrade:
    before: dict[str, Any]
    after: dict[str, Any]
    intent: HashmarksUpgradeIntent
    packet: dict[str, Any]
    source_paths: tuple[str, ...]
    native_remainder: dict[str, Any]


def _source_paths(root: Path) -> tuple[str, ...]:
    return tuple(
        path.relative_to(root).as_posix()
        for path in canonical_source_files(root)
        if path.relative_to(root).as_posix().startswith(_PREFIX + "/")
        and path.suffix == ".py"
    )


def _remainder(version: str, *, after_lock: bytes | None = None) -> dict[str, Any]:
    return compare_native_uv_dependency_artifacts(
        before_manifest=_manifest(_PREVIOUS_VERSION).encode(),
        before_lock=_lock(_PREVIOUS_VERSION),
        after_manifest=_manifest(version).encode(),
        after_lock=_lock(version) if after_lock is None else after_lock,
        excluded_packages=("hashmarks",),
    )


@pytest.fixture(scope="module")
def upgrade(tmp_path_factory: pytest.TempPathFactory) -> _Upgrade:
    version = loaded_hashmarks_version()
    root = tmp_path_factory.mktemp("hashmarks-upgrade-policy")
    _repository(root, version)
    before, after = (
        tomllib.loads(_manifest(_PREVIOUS_VERSION)),
        tomllib.loads(_manifest(version)),
    )
    intent = hashmarks_upgrade_intent(before, after)
    assert intent is not None
    # Real installed distribution, native uv adapter, source discovery, import
    # ownership and test nominations supply the admission baseline once.
    packet = observe_hashmarks_dependency_change(
        root, before_lock=_lock(_PREVIOUS_VERSION)
    )
    return _Upgrade(
        before, after, intent, packet, _source_paths(root), _remainder(version)
    )


def _admit(upgrade: _Upgrade, packet: dict[str, Any]) -> list[dict[str, Any]]:
    return admit_hashmarks_upgrade(
        packet,
        upgrade.intent,
        producer_implementation_identity=upgrade.packet[
            "producer_implementation_identity"
        ],
        source_paths=upgrade.source_paths,
        native_remainder=upgrade.native_remainder,
    )


def test_native_release_upgrade_preserves_each_actual_consumer_nomination(
    upgrade: _Upgrade,
) -> None:
    before = deepcopy(upgrade.packet)
    admitted = _admit(upgrade, upgrade.packet)

    assert {row["changed_path"] for row in admitted} == {
        "backend/pyproject.toml",
        "backend/uv.lock",
    }
    original = {
        row["changed_path"]: row["nominations"]
        for row in upgrade.packet["consumer_verification"]
    }
    assert set(original) == {f"{_PREFIX}/bridge.py", f"{_PREFIX}/secondary.py"}
    for row in admitted:
        selected = row["nominations"]
        assert {item["consumer_source_path"] for item in selected} == set(original)
        assert {
            item["nomination"]["verification_target"]["path"] for item in selected
        } == {
            "backend/tests/test_bridge.py",
            "backend/tests/test_secondary.py",
        }
        assert all(
            item["relationship"] == "dependency-consumer-verifier" for item in selected
        )
        for item in selected:
            assert any(
                item["nomination"] == native["nomination"]
                and item["consumer_relationship"] == native["relationship"]
                for native in original[item["consumer_source_path"]]
            )
    assert upgrade.packet == before


def test_bounded_acceptance_keeps_unknown_global_consumers(upgrade: _Upgrade) -> None:
    projection = upgrade.packet["package_projection"]
    consumers = projection["consumer_evidence"]["observation"]
    assert consumers["source_scope"]["completeness"] == "complete"
    assert consumers["consumer_scope"]["completeness"] == "incomplete"
    assert consumers["consumer_scope"]["negative_evidence"] == "not-admissible"
    assert projection["completeness"] == "incomplete"
    for row in _admit(upgrade, upgrade.packet):
        assert row["qualification"]["global_completeness"] == "not-claimed"
        assert row["qualification"]["negative_evidence_admissible"] is False


def test_native_unrelated_dependency_upgrade_is_not_admitted(
    upgrade: _Upgrade,
    tmp_path: Path,
) -> None:
    _repository(tmp_path, upgrade.intent.after_version, other_version="2")
    packet = observe_hashmarks_dependency_change(
        tmp_path, before_lock=_lock(_PREVIOUS_VERSION)
    )
    assert {
        row["component_id"]
        for row in packet["delta"]["component_selection_transitions"]
    } == {
        "hashmarks",
        "other-dependency",
    }
    remainder = _remainder(
        upgrade.intent.after_version,
        after_lock=_lock(upgrade.intent.after_version, other_version="2"),
    )
    with pytest.raises(ValueError, match="non-Hashmarks dependency artifacts"):
        _admit(replace(upgrade, native_remainder=remainder), packet)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("producer_implementation_identity",), "sha256:" + "0" * 64),
        (("delta", "comparability"), "unknown"),
        (("delta", "after_resolution_identity"), "sha256:" + "0" * 64),
        (("delta", "change_axes", "coverage"), "changed"),
        (("package_projection", "truncation"), "truncated"),
        (("package_projection", "assessment", "source_scope", "freshness"), "unknown"),
        (
            ("package_projection", "assessment", "source_scope", "source_equivalence"),
            "unknown",
        ),
        (("package_projection", "source_scope", "completeness"), "incomplete"),
        (("package_projection", "consumer_evidence", "omitted_node_count"), 1),
        (
            (
                "package_projection",
                "consumer_evidence",
                "observation",
                "source_scope",
                "completeness",
            ),
            "incomplete",
        ),
        (
            (
                "package_projection",
                "consumer_evidence",
                "observation",
                "consumer_scope",
                "negative_evidence",
            ),
            "admissible",
        ),
        (("consumer_verification", 0, "state"), "UNAVAILABLE"),
        (("consumer_verification", 0, "stale"), True),
        (("consumer_verification", 0, "edit_candidates"), []),
        (("consumer_verification", 0, "nominations"), []),
    ],
)
def test_unqualified_dependency_evidence_fails_closed(
    upgrade: _Upgrade,
    path: tuple[str | int, ...],
    value: object,
) -> None:
    packet = deepcopy(upgrade.packet)
    selected: Any = packet
    for key in path[:-1]:
        selected = selected[key]
    selected[path[-1]] = value
    with pytest.raises(ValueError):
        _admit(upgrade, packet)


def test_a_consumer_cannot_borrow_another_consumers_successful_verifier(
    upgrade: _Upgrade,
) -> None:
    packet = deepcopy(upgrade.packet)
    packet["consumer_verification"] = packet["consumer_verification"][:1]
    with pytest.raises(ValueError, match="lacks its own verification evidence"):
        _admit(upgrade, packet)


def _move_consumer_to_native_pruned_package(root: Path) -> str:
    package = root / _PREFIX / "build"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (root / _PREFIX / "secondary.py").rename(package / "secondary.py")
    (root / "backend/tests/test_secondary.py").write_text(
        "from oh_goon.integrations.hashmarks.build.secondary import secondary\n"
        "def test_secondary():\n    assert secondary(b'hello')\n",
        encoding="utf-8",
    )
    return f"{_PREFIX}/build/secondary.py"


def test_native_pruning_cannot_hide_a_packaged_dependency_consumer(
    upgrade: _Upgrade,
    tmp_path: Path,
) -> None:
    _repository(tmp_path, upgrade.intent.after_version)
    # A package called build is authored source here, but native repository
    # discovery prunes that directory class. Scoped completeness is insufficient.
    hidden = _move_consumer_to_native_pruned_package(tmp_path)
    packet = observe_hashmarks_dependency_change(
        tmp_path, before_lock=_lock(_PREVIOUS_VERSION)
    )

    assert hidden in _source_paths(tmp_path)
    assert hidden not in packet["source_discovery"]["paths"]
    assert (
        packet["package_projection"]["consumer_evidence"]["observation"][
            "source_scope"
        ]["completeness"]
        == "complete"
    )
    with pytest.raises(ValueError, match="source scope"):
        _admit(replace(upgrade, source_paths=_source_paths(tmp_path)), packet)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("requires-python", ">=3.13"),
        ("optional-dependencies", {"extra": ["other-dependency==2"]}),
        ("version", "2"),
    ],
)
def test_manifest_admission_does_not_cover_other_project_changes(
    upgrade: _Upgrade,
    key: str,
    value: object,
) -> None:
    after = deepcopy(upgrade.after)
    after["project"][key] = value
    assert hashmarks_upgrade_intent(upgrade.before, after) is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("dependency-groups", {"test": ["pytest>=9"]}),
        (
            "build-system",
            {"requires": ["hatchling"], "build-backend": "hatchling.build"},
        ),
        ("tool", {"uv": {"sources": {"hashmarks": {"path": "../unreleased"}}}}),
    ],
)
def test_manifest_admission_rejects_changed_dependency_authority(
    upgrade: _Upgrade,
    key: str,
    value: object,
) -> None:
    after = deepcopy(upgrade.after)
    after[key] = value
    assert hashmarks_upgrade_intent(upgrade.before, after) is None


@pytest.mark.parametrize(
    "reference",
    [
        "hashmarks>=0.34.0",
        "hashmarks @ file:///tmp/hashmarks-0.34.0-py3-none-any.whl",
        "hashmarks @ git+https://github.com/Hugloss/Hashmarks.git@main",
        "hashmarks @ https://github.com/another/Hashmarks/releases/download/v0.34.0/hashmarks-0.34.0-py3-none-any.whl",
        "hashmarks @ https://github.com/Hugloss/Hashmarks/releases/download/v0.34.0/hashmarks-0.34.1-py3-none-any.whl",
    ],
)
def test_only_canonical_same_version_release_urls_are_admitted(
    upgrade: _Upgrade,
    reference: str,
) -> None:
    after = deepcopy(upgrade.after)
    after["project"]["dependencies"][0] = reference
    assert hashmarks_upgrade_intent(upgrade.before, after) is None


def test_manifest_admission_rejects_an_unrelated_dependency_bump(
    upgrade: _Upgrade,
) -> None:
    after = deepcopy(upgrade.after)
    after["project"]["dependencies"][1] = "other-dependency==2"
    assert hashmarks_upgrade_intent(upgrade.before, after) is None


def test_manifest_admission_requires_one_changed_hashmarks_reference(
    upgrade: _Upgrade,
) -> None:
    assert hashmarks_upgrade_intent(upgrade.before, upgrade.before) is None
    after = deepcopy(upgrade.after)
    after["project"]["dependencies"].append(after["project"]["dependencies"][0])
    assert hashmarks_upgrade_intent(upgrade.before, after) is None


def _commit(root: Path, message: str) -> str:
    development_verification._git(root, "add", ".")
    development_verification._git(
        root,
        "-c",
        "user.name=Dependency proof fixture",
        "-c",
        "user.email=proof@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        message,
    )
    return development_verification._git(root, "rev-parse", "HEAD").strip()


def _committed_upgrade(root: Path, *, scope: str = "hashmarks") -> tuple[str, str]:
    _repository(root, _PREVIOUS_VERSION)
    for relative in VERIFICATION_POLICY_TESTS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "def test_policy_fixture():\n    assert True\n", encoding="utf-8"
        )
    if scope == "no-consumers":
        for name in ("bridge", "secondary"):
            (root / _PREFIX / f"{name}.py").unlink()
            (root / "backend/tests" / f"test_{name}.py").unlink()
    if scope == "excluded-consumer":
        _move_consumer_to_native_pruned_package(root)
    development_verification._git(root, "init", "-q")
    base = _commit(root, "Native dependency base")
    version = loaded_hashmarks_version()
    manifest = _manifest(version)
    if scope == "another-package":
        manifest = manifest.replace("other-dependency==1", "other-dependency==2")
    (root / "backend/pyproject.toml").write_text(manifest, encoding="utf-8")
    if scope != "manifest-only":
        (root / "backend/uv.lock").write_bytes(
            _lock(
                version,
                other_version="2" if scope == "another-package" else "1",
                wheel_hash=("c" if scope == "wheel-hash" else "a") * 64,
                sdist_hash=("d" if scope == "sdist-hash" else "b") * 64,
            )
        )
    if scope == "frontend":
        (root / "frontend").mkdir()
        (root / "frontend/package-lock.json").write_text(
            '{"name":"fixture-frontend","lockfileVersion":3,"packages":{}}\n',
            encoding="utf-8",
        )
    return base, _commit(root, "Selected dependency candidate")


def test_change_admission_retains_native_dependency_chain_and_policy_protection(
    tmp_path: Path,
) -> None:
    base, candidate = _committed_upgrade(tmp_path)
    plan = development_verification.admit_change(
        tmp_path, base=base, candidate=candidate
    )

    assert plan.base == base
    assert plan.candidate == candidate
    assert plan.changed_paths == ("backend/pyproject.toml", "backend/uv.lock")
    assert plan.static == ("architecture", "package", "python")
    assert plan.policy_status == "REVIEW_REQUIRED"
    assert plan.obligations == (
        "verification-policy protection",
        HASHMARKS_DEPENDENCY_OBLIGATION,
    )
    assert set(plan.pytest) == {
        *VERIFICATION_POLICY_TESTS,
        "backend/tests/test_bridge.py",
        "backend/tests/test_secondary.py",
    }
    assert not plan.unverified
    dependency = plan.evidence["dependency_evidence"]
    assert dependency["admitted"] is True
    native = dependency["native"]
    assert {row["changed_path"] for row in native["consumer_verification"]} == {
        f"{_PREFIX}/bridge.py",
        f"{_PREFIX}/secondary.py",
    }
    assert (
        native["package_projection"]["consumer_evidence"]["observation"][
            "consumer_scope"
        ]["completeness"]
        == "incomplete"
    )
    development_verification.require_merge_selection(tmp_path, plan)


@pytest.mark.parametrize(
    "scope",
    [
        "another-package",
        "frontend",
        "no-consumers",
        "manifest-only",
        "excluded-consumer",
    ],
)
def test_policy_protection_does_not_admit_unsupported_dependency_changes(
    tmp_path: Path,
    scope: str,
) -> None:
    base, candidate = _committed_upgrade(tmp_path, scope=scope)
    plan = development_verification.admit_change(
        tmp_path, base=base, candidate=candidate
    )

    assert set(VERIFICATION_POLICY_TESTS) <= set(plan.pytest)
    assert "DEPENDENCY_CONSUMER_SCOPE_UNVERIFIED" in plan.unverified
    with pytest.raises(ValueError, match="SELECTION_UNVERIFIED"):
        development_verification.require_merge_selection(tmp_path, plan)
    if scope == "frontend":
        # A qualified backend upgrade cannot erase an unrelated lock's unknown scope.
        assert plan.evidence["dependency_evidence"]["admitted"] is True
        assert HASHMARKS_DEPENDENCY_OBLIGATION in plan.obligations
    elif scope == "no-consumers":
        dependency = plan.evidence["dependency_evidence"]
        assert dependency["admitted"] is False
        assert dependency["native"]["consumer_verification"] == []
    elif scope == "excluded-consumer":
        dependency = plan.evidence["dependency_evidence"]
        hidden = f"{_PREFIX}/build/secondary.py"
        assert hidden in dependency["source_inventory"]["paths"]
        assert hidden not in dependency["native"]["source_discovery"]["paths"]
        assert dependency["admitted"] is False


@pytest.mark.parametrize("scope", ["wheel-hash", "sdist-hash"])
def test_change_admission_rejects_unrelated_artifact_changes_despite_same_selections(
    tmp_path: Path,
    scope: str,
) -> None:
    base, candidate = _committed_upgrade(tmp_path, scope=scope)
    plan = development_verification.admit_change(
        tmp_path, base=base, candidate=candidate
    )

    dependency = plan.evidence["dependency_evidence"]
    comparison = dependency["native_artifact_comparison"]
    assert comparison["equivalent"] is False
    assert comparison["before_export"] != comparison["after_export"]
    assert {
        row["component_id"]
        for row in dependency["native"]["delta"]["component_selection_transitions"]
    } == {"hashmarks"}
    assert dependency["admitted"] is False
    assert "DEPENDENCY_CONSUMER_SCOPE_UNVERIFIED" in plan.unverified
    with pytest.raises(ValueError, match="SELECTION_UNVERIFIED"):
        development_verification.require_merge_selection(tmp_path, plan)


def test_dependency_observers_must_capture_the_same_lock_during_a_restored_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, candidate = _committed_upgrade(tmp_path)
    lock = tmp_path / "backend/uv.lock"
    original = lock.read_bytes()
    source_identity = canonical_source_identity(tmp_path)
    native_observe = development_verification.observe_hashmarks_dependency_change

    def observe_other_artifact(root: Path, *, before_lock: bytes) -> dict[str, Any]:
        # uv has already observed the committed bytes. Give the real Hashmarks
        # observer another valid lock, then restore the checkout before admission
        # rechecks source identity. Neither individual observer result is forged.
        lock.write_bytes(_lock(loaded_hashmarks_version(), wheel_hash="e" * 64))
        try:
            return native_observe(root, before_lock=before_lock)
        finally:
            lock.write_bytes(original)

    monkeypatch.setattr(
        development_verification,
        "observe_hashmarks_dependency_change",
        observe_other_artifact,
    )
    plan = development_verification.admit_change(
        tmp_path, base=base, candidate=candidate
    )

    assert lock.read_bytes() == original
    assert canonical_source_identity(tmp_path) == source_identity
    dependency = plan.evidence["dependency_evidence"]
    assert dependency["native_artifact_comparison"]["equivalent"] is True
    assert {
        row["component_id"]
        for row in dependency["native"]["delta"]["component_selection_transitions"]
    } == {"hashmarks"}
    assert (
        dependency["native"]["after_observation"]["repository_inputs"][0][
            "source_equivalence"
        ]
        == "proven"
    )
    assert dependency["admitted"] is False
    assert "same lock inputs" in dependency["reason"]
    assert "DEPENDENCY_CONSUMER_SCOPE_UNVERIFIED" in plan.unverified
