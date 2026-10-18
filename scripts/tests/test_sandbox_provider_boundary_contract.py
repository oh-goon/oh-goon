from pathlib import Path

from oh_goon.repository_tooling.architecture import (
    sandbox_provider_boundary_contract as contract,
)


def test_current_tree_satisfies_sandbox_provider_boundary() -> None:
    assert contract.check_sandbox_provider_boundary_contract() == []


def test_provider_sdk_import_is_rejected(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path
    transport = root / contract._TRANSPORT
    isolation = root / contract._ISOLATION
    pyproject = root / contract._PYPROJECT
    transport.parent.mkdir(parents=True)
    isolation.parent.mkdir(parents=True, exist_ok=True)
    pyproject.parent.mkdir(parents=True, exist_ok=True)
    transport.write_text("import opensandbox\n", encoding="utf-8")
    isolation.write_text(
        "runtime_attestation_sha256 osb_sha256 OpenSandboxRuntimeKind.KATA OpenSandboxRuntimeKind.FIRECRACKER server_configuration_sha256",
        encoding="utf-8",
    )
    pyproject.write_text(
        '[project]\nname="x"\nversion="1"\ndependencies=[]\n', encoding="utf-8"
    )
    violations = contract.check_sandbox_provider_boundary_contract(root)
    assert any("must not import" in item.message for item in violations)


def test_provider_python_dependency_is_rejected(tmp_path: Path) -> None:
    root = tmp_path
    transport = root / contract._TRANSPORT
    isolation = root / contract._ISOLATION
    pyproject = root / contract._PYPROJECT
    transport.parent.mkdir(parents=True)
    isolation.parent.mkdir(parents=True, exist_ok=True)
    pyproject.parent.mkdir(parents=True, exist_ok=True)
    transport.write_text(
        'env["OPEN_SANDBOX_API_KEY"] = settings.api_key\n"networkPolicy": {"defaultAction": "deny", "egress": []}\n"resourceLimits": _opensandbox_resource(resources)\n',
        encoding="utf-8",
    )
    isolation.write_text(
        "runtime_attestation_sha256 osb_sha256 OpenSandboxRuntimeKind.KATA OpenSandboxRuntimeKind.FIRECRACKER server_configuration_sha256",
        encoding="utf-8",
    )
    pyproject.write_text(
        '[project]\nname="x"\nversion="1"\ndependencies=["opensandbox==0.1.15"]\n',
        encoding="utf-8",
    )
    violations = contract.check_sandbox_provider_boundary_contract(root)
    assert any("Python dependency authority" in item.message for item in violations)


def test_network_policy_payload_is_behavioral_not_source_text_authority(
    tmp_path: Path,
) -> None:
    root = tmp_path
    transport = root / contract._TRANSPORT
    isolation = root / contract._ISOLATION
    pyproject = root / contract._PYPROJECT
    transport.parent.mkdir(parents=True)
    isolation.parent.mkdir(parents=True, exist_ok=True)
    pyproject.parent.mkdir(parents=True, exist_ok=True)
    transport.write_text(
        'env["OPEN_SANDBOX_API_KEY"] = settings.api_key\n'
        'payload = {"networkPolicy": {"egress": [], "defaultAction": "allow"}}\n'
        'decoy = \'"networkPolicy": {"defaultAction": "deny", "egress": []}\'\n'
        'payload["resourceLimits"] = _opensandbox_resource(resources)\n',
        encoding="utf-8",
    )
    isolation.write_text(
        "runtime_attestation_sha256 osb_sha256 OpenSandboxRuntimeKind.KATA "
        "OpenSandboxRuntimeKind.FIRECRACKER server_configuration_sha256",
        encoding="utf-8",
    )
    pyproject.write_text(
        '[project]\nname="x"\nversion="1"\ndependencies=[]\n',
        encoding="utf-8",
    )

    violations = contract.check_sandbox_provider_boundary_contract(root)

    assert not any("networkPolicy" in item.message for item in violations)
