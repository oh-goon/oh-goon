import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { AdoptUvPreview } from "@generated/models";
import type { ReadResource } from "@/domain/reads/ReadResource";

export interface AdoptUvPreviewState { resource: ReadResource<AdoptUvPreview> }

export function useAdoptUvPreview(enabled: boolean): AdoptUvPreviewState {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback((signal: AbortSignal) => client.adoptUvPreview({ signal }), [client]);
  const { resource } = useReadResource({
    scope: "migration-preview",
    source: "GET /fleet/adopt-uv",
    enabled,
    errorMessage: t("errors.refresh"),
    request,
  });
  return { resource };
}
