from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from oh_goon.core.file_digest import sha256_file_with_size, update_digest_from_file
from oh_goon.repository_tooling.release.package_slim import (
    archive_source_identity,
    canonical_source_files,
    canonical_source_identity,
    package_slim,
)
from oh_goon.repository_tooling.self_certification_artifacts import (
    build_artifact_manifest,
    build_artifact_sidecars,
    build_cumulative_patch,
    build_handoff,
    cumulative_patch_path,
    load_cumulative_patch_evidence,
    load_fresh_zip_evidence,
    load_handoff_evidence,
    load_manifest_evidence,
    load_parent_source_input,
    load_patch_replay_evidence,
    load_persist_evidence,
    load_retained_readback_evidence,
    load_sidecar_evidence,
    manifest_path,
    parent_checkpoint_spec,
    parent_input_path,
    persist_artifact_set,
    retained_artifact_directory,
    stage_parent_source_zip,
    verify_cumulative_patch_replay,
    verify_fresh_source_zip,
    verify_retained_artifact_set,
)

PLAN_NAME = "builtin:self-verification-v4"
SCRIPTS_SURFACE = "scripts-non-certification"
SHARD_STRATEGY = "deterministic-contiguous-v1"
MAX_TESTS_PER_SHARD = 16
SCRIPTS_BUDGET_SECONDS = 240
STATIC_GOON_ADMISSION_UNIT = "goon-static-admission"
LIFECYCLE_ADVERSARIAL_UNIT = "certification-lifecycle-adversarial"
LIFECYCLE_PRODUCTION_UNIT = "certification-lifecycle-production"
LIFECYCLE_RECONSTRUCTION_UNIT = "certification-lifecycle-reconstruction"
LIFECYCLE_INTEGRATION_FILES = (
    "backend/tests/integration/services/test_local_vertical_lifecycle.py",
    "backend/tests/integration/services/test_failure_recovery_resume_closure.py",
)

INHERITED_CLOSURE_CONTRACTS: dict[str, tuple[str, ...]] = {
    "initialized-repository-authority": (
        "canonical-init-single-materialization",
        "verification-consumes-initialized-repository",
        "no-certification-dependency-reacquisition",
    ),
    "bootstrap-authority": (
        "native-uv-bootstrap-authority",
        "init-doctor-diagnostic-only",
    ),
}

def _source_gate_dependencies(source_units: list[dict[str, object]]) -> dict[str, tuple[str, ...]]:
    script_units = tuple(
        str(unit["unit"]) for unit in source_units if unit.get("surface") == SCRIPTS_SURFACE
    )
    if not script_units:
        raise ValueError("self-certification source gates require at least one scripts shard")
    return {
        "admission": (STATIC_GOON_ADMISSION_UNIT,),
        "integration": ("integration",),
        "release": script_units,
        "architecture": ("architecture",),
        "QA": ("generated", "repository-understanding", *script_units),
        "compile": ("compile",),
        "shell": ("shell",),
        "scripts": script_units,
        "certification-realism": (
            LIFECYCLE_ADVERSARIAL_UNIT,
            LIFECYCLE_PRODUCTION_UNIT,
            LIFECYCLE_RECONSTRUCTION_UNIT,
        ),
    }


def _sha256_json(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _hash_files(root: Path, relative_paths: Iterable[str]) -> str:
    hasher = hashlib.sha256()
    for relative in sorted(relative_paths):
        path = root / relative
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\0")
        if path.is_file():
            update_digest_from_file(hasher, path)
        else:
            hasher.update(b"<missing>")
        hasher.update(b"\0")
    return "sha256:" + hasher.hexdigest()


@dataclass(frozen=True)
class Shard:
    index: int
    total: int
    members: tuple[str, ...]
    surface_identity: str

    @property
    def shard_id(self) -> str:
        return f"{self.index}/{self.total}"

    @property
    def unit_name(self) -> str:
        return f"scripts/{self.index}"

    @property
    def command(self) -> list[str]:
        return [
            "backend/.venv/bin/python",
            "-m",
            "pytest",
            "-c",
            "backend/pyproject.toml",
            "-m",
            "not certification",
            *self.members,
        ]

    @property
    def execution_unit_id(self) -> str:
        return _sha256_json(
            {
                "surface": SCRIPTS_SURFACE,
                "surface_identity": self.surface_identity,
                "strategy": SHARD_STRATEGY,
                "shard": self.shard_id,
                "members": self.members,
                "marker_expression": "not certification",
                "execution_budget_seconds": SCRIPTS_BUDGET_SECONDS,
                "command": self.command,
            }
        )

    def as_payload(self) -> dict[str, object]:
        return {
            "id": self.shard_id,
            "unit": self.unit_name,
            "test_count": len(self.members),
            "members": list(self.members),
            "execution_unit_id": self.execution_unit_id,
            "execution_budget_seconds": SCRIPTS_BUDGET_SECONDS,
            "command": self.command,
        }


def scripts_non_certification_shards(root: Path) -> tuple[Shard, ...]:
    tests_root = root / "scripts" / "tests"
    members = tuple(
        path.relative_to(root).as_posix()
        for path in sorted(tests_root.glob("test_*.py"), key=lambda item: item.as_posix())
    )
    if not members:
        raise ValueError(f"{SCRIPTS_SURFACE} discovered no test files")
    surface_identity = _sha256_json(
        {
            "surface": SCRIPTS_SURFACE,
            "strategy": SHARD_STRATEGY,
            "members": members,
            "marker_expression": "not certification",
            "max_tests_per_shard": MAX_TESTS_PER_SHARD,
        }
    )
    count = max(1, math.ceil(len(members) / MAX_TESTS_PER_SHARD))
    base, extra = divmod(len(members), count)
    shards: list[Shard] = []
    start = 0
    for offset in range(count):
        size = base + (1 if offset < extra else 0)
        chunk = members[start : start + size]
        start += size
        shards.append(Shard(offset + 1, count, chunk, surface_identity))
    assigned = [member for shard in shards for member in shard.members]
    if assigned != list(members) or len(set(assigned)) != len(members):
        raise RuntimeError("invalid deterministic shard plan")
    return tuple(shards)


def shard_plan(root: Path) -> dict[str, object]:
    shards = scripts_non_certification_shards(root)
    members = [member for shard in shards for member in shard.members]
    return {
        "surface": SCRIPTS_SURFACE,
        "surface_identity": shards[0].surface_identity,
        "strategy": SHARD_STRATEGY,
        "test_count": len(members),
        "assigned": len(members),
        "duplicates": len(members) - len(set(members)),
        "missing": 0,
        "marker_expression": "not certification",
        "shards": [shard.as_payload() for shard in shards],
    }


def _lifecycle_certification_unit_specs() -> tuple[dict[str, object], ...]:
    pytest = ["backend/.venv/bin/python", "-m", "pytest", "-c", "backend/pyproject.toml"]
    return (
        {
            "unit": LIFECYCLE_ADVERSARIAL_UNIT,
            "surface": "certification-lifecycle-adversarial",
            "strategy": "bounded",
            "evidence_level": "L2_ADVERSARIAL",
            "execution_budget_seconds": 120,
            "command": [
                *pytest,
                "backend/tests/unit/runtime/test_contained_effect_recovery.py",
                "backend/tests/unit/runtime/test_recovery_reconciliation.py",
            ],
        },
        {
            "unit": LIFECYCLE_PRODUCTION_UNIT,
            "surface": "certification-lifecycle-production",
            "strategy": "bounded",
            "evidence_level": "L3_LIFECYCLE",
            "execution_budget_seconds": 120,
            "command": [
                *pytest,
                LIFECYCLE_INTEGRATION_FILES[0],
                "-k",
                "not reconstructs_from_fresh_durable_owners and not process_restart_recovery",
            ],
        },
        {
            "unit": LIFECYCLE_RECONSTRUCTION_UNIT,
            "surface": "certification-lifecycle-reconstruction",
            "strategy": "bounded",
            "evidence_level": "L4_RECONSTRUCTION",
            "execution_budget_seconds": 120,
            "command": [
                *pytest,
                *LIFECYCLE_INTEGRATION_FILES,
                "-k",
                "reconstructs_from_fresh_durable_owners or process_restart_recovery or failure_recovery_resume_closure",
            ],
        },
    )


def _direct_unit_specs() -> tuple[dict[str, object], ...]:
    return (
        {
            "unit": STATIC_GOON_ADMISSION_UNIT,
            "surface": "canonical-goon-static-admission",
            "strategy": "bounded",
            "execution_budget_seconds": 30,
            "command": ["backend/.venv/bin/oh-goon-goon-compile"],
            "requires": [],
        },
        {
            "unit": "integration",
            "surface": "integration",
            "strategy": "bounded",
            "execution_budget_seconds": 240,
            "command": [
                "backend/.venv/bin/python", "-m", "pytest", "-c", "backend/pyproject.toml",
                "backend/tests/integration", "tests/integration",
                "--ignore", LIFECYCLE_INTEGRATION_FILES[0],
                "--ignore", LIFECYCLE_INTEGRATION_FILES[1],
            ],
        },
        *_lifecycle_certification_unit_specs(),
        {
            "unit": "architecture",
            "surface": "architecture",
            "strategy": "bounded",
            "execution_budget_seconds": 120,
            "command": ["backend/.venv/bin/oh-goon-architecture"],
        },
        {
            "unit": "generated",
            "surface": "generated-contracts",
            "strategy": "bounded",
            "execution_budget_seconds": 120,
            "command": ["backend/.venv/bin/oh-goon-check-generated"],
        },
        {
            "unit": "repository-understanding",
            "surface": "repository-understanding",
            "strategy": "bounded",
            "execution_budget_seconds": 120,
            "command": ["backend/.venv/bin/oh-goon-repository-understanding-exam", "verify"],
        },
        {
            "unit": "compile",
            "surface": "python-compile",
            "strategy": "bounded",
            "execution_budget_seconds": 120,
            "command": [
                "backend/.venv/bin/python", "-m", "compileall", "-q",
                "backend/src", "backend/tests", "scripts", "tests",
            ],
        },
        {
            "unit": "shell",
            "surface": "shell-syntax",
            "strategy": "bounded",
            "execution_budget_seconds": 120,
            "command": ["backend/.venv/bin/oh-goon-shell-syntax"],
        },
    )



def _executable_identity(root: Path, command: list[str]) -> dict[str, object]:
    executable = command[0] if command else ""
    candidate = (root / executable) if executable and not Path(executable).is_absolute() else Path(executable)
    if not executable or not candidate.is_file():
        return {"executable": executable, "state": "UNAVAILABLE"}
    try:
        sha256, byte_count = sha256_file_with_size(candidate)
        mode = candidate.stat().st_mode & 0o777
    except OSError:
        return {"executable": executable, "state": "UNAVAILABLE"}
    return {
        "executable": executable,
        "state": "PRESENT",
        "sha256": sha256,
        "byte_count": byte_count,
        "mode": mode,
    }


def _toolchain_identity(root: Path, unit: dict[str, object]) -> str:
    return _sha256_json(_executable_identity(root, list(unit["command"])))

def _source_units(root: Path) -> list[dict[str, object]]:
    units: list[dict[str, object]] = []
    for spec in _direct_unit_specs():
        unit = dict(spec)
        unit["phase"] = "source"
        unit.setdefault("evidence_level", "L1_LOGIC")
        if str(unit["unit"]) != STATIC_GOON_ADMISSION_UNIT:
            unit.setdefault("requires", [STATIC_GOON_ADMISSION_UNIT])
        unit["execution_unit_id"] = _sha256_json(spec)
        unit["toolchain_identity"] = _toolchain_identity(root, unit)
        units.append(unit)
    for shard in scripts_non_certification_shards(root):
        unit = {
            "unit": shard.unit_name,
            "phase": "source",
            "surface": SCRIPTS_SURFACE,
            "strategy": "bounded",
            "requires": [STATIC_GOON_ADMISSION_UNIT],
            "execution_unit_id": shard.execution_unit_id,
            "execution_budget_seconds": SCRIPTS_BUDGET_SECONDS,
            "command": shard.command,
            "evidence_level": "L1_LOGIC",
        }
        unit["toolchain_identity"] = _toolchain_identity(root, unit)
        units.append(unit)
    return units


def _verification_config_identity(root: Path, source_units: list[dict[str, object]]) -> str:
    verification_inputs = _hash_files(
        root,
        (
            "backend/pyproject.toml",
            "backend/uv.lock",
            "frontend/package-lock.json",
        ),
    )
    return _sha256_json(
        {
            "plan": PLAN_NAME,
            "verification_inputs_identity": verification_inputs,
            "source_gate_dependencies": _source_gate_dependencies(source_units),
            "inherited_closure_contracts": INHERITED_CLOSURE_CONTRACTS,
            "units": [
                {
                    "unit": unit["unit"],
                    "surface": unit["surface"],
                    "execution_unit_id": unit["execution_unit_id"],
                    "execution_budget_seconds": unit["execution_budget_seconds"],
                    "command": unit["command"],
                    "toolchain_identity": unit["toolchain_identity"],
                    "evidence_level": unit["evidence_level"],
                }
                for unit in source_units
            ],
        }
    )
