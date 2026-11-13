import ast
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
    (integration / "__init__.py").write_text(
        "from .consumer import observe\n__all__ = ('observe',)\n", encoding="utf-8"
    )
    (integration / "consumer.py").write_text(
        "from hashmarks import __version__\ndef observe(): return __version__\n",
        encoding="utf-8",
    )
    return package


def test_package_boundary_reobserves_source_after_semantic_import_change(
    tmp_path: Path,
) -> None:
    package = tmp_path / "oh_goon"
    package.mkdir()
    source = package / "owner.py"
    source.write_text("class RepositoryImpactGraph:\n    pass\n", encoding="utf-8")

    assert contract._package_boundary_violations(package) == ()
    assert contract._package_boundary_violations(package) == ()

    source.write_text("import hashmarks\n", encoding="utf-8")
    violations = contract._package_boundary_violations(package)
    assert any(
        "explicit oh_goon.integrations.hashmarks package" in item.message
        for item in violations
    )


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


@pytest.mark.parametrize(
    "statement",
    (
        "from oh_goon.integrations.hashmarks.consumer import observe",
        "import oh_goon.integrations.hashmarks.consumer as owner",
        "from .integrations.hashmarks.consumer import observe as expose",
        "from oh_goon.integrations.hashmarks import consumer",
        "from oh_goon.integrations.hashmarks import missing_capability",
        "from oh_goon.integrations.hashmarks import *",
        "import oh_goon.integrations.hashmarks as evidence\nowner = evidence.consumer",
        "from .integrations import hashmarks as evidence\nowner = evidence.consumer",
        "import oh_goon as product\nowner = product.integrations.hashmarks.consumer",
        "from importlib import import_module as load\nload('oh_goon.integrations.hashmarks.consumer')",
        "import importlib\nimportlib.import_module('.consumer', 'oh_goon.integrations.hashmarks')",
        "import importlib\nimportlib.import_module(name='oh_goon.integrations.hashmarks.consumer')",
        "import importlib\nimportlib.import_module(name='.consumer', package='oh_goon.integrations.hashmarks')",
        "__import__('oh_goon.integrations.hashmarks.consumer')",
        "__import__(name='oh_goon.integrations.hashmarks.consumer')",
        "__import__('oh_goon.integrations.hashmarks', fromlist=['consumer'])",
        "__import__('oh_goon.integrations.hashmarks', fromlist=('missing_capability',))",
        "__import__('oh_goon.integrations.hashmarks', fromlist=['*'])",
        "__import__('oh_goon.integrations.hashmarks', {}, {}, ('consumer',))",
        "from builtins import __import__ as load\nload('oh_goon.integrations.hashmarks.consumer')",
        "load = __import__\nload('oh_goon.integrations.hashmarks.consumer')",
        "def local(__import__):\n    return __import__('local')\n"
        "__import__('oh_goon.integrations.hashmarks.consumer')",
        "class Local:\n    __import__ = None\n"
        "    def method(self):\n        return __import__('oh_goon.integrations.hashmarks.consumer')",
    ),
)
def test_consumers_cannot_bypass_public_capability_census(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, statement: str
) -> None:
    package = _configure_package(tmp_path, monkeypatch)
    owner = package / "owner.py"
    owner.write_text(statement + "\n", encoding="utf-8")

    assert any(
        row.path == owner and "declared public API capabilities" in row.message
        for row in contract.check_hashmarks_harness_boundary_contract()
    )


@pytest.mark.parametrize(
    "statement",
    (
        "from importlib import import_module as load\nload('hashmarks.codemap')",
        "import importlib\nimportlib.import_module('hashmarks')",
        "import importlib\nimportlib.import_module(name='hashmarks')",
        "__import__('hashmarks')",
        "__import__(name='hashmarks')",
        "from hashmarks import CodeMap\nCodeMap = None",
        "from .integrations.hashmarks.consumer import observe\nobserve = None",
    ),
)
def test_dynamic_or_rebound_imports_do_not_change_boundary_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, statement: str
) -> None:
    package = _configure_package(tmp_path, monkeypatch)
    owner = package / "owner.py"
    owner.write_text(statement + "\n", encoding="utf-8")
    assert any(
        row.path == owner
        for row in contract.check_hashmarks_harness_boundary_contract()
    )


def test_native_literal_import_selector_cannot_evade_boundary_with_source_concatenation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _configure_package(tmp_path, monkeypatch)
    owner = package / "owner.py"
    owner.write_text(
        "import importlib\nload = importlib.import_module\n"
        "name = 'hash' + 'marks'\nload(name)\n",
        encoding="utf-8",
    )

    assert any(
        row.path == owner
        and "explicit oh_goon.integrations.hashmarks package" in row.message
        for row in contract.check_hashmarks_harness_boundary_contract()
    )


@pytest.mark.parametrize(
    ("selector", "private_selector"),
    [("['consumer']", True), ("[unknown('consumer')]", False)],
)
def test_native_fromlist_literals_are_bound_to_direct_authored_container_elements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selector: str,
    private_selector: bool,
) -> None:
    package = _configure_package(tmp_path, monkeypatch)
    owner = package / "owner.py"
    owner.write_text(
        "from builtins import __import__ as load\n"
        f"load('oh_goon.integrations.hashmarks', fromlist={selector})\n",
        encoding="utf-8",
    )

    rows = contract.check_hashmarks_harness_boundary_contract()
    assert any(row.path == owner for row in rows) is private_selector


@pytest.mark.parametrize(
    "statement",
    (
        "from oh_goon.integrations.hashmarks import observe as selected\nselected()",
        "from .integrations.hashmarks import observe\nobserve()",
        "from .integrations import hashmarks as evidence\nevidence.observe()",
        "import oh_goon.integrations.hashmarks as evidence\nevidence.observe()",
        "import oh_goon.integrations.hashmarks as evidence\n"
        "def local(evidence):\n    return evidence.consumer",
        "__import__('oh_goon.integrations.hashmarks', fromlist=['observe'])",
        "from local_loader import __import__\n__import__('oh_goon.integrations.hashmarks.consumer')",
        "from local_loader import load\n__import__ = load\n__import__('hashmarks')",
        "__import__ = None\n__import__('hashmarks')",
        "def __import__(name):\n    return name\n__import__('hashmarks')",
        "def local(__import__):\n    return __import__('hashmarks')",
        "[__import__('hashmarks') for __import__ in custom_loaders]",
    ),
)
def test_declared_public_capabilities_and_shadowed_local_objects_remain_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, statement: str
) -> None:
    package = _configure_package(tmp_path, monkeypatch)
    (package / "owner.py").write_text(statement + "\n", encoding="utf-8")
    assert contract.check_hashmarks_harness_boundary_contract() == []


@pytest.mark.parametrize(
    "statement",
    (
        "def local(loader=__import__):\n    return loader('hashmarks')\nlocal(custom)",
        "def local(loader=__import__):\n    alias = loader\n"
        "    return alias('hashmarks')\nlocal(custom)",
        "def local(__import__=__import__):\n    return __import__('hashmarks')\nlocal(custom)",
        "def local(loader):\n    return loader('hashmarks')\n"
        "local(__import__)\nlocal(custom)",
        "import importlib\ndef local(loader=importlib.import_module):\n"
        "    return loader('hashmarks')\nlocal(custom)",
        "import importlib\ndef local(loader):\n    return loader('hashmarks')\n"
        "local(importlib.import_module)\nlocal(custom)",
    ),
)
def test_supplied_callable_inputs_do_not_become_unconditional_native_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, statement: str
) -> None:
    package = _configure_package(tmp_path, monkeypatch)
    (package / "owner.py").write_text(statement + "\n", encoding="utf-8")
    assert contract.check_hashmarks_harness_boundary_contract() == []


def test_guard_does_not_upgrade_callable_defaults_or_unresolved_builtin_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.repository_tooling.architecture.common import (
        python_ast,
        python_semantic_facts,
        python_source_snapshot,
    )

    package = _configure_package(tmp_path, monkeypatch)
    owner = package / "owner.py"
    owner.write_text(
        "from importlib import import_module\n"
        "def local(loader=import_module):\n    return loader('hashmarks')\n"
        "__import__('unrelated')\n",
        encoding="utf-8",
    )
    with python_source_snapshot():
        before = python_semantic_facts(owner, imported_only=True)
        guarded = contract._import_facts(owner, python_ast(owner), package)
        after = python_semantic_facts(owner, imported_only=True)

    assert before.calls == guarded.calls == ()
    observed = before.evidence.packet()["facts"]
    assert any(
        fact["kind"] == "default-binding" and fact["qualification"] == "conditional"
        for fact in observed
    )
    assert any(
        fact["kind"] == "call-site" and "parameter-value-unknown" in fact["reasons"]
        for fact in observed
    )
    assert after == before


@pytest.mark.parametrize(
    "declaration",
    ("", "__all__ = ()", "__all__ = ('observe', 'observe')", "__all__ = (123,)"),
)
def test_public_boundary_requires_a_nonempty_unambiguous_census(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declaration: str
) -> None:
    package = _configure_package(tmp_path, monkeypatch)
    init = package / "integrations/hashmarks/__init__.py"
    init.write_text(
        "from .consumer import observe\n" + declaration + "\n", encoding="utf-8"
    )
    assert any(
        row.path == init and "__all__ capability census" in row.message
        for row in contract.check_hashmarks_harness_boundary_contract()
    )


def test_relative_import_qualification_does_not_mutate_shared_architecture_syntax(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.repository_tooling.architecture.common import (
        python_ast,
        python_source_snapshot,
    )

    package = _configure_package(tmp_path, monkeypatch)
    owner = package / "owner.py"
    owner.write_text(
        "from .integrations import hashmarks as evidence\nevidence.consumer.observe()\n",
        encoding="utf-8",
    )
    with python_source_snapshot():
        tree = python_ast(owner)
        original = ast.dump(tree, include_attributes=True)
        assert contract._package_boundary_violations(package)
        assert python_ast(owner) is tree
        assert ast.dump(tree, include_attributes=True) == original
        assert contract._imports(owner, tree=tree, package_root=package) == {
            "oh_goon.integrations",
            "oh_goon.integrations.hashmarks",
        }


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
    _configure_package(tmp_path, monkeypatch)
    owner = tmp_path / "self_certification_plan.py"
    owner.write_text("def evaluate():\n    return ()\n", encoding="utf-8")
    monkeypatch.setattr(contract, "VALIDATION_AUTHORITY_OWNER", owner)
    assert contract.check_hashmarks_harness_boundary_contract() == []

    owner.write_text(
        "from oh_goon.integrations.hashmarks.repository_intelligence import HashmarksEvidenceKind\n",
        encoding="utf-8",
    )
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
    _configure_package(tmp_path, monkeypatch)
    path = tmp_path / "game_tape.py"
    identity = "producer_contract_identity: str\n"
    path.write_text(identity, encoding="utf-8")
    monkeypatch.setattr(contract, "GAME_TAPE_MODEL", path)
    assert contract.check_hashmarks_harness_boundary_contract() == []

    path.write_text(
        identity + "canonical_payload_json = 'forbidden'\n", encoding="utf-8"
    )
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


@pytest.mark.parametrize("captured_packets", [False, True])
def test_import_consumers_reuse_detached_native_declarations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_packets: bool
) -> None:
    from dataclasses import FrozenInstanceError
    from typing import Any

    from oh_goon.integrations.hashmarks import HashmarksSourceEvidence
    from oh_goon.repository_tooling.architecture import common

    package = _configure_package(tmp_path, monkeypatch)
    owner = package / "owner.py"
    owner.write_text(
        "from .integrations.hashmarks import observe as selected\n"
        "selected = None\n"
        "if enabled:\n    from .integrations.hashmarks import *\n",
        encoding="utf-8",
    )
    native_packet = HashmarksSourceEvidence.packet
    reads: list[HashmarksSourceEvidence] = []

    def packet(evidence: HashmarksSourceEvidence) -> dict[str, Any]:
        reads.append(evidence)
        return native_packet(evidence)

    with common.python_source_snapshot(tmp_path, captured_packets=captured_packets):
        for path in package.rglob("*.py"):
            common.python_semantic_facts(
                path, imported_only=True, tree=common.python_ast(path)
            )
        facts = common.python_semantic_facts(owner, imported_only=True)
        baseline = contract._package_boundary_violations(package)
        assert any(
            row.path == owner and "wildcard imports" in row.message for row in baseline
        )
        original = facts.evidence.packet()
        detached = facts.evidence.packet()
        for row in detached["facts"]:
            if row.get("kind") == "import-binding":
                row["import_syntax"]["module"] = "unrelated"
        with pytest.raises(FrozenInstanceError):
            setattr(facts.import_declarations[0], "module", "unrelated")
        assert facts.evidence.packet() == original
        monkeypatch.setattr(HashmarksSourceEvidence, "packet", packet)
        assert contract._imports(owner, package_root=package, facts=facts) == {
            "oh_goon.integrations.hashmarks",
            "oh_goon.integrations.hashmarks.observe",
        }
        assert contract._package_boundary_violations(package) == baseline
        assert reads == []


@pytest.mark.parametrize("captured_packets", [False, True])
@pytest.mark.parametrize(
    "mutation", ["schema", "level", "local-name", "properties", "spelling"]
)
def test_import_projection_keeps_native_spelling_and_refuses_malformed_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    captured_packets: bool,
    mutation: str,
) -> None:
    import json
    from dataclasses import replace
    from collections.abc import Sequence
    from typing import Any

    from oh_goon.integrations.hashmarks import (
        HashmarksSourceEvidence,
        observe_source_bindings,
    )
    from oh_goon.repository_tooling.architecture import common

    source = tmp_path / "source.py"
    source.write_text("from .package import selected as alias\n", encoding="utf-8")
    native = observe_source_bindings

    def observe(
        root: Path, paths: Sequence[str], **kwargs: Any
    ) -> dict[str, HashmarksSourceEvidence]:
        receiver = kwargs.pop("on_observation", None)
        originals = native(root, paths, **kwargs)
        result: dict[str, HashmarksSourceEvidence] = {}
        for member, evidence in originals.items():
            packet = evidence.packet()
            declaration = next(
                row for row in packet["facts"] if row["kind"] == "import-binding"
            )
            syntax = declaration["import_syntax"]
            if mutation == "schema":
                packet["schema"] = "unsupported-source-schema"
            elif mutation == "level":
                syntax["level"] = "1"
            elif mutation == "local-name":
                syntax["local_name"] = []
            elif mutation == "properties":
                syntax["properties"] = ["retained", 1]
            else:
                syntax["properties"] = ["retained"]
            transferred = replace(evidence, canonical_packet_json=json.dumps(packet))
            if receiver is not None:
                receiver(member, transferred, packet)
            result[member] = transferred
        return result

    monkeypatch.setattr(common, "observe_source_bindings", observe)
    with common.python_source_snapshot(tmp_path, captured_packets=captured_packets):
        if mutation == "spelling":
            facts = common.python_semantic_facts(source, imported_only=True)
            declaration = facts.import_declarations[0]
            assert (
                declaration.module,
                declaration.level,
                declaration.name,
                declaration.local_name,
            ) == ("package", 1, "selected", "alias")
            assert declaration.spelling == ".package.selected.retained"
            assert facts.imports == frozenset({".package.selected.retained"})
        else:
            with pytest.raises(ValueError, match="malformed|unavailable or incomplete"):
                common.python_semantic_facts(source, imported_only=True)
