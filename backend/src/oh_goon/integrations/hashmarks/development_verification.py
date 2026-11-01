"""Source-qualified verifier nominations for the existing development check owner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime

if TYPE_CHECKING:
    from hashmarks import CodeMap


def nominate_source_verification(
    native: CodeMap, relative: str, *, workspace_prefix: str = ""
) -> dict[str, Any]:
    """Project the same native source-to-test relations for changed or dependent source."""
    from hashmarks.operation_contract import validate_operation_response
    from hashmarks.verification_nomination_validation import validate_verification_nomination

    nominated = native.verification.nomination(relative)
    if validate_verification_nomination(nominated).get("valid") is not True:
        raise ValueError("Hashmarks rejected its changed-source nomination")
    if nominated.get("available") is True:
        return {
            "changed_path": workspace_prefix + relative,
            "state": "CHANGED_TEST",
            "nominations": [{"nomination": nominated, "workspace_prefix": workspace_prefix}],
        }
    ownership = cast(
        dict[str, Any],
        native.tasks.verification_ownership_graph(
            f"Verify {relative}", limit=20, candidate_limit=16
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
                "workspace_prefix": workspace_prefix,
            }
        )
    return {
        "changed_path": workspace_prefix + relative,
        "state": "LINKED" if nominations else "UNAVAILABLE",
        "edit_candidates": [
            workspace_prefix + value for value in ownership.get("edit_candidates", [])
        ],
        "stale": ownership.get("stale"),
        "qualification": ownership.get("verification_qualification", {}),
        "nominations": nominations,
    }


def nominate_changed_verification(
    root: Path,
    paths: tuple[str, ...],
    *,
    required: tuple[str, ...] = (),
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
            ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
                # Native observe may reconcile a contributor and recompute the
                # whole operation. Retain only rows from its accepted attempt.
                observed: list[dict[str, Any]] = []
                checks: list[dict[str, Any]] = []
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
                    observed.append(
                        nominate_source_verification(native, relative, workspace_prefix=prefix)
                    )
                return observed, checks, assets

            observed, checks, assets = native.reads.observe(nominate_workspace)
            observations.extend(observed)
            required_checks.extend(checks)
            if assets is not None:
                asset_evidence = assets
    packet = {
        "schema": "oh-goon.hashmarks-development-nominations/v1",
        "producer_implementation_identity": implementation,
        "observations": observations,
        "required_checks": required_checks,
        "owner_coverage": "not-claimed",
        "asset_evidence": asset_evidence,
    }
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
