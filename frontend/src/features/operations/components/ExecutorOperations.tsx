import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ExecutorAdmissionMode, ExecutorTrustState } from "@generated/enums";
import type { CancellationRequestedPlacementQueueEntry, FleetExecutorPoolSnapshot, FleetExecutorStatus, PlacementQueueSnapshot, WaitingPlacementQueueEntry } from "@generated/models";
import { StatusBadge } from "@/components/common/StatusBadge";

type PlacementQueueEntry = WaitingPlacementQueueEntry | CancellationRequestedPlacementQueueEntry;

interface Props {
  executors: FleetExecutorPoolSnapshot | null;
  placementQueue: PlacementQueueSnapshot | null;
  actionsDisabled: boolean;
  onDrain: (executor: FleetExecutorStatus) => void;
  onActivate: (executor: FleetExecutorStatus) => void;
  onRevokeTrust: (executor: FleetExecutorStatus) => void;
  onRestoreTrust: (executor: FleetExecutorStatus) => void;
  onCancelQueued: (entry: PlacementQueueEntry) => void;
  onRefresh: () => void;
}

export function ExecutorOperations({
  executors,
  placementQueue,
  actionsDisabled,
  onDrain,
  onActivate,
  onRevokeTrust,
  onRestoreTrust,
  onCancelQueued,
  onRefresh,
}: Props) {
  const { t } = useTranslation();
  const rows = executors?.executors ?? [];
  const queue = placementQueue?.entries ?? [];
  const [confirmingRevokeIdentity, setConfirmingRevokeIdentity] = useState<string | null>(null);
  const executorIdentity = (executor: FleetExecutorStatus) => `${executor.executor_id}:${executor.generation}:${executor.operational_revision}`;

  return <section className="panel executor-operations-panel">
    <div className="section-heading"><div>
      <span className="eyebrow">{t("operationsTool.eyebrow")}</span>
      <h2>{t("operationsTool.title")}</h2>
      <p>{t("operationsTool.description")}</p>
    </div><button className="button button--secondary" type="button" onClick={onRefresh}>{t("common.refresh")}</button></div>
    <div className="notice notice--scope"><strong>{t("operationsTool.authorityTitle")}</strong><p>{t("operationsTool.authorityMessage")}</p></div>
    {rows.length === 0 ? <p className="muted-text">{t("operationsTool.empty")}</p> : <div className="table-shell"><table>
      <thead><tr><th>{t("operationsTool.columns.executor")}</th><th>{t("operationsTool.columns.admission")}</th><th>{t("operationsTool.columns.trust")}</th><th>{t("operationsTool.columns.runs")}</th><th>{t("operationsTool.columns.actions")}</th></tr></thead>
      <tbody>{rows.map((executor) => <tr key={executor.executor_id}>
        <td><strong>{executor.executor_id}</strong><small>{executor.backend_id} · gen {executor.generation}</small></td>
        <td><StatusBadge label={executor.admission_mode} tone={executor.admission_mode === ExecutorAdmissionMode.ACTIVE ? "success" : "warning"} /></td>
        <td><StatusBadge label={executor.trust_state} tone={executor.trust_state === ExecutorTrustState.TRUSTED ? "success" : "danger"} /></td>
        <td>{executor.active_fleet_run_ids?.length ?? 0}</td>
        <td><div className="action-row">
          {executor.admission_mode === ExecutorAdmissionMode.ACTIVE
            ? <button className="button button--secondary button--compact" type="button" disabled={actionsDisabled} onClick={() => onDrain(executor)}>{t("operationsTool.drain")}</button>
            : <button className="button button--secondary button--compact" type="button" disabled={actionsDisabled} onClick={() => onActivate(executor)}>{t("operationsTool.activate")}</button>}
          {executor.trust_state === ExecutorTrustState.TRUSTED
            ? confirmingRevokeIdentity === executorIdentity(executor)
              ? <><button className="button button--danger button--compact" type="button" disabled={actionsDisabled} onClick={() => { setConfirmingRevokeIdentity(null); onRevokeTrust(executor); }}>{t("operationsTool.confirmRevoke")}</button><button className="text-button" type="button" onClick={() => setConfirmingRevokeIdentity(null)}>{t("common.cancel")}</button></>
              : <button className="button button--danger button--compact" type="button" disabled={actionsDisabled} onClick={() => setConfirmingRevokeIdentity(executorIdentity(executor))}>{t("operationsTool.revoke")}</button>
            : <button className="button button--secondary button--compact" type="button" disabled={actionsDisabled} onClick={() => onRestoreTrust(executor)}>{t("operationsTool.restore")}</button>}
        </div></td>
      </tr>)}</tbody>
    </table></div>}
    <div className="section-heading section-heading--sub"><div><h3>{t("operationsTool.queueTitle")}</h3><p>{t("operationsTool.queueDescription")}</p></div></div>
    {queue.length === 0 ? <p className="muted-text">{t("operationsTool.queueEmpty")}</p> : <div className="compact-card-list">{queue.map((entry) => <article className="compact-card" key={entry.fleet_run_id}>
      <div><strong>{entry.fleet_run_id}</strong><small>{t("operationsTool.queuePosition", { position: entry.position })}</small></div>
      <StatusBadge label={entry.state} tone={entry.state === "waiting" ? "warning" : "neutral"} />
      <button className="text-button" type="button" disabled={actionsDisabled || entry.state !== "waiting"} onClick={() => onCancelQueued(entry)}>{t("operationsTool.cancelQueued")}</button>
    </article>)}</div>}
  </section>;
}
