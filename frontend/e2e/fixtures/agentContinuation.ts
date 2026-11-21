import type { AgentCampaignResult, AgentContinuationInspection, AgentContinuationReceipt, AgentContinuationRequest, AgentContinuationResult } from "@generated/models";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";

import { fixtureCampaignReadback, fixtureUnadmittedCampaignResult } from "./gameTape";
import { PRIMARY_REPOSITORY, fixtureRepositoryInstanceId } from "./identities";
import { FIXTURE_FLEET_PLAN_TARGET, FIXTURE_SHA } from "./plans";
import type { FixtureRunRegistry } from "./FixtureRunRegistry";
import { fixtureRunFeedback } from "./runFeedback";
import { FAILED_RUN_ID } from "./runIds";

export const CONTINUATION_CAMPAIGN_ID = "campaign-browser-stopped";

export function fixtureAgentContinuation(implementation: "codex" | "pi" | "opencode" = "codex", profile = "current-native"): AgentContinuationInspection {
  const sourceAuthority = { authority_id: "authority-current", subject_instance_id: "subject-current", source_snapshot: { sha256: "c".repeat(64), entry_count: 4, byte_count: 512 } };
  const feedback = fixtureRunFeedback(FAILED_RUN_ID, { campaign: { campaign_id: CONTINUATION_CAMPAIGN_ID, current_candidate_identity: "captured-candidate", scope_identity: "original-scope", original_execution_digest: "original-execution", phase: "regression", attempt: 2 } });
  return {
    campaign_id: CONTINUATION_CAMPAIGN_ID, state: "needs_review", previous_run_id: FAILED_RUN_ID,
    repository: PRIMARY_REPOSITORY, feedback,
    source: { source_authority: sourceAuthority, repository_instance_id: fixtureRepositoryInstanceId(PRIMARY_REPOSITORY.repository_id), recorded_candidate_identity: "captured-candidate", original_delivery_sha256: FIXTURE_SHA,
      diff: { availability: "available", text: "diff --git a/src/service.py b/src/service.py\n-return old_result\n+return current_result", truncated: true, byte_count: 4096, omitted_bytes: 3968, reason: "Original executable modes were not retained; mode lines do not establish historical permission changes.", baseline: "last_captured_candidate" } },
    selection: { implementation, profile, mode: "headless_cli", selected_backend: "local", sandbox_owner: "harness", executable_sha256: FIXTURE_SHA, selection_identity: `agent-continuation-selection-${FIXTURE_SHA}`, environment_qualification: "unqualified" },
    review: { campaign_id: CONTINUATION_CAMPAIGN_ID, campaign_revision: 203, decision_id: "stopped-decision", previous_run_id: FAILED_RUN_ID, previous_execution_digest: "original-execution", repository_id: PRIMARY_REPOSITORY.repository_id, repository_instance_id: fixtureRepositoryInstanceId(PRIMARY_REPOSITORY.repository_id), source_authority: sourceAuthority, implementation, profile, config_file: null, selection_identity: `agent-continuation-selection-${FIXTURE_SHA}`, verification: [["uv", "run", "pytest", "tests/test_service.py"]], verification_timeouts: [3600] },
    verification_status: "available", verification_reason: "The original recorded regression remains an explicit verification command.", can_continue: true, refusal_reasons: [],
  };
}

export function fixtureStoppedCampaign(): AgentCampaignResult {
  const original = fixtureUnadmittedCampaignResult(CONTINUATION_CAMPAIGN_ID);
  const inspection = fixtureAgentContinuation();
  const records = fixtureCampaignReadback(FAILED_RUN_ID, true).records.map((record) => ({ ...record, fact: {
    ...record.fact, campaign_id: CONTINUATION_CAMPAIGN_ID,
    ...(record.fact.kind === "phase" ? { context: { ...record.fact.context, repair_id: CONTINUATION_CAMPAIGN_ID } } : {}),
    ...(record.fact.kind === "decision" ? { decision_id: "stopped-decision", stop_reason: "regression_failed" as const, message: "The original regression failed. Its recorded outcome remains unchanged." } : {}),
  } }));
  const intent = records.find((record) => record.fact.kind === "intent")?.fact;
  return { ...original, feedback: inspection.feedback, runs: [{ fleet_run_id: FAILED_RUN_ID, status: "FAILED", outcome: "FAILED" }], unstarted_selection: null,
    intent: intent?.kind === "intent" ? intent : null, decision_history: records,
    controller_result: { availability: "journal", stop_reason: "regression_failed", decision_id: "stopped-decision" }, result: "failed", incomplete_reasons: [] };
}

export function fixtureContinuationReceipt(request: AgentContinuationRequest): AgentContinuationReceipt {
  return { request_identity: `agent-continuation-request-${FIXTURE_SHA}`, review: request.review, feedback_sha256: FIXTURE_SHA };
}

interface ContinuationFixtureDependencies {
  runs: FixtureRunRegistry;
  saved<Value>(value: Value): WriteCommandResult<Value>;
}

/** Typed continuation envelopes share canonical fixture Run identity and history. */
export class AgentContinuationFixtureApi {
  constructor(private readonly dependencies: ContinuationFixtureDependencies) {}

  async inspect(campaignId: string, implementation?: string | null, profile?: string | null): Promise<AgentContinuationInspection> {
    if (campaignId !== CONTINUATION_CAMPAIGN_ID) throw new Error("No stopped admitted Agent Run is available for continuation");
    const selected = implementation || "codex";
    if (selected !== "codex" && selected !== "pi" && selected !== "opencode") throw new Error("Unknown fixture native implementation");
    return fixtureAgentContinuation(selected, profile || "current-native");
  }

  async start(campaignId: string, request: AgentContinuationRequest): Promise<WriteCommandResult<AgentContinuationResult>> {
    if (campaignId !== CONTINUATION_CAMPAIGN_ID) throw new Error("Unknown continuation campaign");
    const receipt = fixtureContinuationReceipt(request);
    const target = { ...FIXTURE_FLEET_PLAN_TARGET, agent_continuation: receipt };
    this.dependencies.runs.register({ runId: request.new_fleet_run_id, targets: [PRIMARY_REPOSITORY], planTarget: target, terminalAfterEventReads: 3 });
    return this.dependencies.saved({ fleet_run_id: request.new_fleet_run_id, receipt, acknowledgment: "admitted" });
  }
}
