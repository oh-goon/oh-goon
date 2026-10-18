import { useCallback, useEffect } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import { retainDataUnlessNotFound } from "@/domain/reads/retainDataOnError";
import type { ReadResource } from "@/domain/reads/ReadResource";
import type { ScoutRunEvidence } from "@generated/models";

export function useScoutRunEvidence(runId: string, enabled: boolean): ReadResource<ScoutRunEvidence> {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback((signal: AbortSignal) => client.scoutRun(runId, { signal }), [client, runId]);
  const handle = useReadResource({
    scope: "scout-run-evidence",
    source: "GET /scout/runs/:runId",
    identity: enabled ? runId : null,
    enabled,
    errorMessage: t("errors.scout"),
    request,
    pollIntervalMs: 3_000,
    shouldPoll: (value) => (value.repositories ?? []).some((repository) => ["PLANNED", "ADMITTED", "RUNNING"].includes(repository.status)),
    retainDataOnError: retainDataUnlessNotFound,
    validate: (value) => {
      if (value.run_id !== runId) throw new Error(`Scout Run evidence identity mismatch: requested ${runId}, received ${value.run_id}`);
    },
  });
  const { resource, refresh } = handle;
  useEffect(() => {
    if (!enabled || resource.data === null) return;
    if ((resource.data.repositories ?? []).some((repository) => ["PLANNED", "ADMITTED", "RUNNING"].includes(repository.status))) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, 300_000);
    const resume = () => { if (document.visibilityState === "visible") void refresh(); };
    document.addEventListener("visibilitychange", resume);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", resume); };
  }, [enabled, refresh, resource.data]);
  return resource;
}
