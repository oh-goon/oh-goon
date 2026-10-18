import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import type { ReadResource } from "@/domain/reads/ReadResource";
import type { DependencySearchResult } from "@generated/models";

const SEARCH_PAGE_LIMIT = 30;

export function useDependencySearch(query: string, offset: number): ReadResource<DependencySearchResult> {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback(
    (signal: AbortSignal) => client.dependencySearch(query, offset, SEARCH_PAGE_LIMIT, { signal }),
    [client, offset, query],
  );
  return useReadResource<DependencySearchResult>({
    scope: "dependency-search",
    source: "GET /dependencies/search",
    identity: query ? canonicalIdentity({ query, offset }) : null,
    enabled: Boolean(query),
    errorMessage: t("dependencies.errors.search"),
    request,
    validate: (value) => {
      if (value.query !== query || value.offset !== offset || value.limit !== SEARCH_PAGE_LIMIT) {
        throw new Error("Dependency search response does not match the requested query page");
      }
    },
  }).resource;
}
