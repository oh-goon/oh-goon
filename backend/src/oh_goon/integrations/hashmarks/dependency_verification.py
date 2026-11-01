"""Native dependency-to-source-to-verifier facts for the Hashmarks upgrade boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.metadata import distribution
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from oh_goon.integrations.hashmarks.dependencies import captured_lock_revision
from oh_goon.integrations.hashmarks.development_verification import (
    nominate_source_verification,
)
from oh_goon.integrations.hashmarks.runtime_contract import (
    loaded_hashmarks_version,
    require_loaded_hashmarks_runtime,
)

if TYPE_CHECKING:
    from hashmarks import CodeMap

_LOCK_PATH = "backend/uv.lock"
_SOURCE_PREFIX = "backend/src/oh_goon/integrations/hashmarks"
_MAX_LOCK_BYTES = 8 * 1024 * 1024
_MAX_METADATA_BYTES = 4 * 1024 * 1024
_MAX_PACKET_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class _DistributionMetadata:
    package_root: Path
    metadata_path: Path
    record_path: Path
    metadata: bytes
    record: bytes


def _read_bounded(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"Dependency evidence exceeds its byte budget: {path}")
    return raw


def _installed_hashmarks_metadata() -> _DistributionMetadata:
    """Capture data from the distribution that contains the actually loaded package.

    The producer interprets METADATA and RECORD. Importlib only locates installed
    artifacts; an editable or shadowed package cannot impersonate a selected wheel.
    """
    import hashmarks

    installed = distribution("hashmarks")
    package_root = Path(str(hashmarks.__file__)).resolve().parent
    if Path(str(installed.locate_file("hashmarks"))).resolve() != package_root:
        raise ValueError("Installed Hashmarks metadata does not belong to the loaded package")
    files = installed.files
    if files is None:
        raise ValueError("Installed Hashmarks distribution has no RECORD inventory")
    selected: dict[str, Path] = {}
    for name in ("METADATA", "RECORD"):
        matches = [
            row for row in files if row.name == name and row.parent.name.endswith(".dist-info")
        ]
        if len(matches) != 1:
            raise ValueError(f"Installed Hashmarks requires one {name} artifact")
        selected[name] = Path(str(installed.locate_file(matches[0]))).resolve()
    return _DistributionMetadata(
        package_root=package_root,
        metadata_path=selected["METADATA"],
        record_path=selected["RECORD"],
        metadata=_read_bounded(selected["METADATA"], _MAX_METADATA_BYTES),
        record=_read_bounded(selected["RECORD"], _MAX_METADATA_BYTES),
    )


def _source_discovery(native: CodeMap) -> dict[str, Any]:
    members = sorted(
        (
            {"path": row.rel, "visibility": row.visibility.value}
            for row in native.files.iter_files(_SOURCE_PREFIX)
            if row.path.suffix == ".py"
        ),
        key=lambda row: row["path"],
    )
    if not 1 <= len(members) <= 64:
        raise ValueError("Hashmarks dependency verification requires 1 to 64 admitted sources")
    return {
        "prefix": _SOURCE_PREFIX,
        "paths": [row["path"] for row in members],
        "members": members,
        "coverage": native.files.discovery_coverage(len(members)),
    }


def _selected_node(native: CodeMap, observation: dict[str, Any]) -> str:
    from hashmarks.operation_contract import validate_operation_response

    projection = native.dependencies.projection(
        observation, packages=[{"ecosystem": "pypi", "name": "hashmarks"}]
    )
    validate_operation_response("dependency_projection", projection)
    packages = cast(list[dict[str, Any]], projection["packages"])
    if len(packages) != 1 or packages[0]["match_count"] != 1:
        raise ValueError("Hashmarks dependency evidence requires one exact selected package")
    if packages[0]["truncation"] != "complete":
        raise ValueError("Hashmarks selected package projection is truncated")
    return str(packages[0]["matches"][0]["selection"]["node_id"])


def observe_hashmarks_dependency_change(root: Path, *, before_lock: bytes) -> dict[str, Any]:
    """Retain native facts; admission and execution remain with the caller.

    ``before_lock`` is the caller's exact base-revision capture, not current source.
    Both lock endpoints use the same native uv adapter. Installed metadata augments
    only the current endpoint used for source consumers; it does not contaminate
    the lock-only semantic delta with an artificial metadata change.
    """
    from hashmarks import CodeMap, hash_bytes
    from hashmarks.adapters.uv_dependency import uv_lock_dependency_observation
    from hashmarks.operation_contract import validate_operation_response

    if not isinstance(before_lock, bytes) or len(before_lock) > _MAX_LOCK_BYTES:
        raise ValueError("Base dependency lock requires bounded captured bytes")
    implementation = require_loaded_hashmarks_runtime()
    installed = _installed_hashmarks_metadata()
    with CodeMap(root) as native:
        native.index.sync()

        def observe(_scope: object) -> dict[str, Any]:
            admitted = native.files.visible_file(_LOCK_PATH)
            if admitted is None or admitted.visibility.value != "source":
                raise ValueError("Current dependency lock is not an admitted readable source")
            after_lock = _read_bounded(root / _LOCK_PATH, _MAX_LOCK_BYTES)
            inputs = [{"path": _LOCK_PATH, "member_revision": captured_lock_revision(after_lock)}]
            # Historical bytes retain their supplied digest. They make no claim
            # to equal the candidate's current lock member.
            before = cast(
                dict[str, Any],
                native.dependencies.observe(uv_lock_dependency_observation(lock=before_lock)),
            )
            after = cast(
                dict[str, Any],
                native.dependencies.observe(
                    uv_lock_dependency_observation(lock=after_lock, repository_inputs=inputs)
                ),
            )
            delta = native.dependencies.delta(before, after)
            selected = _selected_node(native, after)
            metadata_observation = native.dependencies.observe(
                uv_lock_dependency_observation(
                    lock=after_lock,
                    repository_inputs=inputs,
                    distribution_metadata=[
                        {
                            "node_id": selected,
                            "metadata": installed.metadata,
                            "record": installed.record,
                        }
                    ],
                )
            )
            discovery = _source_discovery(native)
            projection = native.dependencies.projection(
                metadata_observation,
                packages=[{"ecosystem": "pypi", "name": "hashmarks"}],
                source_paths=discovery["paths"],
                limit=256,
                max_depth=8,
            )
            validate_operation_response("dependency_projection", projection)
            consumer_evidence = cast(dict[str, Any], projection["consumer_evidence"])
            consumer = consumer_evidence.get("observation") or {}
            consumer_paths = sorted({row["source_path"] for row in consumer.get("consumers", [])})
            verification = [nominate_source_verification(native, path) for path in consumer_paths]
            current = native.dependencies.requalify(projection)
            validate_operation_response("dependency_projection", current)
            if current != projection or _source_discovery(native) != discovery:
                raise ValueError(
                    "Hashmarks dependency or consumer source changed during observation"
                )
            if _read_bounded(root / _LOCK_PATH, _MAX_LOCK_BYTES) != after_lock:
                raise ValueError("Dependency lock changed during observation")
            return {
                "schema": "oh-goon.hashmarks-dependency-verification/v1",
                "producer_implementation_identity": implementation,
                "lock_path": _LOCK_PATH,
                "before_source": {
                    "authority": "caller-supplied-base-lock",
                    "member_revision": captured_lock_revision(before_lock),
                },
                "before_observation": before,
                "after_observation": after,
                "delta": delta,
                "package_projection": projection,
                "source_discovery": discovery,
                "installed_distribution": {
                    "version": loaded_hashmarks_version(),
                    "package_root": str(installed.package_root),
                    "metadata_path": str(installed.metadata_path),
                    "record_path": str(installed.record_path),
                    "metadata_revision": hash_bytes(installed.metadata).hash,
                    "record_revision": hash_bytes(installed.record).hash,
                },
                "consumer_verification": verification,
                "required_checks": [
                    nomination for row in verification for nomination in row["nominations"]
                ],
                "owner_coverage": "not-claimed",
            }

        packet = native.reads.observe(observe)
    if _installed_hashmarks_metadata() != installed:
        raise ValueError("Installed Hashmarks distribution metadata changed during observation")
    require_loaded_hashmarks_runtime(expected_implementation_identity=implementation)
    if len(json.dumps(packet).encode("utf-8")) > _MAX_PACKET_BYTES:
        raise ValueError("Hashmarks dependency evidence exceeds the bounded transport")
    return packet
