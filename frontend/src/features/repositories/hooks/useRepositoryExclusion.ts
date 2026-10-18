import { useCallback } from "react";

import { useApiClient, useReportMutationFeedback } from "@/app/ApiClientProvider";
import type { FleetRepository } from "@generated/models";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";

export interface RepositoryExclusionState {
  setExcluded(repository: FleetRepository, repositoryInstanceId: string, excluded: boolean): Promise<WriteCommandResult<unknown>>;
}

export function useRepositoryExclusion(): RepositoryExclusionState {
  const client = useApiClient();
  const reportMutationFeedback = useReportMutationFeedback("repositories");
  const setExcluded = useCallback(async (
    repository: FleetRepository,
    repositoryInstanceId: string,
    excluded: boolean,
  ): Promise<WriteCommandResult<unknown>> => {
    const result = excluded
      ? await client.excludeRepository(repository.repository.path, repositoryInstanceId)
      : await client.includeRepository(repository.repository.path, repositoryInstanceId);
    reportMutationFeedback(result);
    return result;
  }, [client, reportMutationFeedback]);
  return { setExcluded };
}
