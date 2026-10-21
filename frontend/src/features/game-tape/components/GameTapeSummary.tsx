import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { StatusBadge } from "@/components/common/StatusBadge";
import { GameTapeObservabilityReceipt } from "@/features/game-tape/components/GameTapeObservabilityReceipt";
import { GameTapeConfiguration } from "@/features/game-tape/components/GameTapeConfiguration";
import { GameTapeTeamOverview } from "@/features/game-tape/components/GameTapeTeamOverview";
import { GameTapeStatus } from "@generated/enums";
import type { GameTape } from "@generated/models";
import { runLineage } from "@/domain/presentation/runProvenance";
import {
  duration,
  formatDateTime,
  shortSha,
  statusSymbol,
  statusTone,
} from "@/features/game-tape/gameTapeFormat";
import { gameTapeExecutionReference, gameTapeSourceLabel } from "@/features/game-tape/gameTapeExecutionReference";

const SUMMARY_REPOSITORY_PAGE_SIZE = 50;

export function GameTapeSummary({ tape, compact = false }: { tape: GameTape; compact?: boolean }) {
  const { t } = useTranslation();
  const summary = tape.summary;
  const firstIssue = tape.issue_focus?.[0] ?? null;
  const failedContext = tape.repositories.flatMap((repository) => (repository.steps ?? []).map((step) => ({ repository, step })))
    .find(({ step }) => step.status === GameTapeStatus.FAILED || step.status === GameTapeStatus.INTERRUPTED || step.status === GameTapeStatus.ATTENTION_REQUIRED);
  const failureAnswer = firstIssue?.message
    ?? (failedContext ? `${failedContext.repository.repository.name} → ${failedContext.step.step_id}` : t("gameTape.summary.noFailure"));
  const executionReference = gameTapeExecutionReference(tape);
  return <div className="game-tape-summary">
    <section className="game-tape-summary__answer" aria-label={t("gameTape.summary.answerTitle")}>
      <div className="game-tape-summary__outcome" data-status={tape.status}>
        <span className="game-tape-status-mark" data-status={tape.status} aria-hidden="true">{statusSymbol(tape.status)}</span>
        <div><span className="eyebrow">{t("gameTape.summary.outcome")}</span><StatusBadge label={t(`gameTape.status.${tape.status}`)} tone={statusTone(tape.status)} /></div>
      </div>
      <div><span className="eyebrow">{t("gameTape.summary.duration")}</span><strong>{duration(tape.receipt.duration_ms)}</strong></div>
      <div className={summary.failed_steps > 0 ? "game-tape-summary__failure-answer" : ""}><span className="eyebrow">{t("gameTape.summary.whatFailed")}</span><strong>{failureAnswer}</strong>{summary.failed_steps > 1 ? <small>{t("gameTape.summary.moreFailures", { count: summary.failed_steps - 1 })}</small> : null}</div>
    </section>
    <details className="advanced-disclosure game-tape-summary__run-facts"><summary>{t("gameTape.summary.moreRunFacts")}</summary>
      <dl className="game-tape-summary__facts">
        <div><dt>{t("gameTape.summary.plan")}</dt><dd>{executionReference?.plan_name ?? "—"}</dd></div>
        <div><dt>{t("gameTape.summary.repositories")}</dt><dd>{summary.repositories}</dd></div>
        <div><dt>{t("gameTape.summary.steps")}</dt><dd>{summary.executed_steps} / {summary.planned_steps}</dd></div>
        <div className="game-tape-summary__fact--passed"><dt>{t("gameTape.summary.passed")}</dt><dd>{summary.passed_steps}</dd></div>
        <div className="game-tape-summary__fact--failed"><dt>{t("gameTape.summary.failed")}</dt><dd>{summary.failed_steps}</dd></div>
        <div className="game-tape-summary__fact--not-run"><dt>{t("gameTape.summary.notRun")}</dt><dd>{summary.not_run_steps}</dd></div>
      </dl>
      <GameTapeTeamOverview tape={tape} />
    </details>
    <GameTapeConfiguration configuration={tape.configuration} />
    {!compact ? <details className="advanced-disclosure game-tape-technical-evidence"><summary>{t("gameTape.summary.technicalEvidence")}</summary>
      <p className="muted">{t("gameTape.summary.technicalEvidenceHelp")}</p>
      <TechnicalIdentity tape={tape} />
      <LaunchReceipt tape={tape} />
      <ExecutionReceipt tape={tape} />
      <GameTapeObservabilityReceipt tape={tape} />
      <PlanActual tape={tape} />
      <EnvironmentFingerprints tape={tape} />
    </details> : null}
  </div>;
}

function SummaryRepositoryPager({ page, pageCount, from, to, total, onPage }: { page: number; pageCount: number; from: number; to: number; total: number; onPage(page: number): void }) {
  const { t } = useTranslation();
  return <nav className="game-tape-repository-pagination" aria-label={t("gameTape.repositoryPaginationLabel")}><button className="button button--secondary button--compact" type="button" disabled={page === 0} onClick={() => onPage(Math.max(0, page - 1))}>{t("common.previous")}</button><span>{t("gameTape.repositoryPageRange", { from, to, total })}</span><button className="button button--secondary button--compact" type="button" disabled={page + 1 >= pageCount} onClick={() => onPage(Math.min(pageCount - 1, page + 1))}>{t("common.next")}</button></nav>;
}

function TechnicalIdentity({ tape }: { tape: GameTape }) {
  const { t } = useTranslation();
  const executionReference = gameTapeExecutionReference(tape);
  const lineage = runLineage(tape.execution);
  return <section className="game-tape-section">
    <header><div><span className="eyebrow">{t("gameTape.summary.identityEyebrow")}</span><h3>{t("gameTape.summary.identityTitle")}</h3></div></header>
    <dl className="game-tape-receipt">
      <div><dt>{t("gameTape.summary.run")}</dt><dd><code>{tape.fleet_run_id}</code></dd></div>
      <div><dt>{t("gameTape.summary.executionSource")}</dt><dd>{t(`history.sources.${tape.run_source ?? "MANUAL"}`)}{lineage ? <small>{t(`history.lineage.${lineage.kind}`, { runId: lineage.parentRunId })}</small> : null}</dd></div>
      <div><dt>{t("gameTape.summary.plan")}</dt><dd>{executionReference?.plan_name ?? "—"}<small><code>{executionReference?.plan_id}</code></small></dd></div>
      <div><dt>{t("gameTape.summary.source")}</dt><dd>{executionReference ? gameTapeSourceLabel(executionReference) : "—"}<small><code>{shortSha(executionReference?.source.sha256)}</code></small></dd></div>
    </dl>
  </section>;
}

function LaunchReceipt({ tape }: { tape: GameTape }) {
  const { t } = useTranslation();
  const launch = tape.application_launch;
  return <section className="game-tape-section">
    <header><div><span className="eyebrow">{t("gameTape.launch.eyebrow")}</span><h3>{t("gameTape.launch.title")}</h3></div></header>
    {!launch || !("launch_id" in launch) ? <p className="muted">{t("gameTape.launch.unmanaged")}</p> : <dl className="game-tape-receipt">
      <div><dt>{t("gameTape.launch.launchId")}</dt><dd><code>{launch.launch_id}</code></dd></div>
      <div><dt>{t("gameTape.launch.profile")}</dt><dd>{launch.profile}</dd></div>
      <div><dt>{t("gameTape.launch.intent")}</dt><dd><code>{shortSha(launch.resolved_intent_identity)}</code></dd></div>
      <div><dt>{t("gameTape.launch.ownerGeneration")}</dt><dd>{launch.owner_generation}</dd></div>
      <div><dt>{t("gameTape.launch.backendGeneration")}</dt><dd>{launch.backend_generation}</dd></div>
      <div><dt>{t("gameTape.launch.backendProcess")}</dt><dd><code>{launch.backend_pid}</code><small>{launch.backend_birth_provider} · {launch.backend_boot_id.slice(0, 12)} · {launch.backend_start_ticks}</small></dd></div>
      <div><dt>{t("gameTape.launch.observedEndpoint")}</dt><dd>{launch.observed_host && launch.observed_port ? `${launch.observed_host}:${launch.observed_port}` : t("gameTape.launch.endpointUnavailable")}</dd></div>
    </dl>}
    <p className="game-tape-guarantees">{t("gameTape.launch.contract")}</p>
  </section>;
}

function ExecutionReceipt({ tape }: { tape: GameTape }) {
  const { t } = useTranslation();
  const receipt = tape.receipt;
  return <section className="game-tape-section">
    <header><div><span className="eyebrow">{t("gameTape.receipt.eyebrow")}</span><h3>{t("gameTape.receipt.title")}</h3></div></header>
    <dl className="game-tape-receipt">
      <div><dt>{t("gameTape.receipt.validation")}</dt><dd>{receipt.validation}</dd></div>
      <div><dt>{t("gameTape.receipt.requirements")}</dt><dd>{receipt.requirements_admitted ?? "—"} / {(tape.requirements ?? []).length}</dd></div>
      <div><dt>{t("gameTape.receipt.planned")}</dt><dd>{tape.summary.planned_steps}</dd></div>
      <div><dt>{t("gameTape.receipt.executed")}</dt><dd>{tape.summary.executed_steps}</dd></div>
      <div><dt>{t("gameTape.receipt.succeeded")}</dt><dd>{tape.summary.passed_steps}</dd></div>
      <div><dt>{t("gameTape.receipt.started")}</dt><dd>{formatDateTime(receipt.started_at)}</dd></div>
      <div><dt>{t("gameTape.receipt.finished")}</dt><dd>{formatDateTime(receipt.finished_at)}</dd></div>
      <div><dt>{t("gameTape.receipt.failed")}</dt><dd>{tape.repositories.flatMap((repository) => (repository.steps ?? []).filter((step) => step.status === "FAILED").map((step) => `${repository.repository.name}:${step.step_id}`)).join(", ") || "—"}</dd></div>
    </dl>
    {(tape.requirements ?? []).length > 0 ? <div className="game-tape-requirements">
      <strong>{t("gameTape.receipt.requirementFacts")}</strong>
      <ol>{(tape.requirements ?? []).map((requirement) => <li key={`${requirement.order}-${requirement.kind}`}><b>{requirement.order}</b><span>{requirement.description}</span></li>)}</ol>
    </div> : null}
    <p className="game-tape-guarantees">{t("gameTape.receipt.guarantees")}</p>
  </section>;
}

function PlanActual({ tape }: { tape: GameTape }) {
  const { t } = useTranslation();
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(tape.repositories.length / SUMMARY_REPOSITORY_PAGE_SIZE));
  const boundedPage = Math.min(page, pageCount - 1);
  useEffect(() => { if (page !== boundedPage) setPage(boundedPage); }, [boundedPage, page]);
  const pageStart = boundedPage * SUMMARY_REPOSITORY_PAGE_SIZE;
  const repositories = tape.repositories.slice(pageStart, pageStart + SUMMARY_REPOSITORY_PAGE_SIZE);
  return <section className="game-tape-section">
    <header><div><span className="eyebrow">{t("gameTape.planActual.eyebrow")}</span><h3>{t("gameTape.planActual.title")}</h3></div></header>
    <div className="game-tape-plan-actual">
      {repositories.map((repository) => <article key={repository.repository_execution_id}>
        <header><strong>{repository.repository.name}</strong><span>{repository.steps?.length ?? 0} {t("gameTape.planActual.steps")}</span></header>
        <div className="game-tape-plan-actual__head"><span>{t("gameTape.planActual.plan")}</span><span>{t("gameTape.planActual.actual")}</span><span>{t("gameTape.planActual.time")}</span></div>
        {(repository.steps ?? []).map((step) => <div className="game-tape-plan-actual__row" key={step.step_execution_id}>
          <span><b>{step.order}</b>{step.step_id}</span>
          <StatusBadge label={t(`gameTape.status.${step.status}`)} tone={statusTone(step.status)} />
          <span>{duration(step.duration_ms)}</span>
        </div>)}
      </article>)}
    </div>
    {tape.repositories.length > SUMMARY_REPOSITORY_PAGE_SIZE ? <SummaryRepositoryPager page={boundedPage} pageCount={pageCount} from={pageStart + 1} to={Math.min(pageStart + repositories.length, tape.repositories.length)} total={tape.repositories.length} onPage={setPage} /> : null}
  </section>;
}

function EnvironmentFingerprints({ tape }: { tape: GameTape }) {
  const { t } = useTranslation();
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(tape.repositories.length / SUMMARY_REPOSITORY_PAGE_SIZE));
  const boundedPage = Math.min(page, pageCount - 1);
  useEffect(() => { if (page !== boundedPage) setPage(boundedPage); }, [boundedPage, page]);
  const pageStart = boundedPage * SUMMARY_REPOSITORY_PAGE_SIZE;
  const repositories = tape.repositories.slice(pageStart, pageStart + SUMMARY_REPOSITORY_PAGE_SIZE);
  return <section className="game-tape-section">
    <header><div><span className="eyebrow">{t("gameTape.environment.eyebrow")}</span><h3>{t("gameTape.environment.title")}</h3></div></header>
    <div className="game-tape-fingerprints">
      {repositories.map((repository) => {
        const fingerprint = repository.fingerprint;
        return <article key={repository.repository_execution_id}>
          <header><strong>{repository.repository.name}</strong><StatusBadge label={t(`gameTape.status.${repository.status}`)} tone={statusTone(repository.status)} /></header>
          <dl>
            <div><dt>{t("gameTape.environment.commit")}</dt><dd><code>{shortSha(fingerprint.repository_head_sha)}</code></dd></div>
            <div><dt>{t("gameTape.environment.branch")}</dt><dd>{fingerprint.branch ?? fingerprint.branch_state ?? "—"}</dd></div>
            <div><dt>{t("gameTape.environment.plan")}</dt><dd><code>{shortSha(fingerprint.plan_source_sha256)}</code></dd></div>
            <div><dt>{t("gameTape.environment.contract")}</dt><dd>{fingerprint.plan_contract_version ? `v${fingerprint.plan_contract_version}` : "—"}</dd></div>
            <div><dt>{t("gameTape.environment.input")}</dt><dd><code>{fingerprint.execution_input_id ?? "—"}</code></dd></div>
            <div><dt>{t("gameTape.environment.baseWorkspace")}</dt><dd><code>{shortSha(fingerprint.base_workspace_sha256)}</code></dd></div>
            <div><dt>{t("gameTape.environment.runtimeRoot")}</dt><dd><code>{shortSha(fingerprint.runtime_root_sha256)}</code></dd></div>
            <div><dt>{t("gameTape.environment.toolchain")}</dt><dd><code>{fingerprint.host_toolchain_id ?? "—"}</code></dd></div>
            <div><dt>{t("gameTape.environment.isolation")}</dt><dd>{fingerprint.isolation_backend_id ?? "—"}</dd></div>
            <div><dt>{t("gameTape.environment.isolationConfig")}</dt><dd><code>{fingerprint.isolation_configuration_id ?? "—"}</code></dd></div>
            <div><dt>{t("gameTape.environment.kernelBoundary")}</dt><dd>{fingerprint.isolation_kernel_boundary ?? "—"}</dd></div>
            <div><dt>{t("gameTape.environment.remote")}</dt><dd>{fingerprint.isolation_remote_execution === null || fingerprint.isolation_remote_execution === undefined ? "—" : fingerprint.isolation_remote_execution ? t("gameTape.environment.yes") : t("gameTape.environment.no")}</dd></div>
            <div><dt>{t("gameTape.environment.experimental")}</dt><dd>{fingerprint.isolation_experimental === null || fingerprint.isolation_experimental === undefined ? "—" : fingerprint.isolation_experimental ? t("gameTape.environment.yes") : t("gameTape.environment.no")}</dd></div>
            <div><dt>{t("gameTape.environment.hostEnv")}</dt><dd>{(fingerprint.host_environment_names ?? []).length}</dd></div>
            <div><dt>{t("gameTape.environment.inputs")}</dt><dd>{t("gameTape.environment.inputCounts", { managed: fingerprint.managed_input_count ?? 0, content: fingerprint.content_input_count ?? 0 })}</dd></div>
          </dl>
          {repository.bottleneck_step_id ? <p className="game-tape-bottleneck"><strong>{t("gameTape.timeline.bottleneck")}</strong> {t("gameTape.timeline.bottleneckValue", { step: repository.bottleneck_step_id, percent: repository.bottleneck_percent ?? 0 })}</p> : null}
          {(fingerprint.runtime_facts ?? []).length > 0 ? <div className="game-tape-runtime-facts"><strong>{t("gameTape.environment.runtimeFacts")}</strong>{(fingerprint.runtime_facts ?? []).map((fact) => <span key={`${fact.name}-${fact.version}-${fact.source}`}><b>{fact.name}</b><code>{fact.version}</code><small>{t(`gameTape.environment.runtimeSource.${fact.source}`)}</small></span>)}</div> : null}
          {(fingerprint.tools ?? []).length > 0 ? <div className="game-tape-tools"><strong>{t("gameTape.environment.tools")}</strong>{(fingerprint.tools ?? []).map((tool) => <span key={`${tool.name}-${tool.sha256}`}><b>{tool.name}</b><code>{shortSha(tool.sha256)}</code><small>{tool.byte_count} B</small></span>)}</div> : <p className="muted">{t("gameTape.environment.toolVersionsUnavailable")}</p>}
        </article>;
      })}
    </div>
    {tape.repositories.length > SUMMARY_REPOSITORY_PAGE_SIZE ? <SummaryRepositoryPager page={boundedPage} pageCount={pageCount} from={pageStart + 1} to={Math.min(pageStart + repositories.length, tape.repositories.length)} total={tape.repositories.length} onPage={setPage} /> : null}
  </section>;
}
