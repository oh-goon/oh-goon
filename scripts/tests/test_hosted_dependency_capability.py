from __future__ import annotations

from typing import Any, cast
from collections.abc import Callable

import importlib.metadata

import argparse
import json
import tomllib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.repository_tooling import hosted_dependency_capability as capability


ROOT = Path(__file__).resolve().parents[2]


def _build(**kwargs: Any) -> dict[str, Any]:
    return cast(dict[str, Any], capability.build_capability(**kwargs))


def _versions(**overrides: str) -> Callable[[str], str]:
    defaults = {
        "cryptography": "46.0.4",
        "fastapi": "0.128.2",
        "httpx2": "2.12",
        "pydantic": "2.13.4",
        "pyyaml": "6.0.3",
        "uvicorn": "0.48.0",
        "pytest": "9.0.2",
        "hashmarks": "0.21.0",
        "pytest-cov": "7.0.0",
        "mypy": "1.20.2",
        "ruff": "0.16.0",
        "types-pyyaml": "6.0.12",
        "editables": "0.5",
        "hatchling": "1.32.0",
    }
    defaults.update(overrides)

    def read(name: str) -> str:
        value = defaults[name]
        if value == "MISSING":
            raise importlib.metadata.PackageNotFoundError(name)
        return value

    return read


def _no_op_import_probe(_distribution: str, _import_name: str) -> None:
    return None


def test_host_capability_accepts_exact_declared_cryptography_minimum() -> None:
    payload = _build(
        version_reader=_versions(cryptography="46.0.4"),
        import_probe=_no_op_import_probe,
        interpreter="/host/python",
        python_version="3.13.5",
    )
    crypto = next(
        row for row in payload["packages"] if row["distribution"] == "cryptography"
    )
    assert crypto == {
        "distribution": "cryptography",
        "import": "cryptography",
        "requirement": "cryptography>=46.0.4",
        "observed_version": "46.0.4",
        "status": "compatible",
    }
    assert payload["result"] == "HOST-CAPABILITY-READY"
    assert payload["python_status"] == "compatible"
    assert payload["requires_python"] == ">=3.12,<3.15"
    assert payload["native_authority"] is False
    assert payload["dependency_complete_authority"] is False
    assert payload["uv_artifact_cache_evaluated"] is False


@pytest.mark.parametrize(
    "requirement",
    ["httpx2[ws]>=2.12", "httpx2[ ws, http2 ] >= 2.12", "  httpx2[ws]>=2.12  "],
)
def test_host_capability_preserves_native_requirement_extras_as_diagnostic_intent(
    tmp_path: Path, requirement: str
) -> None:
    original = (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        original.replace("httpx2[ws]>=2.12", requirement), encoding="utf-8"
    )
    probes: list[tuple[str, str]] = []
    payload = _build(
        pyproject=pyproject,
        version_reader=_versions(),
        import_probe=lambda distribution, name: probes.append((distribution, name)),
        python_version="3.13.5",
    )
    package = next(
        row for row in payload["packages"] if row["distribution"] == "httpx2"
    )
    assert package["requirement"] == requirement
    assert package["status"] == "compatible"
    assert probes.count(("httpx2", "httpx2")) == 1
    assert payload["native_authority"] is False
    assert payload["dependency_complete_authority"] is False


@pytest.mark.parametrize(
    "requirement",
    [
        "httpx2[]>=2.12",
        "httpx2[ws,]>=2.12",
        "httpx2[ws][http2]>=2.12",
        "httpx2[ws]==2.12",
        "httpx2[ws]>=2.12; python_version >= '3.12'",
    ],
)
def test_host_capability_extras_do_not_relax_the_minimum_policy(
    tmp_path: Path, requirement: str
) -> None:
    original = (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        original.replace("httpx2[ws]>=2.12", requirement), encoding="utf-8"
    )
    with pytest.raises(
        capability.HostedCapabilityError, match="minimum-compatible >= policy"
    ):
        _build(
            pyproject=pyproject,
            version_reader=_versions(),
            import_probe=_no_op_import_probe,
        )


@pytest.mark.parametrize("version", ("3.11.9", "3.15.0"))
def test_host_capability_blocks_interpreter_outside_project_python_range(
    version: str,
) -> None:
    payload = _build(
        version_reader=_versions(),
        import_probe=_no_op_import_probe,
        python_version=version,
    )
    assert payload["python_status"] == "incompatible"
    assert payload["result"] == "HOST-CAPABILITY-BLOCKED"
    assert f"python: {version} does not satisfy >=3.12,<3.15" in payload["blockers"]


def test_host_capability_rejects_cryptography_below_declared_minimum() -> None:
    payload = _build(
        version_reader=_versions(cryptography="46.0.3"),
        import_probe=_no_op_import_probe,
    )
    crypto = next(
        row for row in payload["packages"] if row["distribution"] == "cryptography"
    )
    assert crypto["status"] == "incompatible"
    assert payload["result"] == "HOST-CAPABILITY-BLOCKED"
    assert (
        "cryptography: 46.0.3 does not satisfy cryptography>=46.0.4"
        in payload["blockers"]
    )


def test_host_capability_rejects_prerelease_that_numeric_prefix_would_overaccept() -> (
    None
):
    payload = _build(
        version_reader=_versions(cryptography="46.0.4rc1"),
        import_probe=_no_op_import_probe,
    )
    crypto = next(
        row for row in payload["packages"] if row["distribution"] == "cryptography"
    )
    assert crypto["status"] == "incompatible"
    assert "final numeric releases" in crypto["detail"]
    assert payload["result"] == "HOST-CAPABILITY-BLOCKED"


def test_host_capability_blocks_when_required_cryptography_is_missing() -> None:
    payload = _build(
        version_reader=_versions(cryptography="MISSING"),
        import_probe=_no_op_import_probe,
    )
    crypto = next(
        row for row in payload["packages"] if row["distribution"] == "cryptography"
    )
    assert crypto["status"] == "missing"
    assert payload["result"] == "HOST-CAPABILITY-BLOCKED"
    assert "cryptography: missing" in payload["blockers"]


def test_missing_nonrequired_leaf_is_recorded_without_disabling_other_hosted_work() -> (
    None
):
    payload = _build(
        version_reader=_versions(httpx2="MISSING"),
        import_probe=_no_op_import_probe,
    )
    httpx2 = next(row for row in payload["packages"] if row["distribution"] == "httpx2")
    assert httpx2["status"] == "missing"
    assert payload["result"] == "HOST-CAPABILITY-READY"


def test_host_capability_blocks_broken_cryptography_import_or_native_binding() -> None:
    def broken(distribution: str, _import_name: str) -> None:
        if distribution == "cryptography":
            raise ImportError("OpenSSL binding unavailable")

    payload = _build(version_reader=_versions(), import_probe=broken)
    crypto = next(
        row for row in payload["packages"] if row["distribution"] == "cryptography"
    )
    assert crypto["status"] == "import-broken"
    assert "OpenSSL binding unavailable" in crypto["detail"]
    assert payload["result"] == "HOST-CAPABILITY-BLOCKED"


def test_cryptography_requirement_is_read_from_pyproject_not_duplicated_policy(
    tmp_path: Path,
) -> None:
    original = (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        original.replace("cryptography>=46.0.4", "cryptography>=47.0.0"),
        encoding="utf-8",
    )
    payload = _build(
        pyproject=pyproject,
        version_reader=_versions(cryptography="46.0.4"),
        import_probe=_no_op_import_probe,
    )
    crypto = next(
        row for row in payload["packages"] if row["distribution"] == "cryptography"
    )
    assert crypto["requirement"] == "cryptography>=47.0.0"
    assert crypto["status"] == "incompatible"


def test_host_capability_fails_closed_if_crypto_intent_stops_using_minimum_policy(
    tmp_path: Path,
) -> None:
    original = (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        original.replace("cryptography>=46.0.4", "cryptography==46.0.4"),
        encoding="utf-8",
    )
    with pytest.raises(
        capability.HostedCapabilityError, match="minimum-compatible >= policy"
    ):
        _build(
            pyproject=pyproject,
            version_reader=_versions(),
            import_probe=_no_op_import_probe,
        )


def test_host_capability_fails_closed_if_crypto_intent_disappears(
    tmp_path: Path,
) -> None:
    original = (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        original.replace('  "cryptography>=46.0.4",\n', ""), encoding="utf-8"
    )
    with pytest.raises(
        capability.HostedCapabilityError,
        match="missing hosted capability requirements: cryptography",
    ):
        _build(
            pyproject=pyproject,
            version_reader=_versions(),
            import_probe=_no_op_import_probe,
        )


def test_host_capability_blocks_unsupported_python_policy_shape(tmp_path: Path) -> None:
    original = (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        original.replace(
            'requires-python = ">=3.12,<3.15"', 'requires-python = ">=3.12"'
        ),
        encoding="utf-8",
    )
    payload = _build(
        pyproject=pyproject,
        version_reader=_versions(),
        import_probe=_no_op_import_probe,
        python_version="3.13.5",
    )
    assert payload["python_status"] == "incompatible"
    assert payload["result"] == "HOST-CAPABILITY-BLOCKED"
    assert any(
        "requires project.requires-python in >=MIN,<MAX form" in item
        for item in payload["blockers"]
    )


def test_host_capability_does_not_require_portable_resolution_artifact(
    tmp_path: Path,
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_bytes((ROOT / "backend/pyproject.toml").read_bytes())
    payload = _build(
        pyproject=pyproject,
        version_reader=_versions(cryptography="46.0.4"),
        import_probe=_no_op_import_probe,
    )
    crypto = next(
        row for row in payload["packages"] if row["distribution"] == "cryptography"
    )
    assert crypto["status"] == "compatible"
    assert payload["result"] == "HOST-CAPABILITY-READY"


def _cryptography_package_capability() -> tuple[dict[str, object], str | None]:
    observed = importlib.metadata.version("cryptography")
    return capability._package_capability(
        distribution="cryptography",
        import_name="cryptography",
        requirement="cryptography>=46.0.4",
        version_reader=lambda _name: observed,
        import_probe=capability._default_import_probe,
    )


def _assert_cryptography_surface_failure(detail: str) -> None:
    row, blocker = _cryptography_package_capability()
    assert row["status"] == "import-broken"
    assert detail in str(row["detail"])
    assert blocker is not None
    assert detail in blocker


def test_default_cryptography_probe_blocks_when_signing_surface_is_broken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    class BrokenSigningKey:
        def sign(self, _message: bytes) -> bytes:
            raise RuntimeError("sign unavailable")

    monkeypatch.setattr(
        Ed25519PrivateKey,
        "from_private_bytes",
        staticmethod(lambda _data: BrokenSigningKey()),
    )
    _assert_cryptography_surface_failure("sign unavailable")


def test_default_cryptography_probe_blocks_when_verification_surface_is_broken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    class BrokenPublicKey:
        def verify(self, _signature: bytes, _message: bytes) -> None:
            raise RuntimeError("verify unavailable")

    class SigningKey:
        def sign(self, _message: bytes) -> bytes:
            return b"signature"

        def public_key(self) -> BrokenPublicKey:
            return BrokenPublicKey()

    monkeypatch.setattr(
        Ed25519PrivateKey,
        "from_private_bytes",
        staticmethod(lambda _data: SigningKey()),
    )
    _assert_cryptography_surface_failure("verify unavailable")


def test_default_cryptography_probe_blocks_when_public_key_load_is_broken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cryptography.hazmat.primitives import serialization

    def broken_load(_payload: bytes) -> None:
        raise RuntimeError("public-key load unavailable")

    monkeypatch.setattr(serialization, "load_pem_public_key", broken_load)
    _assert_cryptography_surface_failure("public-key load unavailable")


def test_default_cryptography_probe_blocks_when_x509_load_is_broken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cryptography import x509

    def broken_load(_payload: bytes) -> None:
        raise RuntimeError("x509 load unavailable")

    monkeypatch.setattr(x509, "load_pem_x509_certificates", broken_load)
    _assert_cryptography_surface_failure("x509 load unavailable")


def test_default_cryptography_probe_succeeds_with_real_declared_host_package() -> None:
    declared, requirements, _requires_python, native_only = (
        capability._project_contract(ROOT / "backend/pyproject.toml")
    )
    observed = importlib.metadata.version("cryptography")
    assert capability._release(observed) >= capability._minimum(
        requirements["cryptography"]
    )
    capability._default_import_probe("cryptography", "cryptography")


def test_default_import_probe_accepts_repository_local_virtualenv_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = ROOT / "backend/.venv/lib/python3.14/site-packages/fastapi/__init__.py"
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda _name: SimpleNamespace(__file__=str(installed)),
    )
    capability._default_import_probe("fastapi", "fastapi")


def test_default_import_probe_rejects_repository_shadow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shadow = ROOT / "backend/src/shadow.py"
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda _name: SimpleNamespace(__file__=str(shadow)),
    )
    with pytest.raises(
        capability.HostedCapabilityError, match="shadowed by repository source"
    ):
        capability._default_import_probe("fastapi", "fastapi")


def test_default_cryptography_probe_rejects_module_metadata_version_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = SimpleNamespace(
        __file__="/host/site-packages/cryptography/__init__.py", __version__="46.0.5"
    )
    monkeypatch.setattr(importlib, "import_module", lambda _name: module)
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "46.0.4")
    with pytest.raises(
        capability.HostedCapabilityError, match="cryptography import metadata mismatch"
    ):
        capability._default_import_probe("cryptography", "cryptography")


def test_host_capability_inventory_covers_every_declared_python_dependency() -> None:
    declared, requirements, _requires_python, native_only = (
        capability._project_contract(ROOT / "backend/pyproject.toml")
    )
    payload = _build(
        version_reader=_versions(),
        import_probe=_no_op_import_probe,
        python_version="3.13.5",
    )
    assert payload["declared_python_dependencies"] == list(declared)
    assert payload["evaluated_python_dependencies"] == list(requirements)
    assert native_only == frozenset({"hashmarks"})
    assert payload["excluded_python_dependencies"] == sorted(native_only)
    assert {row["distribution"] for row in payload["packages"]} == set(requirements)
    assert {
        "cryptography",
        "fastapi",
        "httpx2",
        "hashmarks",
        "pydantic",
        "pyyaml",
        "uvicorn",
        "pytest",
        "pytest-cov",
        "mypy",
        "ruff",
        "editables",
        "hatchling",
    } <= set(declared)
    assert "hashmarks" not in requirements


def test_native_only_policy_changes_once_without_capability_code_change(
    tmp_path: Path,
) -> None:
    original = (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    declared = tomllib.loads(original)["project"]["dependencies"]
    native_requirement = next(
        value for value in declared if value.partition("@")[0].strip() == "hashmarks"
    )
    pyproject.write_text(
        original.replace(
            native_requirement,
            "native-observer>=1.0.0",
        ).replace(
            'native-only-dependencies = ["hashmarks"]',
            'native-only-dependencies = ["native-observer"]',
        ),
        encoding="utf-8",
    )
    payload = _build(
        pyproject=pyproject,
        version_reader=_versions(),
        import_probe=_no_op_import_probe,
        python_version="3.13.5",
    )
    assert payload["excluded_python_dependencies"] == ["native-observer"]
    assert "native-observer" in payload["declared_python_dependencies"]
    assert "native-observer" not in payload["evaluated_python_dependencies"]
    assert {row["distribution"] for row in payload["packages"]} == set(
        payload["evaluated_python_dependencies"]
    )


def test_native_only_policy_rejects_undeclared_dependency(tmp_path: Path) -> None:
    original = (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        original.replace(
            'native-only-dependencies = ["hashmarks"]',
            'native-only-dependencies = ["missing-native-owner"]',
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        capability.HostedCapabilityError, match="not declared dependencies"
    ):
        capability._project_contract(pyproject)


def test_registry_or_uv_cache_state_is_not_part_of_host_package_availability() -> None:
    payload = _build(
        version_reader=_versions(),
        import_probe=_no_op_import_probe,
        python_version="3.13.5",
    )
    assert payload["uv_artifact_cache_evaluated"] is False
    assert payload["native_authority"] is False
    assert all(row["status"] == "compatible" for row in payload["packages"])


def test_new_declared_dependency_is_automatically_inventoried_without_allowlist(
    tmp_path: Path,
) -> None:
    original = (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        original.replace(
            '  "uvicorn>=0.48.0",', '  "uvicorn>=0.48.0",\n  "future-lib>=1.2.3",'
        ),
        encoding="utf-8",
    )
    versions = _versions()

    def read(name: str) -> str:
        return "1.2.3" if name == "future-lib" else versions(name)

    payload = _build(
        pyproject=pyproject, version_reader=read, import_probe=_no_op_import_probe
    )
    future = next(
        row for row in payload["packages"] if row["distribution"] == "future-lib"
    )
    assert future["requirement"] == "future-lib>=1.2.3"
    assert future["import"] == "future_lib"
    assert future["status"] == "compatible"


def test_capability_identity_binds_dependency_intent_and_package_statuses() -> None:
    first = _build(
        version_reader=_versions(),
        import_probe=_no_op_import_probe,
        interpreter="/host/python",
        python_version="3.13.5",
    )
    second = _build(
        version_reader=_versions(cryptography="46.0.5"),
        import_probe=_no_op_import_probe,
        interpreter="/host/python",
        python_version="3.13.5",
    )
    assert first["identity"].startswith("sha256:")
    assert first["identity"] != second["identity"]
    assert first["dependency_intent_sha256"].startswith("sha256:")


def test_capability_payload_is_json_serializable() -> None:
    payload = _build(
        version_reader=_versions(httpx2="MISSING"),
        import_probe=_no_op_import_probe,
    )
    json.dumps(payload, sort_keys=True)


def test_capability_parsers_fail_closed_on_missing_requirement_and_project_contract() -> (
    None
):
    with pytest.raises(capability.HostedCapabilityError, match="cannot normalize"):
        capability._requirement_name("")
    with pytest.raises(capability.HostedCapabilityError, match="cannot parse"):
        capability._minimum("")
    with pytest.raises(capability.HostedCapabilityError, match=r"missing \[project\]"):
        capability._python_range({})
    with pytest.raises(
        capability.HostedCapabilityError, match="missing project.requires-python"
    ):
        capability._python_range({"project": {}})


def test_project_contract_rejects_duplicate_hosted_dependency_owner(
    tmp_path: Path,
) -> None:
    original = (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        original.replace("dev = [\n", 'dev = [\n  "cryptography>=46.0.4",\n'),
        encoding="utf-8",
    )
    with pytest.raises(
        capability.HostedCapabilityError,
        match="duplicate hosted dependency requirement",
    ):
        capability._project_contract(pyproject)


def test_required_host_capability_probe_uses_single_semantic_owner() -> None:
    observed: list[tuple[str, str]] = []

    def observe(distribution: str, import_name: str) -> None:
        observed.append((distribution, import_name))

    blockers = capability.required_host_capability_blockers(
        version_reader=_versions(),
        import_probe=observe,
    )
    assert blockers == ()
    assert tuple(distribution for distribution, _import_name in observed) == (
        "cryptography",
        "pytest",
    )
    assert tuple(distribution for distribution, _import_name in observed) == (
        capability.REQUIRED_HOST_CAPABILITIES
    )


def test_required_host_capability_probe_rejects_actual_missing_capability() -> None:
    blockers = capability.required_host_capability_blockers(
        version_reader=_versions(pytest="MISSING"),
        import_probe=_no_op_import_probe,
    )
    assert blockers == ("pytest: missing",)


def test_capability_main_probe_mode_reports_semantic_required_capability_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        capability,
        "_parse_args",
        lambda: argparse.Namespace(output=None, probe_required_host_capabilities=True),
    )
    monkeypatch.setattr(
        capability,
        "required_host_capability_blockers",
        lambda: ("pytest: missing",),
    )
    assert capability.main() == 3
    assert "pytest: missing" in capsys.readouterr().err


def test_capability_main_writes_ready_receipt_and_reports_separate_uv_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "capability.json"
    payload = _build(
        version_reader=_versions(httpx2="MISSING"),
        import_probe=_no_op_import_probe,
        interpreter="/host/python",
        python_version="3.13.5",
    )
    monkeypatch.setattr(
        capability, "_parse_args", lambda: argparse.Namespace(output=output)
    )
    monkeypatch.setattr(capability, "build_capability", lambda **_kwargs: payload)
    assert capability.main() == 0
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["identity"] == payload["identity"]
    captured = capsys.readouterr()
    assert "cryptography: 46.0.4 -> compatible" in captured.out
    assert "Native uv artifact cache: NOT EVALUATED" in captured.out


def test_capability_main_returns_blocked_without_claiming_native_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "capability.json"
    payload = _build(
        version_reader=_versions(cryptography="MISSING"),
        import_probe=_no_op_import_probe,
        python_version="3.13.5",
    )
    monkeypatch.setattr(
        capability, "_parse_args", lambda: argparse.Namespace(output=output)
    )
    monkeypatch.setattr(capability, "build_capability", lambda **_kwargs: payload)
    assert capability.main() == 3
    captured = capsys.readouterr()
    assert "incompatible host dependency capability" in captured.err
    assert "cryptography: missing" in captured.err


def test_capability_direct_stdlib_launch_does_not_require_installed_oh_goon(
    tmp_path: Path,
) -> None:
    script = (
        ROOT / "backend/src/oh_goon/repository_tooling/hosted_dependency_capability.py"
    )
    output = tmp_path / "capability.json"
    environment = {"PATH": os.environ.get("PATH", ""), "PYTHONNOUSERSITE": "1"}
    result = subprocess.run(
        (sys.executable, "-I", "-S", str(script), "--output", str(output)),
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    # -S deliberately removes every installed third-party package.  The probe is
    # therefore expected to classify missing host capability, not crash because
    # the Oh-Goon package itself has not been materialized yet.
    assert result.returncode == 3
    assert "ModuleNotFoundError: No module named 'oh_goon'" not in result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["result"] == "HOST-CAPABILITY-BLOCKED"
    assert payload["native_authority"] is False
