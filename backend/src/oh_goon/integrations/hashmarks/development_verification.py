"""Source-qualified verifier nominations for the existing development check owner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime


def nominate_changed_verification(
    root: Path, paths: tuple[str, ...], *, required: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Keep producer relations and qualifications; never turn them into owner coverage.

    One native map/read session serves the complete supplied change set. The
    consumer applies cadence later; Hashmarks retains repository relevance.
    """
    from hashmarks import CodeMap
    from hashmarks.operation_contract import validate_operation_response
    from hashmarks.verification_nomination_validation import validate_verification_nomination

    implementation = require_loaded_hashmarks_runtime()
    observations: list[dict[str, Any]] = []
    required_checks: list[dict[str, Any]] = []
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
            with native.reads.session():
                if not prefix:
                    for path in required:
                        nomination = native.verification.nomination(path)
                        if validate_verification_nomination(nomination).get("valid") is not True:
                            raise ValueError("Hashmarks rejected a required policy proof")
                        required_checks.append({"nomination": nomination, "workspace_prefix": ""})
                for path in selected_paths:
                    relative = path.removeprefix(prefix)
                    if not (root / path).is_file():
                        observations.append(
                            {
                                "changed_path": path,
                                "state": "UNAVAILABLE",
                                "reason": "source-absent",
                            }
                        )
                        continue
                    nominated = native.verification.nomination(relative)
                    validation = validate_verification_nomination(nominated)
                    if validation.get("valid") is not True:
                        raise ValueError("Hashmarks rejected its changed-source nomination")
                    if nominated.get("available") is True:
                        observations.append(
                            {
                                "changed_path": path,
                                "state": "CHANGED_TEST",
                                "nominations": [
                                    {"nomination": nominated, "workspace_prefix": prefix}
                                ],
                            }
                        )
                        continue
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
                        validation = validate_verification_nomination(nomination)
                        if validation.get("valid") is not True:
                            raise ValueError("Hashmarks rejected its linked verifier nomination")
                        nominations.append(
                            {
                                "nomination": nomination,
                                "relationship": row["coverage_evidence"],
                                "workspace_prefix": prefix,
                            }
                        )
                    observations.append(
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
    packet = {
        "schema": "oh-goon.hashmarks-development-nominations/v1",
        "producer_implementation_identity": implementation,
        "observations": observations,
        "required_checks": required_checks,
        "owner_coverage": "not-claimed",
    }
    if len(json.dumps(packet).encode("utf-8")) > 4 * 1024 * 1024:
        raise ValueError("Hashmarks development evidence exceeds the bounded transport")
    return packet
