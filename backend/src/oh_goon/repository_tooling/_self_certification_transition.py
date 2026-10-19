from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oh_goon.repository_tooling._self_certification_plan_evidence import (
    _load_package_build_evidence,
    _package_config_identity,
    _source_zip_path,
    build_sealed_source_zip,
    evidence_root,
    record_source_seal,
    verify_sealed_source_zip_identity,
)
from oh_goon.repository_tooling._self_certification_plan_units import (
    _source_units,
    _verification_config_identity,
)
from oh_goon.repository_tooling.release.package_slim import (
    canonical_source_identity,
)
from oh_goon.repository_tooling.self_certification_artifacts import (
    build_artifact_manifest,
    build_artifact_sidecars,
    build_cumulative_patch,
    build_handoff,
    load_cumulative_patch_evidence,
    load_fresh_zip_evidence,
    load_handoff_evidence,
    load_manifest_evidence,
    load_parent_source_input,
    load_patch_replay_evidence,
    load_persist_evidence,
    load_sidecar_evidence,
    persist_artifact_set,
    stage_parent_source_zip,
    verify_cumulative_patch_replay,
    verify_fresh_source_zip,
    verify_retained_artifact_set,
)


def stage_parent_source_input(root: Path, candidate: Path) -> dict[str, object]:
    root = root.resolve()
    source_identity = canonical_source_identity(root)
    source_units = _source_units(root)
    verification_config_identity = _verification_config_identity(root, source_units)
    state_dir = evidence_root(root, source_identity, verification_config_identity)
    stage_parent_source_zip(root, state_dir, candidate)
    verified = load_parent_source_input(root, state_dir)
    if verified is None:
        raise RuntimeError("parent source ZIP did not remain verified after staging")
    return verified


def _current_plan(root: Path) -> dict[str, object]:
    from oh_goon.repository_tooling.self_certification_plan import self_certification_plan

    return self_certification_plan(root)


@dataclass(frozen=True, slots=True)
class _ArtifactTransitionContext:
    root: Path
    payload: dict[str, object]
    source_identity: str
    verification_config_identity: str
    state_dir: Path
    seal_identity: str

    @property
    def source_units(self) -> list[dict[str, object]]:
        return [item for item in self.payload["all_units"] if item.get("phase") == "source"]


def _transition_context(root: Path, payload: dict[str, object]) -> _ArtifactTransitionContext:
    source_identity = str(payload["source_identity"])
    verification_config_identity = str(payload["verification_config_identity"])
    return _ArtifactTransitionContext(
        root=root,
        payload=payload,
        source_identity=source_identity,
        verification_config_identity=verification_config_identity,
        state_dir=evidence_root(root, source_identity, verification_config_identity),
        seal_identity=str(payload["source_seal"].get("seal_identity") or ""),
    )


def _execute_source_seal(ctx: _ArtifactTransitionContext) -> int:
    record_source_seal(
        ctx.root,
        ctx.source_identity,
        ctx.verification_config_identity,
        str(ctx.payload["plan_identity"]),
        ctx.source_units,
    )
    return 0


def _execute_source_zip(ctx: _ArtifactTransitionContext) -> int:
    if not ctx.seal_identity:
        return 3
    build_sealed_source_zip(
        ctx.root, ctx.source_identity, ctx.verification_config_identity, ctx.seal_identity
    )
    return 0


def _package_inputs(ctx: _ArtifactTransitionContext):
    build_evidence = _load_package_build_evidence(
        ctx.root, ctx.source_identity, ctx.verification_config_identity, ctx.seal_identity
    )
    source_zip = _source_zip_path(ctx.root, ctx.source_identity, ctx.verification_config_identity)
    source_zip_sha256 = str(build_evidence["artifact_sha256"]) if build_evidence else ""
    return build_evidence, source_zip, source_zip_sha256


def _verify_source_identity_transition(ctx: _ArtifactTransitionContext, build_evidence) -> int:
    if build_evidence is None:
        return 3
    verify_sealed_source_zip_identity(
        ctx.root,
        ctx.source_identity,
        ctx.verification_config_identity,
        ctx.seal_identity,
        build_evidence,
    )
    return 0


def _execute_early_package_transition(
    ctx: _ArtifactTransitionContext, unit_name: str
) -> int | None:
    build_evidence, source_zip, source_zip_sha256 = _package_inputs(ctx)
    if unit_name == "package/verify-source-identity":
        return _verify_source_identity_transition(ctx, build_evidence)
    if unit_name == "package/fresh-zip-acceptance":
        if build_evidence is None:
            return 3
        verify_fresh_source_zip(
            ctx.root,
            ctx.state_dir,
            source_zip,
            source_zip_sha256,
            ctx.source_identity,
            ctx.seal_identity,
        )
        return 0
    parent_input = load_parent_source_input(ctx.root, ctx.state_dir)
    if unit_name == "package/cumulative-patch":
        if parent_input is None or build_evidence is None:
            return 3
        build_cumulative_patch(ctx.root, ctx.state_dir, source_zip, source_zip_sha256, parent_input)
        return 0
    patch_evidence = (
        load_cumulative_patch_evidence(ctx.root, ctx.state_dir, source_zip_sha256, parent_input)
        if parent_input is not None
        else None
    )
    if unit_name == "package/verify-patch-replay":
        if patch_evidence is None:
            return 3
        verify_cumulative_patch_replay(
            ctx.root, ctx.state_dir, source_zip, source_zip_sha256, patch_evidence
        )
        return 0
    return _execute_artifact_evidence_transition(ctx, unit_name, source_zip_sha256, patch_evidence)


def _build_handoff_transition(
    ctx: _ArtifactTransitionContext,
    source_zip_sha256: str,
    patch_evidence,
    replay_evidence,
    fresh_zip_evidence,
) -> int:
    if patch_evidence is None or replay_evidence is None or fresh_zip_evidence is None:
        return 3
    build_handoff(
        ctx.root,
        ctx.state_dir,
        plan_identity=str(ctx.payload["plan_identity"]),
        source_identity=ctx.source_identity,
        verification_config_identity=ctx.verification_config_identity,
        seal_identity=ctx.seal_identity,
        source_units=ctx.source_units,
        source_zip_sha256=source_zip_sha256,
        patch_evidence=patch_evidence,
        replay_evidence=replay_evidence,
        fresh_zip_evidence=fresh_zip_evidence,
    )
    return 0


def _execute_artifact_evidence_transition(
    ctx: _ArtifactTransitionContext,
    unit_name: str,
    source_zip_sha256: str,
    patch_evidence,
) -> int | None:
    replay_evidence = (
        load_patch_replay_evidence(ctx.state_dir, source_zip_sha256, patch_evidence)
        if patch_evidence is not None
        else None
    )
    fresh_zip_evidence = load_fresh_zip_evidence(
        ctx.state_dir, source_zip_sha256, ctx.source_identity, ctx.seal_identity
    )
    if unit_name == "package/handoff":
        return _build_handoff_transition(
            ctx, source_zip_sha256, patch_evidence, replay_evidence, fresh_zip_evidence
        )
    handoff_evidence = (
        load_handoff_evidence(
            ctx.root,
            ctx.state_dir,
            source_zip_sha256,
            patch_evidence,
            replay_evidence,
            fresh_zip_evidence,
        )
        if patch_evidence and replay_evidence and fresh_zip_evidence
        else None
    )
    if unit_name == "package/sidecars":
        if patch_evidence is None or handoff_evidence is None:
            return 3
        build_artifact_sidecars(
            ctx.root, ctx.state_dir, source_zip_sha256, patch_evidence, handoff_evidence
        )
        return 0
    sidecar_evidence = (
        load_sidecar_evidence(
            ctx.root, ctx.state_dir, source_zip_sha256, patch_evidence, handoff_evidence
        )
        if patch_evidence and handoff_evidence
        else None
    )
    if unit_name == "package/manifest":
        if sidecar_evidence is None:
            return 3
        build_artifact_manifest(
            ctx.root,
            ctx.state_dir,
            plan_identity=str(ctx.payload["plan_identity"]),
            source_identity=ctx.source_identity,
            verification_config_identity=ctx.verification_config_identity,
            seal_identity=ctx.seal_identity,
            package_config_identity=_package_config_identity(),
            sidecar_evidence=sidecar_evidence,
            replay_evidence=replay_evidence,
            fresh_zip_evidence=fresh_zip_evidence,
        )
        return 0
    return _execute_persistence_transition(
        ctx, unit_name, source_zip_sha256, sidecar_evidence, replay_evidence, fresh_zip_evidence
    )


def _execute_persistence_transition(
    ctx: _ArtifactTransitionContext,
    unit_name: str,
    source_zip_sha256: str,
    sidecar_evidence,
    replay_evidence,
    fresh_zip_evidence,
) -> int | None:
    manifest_evidence = (
        load_manifest_evidence(
            ctx.root,
            ctx.state_dir,
            _package_config_identity(),
            sidecar_evidence,
            replay_evidence,
            fresh_zip_evidence,
        )
        if sidecar_evidence is not None
        and replay_evidence is not None
        and fresh_zip_evidence is not None
        else None
    )
    if unit_name == "package/persist":
        if manifest_evidence is None:
            return 3
        persist_artifact_set(ctx.root, ctx.state_dir, manifest_evidence)
        return 0
    persist_evidence = (
        load_persist_evidence(ctx.root, ctx.state_dir, manifest_evidence)
        if manifest_evidence is not None
        else None
    )
    if unit_name == "package/verify-persistence":
        if manifest_evidence is None or persist_evidence is None:
            return 3
        verify_retained_artifact_set(ctx.root, ctx.state_dir, manifest_evidence, persist_evidence)
        return 0
    return None


def execute_artifact_transition(root: Path, unit_name: str) -> int:
    payload = _current_plan(root)
    matching = [unit for unit in payload["all_units"] if unit["unit"] == unit_name]
    if not matching:
        raise ValueError(f"unknown self-certification unit: {unit_name}")
    unit = matching[0]
    if unit["status"] == "PASS":
        return 0
    if unit["status"] == "BLOCKED":
        return 3
    if unit.get("phase") == "source":
        raise ValueError(
            "source verification units are executable only through the canonical Fleet Plan"
        )
    ctx = _transition_context(root, payload)
    if unit_name == "seal-source":
        return _execute_source_seal(ctx)
    if unit_name == "package/source-zip":
        return _execute_source_zip(ctx)
    result = _execute_early_package_transition(ctx, unit_name)
    if result is not None:
        return result
    raise ValueError(f"self-certification transition is not an artifact transition: {unit_name}")
