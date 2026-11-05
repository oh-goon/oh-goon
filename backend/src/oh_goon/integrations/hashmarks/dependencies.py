"""Native dependency acquisition and producer-owned projection/requalification."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import metadata as distribution_metadata
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from oh_goon.core.dependency_identity import DependencyEcosystem
from oh_goon.core.models.dependency_fleet import DependencyQueryIdentity
from oh_goon.core.models.fleet_observation import (
    DependencyEvidenceAssessment,
    DependencyObservation,
    DependencyObservationSnapshot,
    ObservationSourceEvidence,
)
from oh_goon.integrations.hashmarks.repository_scope import repository_evidence_scope
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime

if TYPE_CHECKING:
    from hashmarks import CodeMap

_MAX_LOCK_BYTES = 8 * 1024 * 1024
_MAX_DISTRIBUTION_ARTIFACT_BYTES = 4 * 1024 * 1024
_MAX_DISTRIBUTION_METADATA_BYTES = 8 * 1024 * 1024
_MAX_DISTRIBUTION_ROOT_ENTRIES = 8192
_CONSUMER_CHUNK_SIZE = 64
_MAX_REQUIRED_DEPENDENCY_INTRODUCTIONS = 32


@dataclass(frozen=True)
class LockedInventoryAssessment:
    evidence: DependencyEvidenceAssessment
    projection: dict[str, Any] | None = None


def _assessment(assessment: Mapping[str, Any]) -> DependencyEvidenceAssessment:
    """Translate producer-qualified facts without deriving repository semantics."""
    source = assessment["source_scope"]
    inventory = assessment["resolved_inventory"]
    freshness = source["freshness"]
    return DependencyEvidenceAssessment(
        freshness=freshness,
        completeness=inventory["completeness"],
        availability=source["availability"],
        absence_admissible=inventory["negative_evidence"] == "admissible-within-declared-scope",
        reason=None if freshness == "current" else "lock-source-" + freshness,
        producer_assessment=dict(assessment),
    )


def _projection_receipt(
    projection: Mapping[str, Any], producer_implementation_identity: str
) -> dict[str, Any]:
    """Avoid copying the full retained inventory into each package query result.

    Canonical Step evidence retains these raw domains. The producer's requested
    package matches and every qualifier remain present; this disclosure receipt
    is not an envelope accepted by the producer's retained requalification API.
    """
    observation = projection["observation"]
    omitted = ("components", "selections", "inventory", "relationships", "module_ownership")
    return {
        **projection,
        "producer_implementation_identity": producer_implementation_identity,
        "observation": {key: value for key, value in observation.items() if key not in omitted},
        "disclosure": {
            "omitted_observation_fields": list(omitted),
            "reason": "full-raw-domains-retained-in-original-step-observation",
            "requalification_envelope": False,
        },
    }


def dependency_version_predicate(ecosystem: DependencyEcosystem, expression: str) -> dict[str, Any]:
    """Select the producer's ecosystem dialect without interpreting its expression."""
    return {
        "dialect": {"pypi": "pep440", "npm": "npm-semver"}[ecosystem],
        "expression": expression,
    }


def _query_identity(
    fact: Mapping[str, Any],
    *,
    ecosystem: DependencyEcosystem,
    requested_name: str,
    implementation: str,
) -> DependencyQueryIdentity:
    """Bind the supported native query fact without recreating its name semantics."""
    engine = fact.get("engine")
    pypi = ecosystem == "pypi"
    if (
        fact.get("schema") != "hashmarks.dependency-package-query-identity.v1"
        or fact.get("ecosystem") != ecosystem
        or fact.get("requested_name") != requested_name
        or fact.get("semantics") != ("pypa-normalized-name.v1" if pypi else "opaque-exact-name.v1")
        or fact.get("scope")
        != "query-identity-only; retained-component-and-selection-ids-unchanged"
        or fact.get("coordinate_existence") != "not-observed"
        or not isinstance(fact.get("identity"), str)
        or not fact["identity"]
        or not isinstance(engine, Mapping)
        or engine.get("name") != ("packaging" if pypi else "exact-text")
        or engine.get("operation") != ("packaging.utils.canonicalize_name" if pypi else "equality")
        or (
            (not isinstance(engine.get("version"), str) or not engine["version"])
            if pypi
            else engine.get("version") is not None
        )
    ):
        raise ValueError("Unsupported producer dependency query identity authority")
    return DependencyQueryIdentity.model_validate(
        {
            "ecosystem": ecosystem,
            "requested_name": requested_name,
            "canonical_name": fact.get("canonical_name"),
            "producer_implementation_identity": implementation,
            "producer_evidence": dict(fact),
        }
    )


def dependency_package_query_identity(
    ecosystem: DependencyEcosystem, requested_name: str
) -> DependencyQueryIdentity:
    """Consume native ecosystem-qualified matching with its original provenance."""
    from hashmarks import dependency_package_query_identity as native_query_identity

    implementation = require_loaded_hashmarks_runtime()
    return _query_identity(
        native_query_identity(ecosystem, requested_name),
        ecosystem=ecosystem,
        requested_name=requested_name,
        implementation=implementation,
    )


def _dependency_rows(
    package: Mapping[str, Any], *, ecosystem: DependencyEcosystem, lock_path: str
) -> tuple[DependencyObservation, ...]:
    """Translate the producer's explicit positive inventory matches, without joins."""
    return tuple(
        DependencyObservation(
            ecosystem=match["component"]["ecosystem"],
            package_name=package["package_identity"]["canonical_name"],
            component_id=match["component"]["component_id"],
            node_id=match["selection"]["node_id"],
            resolved_version=match["selection"]["version"],
            resolved_source=lock_path,
            package_source=match["selection"]["source"],
            contexts=tuple(row["context"] for row in match["inventory"]),
            relationships=tuple(match["relationships"]),
            producer_package={key: value for key, value in package.items() if key != "matches"}
            | {"matches": [match]},
        )
        for match in package["matches"]
        if match["inventory"]
    )


def observe_locked_dependencies(
    repository: Path,
    *,
    ecosystem: DependencyEcosystem = "pypi",
    lock_path: str | None = None,
) -> DependencyObservationSnapshot:
    from hashmarks.adapters.npm_dependency import npm_lock_dependency_observation
    from hashmarks.adapters.uv_dependency import uv_lock_dependency_observation

    lock_path = (
        {"pypi": "uv.lock", "npm": "package-lock.json"}[ecosystem]
        if lock_path is None
        else lock_path
    )
    try:
        implementation = require_loaded_hashmarks_runtime()
        with tempfile.TemporaryDirectory(prefix="oh-goon-dependency-evidence-") as directory:
            state = Path(directory)
            with repository_evidence_scope(
                repository, state_dir=state, artifact_db=state / "artifacts.sqlite3"
            ) as scope:
                native = scope.native
                admitted = native.files.visible_file(lock_path)
                if admitted is None or admitted.visibility.value != "source":
                    return DependencyObservationSnapshot(
                        ecosystem=ecosystem,
                        evidence=DependencyEvidenceAssessment(
                            availability="unavailable",
                            reason="Selected lock is not an admitted readable source file",
                        ),
                    )
                lock = _current_lock_bytes(native, repository.resolve(), lock_path)
                revision = _captured_member_revision(lock)
                adapter = {
                    "pypi": uv_lock_dependency_observation,
                    "npm": npm_lock_dependency_observation,
                }[ecosystem](
                    lock=lock, repository_inputs=[{"path": lock_path, "member_revision": revision}]
                )
                observation = cast(dict[str, Any], native.dependencies.observe(adapter))
                assessment = _assessment(native.dependencies.assessment(observation))
                # Native name correspondence scopes query deduplication only;
                # retained component/selection identities remain unchanged.
                from hashmarks import dependency_package_query_identity as native_query_identity

                names = dict.fromkeys(
                    (
                        row["ecosystem"],
                        _query_identity(
                            native_query_identity(row["ecosystem"], row["name"]),
                            ecosystem=row["ecosystem"],
                            requested_name=row["name"],
                            implementation=implementation,
                        ).canonical_name,
                    )
                    for row in observation["components"]
                )
                selectors = [{"ecosystem": scope, "name": name} for scope, name in names]
                dependencies: list[DependencyObservation] = []
                retained: dict[str, Any] = {}
                for start in range(0, len(selectors), 64):
                    projection = cast(
                        dict[str, Any],
                        native.dependencies.projection(
                            observation, packages=selectors[start : start + 64], limit=256
                        ),
                    )
                    if not retained:
                        retained = projection
                    for package in projection["packages"]:
                        dependencies.extend(
                            _dependency_rows(package, ecosystem=ecosystem, lock_path=lock_path)
                        )
        return DependencyObservationSnapshot(
            ecosystem=ecosystem,
            sources=(ObservationSourceEvidence(path=lock_path, member_revision=revision),),
            evidence=assessment,
            producer_implementation_identity=implementation,
            adapter_observation=adapter,
            producer_observation=observation,
            producer_projection=retained,
            dependencies=tuple(dependencies) if assessment.availability == "available" else (),
        )
    except (OSError, RuntimeError) as exc:
        return DependencyObservationSnapshot(
            ecosystem=ecosystem,
            evidence=DependencyEvidenceAssessment(availability="unavailable", reason=str(exc)),
        )
    except ValueError as exc:
        return DependencyObservationSnapshot(
            ecosystem=ecosystem,
            evidence=DependencyEvidenceAssessment(availability="unsupported", reason=str(exc)),
        )


def assess_locked_dependencies(
    repository: Path, snapshot: DependencyObservationSnapshot
) -> LockedInventoryAssessment:
    """Ask the producer to bind retained evidence to the current lock source."""
    if snapshot.schema_version != 3:
        return LockedInventoryAssessment(
            DependencyEvidenceAssessment(
                availability="unsupported",
                reason="historical-dependency-observation-refresh-required",
            )
        )
    if snapshot.evidence.availability != "available":
        return LockedInventoryAssessment(
            snapshot.evidence.model_copy(
                update={"freshness": "unknown", "absence_admissible": False}
            )
        )
    try:
        implementation = require_loaded_hashmarks_runtime()
        if snapshot.producer_implementation_identity != implementation:
            return LockedInventoryAssessment(
                DependencyEvidenceAssessment(
                    availability="unsupported", reason="producer-changed-refresh-inventory"
                )
            )
        if not repository.is_dir():
            return LockedInventoryAssessment(
                DependencyEvidenceAssessment(reason="repository-unavailable")
            )
        with tempfile.TemporaryDirectory(prefix="oh-goon-dependency-assessment-") as directory:
            state = Path(directory)
            with repository_evidence_scope(
                repository, state_dir=state, artifact_db=state / "artifacts.sqlite3"
            ) as scope:
                native = scope.native
                if snapshot.producer_projection:
                    projection = cast(
                        dict[str, Any], native.dependencies.requalify(snapshot.producer_projection)
                    )
                    if projection["observation"] != snapshot.producer_observation:
                        raise ValueError("Retained dependency projection changed its observation")
                    return LockedInventoryAssessment(
                        _assessment(projection["assessment"]),
                        _projection_receipt(projection, implementation),
                    )
                # An empty inventory has no component selector. Its retained
                # observation is still requalified by the same producer owner.
                return LockedInventoryAssessment(
                    _assessment(native.dependencies.assessment(snapshot.producer_observation))
                )
    except (OSError, RuntimeError, ValueError) as exc:
        return LockedInventoryAssessment(DependencyEvidenceAssessment(reason=str(exc)))


def project_dependency_package(
    repository: Path,
    snapshot: DependencyObservationSnapshot,
    package_name: str,
    *,
    context: str | None = None,
    version_specifier: str | None = None,
) -> LockedInventoryAssessment:
    """Ask the producer for presence/absence/predicate facts over retained evidence."""
    if snapshot.schema_version != 3:
        raise ValueError("Historical dependency observation requires a fresh producer projection")
    implementation = require_loaded_hashmarks_runtime()
    if snapshot.producer_implementation_identity != implementation:
        raise ValueError("Dependency projection producer changed; refresh inventory")
    selector: dict[str, Any] = {"ecosystem": snapshot.ecosystem, "name": package_name}
    if context is not None:
        selector["context"] = context
    if version_specifier is not None:
        selector["version_predicate"] = dependency_version_predicate(
            snapshot.ecosystem, version_specifier
        )
    with tempfile.TemporaryDirectory(prefix="oh-goon-dependency-projection-") as directory:
        state = Path(directory)
        with repository_evidence_scope(
            repository, state_dir=state, artifact_db=state / "artifacts.sqlite3"
        ) as scope:
            native = scope.native
            if snapshot.producer_projection:
                retained = native.dependencies.requalify(snapshot.producer_projection)
                if retained["observation"] != snapshot.producer_observation:
                    raise ValueError("Retained dependency projection changed its observation")
            projection = cast(
                dict[str, Any],
                native.dependencies.projection(
                    snapshot.producer_observation, packages=[selector], limit=256
                ),
            )
            return LockedInventoryAssessment(
                _assessment(projection["assessment"]),
                _projection_receipt(projection, implementation),
            )


def _captured_member_revision(data: bytes) -> str:
    """Use the producer's byte identity without reinterpreting its hash format."""
    from hashmarks import hash_file

    with tempfile.TemporaryDirectory(prefix="oh-goon-dependency-member-") as directory:
        captured = Path(directory) / "captured"
        captured.write_bytes(data)
        return hash_file(captured).hash


def _bounded_regular_bytes(path: Path, *, limit: int) -> bytes:
    if path.resolve(strict=True) != path or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"dependency artifact is not a contained regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > limit:
            raise ValueError(f"dependency artifact exceeds its regular-file byte budget: {path}")
        data = stream.read(limit + 1)
        if len(data) > limit or _artifact_stat(os.fstat(stream.fileno())) != _artifact_stat(opened):
            raise ValueError(f"dependency artifact changed or exceeded its byte budget: {path}")
    if _artifact_stat(path.stat()) != _artifact_stat(opened) or path.resolve(strict=True) != path:
        raise ValueError(f"dependency artifact changed during observation: {path}")
    return data


def _artifact_stat(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _current_lock_bytes(native: CodeMap, root: Path, lock_path: str) -> bytes:
    from hashmarks.paths import normalize_relative_path

    if root.resolve() != native.workspace:
        raise ValueError("dependency evidence belongs to another repository workspace")
    if normalize_relative_path(lock_path, allow_root=False) != lock_path:
        raise ValueError("dependency lock path must be canonical and repository-relative")
    admitted = native.files.visible_file(lock_path)
    if admitted is None or admitted.visibility.value != "source":
        raise ValueError("dependency lock is not an admitted readable source file")
    return _bounded_regular_bytes(root.resolve() / lock_path, limit=_MAX_LOCK_BYTES)


def _dependency_environment() -> dict[str, Any]:
    return {
        "python_executable": sys.executable,
        "python_prefix": sys.prefix,
        "python_version": sys.version,
        "producer_implementation_identity": require_loaded_hashmarks_runtime(),
        "metadata_discovery": "importlib.metadata.distributions",
    }


def _preflight_distribution_root(root: Path) -> None:
    """Bound the public Distribution.files owner's inputs before it reads RECORD.

    Distribution.locate_file supplies the native installation root. Its public
    file inventory reads RECORD internally, so preflight metadata containers in
    that root without guessing a selected distribution's directory spelling.
    """
    if root.resolve(strict=True) != root or not root.is_dir():
        raise ValueError("installed distribution root is not a canonical directory")
    with os.scandir(root) as entries:
        for position, entry in enumerate(entries):
            if position >= _MAX_DISTRIBUTION_ROOT_ENTRIES:
                raise ValueError("installed distribution discovery exceeds its entry budget")
            if entry.name.endswith(".egg-info"):
                raise ValueError("legacy installed distribution metadata is unsupported")
            if not entry.name.endswith(".dist-info"):
                continue
            directory = Path(entry.path)
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                raise ValueError("installed distribution metadata directory is not regular")
            for name in ("METADATA", "RECORD"):
                path = directory / name
                info = path.lstat()
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_size > _MAX_DISTRIBUTION_ARTIFACT_BYTES
                ):
                    raise ValueError("installed distribution metadata exceeds its file budget")


@dataclass(frozen=True)
class _DistributionCapture:
    provenance: dict[str, Any]
    metadata: bytes
    record: bytes

    def adapter_input(self) -> dict[str, Any]:
        return {
            "node_id": self.provenance["node_id"],
            "metadata": self.metadata,
            "record": self.record,
        }


def _capture_distribution(selection: Mapping[str, Any]) -> _DistributionCapture:
    name = selection["name"]
    distributions = list(islice(distribution_metadata.distributions(name=name), 2))
    if len(distributions) != 1:
        raise ValueError(f"dependency requires one installed distribution: {name}")
    distribution = distributions[0]
    root = Path(str(distribution.locate_file("")))
    _preflight_distribution_root(root)
    files = distribution.files
    if files is None:
        raise ValueError(f"installed distribution has no native file inventory: {name}")
    paths: dict[str, Path] = {}
    for member in files:
        if len(member.parts) != 2 or not member.parts[0].endswith(".dist-info"):
            continue
        if member.name not in {"METADATA", "RECORD"}:
            continue
        path = Path(str(distribution.locate_file(member)))
        if member.name in paths or path != root / str(member):
            raise ValueError("installed distribution metadata origin is ambiguous")
        paths[member.name] = path
    if set(paths) != {"METADATA", "RECORD"} or paths["METADATA"].parent != paths["RECORD"].parent:
        raise ValueError(f"installed distribution requires one METADATA and RECORD pair: {name}")
    metadata = _bounded_regular_bytes(paths["METADATA"], limit=_MAX_DISTRIBUTION_ARTIFACT_BYTES)
    record = _bounded_regular_bytes(paths["RECORD"], limit=_MAX_DISTRIBUTION_ARTIFACT_BYTES)
    for field, raw in (("METADATA", metadata), ("RECORD", record)):
        # RECORD is a declaration and can list a different distribution's files.
        # Confirm that the native discovery owner supplies these exact inputs;
        # read_text performs universal newline translation, but no parsing here.
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        if distribution.read_text(field) != text:
            raise ValueError("installed distribution file inventory redirects metadata origin")
    return _DistributionCapture(
        provenance={
            "node_id": selection["node_id"],
            "component_id": selection["component_id"],
            "name": name,
            "version": selection["version"],
            "discovery": "importlib.metadata.distributions",
            "distribution_root": str(root),
            "metadata_path": str(paths["METADATA"]),
            "record_path": str(paths["RECORD"]),
            "metadata_member_revision": _captured_member_revision(metadata),
            "record_member_revision": _captured_member_revision(record),
        },
        metadata=metadata,
        record=record,
    )


def _capture_distributions(selections: list[Mapping[str, Any]]) -> list[_DistributionCapture]:
    captures = []
    total = 0
    for selection in selections:
        capture = _capture_distribution(selection)
        total += len(capture.metadata) + len(capture.record)
        if total > _MAX_DISTRIBUTION_METADATA_BYTES:
            raise ValueError("installed dependency metadata exceeds its aggregate byte budget")
        captures.append(capture)
    return captures


def _changed_current_selections(
    after: Mapping[str, Any], delta: Mapping[str, Any]
) -> list[dict[str, Any]]:
    if delta.get("comparability") != "comparable" or any(
        delta.get(key)
        for key in (
            "components_removed",
            "components_changed",
            "selections_changed",
        )
    ):
        raise ValueError("dependency delta is not an existing selection replacement")
    transitions = delta["component_selection_transitions"]
    if not 1 <= len(transitions) <= _CONSUMER_CHUNK_SIZE:
        raise ValueError("dependency delta requires 1 to 64 unambiguous selection replacements")
    components = {row["component_id"]: row for row in after["components"]}
    selections = {row["node_id"]: row for row in after["selections"]}
    added: set[str] = set()
    removed: set[str] = set()
    result = []
    for transition in transitions:
        component = components[transition["component_id"]]
        if (
            component["ecosystem"] != "pypi"
            or len(transition["added"]) != 1
            or len(transition["removed"]) != 1
            or transition["changed"]
            or sum(
                selection["component_id"] == component["component_id"]
                for selection in selections.values()
            )
            != 1
        ):
            raise ValueError("dependency replacement has unsupported or ambiguous selection scope")
        node = transition["added"][0]["node_id"]
        added.add(node)
        removed.add(transition["removed"][0]["node_id"])
        result.append({**selections[node], "name": component["name"]})
    introduced_components = delta.get("components_added", [])
    if introduced_components:
        if (
            len(transitions) != 1
            or not 1 <= len(introduced_components) <= _MAX_REQUIRED_DEPENDENCY_INTRODUCTIONS
        ):
            raise ValueError(
                "required dependency introductions require one bounded parent replacement"
            )
        parent = next(iter(added))
        if selections[parent].get("marker") or selections[parent].get("contexts") != ["lock"]:
            raise ValueError("required dependency parent must be unconditional in the lock context")
        _required_parent_edge(after, parent)
        for component_id in introduced_components:
            component = components[component_id]
            candidates = [row for row in selections.values() if row["component_id"] == component_id]
            if component["ecosystem"] != "pypi" or len(candidates) != 1:
                raise ValueError(
                    "required dependency introduction selection is ambiguous or unsupported"
                )
            child = candidates[0]
            if child.get("marker") or child.get("contexts") != ["lock"]:
                raise ValueError(
                    "required dependency introduction must be unconditional in the lock context"
                )
            _required_introduction_edge(after, parent, child["node_id"])
            added.add(child["node_id"])
            result.append({**child, "name": component["name"]})
    if added != set(delta["selections_added"]) or removed != set(delta["selections_removed"]):
        raise ValueError("dependency delta contains unaccounted selection changes")
    for side, nodes in (("added", added), ("removed", removed)):
        if any(row[0] not in nodes for row in delta[f"inventory_{side}"]) or any(
            not nodes.intersection(row[:2]) for row in delta[f"relationships_{side}"]
        ):
            raise ValueError(
                "dependency delta contains unrelated inventory or relationship changes"
            )
    return sorted(result, key=lambda row: row["node_id"])


def _required_parent_edge(observation: Mapping[str, Any], parent: str) -> None:
    roots = {row["node_id"] for row in observation["roots"]}
    required = [
        row
        for row in observation["relationships"]
        if row.get("source") in roots
        and row.get("target") == parent
        and all(
            row.get(key) == expected
            for key, expected in {
                "kind": "dependency",
                "context": "lock",
                "marker": "",
                "effective_scope": "",
            }.items()
        )
    ]
    if len(required) != 1:
        raise ValueError("required dependency parent needs one unconditional runtime root edge")


def _required_introduction_edge(
    observation: Mapping[str, Any], parent: str, child: str
) -> dict[str, Any]:
    roots = {row["node_id"] for row in observation["roots"]}
    if (
        parent in roots
        or child in roots
        or not any(
            row["node_id"] == child and row["context"] == "lock" for row in observation["inventory"]
        )
    ):
        raise ValueError("required dependency introduction must join external inventory selections")
    edges = [row for row in observation["relationships"] if row["target"] == child]
    if len(edges) != 1 or any(
        edges[0].get(key) != expected
        for key, expected in {
            "source": parent,
            "kind": "dependency",
            "context": "lock",
            "marker": "",
            "effective_scope": "",
        }.items()
    ):
        raise ValueError(
            "dependency delta is not an existing selection replacement or required introduction"
        )
    return cast(dict[str, Any], edges[0])


def _introduction_requests(parent: str, nodes: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "operation": "paths",
            "node_id": parent,
            "target_id": child,
            "context": "lock",
            "max_depth": 1,
            "max_results": 2,
            "max_visits": 64,
        }
        for child in nodes
    ]


def validate_required_dependency_introductions(evidence: Mapping[str, Any]) -> dict[str, str]:
    """Check this consumer policy's retained bindings, not producer graph semantics."""
    introduction = evidence.get("dependency_introductions")
    if introduction is None:
        if evidence["delta"].get("components_added"):
            raise ValueError("required dependency introduction evidence is missing")
        return {}
    if not isinstance(introduction, Mapping):
        raise ValueError("required dependency introduction evidence must be a mapping")
    after, delta = evidence["after_observation"], evidence["delta"]
    selections = _changed_current_selections(after, delta)
    transitions = delta["component_selection_transitions"]
    if len(transitions) != 1 or not delta.get("components_added"):
        raise ValueError("required dependency introduction parent is unavailable")
    parent = transitions[0]["added"][0]["node_id"]
    components = set(delta["components_added"])
    nodes = sorted(row["node_id"] for row in selections if row["component_id"] in components)
    if introduction.get("parent_node_id") != parent or introduction.get("node_ids") != nodes:
        raise ValueError("required dependency introduction endpoints differ from the native delta")
    requests = _introduction_requests(parent, nodes)
    retained_requests = introduction.get("requests")
    queries = introduction.get("queries", {})
    if not isinstance(queries, Mapping):
        raise ValueError("required dependency introduction queries must be a mapping")
    results = queries.get("results", [])
    if (
        retained_requests != requests
        or not isinstance(retained_requests, list)
        or any(
            not isinstance(request, Mapping)
            or any(
                type(request.get(key)) is not int
                for key in ("max_depth", "max_results", "max_visits")
            )
            for request in retained_requests
        )
        or introduction.get("edges")
        != [_required_introduction_edge(after, parent, child) for child in nodes]
        or queries.get("observation_identity") != after.get("observation_identity")
        or queries.get("schema") != "hashmarks.dependency-query-batch.v1"
        or queries.get("authority") != "qualified-external-observation"
        or not isinstance(results, list)
        or len(results) != len(nodes)
    ):
        raise ValueError("required dependency introduction queries differ from admitted inputs")
    for child, result in zip(nodes, results, strict=True):
        if not isinstance(result, Mapping) or not isinstance(result.get("bounds"), Mapping):
            raise ValueError("required dependency introduction path result is unavailable")
        bounds = result.get("bounds", {})
        visited = bounds.get("visited")
        if (
            result.get("schema") != "hashmarks.dependency-query-result.v1"
            or result.get("operation") != "paths"
            or result.get("context") != "lock"
            or result.get("result") != [[parent, child]]
            or result.get("completeness") != "complete"
            or result.get("omissions") != []
            or result.get("authority") != "qualified-external-observation"
            or any(
                type(bounds.get(key)) is not int or bounds.get(key) != requests[0][key]
                for key in ("max_depth", "max_results", "max_visits")
            )
            or type(visited) is not int
            or not 0 < visited <= 64
        ):
            raise ValueError("required dependency introduction path is incomplete or unsupported")
    return dict.fromkeys(nodes, parent)


def _require_current_dependency_assessment(
    native: CodeMap, observation: Mapping[str, Any]
) -> dict[str, Any]:
    # The supported query boundary checks repository/generation correspondence
    # independently of the exact-input assessment. An empty batch needs no graph
    # traversal and does not promote Git-free global freshness from unknown.
    native.dependencies.queries(observation, ())
    assessment = cast(dict[str, Any], native.dependencies.assessment(observation))
    source = assessment["source_scope"]
    if (
        source["availability"] != "available"
        or source["freshness"] != "current"
        or source["source_equivalence"] != "proven"
    ):
        raise ValueError("current dependency lock binding is unavailable, stale, or foreign")
    return assessment


def require_current_dependency_environment(evidence: Mapping[str, Any]) -> None:
    """Recheck retained external inputs without rebuilding repository evidence."""
    if evidence["environment"] != _dependency_environment():
        raise ValueError("dependency observation environment changed")
    captures = _capture_distributions(evidence["metadata"])
    if [capture.provenance for capture in captures] != evidence["metadata"]:
        raise ValueError("installed dependency metadata changed after observation")


def revalidate_changed_dependency_consumers(
    native: CodeMap, evidence: Mapping[str, Any], *, root: Path
) -> dict[str, Any]:
    """Recheck the same lock and external metadata after verifier composition."""
    lock = _current_lock_bytes(native, root, evidence["lock_path"])
    if _captured_member_revision(lock) != evidence["after_member_revision"]:
        raise ValueError("dependency lock changed after observation")
    require_current_dependency_environment(evidence)
    for packet in evidence["consumer_packets"]:
        for member in packet["source_scope"]["members"]:
            current = native.source.observe(member["path"], max_bytes=1_048_576)
            if current["member"] != member:
                raise ValueError("dependency consumer source changed after observation")
    return _require_current_dependency_assessment(native, evidence["after_observation"])


def observe_changed_dependency_consumers(
    native: CodeMap,
    *,
    root: Path,
    lock_path: str,
    before_lock: bytes,
    base_revision: str,
    source_paths: tuple[str, ...],
) -> dict[str, Any]:
    """Compose released native evidence within the caller's accepted read attempt.

    The base lock is explicitly historical Git input. Observing its claim in the
    current workspace retains the mismatch instead of relabeling it current.
    Installed metadata supplies positive namespace evidence, never consumer
    closure, runtime loadability, or execution admission.
    """
    from hashmarks.adapters.uv_dependency import uv_lock_dependency_observation
    from hashmarks.paths import normalize_relative_path

    if (
        not base_revision
        or not isinstance(before_lock, bytes)
        or len(before_lock) > _MAX_LOCK_BYTES
    ):
        raise ValueError("dependency base requires a revision and bounded historical lock bytes")
    if (
        not source_paths
        or len(set(source_paths)) != len(source_paths)
        or any(normalize_relative_path(path, allow_root=False) != path for path in source_paths)
    ):
        raise ValueError("dependency consumers require unique canonical explicit source paths")
    environment = _dependency_environment()
    lock = _current_lock_bytes(native, root, lock_path)
    before_revision = _captured_member_revision(before_lock)
    after_revision = _captured_member_revision(lock)
    before = cast(
        dict[str, Any],
        native.dependencies.observe(
            uv_lock_dependency_observation(
                lock=before_lock,
                repository_inputs=[{"path": lock_path, "member_revision": before_revision}],
            )
        ),
    )
    after = cast(
        dict[str, Any],
        native.dependencies.observe(
            uv_lock_dependency_observation(
                lock=lock,
                repository_inputs=[{"path": lock_path, "member_revision": after_revision}],
            )
        ),
    )
    _require_current_dependency_assessment(native, after)
    selections = _changed_current_selections(after, native.dependencies.delta(before, after))
    captures = _capture_distributions(list(selections))
    after = cast(
        dict[str, Any],
        native.dependencies.observe(
            uv_lock_dependency_observation(
                lock=lock,
                repository_inputs=[{"path": lock_path, "member_revision": after_revision}],
                distribution_metadata=[capture.adapter_input() for capture in captures],
            )
        ),
    )
    changed_nodes = [selection["node_id"] for selection in selections]
    packets = []
    for offset in range(0, len(source_paths), _CONSUMER_CHUNK_SIZE):
        packet = native.dependencies.consumers(
            after,
            source_paths=source_paths[offset : offset + _CONSUMER_CHUNK_SIZE],
            node_ids=changed_nodes,
            limit=1000,
        )
        scope = cast(Mapping[str, Any], packet["source_scope"])
        if (
            packet["truncation"] != "complete"
            or scope["completeness"] != "complete"
            or scope["unavailable"]
        ):
            raise ValueError("dependency consumers are truncated or source members are unavailable")
        packets.append(packet)
    result: dict[str, Any] = {
        "schema": "oh-goon.changed-dependency-consumers/v1",
        "lock_path": lock_path,
        "base_revision": base_revision,
        "before_member_revision": before_revision,
        "after_member_revision": after_revision,
        "before_observation": before,
        "before_assessment": native.dependencies.assessment(before),
        "after_observation": after,
        "after_assessment": _require_current_dependency_assessment(native, after),
        "delta": native.dependencies.delta(before, after),
        "changed_node_ids": changed_nodes,
        "metadata": [capture.provenance for capture in captures],
        "environment": environment,
        "consumer_packets": packets,
        "consumer_scope": {
            "paths": list(source_paths),
            "completeness": "incomplete",
            "negative_evidence": "not-admissible",
            "reasons": ["explicit-source-scope", "native-consumer-scope-incomplete"],
        },
        "truncation": "complete",
    }
    delta = cast(dict[str, Any], result["delta"])
    if delta["components_added"]:
        parent = delta["component_selection_transitions"][0]["added"][0]["node_id"]
        introduced = set(delta["components_added"])
        nodes = sorted(
            selection["node_id"]
            for selection in selections
            if selection["component_id"] in introduced
        )
        requests = _introduction_requests(parent, nodes)
        result["dependency_introductions"] = {
            "parent_node_id": parent,
            "node_ids": nodes,
            "requests": requests,
            "queries": native.dependencies.queries(after, requests),
            "edges": [_required_introduction_edge(after, parent, child) for child in nodes],
        }
        validate_required_dependency_introductions(result)
    result["after_assessment"] = revalidate_changed_dependency_consumers(native, result, root=root)
    return result
