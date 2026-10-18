"""Aggregate bounded pytest reports into non-authoritative hosted diagnostic evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from . import hosted_dependency_capability as hosted_capability_contract
from . import pytest_execution_report as pytest_execution_report_contract

SCHEMA = "oh-goon.hosted-development-diagnostic/v2"
BASE_MARKER = (
    "not certification and not slow and not process and not scale and not host_dns "
    "and not dependency_complete"
)
SURFACES = ("backend-unit", "backend-integration", "repository-tooling")
_EXTRA_MARKER = re.compile(r"(?:[A-Za-z_][A-Za-z0-9_]*|not\s+[A-Za-z_][A-Za-z0-9_]*)\Z")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--host-capability", required=True, type=Path)
    parser.add_argument("--surface", required=True)
    parser.add_argument("--first-batch", required=True, type=int)
    parser.add_argument("--batch-limit", required=True, type=int)
    parser.add_argument("--base-marker", required=True)
    parser.add_argument("--extra-marker", default="")
    parser.add_argument("--effective-marker", required=True)
    return parser.parse_args()


def _canonical_identity(payload: dict[str, Any]) -> str:
    unsigned = dict(payload)
    unsigned.pop("identity", None)
    encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _validate_host_capability_header(payload: dict[str, Any]) -> None:
    if payload.get("schema") != hosted_capability_contract.SCHEMA:
        raise RuntimeError("unsupported hosted dependency capability schema")
    if (
        payload.get("authority") != "diagnostic-only"
        or payload.get("native_authority") is not False
    ):
        raise RuntimeError("hosted dependency capability attempted to claim native authority")
    if payload.get("dependency_complete_authority") is not False:
        raise RuntimeError("hosted dependency capability attempted dependency-complete authority")
    if payload.get("uv_artifact_cache_evaluated") is not False:
        raise RuntimeError(
            "hosted dependency capability must not claim uv artifact-cache evaluation"
        )
    if payload.get("result") != "HOST-CAPABILITY-READY":
        raise RuntimeError("hosted dependency capability is not ready")
    _validate_host_capability_identity(payload)
    _validate_host_capability_runtime(payload)


def _validate_host_capability_identity(payload: dict[str, Any]) -> None:
    identity = payload.get("identity")
    if not isinstance(identity, str) or identity != _canonical_identity(payload):
        raise RuntimeError("hosted dependency capability identity does not match payload")
    intent = payload.get("dependency_intent_sha256")
    if not isinstance(intent, str) or not intent.startswith("sha256:"):
        raise RuntimeError("hosted dependency capability lacks dependency-intent identity")


def _validate_host_capability_runtime(payload: dict[str, Any]) -> None:
    interpreter = payload.get("interpreter")
    if interpreter != str(Path(sys.executable).resolve()):
        raise RuntimeError("hosted dependency capability interpreter does not match receipt writer")
    if payload.get("python_status") != "compatible":
        raise RuntimeError(
            "hosted dependency capability interpreter is outside project requires-python"
        )
    if not isinstance(payload.get("requires_python"), str):
        raise RuntimeError("hosted dependency capability lacks project requires-python")
    if not isinstance(payload.get("python_version"), str):
        raise RuntimeError("hosted dependency capability lacks interpreter version")
    required = payload.get("required_host_capabilities")
    expected = list(hosted_capability_contract.REQUIRED_HOST_CAPABILITIES)
    if required != expected:
        raise RuntimeError(
            "hosted dependency capability required capability contract does not match owner"
        )


def _dependency_name_set(payload: dict[str, Any], field: str) -> set[str]:
    value = payload.get(field)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RuntimeError(f"hosted dependency capability {field} is invalid")
    return set(value)


def _validate_host_capability_packages(payload: dict[str, Any]) -> None:
    packages = payload.get("packages")
    if not isinstance(packages, list):
        raise RuntimeError("hosted dependency capability packages are invalid")
    by_name = {
        row.get("distribution"): row
        for row in packages
        if isinstance(row, dict) and isinstance(row.get("distribution"), str)
    }
    declared = _dependency_name_set(payload, "declared_python_dependencies")
    evaluated = _dependency_name_set(payload, "evaluated_python_dependencies")
    excluded = _dependency_name_set(payload, "excluded_python_dependencies")
    if evaluated & excluded:
        raise RuntimeError("hosted dependency capability dependency partition overlaps")
    if declared != evaluated | excluded:
        raise RuntimeError("hosted dependency capability dependency partition is incomplete")
    if set(by_name) != evaluated:
        raise RuntimeError(
            "hosted dependency capability packages do not exactly cover evaluated dependencies"
        )
    for distribution in hosted_capability_contract.REQUIRED_HOST_CAPABILITIES:
        required_package = by_name.get(distribution)
        if not isinstance(required_package, dict) or required_package.get("status") != "compatible":
            raise RuntimeError(
                "hosted dependency capability has no compatible required capability proof for "
                f"{distribution}"
            )


def _load_host_capability(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    _validate_host_capability_header(payload)
    _validate_host_capability_packages(payload)
    return payload


def _effective_marker(extra_marker: str) -> str:
    if not extra_marker:
        return BASE_MARKER
    if _EXTRA_MARKER.fullmatch(extra_marker) is None:
        raise RuntimeError("hosted diagnostic extra marker is not narrowing-only")
    return f"{BASE_MARKER} and ({extra_marker})"


def _expected_manifest(args: argparse.Namespace) -> list[tuple[str, int]]:
    if args.surface == "all":
        surfaces = SURFACES
    elif args.surface in SURFACES:
        surfaces = (args.surface,)
    else:
        raise RuntimeError(f"unsupported hosted diagnostic surface: {args.surface}")
    if args.first_batch <= 0 or args.batch_limit <= 0:
        raise RuntimeError("hosted diagnostic batch selection must be positive")
    return [
        (surface, batch)
        for surface in surfaces
        for batch in range(args.first_batch, args.first_batch + args.batch_limit)
    ]


def _load_manifest(path: Path) -> list[tuple[str, int, Path]]:
    entries: list[tuple[str, int, Path]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw:
            continue
        parts = raw.split("\t")
        if len(parts) != 3:
            raise RuntimeError(f"invalid hosted diagnostic manifest line {line_number}")
        surface, batch_text, report_text = parts
        entries.append((surface, int(batch_text), Path(report_text)))
    if not entries:
        raise RuntimeError("hosted diagnostic manifest is empty")
    return entries


def _load_batch_report(surface: str, batch: int, path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != pytest_execution_report_contract.execution_report_schema():
        raise RuntimeError(f"unsupported batch report schema for {surface} batch {batch}")
    membership = payload.get("membership_receipt")
    if not isinstance(membership, dict):
        raise RuntimeError(f"missing membership receipt for {surface} batch {batch}")
    if payload.get("status") != "PASS" or membership.get("terminal_success") is not True:
        raise RuntimeError(
            f"non-passing batch cannot produce hosted success receipt: {surface} batch {batch}"
        )

    def count(name: str) -> int:
        value = membership.get(name)
        if not isinstance(value, list):
            raise RuntimeError(f"invalid membership field {name!r} for {surface} batch {batch}")
        return len(value)

    def identity(name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value.startswith("sha256:"):
            raise RuntimeError(f"missing required {name} for {surface} batch {batch}")
        return value

    selected_identity = membership.get("selected_membership_identity")
    terminal_identity = membership.get("terminal_membership_identity")
    if not isinstance(selected_identity, str) or not selected_identity.startswith("sha256:"):
        raise RuntimeError(f"missing selected membership identity for {surface} batch {batch}")
    if not isinstance(terminal_identity, str) or not terminal_identity.startswith("sha256:"):
        raise RuntimeError(f"missing terminal membership identity for {surface} batch {batch}")

    return {
        "surface": surface,
        "batch": batch,
        "status": "PASS",
        "selected": count("selected"),
        "attempted": count("attempted"),
        "completed": count("completed"),
        "passed": count("passed"),
        "failed": count("failed"),
        "non_passed": count("non_passed"),
        "resumed": count("resumed"),
        "missing": count("missing"),
        "duplicates": count("duplicates"),
        "extras": count("extras"),
        "selected_membership_identity": selected_identity,
        "terminal_membership_identity": terminal_identity,
        "source_identity": identity("source_identity"),
        "test_plan_identity": identity("test_plan_identity"),
        "toolchain_identity": identity("toolchain_identity"),
        "execution_plan_identity": identity("execution_plan_identity"),
    }


def _validate_selector(args: argparse.Namespace) -> str:
    expected_marker = _effective_marker(args.extra_marker)
    if args.base_marker != BASE_MARKER:
        raise RuntimeError("hosted diagnostic base marker does not match immutable policy")
    if args.effective_marker != expected_marker:
        raise RuntimeError("hosted diagnostic effective marker does not match narrowing policy")
    return expected_marker


def _validate_manifest(args: argparse.Namespace, manifest: list[tuple[str, int, Path]]) -> None:
    actual_manifest = [(surface, batch) for surface, batch, _ in manifest]
    if actual_manifest != _expected_manifest(args):
        raise RuntimeError("hosted diagnostic manifest does not exactly match requested selector")
    report_paths = [path.resolve() for _, _, path in manifest]
    if len(report_paths) != len(set(report_paths)):
        raise RuntimeError("hosted diagnostic manifest reuses a batch report")


def _validate_batch_coherence(batches: list[dict[str, Any]]) -> None:
    if len({batch["source_identity"] for batch in batches}) != 1:
        raise RuntimeError("hosted diagnostic batch reports do not share one source identity")
    if len({batch["toolchain_identity"] for batch in batches}) != 1:
        raise RuntimeError("hosted diagnostic batch reports do not share one toolchain identity")
    for surface in {batch["surface"] for batch in batches}:
        plans = {batch["test_plan_identity"] for batch in batches if batch["surface"] == surface}
        if len(plans) != 1:
            raise RuntimeError(
                f"hosted diagnostic {surface} batches do not share one test-plan identity"
            )
    for identity_field in ("selected_membership_identity", "execution_plan_identity"):
        identities = [batch[identity_field] for batch in batches]
        if len(identities) != len(set(identities)):
            raise RuntimeError(f"hosted diagnostic batch reports reuse {identity_field}")


def _aggregate_totals(batches: list[dict[str, Any]]) -> dict[str, int]:
    fields = (
        "selected",
        "attempted",
        "completed",
        "passed",
        "failed",
        "non_passed",
        "resumed",
        "missing",
        "duplicates",
        "extras",
    )
    totals = {name: sum(int(batch[name]) for batch in batches) for name in fields}
    if totals["selected"] <= 0:
        raise RuntimeError(
            "hosted diagnostic success receipt requires non-empty selected membership"
        )
    dirty = ("failed", "non_passed", "missing", "duplicates", "extras")
    if any(totals[name] for name in dirty):
        raise RuntimeError("hosted diagnostic success receipt requires terminally clean membership")
    if totals["selected"] != totals["completed"] or totals["completed"] != totals["passed"]:
        raise RuntimeError(
            "hosted diagnostic success receipt requires selected == completed == passed"
        )
    return totals


def build_receipt(args: argparse.Namespace) -> dict[str, Any]:
    expected_marker = _validate_selector(args)
    host_capability = _load_host_capability(args.host_capability)
    manifest = _load_manifest(args.manifest)
    _validate_manifest(args, manifest)
    batches = [_load_batch_report(surface, batch, path) for surface, batch, path in manifest]
    _validate_batch_coherence(batches)
    totals = _aggregate_totals(batches)
    return {
        "schema": SCHEMA,
        "authority": "diagnostic-only",
        "native_authority": False,
        "certification_authority": False,
        "promotion_authority": False,
        "result": "HOSTED-DIAGNOSTIC-PASSED",
        "host_capability": host_capability,
        "selector": {
            "surface": args.surface,
            "first_batch": args.first_batch,
            "batch_limit": args.batch_limit,
            "base_marker": BASE_MARKER,
            "extra_marker": args.extra_marker or None,
            "effective_marker": expected_marker,
        },
        "totals": totals,
        "batches": batches,
    }


def write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    args = _parse_args()
    if args.output.is_dir() and not args.output.is_symlink():
        raise RuntimeError(f"hosted diagnostic receipt output is a directory: {args.output}")
    try:
        args.output.unlink()
    except FileNotFoundError:
        pass
    write_atomic(args.output, build_receipt(args))
    print(f"Hosted diagnostic receipt: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
