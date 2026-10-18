import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { PrivacyResponse } from "@generated/models";
import type { ReadResource } from "@/domain/reads/ReadResource";

export interface PrivacyResponseState { resource: ReadResource<PrivacyResponse> }

export function usePrivacyResponse(): PrivacyResponseState {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback((signal: AbortSignal) => client.privacy({ signal }), [client]);
  const { resource } = useReadResource({
    scope: "privacy",
    source: "GET /privacy",
    errorMessage: t("errors.refresh"),
    request,
  });
  return { resource };
}
