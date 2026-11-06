from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture import filesystem_identity_contract
from oh_goon.repository_tooling.architecture.common import Violation


def test_hashmarks_version_alias_is_not_architecture_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = tmp_path / "identity.py"
    adapter.write_text(
        "from hashmarks import RepositoryIdentity, __version__ as HM_RELEASE_VERSION, hash_file\n"
        "def identify(identity, verify):\n"
        "    return identity.observe_directory(verify=verify)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(filesystem_identity_contract, "ADAPTER", adapter)
    monkeypatch.setattr(filesystem_identity_contract, "IDENTITY_MODULES", ())
    assert filesystem_identity_contract.check_filesystem_identity_contract() == []


def test_hashmarks_version_lookup_location_is_not_identity_architecture_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = tmp_path / "identity.py"
    adapter.write_text(
        "def identify(path, verify):\n"
        "    from hashmarks import RepositoryIdentity, hash_file\n"
        "    if path.is_file():\n"
        "        return hash_file(path)\n"
        "    with RepositoryIdentity(path) as identity:\n"
        "        return identity.observe_directory(verify=verify)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(filesystem_identity_contract, "ADAPTER", adapter)
    monkeypatch.setattr(filesystem_identity_contract, "IDENTITY_MODULES", ())
    assert filesystem_identity_contract.check_filesystem_identity_contract() == []


def test_architecture_does_not_certify_known_private_freshness_spellings() -> None:
    root = Path(__file__).resolve().parents[2]
    freshness = (
        root
        / "backend/src/oh_goon/repository_tooling/architecture/_structural_authority_freshness.py"
    ).read_text(encoding="utf-8")
    release = (
        root
        / "backend/src/oh_goon/repository_tooling/architecture/_structural_project_dependency_contract.py"
    ).read_text(encoding="utf-8")
    for token in (
        "previous_folders =",
        "const shouldPollRef",
        "shouldPollRef.current",
        "pollRefreshInFlightRef",
        "batch.next_sequence !== after",
        "callbacks.onInvariant()",
        "stopped = true",
    ):
        assert token not in freshness
    for token in (
        "_restore_authoring_state(snapshot)",
        "_regenerate_generated_authority()",
        "assert_canonical_source_layout(ROOT)",
    ):
        assert token not in release


def test_typescript_fence_parameter_names_are_not_architecture_authority(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.architecture._structural_authority_frontend import (
        _check_mutable_resource_fences,
    )

    api = tmp_path / "frontend/src/domain/api/ApiClient.ts"
    api.parent.mkdir(parents=True)
    api.write_text(
        "interface ApiClient {\n"
        "  removeLocation(location: string, revision: string): unknown;\n"
        "  cancelQueuedAdmission(id: string, sequence: number): unknown;\n"
        "  drainExecutor(id: string, generation: number, revision: number): unknown;\n"
        "  activateExecutor(id: string, generation: number, revision: number): unknown;\n"
        "  revokeExecutorTrust(id: string, generation: number, revision: number): unknown;\n"
        "  restoreExecutorTrust(id: string, generation: number, revision: number): unknown;\n"
        "  reconcileEffect(id: string, version: number): unknown;\n"
        "  acknowledgeEffect(id: string, message: string, version: number): unknown;\n"
        "}\n",
        encoding="utf-8",
    )
    assert _check_mutable_resource_fences(tmp_path) == []


def test_native_environment_local_name_is_not_architecture_authority(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.architecture._structural_project_dependency_contract import (
        _check_native_command_policy,
    )

    native = tmp_path / "backend/src/oh_goon/repository_tooling/native_command.py"
    native.parent.mkdir(parents=True)
    native.write_text(
        "import os\n"
        "def run():\n"
        "    runtime_environment = os.environ.copy()\n"
        "    keys = ('UV_DEFAULT_INDEX', 'UV_INDEX', 'UV_CONFIG_FILE')\n"
        "    return runtime_environment, keys\n",
        encoding="utf-8",
    )
    assert _check_native_command_policy(tmp_path) == []


def test_sandbox_environment_local_name_is_not_architecture_authority(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.architecture.sandbox_provider_boundary_contract import (
        _assigns_environment_key,
    )

    transport = tmp_path / "transport.py"
    transport.write_text(
        "def configure(config):\n"
        "    runtime_environment = {}\n"
        "    runtime_environment['OPEN_SANDBOX_API_KEY'] = config.api_key\n"
        "    return runtime_environment\n",
        encoding="utf-8",
    )
    assert _assigns_environment_key(transport, "OPEN_SANDBOX_API_KEY") is True


def test_architecture_no_longer_requires_private_storage_helper_spellings() -> None:
    root = Path(__file__).resolve().parents[2]
    execution = (
        root
        / "backend/src/oh_goon/repository_tooling/architecture/_structural_authority_execution.py"
    ).read_text(encoding="utf-8")
    for token in (
        "probe_writable_directory(state)",
        "storage_unavailable_reason",
        "sanitize_and_persist",
    ):
        assert token not in execution


def test_pytest_testpaths_toml_representation_is_semantic(tmp_path: Path) -> None:
    from oh_goon.repository_tooling.architecture._structural_authority_execution import (
        _check_pytest_testpaths,
    )

    pyproject = tmp_path / "backend/pyproject.toml"
    pyproject.parent.mkdir(parents=True)
    equivalent_forms = (
        "[tool.pytest.ini_options]\ntestpaths = ['tests/unit', 'tests/dependency_complete', 'tests/integration']\n",
        "[tool.pytest.ini_options]\ntestpaths=['tests/unit','tests/dependency_complete','tests/integration']\n",
        '[tool.pytest.ini_options]\ntestpaths = [\n  "tests/unit",\n  "tests/dependency_complete",\n  "tests/integration",\n]\n',
    )
    for text in equivalent_forms:
        pyproject.write_text(text, encoding="utf-8")
        assert _check_pytest_testpaths(pyproject) == []


def test_pytest_testpaths_semantic_removal_is_rejected(tmp_path: Path) -> None:
    from oh_goon.repository_tooling.architecture._structural_authority_execution import (
        _check_pytest_testpaths,
    )

    pyproject = tmp_path / "backend/pyproject.toml"
    pyproject.parent.mkdir(parents=True)
    pyproject.write_text(
        "[tool.pytest.ini_options]\ntestpaths = ['tests/unit', 'tests/integration']\n",
        encoding="utf-8",
    )
    violations = _check_pytest_testpaths(pyproject)
    assert any(
        "pytest testpaths must retain dependency-complete unit ownership"
        in item.message
        for item in violations
    )


def test_wheel_catalog_hook_toml_representation_is_semantic(tmp_path: Path) -> None:
    from oh_goon.repository_tooling.architecture.template_layout_contract import (
        _check_wheel_force_include,
    )

    pyproject = tmp_path / "backend/pyproject.toml"
    pyproject.parent.mkdir(parents=True)
    equivalent_forms = (
        "[tool.hatch.build.targets.wheel.hooks.custom]\npath = 'hatch_build.py'\n",
        "[tool.hatch.build.targets.wheel.hooks.custom]\npath='hatch_build.py'\n",
        '[tool.hatch.build.targets.wheel.hooks.custom]\npath = "hatch_build.py"\n',
    )
    for text in equivalent_forms:
        pyproject.write_text(text, encoding="utf-8")
        violations: list[Violation] = []
        _check_wheel_force_include(pyproject, violations)
        assert violations == []


def test_wheel_catalog_source_cannot_bypass_admitted_staging(tmp_path: Path) -> None:
    from oh_goon.repository_tooling.architecture.template_layout_contract import (
        _check_wheel_force_include,
    )

    pyproject = tmp_path / "backend/pyproject.toml"
    pyproject.parent.mkdir(parents=True)
    pyproject.write_text(
        "[tool.hatch.build.targets.wheel.force-include]\n"
        '"../templates/goons" = "oh_goon/templates/WRONG"\n'
        '[tool.hatch.build.targets.wheel.hooks.custom]\npath = "hatch_build.py"\n',
        encoding="utf-8",
    )
    violations: list[Violation] = []
    _check_wheel_force_include(pyproject, violations)
    assert any(
        'wheel packaging must stage "../templates/goons"' in item.message
        for item in violations
    )
