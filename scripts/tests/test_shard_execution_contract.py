from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture import shard_execution_contract as contract


def test_shard_execution_contract_is_green() -> None:
    assert contract.check_shard_execution_contract() == []


def test_shard_execution_contract_rejects_loss_of_exact_context_field(
    monkeypatch, tmp_path: Path
) -> None:
    runtime_model = tmp_path / "shard_models.py"
    runtime_model.write_text(
        contract.RUNTIME_MODEL.read_text(encoding="utf-8").replace(
            "    dependency_identity: str\n", "    dependency_fact: str\n", 1
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(contract, "RUNTIME_MODEL", runtime_model)
    violations = contract.check_shard_execution_contract()
    assert any(
        "Shard execution context" in item.message
        and "dependency_identity" in item.message
        for item in violations
    )


def test_standalone_shard_cli_rejects_parallel_runtime_or_service_ownership(
    monkeypatch, tmp_path: Path
) -> None:
    cli = tmp_path / "hashmarks_shard_execution.py"
    cli.write_text(
        contract.DIAGNOSTIC_CLI.read_text(encoding="utf-8")
        + "\nfrom oh_goon.services.runs import FleetPlanService\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(contract, "DIAGNOSTIC_CLI", cli)
    violations = contract.check_shard_execution_contract()
    assert any("second canonical Run owner" in item.message for item in violations)


def test_private_runtime_local_rename_does_not_change_architecture(
    monkeypatch, tmp_path: Path
) -> None:
    import re

    runtime = tmp_path / "shards.py"
    runtime.write_text(
        re.sub(
            r"\bremaining\b",
            "available_window",
            contract.RUNTIME.read_text(encoding="utf-8"),
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(contract, "RUNTIME", runtime)
    assert contract.check_shard_execution_contract() == []


def test_runtime_shard_schema_owner_value_change_needs_no_architecture_literal_edit(
    monkeypatch, tmp_path: Path
) -> None:
    owner = tmp_path / "shard_models.py"
    owner.write_text(
        contract.RUNTIME_MODEL_OWNER.read_text(encoding="utf-8").replace(
            'EXECUTION_CONTRACT_VERSION = "oh-goon.shard-execution/v2"',
            'EXECUTION_CONTRACT_VERSION = "oh-goon.shard-execution/v99"',
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(contract, "RUNTIME_MODEL_OWNER", owner)
    monkeypatch.setattr(contract, "RUNTIME_MODEL", owner)
    monkeypatch.setattr(contract, "SHARD_SCHEMA_SCAN_ROOT", tmp_path)

    assert contract.check_shard_execution_contract() == []


def _canonical_schema_value() -> str:
    fields = contract._class_fields(
        contract.CANONICAL_MODEL, "CanonicalShardExecutionEvidence"
    )
    return (
        contract._literal_schema_value(
            contract.CANONICAL_MODEL, fields["contract_schema"][0]
        )
        or ""
    )


def test_canonical_shard_schema_owner_value_change_needs_no_architecture_literal_edit(
    monkeypatch, tmp_path: Path
) -> None:
    current = _canonical_schema_value()
    assert current
    future = current.rsplit("/v", 1)[0] + "/v999"
    owner = tmp_path / "shard_execution.py"
    owner.write_text(
        contract.CANONICAL_MODEL.read_text(encoding="utf-8").replace(current, future),
        encoding="utf-8",
    )
    monkeypatch.setattr(contract, "CANONICAL_MODEL", owner)

    assert contract.check_shard_execution_contract() == []


def test_canonical_shard_schema_owner_requires_fixed_matching_literal(
    monkeypatch, tmp_path: Path
) -> None:
    current = _canonical_schema_value()
    owner = tmp_path / "shard_execution.py"
    owner.write_text(
        contract.CANONICAL_MODEL.read_text(encoding="utf-8").replace(
            f'contract_schema: Literal["{current}"]',
            "contract_schema: str",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(contract, "CANONICAL_MODEL", owner)

    violations = contract.check_shard_execution_contract()
    assert any(
        "contract_schema must be one fixed model-owned string literal" in item.message
        for item in violations
    )


def test_runtime_schema_owner_representation_is_refactor_tolerant(
    monkeypatch, tmp_path: Path
) -> None:
    inspected = tmp_path / "shard_models.py"
    text = contract.RUNTIME_MODEL_OWNER.read_text(encoding="utf-8")
    for name, value in (
        ("EXECUTION_CONTRACT_VERSION", "oh-goon.shard-execution/v2"),
        ("PASS_EVIDENCE_SCHEMA", "oh-goon.shard-pass-evidence/v3"),
        ("REFINEMENT_EVIDENCE_SCHEMA", "oh-goon.shard-refinement-evidence/v2"),
    ):
        text = text.replace(
            f'{name} = "{value}"',
            f"{name}: str = '{value}'",
        )
    inspected.write_text(text, encoding="utf-8")
    monkeypatch.setattr(contract, "RUNTIME_MODEL", inspected)

    assert contract.check_shard_execution_contract() == []


def test_runtime_schema_owner_rejects_wrong_assignment_even_with_comment_decoy(
    monkeypatch, tmp_path: Path
) -> None:
    inspected = tmp_path / "shard_models.py"
    expected_line = 'PASS_EVIDENCE_SCHEMA = "oh-goon.shard-pass-evidence/v3"'
    inspected.write_text(
        contract.RUNTIME_MODEL_OWNER.read_text(encoding="utf-8").replace(
            expected_line,
            'PASS_EVIDENCE_SCHEMA = "oh-goon.shard-pass-evidence/wrong"',
        )
        + f"\n# {expected_line}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(contract, "RUNTIME_MODEL", inspected)

    violations = contract.check_shard_execution_contract()
    assert any(
        "consume the runtime shard-model owner values" in item.message
        for item in violations
    )


def test_runtime_schema_constants_reject_second_assignment_owner(
    monkeypatch, tmp_path: Path
) -> None:
    duplicate = tmp_path / "parallel_owner.py"
    duplicate.write_text(
        'PASS_EVIDENCE_SCHEMA = "oh-goon.shard-pass-evidence/v3"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(contract, "SHARD_SCHEMA_SCAN_ROOT", tmp_path)

    violations = contract.check_shard_execution_contract()
    assert any("must have one runtime owner" in item.message for item in violations)


def test_fleet_shard_evidence_annotation_alias_is_refactor_tolerant(
    monkeypatch, tmp_path: Path
) -> None:
    evidence = tmp_path / "fleet_plan_evidence.py"
    text = contract.FLEET_EVIDENCE.read_text(encoding="utf-8")
    text = text.replace(
        "from oh_goon.core.models.shard_execution import CanonicalShardExecutionEvidence",
        "from oh_goon.core.models.shard_execution import CanonicalShardExecutionEvidence as ShardEvidence",
    ).replace(
        "shard_execution: CanonicalShardExecutionEvidence | None = None",
        "shard_execution: ShardEvidence | None = None",
    )
    evidence.write_text(text, encoding="utf-8")
    monkeypatch.setattr(contract, "FLEET_EVIDENCE", evidence)

    assert contract.check_shard_execution_contract() == []


def test_fleet_shard_evidence_relationship_cannot_be_removed(
    monkeypatch, tmp_path: Path
) -> None:
    evidence = tmp_path / "fleet_plan_evidence.py"
    evidence.write_text(
        contract.FLEET_EVIDENCE.read_text(encoding="utf-8").replace(
            "shard_execution: CanonicalShardExecutionEvidence | None = None",
            "shard_execution: object | None = None",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(contract, "FLEET_EVIDENCE", evidence)

    violations = contract.check_shard_execution_contract()
    assert any(
        "must structurally carry canonical shard execution evidence" in item.message
        for item in violations
    )


def test_projection_expression_refactor_does_not_change_architecture(
    monkeypatch, tmp_path: Path
) -> None:
    projection = tmp_path / "game_tape_projection.py"
    text = contract.GAME_TAPE_PROJECTION.read_text(encoding="utf-8")
    text = (
        text.replace(
            "    return GameTapeVerificationExecutionBoundary(\n",
            "    plan_identity = evidence.plan_identity\n"
            "    policy_identity = evidence.execution_policy_identity\n"
            "    return GameTapeVerificationExecutionBoundary(\n",
            1,
        )
        .replace(
            "execution_plan_identity=evidence.plan_identity",
            "execution_plan_identity=plan_identity",
            1,
        )
        .replace(
            "execution_policy_identity=evidence.execution_policy_identity",
            "execution_policy_identity=policy_identity",
            1,
        )
    )
    projection.write_text(text, encoding="utf-8")
    monkeypatch.setattr(contract, "GAME_TAPE_PROJECTION", projection)

    assert contract.check_shard_execution_contract() == []
