import type { AgentRunControl, RunFeedback } from "@generated/models";

import { FIXTURE_SHA } from "./plans";
import { FIXTURE_FINISHED } from "./time";

export function fixtureRunFeedback(runId = "run-original", overrides: Partial<RunFeedback> = {}): RunFeedback {
  const controls: AgentRunControl[] = [
    { action: "fresh_handoff", state: "available", reason: "A separate fresh resolution and admission is required.", requirements: ["fresh_resolution", "fresh_admission"], route: "fresh_run_with_handoff" },
    { action: "exact_native_resume", state: "unqualified", reason: "No exact native-session qualification was retained.", requirements: [], route: null },
    { action: "cross_harness_fresh_handoff", state: "unqualified", reason: "No target harness and environment qualification was retained.", requirements: ["fresh_resolution", "fresh_admission"], route: null },
    { action: "steer", state: "unsupported", reason: "No admitted steering route is available.", requirements: [], route: null },
    { action: "follow_up", state: "unqualified", reason: "Native follow-up is unqualified for this selection.", requirements: [], route: null },
    { action: "abort", state: "unsupported", reason: "The original Run has already stopped.", requirements: ["original_run_owner"], route: null },
    { action: "detach", state: "unsupported", reason: "No admitted detach route is available.", requirements: [], route: null },
  ];
  return {
    schema: "oh-goon.run-feedback/v1", fleet_run_id: runId, fleet_run_id_sha256: FIXTURE_SHA,
    journal_revision: 203, status: "FAILED", outcome: "FAILED", terminal: true,
    diagnostic: { classification: "known_code_failure", owner: "agent_campaign_settlement", reason: "The original accepted regression failed." },
    recovery_action: "none",
    goals: [{ step_id: "regression", availability: "recorded", summary: "Preserve the original verification scope.", sha256: FIXTURE_SHA, omitted_characters: 0, redacted: false, handoff_identity: "handoff-original", delivery_sha256: FIXTURE_SHA }],
    constraints: { execution_policy: null, limits: null, write_roots: ["src"], omitted_write_roots: 0 },
    campaign: { campaign_id: "campaign-original", current_candidate_identity: "candidate-original", scope_identity: "scope-original", run_candidate_identity: "candidate-at-run", run_scope_identity: "scope-at-run", candidate_freshness: "recorded_not_live", original_execution_digest: "execution-original", phase: "regression", attempt: 2 },
    budgets: { original: { max_attempts: 2, timeout_seconds: 120, step_timeout_seconds: 30 }, maximum_runs: 6, remaining_runs: 2, remaining_runs_authority: "committed_decision", deadline_at: FIXTURE_FINISHED, elapsed_budget: "not_recomputed", model_usage: "unavailable" },
    sources: [{ repository_id: "repository-original", admission_id: "admission-original", source_authority_id: "source-authority-original", source_subject_instance_id: "subject-original", source_content_sha256: FIXTURE_SHA }],
    steps: [{
      repository_id: "repository-original", repository_execution_id: `${runId}:repository-original`, step_id: "regression", step_execution_id: `${runId}:regression`, status: "FAILED",
      diagnostic: { classification: "known_code_failure", owner: "agent_campaign_settlement", reason: "The original accepted regression failed." },
      settlement: { owner: "fleet_step_settlement", outcome: "FAILED", cleanup: "VERIFIED", reason: "FAILED_EXIT", observed_returncode: 1 },
      attempts: [{ attempt: 1, physical_execution_id: "physical-original", observed_returncode: 1 }], cancellation: null,
      stdout_evidence: null, stderr_evidence: null, outputs: [], omitted_outputs: 0,
      retained_diagnostics: [], omitted_retained_diagnostics: 0,
    }],
    verification: [{ repository_id: "repository-original", availability: "unavailable", selected_count: 1, actual_count: 1, result_count: 1, omitted_selected_members: 0, omitted_actual_members: 0, omitted_results: 0, evidence: null, results: [], qualification: "unavailable" }],
    controls: { fleet_run_id: runId, fleet_run_id_sha256: FIXTURE_SHA, run_id_availability: "recorded", journal_revision: 203, selections: [], omitted_selections: 0, controls, environment_qualification: "unqualified", qualification_reason: "Installed harness and environment parity is not established by this fixture." },
    omissions: { steps: 0, goals: 0, sources: 0, verifications: 0, fields: [], response_limit_applied: false },
    authority: "observation_only", context_authority: "untrusted_data_no_effect_or_recovery_permission", native_claims: "not_projected",
    readback: [
      { projection: "detail", fleet_run_id: runId, fleet_run_id_sha256: FIXTURE_SHA, evidence: "original_run" },
      { projection: "game_tape", fleet_run_id: runId, fleet_run_id_sha256: FIXTURE_SHA, evidence: "complete_verification_membership" },
      { projection: "outputs", fleet_run_id: runId, fleet_run_id_sha256: FIXTURE_SHA, evidence: "retained_output_references" },
    ],
    ...overrides,
  };
}
