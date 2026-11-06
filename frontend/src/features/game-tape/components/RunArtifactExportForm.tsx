import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient, useMutationBusy, useReportMutationFeedback } from "@/app/ApiClientProvider";
import { ErrorBanner } from "@/components/common/ErrorBanner";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import type { PresentPlanArtifact, RunArtifactExport } from "@generated/models";

interface Props {
  fleetRunId: string;
  repositoryId: string;
  stepId: string;
  artifact: PresentPlanArtifact;
}

export function RunArtifactExportForm(props: Props) {
  const identity = canonicalIdentity({ fleetRunId: props.fleetRunId, repositoryId: props.repositoryId, stepId: props.stepId, artifact: props.artifact });
  return <RecordedArtifactExport key={identity} {...props} />;
}

function RecordedArtifactExport({ fleetRunId, repositoryId, stepId, artifact }: Props) {
  const { t } = useTranslation();
  const client = useApiClient();
  const busy = useMutationBusy();
  const reportMutation = useReportMutationFeedback("fleet-run");
  const [destination, setDestination] = useState("");
  const [exported, setExported] = useState<RunArtifactExport | null>(null);
  const [failed, setFailed] = useState(false);
  const [pending, setPending] = useState(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const publish = async () => {
    if (busy || pending || !destination.trim() || !artifact.retained_object) return;
    setExported(null);
    setFailed(false);
    setPending(true);
    try {
      const result = await client.exportRunArtifact(fleetRunId, { repository_id: repositoryId, step_id: stepId, path: artifact.path, destination });
      reportMutation(result);
      if (!mounted.current) return;
      if (result.outcome !== "saved") { setFailed(true); return; }
      const receipt = result.value;
      if (receipt.fleet_run_id !== fleetRunId || receipt.repository_id !== repositoryId || receipt.step_id !== stepId || receipt.artifact_path !== artifact.path || receipt.sha256 !== artifact.sha256 || receipt.byte_count !== artifact.byte_count || receipt.reference_id !== artifact.retained_object.reference_id) { setFailed(true); return; }
      setExported(receipt);
    } finally { if (mounted.current) setPending(false); }
  };
  return <div className="artifact-export-form">
    <p className="muted">{t("catalog.exportHelp")}</p>
    <label className="field"><span>{t("catalog.destination")}</span><input value={destination} disabled={busy || pending} onChange={(event) => { setDestination(event.target.value); setExported(null); setFailed(false); }} placeholder={t("catalog.destinationPlaceholder")} /></label>
    <button className="button button--primary" type="button" disabled={busy || pending || !artifact.retained_object || !destination.trim()} onClick={() => void publish()}>{pending ? t("catalog.exporting") : t("catalog.exportButton")}</button>
    {failed ? <ErrorBanner message={t("catalog.exportFailed")} /> : null}
    {exported ? <div className="notice notice--scope" role="status"><strong>{t("catalog.exported")}</strong><p>{exported.destination}</p><p><code>{exported.sha256}</code> · {exported.byte_count} {t("catalog.bytes")}</p>{exported.cleanup_status === "failed" ? <p className="notice notice--warning">{t("catalog.cleanupFailed")}</p> : null}</div> : null}
  </div>;
}
