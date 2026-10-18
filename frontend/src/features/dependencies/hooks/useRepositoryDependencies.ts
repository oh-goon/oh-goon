import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import type { ReadResource } from "@/domain/reads/ReadResource";
import type { RepositoryDependencyPage } from "@generated/models";

const REPOSITORY_DEPENDENCY_PAGE_LIMIT = 100;

export function useRepositoryDependencies(repositoryId: string, offset: number): ReadResource<RepositoryDependencyPage> {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback(
    (signal: AbortSignal) => client.repositoryDependencies(repositoryId, offset, REPOSITORY_DEPENDENCY_PAGE_LIMIT, { signal }),
    [client, offset, repositoryId],
  );
  return useReadResource<RepositoryDependencyPage>({
    scope: "repository-dependencies",
    source: "GET /dependencies/repositories/:repository",
    identity: repositoryId ? canonicalIdentity({ repositoryId, offset }) : null,
    enabled: Boolean(repositoryId),
    errorMessage: t("dependencies.errors.repository"),
    request,
    validate: (value) => {
      if (value.repository.repository_id !== repositoryId || value.offset !== offset || value.limit !== REPOSITORY_DEPENDENCY_PAGE_LIMIT) {
        throw new Error("Repository dependency response does not match the requested repository page");
      }
    },
  }).resource;
}
