from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture._structural_authority_execution import (
    _check_hosted_diagnostic_contract,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/backend_core_test_diagnostic.sh"
HOST_PYTHON_SELECTOR = ROOT / "scripts/host_python.sh"


def test_hosted_diagnostic_selector_delegates_required_capabilities_to_semantic_owner() -> (
    None
):
    text = SCRIPT.read_text(encoding="utf-8")
    selector = HOST_PYTHON_SELECTOR.read_text(encoding="utf-8")
    assert 'source "$ROOT/scripts/host_python.sh"' in text
    assert 'PYTHON_BIN="$(select_host_python)"' in text
    assert "hosted_dependency_capability.py" in selector
    assert "--probe-required-host-capabilities" in selector
    assert "uv sync" not in selector
    assert "uv lock" not in selector
    assert "pip install" not in selector


def test_explicit_host_python_override_remains_authoritative(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("host interpreter selector is POSIX shell tooling")
    explicit = tmp_path / "explicit-python"
    env = os.environ.copy()
    env["PYTHON"] = str(explicit)
    result = subprocess.run(
        ("bash", "-c", f'source "{HOST_PYTHON_SELECTOR}"; select_host_python'),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == str(explicit)


def test_host_python_selector_prefers_capable_python_over_incapable_python3(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("host interpreter selector is POSIX shell tooling")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capable = bin_dir / "python"
    incapable = bin_dir / "python3"
    capable.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    incapable.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    capable.chmod(0o755)
    incapable.chmod(0o755)
    env = os.environ.copy()
    env.pop("PYTHON", None)
    env["PATH"] = f"{bin_dir}{os.pathsep}/usr/bin:/bin"
    result = subprocess.run(
        (
            "bash",
            "-c",
            f'source "{HOST_PYTHON_SELECTOR}"; select_host_python',
        ),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == str(capable)


def test_host_python_selector_accepts_candidate_only_through_semantic_capability_probe(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("host interpreter selector is POSIX shell tooling")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    candidate = bin_dir / "python"
    fallback = bin_dir / "python3"
    candidate.write_text(
        "#!/usr/bin/env bash\n"
        'case "$*" in\n'
        "  *hosted_dependency_capability.py*--probe-required-host-capabilities*) exit 0 ;;\n"
        "  *) exit 41 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fallback.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    candidate.chmod(0o755)
    fallback.chmod(0o755)
    env = os.environ.copy()
    env.pop("PYTHON", None)
    env["PATH"] = f"{bin_dir}{os.pathsep}/usr/bin:/bin"
    result = subprocess.run(
        ("bash", "-c", f'source "{HOST_PYTHON_SELECTOR}"; select_host_python'),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == str(candidate)


def test_predependency_diagnostic_does_not_privately_materialize_hashmarks() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "RETAINED_HASHMARKS" not in text
    assert "HASHMARKS_SITE" not in text
    assert "zipfile -e" not in text
    assert "--exclude-dependency" not in text
    assert 'native-only-dependencies = ["hashmarks"]' in (
        ROOT / "backend/pyproject.toml"
    ).read_text(encoding="utf-8")
    assert 'export PYTHONPATH="$ROOT/backend/src"' in text
    assert "${PYTHONPATH:+" not in text
    assert "trap cleanup_diagnostic_and_report_status EXIT" in text
    assert 'rm -rf -- "$DIAGNOSTIC_TEMP"' in text
    assert "local status=$?" in text


def test_hosted_diagnostic_disables_ambient_pytest_plugin_autoload() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1" in text
    assert "telemetry plugins" in text


def test_hosted_diagnostic_defaults_to_all_dependency_light_surfaces() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'SURFACE="${DIAGNOSTIC_SURFACE:-all}"' in text
    assert "surfaces=(backend-unit backend-integration repository-tooling)" in text
    assert 'BATCH_LIMIT="${DIAGNOSTIC_BATCH_LIMIT:-1}"' in text


def test_hosted_diagnostic_never_installs_or_resolves_dependencies() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "pip install" not in text
    assert "uv sync" not in text
    assert "uv lock" not in text
    assert "npm install" not in text
    assert "npm ci" not in text
    assert "never resolve/install/mutate project dependency state" in text


def test_hosted_diagnostic_validates_host_dependencies_before_running_batches() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    capability_call = "hosted_dependency_capability.py"
    batch_call = "-m oh_goon.repository_tooling.test_batches"
    assert capability_call in text
    assert '--host-capability "$CAPABILITY_PATH"' in text
    assert text.index(capability_call) < text.index(batch_call)
    assert "PYTHONNOUSERSITE=1" in text
    assert '"$PYTHON_BIN" -I ' in text


def test_hosted_diagnostic_cannot_certify_one_crypto_then_import_an_ambient_pythonpath_copy() -> (
    None
):
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'export PYTHONPATH="$ROOT/backend/src"' in text
    assert "${PYTHONPATH:+" not in text
    assert '"$PYTHON_BIN" -P -m oh_goon.repository_tooling.test_batches' in text
    assert (
        '"$PYTHON_BIN" -P -m oh_goon.repository_tooling.hosted_diagnostic_receipt'
        in text
    )
    assert "-c 'import pytest'" not in text


def test_architecture_guard_rejects_ambient_pythonpath_without_freezing_crypto_implementation(
    tmp_path: Path,
) -> None:
    script = tmp_path / "scripts/backend_core_test_diagnostic.sh"
    capability = (
        tmp_path
        / "backend/src/oh_goon/repository_tooling/hosted_dependency_capability.py"
    )
    script.parent.mkdir(parents=True)
    capability.parent.mkdir(parents=True)
    script.write_bytes(SCRIPT.read_bytes())
    (script.parent / "host_python.sh").write_bytes(HOST_PYTHON_SELECTOR.read_bytes())
    capability.write_bytes(
        (
            ROOT
            / "backend/src/oh_goon/repository_tooling/hosted_dependency_capability.py"
        ).read_bytes()
    )
    assert not _check_hosted_diagnostic_contract(script)

    script.write_text(
        script.read_text(encoding="utf-8").replace(
            'export PYTHONPATH="$ROOT/backend/src"',
            'export PYTHONPATH="$ROOT/backend/src${PYTHONPATH:+:$PYTHONPATH}"',
        ),
        encoding="utf-8",
    )
    violations = _check_hosted_diagnostic_contract(script)
    assert any("${PYTHONPATH:+" in item.message for item in violations)

    script.write_bytes(SCRIPT.read_bytes())
    capability.write_text(
        capability.read_text(encoding="utf-8").replace(
            'metadata.version("cryptography")',
            "metadata.version('cryptography')",
        ),
        encoding="utf-8",
    )
    assert not _check_hosted_diagnostic_contract(script)

    selector = script.parent / "host_python.sh"
    selector.write_text(
        selector.read_text(encoding="utf-8").replace(
            "local candidate resolved selector_dir repository_root capability_probe",
            "local candidate resolved\n  local selector_dir repository_root capability_probe",
        ),
        encoding="utf-8",
    )
    assert not _check_hosted_diagnostic_contract(script)


def test_hosted_diagnostic_keeps_native_uv_authority_separate_from_host_capability() -> (
    None
):
    text = SCRIPT.read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    qualification = (
        ROOT / "backend/src/oh_goon/repository_tooling/qualification_exec.py"
    ).read_text(encoding="utf-8")
    assert "native uv/.venv remains authoritative" in text
    init = makefile.split("\ninit:\n", 1)[1].split("\ninit-doctor:", 1)[0]
    assert (
        "uv sync --project backend --locked --group dev --group quality --group build"
        in init
    )
    assert "--no-install-project" not in init
    assert "uv pip install" not in init
    assert "qualification execution refuses interpreter rediscovery" in qualification
    assert 'env["VIRTUAL_ENV"]' not in qualification
    assert "--system-site-packages" not in makefile


def test_hosted_diagnostic_excludes_tests_requiring_external_dns() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "not host_dns" in text
    assert "constrained hosts never probe external DNS" in text


def test_host_dns_marker_is_registered_for_strict_pytest() -> None:
    pyproject = (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    assert '"host_dns: test requires working external DNS/network' in pyproject


def test_hosted_diagnostic_allows_only_narrowing_extra_marker_subset() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'EXTRA_MARKER="${DIAGNOSTIC_EXTRA_MARKER:-}"' in text
    assert 'MARKER="$BASE_MARKER"' in text
    assert 'MARKER="$BASE_MARKER and ($EXTRA_MARKER)"' in text
    assert "must be MARKER or 'not MARKER'" in text
    assert "Marker policy: $MARKER" in text


@pytest.mark.parametrize(
    "unsafe",
    (
        "unit or certification",
        "not unit or host_dns",
        "unit) or certification or (unit",
        "(unit)",
        "unit and not certification",
    ),
)
def test_hosted_diagnostic_rejects_boolean_extra_marker_injection_before_collection(
    tmp_path: Path,
    unsafe: str,
) -> None:
    if os.name == "nt":
        pytest.skip("hosted diagnostic is POSIX shell tooling")
    receipt = tmp_path / "hosted.json"
    receipt.write_text('{"result":"HOSTED-DIAGNOSTIC-PASSED"}\n', encoding="utf-8")
    env = os.environ.copy()
    env["DIAGNOSTIC_EXTRA_MARKER"] = unsafe
    env["OH_GOON_HOSTED_DIAGNOSTIC_RECEIPT"] = str(receipt)
    result = subprocess.run(
        ("bash", str(SCRIPT)),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert not receipt.exists()
    assert "boolean expressions are not accepted" in result.stderr
    assert "=== HOSTED DIAGNOSTIC:" not in result.stdout


def test_hosted_diagnostic_help_documents_narrowing_only_marker_contract() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "Narrow by one MARKER/not MARKER; boolean expressions rejected" in makefile


def test_hosted_diagnostic_does_not_offer_a_full_marker_override() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "DIAGNOSTIC_MARKER" not in text


def test_hosted_diagnostic_uses_native_batch_reports_for_machine_readable_evidence() -> (
    None
):
    text = SCRIPT.read_text(encoding="utf-8")
    assert '--report-path "$report_path"' in text
    assert "REPORT_MANIFEST=" in text
    assert "-m oh_goon.repository_tooling.hosted_diagnostic_receipt" in text


def test_hosted_diagnostic_invalidates_stale_receipt_before_capability_probes() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'rm -f -- "$RECEIPT_PATH"' in text
    assert text.index('rm -f -- "$RECEIPT_PATH"') < text.index(
        'command -v "$PYTHON_BIN"'
    )
