"""Producer-owned uv evidence acquisition, qualification and source revalidation."""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from oh_goon.core.dependency_identity import normalize_package_name
from oh_goon.core.models.fleet_observation import (
    DependencyEvidenceAssessment,
    DependencyObservation,
    DependencyObservationSnapshot,
    ObservationSourceEvidence,
)
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime

_MAX_LOCK_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class LockedInventoryAssessment:
    evidence: DependencyEvidenceAssessment
    packages: frozenset[str] | None = None


def _assessment(observation: Mapping[str, Any]) -> DependencyEvidenceAssessment:
    inputs = observation["repository_inputs"]
    equivalence = {item["source_equivalence"] for item in inputs}
    freshness: Literal["current", "stale", "unknown"] = (
        "stale"
        if "mismatch" in equivalence
        else "current"
        if inputs and equivalence == {"proven"}
        else "unknown"
    )
    coverage = [row for row in observation["coverage"] if row["kind"] == "resolved-inventory"]
    complete = bool(coverage) and all(
        row["completeness"] == "complete" and row["truncation"] == "complete" for row in coverage
    )
    completeness: Literal["complete", "incomplete", "unknown"] = (
        "complete"
        if complete
        else "incomplete"
        if any(
            row["completeness"] == "incomplete" or row["truncation"] == "truncated"
            for row in coverage
        )
        else "unknown"
    )
    negative = [
        row for row in observation["negative_evidence"] if row["kind"] == "resolved-inventory"
    ]
    available = all(item["repository_state"] == "known-present" for item in inputs)
    return DependencyEvidenceAssessment(
        freshness=freshness,
        completeness=completeness,
        availability="available" if available else "unavailable",
        absence_admissible=available
        and freshness == "current"
        and complete
        and bool(negative)
        and all(row["state"] == "admissible-within-declared-scope" for row in negative),
        reason=None if freshness == "current" else "lock-source-" + freshness,
    )


def observe_locked_dependencies(repository: Path) -> DependencyObservationSnapshot:
    from hashmarks import CodeMap, hash_file
    from hashmarks.adapters.uv_dependency import uv_lock_dependency_observation

    try:
        with tempfile.TemporaryDirectory(prefix="oh-goon-dependency-evidence-") as directory:
            state = Path(directory)
            with CodeMap(
                repository, state_dir=state, artifact_db=state / "artifacts.sqlite3"
            ) as native:
                admitted = native.files.visible_file("uv.lock")
                if admitted is None or admitted.visibility.value != "source":
                    return DependencyObservationSnapshot(
                        evidence=DependencyEvidenceAssessment(
                            availability="unavailable",
                            reason="uv.lock is not an admitted readable source file",
                        )
                    )
                # Bounded byte transport follows native admission, so denied,
                # symlink and special-file inputs are rejected before opening.
                with (repository / "uv.lock").open("rb") as stream:
                    lock = stream.read(_MAX_LOCK_BYTES + 1)
                if len(lock) > _MAX_LOCK_BYTES:
                    raise ValueError("uv.lock exceeds the dependency evidence byte budget")
                captured = state / "captured.lock"
                captured.write_bytes(lock)
                revision = hash_file(captured).hash
                adapter = uv_lock_dependency_observation(
                    lock=lock, repository_inputs=[{"path": "uv.lock", "member_revision": revision}]
                )
                captured.unlink()
                observation = cast(dict[str, Any], native.dependencies.observe(adapter))
        assessment = _assessment(observation)
        selections = {row["node_id"]: row for row in observation["selections"]}
        components = {row["component_id"]: row for row in observation["components"]}
        members: dict[str, set[str]] = {}
        for row in observation["inventory"]:
            members.setdefault(row["node_id"], set()).add(row["context"])
        relationships: dict[str, list[dict[str, Any]]] = {}
        for edge in observation["relationships"]:
            for endpoint in {edge["source"], edge["target"]}:
                relationships.setdefault(endpoint, []).append(edge)
        dependencies = tuple(
            DependencyObservation(
                package_name=components[selections[node_id]["component_id"]]["name"],
                node_id=node_id,
                resolved_version=selections[node_id]["version"],
                package_source=selections[node_id]["source"],
                contexts=tuple(sorted(contexts)),
                relationships=tuple(relationships.get(node_id, ())),
            )
            for node_id, contexts in sorted(members.items())
        )
        return DependencyObservationSnapshot(
            sources=(ObservationSourceEvidence(path="uv.lock", member_revision=revision),),
            evidence=assessment,
            producer_implementation_identity=require_loaded_hashmarks_runtime(),
            adapter_observation=adapter,
            producer_observation=observation,
            dependencies=dependencies if assessment.availability == "available" else (),
        )
    except (OSError, RuntimeError) as exc:
        return DependencyObservationSnapshot(
            evidence=DependencyEvidenceAssessment(availability="unavailable", reason=str(exc))
        )
    except ValueError as exc:
        return DependencyObservationSnapshot(
            evidence=DependencyEvidenceAssessment(availability="unsupported", reason=str(exc))
        )


def assess_locked_dependencies(
    repository: Path, snapshot: DependencyObservationSnapshot
) -> LockedInventoryAssessment:
    """Ask the producer to bind retained evidence to the current lock source."""
    from hashmarks import CodeMap

    if snapshot.evidence.availability != "available":
        return LockedInventoryAssessment(
            snapshot.evidence.model_copy(
                update={"freshness": "unknown", "absence_admissible": False}
            )
        )
    try:
        if snapshot.producer_implementation_identity != require_loaded_hashmarks_runtime():
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
            with CodeMap(
                repository, state_dir=state, artifact_db=state / "artifacts.sqlite3"
            ) as native:
                native.dependencies.derivation_authority(snapshot.producer_observation)
                current = cast(
                    dict[str, Any], native.dependencies.observe(snapshot.adapter_observation)
                )
                if (
                    current["resolution_identity"]
                    != snapshot.producer_observation["resolution_identity"]
                ):
                    return LockedInventoryAssessment(
                        DependencyEvidenceAssessment(reason="retained-adapter-evidence-mismatch")
                    )
                selections = {row["node_id"]: row for row in current["selections"]}
                components = {row["component_id"]: row for row in current["components"]}
                packages = frozenset(
                    normalize_package_name(
                        components[selections[row["node_id"]]["component_id"]]["name"]
                    )
                    for row in current["inventory"]
                )
                return LockedInventoryAssessment(_assessment(current), packages)
    except (OSError, RuntimeError, ValueError) as exc:
        return LockedInventoryAssessment(DependencyEvidenceAssessment(reason=str(exc)))
