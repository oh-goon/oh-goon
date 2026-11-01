"""Bounded Hashmarks upgrade admission over native dependency and source evidence.

Hashmarks owns lock interpretation, source imports and verifier relationships.
This policy accepts only the established integration boundary and retains the
producer's explicit unknown dynamic consumers. Native uv still owns lock
freshness, artifact installation and package construction during execution.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

HASHMARKS_DEPENDENCY_OBLIGATION = "bounded Hashmarks dependency-consumer policy"
HASHMARKS_DEPENDENCY_PATHS = ("backend/pyproject.toml", "backend/uv.lock")
_SOURCE_PREFIX = "backend/src/oh_goon/integrations/hashmarks/"
_RELEASE_REFERENCE = re.compile(
    r"hashmarks @ (?P<url>https://github\.com/Hugloss/Hashmarks/releases/download/"
    r"v(?P<version>[0-9]+\.[0-9]+\.[0-9]+)/hashmarks-(?P=version)-py3-none-any\.whl)"
)


@dataclass(frozen=True)
class HashmarksUpgradeIntent:
    before_version: str
    before_url: str
    after_version: str
    after_url: str


def dependency_intent_changed(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    project_fields = ("dependencies", "optional-dependencies", "requires-python")
    return (
        any(
            before.get("project", {}).get(key) != after.get("project", {}).get(key)
            for key in project_fields
        )
        or any(before.get(key) != after.get(key) for key in ("dependency-groups", "build-system"))
        or before.get("tool", {}).get("uv") != after.get("tool", {}).get("uv")
    )


def hashmarks_upgrade_intent(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> HashmarksUpgradeIntent | None:
    """Admit one canonical release reference change, without resolving dependencies."""
    normalized = []
    references = []
    for manifest in (before, after):
        project = manifest.get("project", {})
        dependencies = project.get("dependencies", [])
        matches = [
            (index, match)
            for index, value in enumerate(dependencies)
            if isinstance(value, str) and (match := _RELEASE_REFERENCE.fullmatch(value))
        ]
        if len(matches) != 1:
            return None
        index, reference = matches[0]
        references.append(reference)
        unchanged = list(dependencies)
        unchanged[index] = "<Hashmarks release reference>"
        normalized.append({**manifest, "project": {**project, "dependencies": unchanged}})
    if normalized[0] != normalized[1] or references[0]["version"] == references[1]["version"]:
        return None
    return HashmarksUpgradeIntent(
        references[0]["version"],
        references[0]["url"],
        references[1]["version"],
        references[1]["url"],
    )


def _selected_release(observation: dict[str, Any], version: str, url: str) -> dict[str, Any]:
    selected = [row for row in observation["selections"] if row["component_id"] == "hashmarks"]
    if len(selected) != 1:
        raise ValueError("Hashmarks upgrade requires one native selection at each endpoint")
    row = selected[0]
    if (
        row["version"] != version
        or json.loads(row["source"]) != {"url": url}
        or row["marker"] != ""
        or row["contexts"] != ["lock"]
    ):
        raise ValueError("Native Hashmarks selection differs from the declared release")
    return cast(dict[str, Any], row)


def _require_hashmarks_only_delta(packet: dict[str, Any], intent: HashmarksUpgradeIntent) -> str:
    before, after, delta = (
        packet[key] for key in ("before_observation", "after_observation", "delta")
    )
    old = _selected_release(before, intent.before_version, intent.before_url)
    new = _selected_release(after, intent.after_version, intent.after_url)
    if delta["comparability"] != "comparable":
        raise ValueError("Dependency observations are not comparable")
    for side, observation in (("before", before), ("after", after)):
        for identity in ("resolution_identity", "observation_identity"):
            if delta[f"{side}_{identity}"] != observation[identity]:
                raise ValueError("Dependency delta is not bound to its endpoint")
    unchanged_axes = (
        "repository_identity",
        "repository_generation",
        "semantic_definition",
        "adapter_semantics",
        "producer_provenance",
        "physical_evidence_topology",
        "evidence_qualification",
        "coverage",
        "module_ownership",
    )
    if any(delta["change_axes"].get(axis) != "unchanged" for axis in unchanged_axes):
        raise ValueError("Dependency environment or evidence authority changed")
    for key in (
        "components_added",
        "components_removed",
        "components_changed",
        "selections_changed",
        "module_ownership_added",
        "module_ownership_removed",
        "module_ownership_changed",
    ):
        if delta[key]:
            raise ValueError("Dependency delta exceeds a Hashmarks release replacement")
    if any(before[key] != after[key] for key in ("roots", "scope", "contexts")):
        raise ValueError("Dependency root or resolution scope changed")
    transitions = delta["component_selection_transitions"]
    if len(transitions) != 1 or transitions[0]["component_id"] != "hashmarks":
        raise ValueError("Other dependency selections changed")
    transition = transitions[0]
    if (
        transition["changed"]
        or len(transition["removed"]) != 1
        or len(transition["added"]) != 1
        or transition["removed"][0]["node_id"] != old["node_id"]
        or transition["added"][0]["node_id"] != new["node_id"]
        or delta["selections_removed"] != [old["node_id"]]
        or delta["selections_added"] != [new["node_id"]]
        or delta["inventory_removed"] != [[old["node_id"], "lock"]]
        or delta["inventory_added"] != [[new["node_id"], "lock"]]
    ):
        raise ValueError("Dependency selection or inventory delta exceeds the intended replacement")
    normalized = []
    for key, node in (
        ("relationships_removed", old["node_id"]),
        ("relationships_added", new["node_id"]),
    ):
        edges = delta[key]
        if not edges or any(node not in edge[:2] for edge in edges):
            raise ValueError("Dependency relationship change is outside Hashmarks")
        normalized.append(
            {tuple("<Hashmarks>" if value == node else value for value in edge) for edge in edges}
        )
    if normalized[0] != normalized[1]:
        raise ValueError("Hashmarks dependency relationships changed beyond selection identity")
    return str(new["node_id"])


def _require_current_consumers(
    packet: dict[str, Any], node: str, source_paths: tuple[str, ...]
) -> set[str]:
    projection = packet["package_projection"]
    assessment = projection["assessment"]
    scope = assessment["source_scope"]
    if (
        projection["truncation"] != "complete"
        or projection["repository"]["binding_state"] != "current"
        or scope["freshness"] != "current"
        or scope["source_equivalence"] != "proven"
        or scope["availability"] != "available"
        or assessment["resolved_inventory"]["completeness"] != "complete"
        or {row["path"] for row in scope["members"]} != {"backend/uv.lock"}
        or projection["observation"]["resolution_identity"]
        != packet["after_observation"]["resolution_identity"]
    ):
        raise ValueError("Dependency projection lacks current complete lock evidence")
    packages = projection["packages"]
    if (
        len(packages) != 1
        or packages[0]["match_count"] != 1
        or packages[0]["truncation"] != "complete"
        or packages[0]["matches"][0]["selection"]["node_id"] != node
    ):
        raise ValueError("Dependency projection selected another package")
    discovery = packet["source_discovery"]
    paths = discovery["paths"]
    source = projection["source_scope"]
    if (
        discovery["prefix"] + "/" != _SOURCE_PREFIX
        or set(paths) != set(source_paths)
        or not 1 <= len(paths) <= 64
        or len(set(paths)) != len(paths)
        or any(not path.startswith(_SOURCE_PREFIX) or not path.endswith(".py") for path in paths)
        or any(row["visibility"] != "source" for row in discovery["members"])
        or source["freshness"] != "current"
        or source["completeness"] != "complete"
        or {row["path"] for row in source["retained_members"]} != set(paths)
        or any(row["state"] != "matched" for row in source["correspondence"])
    ):
        raise ValueError("Hashmarks integration source scope is incomplete or stale")
    consumer_evidence = projection["consumer_evidence"]
    if (
        consumer_evidence["state"] != "observed"
        or consumer_evidence["omitted_node_count"] != 0
        or consumer_evidence["node_ids"] != [node]
    ):
        raise ValueError("Native dependency consumer evidence is unavailable")
    consumer = consumer_evidence["observation"]
    if (
        consumer["truncation"] != "complete"
        or consumer["source_scope"]["completeness"] != "complete"
        or set(consumer["source_scope"]["paths"]) != set(paths)
        or consumer["source_scope"]["unavailable"]
        or consumer["consumer_scope"]["negative_evidence"] != "not-admissible"
    ):
        raise ValueError("Native static import scope is incomplete or overclaims absence")
    consumers = consumer["consumers"]
    if not consumers or any(
        row["source_path"] not in paths
        or row["distribution_state"] != "resolved-unique"
        or row["distribution_nodes"] != [node]
        for row in consumers
    ):
        raise ValueError("Hashmarks consumers lack qualified selected-package ownership")
    return {row["source_path"] for row in consumers}


def admit_hashmarks_upgrade(
    packet: dict[str, Any],
    intent: HashmarksUpgradeIntent,
    *,
    producer_implementation_identity: str,
    source_paths: tuple[str, ...],
    native_remainder: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compose native package → source → verifier evidence for changed dependency inputs.

    This is a named OH-Goon sufficiency decision, not a fabricated file-read edge
    or a claim that dynamic/global consumer coverage became complete.
    """
    if (
        native_remainder.get("schema") != "oh-goon.native-uv-artifact-comparison/v1"
        or native_remainder.get("equivalent") is not True
        or not native_remainder.get("before_export")
        or native_remainder.get("before_export") != native_remainder.get("after_export")
        or native_remainder.get("excluded_packages") != ["hashmarks"]
        or native_remainder.get("scope")
        != {
            "format": "pylock.toml",
            "groups": "all",
            "extras": "all",
            "workspace": "not-emitted",
            "exclusion": "native-no-emit-package",
        }
    ):
        raise ValueError("Native uv cannot prove unchanged non-Hashmarks dependency artifacts")
    if (
        packet["lock_path"] != "backend/uv.lock"
        or packet["producer_implementation_identity"] != producer_implementation_identity
        or packet["installed_distribution"]["version"] != intent.after_version
        or packet["owner_coverage"] != "not-claimed"
    ):
        raise ValueError("Hashmarks dependency evidence is not bound to the loaded release")
    node = _require_hashmarks_only_delta(packet, intent)
    consumers = _require_current_consumers(packet, node, source_paths)
    verification = packet["consumer_verification"]
    if (
        len(verification) != len(consumers)
        or {row["changed_path"] for row in verification} != consumers
    ):
        raise ValueError("A native dependency consumer lacks its own verification evidence")
    nominations: list[dict[str, Any]] = []
    for row in verification:
        source = row["changed_path"]
        if row["state"] != "LINKED" or row["stale"] is True or source not in row["edit_candidates"]:
            raise ValueError(f"Unqualified Hashmarks consumer verifier: {source}")
        available = [
            projected
            for projected in row["nominations"]
            if projected["nomination"].get("available") is True
            and projected["nomination"].get("runner_evidence") == "pytest"
            and projected["relationship"] in {"direct-reference", "bounded-indirect-reference"}
        ]
        if not available:
            raise ValueError(f"No executable native Hashmarks consumer verifier: {source}")
        # Native ordering nominates the closest owner first. Admit one positive
        # direct proof per consumer, falling back to its first bounded indirect
        # proof. The complete advisory search remains in the retained packet.
        direct = [row for row in available if row["relationship"] == "direct-reference"]
        selected = (direct or available)[:1]
        nominations.extend(
            {
                **projected,
                "relationship": "dependency-consumer-verifier",
                "consumer_source_path": source,
                "consumer_relationship": projected["relationship"],
            }
            for projected in selected
        )
    return [
        {
            "changed_path": path,
            "state": "LINKED",
            "edit_candidates": [path],
            "stale": False,
            "qualification": {
                "authority": "oh-goon-bounded-dependency-policy",
                "basis": "native-lock-package-source-verifier-chain",
                "consumer_scope": "static Hashmarks integration imports",
                "global_completeness": "not-claimed",
                "negative_evidence_admissible": False,
            },
            "nominations": nominations,
        }
        for path in HASHMARKS_DEPENDENCY_PATHS
    ]
