from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_historical_roadmap_numbers_cannot_masquerade_as_future_checkpoints() -> None:
    text = (ROOT / "docs/HACKER_HOCKEY_THEME.md").read_text(encoding="utf-8")
    assert "Historical roadmap numbering." in text
    assert "future Oh-Goon checkpoint/version assignments" in text
    assert "## Historical design sequence 913–922" in text
    assert "## Historical design sequence 923–932" in text
    assert "### Phase 913 —" not in text
    assert "### Phase 923 —" not in text

    # These are historical design records precisely because the capabilities landed.
    kubernetes = (
        ROOT / "backend/src/oh_goon/core/models/fleet_plan_kubernetes_steps.py"
    ).read_text(encoding="utf-8")
    primitives = (
        ROOT / "backend/src/oh_goon/core/models/fleet_plan_step_primitives.py"
    ).read_text(encoding="utf-8")
    retry = (ROOT / "backend/src/oh_goon/core/fleet_plan_retry.py").read_text(
        encoding="utf-8"
    )
    assert "kubernetes_rollout_verify" in kubernetes
    assert "known_transient_exit_codes" in primitives
    assert "delays_seconds" in primitives
    assert "def " in retry


def test_browser_docs_distinguish_primary_steps_tab_from_cicd_flow_visualization() -> (
    None
):
    coverage = (ROOT / "docs/development/BROWSER_PRODUCT_COVERAGE.md").read_text(
        encoding="utf-8"
    )
    matrix = (ROOT / "docs/development/BROWSER_QUALITY_MATRIX.md").read_text(
        encoding="utf-8"
    )
    frontend = (ROOT / "frontend/README.md").read_text(encoding="utf-8")

    assert "Historical browser-evolution record." in coverage
    assert "historical feature 910" in coverage
    assert "Game Tape opens on the primary **Steps** review tab" in coverage
    assert "Steps (CI/CD Flow)" in matrix
    assert "Game Tape opens on the read-only **Steps** review tab" in frontend
    assert "Game Tape opens on **CI/CD Flow**" not in coverage


def test_development_docs_assign_quality_and_coverage_to_current_make_owners() -> None:
    runbook = (ROOT / "docs/development/APP_TEST_RUN.md").read_text(encoding="utf-8")
    privacy = (ROOT / "docs/PRIVACY.md").read_text(encoding="utf-8")

    assert "`make check` is the fast development feedback boundary" in runbook
    assert "`make quality-check` owns fresh Python branch coverage" in runbook
    assert (
        "`make check-backend` is the backend-local Ruff + coverage + ratchet diagnostic"
        in runbook
    )
    assert "`make check` owns stricter quality/lint/coverage gates" not in runbook
    assert "fast development architecture projection" in privacy


def test_command_owner_preserves_process_group_quiescence_checks() -> None:
    command = (ROOT / "backend/src/oh_goon/engine/command.py").read_text(
        encoding="utf-8"
    )

    assert "require_process_group_quiescence" in command
    assert "leader_exited_before_group_quiescence" in command
    assert 'reason_code="leader_exited_with_descendants"' in command


def test_executor_unavailable_message_points_to_explicit_repository_setup_path() -> (
    None
):
    from oh_goon.core.strict_executor_authority import EXECUTORS_FILE_ENV
    from oh_goon.services.certification_bootstrap import (
        certification_executor_unavailable,
    )

    message = certification_executor_unavailable("systemd user manager is unavailable")

    assert "systemd user manager is unavailable" in message
    assert f"configure {EXECUTORS_FILE_ENV} with a real executor" in message
    assert "Delegate=yes" in message
    assert "run `make strict-executor-setup` once" in message


def test_launch_ownership_inventory_cannot_present_phase_zero_gaps_as_current_truth() -> (
    None
):
    ownership = (ROOT / "docs/development/LAUNCH_OWNERSHIP.md").read_text(
        encoding="utf-8"
    )
    from oh_goon.repository_tooling.architecture.structural_launch_contract import (
        _check_launch_manifest_foundation,
        _check_runtime_authority_registry,
    )

    assert "historical baseline evidence" in ownership
    assert "## Historical Phase 0 entry paths" in ownership
    assert "## Historical Phase 0 responsibility map" in ownership
    assert "Historical snapshot:" in ownership
    assert (
        "| Responsibility | Phase 0 writer/owner | Phase 0 classification | Historical target consequence |"
        in ownership
    )
    assert "**Historical Phase 0 result:**" in ownership
    assert "**Historical 1267.0.37 status:**" in ownership
    assert "current runtime contract" in ownership
    assert "`CHECKPOINT_NOT_RELEASE.md`" in ownership

    # Current launch truth is semantic architecture, not comments/private helper
    # names retained only to satisfy historical documentation tests.
    assert _check_launch_manifest_foundation(ROOT) == []
    assert _check_runtime_authority_registry(ROOT) == []


def test_execution_authority_history_cannot_present_landed_atomic_handoff_as_next_work() -> (
    None
):
    authority = (ROOT / "docs/development/EXECUTION_AUTHORITY_CLOSURE.md").read_text(
        encoding="utf-8"
    )
    assert "**Historical 869 status:**" in authority
    assert "subsequently landed in 870/873" in authority
    assert (
        "full symlink/PATH/interpreter/descriptor TOCTOU closure is the next phase"
        not in authority
    )
    assert "## 870 atomic verification-to-exec closure" in authority


def test_active_launch_exam_questions_are_time_scoped_not_stale_current_status() -> (
    None
):
    questions = (
        ROOT / "docs/certification/questions/11-startup-certification.md"
    ).read_text(encoding="utf-8")
    answers = (
        ROOT / "docs/certification/answers/11-startup-certification.yml"
    ).read_text(encoding="utf-8")

    assert "In the Phase-0 Launch Manifest baseline" in questions
    assert "At the 1267.0.38 deterministic-status boundary" in questions
    assert "Before Launch Manifest changes runtime behavior" not in questions
    assert "until process birth identity exists" not in questions

    assert "The Phase-0 baseline had to describe" in answers
    assert "At the 1267.0.38 boundary" in answers
    assert "before process-birth" in answers
    assert "identity landed" in answers
    assert "they subsequently landed and are tested by Q137" in answers
    assert "attach/reuse admission later covered by Q138" in answers
    assert (
        "Process birth identity and live-owner proof are intentionally deferred to the next phase."
        not in answers
    )


def test_certification_answer_documents_remain_valid_yaml() -> None:
    for path in sorted((ROOT / "docs/certification/answers").glob("*.yml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(payload, dict), path


def test_startup_certification_keeps_bootstrap_and_release_staging_separate() -> None:
    path = ROOT / "docs/certification/answers/11-startup-certification.yml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    questions = {item["id"]: item for item in payload["questions"]}

    q76 = " ".join(claim["statement"] for claim in questions["Q76"]["expected_claims"])
    q220 = " ".join(
        claim["statement"] for claim in questions["Q220"]["expected_claims"]
    )
    assert "native `make init`" in q76
    assert "Native uv/npm own dependency resolution" in q76
    assert "generated/release/static" in q220
    assert "release-artifact staging surface" in q220


def test_active_docs_make_admission_before_work_purpose_independent() -> None:
    workflow = (ROOT / "docs/development/CANONICAL_WORKFLOW.md").read_text(
        encoding="utf-8"
    )
    cicd = (ROOT / "docs/CI_CD_CONTRACT.md").read_text(encoding="utf-8")
    anti_drift = (ROOT / "docs/development/DOCUMENTATION_ANTI_DRIFT.md").read_text(
        encoding="utf-8"
    )

    assert (
        "This rule applies to every execution purpose, not only strict CI/CD." in cicd
    )
    assert "must close before `RUN_ADMITTED`" in cicd
    assert (
        "revalidation must never be the first place an invalid run is discovered"
        in cicd
    )
    assert "one source of truth, not two purpose-selected validators" in cicd
    assert "Verification is not a relaxed admission mode." in workflow
    assert "no state lease, `RUN_ADMITTED`, repository attempt" in workflow
    assert "Runtime preparation may revalidate" in anti_drift
    assert "may not be their first validator" in anti_drift
    assert "Admission does **not** pre-run the requested workload." in workflow
    assert "does not pre-execute the workload" in cicd
    assert "Admission must not become workload simulation." in anti_drift


def test_active_docs_keep_cicd_resource_caps_machine_owned_and_capacity_inherited() -> (
    None
):
    cicd = (ROOT / "docs/CI_CD_CONTRACT.md").read_text(encoding="utf-8")
    anti_drift = (ROOT / "docs/development/DOCUMENTATION_ANTI_DRIFT.md").read_text(
        encoding="utf-8"
    )
    dataflow = (ROOT / "docs/development/DATAFLOW.md").read_text(encoding="utf-8")

    assert "There are no hidden static CPU/RAM/PID defaults." in cicd
    assert (
        "Omitted resource fields continue to inherit admitted executor capacity."
        in cicd
    )
    assert (
        "Canonical shipped Plan templates intentionally do not author fixed resource caps."
        in cicd
    )
    assert (
        "omitted CPU/memory/PID caps inherit the selected executor's admitted capacity"
        in anti_drift
    )
    assert "publish fixed CPU/memory/PID values as effective policy" in anti_drift
    assert (
        "optional author resource caps with executor-capacity inheritance when omitted"
        in dataflow
    )
    assert "2000` CPU millicores, `2048 MiB` memory, and `64` processes" not in cicd
