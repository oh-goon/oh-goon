import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { appLocationHash, queryParamsFromHash, writeAppLocation } from "@/app/navigation";
import { useReadResource } from "@/app/reads/useReadResource";
import { ErrorBanner } from "@/components/common/ErrorBanner";
import { PageHeading } from "@/components/common/PageHeading";
import { GameTapeCampaignJournal } from "@/features/game-tape/GameTapeFeature";
import type { AgentCampaignResult } from "@generated/models";

export function CampaignPage() {
  const { t } = useTranslation();
  const client = useApiClient();
  const campaignId = queryParamsFromHash().get("campaign")?.trim() ?? "";
  const [draft, setDraft] = useState(campaignId);
  const request = useCallback(
    (signal: AbortSignal) => client.agentCampaign(campaignId, { signal }),
    [client, campaignId],
  );
  const read = useReadResource<AgentCampaignResult>({
    scope: "agent-campaign", source: "Campaign readback", identity: campaignId || null,
    enabled: Boolean(campaignId), errorMessage: t("gameTape.campaign.loadFailed"), request,
    validate: (value) => {
      if (value.campaign_id !== campaignId) throw new Error("Campaign result identity mismatch");
    },
  });
  const result = read.resource.data;
  const loading = read.resource.status === "read-loading" || read.resource.status === "read-refreshing";
  return <main className="page">
    <PageHeading eyebrow={t("runsPage.eyebrow")} title={t("gameTape.campaign.title")} description={t("gameTape.campaign.description")} />
    <section className="panel">
      <form onSubmit={(event) => { event.preventDefault(); if (draft.trim()) writeAppLocation("push", "runs", { campaign: draft.trim() }); }}>
        <label><span>{t("gameTape.campaign.identity")}</span><input value={draft} required onChange={(event) => setDraft(event.target.value)} /></label>
        <div className="button-row"><button className="button button--primary" type="submit" disabled={!draft.trim()}>{t("gameTape.campaign.open")}</button>
          <a className="button button--secondary" href={appLocationHash("runs")}>{t("runsPage.title")}</a></div>
      </form>
    </section>
    {loading ? <p role="status">{t("gameTape.campaign.loading")}</p> : null}
    {read.resource.failure ? <ErrorBanner message={read.resource.failure.message} /> : null}
    {result ? <section className="panel">
      <h2>{t("gameTape.campaign.result")}: {result.result}</h2>
      {result.incomplete_reasons.length ? <ul>{result.incomplete_reasons.map((reason, index) => <li key={`${index}:${reason}`}>{reason}</li>)}</ul> : null}
      {result.journal_revision !== null ? <GameTapeCampaignJournal key={result.campaign_id} campaign={{
        campaign_id: result.campaign_id, journal_revision: result.journal_revision,
        complete: result.complete, records: result.decision_history,
      }} /> : <p>{t("sharedPlans.agent.unrecorded")}</p>}
      {result.runs.length ? <ul>{result.runs.map((run, index) => typeof run.fleet_run_id === "string" ? <li key={`${index}:${run.fleet_run_id}`}><a href={appLocationHash("runs", { run: run.fleet_run_id })}>{run.fleet_run_id}</a></li> : null)}</ul> : <p>{t("gameTape.campaign.noRuns")}</p>}
      <details className="advanced-disclosure"><summary>{t("gameTape.campaign.structuredResult")}</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>
    </section> : null}
  </main>;
}
