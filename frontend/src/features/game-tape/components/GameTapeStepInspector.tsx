import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";

import { StatusBadge } from "@/components/common/StatusBadge";
import type { GameTapeFailureHistory, GameTapeStepExecution } from "@generated/models";
import type { GameTapeArtifact } from "@/features/game-tape/gameTapeArtifacts";
import { GameTapeStepArtifacts } from "@/features/game-tape/components/GameTapeStepArtifacts";
import { GameTapeNativeAgentObservations } from "@/features/game-tape/components/GameTapeNativeAgentObservations";
import {
  command,
  duration,
  failureCopyText,
  formatTime,
  statusSymbol,
  statusTone,
} from "@/features/game-tape/gameTapeFormat";

export function GameTapeStepInspector({ step, artifacts = [], failureHistory = null, failureHistoryLoading = false, technicalOpen = false, onClose, onOpenEvidence, onPreviousFailure, onNextFailure, onComparePreviousOccurrence, onTechnicalOpenChange }: { step: GameTapeStepExecution; artifacts?: readonly GameTapeArtifact[]; failureHistory?: GameTapeFailureHistory | null; failureHistoryLoading?: boolean; technicalOpen?: boolean; onClose?(): void; onOpenEvidence?(): void; onPreviousFailure?(): void; onNextFailure?(): void; onComparePreviousOccurrence?(): void; onTechnicalOpenChange?: (open: boolean) => void }) {
  const { t } = useTranslation();
  const renderIdentity = step.step_execution_id;
  const currentIdentity = useRef(renderIdentity);
  currentIdentity.current = renderIdentity;
  const [copiedFor, setCopiedFor] = useState<string | null>(null);
  const [copiedErrorFor, setCopiedErrorFor] = useState<string | null>(null);
  const copied = copiedFor === renderIdentity;
  const copiedError = copiedErrorFor === renderIdentity;
  const inspectorRef = useRef<HTMLElement | null>(null);
  useEffect(() => { inspectorRef.current?.focus(); }, []);
  const exactCommand = command(step);
  const source = step.why.source_location;
  const errorText = failureCopyText(step);
  const authority = step.execution_authority;
  const authorityEconomics = step.attempts?.at(-1)?.execution_authority_economics ?? [];
  const onInspectorKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    const target = event.target as HTMLElement;
    if (target.closest("input, textarea, select, [contenteditable=true]")) return;
    if (event.key === "Escape" && onClose) { event.preventDefault(); event.stopPropagation(); onClose(); return; }
    if (!event.altKey) return;
    if (event.key === "ArrowUp" && onPreviousFailure) { event.preventDefault(); onPreviousFailure(); }
    if (event.key === "ArrowDown" && onNextFailure) { event.preventDefault(); onNextFailure(); }
  };
  return <aside ref={inspectorRef} className="game-tape-inspector" data-status={step.status} aria-label={t("gameTape.inspector.title")} tabIndex={-1} onKeyDown={onInspectorKeyDown}>
    <div className="game-tape-inspector__heading"><div><span className="eyebrow">{t("gameTape.inspector.title")}</span><h3>{step.step_id}</h3></div><span className="button-row"><b className="game-tape-status-mark" data-status={step.status} aria-hidden="true">{statusSymbol(step.status)}</b><StatusBadge label={t(`gameTape.status.${step.status}`)} tone={statusTone(step.status)} />{onClose ? <button type="button" className="button button--quiet" aria-label={t("gameTape.inspector.close")} onClick={onClose}>×</button> : null}</span></div>
    {(onPreviousFailure || onNextFailure || errorText || onOpenEvidence && (step.failure || step.reason)) ? <nav className="button-row" aria-label={t("gameTape.inspector.failureActions")}>
      <button className="button button--secondary button--compact" type="button" disabled={!onPreviousFailure} aria-keyshortcuts="Alt+ArrowUp" onClick={onPreviousFailure}>{t("gameTape.inspector.previousFailure")}</button>
      <button className="button button--secondary button--compact" type="button" disabled={!onNextFailure} aria-keyshortcuts="Alt+ArrowDown" onClick={onNextFailure}>{t("gameTape.inspector.nextFailure")}</button>
      {errorText ? <button className="button button--secondary button--compact" type="button" onClick={() => { const owner = renderIdentity; void navigator.clipboard.writeText(errorText).then(() => { if (currentIdentity.current === owner) setCopiedErrorFor(owner); }, () => { if (currentIdentity.current === owner) setCopiedErrorFor(null); }); }}>{copiedError ? t("gameTape.inspector.errorCopied") : t("gameTape.inspector.copyError")}</button> : null}
      {onOpenEvidence && (step.failure || step.reason) ? <button className="button button--secondary button--compact" type="button" onClick={onOpenEvidence}>{t("gameTape.inspector.openLogs")}</button> : null}
    </nav> : null}
    {(onPreviousFailure || onNextFailure) ? <small className="muted">{t("gameTape.inspector.failureKeyboardHint")}</small> : null}
    <section className="game-tape-why">
      <strong>{t("gameTape.why.title")}</strong>
      <p>{step.why.explanation}</p>
      <ul>
        <li><span>{t("gameTape.why.declared")}</span><code>{source ? `${source.source}:${source.line}:${source.column}` : step.why.declaration}</code></li>
        {step.why.previous_step_id ? <li><span>{t("gameTape.why.previous")}</span><code>{step.why.previous_step_id} · {step.why.previous_step_status}</code></li> : null}
        {step.why.condition ? <li><span>{t("gameTape.why.condition")}</span><code>{step.why.condition}</code></li> : null}
      </ul>
    </section>
    {step.reason ? <p className="game-tape-inspector__reason"><strong>{t("gameTape.inspector.engineReason")}</strong> {step.reason}</p> : null}
    {step.failure ? <section className="game-tape-failure">
      <strong>{t("gameTape.failure.title")}</strong>
      <p>{t("gameTape.failure.signature", { code: step.failure.exit_code, reason: step.failure.reason_code ?? "—" })}</p>
      <p><code>{step.failure.signature_id}</code></p>
      {step.failure.evidence_stream ? <p>{t("gameTape.failure.evidenceAnchor", { stream: step.failure.evidence_stream, lines: step.failure.evidence_line_count ?? 0 })}</p> : null}
      {onOpenEvidence && step.failure.evidence_stream ? <button className="game-tape-evidence-link" type="button" onClick={onOpenEvidence}>{t("gameTape.failure.openEvidence")}</button> : null}
      <small>{t("gameTape.failure.rawPolicy")}</small>
    </section> : null}
    {step.failure && (failureHistoryLoading || failureHistory) ? <section className="compact-card game-tape-inspector__recurrence">
      <strong>{t("gameTape.failureHistory.title")}</strong>
      {failureHistoryLoading ? <p>{t("gameTape.failureHistory.loading")}</p> : failureHistory ? <>
        <dl className="game-tape-inspector__facts">
          <div><dt>{t("gameTape.failureHistory.exactOccurrences")}</dt><dd>{failureHistory.evolution.exact_occurrence_count}</dd></div>
          <div><dt>{t("gameTape.failureHistory.firstSeen")}</dt><dd><code>{failureHistory.evolution.first_seen_run_id}</code><small>{new Date(failureHistory.evolution.first_seen_at).toLocaleString()}</small></dd></div>
          <div><dt>{t("gameTape.failureHistory.lastSeen")}</dt><dd><code>{failureHistory.evolution.last_seen_run_id}</code><small>{new Date(failureHistory.evolution.last_seen_at).toLocaleString()}</small></dd></div>
          <div><dt>{t("gameTape.failureHistory.recurredAfterPass")}</dt><dd>{failureHistory.evolution.exact_failure_recurred_after_pass ? t("common.yes") : t("common.no")}</dd></div>
        </dl>
        {onComparePreviousOccurrence ? <button className="button button--secondary button--compact" type="button" onClick={onComparePreviousOccurrence}>{t("gameTape.failureHistory.comparePreviousExact")}</button> : null}
        <small>{t("gameTape.failureHistory.contract")}</small>
      </> : null}
    </section> : null}
    <GameTapeStepArtifacts artifacts={artifacts} ownerIdentity={step.step_execution_id} />
    <section className="game-tape-provenance" aria-label={t("gameTape.settlement.title")}>
      <strong>{t("gameTape.settlement.title")}</strong>
      {step.settlement ? <dl className="game-tape-inspector__facts">
        <div><dt>{t("gameTape.settlement.outcome")}</dt><dd>{step.settlement.outcome}</dd></div>
        <div><dt>{t("gameTape.settlement.cleanup")}</dt><dd>{step.settlement.cleanup}</dd></div>
        <div><dt>{t("gameTape.settlement.reason")}</dt><dd>{step.settlement.reason}</dd></div>
        <div><dt>{t("gameTape.settlement.owner")}</dt><dd><code>{step.settlement.owner}</code></dd></div>
        <div><dt>{t("gameTape.settlement.observedExit")}</dt><dd>{step.settlement.observed_returncode ?? "—"}</dd></div>
      </dl> : <p>{t("gameTape.settlement.unavailable")}</p>}
    </section>
    <GameTapeNativeAgentObservations observations={step.native_agent_observations ?? []} />
    <details className="advanced-disclosure game-tape-inspector__technical" open={technicalOpen} onToggle={(event) => { const next = event.currentTarget.open; if (next === technicalOpen) return; onTechnicalOpenChange?.(next); }}>
      <summary>{t("gameTape.inspector.technicalDetails")}</summary>
      <p className="muted">{t("gameTape.inspector.technicalDetailsHelp")}</p>
          <dl className="game-tape-inspector__facts">
            <div><dt>{t("gameTape.inspector.identity")}</dt><dd><code>{step.step_execution_id}</code></dd></div>
            <div><dt>{t("gameTape.inspector.duration")}</dt><dd>{duration(step.duration_ms)}</dd></div>
            <div><dt>{t("gameTape.inspector.started")}</dt><dd>{formatTime(step.started_at)}</dd></div>
            <div><dt>{t("gameTape.inspector.finished")}</dt><dd>{formatTime(step.finished_at)}</dd></div>
            <div><dt>{t("gameTape.inspector.exitCode")}</dt><dd>{step.exit_code ?? "—"}</dd></div>
            <div><dt>{t("gameTape.inspector.attempts")}</dt><dd>{step.attempt_count ?? 0}</dd></div>
            <div className="game-tape-inspector__wide"><dt>{t("gameTape.inspector.command")}</dt><dd><code>{exactCommand}</code><button type="button" className="game-tape-copy-command" onClick={() => { const owner = renderIdentity; void navigator.clipboard.writeText(exactCommand).then(() => { if (currentIdentity.current === owner) setCopiedFor(owner); }, () => { if (currentIdentity.current === owner) setCopiedFor(null); }); }}>{copied ? t("gameTape.inspector.copied") : t("gameTape.inspector.copyCommand")}</button></dd></div>
            <div className="game-tape-inspector__wide"><dt>{t("gameTape.inspector.workingDirectory")}</dt><dd><code>{step.working_directory ?? "."}</code></dd></div>
          </dl>
          {(step.environment ?? []).length > 0 ? <section className="game-tape-provenance"><strong>{t("gameTape.inspector.values")}</strong><ul>{(step.environment ?? []).map((binding) => <li key={binding.name}><code>{binding.name}</code><span>← {binding.sources.join(" → ")}</span></li>)}</ul><small>{t("gameTape.inspector.secretRule")}</small></section> : null}
          {(step.output_names ?? []).length > 0 ? <section className="game-tape-provenance"><strong>{t("gameTape.inspector.outputs")}</strong><p>{(step.output_names ?? []).join(", ")}</p></section> : null}
          {authority ? <section className="game-tape-provenance game-tape-execution-authority">
            <strong>{t("gameTape.inspector.executionAuthority")}</strong>
            <dl className="game-tape-inspector__facts">
              <div><dt>{t("gameTape.inspector.materializationReceipt")}</dt><dd><code>{authority.materialization.receipt_identity}</code></dd></div>
              <div><dt>{t("gameTape.inspector.logicalSources")}</dt><dd>{(authority.materialization.logical_sources ?? []).length}</dd></div>
              <div><dt>{t("gameTape.inspector.packageExecutables")}</dt><dd>{(authority.materialization.package_executables ?? []).join(", ") || "—"}</dd></div>
              <div><dt>{t("gameTape.inspector.ownedScripts")}</dt><dd>{(authority.materialization.owned_scripts ?? []).join(", ") || "—"}</dd></div>
              <div><dt>{t("gameTape.inspector.atomicHandoff")}</dt><dd>{authority.atomic_handoff.state} · {authority.atomic_handoff.mechanism}</dd></div>
              <div><dt>{t("gameTape.inspector.terminalProofBytes")}</dt><dd>{authority.atomic_handoff.terminal_proof_bytes}</dd></div>
              {authority.runtime ? <>
                <div><dt>{t("gameTape.inspector.runtimeReceipt")}</dt><dd><code>{authority.runtime.receipt_identity}</code></dd></div>
                <div><dt>{t("gameTape.inspector.runtimeExecutable")}</dt><dd><code>{authority.runtime.executable_path}</code></dd></div>
                <div><dt>{t("gameTape.inspector.runtimeExecutableIdentity")}</dt><dd><code>{authority.runtime.executable_identity}</code></dd></div>
                {authority.runtime.interpreter_path ? <div><dt>{t("gameTape.inspector.runtimeInterpreter")}</dt><dd><code>{authority.runtime.interpreter_path}</code></dd></div> : null}
              </> : null}
            </dl>
            {authorityEconomics.length > 0 ? <ul>{authorityEconomics.map((item) => <li key={item.receipt_identity}><code>{item.scope}</code><span>{t("gameTape.inspector.authorityEconomics", { reused: item.reused_units, recomputed: item.recomputed_units, bytes: item.terminal_proof_bytes })}</span>{(item.recompute_reasons ?? []).length > 0 ? <small>{(item.recompute_reasons ?? []).join(", ")}</small> : null}</li>)}</ul> : null}
            <small>{t("gameTape.inspector.authorityProjectionRule")}</small>
          </section> : null}
    </details>
  </aside>;
}
