from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from oh_goon.integrations.hashmarks import (
    assess_locked_dependencies,
    dependency_package_query_identity,
    observe_locked_dependencies,
    project_dependency_package,
)
from tests.factories.dependency_observations import dependency_snapshot


@pytest.mark.parametrize(
    ("ecosystem", "requested", "canonical", "semantics"),
    [
        ("pypi", " MLFlow ", "mlflow", "pypa-normalized-name.v1"),
        ("pypi", "ML_Flow", "ml-flow", "pypa-normalized-name.v1"),
        ("npm", "ML_Flow", "ML_Flow", "opaque-exact-name.v1"),
        ("npm", "@Scope/ML_Flow", "@Scope/ML_Flow", "opaque-exact-name.v1"),
    ],
)
def test_query_correspondence_preserves_native_ecosystem_semantics_and_provenance(
    ecosystem, requested, canonical, semantics
):
    identity = dependency_package_query_identity(ecosystem, requested)
    assert identity.requested_name == requested
    assert identity.canonical_name == canonical
    assert identity.ecosystem == ecosystem
    assert identity.producer_implementation_identity.startswith("sha256:")
    native = identity.producer_evidence
    assert native["requested_name"] == requested and native["canonical_name"] == canonical
    assert native["semantics"] == semantics
    assert native["identity"] and native["engine"]
    assert native["coordinate_existence"] == "not-observed"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("schema", "hashmarks.dependency-package-query-identity.v999"),
        ("ecosystem", "npm"),
        ("requested_name", "other"),
        ("semantics", "opaque-exact-name.v1"),
        ("scope", "registry-membership"),
        ("coordinate_existence", "observed"),
        ("identity", None),
        ("engine", {"name": "other", "operation": "equality", "version": "1"}),
    ],
)
def test_unrecognized_query_authority_cannot_supply_a_canonical_lookup(
    monkeypatch, field, replacement
):
    import hashmarks

    native = hashmarks.dependency_package_query_identity("pypi", "MLFlow")
    native[field] = replacement
    monkeypatch.setattr(hashmarks, "dependency_package_query_identity", lambda *_args: native)
    with pytest.raises(ValueError, match="query identity authority"):
        dependency_package_query_identity("pypi", "MLFlow")


@pytest.mark.parametrize("canonical", [None, "", "x" * 4097])
def test_invalid_native_canonical_name_cannot_become_a_query(monkeypatch, canonical):
    import hashmarks

    native = hashmarks.dependency_package_query_identity("pypi", "MLFlow")
    native["canonical_name"] = canonical
    monkeypatch.setattr(hashmarks, "dependency_package_query_identity", lambda *_args: native)
    with pytest.raises(ValueError):
        dependency_package_query_identity("pypi", "MLFlow")


def test_alias_inventory_queries_deduplicate_without_rewriting_retained_component_ids(
    tmp_path: Path, monkeypatch
):
    import hashmarks.adapters.uv_dependency as uv_adapter

    original = uv_adapter.uv_lock_dependency_observation
    dependency_snapshot(tmp_path, ("ML_Flow", "1.0.0"), ("ml-flow", "2.0.0"))

    def opaque_components(**arguments):
        adapter = original(**arguments)
        identities = {
            row["component_id"]: "opaque-component:" + str(index)
            for index, row in enumerate(adapter["components"])
        }
        for component in adapter["components"]:
            component["component_id"] = identities[component["component_id"]]
        for selection in adapter["selections"]:
            selection["component_id"] = identities[selection["component_id"]]
        return adapter

    monkeypatch.setattr(uv_adapter, "uv_lock_dependency_observation", opaque_components)
    observed = observe_locked_dependencies(tmp_path)
    assert observed.evidence.availability == "available"
    assert len(observed.dependencies) == 2
    assert {item.package_name for item in observed.dependencies} == {"ml-flow"}
    components = observed.producer_observation["components"]
    assert {row["name"] for row in components} == {"ML_Flow", "ml-flow", "fixture-application"}
    inventory_ids = {
        row["component_id"] for row in components if row["name"] != "fixture-application"
    }
    assert {item.component_id for item in observed.dependencies} == inventory_ids
    assert all(item.component_id.startswith("opaque-component:") for item in observed.dependencies)
    assert {item.node_id for item in observed.dependencies} == {
        row["node_id"]
        for row in observed.producer_observation["selections"]
        if row["component_id"] in inventory_ids
    }
    assert assess_locked_dependencies(tmp_path, observed).evidence.freshness == "current"


def _retained_snapshot(observed, retained, native):
    projection = native.dependencies.projection(
        retained, packages=observed.producer_projection["request"]["packages"], limit=256
    )
    return observed.model_copy(
        update={"producer_observation": retained, "producer_projection": projection}
    )


def test_lock_inventory_excludes_application_and_does_not_read_declarations(tmp_path: Path):
    observed = dependency_snapshot(tmp_path, ("requests", "2.32.0"))
    omitted = tmp_path / "packages" / "omitted"
    omitted.mkdir(parents=True)
    (omitted / "pyproject.toml").write_text('[project]\ndependencies=["excluded-package"]\n')
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv.workspace]\nmembers=["packages/*"]\nexclude=["packages/omitted"]\n'
    )
    current = observe_locked_dependencies(tmp_path)
    assert [item.package_name for item in current.dependencies] == ["requests"]
    assert current.evidence.absence_admissible
    assert current.sources == observed.sources
    assert current.dependencies[0].relationships
    assert current.producer_observation["roots"]


def test_unsupported_and_missing_locks_are_not_empty_complete_inventories(tmp_path: Path):
    missing = observe_locked_dependencies(tmp_path)
    assert missing.evidence.availability == "unavailable"
    (tmp_path / "uv.lock").write_text("version=999\nrevision=1\n")
    unsupported = observe_locked_dependencies(tmp_path)
    assert unsupported.evidence.availability == "unsupported"
    assert not unsupported.evidence.absence_admissible
    assert unsupported.dependencies == ()


def test_explicit_empty_lock_path_cannot_select_the_default_inventory(tmp_path: Path):
    assert dependency_snapshot(tmp_path, ("shared", "1.2.0")).dependencies
    refused = observe_locked_dependencies(tmp_path, lock_path="")
    assert refused.evidence.availability != "available"
    assert refused.dependencies == () and not refused.evidence.absence_admissible


def test_retained_lock_evidence_becomes_stale_after_an_equal_size_edit(tmp_path: Path):
    observed = dependency_snapshot(tmp_path, ("requests", "2.32.0"))
    path = tmp_path / "uv.lock"
    path.write_text(path.read_text().replace("2.32.0", "2.32.1"))
    result = assess_locked_dependencies(tmp_path, observed)
    assert result.evidence.freshness == "stale"
    assert not result.evidence.absence_admissible


@pytest.mark.parametrize(
    ("coverage", "expected"),
    [("missing-context", "unknown"), ("incomplete", "incomplete"), ("truncated", "incomplete")],
)
def test_producer_inventory_coverage_controls_lock_absence(
    tmp_path: Path, coverage: str, expected: str
):
    from hashmarks import CodeMap

    observed = dependency_snapshot(tmp_path, ("requests", "2.32.0"))
    adapter = deepcopy(observed.adapter_observation)
    if coverage == "missing-context":
        adapter["contexts"].append("uncovered")
    else:
        for row in adapter["coverage"]:
            if row["kind"] == "resolved-inventory":
                row["completeness"] = "incomplete"
                row["truncation"] = "truncated" if coverage == "truncated" else "complete"
    with CodeMap(tmp_path, state_dir=tmp_path / "observer-state") as native:
        retained = native.dependencies.observe(adapter)
        snapshot = _retained_snapshot(observed, retained, native)
    result = assess_locked_dependencies(tmp_path, snapshot)

    assert result.evidence.freshness == "current"
    assert result.evidence.completeness == expected
    assert not result.evidence.absence_admissible


def test_matching_claim_does_not_repair_unknown_native_lock_observation(tmp_path: Path):
    from hashmarks import CodeMap

    observed = dependency_snapshot(tmp_path, ("requests", "2.32.0"))
    lock = tmp_path / "uv.lock"
    content = lock.read_bytes()
    lock.unlink()
    with CodeMap(tmp_path, state_dir=tmp_path / "observer-state") as native:
        retained = native.dependencies.observe(observed.adapter_observation)
        snapshot = _retained_snapshot(observed, retained, native)
    lock.write_bytes(content)
    result = assess_locked_dependencies(tmp_path, snapshot)

    assert result.evidence.freshness == "unknown"
    assert not result.evidence.absence_admissible


@pytest.mark.parametrize("claimed_revision", [None, "0" * 64])
def test_external_revision_claim_does_not_relabel_native_source_freshness(
    tmp_path: Path, claimed_revision: str | None
):
    from hashmarks import CodeMap

    observed = dependency_snapshot(tmp_path, ("requests", "2.32.0"))
    adapter = deepcopy(observed.adapter_observation)
    adapter["repository_inputs"][0]["member_revision"] = claimed_revision
    with CodeMap(tmp_path, state_dir=tmp_path / "observer-state") as native:
        retained = native.dependencies.observe(adapter)
        snapshot = _retained_snapshot(observed, retained, native)
    result = assess_locked_dependencies(tmp_path, snapshot)

    assert result.evidence.freshness == "current"
    assert not result.evidence.absence_admissible


@pytest.mark.parametrize("mutation", ["delete", "deny"])
def test_retained_lock_must_remain_available_for_absence(tmp_path: Path, mutation: str):
    observed = dependency_snapshot(tmp_path, ("requests", "2.32.0"))
    if mutation == "delete":
        (tmp_path / "uv.lock").unlink()
    else:
        (tmp_path / ".hashmarks-context.toml").write_text(
            '[[rule]]\npattern="uv.lock"\nindex=false\n'
        )
    result = assess_locked_dependencies(tmp_path, observed)

    assert result.evidence.availability == "unavailable"
    assert not result.evidence.absence_admissible


def test_conditional_relationships_remain_qualified_facts(tmp_path: Path):
    dependency_snapshot(tmp_path, ("requests", "2.32.0"))
    path = tmp_path / "uv.lock"
    path.write_text(
        path.read_text().replace(
            '{name="requests"}', '{name="requests", marker="sys_platform == \'win32\'"}'
        )
    )
    observed = observe_locked_dependencies(tmp_path)
    assert observed.evidence.availability == "available"
    assert observed.dependencies[0].relationships[0]["marker"] == "sys_platform == 'win32'"


def test_native_policy_denies_lock_acquisition_before_interpretation(tmp_path: Path):
    dependency_snapshot(tmp_path, ("requests", "2.32.0"))
    (tmp_path / ".hashmarks-context.toml").write_text('[[rule]]\npattern="uv.lock"\nindex=false\n')
    result = observe_locked_dependencies(tmp_path)
    assert result.evidence.availability == "unavailable"
    assert not result.evidence.absence_admissible
    assert result.adapter_observation == {}


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlink support unavailable")
def test_symlink_lock_is_not_opened_as_dependency_evidence(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.write_text("not a lock\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "uv.lock").symlink_to(outside)
    result = observe_locked_dependencies(repo)
    assert result.evidence.availability == "unavailable"
    assert result.adapter_observation == {}


@pytest.mark.parametrize(
    "ecosystem,name,version,expression,expected",
    [
        ("pypi", "shared", "2.17.2", "~=2.17", "match"),
        ("pypi", "shared", "2.18.0", ">=2.0,<2.18.0", "nonmatch"),
        ("pypi", "shared", "not-a-version", ">=2.0", "unclassified"),
        ("npm", "shared", "1.2.3", "^1.0.0", "match"),
        ("npm", "shared", "2.0.0", "^1.0.0", "nonmatch"),
        ("npm", "@scope/shared", "1.2.3", ">=1.2.0 <2.0.0", "match"),
        ("npm", "@scope/shared", "not-a-version", "^1.0.0", "unclassified"),
    ],
)
def test_released_producer_classifies_ecosystem_versions_without_local_comparison(
    tmp_path, ecosystem, name, version, expression, expected
):
    snapshot = dependency_snapshot(tmp_path, (name, version), ecosystem=ecosystem)
    result = project_dependency_package(tmp_path, snapshot, name, version_specifier=expression)

    assert result.evidence.freshness == "current"
    packet = result.projection
    assert packet is not None
    package = packet["packages"][0]
    assert package["selector"]["ecosystem"] == ecosystem
    assert package["selector"]["name"] == name
    predicate = package["version_predicate"]
    assert predicate["selections"][0]["state"] == expected
    assert predicate["engine"]["name"] in {"packaging", "node-semver"}
    assert predicate["predicate_identity"]


def test_retained_unknown_is_not_repaired_by_current_raw_lock_bytes(tmp_path):
    snapshot = dependency_snapshot(tmp_path, ("@scope/shared", "1.2.3"), ecosystem="npm")
    packet = deepcopy(snapshot.producer_projection)
    packet["projection_identity"] = "caller-forged"
    forged = snapshot.model_copy(update={"producer_projection": packet})

    with pytest.raises(ValueError, match="identity mismatch"):
        project_dependency_package(tmp_path, forged, "missing")
    assert not assess_locked_dependencies(tmp_path, forged).evidence.absence_admissible


def test_scoped_npm_name_and_version_survive_the_consumer_text_budget(tmp_path):
    name = "@scope/" + "a" * 207
    version = "1.2.3-" + "alpha" * 42
    assert len(name) == 214 and 200 < len(version) < 256
    snapshot = dependency_snapshot(tmp_path, (name, version), ecosystem="npm")
    assert snapshot.evidence.availability == "available"
    assert snapshot.dependencies[0].package_name == name
    assert snapshot.dependencies[0].resolved_version == version
    result = project_dependency_package(tmp_path, snapshot, name, version_specifier="*")
    assert result.projection is not None
    predicate = result.projection["packages"][0]["version_predicate"]
    assert predicate["selections"][0]["selection"]["version"] == version
    assert predicate["selections"][0]["state"] == "nonmatch"
