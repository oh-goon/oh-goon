"""Source-qualified verifier nominations for the existing development check owner."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from oh_goon.integrations.hashmarks.dependencies import (
    observe_changed_dependency_consumers,
    revalidate_changed_dependency_consumers,
    validate_required_dependency_introductions,
)
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime

if TYPE_CHECKING:
    from hashmarks import CodeMap


def _changed_verification(
    native: CodeMap, relative: str, *, retain_relationships: bool = False
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from hashmarks.operation_contract import validate_operation_response
    from hashmarks.verification_nomination_validation import validate_verification_nomination

    # This is the producer's operation-native bound, not a locally ranked pair.
    # One per-path packet avoids a global target cap dropping another changed
    # path while retaining every native scope/omission qualification.
    packet = cast(dict[str, Any], native.verification.changed_set((relative,), limit=2))
    validate_operation_response("changed_set_verification", packet)
    changes = packet["changes"]
    if packet["changed_paths"] != [relative] or len(changes) != 1 or changes[0]["path"] != relative:
        raise ValueError("Hashmarks changed-set scope differs from the supplied member")
    change = changes[0]
    nominations: list[dict[str, Any]] = []
    for target in packet["targets"]:
        nomination = target["nomination"]
        if validate_verification_nomination(nomination).get("valid") is not True:
            raise ValueError("Hashmarks rejected its changed-set verifier nomination")
        relationships = [row for row in change["relationships"] if row["target"] == target["path"]]
        if not relationships:
            raise ValueError("Hashmarks target has no changed-source relationship")
        projected: dict[str, Any] = {"nomination": nomination, "workspace_prefix": ""}
        # The intact packet already owns all relationships and contexts. Only
        # the support/dependency admission owners consume a relationship copy.
        if retain_relationships or change["classification"]["kind"] == "verification-support":
            projected["relationships"] = relationships
        nominations.append(projected)
    return packet, nominations


def _dependency_verification(
    native: CodeMap, root: Path, base: Mapping[str, Any], source_paths: tuple[str, ...]
) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence = observe_changed_dependency_consumers(
        native,
        root=root,
        lock_path=base["lock_path"],
        before_lock=base["lock"],
        base_revision=base["base_revision"],
        source_paths=source_paths,
    )
    evidence["source_selection"] = {
        "owner": "hashmarks.files.iter_files",
        "prefix": "backend/src",
        "suffix": ".py",
        "scope": "explicit-backend-runtime-python-sources",
        "consumer_completeness": "not-claimed",
    }
    introduced = validate_required_dependency_introductions(evidence)
    changes: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    chains: list[dict[str, Any]] = []
    nominations: dict[str, dict[str, Any]] = {}
    chain_keys: set[tuple[str, str, str]] = set()
    for packet in evidence["consumer_packets"]:
        for consumer in packet["consumers"]:
            if consumer.get("distribution_state") != "resolved-unique":
                continue
            source = consumer["source_path"]
            if source not in changes:
                changes[source] = _changed_verification(native, source, retain_relationships=True)
            changed, projected_nominations = changes[source]
            for node_id in consumer["distribution_nodes"]:
                if node_id not in evidence["changed_node_ids"]:
                    continue
                supported_nodes = (
                    node_id,
                    *(child for child, parent in introduced.items() if parent == node_id),
                )
                for projected in projected_nominations:
                    if projected["nomination"].get("available") is not True:
                        continue
                    key = json.dumps(projected, sort_keys=True)
                    nominations[key] = projected
                    for supported in supported_nodes:
                        chain_key = supported, source, key
                        if chain_key in chain_keys:
                            continue
                        chain_keys.add(chain_key)
                        chain = {
                            "node_id": supported,
                            "source_path": source,
                            "consumer": consumer,
                            "changed_verification": changed,
                            "nomination": projected,
                        }
                        if supported != node_id:
                            # A required dependency path connects the child to
                            # this imported parent. It is not a child import.
                            chain["consumer_node_id"] = node_id
                        chains.append(chain)
    evidence["chains"] = chains
    observation = {
        "changed_path": base["lock_path"],
        "state": "LINKED" if chains else "UNAVAILABLE",
        "dependency_chain": chains,
        "stale": any(packet.get("freshness") == "stale" for packet, _ in changes.values()),
        "qualification": {"consumer_scope": evidence["consumer_scope"]},
        "nominations": list(nominations.values()),
    }
    return evidence, observation


def nominate_changed_verification(
    root: Path,
    paths: tuple[str, ...],
    *,
    required: tuple[str, ...] = (),
    dependency_base: Mapping[str, Any] | None = None,
    asset_paths: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Transport native changed-set packets without reconstructing ownership.

    Each member uses the producer's two-target operation bound within one native
    root/lifetime. Workspace context and remaining scope uncertainty stay with
    the producer; the consumer retains explicit execution-policy obligations.
    """
    from hashmarks import CodeMap
    from hashmarks.operation_contract import validate_operation_response
    from hashmarks.verification_nomination_validation import validate_verification_nomination

    implementation = require_loaded_hashmarks_runtime()
    if not set(asset_paths) <= set(paths):
        raise ValueError("Asset observation must use the admitted repository change scope")
    with CodeMap(root) as native:
        # Reconcile discovery once, including newly added verifiers. The native
        # operation rechecks contributors but does not invent a fresh inventory.
        native.index.sync()
        dependency_sources: tuple[str, ...] = ()
        if dependency_base and dependency_base["lock_path"] in paths:
            sources: list[str] = []
            for item in native.files.iter_files(prefix="backend/src"):
                if item.rel.endswith(".py") and item.visibility.value != "deny":
                    sources.append(item.rel)
                    if len(sources) > 4096:
                        raise ValueError("Dependency source inventory exceeds its transport bound")
            dependency_sources = tuple(sorted(sources))
        for member in dict.fromkeys((*paths, *required)):
            if dependency_base and member == dependency_base["lock_path"]:
                continue
            observed, _ = native.source.member(member)
            if observed.get("state") == "known-present":
                native.tasks.prepare(f"Verify {member}", 20)

        def nominate_root(_scope: object) -> dict[str, Any]:
            # A native reconciliation retries this complete composition; partial
            # observations from a previous generation are never accumulated.
            observations: list[dict[str, Any]] = []
            checks: list[dict[str, Any]] = []
            dependencies: dict[str, Any] | None = None
            assets = native.verification.changed_set(asset_paths) if asset_paths else None
            if assets is not None:
                validate_operation_response("changed_set_verification", assets)
            for member in required:
                nomination = native.verification.nomination(member)
                if validate_verification_nomination(nomination).get("valid") is not True:
                    raise ValueError("Hashmarks rejected a required policy proof")
                checks.append({"nomination": nomination, "workspace_prefix": ""})
            for member in paths:
                if dependency_base and member == dependency_base["lock_path"]:
                    try:
                        dependencies, observation = _dependency_verification(
                            native, root, dependency_base, dependency_sources
                        )
                    except (OSError, ValueError) as exc:
                        dependencies = {"unavailable": str(exc)}
                        observation = {
                            "changed_path": member,
                            "state": "UNAVAILABLE",
                            "reason": "dependency-evidence-unavailable",
                        }
                else:
                    changed, nominations = _changed_verification(native, member)
                    change = changed["changes"][0]
                    observation = {
                        "changed_path": member,
                        "state": "CHANGED_TEST"
                        if change["classification"]["kind"] == "verifier"
                        and any(
                            row["nomination"].get("available") is True
                            and row["nomination"].get("verification_target", {}).get("path")
                            == member
                            and row["nomination"].get("runner_evidence") in {"pytest", "vitest"}
                            for row in nominations
                        )
                        else "LINKED"
                        if any(row["nomination"]["available"] for row in nominations)
                        else "UNAVAILABLE",
                        "stale": changed["freshness"] == "stale",
                        "qualification": change["consumer_scope"],
                        "changed_set": changed,
                        "nominations": nominations,
                    }
                observations.append(observation)
            if dependencies and "after_observation" in dependencies:
                dependencies["after_assessment"] = revalidate_changed_dependency_consumers(
                    native, dependencies, root=root
                )
            packet: dict[str, Any] = {
                "schema": "oh-goon.hashmarks-development-nominations/v1",
                "producer_implementation_identity": implementation,
                "observations": observations,
                "required_checks": checks,
                "owner_coverage": "not-claimed",
                "asset_evidence": assets,
            }
            if dependencies is not None:
                packet["dependency_changes"] = dependencies
            return packet

        packet = native.reads.observe(nominate_root)
    if require_loaded_hashmarks_runtime() != implementation:
        raise ValueError("Hashmarks implementation changed during nomination")
    if len(json.dumps(packet).encode("utf-8")) > 4 * 1024 * 1024:
        raise ValueError("Hashmarks development evidence exceeds the bounded transport")
    return packet


def observe_repository_assets(
    root: Path, paths: tuple[str, ...], *, limit: int = 100
) -> dict[str, Any]:
    """Reobserve candidate bytes with the caller's installed, trusted producer.

    This public-producer boundary returns evidence only. It does not run candidate
    code, acquire LFS objects or decide whether a documentation exemption applies.
    """
    from hashmarks import CodeMap
    from hashmarks.operation_contract import validate_operation_response

    require_loaded_hashmarks_runtime()
    with CodeMap(root) as native:
        native.index.sync()
        packet = native.verification.changed_set(paths, limit=limit)
        validate_operation_response("changed_set_verification", packet)
        return cast(dict[str, Any], packet)
