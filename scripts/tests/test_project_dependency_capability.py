from __future__ import annotations

from collections.abc import Callable

import importlib.metadata
from pathlib import Path

import pytest

from oh_goon.repository_tooling import project_dependency_capability as capability

ROOT = Path(__file__).resolve().parents[2]


def _versions(**overrides: str) -> Callable[[str], str]:
    defaults = {
        "cryptography": "46.0.4",
        "fastapi": "0.128.2",
        "httpx2": "2.12",
        "hashmarks": "0.25.1",
        "pydantic": "2.13.4",
        "pyyaml": "6.0.3",
        "uvicorn": "0.48.0",
    }
    defaults.update(overrides)

    def read(name: str) -> str:
        value = defaults[name]
        if value == "MISSING":
            raise importlib.metadata.PackageNotFoundError(name)
        return value

    return read


def test_project_runtime_capability_requires_declared_runtime_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text("version = 1\n", encoding="utf-8")
    monkeypatch.setattr(importlib.metadata, "version", _versions(hashmarks="MISSING"))
    monkeypatch.setattr(capability, "_default_import_probe", lambda *_: None)
    payload = capability.build_project_capability(lock=lock)
    assert payload["result"] == "PROJECT-RUNTIME-CAPABILITIES-BLOCKED"
    assert isinstance(payload["packages"], list)
    assert isinstance(payload["blockers"], list)
    assert any(
        row["distribution"] == "hashmarks" and row["status"] == "missing"
        for row in payload["packages"]
    )
    assert (
        "hashmarks: missing from materialized project environment"
        in payload["blockers"]
    )


def test_project_runtime_capability_observes_without_redeciding_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text("version = 1\n", encoding="utf-8")
    # Deliberately below pyproject minima: native uv owns compatibility. This
    # diagnostic only observes the already-materialized runtime capabilities.
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        _versions(cryptography="1.0.0", fastapi="0.1.0", hashmarks="0.1.0"),
    )
    monkeypatch.setattr(capability, "_default_import_probe", lambda *_: None)
    payload = capability.build_project_capability(lock=lock)
    assert payload["result"] == "PROJECT-RUNTIME-CAPABILITIES-OBSERVED"
    assert payload["compatibility_decision"] == "native-uv-only"
    assert isinstance(payload["packages"], list)
    assert isinstance(payload["declared_runtime_dependencies"], list)
    assert {row["status"] for row in payload["packages"]} == {"observed"}
    assert set(payload["declared_runtime_dependencies"]) == {
        "cryptography",
        "fastapi",
        "httpx2",
        "hashmarks",
        "pydantic",
        "pyyaml",
        "uvicorn",
    }
    assert isinstance(payload["dependency_intent_sha256"], str)
    assert isinstance(payload["dependency_resolution_sha256"], str)
    assert payload["dependency_intent_sha256"].startswith("sha256:")
    assert payload["dependency_resolution_sha256"].startswith("sha256:")
    assert payload["registry_evaluated"] is False
    assert payload["uv_artifact_cache_evaluated"] is False


def test_project_runtime_capability_resolution_identity_changes_with_lock_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(importlib.metadata, "version", _versions())
    monkeypatch.setattr(capability, "_default_import_probe", lambda *_: None)
    first = tmp_path / "first.lock"
    first.write_text("version = 1\n", encoding="utf-8")
    second = tmp_path / "second.lock"
    second.write_text("version = 2\n", encoding="utf-8")
    a = capability.build_project_capability(lock=first)
    b = capability.build_project_capability(lock=second)
    assert a["dependency_resolution_sha256"] != b["dependency_resolution_sha256"]
    assert a["identity"] != b["identity"]


def test_project_python_capability_is_explicit_diagnostic_not_startup_gate() -> None:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    init = text.split("init:", 1)[1].split("\n\n", 1)[0]
    target = text.split("backend-python-capability-check:", 1)[1].split("\n\n", 1)[0]
    assert "backend-python-capability-check" not in init
    assert "backend/.venv/bin/oh-goon-project-dependency-capability" in target
    assert "backend-dependencies-check" not in target
