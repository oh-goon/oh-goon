import type { DirectFleetExecutionAdmission, StrictFleetExecutionAdmission } from "@generated/models";

type FleetExecutionAdmission = DirectFleetExecutionAdmission | StrictFleetExecutionAdmission;

export type RunLineage =
  | { kind: "rerun"; parentRunId: string }
  | { kind: "readmitted"; parentRunId: string }
  | null;

export function runLineage(execution: FleetExecutionAdmission): RunLineage {
  if (execution.rerun_from_run_id) {
    return { kind: "rerun", parentRunId: execution.rerun_from_run_id };
  }
  if ("readmitted_from_run_id" in execution && execution.readmitted_from_run_id) {
    return { kind: "readmitted", parentRunId: execution.readmitted_from_run_id };
  }
  return null;
}
