from __future__ import annotations

import json

import shutil

import pytest
from pathlib import Path

from oh_goon.repository_tooling.self_certification_plan import (
    record_source_seal,
    record_unit_pass,
    scripts_non_certification_shards,
    self_certification_plan,
    shard_plan,
)


def _root() -> Path:
    return Path(__file__).resolve().parents[4]


@pytest.fixture
def archive_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from oh_goon.repository_tooling import self_certification_artifacts

    metadata = tmp_path / "checkpoint-parent.json"
    metadata.write_text(
        json.dumps({
            "schema": 1,
            "version": "0.9.0",
            "artifact": "oh-goon-0.9.0.zip",
            "sha256": "0" * 64,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(self_certification_artifacts, "PARENT_METADATA", metadata)
    return _root()


def test_scripts_shards_are_precomputed_complete_deterministic_and_bounded() -> None:
    root = _root()
    first = scripts_non_certification_shards(root)
    second = scripts_non_certification_shards(root)
    assert first == second
    members = [member for shard in first for member in shard.members]
    expected = [
        path.relative_to(root).as_posix()
        for path in sorted((root / "scripts" / "tests").glob("test_*.py"))
    ]
    assert members == expected
    assert len(first) == (len(expected) + 15) // 16
    assert len(set(members)) == len(expected)
    assert all(len(shard.members) <= 16 for shard in first)
    assert all(
        shard.command[:7]
        == [
            "backend/.venv/bin/python",
            "-m",
            "pytest",
            "-c",
            "backend/pyproject.toml",
            "-m",
            "not certification",
        ]
        for shard in first
    )
    assert all(shard.command[7] == shard.members[0] for shard in first)


def test_shard_plan_exposes_pre_execution_budget_and_commands() -> None:
    payload = shard_plan(_root())
    assert payload["assigned"] == payload["test_count"]
    assert payload["duplicates"] == 0
    assert payload["missing"] == 0
    assert len(payload["shards"]) == len(scripts_non_certification_shards(_root()))
    assert {shard["execution_budget_seconds"] for shard in payload["shards"]} == {240}
    assert all(shard["command"] for shard in payload["shards"])


def test_plan_has_disjoint_bounded_source_work_and_blocks_seal_until_pass(tmp_path, monkeypatch, archive_checkout) -> None:
    root = archive_checkout
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    payload = self_certification_plan(root)
    names = [unit["unit"] for unit in payload["pending"]]
    assert names == ["goon-static-admission"]
    blocked_source = [
        unit["unit"] for unit in payload["blocked"] if unit.get("phase") == "source"
    ]
    script_units = [shard.unit_name for shard in scripts_non_certification_shards(root)]
    assert blocked_source == [
        "integration",
        "certification-lifecycle-adversarial",
        "certification-lifecycle-production",
        "certification-lifecycle-reconstruction",
        "architecture",
        "generated",
        "repository-understanding",
        "compile",
        "shell",
        *script_units,
    ]
    gates = {gate["gate"]: gate for gate in payload["source_gates"]}
    assert gates["release"]["evidence_units"] == script_units
    assert gates["scripts"]["evidence_units"] == script_units
    assert gates["QA"]["evidence_units"] == ["generated", "repository-understanding", *script_units]
    assert next(unit for unit in payload["blocked"] if unit["unit"] == "seal-source")
    assert payload["source_seal"]["status"] == "BLOCKED"
    assert {gate["gate"] for gate in payload["source_gates"]} == {
        "admission",
        "certification-realism",
        "integration",
        "release",
        "architecture",
        "QA",
        "compile",
        "shell",
        "scripts",
    }
    assert all("parallel_group" not in unit for unit in payload["all_units"])


def test_static_goon_admission_unblocks_expensive_source_units(tmp_path, monkeypatch, archive_checkout) -> None:
    root = archive_checkout
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    first = self_certification_plan(root)
    static = next(unit for unit in first["all_units"] if unit["unit"] == "goon-static-admission")
    record_unit_pass(
        root,
        first["source_identity"],
        first["verification_config_identity"],
        static,
    )
    resumed = self_certification_plan(root)
    assert next(unit for unit in resumed["all_units"] if unit["unit"] == "goon-static-admission")["status"] == "PASS"
    assert [unit["unit"] for unit in resumed["pending"]][:4] == [
        "integration",
        "certification-lifecycle-adversarial",
        "certification-lifecycle-production",
        "certification-lifecycle-reconstruction",
    ]
    assert all(
        unit["status"] == "PENDING"
        for unit in resumed["all_units"]
        if unit.get("phase") == "source" and unit["unit"] != "goon-static-admission"
    )


def test_all_source_pass_evidence_makes_seal_the_only_pending_transition(tmp_path, monkeypatch, archive_checkout) -> None:
    root = archive_checkout
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    first = self_certification_plan(root)
    source_units = [unit for unit in first["all_units"] if unit.get("phase") == "source"]
    for unit in source_units:
        record_unit_pass(
            root,
            first["source_identity"],
            first["verification_config_identity"],
            unit,
        )
    resumed = self_certification_plan(root)
    assert resumed["source_verification_status"] == "PASS"
    assert [unit["unit"] for unit in resumed["pending"]] == ["seal-source"]
    assert {unit["unit"] for unit in resumed["blocked"]} == {
        "package/source-zip",
        "package/verify-source-identity",
        "package/fresh-zip-acceptance",
        "package/cumulative-patch",
        "package/verify-patch-replay",
        "package/handoff",
        "package/sidecars",
        "package/manifest",
        "package/persist",
        "package/verify-persistence",
    }
    assert all(gate["status"] == "PASS" for gate in resumed["source_gates"])


def test_source_seal_binds_exact_current_evidence_set(tmp_path, monkeypatch, archive_checkout) -> None:
    root = archive_checkout
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    first = self_certification_plan(root)
    source_units = [unit for unit in first["all_units"] if unit.get("phase") == "source"]
    for unit in source_units:
        record_unit_pass(
            root,
            first["source_identity"],
            first["verification_config_identity"],
            unit,
        )
    ready = self_certification_plan(root)
    record_source_seal(
        root,
        ready["source_identity"],
        ready["verification_config_identity"],
        ready["plan_identity"],
        source_units,
    )
    sealed = self_certification_plan(root)
    assert sealed["status"] == "SEALED"
    assert sealed["source_seal"]["status"] == "SEALED"
    assert [unit["unit"] for unit in sealed["pending"]] == ["package/source-zip"]
    assert sealed["source_seal"]["seal_identity"].startswith("sha256:")


def test_evidence_reuse_is_identity_bound_not_extraction_path(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source-a"
    other = tmp_path / "source-b"
    (source / "scripts/tests").mkdir(parents=True)
    (source / "scripts/tests/test_one.py").write_text("def test_one():\n    assert True\n")
    (source / "backend").mkdir()
    (source / "backend/pyproject.toml").write_text("[project]\nname='x'\nversion='1.0.0'\n")
    (source / "backend/uv.lock").write_text('version = 1\nrequires-python = ">=3.12"\npackage = []\n')
    (source / "generated/release").mkdir(parents=True)
    (source / "generated/release/checkpoint-parent.json").write_text(
        '{"schema":1,"version":"0.9.0","artifact":"oh-goon-0.9.0.zip","sha256":"' + "0" * 64 + '"}\n'
    )
    (source / "frontend").mkdir()
    (source / "frontend/package-lock.json").write_text("{}\n")
    shutil.copytree(source, other)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    first = self_certification_plan(source)
    record_unit_pass(
        source,
        first["source_identity"],
        first["verification_config_identity"],
        first["all_units"][0],
    )
    resumed = self_certification_plan(other)
    assert resumed["all_units"][0]["status"] == "PASS"


@pytest.mark.slow
def test_packaging_after_seal_runs_artifact_units_only(tmp_path, monkeypatch, archive_checkout) -> None:
    from oh_goon.repository_tooling import self_certification_plan as module

    root = archive_checkout
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    first = self_certification_plan(root)
    source_units = [unit for unit in first["all_units"] if unit.get("phase") == "source"]
    for unit in source_units:
        record_unit_pass(
            root,
            first["source_identity"],
            first["verification_config_identity"],
            unit,
        )
    assert module.execute_artifact_transition(root, "seal-source") == 0

    assert module.execute_artifact_transition(root, "package/source-zip") == 0
    after_build = self_certification_plan(root)
    assert after_build["source_verification_status"] == "PASS"
    assert [unit["unit"] for unit in after_build["pending"]] == ["package/verify-source-identity"]
    assert module.execute_artifact_transition(root, "package/verify-source-identity") == 0
    verified = self_certification_plan(root)
    assert verified["artifact_status"] == "SOURCE_IDENTITY_VERIFIED"
    assert [unit["unit"] for unit in verified["pending"]] == ["package/fresh-zip-acceptance"]
    assert verified["required_inputs"][0]["status"] == "MISSING"
    assert module.execute_artifact_transition(root, "package/fresh-zip-acceptance") == 0
    waiting = self_certification_plan(root)
    assert waiting["status"] == "WAITING_FOR_INPUT"
    assert waiting["pending"] == []
    assert waiting["required_inputs"][0]["status"] == "MISSING"
    patch = next(unit for unit in waiting["all_units"] if unit["unit"] == "package/cumulative-patch")
    assert patch["status"] == "BLOCKED"


def test_artifact_transition_cannot_execute_source_verification_unit(tmp_path: Path, monkeypatch, archive_checkout) -> None:
    from oh_goon.repository_tooling import self_certification_plan as module

    root = archive_checkout
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    try:
        module.execute_artifact_transition(root, "goon-static-admission")
    except ValueError as exc:
        assert "only through the canonical Fleet Plan" in str(exc)
    else:
        raise AssertionError("artifact transition helper must not execute source verification work")


def test_source_seal_is_not_source_identity_and_toolchain_drift_invalidates_proof_without_source_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source"
    (source / "scripts/tests").mkdir(parents=True)
    (source / "scripts/tests/test_one.py").write_text("def test_one():\n    assert True\n")
    (source / "backend").mkdir()
    (source / "backend/pyproject.toml").write_text("[project]\nname='x'\nversion='1.0.0'\n")
    (source / "backend/uv.lock").write_text('version = 1\nrequires-python = ">=3.12"\npackage = []\n')
    (source / "generated/release").mkdir(parents=True)
    (source / "generated/release/checkpoint-parent.json").write_text(
        '{"schema":1,"version":"0.9.0","artifact":"oh-goon-0.9.0.zip","sha256":"' + "0" * 64 + '"}\n'
    )
    (source / "frontend").mkdir()
    (source / "frontend/package-lock.json").write_text("{}\n")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    first = self_certification_plan(source)
    source_units = [unit for unit in first["all_units"] if unit.get("phase") == "source"]
    for unit in source_units:
        record_unit_pass(
            source,
            first["source_identity"],
            first["verification_config_identity"],
            unit,
        )
    ready = self_certification_plan(source)
    record_source_seal(
        source,
        ready["source_identity"],
        ready["verification_config_identity"],
        ready["plan_identity"],
        source_units,
    )
    sealed = self_certification_plan(source)
    assert sealed["status"] == "SEALED"
    assert sealed["source_seal"]["seal_identity"] != sealed["source_identity"]

    # backend/.venv is local toolchain state, not canonical source. Introducing the
    # resolved executable therefore changes proof-input identity without changing source.
    python = source / "backend/.venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"resolved-local-toolchain-v2")
    drifted = self_certification_plan(source)
    assert drifted["source_identity"] == sealed["source_identity"]
    assert drifted["verification_config_identity"] != sealed["verification_config_identity"]
    assert drifted["source_seal"]["status"] == "BLOCKED"
    assert drifted["source_verification_status"] == "INCOMPLETE"


def test_resume_reports_prior_pass_as_invalidated_when_toolchain_identity_changes(tmp_path, monkeypatch, archive_checkout) -> None:
    from oh_goon.repository_tooling import _self_certification_plan_units as units

    root = archive_checkout
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    first = self_certification_plan(root)
    static = next(unit for unit in first["all_units"] if unit["unit"] == "goon-static-admission")
    record_unit_pass(root, first["source_identity"], first["verification_config_identity"], static)

    original = units._toolchain_identity
    monkeypatch.setattr(
        units,
        "_toolchain_identity",
        lambda current_root, unit: (
            "sha256:" + "f" * 64
            if unit["unit"] == "goon-static-admission"
            else original(current_root, unit)
        ),
    )
    resumed = self_certification_plan(root)
    changed = next(unit for unit in resumed["all_units"] if unit["unit"] == "goon-static-admission")
    assert changed["status"] == "PENDING"
    assert changed["resume_state"] == "INVALIDATED"
    assert changed["resume_invalidation_reason"] == "verification-inputs-changed"
    assert resumed["resume_evidence"]["proven"] == []
    assert resumed["resume_evidence"]["invalidated"] == [
        {"unit": "goon-static-admission", "reason": "verification-inputs-changed"}
    ]
    assert resumed["resume_evidence"]["failed"] == []


def test_resume_reports_tampered_current_pass_as_invalidated_not_proven(tmp_path, monkeypatch, archive_checkout) -> None:
    import json

    root = archive_checkout
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    first = self_certification_plan(root)
    static = next(unit for unit in first["all_units"] if unit["unit"] == "goon-static-admission")
    evidence_path = record_unit_pass(
        root, first["source_identity"], first["verification_config_identity"], static
    )
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    payload["evidence_identity"] = "sha256:" + "0" * 64
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")

    resumed = self_certification_plan(root)
    changed = next(unit for unit in resumed["all_units"] if unit["unit"] == "goon-static-admission")
    assert changed["status"] == "PENDING"
    assert changed["resume_state"] == "INVALIDATED"
    assert changed["resume_invalidation_reason"] == "current-evidence-invalid"
    assert resumed["resume_evidence"]["proven"] == []


def test_certification_realism_gate_requires_l2_l3_l4_evidence(tmp_path, monkeypatch, archive_checkout) -> None:
    root = archive_checkout
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    payload = self_certification_plan(root)
    units = {unit["unit"]: unit for unit in payload["all_units"]}
    assert units["certification-lifecycle-adversarial"]["evidence_level"] == "L2_ADVERSARIAL"
    assert units["certification-lifecycle-production"]["evidence_level"] == "L3_LIFECYCLE"
    reconstruction = units["certification-lifecycle-reconstruction"]
    assert reconstruction["evidence_level"] == "L4_RECONSTRUCTION"
    assert "backend/tests/integration/services/test_failure_recovery_resume_closure.py" in reconstruction["command"]
    gate = next(gate for gate in payload["source_gates"] if gate["gate"] == "certification-realism")
    assert gate["status"] == "PENDING"
    assert gate["evidence_levels"] == ["L2_ADVERSARIAL", "L3_LIFECYCLE", "L4_RECONSTRUCTION"]

    for name in (
        "goon-static-admission",
        "certification-lifecycle-adversarial",
        "certification-lifecycle-production",
        "certification-lifecycle-reconstruction",
    ):
        unit = units[name]
        record_unit_pass(
            root, payload["source_identity"], payload["verification_config_identity"], unit
        )
    resumed = self_certification_plan(root)
    gate = next(gate for gate in resumed["source_gates"] if gate["gate"] == "certification-realism")
    assert gate["status"] == "PASS"


def test_certification_integration_ownership_is_disjoint(tmp_path, monkeypatch, archive_checkout) -> None:
    root = archive_checkout
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    payload = self_certification_plan(root)
    units = {unit["unit"]: unit for unit in payload["all_units"]}

    broad = units["integration"]["command"]
    local_lifecycle = "backend/tests/integration/services/test_local_vertical_lifecycle.py"
    recovery = "backend/tests/integration/services/test_failure_recovery_resume_closure.py"
    assert broad[-4:] == ["--ignore", local_lifecycle, "--ignore", recovery]

    l3 = units["certification-lifecycle-production"]["command"]
    assert local_lifecycle in l3
    assert recovery not in l3
    assert l3[-2:] == [
        "-k",
        "not reconstructs_from_fresh_durable_owners and not process_restart_recovery",
    ]

    l4 = units["certification-lifecycle-reconstruction"]["command"]
    assert local_lifecycle in l4
    assert recovery in l4
    assert l4[-2:] == [
        "-k",
        "reconstructs_from_fresh_durable_owners or process_restart_recovery or failure_recovery_resume_closure",
    ]


def test_unit_evidence_records_certification_level(tmp_path, monkeypatch, archive_checkout) -> None:
    root = archive_checkout
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    payload = self_certification_plan(root)
    unit = next(
        unit for unit in payload["all_units"]
        if unit["unit"] == "certification-lifecycle-reconstruction"
    )
    path = record_unit_pass(
        root, payload["source_identity"], payload["verification_config_identity"], unit
    )
    evidence = json.loads(path.read_text(encoding="utf-8"))
    assert evidence["evidence_level"] == "L4_RECONSTRUCTION"
