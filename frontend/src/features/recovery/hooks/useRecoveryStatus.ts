import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { RecoveryStatus } from "@generated/models";
import type { ReadResource } from "@/domain/reads/ReadResource";

const RECOVERY_POLL_MS = 3_000;

export interface RecoveryStatusState {
  readonly resource: ReadResource<RecoveryStatus>;
  refresh(): Promise<RecoveryStatus | null>;
}

export function useRecoveryStatus(): RecoveryStatusState {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback((signal: AbortSignal) => client.recoveryStatus({ signal }), [client]);
  const { resource, refresh } = useReadResource({
    scope: "recovery",
    source: "GET /recovery",
    errorMessage: t("errors.recovery"),
    request,
    pollIntervalMs: RECOVERY_POLL_MS,
  });
  return { resource, refresh };
}
