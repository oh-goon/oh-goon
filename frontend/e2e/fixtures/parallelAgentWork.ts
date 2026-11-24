import type { FleetRunDetail, FleetRunState, ParallelAgentTape } from "@generated/models";

import { PRIMARY_REPOSITORY } from "./identities";
import { DEFAULT_RUN_ID, FAILED_RUN_ID } from "./runIds";

export const PARALLEL_AGENT_WORK_SCENARIO = "parallel-agent-work";
export const PARALLEL_TASK_ID = "parallel-task";
export const PARALLEL_BASE_REVISION = "b".repeat(40);

export function fixtureParallelAgentWork(
  runId = DEFAULT_RUN_ID,
  status: FleetRunState["status"] = "COMPLETED",
  peerId = FAILED_RUN_ID,
  peerStatus: FleetRunState["status"] = "FAILED",
): ParallelAgentTape {
  const sourceSha256 = "a".repeat(64);
  return {
    work: {
      intent: {
        work_id: PARALLEL_TASK_ID,
        base_revision: PARALLEL_BASE_REVISION,
        repository_id: PRIMARY_REPOSITORY.repository_id,
        source: {
          authority_id: "source-authority",
          subject_instance_id: "repository-subject",
          source_snapshot: { sha256: sourceSha256, byte_count: 12, entry_count: 1 },
        },
        verifier_id: "frozen-verifier",
        notes_enabled: true,
        children: [
          { fleet_run_id: runId, native_selection_id: "native-a", task_sha256: "c".repeat(64) },
          { fleet_run_id: peerId, native_selection_id: "native-b", task_sha256: "d".repeat(64) },
        ],
      },
      children: [
        { fleet_run_id: runId, dispatch: "admitted", status },
        { fleet_run_id: peerId, dispatch: "admitted", status: peerStatus },
      ],
      dispatch_closed: true,
      cancelled: false,
    },
    publications: [{
      note_id: 1,
      kind: "idea",
      request_id: "append-1",
      author: {
        fleet_run_id: runId,
        attempt_id: "attempt-a",
        step_id: "agent-step",
        repository_id: PRIMARY_REPOSITORY.repository_id,
        workspace_id: "workspace-a",
        base_source_sha256: sourceSha256,
      },
    }],
    deliveries: [{
      note_id: 1,
      attempt_id: "attempt-b",
      receiving_run_id: peerId,
      step_id: "agent-step",
      recipient: "admitted-recipient",
      model: "admitted-model",
      outcome: "acknowledged",
    }],
  };
}

export function fixtureParallelAgentReadback(detail: FleetRunDetail, enabled: boolean): void {
  const runId = detail.run.fleet_run_id;
  if (enabled && (runId === DEFAULT_RUN_ID || runId === FAILED_RUN_ID)) {
    detail.game_tape.parallel_agent_work = fixtureParallelAgentWork();
  }
}
