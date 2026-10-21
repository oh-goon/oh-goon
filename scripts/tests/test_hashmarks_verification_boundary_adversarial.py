from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture import (
    _hashmarks_verification_boundary_contract as contract,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _bind(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    package = tmp_path / "oh_goon"
    paths = {
        "selection": package / "integrations/hashmarks/verification_selection.py",
        "primitives": package
        / "integrations/hashmarks/verification_selection_primitives.py",
        "builder": package / "repository_tooling/hashmarks_verification_execution.py",
        "shard": package / "runtime/execution/shard_models.py",
        "member": package / "core/models/pytest_selection.py",
        "projector": package / "runtime/execution/pytest_projection.py",
        "refinement": package / "runtime/execution/_shard_refinement.py",
        "offline": package / "runtime/execution/offline_proof.py",
        "terminal": package / "runtime/execution/terminal_attestation.py",
    }
    monkeypatch.setattr(contract, "BACKEND_PACKAGE_ROOT", package)
    for attr, key in (
        ("_SELECTION", "selection"),
        ("_SELECTION_PRIMITIVES", "primitives"),
        ("_BUILDER", "builder"),
        ("_SHARD_MODEL", "shard"),
        ("_PYTEST_MEMBER", "member"),
        ("_PYTEST_PROJECTOR", "projector"),
        ("_REFINEMENT", "refinement"),
        ("_OFFLINE_PROOF", "offline"),
        ("_TERMINAL_ATTESTATION", "terminal"),
    ):
        monkeypatch.setattr(contract, attr, paths[key])
    return paths


def test_hashmarks_boundary_reports_missing_primary_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind(monkeypatch, tmp_path)
    issues = contract.check_hashmarks_verification_boundary_contract()
    text = "\n".join(issue.message for issue in issues)
    assert "adapter/primitives are missing" in text
    assert "execution-plan builder is missing" in text
    assert "shard execution evidence model is missing" in text
    assert text.count("structured pytest execution boundary is missing") == 3
    assert text.count("offline proof verification owner is missing") == 2


def test_selection_boundary_rejects_schema_layout_runtime_and_missing_structure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _bind(monkeypatch, tmp_path)
    _write(
        p["selection"],
        "import socket\n"
        "import oh_goon.runtime.workspaces\n"
        "from dataclasses import dataclass\n"
        "@dataclass\n"
        "class VerifiedHashmarksVerificationSelection:\n"
        "    repository_identity: str\n",
    )
    _write(
        p["primitives"],
        "from hashmarks import validate_native_consumer_bundle\n"
        "DOWNSTREAM_CONTRACT_SCHEMA = 'x'\n"
        "layout = {'preferred_granularity': True}\n",
    )
    issues = contract._selection_violations()
    text = "\n".join(issue.message for issue in issues)
    assert "must not mirror Hashmarks native schema constants" in text
    assert "must not consume Hashmarks layout advisory 'preferred_granularity'" in text
    assert "must not import runtime/control-plane authority" in text
    assert "missing structural evidence fields" in text


def test_selection_boundary_is_invariant_to_private_adapter_class_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _bind(monkeypatch, tmp_path)
    _write(
        p["selection"],
        "from dataclasses import dataclass\n"
        "@dataclass\n"
        "class AnythingPrivate:\n"
        "    producer_implementation_identity: str\n"
        "    repository_identity: str\n"
        "    source_identity: str\n"
        "    repository_provenance_identity: str\n"
        "    membership_identity: str\n"
        "    producer_contract_identity: str\n"
        "    members: tuple[object, ...]\n",
    )
    _write(
        p["primitives"],
        "from hashmarks import validate_native_consumer_bundle\n",
    )
    issues = contract._selection_violations()
    assert not any(
        "missing structural evidence fields" in issue.message for issue in issues
    )


def test_builder_boundary_rejects_execution_imports_and_hashmarks_layout_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _bind(monkeypatch, tmp_path)
    _write(
        p["builder"],
        "import concurrent.futures\nimport subprocess\n"
        "import oh_goon.runtime.execution.shards\n"
        "layout = {'may_regroup': True}\n",
    )
    issues = contract._builder_violations()
    text = "\n".join(issue.message for issue in issues)
    assert "must not consume Hashmarks layout advisory 'may_regroup'" in text
    assert "must not import execution authority" in text
    assert "concurrent.futures" in text
    assert "subprocess" in text
    assert "oh_goon.runtime.execution.shards" in text


def test_builder_boundary_is_invariant_to_private_helper_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _bind(monkeypatch, tmp_path)
    first = _write(p["builder"], "def private_builder_name():\n    pass\n")
    first_issues = contract._builder_violations()
    first.write_text(
        first.read_text().replace("private_builder_name", "whatever_private_name"),
        encoding="utf-8",
    )
    second_issues = contract._builder_violations()
    assert [issue.message for issue in first_issues] == [
        issue.message for issue in second_issues
    ]


def test_shard_model_requires_structural_verification_evidence_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _bind(monkeypatch, tmp_path)
    _write(
        p["shard"],
        "from dataclasses import dataclass\n"
        "@dataclass\n"
        "class AnythingPlanLike:\n"
        "    verification_selection_evidence_identity: str\n"
        "@dataclass\n"
        "class AnythingReportLike:\n"
        "    verification_selection_evidence_identity: str\n",
    )
    issues = contract._shard_model_violations()
    text = "\n".join(issue.message for issue in issues)
    assert "execution-plan evidence model is missing" in text
    assert "execution-report evidence model is missing" in text


def test_shard_model_contract_is_invariant_to_private_class_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _bind(monkeypatch, tmp_path)
    shared_fields = (
        "producer_implementation_identity",
        "producer_contract_identity",
        "verification_selection_evidence_identity",
        "selected_membership_identity",
        "selected_test_nodes",
        "unproven_owner_members",
    )
    plan_fields = (*shared_fields, "shards")
    report_fields = (
        *shared_fields,
        "results",
        "actual_executed_membership_identity",
        "actual_executed_members",
        "membership_complete",
    )
    body = ["from dataclasses import dataclass"]
    for name, fields in (("PrivateA", plan_fields), ("PrivateB", report_fields)):
        body.extend(["@dataclass", f"class {name}:"])
        body.extend(f"    {field}: object" for field in fields)
    _write(p["shard"], "\n".join(body) + "\n")
    assert contract._shard_model_violations() == []

    p["shard"].write_text(
        p["shard"]
        .read_text(encoding="utf-8")
        .replace("PrivateA", "RenamedA")
        .replace("PrivateB", "RenamedB"),
        encoding="utf-8",
    )
    assert contract._shard_model_violations() == []


def test_structured_pytest_boundary_requires_structured_model_and_import_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _bind(monkeypatch, tmp_path)
    _write(
        p["member"],
        "from dataclasses import dataclass\n"
        "@dataclass\n"
        "class AnyStructuredMember:\n"
        "    path: str\n"
        "    test_symbol: str | None\n"
        "    member_id: str\n",
    )
    _write(p["projector"], "def project_pytest_argv():\n    return ()\n")
    _write(p["refinement"], "def refine_children():\n    return ()\n")
    issues = contract._structured_pytest_projection_violations()
    text = "\n".join(issue.message for issue in issues)
    assert (
        "structured pytest member model is missing required execution evidence fields"
        in text
    )
    assert "must consume the structured pytest model owner" in text
    assert "must retain the structured pytest projection boundary" in text


def test_structured_pytest_boundary_is_invariant_to_private_model_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _bind(monkeypatch, tmp_path)
    _write(
        p["member"],
        "from dataclasses import dataclass\n"
        "@dataclass\n"
        "class AnythingPrivate:\n"
        "    path: str\n"
        "    test_symbol: str | None\n"
        "    member_id: str\n"
        "    node_components: tuple[str, ...]\n",
    )
    _write(
        p["projector"],
        "from oh_goon.core.models.pytest_selection import AnythingPrivate\n",
    )
    _write(
        p["refinement"],
        "import oh_goon.core.models.pytest_selection\n"
        "import oh_goon.runtime.execution.pytest_projection\n",
    )
    assert contract._structured_pytest_projection_violations() == []


def test_structured_pytest_behavior_is_owned_by_executable_tests_not_error_message_spelling() -> (
    None
):
    source = Path(contract.__file__).read_text(encoding="utf-8")
    for private_behavior_spelling in (
        "must already be lexically normalized",
        "must not embed a pytest node separator",
        "duplicate executable projections",
        "duplicate projection identities",
        "conserve admitted verification membership exactly",
    ):
        assert private_behavior_spelling not in source


def test_offline_proof_boundary_rejects_execution_imports_and_production_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _bind(monkeypatch, tmp_path)
    _write(p["offline"], "import oh_goon.runtime.execution.shards\n")
    _write(p["terminal"], "import subprocess\n")
    consumer = tmp_path / "oh_goon" / "services" / "consumer.py"
    _write(
        consumer,
        "import oh_goon.runtime.execution.offline_proof\n"
        "import oh_goon.runtime.execution.terminal_attestation\n",
    )
    issues = contract._proof_reuse_non_authority_violations()
    text = "\n".join(issue.message for issue in issues)
    assert (
        "offline proof verification must not import execution/reuse authority" in text
    )
    assert "oh_goon.runtime.execution.shards" in text
    assert "subprocess" in text
    assert "offline shard-proof verification cannot be imported" in text
    assert "terminal-attestation verification cannot be imported" in text
