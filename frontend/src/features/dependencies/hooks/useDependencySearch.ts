import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import type { ReadResource } from "@/domain/reads/ReadResource";
import type { DependencyEcosystem } from "@/features/dependencies/model";
import type { DependencySearchResult } from "@generated/models";

const SEARCH_PAGE_LIMIT = 30;

export function useDependencySearch(query: string, offset: number, ecosystem?: DependencyEcosystem): ReadResource<DependencySearchResult> {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback(
    (signal: AbortSignal) => client.dependencySearch(query, offset, SEARCH_PAGE_LIMIT, ecosystem, { signal }),
    [client, offset, query, ecosystem],
  );
  return useReadResource<DependencySearchResult>({
    scope: "dependency-search",
    source: "GET /dependencies/search",
    identity: query ? canonicalIdentity({ query, offset, ecosystem }) : null,
    enabled: Boolean(query),
    errorMessage: t("dependencies.errors.search"),
    request,
    validate: (value) => {
      if (value.query !== query || (ecosystem !== undefined && value.ecosystem !== ecosystem) || value.offset !== offset || value.limit !== SEARCH_PAGE_LIMIT) {
        throw new Error("Dependency search response does not match the requested query page");
      }
    },
  }).resource;
}
