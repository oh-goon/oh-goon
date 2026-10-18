import { useCallback } from "react";

import { useApiClient, useReportMutationFeedback } from "@/app/ApiClientProvider";
import { appDiagnosticStore } from "@/domain/diagnostics/appDiagnosticStore";
import type { LocationsResponse } from "@generated/models";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";

export interface AddLocationState {
  add(path: string): Promise<WriteCommandResult<LocationsResponse>>;
}

export function useAddLocation(): AddLocationState {
  const client = useApiClient();
  const reportMutationFeedback = useReportMutationFeedback("locations");
  const add = useCallback(async (path: string) => {
    const result = validateLocationMutationIdentity(await client.addLocation(path), path, true);
    reportMutationFeedback(result);
    return result;
  }, [client, reportMutationFeedback]);
  return { add };
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
