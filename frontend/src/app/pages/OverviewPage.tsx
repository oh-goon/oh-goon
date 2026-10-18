import { FleetRunOutcome, FleetRunStatus } from "@generated/enums";
import type { FleetRunOutcome as FleetRunOutcomeType, FleetRunStatus as FleetRunStatusType } from "@generated/models";
import { useTranslation } from "react-i18next";

import { useFleetSnapshot } from "@/app/FleetRunProvider";
import { queryParamsFromHash, writeAppLocation, type AppPage } from "@/app/navigation";
import { ErrorBanner } from "@/components/common/ErrorBanner";
import { PageHeading } from "@/components/common/PageHeading";
import { ExecutionTimelineGrid, useExecutionOverview, type ExecutionObservationBucket, type ExecutionTimelineViewState } from "@/features/overview/OverviewFeature";
import { useDependencyFleetCoverage } from "@/features/dependencies/DependenciesFeature";
import { useFailureInventory } from "@/features/failures/FailuresFeature";

interface Props {
  navigate(page: AppPage, query?: Readonly<Record<string, string>>): void;
}

export function OverviewPage({ navigate }: Props) {
  const { t } = useTranslation();
  const overview = useExecutionOverview();
  const dependencyCoverage = useDependencyFleetCoverage();
  const fleet = useFleetSnapshot();
  const failures = useFailureInventory();
  const data = overview.resource.data;
  const error = overview.resource.status === "read-error" ? overview.resource.failure?.message ?? null : null;
  const repositories = fleet.resource.data?.summary.discovered ?? 0;
  const openOutcome = (outcome: FleetRunOutcomeType) => navigate("runs", { history_outcome: outcome });
  const openStatus = (status: FleetRunStatusType) => navigate("runs", { history_status: status });
  const timelineState = readOverviewTimelineState();
  const setTimelineState = (state: ExecutionTimelineViewState) => {
    writeOverviewTimelineState(state);
  };

  return <main className="page overview-page">
    <PageHeading eyebrow={t("executionOverview.eyebrow")} title={t("executionOverview.title")} description={t("executionOverview.description")} />
    {error ? <ErrorBanner message={error} /> : null}
    <ExecutionTimelineGrid state={timelineState} onStateChange={setTimelineState} live={Boolean(data && (data.running > 0 || data.queued > 0 || data.cancelling > 0))} onOpenRuns={(repositoryId, fromAt, toAt, planQuery) => navigate("runs", { repository: repositoryId, history_from: fromAt, history_to: toAt, ...(planQuery.trim() ? { history_plan: planQuery.trim() } : {}) })} />
    <section className="panel execution-overview" aria-label={t("executionOverview.execution")}>
      <div className="execution-overview__identity">
        <span className="eyebrow">{t("executionOverview.repositories")}</span>
        <strong>{repositories.toLocaleString()}</strong>
        <p>{t("executionOverview.rule")}</p>
      </div>
      <div className="execution-overview__states">
        <div className="execution-overview__states-heading"><h2>{t("executionOverview.execution")}</h2>{data ? <span>{t("executionOverview.total", { count: data.total })}</span> : null}</div>
        {data ? <div className="execution-state-grid">
          <StateButton label={t("executionOverview.states.successful")} value={data.successful} symbol="✓" tone="success" onClick={() => openOutcome(FleetRunOutcome.SUCCESS)} />
          <StateButton label={t("executionOverview.states.failed")} value={data.failed} symbol="×" tone="danger" onClick={() => openOutcome(FleetRunOutcome.FAILED)} />
          <StateButton label={t("executionOverview.states.running")} value={data.running} symbol="▶" tone="info" onClick={() => openStatus(FleetRunStatus.RUNNING)} />
          <StateButton label={t("executionOverview.states.queued")} value={data.queued} symbol="◷" tone="neutral" onClick={() => openStatus(FleetRunStatus.QUEUED)} />
          {data.attention_required > 0 ? <StateButton label={t("executionOverview.states.attentionRequired")} value={data.attention_required} symbol="!" tone="warning" onClick={() => openOutcome(FleetRunOutcome.ATTENTION_REQUIRED)} /> : null}
          {data.cancelling > 0 ? <StateButton label={t("executionOverview.states.cancelling")} value={data.cancelling} symbol="…" tone="warning" onClick={() => openStatus(FleetRunStatus.CANCELLING)} /> : null}
          {data.cancelled > 0 ? <StateButton label={t("executionOverview.states.cancelled")} value={data.cancelled} symbol="−" tone="neutral" onClick={() => openOutcome(FleetRunOutcome.CANCELLED)} /> : null}
        </div> : <p className="muted loading-state" role="status">{t("executionOverview.loading")}</p>}
      </div>
    </section>
    {failures.resource.data && failures.resource.data.failing_repositories > 0 ? <section className="panel observed-evidence-entry" aria-label={t("executionOverview.currentFailuresTitle")}>
      <div className="section-heading"><div><span className="eyebrow">{t("executionOverview.currentFailuresEyebrow")}</span><h2>{t("executionOverview.currentFailuresTitle")}</h2><p>{t("executionOverview.currentFailuresDescription")}</p></div></div>
      <div className="observed-evidence-entry__body"><div className="observed-evidence-entry__facts"><span><strong>{failures.resource.data.failing_repositories.toLocaleString()}</strong><small>{t("executionOverview.currentFailingRepositories")}</small></span><span><strong>{failures.resource.data.failure_groups.toLocaleString()}</strong><small>{t("executionOverview.currentFailureGroups")}</small></span></div><button className="button button--secondary" type="button" onClick={() => navigate("failures")}>{t("executionOverview.investigateFailures")}</button></div>
    </section> : null}
    {dependencyCoverage.data && dependencyCoverage.data.no_current_dependency_evidence > 0 ? <section className="panel observed-evidence-entry" aria-label={t("executionOverview.observedEvidenceTitle")}>
      <div className="section-heading"><div><span className="eyebrow">{t("executionOverview.observedEvidenceEyebrow")}</span><h2>{t("executionOverview.observedEvidenceTitle")}</h2><p>{t("executionOverview.observedEvidenceDescription")}</p></div></div>
      <div className="observed-evidence-entry__body">
        <div className="observed-evidence-entry__facts">
          <span><strong>{dependencyCoverage.data.no_current_dependency_evidence.toLocaleString()}</strong><small>{t("executionOverview.noCurrentDependencyEvidence")}</small></span>
        </div>
        <button className="button button--secondary" type="button" onClick={() => navigate("dependencies")}>{t("executionOverview.exploreDependencies")}</button>
      </div>
    </section> : null}
  </main>;
}

type StateTone = "success" | "danger" | "info" | "warning" | "neutral";

function StateButton({ label, value, symbol, tone, onClick }: { label: string; value: number; symbol: string; tone: StateTone; onClick(): void }) {
  return <button className="execution-state-card" data-tone={tone} type="button" onClick={onClick}>
    <span className="execution-state-card__symbol" aria-hidden="true">{symbol}</span>
    <span>{label}</span>
    <strong>{value.toLocaleString()}</strong>
  </button>;
}


const OVERVIEW_TIMELINE_BUCKETS = new Set<ExecutionObservationBucket>(["day", "week", "month"]);

function readOverviewTimelineState(): ExecutionTimelineViewState {
  const params = queryParamsFromHash();
  const requestedBucket = params.get("period") as ExecutionObservationBucket | null;
  const requestedOffset = Number.parseInt(params.get("offset") ?? "0", 10);
  return {
    bucket: requestedBucket && OVERVIEW_TIMELINE_BUCKETS.has(requestedBucket) ? requestedBucket : "day",
    planQuery: params.get("plan") ?? "",
    repositoryQuery: params.get("repository") ?? "",
    offset: Number.isSafeInteger(requestedOffset) && requestedOffset > 0 ? requestedOffset : 0,
  };
}

function writeOverviewTimelineState(state: ExecutionTimelineViewState): void {
  const params = new URLSearchParams();
  if (state.bucket !== "day") params.set("period", state.bucket);
  if (state.planQuery.trim()) params.set("plan", state.planQuery);
  if (state.repositoryQuery.trim()) params.set("repository", state.repositoryQuery);
  if (state.offset > 0) params.set("offset", String(state.offset));
  writeAppLocation("replace", "overview", Object.fromEntries(params));
}
