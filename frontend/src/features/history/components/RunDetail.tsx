import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type { FleetRunDetail, FleetSummary, OperationReport } from "@generated/models";
import { StatusBadge } from "@/components/common/StatusBadge";
import { fleetRunFailure, fleetRunFinishedAt, fleetRunStartedAt } from "@/domain/fleet/runLifecycle";
import { OperationResultCard } from "@/features/history/components/OperationResultCard";
import { gameTapeRecoveryAction } from "@/features/game-tape/GameTapeFeature";
import { runLineage } from "@/domain/presentation/runProvenance";
import {
  actionKey,
  outcomeKey,
  outcomeTone,
  runStatusKey,
  runStatusTone,
  targetLabel,
} from "@/domain/presentation/operations";

const RUN_TARGET_PAGE_SIZE = 50;

interface RunDetailProps {
  detail: FleetRunDetail;
  summary: FleetSummary | null;
  actionsDisabled: boolean;
  onRunAgain: (runId: string) => void;
  onReadmit: (runId: string) => void;
  onOpenRecovery: () => void;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function operationByRepository(detail: FleetRunDetail): Map<string, OperationReport> {
  return new Map((detail.operations ?? []).map((report) => [report.repository.repository_id, report]));
}

export function RunDetail({ detail, summary, actionsDisabled, onRunAgain, onReadmit, onOpenRecovery }: RunDetailProps) {
  const { t } = useTranslation();
  const { run } = detail;
  const operations = operationByRepository(detail);
  const trustReceipts = new Map(detail.plan_trust_receipts.map((item) => [item.repository_id, item.receipt]));
  const included = summary?.included ?? 0;
  const action = run.capability.action;
  const failure = fleetRunFailure(run);
  const hasEnforcerReceipt = detail.game_tape.enforcer_receipt_available;
  const failureRecovery = gameTapeRecoveryAction(run, detail.rerun_supported);
  const lineage = runLineage(detail.execution);
  const rerunBlocked =
    !detail.rerun_supported ||
    failureRecovery === "readmit-placement" ||
    failureRecovery === "open-recovery" ||
    actionsDisabled ||
    included === 0;
  const [targetPage, setTargetPage] = useState(0);
  const targetPageCount = Math.max(1, Math.ceil(run.targets.length / RUN_TARGET_PAGE_SIZE));
  const safeTargetPage = Math.min(targetPage, targetPageCount - 1);
  useEffect(() => { if (targetPage !== safeTargetPage) setTargetPage(safeTargetPage); }, [safeTargetPage, targetPage]);
  const targetStart = safeTargetPage * RUN_TARGET_PAGE_SIZE;
  const targetItems = run.targets.slice(targetStart, targetStart + RUN_TARGET_PAGE_SIZE);
  useEffect(() => setTargetPage(0), [run.fleet_run_id]);

  const rerunBlockedReason = actionsDisabled
    ? t("history.runAgainBlockedActive")
    : included === 0
      ? t("history.runAgainBlockedNoRepositories")
      : null;

  return (
    <div className="run-detail">
      <div className="run-detail-heading">
        <div>
          <span className="eyebrow">{t("history.runDetail")}</span>
          <h3>{t(actionKey(action))}</h3>
        </div>
        <div className="run-detail-badges">
          <StatusBadge label={t(outcomeKey(detail.outcome))} tone={outcomeTone(detail.outcome)} />
          <StatusBadge label={t(runStatusKey(run.status))} tone={runStatusTone(run.status)} />
        </div>
      </div>

      {failure ? (
        <div className="history-alert">
          <strong>{t("history.runError")}</strong>
          <span>{failure.message}</span>
          <small>{failure.code} · {failure.stage}</small>
        </div>
      ) : null}

      {detail.rerun_supported || failureRecovery === "readmit-placement" || failureRecovery === "open-recovery" ? (
        <div className="run-detail-actions">
          {failureRecovery === "readmit-placement" ? <button
            className="button button--secondary"
            type="button"
            disabled={actionsDisabled}
            onClick={() => onReadmit(run.fleet_run_id)}
          >{t("gameTape.recovery.retryPlacement")}</button> : null}
          {failureRecovery === "open-recovery" ? <button
            className="button button--secondary"
            type="button"
            disabled={actionsDisabled}
            onClick={onOpenRecovery}
          >{t("gameTape.recovery.openRecovery")}</button> : null}
          {failureRecovery !== "readmit-placement" && failureRecovery !== "open-recovery" ? <button
            className="button button--secondary"
            type="button"
            disabled={rerunBlocked}
            onClick={() => onRunAgain(run.fleet_run_id)}
          >
            {t("history.runAgain", { action: t(actionKey(action)) })}
          </button> : null}
          {rerunBlocked && rerunBlockedReason ? <small role="status">{rerunBlockedReason}</small> : null}
          {hasEnforcerReceipt ? (
            <a
              className="button button--secondary"
              href={`/api/fleet/runs/${encodeURIComponent(run.fleet_run_id)}/enforcer.yml`}
              download={`${run.fleet_run_id}-enforcer.yml`}
              title={t("history.enforcerReceiptHint")}
            >
              {t("history.enforcerReceipt")}
            </a>
          ) : null}
        </div>
      ) : null}

      <dl className="run-meta-grid run-meta-grid--secondary">
        <div><dt>{t("history.progressLabel")}</dt><dd>{run.progress.completed} / {run.progress.total}</dd></div>
        {run.target ? <div><dt>{t("history.target")}</dt><dd>{targetLabel(run.target)}</dd></div> : null}
        <div><dt>{t("history.started")}</dt><dd>{formatDate(fleetRunStartedAt(run))}</dd></div>
        <div><dt>{t("history.finished")}</dt><dd>{formatDate(fleetRunFinishedAt(run))}</dd></div>
        <div><dt>{t("history.accepted")}</dt><dd>{formatDate(run.accepted_at)}</dd></div>
      </dl>

      <details className="history-details"><summary>{t("history.technicalDetails")}</summary><code>{run.fleet_run_id}</code>{lineage ? <small>{t(`history.lineage.${lineage.kind}`, { runId: lineage.parentRunId })}</small> : null}</details>

      {run.targets.length > RUN_TARGET_PAGE_SIZE ? <nav className="run-target-pagination" aria-label={t("history.targetPaginationLabel")}>
        <button className="button button--secondary button--small" type="button" disabled={safeTargetPage === 0} onClick={() => setTargetPage((current) => Math.max(0, current - 1))}>{t("common.previous")}</button>
        <span>{t("history.targetPageRange", { from: targetStart + 1, to: Math.min(run.targets.length, targetStart + RUN_TARGET_PAGE_SIZE), total: run.targets.length })}</span>
        <button className="button button--secondary button--small" type="button" disabled={safeTargetPage >= targetPageCount - 1} onClick={() => setTargetPage((current) => Math.min(targetPageCount - 1, current + 1))}>{t("common.next")}</button>
      </nav> : null}
      <div className="run-target-results">
        {targetItems.map((target) => {
          const report = operations.get(target.repository_id);
          return report ? (
            <OperationResultCard key={target.repository_id} report={report} trustReceipt={trustReceipts.get(target.repository_id) ?? null} />
          ) : (
            <article className="operation-result-card operation-result-card--incomplete" key={target.repository_id}>
              <div className="operation-result-heading">
                <div><strong>{target.name}</strong><small>{target.path}</small></div>
                <StatusBadge label={t("history.notCompleted")} tone="neutral" />
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
