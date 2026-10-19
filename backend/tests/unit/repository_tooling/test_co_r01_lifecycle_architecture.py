"""CO-R01 negative mutations of *actual owner call edges*, not source tokens."""

from __future__ import annotations

from pathlib import Path
from shutil import copy2

import pytest

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    _API,
    _EVIDENCE,
    _EXECUTOR,
    _FLEET,
    _JOURNAL,
    _LIFECYCLE,
    _SCHEDULE,
    _SCHEDULE_STORE,
    _SELECTOR,
    _WORKSPACE,
    check_co_r01_lifecycle_edges,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT

# Complete current source and witness inputs; no archive markers activate checks.
_FILES = (
    "backend/src/oh_goon/api/routes/fleet_runs.py",
    "backend/src/oh_goon/integrations/_fleet_plan_cgroup_resources.py",
    "backend/src/oh_goon/integrations/_fleet_plan_nested_cgroup_launcher.py",
    "backend/src/oh_goon/integrations/_fleet_plan_restricted_egress.py",
    "backend/src/oh_goon/integrations/fleet_plan_bubblewrap_isolation.py",
    "backend/src/oh_goon/integrations/fleet_plan_local_lace_native_reaper.py",
    "backend/src/oh_goon/integrations/fleet_plan_process_isolation.py",
    "backend/src/oh_goon/integrations/fleet_plan_run_execution.py",
    "backend/src/oh_goon/integrations/linux_physical_observer.py",
    "backend/src/oh_goon/runtime/enforcer_lace_fleet_resource_owner.py",
    "backend/src/oh_goon/runtime/execution_evidence_acceptance.py",
    "backend/src/oh_goon/runtime/journal.py",
    "backend/src/oh_goon/runtime/process_group.py",
    "backend/src/oh_goon/runtime/schedule_executions.py",
    "backend/src/oh_goon/runtime/workspaces.py",
    "backend/src/oh_goon/services/fleet_run_control_plane.py",
    "backend/src/oh_goon/services/fleet_run_lifecycle.py",
    "backend/src/oh_goon/services/schedule_execution.py",
    "backend/tests/native/test_co_r3n_native_cgroup_v2_witness.py",
    "backend/tests/native/test_co_r3r_real_visible_holder.py",
    "backend/tests/unit/integrations/test_co_r3d_original_cgroup_quiescence.py",
    "backend/tests/unit/integrations/test_co_r3e_durable_cgroup_recovery.py",
    "backend/tests/unit/integrations/test_co_r3f_cgroup_native_handle.py",
    "backend/tests/unit/integrations/test_co_r3g_bound_observation_removal.py",
    "backend/tests/unit/integrations/test_co_r3h_original_inode_retirement.py",
    "backend/tests/unit/integrations/test_co_r3i_original_lace_cgroup_custody.py",
    "backend/tests/unit/integrations/test_co_r3j_original_parent_lineage.py",
    "backend/tests/unit/integrations/test_co_r3k_prework_identity_rollback.py",
    "backend/tests/unit/integrations/test_co_r3l_bound_nested_attachment.py",
    "backend/tests/unit/integrations/test_co_r3m_pending_cgroup_allocation.py",
    "backend/tests/unit/repository_tooling/test_co_r3b_original_failure_architecture.py",
    "backend/tests/unit/repository_tooling/test_co_r3c_original_quiescence_architecture.py",
    "backend/tests/unit/repository_tooling/test_co_r3d_original_cgroup_architecture.py",
    "backend/tests/unit/repository_tooling/test_co_r3e_original_recovery_architecture.py",
    "backend/tests/unit/repository_tooling/test_co_r3f_handle_architecture.py",
    "backend/tests/unit/repository_tooling/test_co_r3g_bound_observation_architecture.py",
    "backend/tests/unit/repository_tooling/test_co_r3h_inode_retirement_architecture.py",
    "backend/tests/unit/repository_tooling/test_co_r3i_original_lace_custody_architecture.py",
    "backend/tests/unit/repository_tooling/test_co_r3j_parent_lineage_architecture.py",
    "backend/tests/unit/repository_tooling/test_co_r3k_prework_architecture.py",
    "backend/tests/unit/repository_tooling/test_co_r3l_bound_attachment_architecture.py",
    "backend/tests/unit/repository_tooling/test_co_r3m_pending_allocation_architecture.py",
    "backend/tests/unit/repository_tooling/test_co_r3n_native_cgroup_qualification.py",
    "backend/tests/unit/repository_tooling/test_co_r3o_native_witness_contract.py",
    "backend/tests/unit/repository_tooling/test_co_r3p_original_effect_epoch_architecture.py",
    "backend/tests/unit/repository_tooling/test_co_r3q_effect_architecture.py",
    "backend/tests/unit/repository_tooling/test_co_r3r_observer_architecture.py",
    "backend/tests/unit/repository_tooling/test_co_r3r_visible_holder_race.py",
    "backend/tests/unit/repository_tooling/test_co_r3s_architecture.py",
    "backend/tests/unit/repository_tooling/test_co_r3s_pinned_cgroup_observation.py",
    "backend/tests/unit/runtime/test_co_r3_native_provider_conformance.py",
    "backend/tests/unit/runtime/test_co_r3b_original_failure_cuts.py",
    "backend/tests/unit/runtime/test_co_r3c_trust_writer_quiescence.py",
    "backend/tests/unit/services/test_co_r3p_original_fleet_effect_revocation.py",
    "backend/tests/unit/services/test_co_r3q_effect_revalidation.py",
    "scripts/qualify_b12_development_wsl.sh",
)


def _fixture(tmp_path: Path) -> Path:
    for relative in _FILES:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy2(ROOT / relative, destination)
    return tmp_path


def test_original_co_r01_owner_edges_pass(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    assert not (root / ".checkpoint").exists()
    assert check_co_r01_lifecycle_edges(root) == []


@pytest.mark.parametrize(
    ("path", "old", "new"),
    [
        (_API, "return control_plane.start(", "return control_plane._lifecycle.admit("),
        (
            _JOURNAL,
            "ScheduleExecutionStore(self._database).bind_admitted_child_in(",
            "ScheduleExecutionStore(self._database).fake_bind(",
        ),
        (
            _SCHEDULE_STORE,
            "self._update(\n            connection,\n            _with_record_version",
            "self._wrong_update(\n            connection,\n            _with_record_version",
        ),
        (
            _LIFECYCLE,
            "schedule_run_id=schedule_run_id,\n                execution=DirectFleetExecutionAdmission(",
            "execution=DirectFleetExecutionAdmission(",
        ),
        (_SCHEDULE, "self._runs.start_bound(", "self._runs._lifecycle.admit("),
        (_FLEET, "with self._lifecycle.serialize_admission(", "with nullcontext("),
        (
            _FLEET,
            "require_scheduled_child()\n            require_target_caller_file_fresh",
            "require_target_caller_file_fresh",
        ),
        (_FLEET, "self._lifecycle.admit(", "self._other_lifecycle.admit("),
        (
            _WORKSPACE,
            'verify_clean_source_snapshot(binding, label="staged READ_SNAPSHOT")',
            "pass  # staged snapshot no longer verified",
        ),
        (_WORKSPACE, "staging.replace(destination)", "staging.rename(destination)"),
        (_EVIDENCE, "self._validate_durable_rows(\n", "self._validate_cached_rows(\n"),
        (_EXECUTOR, "select_bound_process_isolation_backend(\n", "self._first_backend(\n"),
        (_SELECTOR, "if isolation_backend_evidence(backend) != admitted:", "if False:"),
        (_SELECTOR, "if not backend.capabilities.satisfies(requirements):", "if False:"),
        (_SELECTOR, "backend.require_available(plan)", "pass  # availability bypass"),
    ],
)
def test_mutated_owner_call_edge_fails_before_qualification(tmp_path, path, old, new):
    root = _fixture(tmp_path)
    target = root / path
    text = target.read_text(encoding="utf-8")
    assert text.count(old) == 1, (path, old)
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    assert any(v.path == target for v in check_co_r01_lifecycle_edges(root))


def test_missing_original_owner_fails_closed(tmp_path):
    root = _fixture(tmp_path)
    (root / _FLEET).unlink()
    assert any("cannot be inspected" in v.message for v in check_co_r01_lifecycle_edges(root))


def test_checker_is_registered_in_original_architecture_gate():
    from oh_goon.repository_tooling.check_architecture import CHECKS

    assert check_co_r01_lifecycle_edges in CHECKS
