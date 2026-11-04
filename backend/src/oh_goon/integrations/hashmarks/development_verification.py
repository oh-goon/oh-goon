"""Source-qualified verifier nominations for the existing development check owner."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from oh_goon.integrations.hashmarks.dependencies import (
    observe_changed_dependency_consumers,
    revalidate_changed_dependency_consumers,
)
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime

if TYPE_CHECKING:
    from hashmarks import CodeMap


def _linked_verification(
    native: CodeMap, relative: str, prefix: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from hashmarks.operation_contract import validate_operation_response
    from hashmarks.verification_nomination_validation import validate_verification_nomination

    ownership = cast(
        dict[str, Any],
        native.tasks.verification_ownership_graph(
            # The producer retains its canonical selection and first ranked
            # reference within this pair. Keep its source authority and residual
            # bounds; complete unrelated verification remains daily.
            f"Verify {relative}",
            limit=20,
            candidate_limit=2,
        ),
    )
    validate_operation_response("verification_ownership", ownership)
    nominations: list[dict[str, Any]] = []
    for row in ownership.get("verification_owners", []):
        if relative not in row.get("covers_edit_candidates", []) or row.get(
            "coverage_evidence"
        ) not in {"direct-reference", "bounded-indirect-reference"}:
            continue
        nomination = native.verification.nomination(row["path"])
        if validate_verification_nomination(nomination).get("valid") is not True:
            raise ValueError("Hashmarks rejected its linked verifier nomination")
        nominations.append(
            {
                "nomination": nomination,
                "relationship": row["coverage_evidence"],
                "workspace_prefix": prefix,
            }
        )
    return ownership, nominations


def _dependency_verification(
    native: CodeMap, root: Path, base: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    # This is an explicit runtime source bound, not a second import resolver or
    # an assertion that all possible distribution consumers have been found.
    sources: list[str] = []
    for item in native.files.iter_files(prefix="backend/src"):
        if item.rel.endswith(".py") and item.visibility.value != "deny":
            sources.append(item.rel)
            if len(sources) > 4096:
                raise ValueError("Dependency source inventory exceeds its transport bound")
    evidence = observe_changed_dependency_consumers(
        native,
        root=root,
        lock_path=base["lock_path"],
        before_lock=base["lock"],
        base_revision=base["base_revision"],
        source_paths=tuple(sorted(sources)),
    )
    evidence["source_selection"] = {
        "owner": "hashmarks.files.iter_files",
        "prefix": "backend/src",
        "suffix": ".py",
        "scope": "explicit-backend-runtime-python-sources",
        "consumer_completeness": "not-claimed",
    }
    graphs: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    chains: list[dict[str, Any]] = []
    nominations: dict[str, dict[str, Any]] = {}
    chain_keys: set[tuple[str, str, str]] = set()
    for packet in evidence["consumer_packets"]:
        for consumer in packet["consumers"]:
            if consumer.get("distribution_state") != "resolved-unique":
                continue
            source = consumer["source_path"]
            if source not in graphs:
                graphs[source] = _linked_verification(native, source, "")
            ownership, projected_nominations = graphs[source]
            for node_id in consumer["distribution_nodes"]:
                if node_id not in evidence["changed_node_ids"]:
                    continue
                for projected in projected_nominations:
                    if projected["nomination"].get("available") is not True:
                        continue
                    key = json.dumps(projected, sort_keys=True)
                    chain_key = node_id, source, key
                    if chain_key in chain_keys:
                        continue
                    chain_keys.add(chain_key)
                    nominations[key] = projected
                    chains.append(
                        {
                            "node_id": node_id,
                            "source_path": source,
                            "consumer": consumer,
                            "ownership": ownership,
                            "nomination": projected,
                        }
                    )
    evidence["chains"] = chains
    observation = {
        "changed_path": base["lock_path"],
        "state": "LINKED" if chains else "UNAVAILABLE",
        "dependency_chain": chains,
        # Preserve the graph's actual source owners. The lock is connected by
        # its native dependency delta and metadata, never an invented graph edge.
        "edit_candidates": sorted(
            {
                path
                for ownership, _nominations in graphs.values()
                for path in ownership.get("edit_candidates", [])
            }
        ),
        "stale": any(ownership.get("stale") is True for ownership, _ in graphs.values()),
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
    """Keep producer relations and qualifications; never turn them into owner coverage.

    One native map/read session serves the complete supplied change set. The
    consumer applies cadence later; Hashmarks retains repository relevance.
    """
    from hashmarks import CodeMap
    from hashmarks.operation_contract import validate_operation_response
    from hashmarks.verification_nomination_validation import validate_verification_nomination

    implementation = require_loaded_hashmarks_runtime()
    if not set(asset_paths) <= set(paths) or any(
        path.startswith("frontend/") for path in asset_paths
    ):
        raise ValueError("Asset observation must use the admitted repository change scope")
    observations: list[dict[str, Any]] = []
    required_checks: list[dict[str, Any]] = []
    dependency_changes: dict[str, Any] | None = None
    asset_evidence: dict[str, Any] | None = None
    # The frontend's native package/tsconfig authority is its project root.
    # All other paths retain the repository map, including cross-owner Python refs.
    for prefix in ("", "frontend/"):
        selected_paths = tuple(
            path for path in paths if path.startswith("frontend/") == bool(prefix)
        )
        if not selected_paths and (prefix or not required):
            continue
        workspace = root / prefix
        # Hashmarks owns this derived index and revalidates it natively. CI does
        # not transport/cache this mutable database as verification authority.
        with CodeMap(workspace) as native:
            if not prefix and asset_paths:
                # Reconcile discovery once, including newly added consumers.
                # Member rereads alone cannot discover a new indexed source.
                native.index.sync()

            def nominate_workspace(
                _scope: object,
                *,
                prefix: str = prefix,
                selected_paths: tuple[str, ...] = selected_paths,
            ) -> tuple[
                list[dict[str, Any]],
                list[dict[str, Any]],
                dict[str, Any] | None,
                dict[str, Any] | None,
            ]:
                # Native observe may reconcile a contributor and recompute the
                # whole operation. Retain only rows from its accepted attempt.
                observed: list[dict[str, Any]] = []
                checks: list[dict[str, Any]] = []
                dependencies: dict[str, Any] | None = None
                assets = None
                if not prefix and asset_paths:
                    assets = native.verification.changed_set(asset_paths)
                    validate_operation_response("changed_set_verification", assets)
                if not prefix:
                    for path in required:
                        nomination = native.verification.nomination(path)
                        if validate_verification_nomination(nomination).get("valid") is not True:
                            raise ValueError("Hashmarks rejected a required policy proof")
                        checks.append({"nomination": nomination, "workspace_prefix": ""})
                for path in selected_paths:
                    relative = path.removeprefix(prefix)
                    if not (root / path).is_file():
                        observed.append(
                            {
                                "changed_path": path,
                                "state": "UNAVAILABLE",
                                "reason": "source-absent",
                            }
                        )
                        continue
                    if not prefix and dependency_base and path == dependency_base["lock_path"]:
                        try:
                            dependencies, observation = _dependency_verification(
                                native, root, dependency_base
                            )
                        except (OSError, ValueError) as exc:
                            dependencies = {"unavailable": str(exc)}
                            observation = {
                                "changed_path": path,
                                "state": "UNAVAILABLE",
                                "reason": "dependency-evidence-unavailable",
                            }
                        observed.append(observation)
                        continue
                    nominated = native.verification.nomination(relative)
                    validation = validate_verification_nomination(nominated)
                    if validation.get("valid") is not True:
                        raise ValueError("Hashmarks rejected its changed-source nomination")
                    if nominated.get("available") is True:
                        observed.append(
                            {
                                "changed_path": path,
                                "state": "CHANGED_TEST",
                                "nominations": [
                                    {"nomination": nominated, "workspace_prefix": prefix}
                                ],
                            }
                        )
                        continue
                    ownership, nominations = _linked_verification(native, relative, prefix)
                    observed.append(
                        {
                            "changed_path": path,
                            "state": "LINKED" if nominations else "UNAVAILABLE",
                            "edit_candidates": [
                                prefix + value for value in ownership.get("edit_candidates", [])
                            ],
                            "stale": ownership.get("stale"),
                            "qualification": ownership.get("verification_qualification", {}),
                            "nominations": nominations,
                        }
                    )
                if dependencies and "after_observation" in dependencies:
                    dependencies["after_assessment"] = revalidate_changed_dependency_consumers(
                        native, dependencies, root=root
                    )
                return observed, checks, dependencies, assets

            observed, checks, dependencies, assets = native.reads.observe(nominate_workspace)
            observations.extend(observed)
            required_checks.extend(checks)
            if dependencies is not None:
                dependency_changes = dependencies
            if assets is not None:
                asset_evidence = assets
    packet: dict[str, Any] = {
        "schema": "oh-goon.hashmarks-development-nominations/v1",
        "producer_implementation_identity": implementation,
        "observations": observations,
        "required_checks": required_checks,
        "owner_coverage": "not-claimed",
        "asset_evidence": asset_evidence,
    }
    if dependency_changes is not None:
        packet["dependency_changes"] = dependency_changes
    if len(json.dumps(packet).encode("utf-8")) > 4 * 1024 * 1024:
        raise ValueError("Hashmarks development evidence exceeds the bounded transport")
    return packet


def observe_repository_assets(root: Path, paths: tuple[str, ...]) -> dict[str, Any]:
    """Reobserve candidate bytes with the caller's installed, trusted producer.

    This public-producer boundary returns evidence only. It does not run candidate
    code, acquire LFS objects or decide whether a documentation exemption applies.
    """
    from hashmarks import CodeMap
    from hashmarks.operation_contract import validate_operation_response

    require_loaded_hashmarks_runtime()
    with CodeMap(root) as native:
        native.index.sync()
        packet = native.verification.changed_set(paths)
        validate_operation_response("changed_set_verification", packet)
        return cast(dict[str, Any], packet)
