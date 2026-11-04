"""Admit relevant development proof, then delegate to the original check owners."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import ConfigDict, Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.engine.command import run_command
from oh_goon.integrations.exact_file_write import publish_text_file
from oh_goon.integrations.hashmarks.dependencies import require_current_dependency_environment
from oh_goon.integrations.hashmarks.development_verification import nominate_changed_verification
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime
from oh_goon.repository_tooling.frontend_test_results import vitest_outcomes
from oh_goon.repository_tooling.pytest_member_selection import collect_native_pytest
from oh_goon.repository_tooling.pytest_process_environment import resolve_pytest_invocation
from oh_goon.repository_tooling.release.package_slim import canonical_source_identity
from oh_goon.repository_tooling.verification_gate_control import (
    DOCUMENTATION_ASSET_OBLIGATION,
    DOCUMENTATION_PROOF_OBLIGATION,
    DOCUMENTATION_PROOF_TEST,
    HASHMARKS_UPGRADE_OBLIGATION,
    HASHMARKS_UPGRADE_TESTS,
    admitted_documentation_assets,
    behavioral_pytest_targets,
    documentation_asset_candidates,
    documentation_proof_paths,
    hashmarks_upgrade_intent,
    is_generated_contract_path,
    is_verification_policy_path,
    native_changed_paths,
    non_executable_documentation,
    require_native_pytest_pass,
    required_policy_obligations,
    required_policy_tests,
    required_static_obligations,
)

ROOT = Path(__file__).resolve().parents[4]
_SCHEMA: Literal["oh-goon.development-verification/v1"] = "oh-goon.development-verification/v1"
_MERGE_MARKER = "not certification and not slow and not daily"
_STATIC = Literal["python", "frontend", "architecture", "generated", "package"]


class ChangeVerificationPlan(ImmutableModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["oh-goon.development-verification/v1"] = _SCHEMA
    base: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    candidate: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    requested_head: str | None = None
    source_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    changed_paths: tuple[str, ...]
    removed_paths: tuple[str, ...] = ()
    static: tuple[_STATIC, ...]
    pytest: tuple[str, ...]
    frontend: tuple[str, ...]
    unverified: tuple[str, ...]
    evidence: dict[str, Any]
    behavioral_status: Literal["REQUIRED", "NOT_APPLICABLE"]
    policy_status: Literal["REVIEW_REQUIRED", "ORDINARY"]
    obligations: tuple[str, ...] = ()


def _git(root: Path, *arguments: str) -> str:
    result = run_command(
        "git",
        "-C",
        str(root),
        *arguments,
        timeout=20,
        max_capture_bytes_per_stream=4 * 1024 * 1024,
        require_process_group_quiescence=True,
        parent_death_signal=True,
    )
    if result.returncode != 0 or result.stdout_truncated:
        raise ValueError("Git comparison is incomplete or unavailable")
    return result.stdout


def _base_lock_bytes(root: Path, base: str) -> bytes:
    observed = run_command(
        "git",
        "-C",
        str(root),
        "show",
        f"{base}:backend/uv.lock",
        timeout=20,
        max_capture_bytes_per_stream=4 * 1024 * 1024,
        require_process_group_quiescence=True,
        parent_death_signal=True,
        retain_output_tail=True,
    )
    encoded = observed.stdout.encode("utf-8")
    raw = observed.stdout_evidence
    if (
        observed.returncode
        or observed.stdout_truncated
        or raw is None
        or raw.byte_count != len(encoded)
        or raw.sha256 != hashlib.sha256(encoded).hexdigest()
    ):
        raise ValueError("Native base lock transport does not preserve its exact source bytes")
    return encoded


def capture_change(
    root: Path, base: str, candidate: str | None
) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    base_sha = _git(root, "rev-parse", "--verify", f"{base}^{{commit}}").strip()
    head = _git(root, "rev-parse", "--verify", "HEAD^{commit}").strip()
    if (
        candidate is not None
        and head != _git(root, "rev-parse", "--verify", f"{candidate}^{{commit}}").strip()
    ):
        raise ValueError("Effective checkout differs from the requested candidate")
    arguments = (base_sha, head) if candidate is not None else (base_sha,)
    changed, removed = native_changed_paths(
        _git(root, "diff", "--name-status", "-z", "--find-renames", *arguments)
    )
    paths = set(changed)
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    dirty = _git(root, "diff", "--name-only", "HEAD").strip()
    if candidate is not None and (dirty or any(untracked)):
        raise ValueError("Hosted candidate has uncommitted or untracked source")
    if candidate is None:
        paths.update(path for path in untracked if path)
    for path in paths:
        if Path(path).is_absolute() or ".." in Path(path).parts or "\n" in path:
            raise ValueError("Git comparison contains an unsafe path")
    return base_sha, head, tuple(sorted(paths)), removed


def _backend_dependency_intent(
    root: Path, base: str, paths: tuple[str, ...]
) -> tuple[bool, bool, bool]:
    """Classify authored intent without deciding native dependency compatibility."""
    if "backend/pyproject.toml" not in paths:
        return False, False, False
    previous = tomllib.loads(_git(root, "show", f"{base}:backend/pyproject.toml"))
    with (root / "backend/pyproject.toml").open("r", encoding="utf-8") as stream:
        manifest = stream.read(4 * 1024 * 1024 + 1)
    if len(manifest) > 4 * 1024 * 1024:
        raise ValueError("Dependency declaration exceeds its transport bound")
    current = tomllib.loads(manifest)
    previous_project, current_project = previous.get("project", {}), current.get("project", {})
    runtime_changed = previous_project.get("dependencies") != current_project.get("dependencies")
    unsupported = (
        any(
            previous_project.get(key) != current_project.get(key)
            for key in ("optional-dependencies", "requires-python", "dynamic")
        )
        or any(
            previous.get(key) != current.get(key) for key in ("dependency-groups", "build-system")
        )
        or any(
            previous.get("tool", {}).get(key) != current.get("tool", {}).get(key)
            for key in ("uv", "hatch", "setuptools")
        )
    )
    return runtime_changed or unsupported, unsupported, hashmarks_upgrade_intent(previous, current)


def _dependency_chain_qualified(
    chain: Mapping[str, Any], packets: list[dict[str, Any]], nominations: list[dict[str, Any]]
) -> bool:
    source = chain.get("source_path")
    consumer = chain.get("consumer")
    ownership = chain.get("ownership")
    projected = chain.get("nomination")
    if not all(isinstance(value, Mapping) for value in (consumer, ownership, projected)):
        return False
    assert isinstance(consumer, Mapping)
    assert isinstance(ownership, Mapping)
    assert isinstance(projected, Mapping)
    nomination = projected.get("nomination", {})
    if (
        not isinstance(source, str)
        or not source.startswith("backend/src/")
        or consumer.get("source_path") != source
        or consumer.get("distribution_state") != "resolved-unique"
        or consumer.get("distribution_nodes") != [chain.get("node_id")]
        or consumer.get("import_claim_authority") != "source-observed-static-import"
        or not consumer.get("source_member")
        or ownership.get("stale") is True
        or source not in ownership.get("edit_candidates", [])
        or projected not in nominations
        or projected.get("workspace_prefix") != ""
        or not isinstance(nomination, Mapping)
        or nomination.get("available") is not True
        or nomination.get("runner_evidence") != "pytest"
        or not nomination.get("source_member_revision")
    ):
        return False
    if not any(
        consumer in packet.get("consumers", [])
        and source in packet.get("source_scope", {}).get("paths", [])
        and consumer["source_member"] in packet.get("source_scope", {}).get("members", [])
        for packet in packets
    ):
        return False
    target = nomination.get("verification_target", {}).get("path")
    return any(
        row.get("path") == target
        and source in row.get("covers_edit_candidates", [])
        and row.get("coverage_evidence") in {"direct-reference", "bounded-indirect-reference"}
        and projected.get("relationship") == row.get("coverage_evidence")
        for row in ownership.get("verification_owners", [])
        if isinstance(row, Mapping)
    )


def _bounded_dependency_scope(
    evidence: Mapping[str, Any], base: str, *, selected_files: set[str] | None = None
) -> bool:
    """Admit positive native chains without promoting their residual coverage."""
    bundle = evidence.get("dependency_changes")
    if not isinstance(bundle, Mapping):
        return False
    if (
        bundle.get("schema") != "oh-goon.changed-dependency-consumers/v1"
        or bundle.get("lock_path") != "backend/uv.lock"
        or bundle.get("base_revision") != base
        or not bundle.get("before_member_revision")
        or bundle.get("truncation") != "complete"
        or bundle.get("unaccounted_changes", [])
    ):
        return False
    current = bundle.get("after_assessment", {}).get("source_scope", {})
    if (
        current.get("availability") != "available"
        or current.get("freshness") != "current"
        or current.get("source_equivalence") != "proven"
    ):
        return False
    before, after = bundle.get("before_observation", {}), bundle.get("after_observation", {})
    delta = bundle.get("delta", {})
    if delta.get("comparability") != "comparable" or any(
        not observation.get("observation_identity")
        or delta.get(f"{endpoint}_observation_identity") != observation["observation_identity"]
        for endpoint, observation in (("before", before), ("after", after))
    ):
        return False
    nodes = bundle.get("changed_node_ids")
    metadata = bundle.get("metadata")
    environment = bundle.get("environment")
    if (
        not isinstance(nodes, list)
        or not nodes
        or any(not isinstance(node, str) or not node for node in nodes)
        or len(nodes) != len(set(nodes))
        or not isinstance(metadata, list)
        or len(metadata) != len(nodes)
        or any(not isinstance(row, Mapping) for row in metadata)
        or {row.get("node_id") for row in metadata} != set(nodes)
        or not isinstance(environment, Mapping)
        or environment.get("metadata_discovery") != "importlib.metadata.distributions"
        or environment.get("producer_implementation_identity")
        != evidence.get("producer_implementation_identity")
        or any(
            not isinstance(environment.get(key), str) or not environment[key]
            for key in ("python_executable", "python_prefix", "python_version")
        )
    ):
        return False
    if any(
        row.get("discovery") != "importlib.metadata.distributions"
        or any(
            not isinstance(row.get(key), str) or not row[key]
            for key in (
                "component_id",
                "name",
                "version",
                "distribution_root",
                "metadata_path",
                "record_path",
                "metadata_member_revision",
                "record_member_revision",
            )
        )
        for row in metadata
    ):
        return False
    transitions = delta.get("component_selection_transitions", [])
    if (
        set(delta.get("selections_added", [])) != set(nodes)
        or len(delta.get("selections_added", [])) != len(nodes)
        or delta.get("selections_changed")
        or delta.get("components_added")
        or delta.get("components_removed")
        or delta.get("components_changed")
        or not isinstance(transitions, list)
        or len(transitions) != len(nodes)
        or any(
            not isinstance(transition, Mapping)
            or len(transition.get("added", [])) != 1
            or len(transition.get("removed", [])) != 1
            or transition.get("changed")
            for transition in transitions
        )
    ):
        return False
    added = {transition["added"][0]["node_id"] for transition in transitions}
    removed = {transition["removed"][0]["node_id"] for transition in transitions}
    if added != set(nodes) or removed != set(delta.get("selections_removed", [])):
        return False
    selections = {row["node_id"]: row for row in after.get("selections", [])}
    components = {row["component_id"]: row for row in after.get("components", [])}
    for row in metadata:
        selection = selections.get(row["node_id"], {})
        component = components.get(row["component_id"], {})
        if (
            selection.get("component_id") != row["component_id"]
            or selection.get("version") != row["version"]
            or component.get("name") != row["name"]
            or component.get("ecosystem") != "pypi"
            or not any(
                transition.get("component_id") == row["component_id"]
                and transition["added"][0]["node_id"] == row["node_id"]
                for transition in transitions
            )
        ):
            return False
    scope = bundle.get("consumer_scope", {})
    selection = bundle.get("source_selection", {})
    packets = bundle.get("consumer_packets")
    if (
        scope.get("completeness") != "incomplete"
        or scope.get("negative_evidence") != "not-admissible"
        or selection.get("owner") != "hashmarks.files.iter_files"
        or selection.get("prefix") != "backend/src"
        or selection.get("suffix") != ".py"
        or selection.get("scope") != "explicit-backend-runtime-python-sources"
        or selection.get("consumer_completeness") != "not-claimed"
        or not isinstance(packets, list)
        or not packets
        or any(not isinstance(packet, dict) for packet in packets)
    ):
        return False
    observed_paths: set[str] = set()
    for packet in packets:
        source_scope = packet.get("source_scope", {})
        if (
            packet.get("truncation") != "complete"
            or packet.get("observation_identity") != after.get("observation_identity")
            or not after.get("repository_binding")
            or packet.get("repository_binding") != after["repository_binding"]
            or set(packet.get("selected_node_ids", [])) != set(nodes)
            or source_scope.get("completeness") != "complete"
            or source_scope.get("unavailable")
        ):
            return False
        observed_paths.update(source_scope.get("paths", []))
    if not observed_paths or observed_paths != set(scope.get("paths", [])):
        return False
    lock_rows = [
        row
        for row in evidence.get("observations", [])
        if row.get("changed_path") == "backend/uv.lock"
    ]
    chains = bundle.get("chains")
    if (
        len(lock_rows) != 1
        or lock_rows[0].get("state") != "LINKED"
        or lock_rows[0].get("stale") is True
        or not isinstance(chains, list)
        or not chains
        or lock_rows[0].get("dependency_chain") != chains
        or any(not isinstance(chain, Mapping) for chain in chains)
        or {chain.get("node_id") for chain in chains} != set(nodes)
    ):
        return False
    nominations = lock_rows[0].get("nominations", [])
    if not all(_dependency_chain_qualified(chain, packets, nominations) for chain in chains):
        return False
    return selected_files is None or {
        chain["node_id"]
        for chain in chains
        if chain["nomination"]["nomination"]["verification_target"]["path"] in selected_files
    } == set(nodes)


def _collect_pytest_proof(
    root: Path,
    evidence: dict[str, Any],
    pytest: set[str],
    required: tuple[str, ...],
    excluded_paths: set[str],
) -> tuple[set[str], list[str]]:
    invocation = replace(
        resolve_pytest_invocation(qualified=True, pytest_config=root / "backend/pyproject.toml"),
        cwd=root,
        source_root=root,
    )
    collection = collect_native_pytest(
        tuple(root / path for path in sorted(pytest)), marker=_MERGE_MARKER, invocation=invocation
    )
    selected_files = {row["file"] for row in collection.selected}
    if not selected_files <= {target.split("::", 1)[0] for target in pytest}:
        raise ValueError("Native pytest collection escaped the admitted test files")

    def collected_targets(rows: tuple[dict[str, str], ...]) -> set[str]:
        return {
            target
            for target in pytest
            if any(
                target == row["file"]
                or target == row["selector"]
                or row["selector"].startswith(target + "[")
                for row in rows
            )
        }

    selected = collected_targets(collection.selected)
    admission = (
        evidence["dependency_admission"]
        if "dependency_admission" in evidence
        else evidence.setdefault("pytest_admission", {})
    )
    admission["collection"] = collection.payload()
    admission["collection_context"] = invocation.correctness_context()
    deselected = collected_targets(collection.deselected)
    admission["empty_advisory_targets"] = sorted(pytest - selected - deselected)
    admission["targets_without_selected_members"] = sorted(pytest - selected)
    missing: list[str] = []
    if not set(required) <= selected:
        missing.append("VERIFICATION_POLICY_PROTECTION_UNAVAILABLE")
    for row in evidence.get("observations", []):
        if row.get("changed_path") in excluded_paths:
            continue
        if row.get("state") == "CHANGED_TEST" and row["changed_path"] not in selected:
            missing.append(f"CHANGED_TEST_HAS_NO_NATIVE_MEMBERS:{row['changed_path']}")
        if row.get("state") == "LINKED":
            candidates = {
                str(projected.get("workspace_prefix", ""))
                + projected.get("nomination", {}).get("verification_target", {}).get("path", "")
                for projected in row.get("nominations", [])
                if projected.get("nomination", {}).get("runner_evidence") == "pytest"
                and projected.get("nomination", {}).get("available") is True
            }
            if candidates and not candidates & selected:
                missing.append(f"NO_EXECUTABLE_VERIFIER:{row['changed_path']}")
    return selected, missing


def admit_change(
    root: Path,
    *,
    base: str,
    candidate: str | None = None,
    requested_head: str | None = None,
    nominator: Callable[..., dict[str, Any]] = nominate_changed_verification,
) -> ChangeVerificationPlan:
    base_sha, head, changed, removed = capture_change(root, base, candidate)
    removed_paths = set(removed)
    if requested_head is not None:
        _git(root, "merge-base", "--is-ancestor", requested_head, head)
    before = canonical_source_identity(root)
    executable = tuple(path for path in changed if not non_executable_documentation(root, path))
    authored = tuple(path for path in executable if not is_generated_contract_path(path))
    policy_paths = tuple(path for path in authored if is_verification_policy_path(path))
    required = required_policy_tests(policy_paths)
    dependency_intent, unsupported_dependency_intent, hashmarks_upgrade = (
        _backend_dependency_intent(root, base_sha, executable)
    )
    nomination_arguments: dict[str, Any] = {}
    if required:
        nomination_arguments["required"] = required
    if "backend/uv.lock" in executable and not hashmarks_upgrade:
        nomination_arguments["dependency_base"] = {
            "lock": _base_lock_bytes(root, base_sha),
            "base_revision": base_sha,
            "lock_path": "backend/uv.lock",
        }
    assets = documentation_asset_candidates(authored)
    if assets:
        nomination_arguments["asset_paths"] = assets
    evidence = nominator(root, authored, **nomination_arguments) if authored or required else {}
    documents = documentation_proof_paths(changed)
    if documents:
        evidence["documentation_policy"] = {
            "authority": "oh-goon-execution-policy",
            "paths": list(documents),
            "coverage": "not-claimed",
        }
    if hashmarks_upgrade:
        evidence["hashmarks_upgrade"] = {
            "authority": "oh-goon-execution-policy",
            "scope": "mandatory-hashmarks-upgrade",
            "consumer_coverage": "not-claimed",
        }
    accepted_assets = admitted_documentation_assets(authored, evidence)
    executable = tuple(path for path in executable if path not in accepted_assets)
    static = cast(set[_STATIC], required_static_obligations(executable))
    pytest: set[str] = {DOCUMENTATION_PROOF_TEST} if documents else set()
    if hashmarks_upgrade:
        pytest.update(HASHMARKS_UPGRADE_TESTS)
    frontend: set[str] = set()
    unverified: list[str] = []
    dependency_qualified = (
        "backend/uv.lock" in executable
        and not hashmarks_upgrade
        and not unsupported_dependency_intent
        and _bounded_dependency_scope(evidence, base_sha)
    )
    if (
        (dependency_intent or "backend/uv.lock" in executable)
        and not (dependency_qualified or hashmarks_upgrade)
    ) or "frontend/package-lock.json" in executable:
        unverified.append("DEPENDENCY_CONSUMER_SCOPE_UNVERIFIED")
    if dependency_qualified:
        evidence["dependency_admission"] = {
            "authority": "oh-goon-execution-policy",
            "state": "bounded-runtime-dependency-proof",
            "changed_node_ids": evidence["dependency_changes"]["changed_node_ids"],
            "consumer_scope": evidence["dependency_changes"]["consumer_scope"],
            "rationale": (
                "Each changed current dependency selection has a current resolved-unique "
                "source consumer and a qualified verifier. Native lock, runtime and package "
                "checks are required. Unobserved consumers and bounded verifier searches "
                "remain outside this positive proof; complete impact is not claimed."
            ),
        }
    observations: list[dict[str, Any]] = evidence.get("observations", [])
    policy_proofs = [
        projected
        for projected in evidence.get("required_checks", [])
        if projected.get("nomination", {}).get("available") is True
    ]
    if policy_paths:
        qualified_policy_paths = {
            projected["nomination"].get("verification_target", {}).get("path")
            for projected in policy_proofs
        }
        if not set(required) <= qualified_policy_paths:
            unverified.append("VERIFICATION_POLICY_PROTECTION_UNAVAILABLE")
        pytest.update(str(path) for path in qualified_policy_paths if path in required)
    observed_paths = {row.get("changed_path") for row in observations}
    upgrade_paths = {"backend/pyproject.toml", "backend/uv.lock"} if hashmarks_upgrade else set()
    for path in authored:
        if path not in removed_paths and path not in observed_paths and path not in upgrade_paths:
            unverified.append(f"NO_PRODUCER_OBSERVATION:{path}")
    for row in observations:
        path = str(row.get("changed_path", ""))
        if path not in authored:
            raise ValueError("Producer observation is outside the admitted change set")
        # Git identifies removed members; they cannot supply execution proof.
        # Keep any producer absence observation without claiming no consumers.
        if path in removed_paths or path in accepted_assets or path in upgrade_paths:
            continue
        if row.get("state") not in {"LINKED", "CHANGED_TEST"} or row.get("stale") is True:
            if (
                path in policy_paths
                and not path.endswith(".py")
                and policy_proofs
                and row.get("stale") is not True
            ):
                row = row.copy()
                row.update(state="POLICY_OBLIGATION", nominations=policy_proofs)
            else:
                unverified.append(f"NO_QUALIFIED_VERIFIER:{path}")
                continue
        if (
            row.get("state") == "LINKED"
            and path not in row.get("edit_candidates", [])
            and not (path == "backend/uv.lock" and dependency_qualified)
        ):
            unverified.append(f"PRODUCER_SELECTED_ANOTHER_OWNER:{path}")
            continue
        if (Path(path).name == "conftest.py" or "/tests/factories/" in path) and row.get(
            "qualification", {}
        ).get("completeness") != "complete":
            unverified.append(f"SHARED_FIXTURE_CONSUMER_SCOPE_UNVERIFIED:{path}")
        admitted = 0
        for projected in row.get("nominations", []):
            nomination = projected["nomination"]
            if nomination.get("available") is not True:
                continue
            target = nomination.get("verification_target", {})
            selected = str(projected.get("workspace_prefix", "")) + str(target.get("path", ""))
            if not selected or Path(selected).is_absolute() or ".." in Path(selected).parts:
                raise ValueError("Producer nomination has an unsafe test path")
            runner = nomination.get("runner_evidence")
            if runner == "pytest":
                if not selected.startswith(
                    ("backend/tests/", "scripts/tests/", "tests/integration/")
                ):
                    unverified.append(f"NATIVE_PROJECT_CONTEXT_REQUIRED:{selected}")
                    continue
                pytest.add(selected)
            elif runner == "vitest":
                frontend.add(selected)
                static.add("frontend")
            else:
                continue
            admitted += 1
        if not admitted:
            unverified.append(f"NO_EXECUTABLE_VERIFIER:{path}")
    if pytest and not unverified:
        pytest, unavailable = _collect_pytest_proof(
            root, evidence, pytest, required, set(accepted_assets) | removed_paths | upgrade_paths
        )
        unverified.extend(unavailable)
    if dependency_qualified and "collection" in evidence["dependency_admission"]:
        if not _bounded_dependency_scope(evidence, base_sha, selected_files=pytest):
            unverified.append("DEPENDENCY_CONSUMER_SCOPE_UNVERIFIED")
        require_current_dependency_environment(evidence["dependency_changes"])
    if not changed:
        unverified.append("EMPTY_CHANGE_SET")
    if canonical_source_identity(root) != before:
        raise ValueError("Source changed while verification was being admitted")
    return ChangeVerificationPlan(
        base=base_sha,
        candidate=head,
        requested_head=requested_head,
        source_identity=before,
        changed_paths=changed,
        removed_paths=removed,
        static=tuple(sorted(static)),
        pytest=tuple(sorted(pytest)),
        frontend=tuple(sorted(frontend)),
        unverified=tuple(unverified),
        evidence=evidence,
        behavioral_status="REQUIRED" if executable else "NOT_APPLICABLE",
        policy_status="REVIEW_REQUIRED" if policy_paths else "ORDINARY",
        obligations=required_policy_obligations(policy_paths)
        + ((DOCUMENTATION_ASSET_OBLIGATION,) if accepted_assets else ())
        + ((DOCUMENTATION_PROOF_OBLIGATION,) if documents else ())
        + ((HASHMARKS_UPGRADE_OBLIGATION,) if hashmarks_upgrade else ()),
    )


def require_current_plan(root: Path, plan: ChangeVerificationPlan) -> None:
    if _git(root, "rev-parse", "HEAD").strip() != plan.candidate:
        raise ValueError("Verification candidate changed after admission")
    _git(root, "cat-file", "-e", plan.base + "^{commit}")
    if canonical_source_identity(root) != plan.source_identity:
        raise ValueError("Verification source changed after admission")
    if "producer_implementation_identity" in plan.evidence and plan.evidence.get(
        "producer_implementation_identity"
    ) != (require_loaded_hashmarks_runtime()):
        raise ValueError("Loaded Hashmarks implementation changed after admission")
    if plan.evidence.get("dependency_admission") or (
        "backend/uv.lock" in plan.changed_paths
        and not plan.unverified
        and not plan.evidence.get("hashmarks_upgrade")
    ):
        if not _bounded_dependency_scope(
            plan.evidence,
            plan.base,
            selected_files={path.split("::", 1)[0] for path in plan.pytest},
        ):
            raise ValueError("Bounded dependency proof changed after admission")
        require_current_dependency_environment(plan.evidence["dependency_changes"])


def _commands(root: Path, plan: ChangeVerificationPlan) -> list[tuple[str, tuple[str, ...], Path]]:
    python = sys.executable
    commands: list[tuple[str, tuple[str, ...], Path]] = []
    if "python" in plan.static:
        commands.extend(
            (
                (
                    "Ruff",
                    (
                        python,
                        "-m",
                        "ruff",
                        "check",
                        "backend/src",
                        "backend/tests",
                        "scripts",
                        "tests/integration",
                    ),
                    root,
                ),
                (
                    "Format",
                    (
                        python,
                        "-m",
                        "ruff",
                        "format",
                        "--check",
                        "--diff",
                        "backend/src",
                        "backend/tests",
                        "scripts",
                        "tests/integration",
                    ),
                    root,
                ),
                ("Types", (python, "-m", "mypy"), root / "backend"),
            )
        )
    if "architecture" in plan.static:
        commands.append(
            ("Architecture", (python, "-m", "oh_goon.repository_tooling.check_architecture"), root)
        )
    if "generated" in plan.static:
        commands.append(("Generated contracts", ("make", "check-generated"), root))
    if "package" in plan.static:
        commands.append(
            (
                "Native package",
                (
                    "make",
                    "backend-lock-check",
                    "backend-python-capability-check",
                    "backend-package-build-check",
                ),
                root,
            )
        )
    if "frontend" in plan.static:
        for script in ("typecheck", "lint", "architecture", "i18n:check"):
            commands.append((f"Frontend {script}", ("npm", "run", script), root / "frontend"))
    return commands


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("Verification evidence exceeds its transport bound")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Verification evidence requires an object")
    return value


def require_complete_membership(
    payload: Mapping[str, Any], *, source_identity: str | None = None
) -> None:
    require_native_pytest_pass(payload, expected_source=source_identity)


def _retain_native_report(outcome: dict[str, Any], path: Path) -> dict[str, Any]:
    try:
        payload = _read_json(path)
    except (OSError, ValueError) as exc:
        outcome["native_evidence_error"] = str(exc)
        return {}
    outcome["native_report"] = payload
    return payload


def require_merge_selection(root: Path, plan: ChangeVerificationPlan) -> None:
    if plan.unverified:
        raise ValueError("SELECTION_UNVERIFIED: " + "; ".join(plan.unverified))
    assets = admitted_documentation_assets(plan.changed_paths, plan.evidence)
    current_paths = (
        {path for path in plan.changed_paths if not non_executable_documentation(root, path)}
        - set(plan.removed_paths)
        - set(assets)
    )
    behavioral_pytest = behavioral_pytest_targets(plan.pytest, plan.evidence, current_paths)
    if (
        plan.behavioral_status == "REQUIRED"
        and not (behavioral_pytest or plan.frontend)
        and not all(
            is_generated_contract_path(path)
            or non_executable_documentation(root, path)
            or path in assets
            for path in plan.changed_paths
        )
    ):
        raise ValueError("SELECTION_UNVERIFIED: executable changes have no behavioral proof")


def execute_change(
    root: Path, plan: ChangeVerificationPlan, *, mode: str, workers: int
) -> dict[str, Any]:
    require_current_plan(root, plan)
    if mode == "merge":
        require_merge_selection(root, plan)
    outcomes: list[dict[str, Any]] = []
    for label, command, cwd in _commands(root, plan):
        result = run_command(
            *command,
            cwd=cwd,
            timeout=300,
            require_process_group_quiescence=True,
            parent_death_signal=True,
            max_capture_bytes_per_stream=64 * 1024,
            stream_output=lambda stream, text: print(
                text, end="", file=sys.stdout if stream == "stdout" else sys.stderr, flush=True
            ),
        )
        outcomes.append({"owner": label, "returncode": result.returncode})
        try:
            require_current_plan(root, plan)
        except ValueError as exc:
            return {
                "status": "FAIL" if result.returncode else "INCOMPLETE",
                "outcomes": outcomes,
                "reason": str(exc),
            }
        if result.returncode:
            return {"status": "FAIL", "outcomes": outcomes}
    if plan.frontend:
        with tempfile.TemporaryDirectory(prefix="oh-goon-change-frontend-") as directory:
            report = Path(directory) / "vitest.json"
            result = run_command(
                "npm",
                "run",
                "test",
                "--",
                *(path.removeprefix("frontend/") for path in plan.frontend),
                "--reporter=default",
                "--reporter=json",
                f"--outputFile={report}",
                cwd=root / "frontend",
                timeout=300,
                require_process_group_quiescence=True,
                parent_death_signal=True,
                max_capture_bytes_per_stream=64 * 1024,
                stream_output=lambda stream, text: print(
                    text, end="", file=sys.stdout if stream == "stdout" else sys.stderr, flush=True
                ),
            )
            outcomes.append(
                {
                    "owner": "Frontend behavior",
                    "returncode": result.returncode,
                    "native_project_root": str(root / "frontend"),
                }
            )
            native_frontend = _retain_native_report(outcomes[-1], report)
            if result.returncode:
                return {"status": "FAIL", "outcomes": outcomes}
            if not native_frontend:
                return {
                    "status": "INCOMPLETE",
                    "outcomes": outcomes,
                    "reason": "Native frontend evidence unavailable",
                }
            try:
                complete, satisfied, _counts = vitest_outcomes(native_frontend)
            except ValueError as exc:
                return {"status": "INCOMPLETE", "outcomes": outcomes, "reason": str(exc)}
            if not complete or not satisfied:
                return {
                    "status": "INCOMPLETE",
                    "outcomes": outcomes,
                    "reason": "Native frontend assertions are skipped, failed or incomplete",
                }
            outcomes[-1]["native_report"] = native_frontend
            require_current_plan(root, plan)
    if plan.pytest:
        with tempfile.TemporaryDirectory(prefix="oh-goon-change-result-") as directory:
            report = Path(directory) / "pytest.json"
            result = run_command(
                sys.executable,
                "-m",
                "oh_goon.repository_tooling.test_batches",
                "--pytest-config",
                "backend/pyproject.toml",
                "--qualified",
                "--fail-fast",
                "--show-output",
                "--granularity",
                "tests",
                "--workers",
                str(workers),
                "--batch-size",
                "64",
                "--batch-timeout",
                "180",
                "--marker",
                _MERGE_MARKER,
                "--report-path",
                str(report),
                "--report-projection",
                "verification",
                *plan.pytest,
                cwd=root,
                timeout=None,
                require_process_group_quiescence=True,
                parent_death_signal=True,
                max_capture_bytes_per_stream=64 * 1024,
                stream_output=lambda stream, text: print(
                    text, end="", file=sys.stdout if stream == "stdout" else sys.stderr, flush=True
                ),
            )
            outcomes.append({"owner": "Native pytest", "returncode": result.returncode})
            native = _retain_native_report(outcomes[-1], report)
            if result.returncode:
                return {"status": "FAIL", "outcomes": outcomes}
            if not native:
                return {
                    "status": "INCOMPLETE",
                    "outcomes": outcomes,
                    "reason": "Native pytest evidence unavailable",
                }
            try:
                require_complete_membership(native, source_identity=plan.source_identity)
            except ValueError as exc:
                return {"status": "INCOMPLETE", "outcomes": outcomes, "reason": str(exc)}
            outcomes[-1]["native_report"] = native
    require_current_plan(root, plan)
    return {
        "status": "CHECKS_PASSED",
        "outcomes": outcomes,
        "daily_deferred": "Unrelated, exhaustive and daily-cadence instances; no full-regression claim",
        "performance": "PROVISIONAL",
    }


def _write_transport(path: Path, payload: Mapping[str, Any], root: Path) -> None:
    target = path.absolute()
    if target.is_relative_to(root) or target.parent.resolve(strict=True) != target.parent:
        raise ValueError("Verification transport must use canonical scratch outside source")
    metadata = target.parent.stat(follow_symlinks=False)
    if metadata.st_mode & 0o077:
        raise ValueError("Verification transport parent must be private")
    publish_text_file(target, json.dumps(payload, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--candidate")
    parser.add_argument("--head")
    parser.add_argument("--mode", choices=("focused", "merge"), default="focused")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--execute", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("workers must be positive")
    try:
        root = args.root.resolve(strict=True)
        plan = (
            ChangeVerificationPlan.model_validate(_read_json(args.execute))
            if args.execute
            else admit_change(
                root, base=args.base, candidate=args.candidate, requested_head=args.head
            )
        )
        if args.plan:
            _write_transport(args.plan, plan.model_dump(mode="json"), root)
            if args.mode == "merge":
                require_merge_selection(root, plan)
            if args.github_output:
                with args.github_output.open("a", encoding="utf-8") as stream:
                    stream.write(f"python={'true' if 'python' in plan.static else 'false'}\n")
                    stream.write(f"frontend={'true' if 'frontend' in plan.static else 'false'}\n")
                    stream.write(f"pytest={'true' if plan.pytest else 'false'}\n")
            return 0
        result = execute_change(root, plan, mode=args.mode, workers=args.workers)
        payload = {"plan": plan.model_dump(mode="json"), **result}
        if result["status"] == "CHECKS_PASSED":
            payload["status"] = "FOCUSED / NOT_FULLY_VERIFIED"
            if args.mode == "merge":
                payload["status"] = "MERGE_PROOF_READY"
                payload["merge_authority"] = (
                    "UNVERIFIED: independent trusted-base aggregate required"
                )
        if args.report:
            _write_transport(args.report, payload, root)
            # The report file is the complete machine-readable proof. Printing
            # it again can stream megabytes of native pytest receipts into the
            # hosted log and make the CI transport slower than the tests.
            # Keep the historical complete stdout contract without --report.
            summary: dict[str, Any] = {
                "status": payload["status"],
                "candidate": plan.candidate,
                "outcomes": [
                    {"owner": row["owner"], "returncode": row["returncode"]}
                    for row in result["outcomes"]
                ],
            }
            if "reason" in result:
                summary["reason"] = result["reason"]
            print(json.dumps(summary, sort_keys=True))
        else:
            print(json.dumps(payload, sort_keys=True))
        return (
            0 if payload["status"] in {"MERGE_PROOF_READY", "FOCUSED / NOT_FULLY_VERIFIED"} else 1
        )
    except (ValueError, OSError) as exc:
        print(f"SELECTION_UNVERIFIED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
