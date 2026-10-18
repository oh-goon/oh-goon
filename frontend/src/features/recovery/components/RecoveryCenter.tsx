import { useTranslation } from "react-i18next";

import { StatusBadge } from "@/components/common/StatusBadge";
import type { RecoveryStatus } from "@generated/models";

interface Props {
  status: RecoveryStatus | null;
  actionsDisabled: boolean;
  onReconcile: (operationId: string, recordVersion: number) => void;
  onAcknowledge: (operationId: string, recordVersion: number) => void;
}

export function RecoveryCenter({ status, actionsDisabled, onReconcile, onAcknowledge }: Props) {
  const { t } = useTranslation();
  const attempts = status?.effect_quarantines ?? [];
  const findings = status?.invariants.findings ?? [];
  const repairActions = status?.repair_plan.actions ?? [];
  return <>
    <section className="panel recovery-summary">
      <div className="section-heading"><div><span className="eyebrow">{t("recovery.eyebrow")}</span><h2>{t("recovery.summaryTitle")}</h2><p>{t("recovery.summaryDescription")}</p></div></div>
      <div className="summary-strip">
        <div><strong>{attempts.length}</strong><span>{t("recovery.uncertainCount")}</span></div>
        <div><strong>{findings.length}</strong><span>{t("recovery.findingCount")}</span></div>
        <div><StatusBadge label={status?.invariants.healthy ? t("recovery.healthy") : t("recovery.needsAttention")} tone={status?.invariants.healthy ? "success" : "warning"} /></div>
        <div><StatusBadge label={status?.runtime_safety.effects_allowed ? t("recovery.runtimeSafe") : t("recovery.runtimeBlocked")} tone={status?.runtime_safety.effects_allowed ? "success" : "warning"} /></div>
      </div>
    </section>
    <section className="panel">
      <div className="section-heading"><div><h2>{t("recovery.attemptsTitle")}</h2><p>{t("recovery.attemptsDescription")}</p></div></div>
      {attempts.length === 0 ? <p className="muted-text">{t("recovery.noAttempts")}</p> : <div className="recovery-list">{attempts.map((attempt) => {
        const reconciliation = "reconciliation" in attempt ? attempt.reconciliation : undefined;
        const canAcknowledge = reconciliation?.outcome === "PARTIALLY_APPLIED" || reconciliation?.outcome === "INDETERMINATE";
        return <article className="recipe-card" key={attempt.operation_id}>
          <div className="recipe-card-heading"><div><strong>{attempt.repository.path}</strong><small>{attempt.source} · {attempt.action} · {attempt.operation_id}</small></div><StatusBadge label={reconciliation?.outcome ?? attempt.status} tone="warning" /></div>
          {reconciliation ? <><p>{reconciliation.reason}</p><dl className="detail-grid"><div><dt>{t("recovery.baselineHead")}</dt><dd><code>{reconciliation.baseline?.head_sha ?? "—"}</code></dd></div><div><dt>{t("recovery.currentHead")}</dt><dd><code>{reconciliation.current?.head_sha ?? "—"}</code></dd></div></dl></> : <p>{t("recovery.noReconciliation")}</p>}
          <div className="action-row"><button className="button button--primary" type="button" disabled={actionsDisabled} onClick={() => onReconcile(attempt.operation_id, attempt.record_version)}>{t("recovery.reconcile")}</button><button className="button button--secondary" type="button" disabled={actionsDisabled || !canAcknowledge} onClick={() => onAcknowledge(attempt.operation_id, attempt.record_version)}>{t("recovery.acknowledge")}</button></div>
        </article>;
      })}</div>}
    </section>
    <section className="panel">
      <div className="section-heading"><div><h2>{t("recovery.invariantsTitle")}</h2><p>{t("recovery.invariantsDescription")}</p></div></div>
      {findings.length === 0 ? <p className="muted-text">{t("recovery.noFindings")}</p> : <ul className="recovery-findings">{findings.map((finding) => <li key={`${finding.finding_type}:${finding.code}:${finding.message}`}><strong>{finding.code}</strong><span>{finding.message}</span></li>)}</ul>}
    </section>
    <section className="panel">
      <div className="section-heading"><div><h2>{t("recovery.repairPlanTitle")}</h2><p>{t("recovery.repairPlanDescription")}</p></div></div>
      {repairActions.length === 0 ? <p className="muted-text">{t("recovery.noRepairActions")}</p> : <ul className="recovery-findings">{repairActions.map((action) => <li key={`${action.action}:${action.target}`}><strong>{action.action}</strong><span>{action.explanation}</span><small>{action.requires_confirmation ? t("recovery.confirmationRequired") : t("recovery.noConfirmationRequired")}</small></li>)}</ul>}
    </section>
  </>;
}
