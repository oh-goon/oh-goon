"""Execution need from producer-selected Make source candidates."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from oh_goon.integrations.hashmarks import HashmarksSourceEvidence, observe_make_shell_declarations
from oh_goon.integrations.package_script_execution_authority import PackageExecutableAuthority
from oh_goon.integrations.shell_execution_authority import (
    ShellSourceObservations,
    derive_shell_observation_authority,
)


@dataclass(frozen=True, slots=True)
class MakeTargetAuthority:
    target: str
    tools: tuple[str, ...]
    provenance: tuple[tuple[str, str], ...]
    unresolved: tuple[str, ...]
    source_identities: tuple[tuple[str, str], ...]
    package_executables: tuple[PackageExecutableAuthority, ...]
    package_provenance: tuple[tuple[str, str], ...]
    materialized_executables: tuple[str, ...]
    repository_evidence: tuple[HashmarksSourceEvidence, ...] = ()


def observe_make_target_sources(
    root: Path,
    targets: tuple[str, ...],
    *,
    scope: object | None = None,
    source_capture: HashmarksSourceEvidence | None = None,
) -> HashmarksSourceEvidence:
    """Acquire one native parse and independent source projection per selected root."""
    return observe_make_shell_declarations(
        root,
        make_paths=("Makefile",),
        layout="file",
        selected_targets=targets,
        include_source_bytes=True,
        scope=scope,
        source_capture=source_capture,
    )


def _custody_identities(
    paths: set[str],
    observations: tuple[HashmarksSourceEvidence, ...],
    cache: dict[str, str],
    identities: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    # Execution custody hashes exact transported bytes; it does not recreate
    # producer content identity, source meaning or repository freshness.
    for relative, digest in identities:
        cache.setdefault(relative, digest)
    for evidence in observations:
        if evidence.source_bytes is None:
            continue
        inputs = [
            row
            for provider in evidence.packet()["providers"]
            for row in provider["inputs"]
            if row.get("observation_kind") == "content"
        ]
        if len(inputs) == 1 and inputs[0]["path"] in paths:
            cache.setdefault(
                inputs[0]["path"], "sha256:" + hashlib.sha256(evidence.source_bytes).hexdigest()
            )
    for relative in sorted(paths):
        if relative not in cache:
            raise ValueError("Execution source byte custody is unavailable: " + relative)
    return tuple((relative, cache[relative]) for relative in sorted(paths))


def derive_make_target_authority(
    root: Path,
    target: str,
    *,
    source_evidence: HashmarksSourceEvidence | None = None,
    custody_cache: dict[str, str] | None = None,
    source_observations: ShellSourceObservations | None = None,
) -> MakeTargetAuthority:
    """Project positive source need without interpreting native Make semantics."""
    root = root.resolve()
    try:
        evidence = source_evidence or observe_make_target_sources(root, (target,))
        packet = evidence.packet()
        providers = packet.get("providers", [])
        groups = packet.get("declarations", {}).get("groups", [])
        if len(providers) != 1 or providers[0].get("state") != "collected" or len(groups) != 1:
            raise ValueError("Make selected source evidence is unavailable")
        file = groups[0]["declarations"][0]["value"]
        selection = file["selected_target_source"]
        selected = selection["by_target"].get(target)
        if selected is None or selected["selected_targets"] != [target]:
            raise ValueError("Make target lacks its producer-selected source projection")
        shell = derive_shell_observation_authority(
            root,
            evidence,
            selected["source_candidate_indices"],
            chain=(target,),
            candidate_paths=selected["candidate_paths"],
            source_observations=source_observations,
        )
        unresolved = set(shell.unresolved)
        unresolved.update(f"{target}: {row['reason']}" for row in selected["unresolved"])
        unresolved.update("make source cycle: " + " -> ".join(path) for path in selected["cycles"])
        if any(
            fact["kind"] in {"make-variable-declaration", "make-conditional-syntax"}
            for fact in file["facts"]
        ):
            unresolved.add(f"{target}: native Make configuration evaluation is unproven")
        if (
            selected["unresolved_source_indices"]
            or selected["source_truncation"] != "not-truncated"
        ):
            unresolved.add(f"{target}: selected Make source contains uninterpreted syntax")
        sources = {"Makefile", *shell.scripts, *shell.package_sources}
        identities = _custody_identities(
            sources,
            shell.repository_evidence,
            {} if custody_cache is None else custody_cache,
            shell.source_identities,
        )
        return MakeTargetAuthority(
            target=target,
            tools=shell.tools,
            provenance=shell.provenance,
            unresolved=tuple(sorted(unresolved)),
            source_identities=identities,
            package_executables=shell.package_executables,
            package_provenance=shell.package_provenance,
            materialized_executables=shell.materialized_executables,
            repository_evidence=shell.repository_evidence,
        )
    except (OSError, ValueError) as exc:
        return MakeTargetAuthority(target, (), (), (str(exc),), (), (), (), ())
