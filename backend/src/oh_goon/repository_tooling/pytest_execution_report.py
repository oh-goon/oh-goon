"""Atomic persistence and schema authority for bounded pytest execution reports."""
from __future__ import annotations

import json
import os
from pathlib import Path


PYTEST_BATCH_EXECUTION_REPORT_SCHEMA_FAMILY = "oh-goon.pytest-batch-execution"
PYTEST_BATCH_EXECUTION_REPORT_SCHEMA_VERSION = 1


def execution_report_schema() -> str:
    family = PYTEST_BATCH_EXECUTION_REPORT_SCHEMA_FAMILY
    version = PYTEST_BATCH_EXECUTION_REPORT_SCHEMA_VERSION
    if not isinstance(family, str) or family.strip() != family or not family:
        raise RuntimeError("pytest batch execution-report schema family owner is invalid")
    if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
        raise RuntimeError("pytest batch execution-report schema version owner is invalid")
    return f"{family}/v{version}"


def write_execution_report(path: Path, payload: dict[str, object]) -> None:
    expected_schema = execution_report_schema()
    if payload.get("schema") != expected_schema:
        raise RuntimeError("pytest batch execution report schema does not match owner")
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)
