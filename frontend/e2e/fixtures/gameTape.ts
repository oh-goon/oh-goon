import { GameTapeStatus } from "@generated/enums";
import type { AgentCampaignReadback, AgentCampaignResult, FleetRunDetail, GameTapeArtifactObservation, GameTapeFailureHistory, GameTapeLiveOutputBatch, GameTapeLiveOutputChunk, GameTapeRunDiff } from "@generated/models";

import { PRIMARY_REPOSITORY } from "./identities";
import { FIXTURE_PLAN_ID } from "./plans";
import { ARTIFACT_OBSERVATION_RELEASE_EVENT, ARTIFACT_OBSERVATION_REQUEST_EVENT, ARTIFACT_OBSERVATION_RESPONSE_EVENT, type BrowserFixtureScenarioDefinition } from "./scenarios";
import { FIXTURE_FINISHED, FIXTURE_NOW } from "./time";

export async function fixtureArtifactReadback(
  detail: FleetRunDetail,
  mode: BrowserFixtureScenarioDefinition["artifactObservation"],
  observeArtifacts: boolean,
): Promise<FleetRunDetail> {
  if (mode === undefined) return detail;
  detail.journal_revision = 203;
  detail.game_tape.journal_revision = 203;
  detail.game_tape.artifact_observation = null;
  fixtureArtifactEvidence(detail);
  if (observeArtifacts) {
    const runId = detail.run.fleet_run_id;
    if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(ARTIFACT_OBSERVATION_REQUEST_EVENT, { detail: { runId } }));
    if (mode === "held") {
      await new Promise<void>((resolve) => window.addEventListener(ARTIFACT_OBSERVATION_RELEASE_EVENT, () => resolve(), { once: true }));
    }
    detail.game_tape.artifact_observation = fixtureArtifactObservation(detail);
    if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(ARTIFACT_OBSERVATION_RESPONSE_EVENT, { detail: { runId } }));
  }
  return detail;
}

/** Recorded artifact evidence is unchanged by the separately requested observation. */
function fixtureArtifactEvidence(detail: FleetRunDetail): void {
  const repository = detail.game_tape.repositories[0];
  const step = repository?.steps?.[0];
  if (!repository || !step) throw new Error("Artifact fixture requires a recorded producer Step");
  step.artifacts = ["metrics", "model", "evaluation", "manifest"].map((name, index) => ({
    status: "PRESENT",
    path: `ml-output/${name}.json`,
    sha256: String(index + 1).repeat(64),
    byte_count: 64,
    retained_object: {
      kind: "file",
      sha256: String(index + 5).repeat(64),
      byte_count: 64,
      entry_count: 1,
      object_id: `file-sha256-${String(index + 5).repeat(64)}`,
      reference_id: `ref-${String(index + 1).repeat(32)}`,
      owner_repository_id: repository.repository.repository_id,
    },
  }));
}

function fixtureArtifactObservation(detail: FleetRunDetail): GameTapeArtifactObservation {
  const repository = detail.game_tape.repositories[0];
  const step = repository?.steps?.[0];
  if (!repository || !step) throw new Error("Artifact fixture requires a recorded producer Step");
  const observations: GameTapeArtifactObservation["observations"] = (step.artifacts ?? []).slice(0, 2).map((artifact, index) => {
    if (artifact.status !== "PRESENT") throw new Error("Artifact fixture requires present evidence");
    return {
      repository_execution_id: repository.repository_execution_id,
      repository_id: repository.repository.repository_id,
      step_execution_id: step.step_execution_id,
      step_id: step.step_id,
      path: artifact.path,
      recorded_status: artifact.status,
      recorded_sha256: artifact.sha256,
      recorded_byte_count: artifact.byte_count,
      reference_id: artifact.retained_object?.reference_id ?? null,
      status: index === 0 ? "AVAILABLE" : "UNAVAILABLE",
      reason: index === 0 ? "VERIFIED" : "REFERENCE_UNAVAILABLE",
      checked_at: FIXTURE_FINISHED,
      staging_cleanup: index === 0 ? "VERIFIED" : "NOT_REQUIRED",
      cleanup_failure: null,
    };
  });
  return {
    schema_name: "oh-goon.artifact-availability/v1",
    fleet_run_id: detail.run.fleet_run_id,
    journal_revision: detail.journal_revision ?? null,
    started_at: FIXTURE_FINISHED,
    finished_at: FIXTURE_FINISHED,
    artifact_limit: 2,
    byte_budget: 1024,
    time_budget_seconds: 3,
    total_artifacts: step.artifacts?.length ?? 0,
    omitted_artifacts: 2,
    bytes_scheduled: 64,
    observations,
  };
}

export function fixtureCampaignResultForId(campaignId: string): AgentCampaignResult {
  if (campaignId !== "campaign-browser-refused") throw new Error("Unknown fixture campaign");
  return fixtureUnadmittedCampaignResult(campaignId);
}

export function fixtureUnadmittedCampaignResult(campaignId = "campaign-browser-refused"): AgentCampaignResult {
  const selection = { selected_run_id: campaignId, phase_ordinal: 1, phase: "review", selected_step_ids: ["refused-review-step"], admission_status: "not_admitted" as const };
  const intent = {
    kind: "intent" as const, campaign_id: campaignId, repository_id: PRIMARY_REPOSITORY.repository_id,
    original_execution_digest: "original-execution-identity", maximum_runs: 6,
    initial_phase: "review", initial_step_ids: ["refused-review-step"],
    budget: { max_attempts: 2, timeout_seconds: 120, step_timeout_seconds: 30 }, deadline_at: FIXTURE_FINISHED,
  };
  return {
    schema: "oh-goon.agent-campaign-result/v2", campaign_id: campaignId, journal_revision: 203,
    result: "incomplete", complete: true, intent, runs: [], repositories: [],
    incomplete_reasons: ["No admitted campaign Runs are available"],
    controller_result: { availability: "journal", stop_reason: "execution_unresolved", decision_id: "initial-refusal" },
    safe_next_action: "inspect_original_runs_and_resolve_ownership", unstarted_selection: selection,
    decision_history: [
      { session_id: "intent", revision: 1, fact: intent },
      { session_id: "refusal", revision: 2, fact: {
        kind: "decision", campaign_id: campaignId, expected_revision: 1, decision_id: "initial-refusal",
        phase_ordinal: 0, remaining_runs: 6, stop_reason: "execution_unresolved", consumed_runs: [],
        unstarted_selection: selection, message: "Initial admission was refused before any Run started.",
      } },
    ],
    original_execution_digest: null, initial_candidate: null, last_admitted_candidate: null, final_candidate: null,
    reviews: [], findings: [], adjudications: [], repair_attempts: [], verification: [],
    claim_authority: "Review and adjudication records are source-bound Agent claims; Step results retain execution authority",
    continuation_authority: "Read-only projection; a fresh authorized invocation is required",
  };
}

export function fixtureCampaignReadback(runId: string, terminal: boolean, unstarted = false): AgentCampaignReadback {
  const campaignId = "campaign-browser-original";
  return {
    campaign_id: campaignId, journal_revision: 203, complete: true,
    records: [
      { session_id: "campaign-intent", revision: 1, fact: {
        kind: "intent", campaign_id: campaignId, repository_id: PRIMARY_REPOSITORY.repository_id,
        original_execution_digest: "original-execution-identity", maximum_runs: 8,
        initial_phase: "review", initial_step_ids: ["review-a", "review-b"],
        budget: { max_attempts: 2, timeout_seconds: 120, step_timeout_seconds: 30 },
        deadline_at: FIXTURE_FINISHED, candidate_identity: "captured-candidate", scope_identity: "complete-review-scope", write_roots: ["src"],
      } },
      { session_id: "campaign-phase", revision: 2, fact: {
        kind: "phase", campaign_id: campaignId, expected_revision: 1, fleet_run_id: runId,
        execution_digest: "verification-execution-identity", selected_step_ids: ["verification"],
        context: { repair_id: campaignId, attempt: 1, max_attempts: 8, limits: { deadline_at: FIXTURE_FINISHED, step_timeout_seconds: 30 }, phase: "verification", review_pass: 1, original_execution_digest: "original-execution-identity", candidate_identity: "captured-candidate", scope_identity: "complete-review-scope", write_roots: ["src"] },
      } },
      ...(unstarted ? [{ session_id: "unstarted-decision", revision: 3, fact: {
        kind: "decision" as const, campaign_id: campaignId, expected_revision: 2,
        decision_id: "admission-blocked-decision", phase_ordinal: 1, remaining_runs: 7,
        consumed_runs: [{ fleet_run_id: runId, terminal_sequence: 202 }],
        stop_reason: "execution_unresolved" as const, unresolved_run_ids: [],
        unstarted_selection: {
          selected_run_id: "selected-but-not-admitted-run", phase_ordinal: 2, phase: "verification",
          selected_step_ids: ["unstarted-verification-step"], admission_status: "not_admitted" as const,
        },
      } }] : terminal ? [{ session_id: "campaign-decision", revision: 3, fact: {
        kind: "decision" as const, campaign_id: campaignId, expected_revision: 2,
        decision_id: "committed-campaign-decision", phase_ordinal: 1, remaining_runs: 7,
        consumed_runs: [{ fleet_run_id: runId, terminal_sequence: 202 }],
        stop_reason: "converged" as const, unresolved_run_ids: [], message: "Final review and verification were accepted.",
      } }] : []),
    ],
  };
}

export function fixtureRunDiff(runId: string, baselineRunId: string): GameTapeRunDiff {
  return {
    base_run_id: baselineRunId,
    compared_run_id: runId,
    base_plan_id: FIXTURE_PLAN_ID,
    compared_plan_id: FIXTURE_PLAN_ID,
    base_duration_ms: 51_000,
    compared_duration_ms: 102_000,
    duration_delta_ms: 51_000,
    plan_changed: false,
    repositories: [{
      repository_id: PRIMARY_REPOSITORY.repository_id,
      repository_name: PRIMARY_REPOSITORY.name,
      base_head_sha: "b".repeat(40),
      compared_head_sha: "c".repeat(40),
      base_branch: "main",
      compared_branch: "main",
      workspace_changed: false,
      runtime_changed: false,
      toolchain_changed: false,
      isolation_changed: false,
      steps: [
        { step_id: "ruff-format-and-static-quality", base_status: GameTapeStatus.PASSED, compared_status: GameTapeStatus.PASSED, base_duration_ms: 12_000, compared_duration_ms: 18_000, duration_delta_ms: 6_000, command_changed: false, provenance_changed: false, base_attempt_count: 1, compared_attempt_count: 1, attempt_count_changed: false, tool_changed: false, egress_changed: false, resource_policy_changed: false, failure_signature_changed: false },
        { step_id: "pytest-production-suite", base_status: GameTapeStatus.PASSED, compared_status: GameTapeStatus.FAILED, base_duration_ms: 20_000, compared_duration_ms: 46_000, duration_delta_ms: 26_000, command_changed: false, provenance_changed: false, base_attempt_count: 1, compared_attempt_count: 1, attempt_count_changed: false, tool_changed: false, egress_changed: false, resource_policy_changed: false, failure_signature_changed: true },
        { step_id: "trivy-image-audit", base_status: GameTapeStatus.PASSED, compared_status: GameTapeStatus.PASSED, base_duration_ms: 19_000, compared_duration_ms: 38_000, duration_delta_ms: 19_000, command_changed: true, provenance_changed: false, base_attempt_count: 1, compared_attempt_count: 2, attempt_count_changed: true, tool_changed: false, egress_changed: false, resource_policy_changed: false, failure_signature_changed: false },
      ],
    }],
  };
}

export function fixtureFailureHistory(runId: string, repositoryId: string, stepId: string): GameTapeFailureHistory {
  const contextCounts = { same_context: 2, changed_context: 0, incomplete_context: 0 };
  const sameContext = {
    command: "MATCH",
    isolation: "MATCH",
    runtime: "MATCH",
    toolchain: "MATCH",
    workspace: "MATCH",
    summary: { classification: "SAME_CONTEXT", different_dimensions: 0, known_dimensions: 5, matching_dimensions: 5, total_dimensions: 5 },
  } as const;
  return {
    fleet_run_id: runId, plan_id: FIXTURE_PLAN_ID, repository_id: repositoryId, step_id: stepId, signature_id: "fixture-failure-signature", sample_limit: 10, history_candidates_considered: 4, same_plan_step_failures: 3, occurrences: [],
    evolution: { exact_occurrence_count: 3, prior_exact_occurrences: 2, later_exact_occurrences: 0, first_seen_run_id: "fleet-run-browser-fixture-earlier-failure", first_seen_at: "2026-08-20T15:00:00Z", last_seen_run_id: runId, last_seen_at: FIXTURE_NOW, later_comparable_runs: 1, exact_failure_recurred_after_pass: true, first_later_pass: null, prior_exact_contexts: contextCounts, later_exact_contexts: { same_context: 0, changed_context: 0, incomplete_context: 0 }, later_pass_contexts: { same_context: 1, changed_context: 0, incomplete_context: 0 } },
    sequence: { sample_limit: 10, entries: [{ fleet_run_id: runId, accepted_at: FIXTURE_NOW, position: "SELECTED", status: GameTapeStatus.FAILED, signature_relation: "EXACT_SIGNATURE", context: sameContext }], transitions: [], summary: { prior_comparable_runs: 2, later_comparable_runs: 1, sampled_prior_runs: 2, sampled_later_runs: 1, complete_context_entries: 3, incomplete_context_entries: 0, later_same_context_passes: 1, later_same_context_exact_failures: 0, latest_comparable_run_id: runId, latest_comparable_status: GameTapeStatus.FAILED, latest_signature_relation: "EXACT_SIGNATURE", latest_context_classification: "SAME_CONTEXT", exact_failure_recurred_in_same_context_after_same_context_pass: true, first_later_same_context_pass_run_id: null }, selected_context_window: { prior_same_context_runs: 2, later_same_context_runs: 1, total_same_context_runs_including_selected: 4, nearest_prior_boundary_run_id: null, nearest_prior_boundary_classification: null, nearest_later_boundary_run_id: null, nearest_later_boundary_classification: null }, transition_summary: { sampled_transition_count: 0, status_change_count: 0, same_context_status_changes: 0, changed_context_status_changes: 0, incomplete_context_status_changes: 0, signature_relation_change_count: 0 } },
  };
}

export function fixtureLiveOutput(runId: string, after = 0): GameTapeLiveOutputBatch {
  const chunks: GameTapeLiveOutputChunk[] = [
    { sequence: 1, repository_id: PRIMARY_REPOSITORY.repository_id, step_id: "ruff-format-and-static-quality", attempt: 1, stream: "stdout", line_start: 1, line_end: 1, text: "ruff check completed successfully", emitted_at: FIXTURE_NOW },
    { sequence: 2, repository_id: PRIMARY_REPOSITORY.repository_id, step_id: "ruff-format-and-static-quality", attempt: 1, stream: "stderr", line_start: 1, line_end: 1, text: "diagnostic warning retained for browser filtering", emitted_at: FIXTURE_NOW },
    { sequence: 3, repository_id: PRIMARY_REPOSITORY.repository_id, step_id: "ruff-format-and-static-quality", attempt: 1, stream: "stdout", line_start: 2, line_end: 2, text: "quality report written", emitted_at: FIXTURE_FINISHED },
  ];
  return {
    fleet_run_id: runId, state: "SEALED", available: true, next_sequence: 4, retained_from_sequence: 1, dropped_chunk_count: 0, expires_at: "2026-08-21T16:03:00Z", failure_navigation_state: "AVAILABLE",
    chunks: chunks.filter((chunk) => chunk.sequence > after),
    failure_anchors: [],
  };
}
