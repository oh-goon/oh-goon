import { useCallback } from "react";

import { useApiClient, useReportMutationFeedback } from "@/app/ApiClientProvider";
import { appDiagnosticStore } from "@/domain/diagnostics/appDiagnosticStore";
import type { LocationsResponse } from "@generated/models";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";

export interface RemoveLocationState {
  remove(path: string, expectedRevision: string): Promise<WriteCommandResult<LocationsResponse>>;
}

export function useRemoveLocation(): RemoveLocationState {
  const client = useApiClient();
  const reportMutationFeedback = useReportMutationFeedback("locations");
  const remove = useCallback(async (path: string, expectedRevision: string) => {
    const result = validateLocationMutationIdentity(await client.removeLocation(path, expectedRevision), path, false);
    reportMutationFeedback(result);
    return result;
  }, [client, reportMutationFeedback]);
  return { remove };
}

function validateLocationMutationIdentity(
  result: WriteCommandResult<LocationsResponse>,
  expectedPath: string,
  shouldExist: boolean,
): WriteCommandResult<LocationsResponse> {
  if (result.outcome !== "saved" || result.value.locations.includes(expectedPath) === shouldExist) return result;
  const diagnostic = appDiagnosticStore.record({
    source: "location-mutation-identity",
    error: new Error(`Location mutation result does not reflect the requested target ${expectedPath}`),
    scope: "locations",
    causalityRevision: result.causalityRevision,
  });
  return { outcome: "write-error", diagnosticId: diagnostic.id, recovery: "refresh" };
}
