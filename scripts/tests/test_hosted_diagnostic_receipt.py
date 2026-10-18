from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from oh_goon.repository_tooling import hosted_diagnostic_receipt as receipt_module


ROOT = Path(__file__).resolve().parents[2]
DIAGNOSTIC = ROOT / "scripts/backend_core_test_diagnostic.sh"
MAKEFILE = ROOT / "Makefile"


def _host_capability(path: Path, *, interpreter: str | None = None) -> None:
    payload = _host_capability_payload(interpreter=interpreter)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _host_capability_payload(*, interpreter: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "oh-goon.hosted-dependency-capability/v2",
        "authority": "diagnostic-only",
        "native_authority": False,
        "dependency_complete_authority": False,
        "uv_artifact_cache_evaluated": False,
        "result": "HOST-CAPABILITY-READY",
        "interpreter": interpreter or str(Path(sys.executable).resolve()),
        "python_version": "3.13.5",
        "requires_python": ">=3.12,<3.15",
        "python_status": "compatible",
        "dependency_intent_sha256": "sha256:intent",
        "required_host_capabilities": ["cryptography", "pytest"],
        "declared_python_dependencies": ["cryptography", "pytest", "hashmarks"],
        "evaluated_python_dependencies": ["cryptography", "pytest"],
        "excluded_python_dependencies": ["hashmarks"],
        "packages": [
            {
                "distribution": "cryptography",
                "import": "cryptography",
                "requirement": "cryptography>=46.0.4",
                "observed_version": "46.0.4",
                "status": "compatible",
            },
            {
                "distribution": "pytest",
                "import": "pytest",
                "requirement": "pytest>=9.0.2",
                "observed_version": "9.0.2",
                "status": "compatible",
            },
        ],
        "blockers": [],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["identity"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    return payload


def _resign(payload: dict[str, object]) -> None:
    unsigned = dict(payload)
    unsigned.pop("identity", None)
    encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["identity"] = "sha256:" + hashlib.sha256(encoded).hexdigest()


def _batch_report(
    path: Path,
    *,
    status: str = "PASS",
    terminal_success: bool = True,
    members: list[str] | None = None,
    omit_identity: str | None = None,
    source_identity: str = "sha256:source",
    test_plan_identity: str = "sha256:plan",
    toolchain_identity: str = "sha256:tools",
    execution_plan_identity: str = "sha256:execution",
    selected_membership_identity: str = "sha256:selected",
) -> None:
    members = ["a.py", "b.py"] if members is None else members
    payload = {
        "schema": "oh-goon.pytest-batch-execution/v1",
        "status": status,
        "source_identity": source_identity,
        "test_plan_identity": test_plan_identity,
        "toolchain_identity": toolchain_identity,
        "execution_plan_identity": execution_plan_identity,
        "membership_receipt": {
            "selected": members,
            "attempted": members,
            "completed": members,
            "passed": members if terminal_success else ["a.py"],
            "failed": [] if terminal_success else ["b.py"],
            "non_passed": [] if terminal_success else ["b.py"],
            "resumed": [],
            "missing": [],
            "duplicates": [],
            "extras": [],
            "selected_membership_identity": selected_membership_identity,
            "terminal_membership_identity": "sha256:terminal",
            "terminal_success": terminal_success,
        },
    }
    if omit_identity is not None:
        if omit_identity in payload:
            payload.pop(omit_identity)
        else:
            payload["membership_receipt"].pop(omit_identity, None)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _invoke_writer(
    tmp_path: Path,
    manifest_text: str,
    *,
    surface: str = "backend-unit",
    first_batch: int = 1,
    batch_limit: int = 1,
    base_marker: str = "not certification and not slow and not process and not scale and not host_dns and not dependency_complete",
    extra_marker: str = "",
    effective_marker: str = "not certification and not slow and not process and not scale and not host_dns and not dependency_complete",
    stale_output: bool = False,
    capability_interpreter: str | None = None,
    tamper_capability_identity: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text(manifest_text, encoding="utf-8")
    capability = tmp_path / "host-capability.json"
    _host_capability(capability, interpreter=capability_interpreter)
    if tamper_capability_identity:
        payload = json.loads(capability.read_text(encoding="utf-8"))
        payload["packages"][0]["observed_version"] = "99.0.0"
        capability.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "receipt.json"
    if stale_output:
        output.write_text('{"result":"HOSTED-DIAGNOSTIC-PASSED"}\n', encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "backend/src") + os.pathsep + env.get("PYTHONPATH", "")
    command = [
        sys.executable,
        "-m",
        "oh_goon.repository_tooling.hosted_diagnostic_receipt",
        "--output",
        str(output),
        "--manifest",
        str(manifest),
        "--host-capability",
        str(capability),
        "--surface",
        surface,
        "--first-batch",
        str(first_batch),
        "--batch-limit",
        str(batch_limit),
        "--base-marker",
        base_marker,
        "--effective-marker",
        effective_marker,
    ]
    if extra_marker:
        command.extend(("--extra-marker", extra_marker))
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    return result, output


def _run_writer(tmp_path: Path, report: Path) -> tuple[subprocess.CompletedProcess[str], Path]:
    return _invoke_writer(tmp_path, f"backend-unit\t1\t{report}\n")


def test_hosted_receipt_is_explicitly_non_authoritative_and_membership_complete(tmp_path: Path) -> None:
    report = tmp_path / "batch.json"
    _batch_report(report)
    result, output = _run_writer(tmp_path, report)
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema"] == "oh-goon.hosted-development-diagnostic/v2"
    assert payload["authority"] == "diagnostic-only"
    assert payload["native_authority"] is False
    assert payload["certification_authority"] is False
    assert payload["promotion_authority"] is False
    assert payload["result"] == "HOSTED-DIAGNOSTIC-PASSED"
    assert payload["host_capability"]["packages"][0]["distribution"] == "cryptography"
    assert payload["host_capability"]["packages"][0]["status"] == "compatible"
    assert payload["totals"] == {
        "selected": 2,
        "attempted": 2,
        "completed": 2,
        "passed": 2,
        "failed": 0,
        "non_passed": 0,
        "resumed": 0,
        "missing": 0,
        "duplicates": 0,
        "extras": 0,
    }


def test_build_receipt_directly_binds_crypto_capability_to_batch_evidence(tmp_path: Path) -> None:
    report = tmp_path / "batch.json"
    _batch_report(report)
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text(f"backend-unit\t1\t{report}\n", encoding="utf-8")
    capability = tmp_path / "host-capability.json"
    _host_capability(capability)
    args = receipt_module.argparse.Namespace(
        output=tmp_path / "receipt.json",
        manifest=manifest,
        host_capability=capability,
        surface="backend-unit",
        first_batch=1,
        batch_limit=1,
        base_marker=receipt_module.BASE_MARKER,
        extra_marker="",
        effective_marker=receipt_module.BASE_MARKER,
    )
    payload = receipt_module.build_receipt(args)
    assert payload["schema"] == "oh-goon.hosted-development-diagnostic/v2"
    assert payload["host_capability"]["packages"][0]["distribution"] == "cryptography"
    assert payload["host_capability"]["packages"][0]["status"] == "compatible"
    assert payload["totals"]["selected"] == payload["totals"]["passed"] == 2


def test_hosted_receipt_rejects_tampered_host_capability_identity(tmp_path: Path) -> None:
    report = tmp_path / "batch.json"
    _batch_report(report)
    result, output = _invoke_writer(
        tmp_path,
        f"backend-unit\t1\t{report}\n",
        tamper_capability_identity=True,
    )
    assert result.returncode != 0
    assert not output.exists()
    assert "capability identity does not match payload" in result.stderr


def test_hosted_receipt_rejects_capability_from_different_interpreter(tmp_path: Path) -> None:
    report = tmp_path / "batch.json"
    _batch_report(report)
    result, output = _invoke_writer(
        tmp_path,
        f"backend-unit\t1\t{report}\n",
        capability_interpreter="/different/python",
    )
    assert result.returncode != 0
    assert not output.exists()
    assert "interpreter does not match receipt writer" in result.stderr


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("native_authority", True, "attempted to claim native authority"),
        ("dependency_complete_authority", True, "attempted dependency-complete authority"),
        ("uv_artifact_cache_evaluated", True, "must not claim uv artifact-cache evaluation"),
        ("python_status", "incompatible", "outside project requires-python"),
    ),
)
def test_host_capability_validator_rejects_authority_or_runtime_promotion(
    field: str,
    value: object,
    message: str,
) -> None:
    payload = _host_capability_payload()
    payload[field] = value
    _resign(payload)
    with pytest.raises(RuntimeError, match=message):
        receipt_module._validate_host_capability_header(payload)


def test_host_capability_validator_accepts_equivalent_native_only_partition_identity() -> None:
    payload = _host_capability_payload()
    payload["declared_python_dependencies"] = ["cryptography", "pytest", "native-observer"]
    payload["excluded_python_dependencies"] = ["native-observer"]
    _resign(payload)
    receipt_module._validate_host_capability_packages(payload)


def test_host_capability_validator_rejects_missing_declared_dependency_partition() -> None:
    payload = _host_capability_payload()
    payload["excluded_python_dependencies"] = []
    _resign(payload)
    with pytest.raises(RuntimeError, match="dependency partition is incomplete"):
        receipt_module._validate_host_capability_packages(payload)


def test_host_capability_validator_rejects_overlapping_or_incomplete_partition() -> None:
    payload = _host_capability_payload()
    payload["evaluated_python_dependencies"] = ["cryptography", "pytest", "hashmarks"]
    _resign(payload)
    with pytest.raises(RuntimeError, match="dependency partition overlaps"):
        receipt_module._validate_host_capability_packages(payload)

    payload = _host_capability_payload()
    payload["declared_python_dependencies"].append("future-lib")
    _resign(payload)
    with pytest.raises(RuntimeError, match="dependency partition is incomplete"):
        receipt_module._validate_host_capability_packages(payload)


def test_host_capability_validator_requires_packages_to_match_evaluated_partition() -> None:
    payload = _host_capability_payload()
    payload["packages"] = payload["packages"][:-1]
    _resign(payload)
    with pytest.raises(RuntimeError, match="packages do not exactly cover evaluated dependencies"):
        receipt_module._validate_host_capability_packages(payload)


def test_host_capability_validator_requires_compatible_cryptography_package() -> None:
    payload = _host_capability_payload()
    packages = payload["packages"]
    assert isinstance(packages, list)
    crypto = packages[0]
    assert isinstance(crypto, dict)
    crypto["status"] = "missing"
    _resign(payload)
    receipt_module._validate_host_capability_header(payload)
    with pytest.raises(RuntimeError, match="no compatible required capability proof for cryptography"):
        receipt_module._validate_host_capability_packages(payload)


def test_hosted_receipt_refuses_nonpassing_batch_evidence(tmp_path: Path) -> None:
    report = tmp_path / "batch.json"
    _batch_report(report, status="FAIL", terminal_success=False)
    result, output = _run_writer(tmp_path, report)
    assert result.returncode != 0
    assert not output.exists()
    assert "non-passing batch cannot produce hosted success receipt" in result.stderr


def test_hosted_receipt_refuses_selector_policy_drift_and_invalidates_stale_output(tmp_path: Path) -> None:
    report = tmp_path / "batch.json"
    _batch_report(report)
    result, output = _invoke_writer(
        tmp_path,
        f"backend-unit\t1\t{report}\n",
        base_marker="not certification",
        stale_output=True,
    )
    assert result.returncode != 0
    assert not output.exists()
    assert "base marker does not match immutable policy" in result.stderr


def test_hosted_receipt_refuses_unsafe_or_mismatched_effective_marker(tmp_path: Path) -> None:
    report = tmp_path / "batch.json"
    _batch_report(report)
    result, output = _invoke_writer(
        tmp_path,
        f"backend-unit\t1\t{report}\n",
        extra_marker="unit or certification",
        effective_marker="not certification and (unit or certification)",
    )
    assert result.returncode != 0
    assert not output.exists()
    assert "extra marker is not narrowing-only" in result.stderr


def test_hosted_receipt_requires_exact_all_surface_manifest(tmp_path: Path) -> None:
    report = tmp_path / "backend-unit.json"
    _batch_report(report)
    result, output = _invoke_writer(
        tmp_path,
        f"backend-unit\t1\t{report}\n",
        surface="all",
    )
    assert result.returncode != 0
    assert not output.exists()
    assert "manifest does not exactly match requested selector" in result.stderr


def test_hosted_receipt_accepts_exact_all_surface_manifest(tmp_path: Path) -> None:
    rows = []
    for index, surface in enumerate(("backend-unit", "backend-integration", "repository-tooling"), 1):
        report = tmp_path / f"{surface}.json"
        _batch_report(
            report,
            execution_plan_identity=f"sha256:execution-{index}",
            selected_membership_identity=f"sha256:selected-{index}",
        )
        rows.append(f"{surface}\t1\t{report}")
    result, output = _invoke_writer(tmp_path, "\n".join(rows) + "\n", surface="all")
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert [(item["surface"], item["batch"]) for item in payload["batches"]] == [
        ("backend-unit", 1),
        ("backend-integration", 1),
        ("repository-tooling", 1),
    ]
    assert payload["totals"]["selected"] == 6


def test_hosted_receipt_refuses_reused_batch_report(tmp_path: Path) -> None:
    report = tmp_path / "batch.json"
    _batch_report(report)
    manifest = f"backend-unit\t1\t{report}\nbackend-unit\t2\t{report}\n"
    result, output = _invoke_writer(tmp_path, manifest, batch_limit=2)
    assert result.returncode != 0
    assert not output.exists()
    assert "manifest reuses a batch report" in result.stderr


def test_hosted_receipt_refuses_mixed_source_identity(tmp_path: Path) -> None:
    first = tmp_path / "batch-1.json"
    second = tmp_path / "batch-2.json"
    _batch_report(
        first,
        execution_plan_identity="sha256:execution-1",
        selected_membership_identity="sha256:selected-1",
    )
    _batch_report(
        second,
        source_identity="sha256:other-source",
        execution_plan_identity="sha256:execution-2",
        selected_membership_identity="sha256:selected-2",
    )
    manifest = f"backend-unit\t1\t{first}\nbackend-unit\t2\t{second}\n"
    result, output = _invoke_writer(tmp_path, manifest, batch_limit=2)
    assert result.returncode != 0
    assert not output.exists()
    assert "do not share one source identity" in result.stderr


def test_hosted_receipt_refuses_reused_execution_identity_across_distinct_reports(tmp_path: Path) -> None:
    first = tmp_path / "batch-1.json"
    second = tmp_path / "batch-2.json"
    _batch_report(first, selected_membership_identity="sha256:selected-1")
    _batch_report(second, selected_membership_identity="sha256:selected-2")
    manifest = f"backend-unit\t1\t{first}\nbackend-unit\t2\t{second}\n"
    result, output = _invoke_writer(tmp_path, manifest, batch_limit=2)
    assert result.returncode != 0
    assert not output.exists()
    assert "reuse execution_plan_identity" in result.stderr


def test_hosted_receipt_refuses_empty_membership(tmp_path: Path) -> None:
    report = tmp_path / "batch.json"
    _batch_report(report, members=[])
    result, output = _run_writer(tmp_path, report)
    assert result.returncode != 0
    assert not output.exists()
    assert "requires non-empty selected membership" in result.stderr


def test_hosted_receipt_requires_batch_provenance_identities(tmp_path: Path) -> None:
    report = tmp_path / "batch.json"
    _batch_report(report, omit_identity="execution_plan_identity")
    result, output = _run_writer(tmp_path, report)
    assert result.returncode != 0
    assert not output.exists()
    assert "missing required execution_plan_identity" in result.stderr


def test_blocked_diagnostic_invalidates_previous_success_receipt(tmp_path: Path) -> None:
    receipt = tmp_path / "old-success.json"
    receipt.write_text('{"result":"HOSTED-DIAGNOSTIC-PASSED"}\n', encoding="utf-8")
    env = os.environ.copy()
    env["PYTHON"] = "oh-goon-python-does-not-exist"
    env["OH_GOON_HOSTED_DIAGNOSTIC_RECEIPT"] = str(receipt)
    status_file = tmp_path / "diagnostic-exit.status"
    env["OH_GOON_HOSTED_DIAGNOSTIC_STATUS"] = str(status_file)
    result = subprocess.run(("bash", str(DIAGNOSTIC)), cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 3
    assert status_file.read_text(encoding="utf-8") == "3\n"
    assert not receipt.exists()
    assert "HOSTED DEVELOPMENT DIAGNOSTIC BLOCKED" in result.stderr


def test_make_target_binds_hosted_receipt_to_test_run_log_namespace() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    assert 'OH_GOON_HOSTED_DIAGNOSTIC_RECEIPT="$(RUN_LOG_DIR)/test/hosted-diagnostic-receipt.json"' in text
