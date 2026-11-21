import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient, useMutationBusy, usePresentationLocationAuthority, useReportMutationFeedback } from "@/app/ApiClientProvider";
import { appLocationHash, queryParamsFromHash, writeAppLocation } from "@/app/navigation";
import { useReadResource } from "@/app/reads/useReadResource";
import { ErrorBanner } from "@/components/common/ErrorBanner";
import { newFleetRunId } from "@/domain/identity/commandIdentity";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import { GameTapeRunFeedback } from "@/features/game-tape/GameTapeFeature";
import type { AgentContinuationInspection, AgentContinuationReview } from "@generated/models";

type Implementation = AgentContinuationReview["implementation"];

export function CampaignContinuationPanel({ campaignId }: { campaignId: string }) {
  const { t } = useTranslation();
  const client = useApiClient();
  const busy = useMutationBusy();
  const location = usePresentationLocationAuthority();
  const reportMutation = useReportMutationFeedback("fleet-run");
  const [implementation, setImplementation] = useState<Implementation | "">("");
  const [profile, setProfile] = useState("");
  const [feedback, setFeedback] = useState("");
  const [authorized, setAuthorized] = useState<string | null>(null);
  const [proposedRunId, setProposedRunId] = useState(() => queryParamsFromHash().get("continuation_run")?.trim() || null);
  const [submission, setSubmission] = useState<"idle" | "pending" | "admitted" | "unknown">(() => proposedRunId ? "unknown" : "idle");
  const submitting = useRef(false);
  const inspectionHeading = useRef<HTMLHeadingElement>(null);
  const focusOwner = useRef<ReturnType<typeof location.capture> | null>(null);
  const selectionIdentity = JSON.stringify({ campaignId, implementation, profile });
  const requestInspection = useCallback((signal: AbortSignal) => client.inspectAgentContinuation(
    campaignId, implementation || undefined, profile || undefined, { signal },
  ), [campaignId, client, implementation, profile]);
  const inspectionRead = useReadResource<AgentContinuationInspection>({
    scope: "agent-continuation", source: "Campaign continuation inspection", identity: selectionIdentity,
    auto: false, errorMessage: t("campaignContinuation.inspectionFailed"), request: requestInspection,
    validate: (value) => {
      if (value.campaign_id !== campaignId || value.review.campaign_id !== campaignId
        || value.review.previous_run_id !== value.previous_run_id || value.review.repository_id !== value.repository.repository_id
        || value.feedback.fleet_run_id !== value.previous_run_id
        || value.review.repository_instance_id !== value.source.repository_instance_id
        || value.review.selection_identity !== value.selection.selection_identity
        || value.review.implementation !== value.selection.implementation || value.review.profile !== value.selection.profile
        || implementation !== "" && value.review.implementation !== implementation || profile !== "" && value.review.profile !== profile
        || canonicalIdentity(value.review.source_authority) !== canonicalIdentity(value.source.source_authority)) throw new Error("Campaign continuation inspection identity mismatch");
    },
  });
  const inspection = inspectionRead.resource.data;
  useEffect(() => {
    if (focusOwner.current === null || inspectionRead.resource.status !== "read-fresh") return;
    if (location.owns(focusOwner.current)) inspectionHeading.current?.focus();
    focusOwner.current = null;
  }, [inspectionRead.resource.fetchedAt, inspectionRead.resource.status, location]);
  const checking = inspectionRead.resource.status === "read-loading" || inspectionRead.resource.status === "read-refreshing";
  const authorizationIdentity = JSON.stringify({ review: inspection?.review, selectionIdentity, feedback });
  const isAuthorized = authorized === authorizationIdentity;
  const feedbackBytes = new TextEncoder().encode(feedback).byteLength;
  const canSubmit = inspectionRead.resource.status === "read-fresh" && inspection?.can_continue === true
    && inspection.review !== null && feedback.trim().length > 0 && feedbackBytes <= 8192
    && isAuthorized && proposedRunId === null && !busy && !submitting.current;
  const requestOutcome = useCallback((signal: AbortSignal) => {
    if (proposedRunId === null) return Promise.reject(new Error("Continuation outcome requested without a Run identity"));
    return client.fleetRunDetail(proposedRunId, { signal });
  }, [client, proposedRunId]);
  const outcomeRead = useReadResource({
    scope: "agent-continuation-outcome", source: "Continuation Run outcome", identity: proposedRunId,
    enabled: proposedRunId !== null, auto: false, errorMessage: t("campaignContinuation.outcomeUnavailable"),
    request: requestOutcome,
    validate: (value) => {
      if (value.run.fleet_run_id !== proposedRunId || value.run.target?.kind !== "fleet_plan"
        || value.run.target.agent_continuation?.review.campaign_id !== campaignId) throw new Error("Continuation Run outcome identity mismatch");
    },
  });

  const check = async () => {
    if (checking || submitting.current) return;
    setAuthorized(null);
    focusOwner.current = location.capture();
    const value = await inspectionRead.refresh();
    if (value === null) focusOwner.current = null;
  };
  const submit = async () => {
    if (!canSubmit || !inspection?.review || submitting.current) return;
    submitting.current = true;
    setAuthorized(null);
    const id = newFleetRunId();
    setProposedRunId(id);
    setSubmission("pending");
    writeAppLocation("replace", "runs", { campaign: campaignId, continuation_run: id });
    const owner = location.capture();
    try {
      const result = await client.continueAgentCampaign(campaignId, {
        new_fleet_run_id: id, review: inspection.review, feedback, authorize: true,
      });
      reportMutation(result);
      if (!location.owns(owner)) return;
      if (result.outcome === "saved" && result.value.fleet_run_id === id
        && canonicalIdentity(result.value.receipt.review) === canonicalIdentity(inspection.review)) {
        setSubmission("admitted");
      } else setSubmission("unknown");
    } catch {
      if (location.owns(owner)) setSubmission("unknown");
    } finally {
      submitting.current = false;
    }
  };
  const observeOutcome = async () => {
    const owner = location.capture();
    const value = await outcomeRead.refresh();
    if (value !== null && location.owns(owner)) setSubmission("admitted");
  };
  return <section className="panel campaign-continuation" aria-label={t("campaignContinuation.title")}>
    <h2>{t("campaignContinuation.title")}</h2>
    <p>{t("campaignContinuation.explanation")}</p>
    <p>{t("campaignContinuation.observer")}</p>
    <div className="form-grid">
      <label><span>{t("campaignContinuation.implementation")}</span><select value={implementation} disabled={proposedRunId !== null} onChange={(event) => { setImplementation(event.target.value as Implementation | ""); setAuthorized(null); }}>
        <option value="">{t("campaignContinuation.recordedSelection")}</option>
        <option value="codex">codex</option><option value="pi">pi</option><option value="opencode">opencode</option>
      </select></label>
      <label><span>{t("campaignContinuation.profile")}</span><input value={profile} disabled={proposedRunId !== null} onChange={(event) => { setProfile(event.target.value); setAuthorized(null); }} /></label>
    </div>
    <p>{t("campaignContinuation.selectionHelp")}</p>
    <button className="button button--secondary" type="button" disabled={checking || submission === "pending"} onClick={() => { void check(); }}>{checking ? t("campaignContinuation.checking") : t("campaignContinuation.check")}</button>
    {inspectionRead.resource.failure ? <ErrorBanner message={inspectionRead.resource.failure.message} /> : null}
    {inspection ? <section aria-label={t("campaignContinuation.inspection")}>
      <h3 ref={inspectionHeading} tabIndex={-1}>{t("campaignContinuation.inspection")}</h3>
      <p role="status">{t(`campaignContinuation.states.${inspection.state}`)}</p>
      <dl className="game-tape-receipt">
        <div><dt>{t("campaignContinuation.repository")}</dt><dd>{inspection.repository.name}<br /><code>{inspection.repository.repository_id}</code></dd></div>
        <div><dt>{t("campaignContinuation.originalRun")}</dt><dd><a href={appLocationHash("runs", { run: inspection.previous_run_id })}>{inspection.previous_run_id}</a></dd></div>
        <div><dt>{t("campaignContinuation.recordedCandidate")}</dt><dd><code>{inspection.source?.recorded_candidate_identity ?? t("gameTape.feedback.unavailable")}</code></dd></div>
        <div><dt>{t("campaignContinuation.currentCandidate")}</dt><dd><code>{inspection.source?.source_authority.source_snapshot.sha256 ?? t("gameTape.feedback.unavailable")}</code></dd></div>
        <div><dt>{t("campaignContinuation.currentSubject")}</dt><dd><code>{inspection.source?.repository_instance_id ?? t("gameTape.feedback.unavailable")}</code></dd></div>
        {inspection.review ? <div><dt>{t("campaignContinuation.currentSelection")}</dt><dd>{inspection.review.implementation} · {inspection.review.profile}<br /><code>{inspection.review.selection_identity}</code></dd></div> : null}
      </dl>
      <p>{inspection.verification_status} · {inspection.verification_reason}</p>
      <p>{inspection.selection.mode} · {inspection.selection.selected_backend} · {inspection.selection.sandbox_owner} · {inspection.selection.environment_qualification}</p>
      <p><code>{inspection.selection.executable_sha256}</code></p>
      {inspection.review ? <details className="advanced-disclosure"><summary>{t("campaignContinuation.checks")}</summary><pre>{JSON.stringify((inspection.review.verification ?? []).map((argv, index) => ({ argv, timeout_seconds: inspection.review.verification_timeouts?.[index] })), null, 2)}</pre></details> : null}
      {inspection.refusal_reasons?.length ? <ul>{inspection.refusal_reasons.map((reason, index) => <li key={`${index}:${reason}`}>{reason}</li>)}</ul> : null}
      <details className="advanced-disclosure"><summary>{t("campaignContinuation.diff")}</summary>
        <p>{t("campaignContinuation.diffBaseline")}</p>
        <p>{t("campaignContinuation.diffBytes", { total: inspection.source.diff.byte_count ?? t("gameTape.feedback.unavailable"), omitted: inspection.source.diff.omitted_bytes ?? t("gameTape.feedback.unavailable") })}</p>
        {inspection.source?.diff.availability === "available" ? <>
          {inspection.source.diff.reason ? <p>{inspection.source.diff.reason}</p> : null}
          {inspection.source.diff.truncated ? <p role="status">{t("campaignContinuation.diffTruncated")}</p> : null}
          <pre>{inspection.source.diff.text || t("campaignContinuation.noDiff")}</pre>
        </> : <p>{t("campaignContinuation.diffUnavailable")} {inspection.source?.diff.reason}</p>}
      </details>
      <GameTapeRunFeedback feedback={inspection.feedback} runId={inspection.previous_run_id} campaignId={campaignId} />
    </section> : null}
    <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <label><span>{t("campaignContinuation.feedback")}</span><textarea value={feedback} required disabled={proposedRunId !== null} onChange={(event) => { setFeedback(event.target.value); setAuthorized(null); }} /></label>
      <p>{t("campaignContinuation.feedbackLimit", { count: feedbackBytes })}</p>
      <label className="checkbox-row"><input type="checkbox" checked={isAuthorized} disabled={!inspection?.can_continue || inspectionRead.resource.status !== "read-fresh" || proposedRunId !== null || feedback.trim().length === 0 || feedbackBytes > 8192} onChange={(event) => setAuthorized(event.target.checked ? authorizationIdentity : null)} />{t("campaignContinuation.authorize")}</label>
      <button className="button button--primary" type="submit" disabled={!canSubmit}>{t("campaignContinuation.submit")}</button>
    </form>
    {proposedRunId ? <section aria-label={t("campaignContinuation.submission")}>
      <h3>{t("campaignContinuation.submission")}</h3>
      <p><code>{proposedRunId}</code></p>
      <p role="status">{t(`campaignContinuation.submissionStates.${submission}`)}</p>
      <div className="button-row">
        <a className="button button--secondary" href={appLocationHash("runs", { run: proposedRunId })}>{t("campaignContinuation.openRun")}</a>
        <button className="button button--secondary" type="button" disabled={submission === "pending" || outcomeRead.resource.status === "read-loading" || outcomeRead.resource.status === "read-refreshing"} onClick={() => { void observeOutcome(); }}>{t("campaignContinuation.checkOutcome")}</button>
      </div>
      {outcomeRead.resource.failure ? <ErrorBanner message={outcomeRead.resource.failure.message} /> : null}
      {outcomeRead.resource.data ? <p>{outcomeRead.resource.data.run.status}{"outcome" in outcomeRead.resource.data.run ? ` · ${outcomeRead.resource.data.run.outcome}` : ""}</p> : null}
      {outcomeRead.resource.data?.feedback?.continuation_state ? <p role="status">{t(`campaignContinuation.states.${outcomeRead.resource.data.feedback.continuation_state}`)}</p> : null}
    </section> : null}
  </section>;
}
