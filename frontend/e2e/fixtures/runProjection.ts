import { FleetRunOutcome, FleetRunStatus, GameTapeStatus, OperationAction } from "@generated/enums";
import type {
  DirectFleetExecutionAdmission,
  FleetPlanTarget,
  FleetRunDetail,
  FleetRunState,
  GameTapeObservabilityReceipt,
  GameTapeStepExecution,
  OperationReport,
  RepositoryIdentity,
  ResolvedExecutionReference,
} from "@generated/models";

import { LONG_GOON_NAME, LONG_NATIVE_INPUT_IDENTITY, LONG_STEP_NAME } from "./identities";
import { FIXTURE_FLEET_PLAN_TARGET, FIXTURE_SHA } from "./plans";
import { FIXTURE_FINISHED, FIXTURE_NOW } from "./time";

function executionReferenceForTarget(target: FleetPlanTarget): ResolvedExecutionReference {
  return {
    plan_id: target.plan.plan_id,
    plan_name: target.plan.name,
    source: target.source,
    identity: target.identity,
  };
}

export const FIXTURE_DIRECT_EXECUTION: DirectFleetExecutionAdmission = { kind: "direct" };

export function runState(
  runId: string,
  status: FleetRunState["status"],
  targets: RepositoryIdentity[],
  total: number,
  completed: number,
  target: FleetPlanTarget = FIXTURE_FLEET_PLAN_TARGET,
): FleetRunState {
  const common = {
    fleet_run_id: runId,
    capability: { capability_id: "fleet-plan-execute", policy_fingerprint: "fixture-policy", action: OperationAction.EXECUTE_PLAN, target_kind: "FLEET_PLAN" as const, safety: "OPAQUE_MUTATION" as const, invalidates_all: true, invalidates_capabilities: [] },
    repository_scope: { kind: "EXPLICIT" as const, repository_ids: targets.map((item) => item.repository_id) },
    target,
    targets,
    accepted_at: FIXTURE_NOW,
    progress: {
      total,
      succeeded: status === FleetRunStatus.COMPLETED ? completed : 0,
      failed: status === FleetRunStatus.FAILED ? completed : 0,
      needs_attention: status === FleetRunStatus.FAILED ? completed : 0,
      completed,
    },
  };
  if (status === FleetRunStatus.COMPLETED) return { ...common, status: FleetRunStatus.COMPLETED, started_at: FIXTURE_NOW, finished_at: FIXTURE_FINISHED };
  if (status === FleetRunStatus.FAILED) return {
    ...common,
    status: FleetRunStatus.FAILED,
    started_at: FIXTURE_NOW,
    finished_at: FIXTURE_FINISHED,
    failure: { stage: "EXECUTION", code: "FIXTURE_LONG_FAILURE", kind: "INTERNAL", retryable: false, message: "A deliberately long failure message verifies that operational failure information wraps and remains readable instead of being clipped, hidden by overlays, or communicated through color alone." },
  };
  if (status === FleetRunStatus.CANCELLED) return { ...common, status: FleetRunStatus.CANCELLED, started_at: FIXTURE_NOW, cancel_requested_at: FIXTURE_NOW, finished_at: FIXTURE_FINISHED };
  if (status === FleetRunStatus.CANCELLING) return { ...common, status: FleetRunStatus.CANCELLING, started_at: FIXTURE_NOW, cancel_requested_at: FIXTURE_NOW };
  if (status === FleetRunStatus.QUEUED) return { ...common, status: FleetRunStatus.QUEUED };
  return { ...common, status: FleetRunStatus.RUNNING, started_at: FIXTURE_NOW };
}

export function runDetail(run: FleetRunState, tapeStatus: FleetRunDetail["game_tape"]["status"], failed: boolean): FleetRunDetail {
  if (run.targets.length === 0) throw new Error(`Fixture Run ${run.fleet_run_id} has no repository targets`);
  if (run.target?.kind !== "fleet_plan") throw new Error(`Fixture Run ${run.fleet_run_id} does not have a FleetPlanTarget`);
  const targets = run.targets;
  const runTarget = run.target;
  const executionReference = executionReferenceForTarget(runTarget);
  const running = tapeStatus === GameTapeStatus.RUNNING;
  const sourceLong = `templates/goons/${LONG_GOON_NAME}/goon.yml`;
  const repositoryExecutions = targets.map((target, repositoryIndex) => {
    const laneFailed = failed && repositoryIndex === 0;
    const laneRunning = running && repositoryIndex === 0;
    const stepStatuses = laneFailed
      ? [GameTapeStatus.PASSED, GameTapeStatus.FAILED, GameTapeStatus.NOT_RUN, GameTapeStatus.NOT_RUN]
      : laneRunning
        ? [GameTapeStatus.PASSED, GameTapeStatus.RUNNING, GameTapeStatus.PLANNED, GameTapeStatus.PLANNED]
        : [GameTapeStatus.PASSED, GameTapeStatus.PASSED, GameTapeStatus.PASSED, GameTapeStatus.PASSED];
    const steps = [
      fixtureStep(run.fleet_run_id, target.repository_id, "ruff-format-and-static-quality", 1, "templates/goons/python/python-quality/goon.yml", stepStatuses[0]!, false),
      fixtureStep(run.fleet_run_id, target.repository_id, LONG_STEP_NAME, 2, sourceLong, stepStatuses[1]!, laneFailed),
      fixtureStep(run.fleet_run_id, target.repository_id, "trivy-container-and-filesystem-audit", 3, "templates/goons/security/trivy-audit/goon.yml", stepStatuses[2]!, false),
      fixtureStep(run.fleet_run_id, target.repository_id, "publish-quality-and-security-report", 4, sourceLong, stepStatuses[3]!, false),
    ];
    const status = laneFailed ? GameTapeStatus.FAILED : laneRunning ? GameTapeStatus.RUNNING : GameTapeStatus.PASSED;
    return {
      repository_execution_id: `${run.fleet_run_id}:repository:${target.repository_id}`,
      repository: target,
      status,
      operation_id: `operation:${run.fleet_run_id}:${target.repository_id}`,
      started_at: FIXTURE_NOW,
      finished_at: laneRunning ? null : FIXTURE_FINISHED,
      duration_ms: laneRunning ? null : 102000 + repositoryIndex * 4000,
      bottleneck_step_id: LONG_STEP_NAME,
      bottleneck_percent: 61,
      fingerprint: { repository_head_sha: `fixture-head-sha-${repositoryIndex + 1}`, branch: "main", branch_state: "NAMED", plan_source_sha256: FIXTURE_SHA, plan_contract_version: 39, execution_input_id: `fixture-input-${repositoryIndex + 1}`, base_workspace_sha256: FIXTURE_SHA, runtime_root_sha256: FIXTURE_SHA, host_toolchain_id: "uv-0.11.15-python-3.12", isolation_backend_id: "local", isolation_configuration_id: "fixture-isolation", isolation_kernel_boundary: "process", isolation_remote_execution: false, isolation_experimental: false, host_environment_names: [], managed_input_count: 0, content_input_count: 0, runtime_facts: [], tools: [] },
      steps,
    };
  });
  const allSteps = repositoryExecutions.flatMap((repository) => repository.steps);
  const failureRepository = repositoryExecutions.find((repository) => repository.status === GameTapeStatus.FAILED) ?? null;
  const failureStep = failureRepository?.steps.find((step) => step.status === GameTapeStatus.FAILED) ?? null;
  const failedRepositories = repositoryExecutions.filter((repository) => repository.status === GameTapeStatus.FAILED).length;
  const runningRepositories = repositoryExecutions.filter((repository) => repository.status === GameTapeStatus.RUNNING).length;
  const passedRepositories = repositoryExecutions.filter((repository) => repository.status === GameTapeStatus.PASSED).length;
  const executedSteps = allSteps.filter((step) => step.status !== GameTapeStatus.PLANNED && step.status !== GameTapeStatus.NOT_RUN).length;
  const passedSteps = allSteps.filter((step) => step.status === GameTapeStatus.PASSED).length;
  const failedSteps = allSteps.filter((step) => step.status === GameTapeStatus.FAILED).length;
  const notRunSteps = allSteps.filter((step) => step.status === GameTapeStatus.PLANNED || step.status === GameTapeStatus.NOT_RUN).length;
  const statusCounts = [
    ...(passedRepositories > 0 ? [{ status: GameTapeStatus.PASSED, count: passedRepositories }] : []),
    ...(runningRepositories > 0 ? [{ status: GameTapeStatus.RUNNING, count: runningRepositories }] : []),
    ...(failedRepositories > 0 ? [{ status: GameTapeStatus.FAILED, count: failedRepositories }] : []),
  ];
  const failureTarget = failureRepository?.repository ?? null;
  const observability: GameTapeObservabilityReceipt = {
    durable_evidence: ["run_lifecycle", "repository_lifecycle", "step_lifecycle", "timing", "command", "failure_signature"],
    transient_evidence: ["raw_stdout", "raw_stderr"],
    raw_output_max_chunks: 40,
    raw_output_max_chunk_chars: 16000,
    terminal_handoff_seconds: 30,
    execution_order_authority: "resolved_plan",
    failure_navigation_source: "durable_failure_evidence",
    persistent_raw_output_store: false,
    persistent_search_index: false,
    raw_output_survives_process_restart: false,
    ui_execution_authority: false,
  };
  const tape = {
    schema_name: "oh-goon.game-tape/v1" as const,
    configuration: runTarget.plan.configuration ?? null,
    terminal: "finished_at" in run,
    execution: FIXTURE_DIRECT_EXECUTION,
    fleet_run_id: run.fleet_run_id,
    status: tapeStatus,
    execution_reference: executionReference,
    issue_count: failed ? 1 : 0,
    hidden_issue_count: 0,
    issue_focus: failed && failureRepository && failureTarget && failureStep ? [{ scope: "STEP" as const, repository_execution_id: failureRepository.repository_execution_id, repository_name: failureTarget.name, step_execution_id: failureStep.step_execution_id, step_id: failureStep.step_id, code: "FIXTURE_LONG_FAILURE", message: failureStep.reason ?? "Recorded Step failure", exit_code: 1 }] : [],
    summary: { repositories: repositoryExecutions.length, passed: passedRepositories, failed: failedRepositories, attention_required: 0, not_run: 0, planned_steps: allSteps.length, executed_steps: executedSteps, passed_steps: passedSteps, failed_steps: failedSteps, skipped_steps: 0, not_run_steps: notRunSteps },
    receipt: { validation: "PASSED" as const, requirements_admitted: allSteps.length, accepted_at: FIXTURE_NOW, started_at: FIXTURE_NOW, finished_at: running ? null : FIXTURE_FINISHED, duration_ms: running ? null : 106000 },
    storage: { pressure: "NO_EVIDENCE" as const, maintenance_cuts: 0 },
    team: { repository_count: repositoryExecutions.length, timed_repository_count: repositoryExecutions.length, observed_peak_concurrency: repositoryExecutions.length, overlap_observed: repositoryExecutions.length > 1, overlapping_repository_execution_ids: repositoryExecutions.length > 1 ? repositoryExecutions.map((repository) => repository.repository_execution_id) : [], started_at: FIXTURE_NOW, finished_at: running ? null : FIXTURE_FINISHED, duration_ms: running ? null : 106000, status_counts: statusCounts, first_failure: failed && failureRepository && failureTarget && failureStep ? { repository_execution_id: failureRepository.repository_execution_id, repository_id: failureTarget.repository_id, repository_name: failureTarget.name, step_execution_id: failureStep.step_execution_id, step_id: failureStep.step_id, occurred_at: FIXTURE_FINISHED } : null },
    observability,
    requirements: [],
    play_by_play: [],
    repositories: repositoryExecutions,
  };
  const operations: OperationReport[] = targets.map((repositoryIdentity, index) => ({
    provenance: {
      operation_id: `operation:${run.fleet_run_id}:${repositoryIdentity.repository_id}`,
      fleet_run_id: run.fleet_run_id,
      started_at: FIXTURE_NOW,
      finished_at: FIXTURE_FINISHED,
    },
    capability: run.capability,
    repository: repositoryIdentity,
    target: runTarget,
    outcome: failed && index === 0
      ? { state: "failed", verdict: "FAILED", returncode: 1, reason_code: "OPERATION_FAILED" }
      : { state: "successful", verdict: "READY", returncode: 0, reason_code: "READY" },
    evidence: { kind: "FLEET_PLAN", plan: { plan_id: runTarget.plan.plan_id, steps: [] } },
  }));
  return {
    run,
    execution: FIXTURE_DIRECT_EXECUTION,
    outcome: failed ? FleetRunOutcome.FAILED : running ? FleetRunOutcome.IN_PROGRESS : FleetRunOutcome.SUCCESS,
    game_tape: tape,
    plan_trust_receipts: [],
    repository_admissions: targets.map((target, index) => ({
      repository_id: target.repository_id,
      plan_id: runTarget.plan.plan_id,
      semantic_preview_id: `fleet-plan-repository-preview-${String(index + 1).padStart(64, "0")}`,
      source_authority: {
        subject_instance_id: `repo-subject-instance-${String(index + 1).padStart(64, "0")}`,
        source_snapshot: {
          sha256: String(index + 1).padStart(64, "0"),
          byte_count: 0,
          entry_count: 0,
        },
        authority_id: `repository-source-authority-${String(index + 1).padStart(64, "0")}`,
      },
      admission_id: `fleet-plan-repository-admission-${String(index + 1).padStart(64, "0")}`,
      import_applicability: [],
      active_step_ids: runTarget.plan.steps.map((step) => step.step_id),
    })),
    rerun_supported: true,
    operations,
  };
}

function fixtureStep(
  runId: string,
  repositoryId: string,
  stepId: string,
  order: number,
  source: string,
  status: GameTapeStepExecution["status"],
  failed: boolean,
): GameTapeStepExecution {
  const reached = status !== GameTapeStatus.PLANNED && status !== GameTapeStatus.NOT_RUN;
  return {
    step_execution_id: `${runId}:${repositoryId}:step:${order}`,
    step_id: stepId,
    kind: "command",
    order,
    status,
    started_at: reached ? fixtureTime([0, 13_000, 76_000, 97_000][order - 1] ?? 0) : null,
    finished_at: reached && status !== GameTapeStatus.RUNNING ? fixtureTime([12_000, 75_000, 96_000, 102_000][order - 1] ?? 0) : null,
    duration_ms: reached ? ([12_000, 62_000, 20_000, 5_000][order - 1] ?? 0) : null,
    command: order === 2 ? ["uv", "run", "pytest", "tests/with/a/realistically/long/path/that/must/remain-visible/test_browser_quality.py"] : ["uv", "run", stepId],
    working_directory: ".",
    project_source: order === 2 ? {
      content_sha256: FIXTURE_SHA,
      archive_sha256: "b".repeat(64),
      byte_count: 42,
      entry_count: 2,
      application: {
        kind: "application",
        git: "https://github.com/example/independent-verification-application-with-a-long-source-name.git",
        ref: "release",
        path: "tools/verification-with-a-long-contained-subdirectory-name",
        commit: "c".repeat(40),
      },
    } : null,
    uv: order === 2 ? { source: "captured", project: ".", lock_mode: "frozen", group: "dev" } : null,
    exit_code: failed ? 1 : reached && status !== GameTapeStatus.RUNNING ? 0 : null,
    output_names: order === 4 ? ["quality-report-with-a-deliberately-long-output-name"] : [],
    attempt_count: reached ? 1 : 0,
    attempts: [],
    attempt_transitions: [],
    environment: [],
    retry_context: { classification: reached ? "NOT_RETRIED" : "NO_ATTEMPT_EVIDENCE", retry_count: 0, linked_retry_count: 0, physical_context_changed: false },
    why: { declaration: `${source}:${16 + order}`, source_location: { source, line: 16 + order, column: 5 }, reached, explanation: reached ? "Execution reached this declared Step in admitted Plan order." : "The Step is in the admitted Plan but execution has not reached it." },
    stdout_evidence: null,
    stderr_evidence: null,
    reason: failed ? "The exact Step failure text is intentionally long so browser visibility tests can verify wrapping, zoom and evidence readability." : null,
    failure: failed ? { exit_code: 1, reason_code: "FIXTURE_LONG_FAILURE", signature_id: "fixture-failure-signature", raw_log_available: false, evidence_stream: "stderr", evidence_line_count: 1, evidence_byte_count: 128, evidence_sha256: FIXTURE_SHA, attempt: 1 } : null,
    native_agent_observations: failed ? [{
      kind: "native_agent",
      agent: "pi",
      implementation_identity: `pi:sha256:${FIXTURE_SHA}`,
      profile: "isolated-managed",
      input_identity: LONG_NATIVE_INPUT_IDENTITY,
      occurrence: stepId,
      source_identity: `sha256:${FIXTURE_SHA}`,
      role: "report",
      events: [{ kind: "context", summary: "pi: compaction_end", native_event_id: null }],
      native_event_count: 6003,
      omitted_observations: 5874,
      settled: true,
      failed: false,
      authority: "UNTRUSTED_HARNESS_CLAIM",
    }] : [],
  };
}

function fixtureTime(offsetMs: number): string {
  return new Date(Date.parse(FIXTURE_NOW) + offsetMs).toISOString();
}
