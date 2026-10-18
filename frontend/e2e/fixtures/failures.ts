import type { FleetFailureGroup } from "@generated/models";

import { LONG_GOON_NAME, LONG_PLAN_NAME, LONG_STEP_NAME } from "./identities";
import { FIXTURE_PLAN_ID as PLAN_ID } from "./plans";
import { SCALE_RUN_ID } from "./runIds";

export function defaultFailureGroup(): FleetFailureGroup {
  return {
    group_id: "failure-group-default",
    plan_id: PLAN_ID,
    plan_name: LONG_PLAN_NAME,
    source_kind: "shared_plan",
    goon_name: LONG_GOON_NAME,
    step_id: LONG_STEP_NAME,
    affected_repositories: 1,
    signature_count: 1,
  };
}

export function scaleFailureGroups(scaleRepositoryCount: number): FleetFailureGroup[] {
  return Array.from({ length: 121 }, (_, index) => ({
    group_id: `failure-group-scale-${String(index).padStart(3, "0")}`,
    plan_id: `${PLAN_ID}-${String(index).padStart(3, "0")}`,
    plan_name: index === 0 ? LONG_PLAN_NAME : `Scale Plan ${String(index).padStart(3, "0")}`,
    source_kind: "shared_plan",
    goon_name: index === 0 ? LONG_GOON_NAME : `scale-goon-${String(index).padStart(3, "0")}`,
    step_id: index === 0 ? LONG_STEP_NAME : `scale-step-${String(index).padStart(3, "0")}`,
    affected_repositories: index === 0 ? scaleRepositoryCount : 1,
    signature_count: index === 0 ? 4 : 1,
  }));
}

export function requireFailureGroup(groups: readonly FleetFailureGroup[], groupId: string): FleetFailureGroup {
  const group = groups.find((item) => item.group_id === groupId);
  if (group === undefined) throw new Error(`Unknown fixture failure group ${groupId}`);
  return group;
}

export function scaleFailureRunId(index: number): string {
  return `${SCALE_RUN_ID}-failure-${String(index).padStart(5, "0")}`;
}
