from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture import (
    hashmarks_harness_boundary_contract as contract,
)


def _configure_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    package = tmp_path / "oh_goon"
    package.mkdir(exist_ok=True)
    monkeypatch.setattr(contract, "BACKEND_PACKAGE_ROOT", package)
    monkeypatch.setattr(contract, "AGENTS", tmp_path / "AGENTS.md")
    contract.AGENTS.write_text(
        "Hashmarks and Oh-Goon have separate responsibilities.\n"
        "This wording is documentation, not executable architecture authority.\n",
        encoding="utf-8",
    )
    integration = package / "integrations" / "hashmarks"
    integration.mkdir(parents=True, exist_ok=True)
    (integration / "__init__.py").write_text("", encoding="utf-8")
    (integration / "consumer.py").write_text(
        "from hashmarks import __version__\n", encoding="utf-8"
    )
    return package


def test_package_boundary_scan_reuses_unchanged_tree_and_invalidates_on_semantic_import_change(
    tmp_path: Path,
) -> None:
    package = tmp_path / "oh_goon"
    package.mkdir()
    source = package / "owner.py"
    source.write_text("class RepositoryImpactGraph:\n    pass\n", encoding="utf-8")

    contract._package_boundary_violations_cached.cache_clear()
    assert contract._package_boundary_violations(package) == ()
    after_first = contract._package_boundary_violations_cached.cache_info()
    assert contract._package_boundary_violations(package) == ()
    after_second = contract._package_boundary_violations_cached.cache_info()
    assert after_second.hits == after_first.hits + 1

    source.write_text("import hashmarks\n", encoding="utf-8")
    violations = contract._package_boundary_violations(package)
    assert any(
        "explicit oh_goon.integrations.hashmarks package" in item.message
        for item in violations
    )
    assert (
        contract._package_boundary_violations_cached.cache_info().misses
        == after_second.misses + 1
    )


def test_hashmarks_harness_boundary_contract() -> None:
    assert contract.check_hashmarks_harness_boundary_contract() == []


def test_hashmarks_boundary_documentation_can_be_reworded_without_architecture_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_package(tmp_path, monkeypatch)
    contract.AGENTS.write_text(
        "Repository evidence comes from the repository-evidence provider.\n"
        "Execution and certification remain controlled by Oh-Goon.\n",
        encoding="utf-8",
    )

    assert contract.check_hashmarks_harness_boundary_contract() == []


def test_hashmarks_boundary_requires_real_structural_integration_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _configure_package(tmp_path, monkeypatch)
    integration = package / "integrations" / "hashmarks"
    (integration / "consumer.py").unlink()
    (integration / "notes.py").write_text(
        "# from hashmarks import __version__\n"
        "BOUNDARY_NOTE = 'Hashmarks imports belong in this package'\n",
        encoding="utf-8",
    )

    violations = contract.check_hashmarks_harness_boundary_contract()

    assert any(
        item.path == integration
        and "must structurally own external Hashmarks API consumption" in item.message
        for item in violations
    )


def test_private_names_do_not_define_hashmarks_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _configure_package(tmp_path, monkeypatch)
    path = package / "anything.py"
    path.write_text(
        "class RepositoryImpactGraph:\n    pass\n\n"
        "def verification_selection_engine(change):\n    return change\n\n"
        "class RepositoryRanker:\n    pass\n\n"
        "class RepositoryNegativeEvidenceStore:\n    pass\n",
        encoding="utf-8",
    )
    assert contract.check_hashmarks_harness_boundary_contract() == []


def test_direct_hashmarks_import_outside_integration_boundary_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _configure_package(tmp_path, monkeypatch)
    path = package / "runtime_owner.py"
    path.write_text("from hashmarks import CodeMap\n", encoding="utf-8")
    violations = contract.check_hashmarks_harness_boundary_contract()
    assert any(
        item.path == path
        and "explicit oh_goon.integrations.hashmarks package" in item.message
        for item in violations
    )


def test_hashmarks_integration_filename_is_semantically_irrelevant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _configure_package(tmp_path, monkeypatch)
    integration = package / "integrations" / "hashmarks"
    integration.mkdir(parents=True, exist_ok=True)
    for name in ("evidence.py", "selection.py", "foo.py"):
        (integration / name).write_text(
            "from hashmarks import CodeMap\n", encoding="utf-8"
        )
    assert contract.check_hashmarks_harness_boundary_contract() == []


def test_hashmarks_prefix_outside_explicit_package_has_no_privilege(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _configure_package(tmp_path, monkeypatch)
    integrations = package / "integrations"
    integrations.mkdir(exist_ok=True)
    path = integrations / "hashmarks_legacy.py"
    path.write_text("from hashmarks import CodeMap\n", encoding="utf-8")
    violations = contract.check_hashmarks_harness_boundary_contract()
    assert any(
        item.path == path
        and "explicit oh_goon.integrations.hashmarks package" in item.message
        for item in violations
    )


@pytest.mark.parametrize(
    "statement",
    (
        "import hashmarks.benchmark_shards",
        "from hashmarks import benchmark_shards",
        "from hashmarks.benchmark_shards import execute_shard",
        "from hashmarks import harness as renamed",
    ),
)
def test_hashmarks_integration_rejects_producer_execution_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, statement: str
) -> None:
    package = _configure_package(tmp_path, monkeypatch)
    integration = package / "integrations" / "hashmarks"
    integration.mkdir(parents=True, exist_ok=True)
    path = integration / "execution.py"
    path.write_text(statement + "\n", encoding="utf-8")
    violations = contract.check_hashmarks_harness_boundary_contract()
    assert any(
        item.path == path and "Oh-Goon owns execution" in item.message
        for item in violations
    )


@pytest.mark.parametrize(
    "statement",
    (
        "import socket",
        "import urllib.request",
        "from urllib import request as transport",
    ),
)
def test_hashmarks_integration_rejects_ambient_daemon_socket_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, statement: str
) -> None:
    package = _configure_package(tmp_path, monkeypatch)
    integration = package / "integrations" / "hashmarks"
    integration.mkdir(parents=True, exist_ok=True)
    path = integration / "evidence.py"
    path.write_text(statement + "\n", encoding="utf-8")
    violations = contract.check_hashmarks_harness_boundary_contract()
    assert any(
        item.path == path
        and "ambient daemon/socket/process/network authority" in item.message
        for item in violations
    )


def test_relative_integration_import_keeps_oh_goon_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _configure_package(tmp_path, monkeypatch)
    path = package / "integrations" / "hashmarks" / "consumer.py"
    path.write_text(
        "from . import identity\nfrom hashmarks import CodeMap\n", encoding="utf-8"
    )

    assert contract._imports(path, package_root=package) == {
        "oh_goon.integrations.hashmarks",
        "oh_goon.integrations.hashmarks.identity",
        "hashmarks",
        "hashmarks.CodeMap",
    }
    assert contract._package_boundary_violations(package) == ()


def test_hashmarks_execution_reuses_existing_schedule_authority() -> None:
    from oh_goon.core.capability_plan_definitions import PLAN_CAPABILITIES

    definition = next(
        item for item in PLAN_CAPABILITIES if item.capability_id == "fleet-plan-execute"
    )
    assert definition.schedule_supported is True
    assert definition.fleet_supported is True


def test_canonical_validation_authority_cannot_import_hashmarks_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = tmp_path / "self_certification_plan.py"
    owner.write_text(
        "from oh_goon.integrations.hashmarks.repository_intelligence import HashmarksEvidenceKind\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(contract, "VALIDATION_AUTHORITY_OWNER", owner)
    violations = contract.check_hashmarks_harness_boundary_contract()
    assert any(
        item.path == owner
        and "must not own or suppress canonical validation gates" in item.message
        for item in violations
    )


def test_canonical_validation_authority_retains_mandatory_gate_owner() -> None:
    assert contract.VALIDATION_AUTHORITY_OWNER.name == "self_certification_plan.py"
    assert contract._validation_authority_violations() == []


def test_validation_gate_source_representation_is_not_architecture_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = tmp_path / "_self_certification_plan_units.py"
    owner.write_text(
        "def helper(script_units):\n"
        "    local = {\n"
        "        'scripts': list(script_units),\n"
        "        'shell': ['shell'],\n"
        "        'compile': ['compile'],\n"
        "        'architecture': ['architecture'],\n"
        "        'integration': ['integration'],\n"
        "        'admission': ['goon-static-admission'],\n"
        "    }\n"
        "    return local\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(contract, "VALIDATION_GATE_POLICY_OWNER", owner)

    assert contract._validation_authority_violations() == []


def test_validation_gate_semantics_accept_equivalent_sequence_types_and_ordering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = contract._resolved_validation_gate_dependencies()
    equivalent = {}
    for gate, dependencies in reversed(tuple(resolved.items())):
        assert isinstance(dependencies, (list, tuple, set, frozenset))
        equivalent[gate] = list(dependencies)

    monkeypatch.setattr(
        contract, "_resolved_validation_gate_dependencies", lambda: equivalent
    )

    assert contract._validation_authority_violations() == []


def test_validation_gate_missing_required_surface_fails_semantically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = dict(contract._resolved_validation_gate_dependencies())
    resolved.pop("architecture")
    monkeypatch.setattr(
        contract, "_resolved_validation_gate_dependencies", lambda: resolved
    )

    violations = contract._validation_authority_violations()

    assert any(
        "required validation gate 'architecture' is missing" in item.message
        for item in violations
    )


def test_dead_validation_gate_fragments_cannot_satisfy_semantic_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = tmp_path / "_self_certification_plan_units.py"
    owner.write_text(
        '# "architecture": ("architecture",)\n'
        'DEAD = \'"shell": ("shell",)\'\n'
        '# "scripts": script_units\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(contract, "VALIDATION_GATE_POLICY_OWNER", owner)
    resolved = dict(contract._resolved_validation_gate_dependencies())
    resolved.pop("shell")
    monkeypatch.setattr(
        contract, "_resolved_validation_gate_dependencies", lambda: resolved
    )

    violations = contract._validation_authority_violations()

    assert any(
        "required validation gate 'shell' is missing" in item.message
        for item in violations
    )


def test_hashmarks_contract_identity_is_preserved_across_admission_materialization_and_game_tape() -> (
    None
):
    for path in (
        contract.HASHMARKS_SHARD_ADAPTER,
        contract.EXECUTION_MATERIALIZATION_MODEL,
        contract.GAME_TAPE_MODEL,
    ):
        assert "producer_contract_identity" in path.read_text(encoding="utf-8")


def test_game_tape_cannot_project_raw_hashmarks_repository_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "game_tape.py"
    path.write_text("canonical_payload_json = 'forbidden'\n", encoding="utf-8")
    monkeypatch.setattr(contract, "GAME_TAPE_MODEL", path)
    violations = contract.check_hashmarks_harness_boundary_contract()
    assert any(
        item.path == path
        and "identity-only Hashmarks disclosure metadata" in item.message
        for item in violations
    )


def test_offline_proof_architecture_rejects_execution_reuse_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.repository_tooling.architecture import (
        _hashmarks_verification_boundary_contract as verification_contract,
    )

    package = tmp_path / "oh_goon"
    execution = package / "runtime" / "execution"
    execution.mkdir(parents=True)
    offline = execution / "offline_proof.py"
    terminal = execution / "terminal_attestation.py"
    executor = execution / "shards.py"
    offline.write_text(
        "import oh_goon.runtime.execution.shards\n",
        encoding="utf-8",
    )
    terminal.write_text(
        "from oh_goon.runtime.execution.offline_proof import verify_completed_shard_proof\n",
        encoding="utf-8",
    )
    executor.write_text(
        "from oh_goon.runtime.execution.terminal_attestation import verify_terminal_execution_attestation\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(verification_contract, "BACKEND_PACKAGE_ROOT", package)
    monkeypatch.setattr(verification_contract, "_OFFLINE_PROOF", offline)
    monkeypatch.setattr(verification_contract, "_TERMINAL_ATTESTATION", terminal)

    violations = verification_contract._proof_reuse_non_authority_violations()
    assert any(
        item.path == offline and "execution/reuse authority" in item.message
        for item in violations
    )
    assert any(
        item.path == executor and "terminal-attestation verification" in item.message
        for item in violations
    )


def test_hashmarks_harness_and_verification_boundaries_are_independent_architecture_owners() -> (
    None
):
    from oh_goon.repository_tooling.architecture._hashmarks_verification_boundary_contract import (
        check_hashmarks_verification_boundary_contract,
    )
    from oh_goon.repository_tooling.check_architecture import CHECKS

    assert contract.check_hashmarks_harness_boundary_contract in CHECKS
    assert check_hashmarks_verification_boundary_contract in CHECKS
    harness_source = Path(contract.__file__).read_text(encoding="utf-8")
    assert "check_hashmarks_verification_boundary_contract" not in harness_source
