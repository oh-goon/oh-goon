import { GameTapeStatus } from "@generated/enums";
import type { GameTapeFailureHistory, GameTapeLiveOutputBatch, GameTapeLiveOutputChunk, GameTapeRunDiff } from "@generated/models";

import { PRIMARY_REPOSITORY } from "./identities";
import { FIXTURE_PLAN_ID } from "./plans";
import { FIXTURE_FINISHED, FIXTURE_NOW } from "./time";

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
