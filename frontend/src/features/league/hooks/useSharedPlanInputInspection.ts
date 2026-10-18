import { useCallback } from "react";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import type { SharedPlanInputInspection } from "@generated/models";

/** Inspection never gates execution; preview and lock receipts retain all authority. */
export function useSharedPlanInputInspection(
  planKey: string, planRevision: string, inputs: Record<string, string>, enabled: boolean,
) {
  const client = useApiClient();
  const request = useCallback(
    (signal: AbortSignal) => client.inspectSharedPlanInputs(planKey, planRevision, inputs, { signal }),
    [client, inputs, planKey, planRevision],
  );
  return useReadResource<SharedPlanInputInspection>({
    scope: "shared-plan-input-inspection",
    source: "POST /league/shared-plan-input-inspections",
    identity: enabled ? canonicalIdentity({ planKey, planRevision, inputs }) : null,
    enabled, debounceMs: 200, errorMessage: "Plan input inspection unavailable",
    request,
    validate: (value) => {
      if (value.schema !== "oh-goon.shared-plan-input-inspection/v1"
        || value.execution !== "not_started_by_inspection") throw new Error("Unrecognized Plan inspection");
      if (value.status === "accepted") {
        if (value.plan_key !== planKey || value.sha256 !== planRevision
          || !value.plan_id || value.diagnostic !== null) throw new Error("Plan inspection identity mismatch");
      } else if (value.status === "rejected") {
        if (value.plan_key !== null || value.sha256 !== null || value.plan_id !== null
          || value.diagnostic?.schema !== "oh-goon.plan-validation-diagnostic/v1"
          || value.diagnostic.status !== "rejected" || value.diagnostic.execution !== "not_started_by_diagnostic") {
          throw new Error("Invalid Plan rejection envelope");
        }
      } else {
        throw new Error("Unknown Plan inspection status");
      }
    },
  });
}
