import { useTranslation } from "react-i18next";

import { StatusBadge } from "@/components/common/StatusBadge";

import { GameTapeFailureNavigationState } from "@generated/enums";
import type {
  GameTape,
  GameTapeFailureHistory,
  GameTapeLiveFailureAnchor,
  GameTapeLiveOutputBatch,
  GameTapeStepExecution,
} from "@generated/models";
import type { GameTapeArtifact } from "@/features/game-tape/gameTapeArtifacts";
import { GameTapeStepArtifacts } from "@/features/game-tape/components/GameTapeStepArtifacts";
import { gameTapeArtifactKey } from "@/features/game-tape/gameTapeArtifacts";
import { FailureContextEvidence, ReproductionContextSummary } from "@/features/game-tape/components/GameTapeFailureContext";
import { GameTapeFailureSequenceView } from "@/features/game-tape/components/GameTapeFailureSequence";
import { GameTapeLiveLogs, type GameTapeLogStream } from "@/features/game-tape/components/GameTapeLiveLogs";
import { gameTapeStepContexts, findGameTapeStepContext } from "@/features/game-tape/gameTapeSteps";
import {
  duration,
  outputEvidence,
  statusSymbol,
  statusTone,
} from "@/features/game-tape/gameTapeFormat";

export function GameTapeEvidence({ tape, selectedId, onSelect, failureHistory, failureHistoryLoading, liveOutput, artifacts, logQuery = "", logStream = "all", logPageFromEnd = 0, rawLogsOpen = false, onLogQueryChange, onLogStreamChange, onLogPageChange, onRawLogsOpenChange }: { tape: GameTape; selectedId: string | null; onSelect(id: string): void; failureHistory: GameTapeFailureHistory | null; failureHistoryLoading: boolean; liveOutput: GameTapeLiveOutputBatch | null; artifacts: ReadonlyMap<string, GameTapeArtifact[]>; logQuery?: string; logStream?: GameTapeLogStream; logPageFromEnd?: number; rawLogsOpen?: boolean; onLogQueryChange?: (query: string) => void; onLogStreamChange?: (stream: GameTapeLogStream) => void; onLogPageChange?: (pageFromEnd: number) => void; onRawLogsOpenChange?: (open: boolean) => void }) {
  const { t } = useTranslation();
  const contexts = gameTapeStepContexts(tape);
  const selectedContext = findGameTapeStepContext(contexts, selectedId);
  const selected = selectedContext?.step ?? null;
  const liveAnchor = selected
    ? (liveOutput?.failure_anchors ?? []).find(
      (anchor) => anchor.step_execution_id === selected.step_execution_id,
    ) ?? null
    : null;
  return <div className="game-tape-logs">
    <div className="game-tape-log-policy"><strong>{t("gameTape.logs.privacyTitle")}</strong><span>{t("gameTape.logs.privacy")}</span></div>
    <div className="game-tape-log-grid">
      <div className="game-tape-log-index">
        {contexts.map(({ repository, step }) => <button
          type="button"
          className={selectedId === step.step_execution_id ? "game-tape-log-step game-tape-log-step--selected" : "game-tape-log-step"}
          aria-current={selectedId === step.step_execution_id ? "true" : undefined}
          key={step.step_execution_id}
          onClick={() => onSelect(step.step_execution_id)}
        ><span className="game-tape-log-step__head"><b className="game-tape-status-mark" data-status={step.status} aria-hidden="true">{statusSymbol(step.status)}</b><span><span>{repository.repository.name}</span><strong>{step.step_id}</strong></span></span><small>{t(`gameTape.status.${step.status}`)}</small></button>)}
      </div>
      <div className="game-tape-log-evidence">
        {selected && selectedContext ? <>
          <StepEvidenceSummary repositoryName={selectedContext.repository.repository.name} step={selected} />
          <OutputEvidence step={selected} liveAnchor={liveAnchor} liveOutput={liveOutput} />
          <details className="advanced-disclosure game-tape-raw-logs-disclosure" aria-label={t("gameTape.logs.rawDisclosure")} open={rawLogsOpen} onToggle={(event) => { const next = event.currentTarget.open; if (next === rawLogsOpen) return; onRawLogsOpenChange?.(next); }}>
            <summary>{t("gameTape.logs.rawDisclosure")}</summary>
            <p className="muted">{t("gameTape.logs.rawDisclosureHelp")}</p>
            <GameTapeLiveLogs batch={liveOutput} repositoryId={selectedContext.repository.repository.repository_id} stepId={selected.step_id} focusSequence={liveAnchor?.sequence ?? null} query={logQuery} stream={logStream} pageFromEnd={logPageFromEnd} onQueryChange={onLogQueryChange} onStreamChange={onLogStreamChange} onPageChange={onLogPageChange} />
          </details>
          {selected.failure ? <FailureHistoryPanel history={failureHistory} loading={failureHistoryLoading} /> : null}
          <GameTapeStepArtifacts artifacts={artifacts.get(gameTapeArtifactKey(selectedContext.repository.repository.repository_id, selected.step_id)) ?? []} ownerIdentity={selected.step_execution_id} />
        </> : <div className="game-tape-no-selection"><strong>{t("gameTape.logs.selectStep")}</strong><span>{t("gameTape.logs.selectStepHelp")}</span></div>}
      </div>
    </div>
  </div>;
}


function StepEvidenceSummary({ repositoryName, step }: { repositoryName: string; step: GameTapeStepExecution }) {
  const { t } = useTranslation();
  return <section className="compact-card game-tape-evidence-hero" data-status={step.status} aria-label={t("gameTape.logs.stepSummary")}>
    <div><span className="eyebrow">{t("gameTape.logs.stepSummary")}</span><strong>{repositoryName} → {step.step_id}</strong><small>{t("gameTape.logs.structuredFirst")}</small></div>
    <span className="button-row"><b className="game-tape-status-mark" data-status={step.status} aria-hidden="true">{statusSymbol(step.status)}</b><StatusBadge label={t(`gameTape.status.${step.status}`)} tone={statusTone(step.status)} /><small>{duration(step.duration_ms)}</small></span>
    {step.failure ? <p><strong>{t("gameTape.failure.title")}</strong> · {t("gameTape.failure.signature", { code: step.failure.exit_code, reason: step.failure.reason_code ?? "—" })}</p> : null}
  </section>;
}


function OutputEvidence({ step, liveAnchor, liveOutput }: { step: GameTapeStepExecution; liveAnchor: GameTapeLiveFailureAnchor | null; liveOutput: GameTapeLiveOutputBatch | null }) {
  const { t } = useTranslation();
  return <div className="game-tape-output-evidence">
    {step.failure ? <section className="game-tape-failure game-tape-failure--primary">
      <div className="game-tape-failure__heading"><span className="eyebrow">{t("gameTape.failure.title")}</span><strong>{t("gameTape.failure.signature", { code: step.failure.exit_code, reason: step.failure.reason_code ?? "—" })}</strong></div>
      <dl>
        <div><dt>{t("gameTape.inspector.exitCode")}</dt><dd>{step.failure.exit_code}</dd></div>
        <div><dt>{t("gameTape.attempts.failureAttempt")}</dt><dd>{step.failure.attempt ?? "—"}</dd></div>
        <div><dt>{t("gameTape.failure.evidenceStream")}</dt><dd>{step.failure.evidence_stream ?? "—"}</dd></div>
        <div><dt>{t("gameTape.failure.signatureId")}</dt><dd><code>{step.failure.signature_id}</code></dd></div>
      </dl>
      <p className="game-tape-failure__anchor">{liveAnchor?.retained ? t("gameTape.failure.liveAnchorReady", { stream: liveAnchor.stream, line: liveAnchor.line })
        : liveAnchor ? t("gameTape.failure.liveAnchorDropped", { stream: liveAnchor.stream, line: liveAnchor.line })
          : liveOutput?.failure_navigation_state === GameTapeFailureNavigationState.DEGRADED ? t("gameTape.failure.navigationDegraded")
            : t("gameTape.failure.rawUnavailable")}</p>
    </section> : null}
    <details className="advanced-disclosure game-tape-output-fingerprints">
      <summary>{t("gameTape.logs.durableOutputEvidence")}</summary>
      <p className="muted">{t("gameTape.logs.durableOutputEvidenceHelp")}</p>
      <div className="game-tape-output-fingerprints__grid">{(["stdout", "stderr"] as const).map((stream) => {
        const evidence = outputEvidence(step, stream);
        return <section key={stream}><strong>{stream}</strong>{evidence ? <dl>
          <div><dt>{t("gameTape.logs.lines")}</dt><dd>{evidence.line_count}</dd></div>
          <div><dt>{t("gameTape.logs.bytes")}</dt><dd>{evidence.byte_count}</dd></div>
          <div className="game-tape-output-fingerprints__digest"><dt>SHA-256</dt><dd><code>{evidence.sha256}</code></dd></div>
        </dl> : <p>—</p>}</section>;
      })}</div>
      {step.failure ? <dl className="game-tape-failure__durable">
        <div><dt>{t("gameTape.failure.reason")}</dt><dd>{step.failure.reason_code ?? "—"}</dd></div>
        <div><dt>{t("gameTape.logs.lines")}</dt><dd>{step.failure.evidence_line_count ?? "—"}</dd></div>
        <div><dt>{t("gameTape.logs.bytes")}</dt><dd>{step.failure.evidence_byte_count ?? "—"}</dd></div>
        <div><dt>SHA-256</dt><dd><code>{step.failure.evidence_sha256 ?? "—"}</code></dd></div>
      </dl> : null}
    </details>
  </div>;
}

function FailureHistoryPanel({ history, loading }: { history: GameTapeFailureHistory | null; loading: boolean }) {
  const { t } = useTranslation();
  if (loading) return <section className="game-tape-failure-history"><strong>{t("gameTape.failureHistory.title")}</strong><p>{t("gameTape.failureHistory.loading")}</p></section>;
  if (!history) return null;
  return <section className="game-tape-failure-history">
    <header><div><span className="eyebrow">{t("gameTape.failureHistory.eyebrow")}</span><strong>{t("gameTape.failureHistory.title")}</strong></div><code>{history.signature_id}</code></header>
    <p>{t("gameTape.failureHistory.rule", { failures: history.same_plan_step_failures, considered: history.history_candidates_considered })}</p>
    <FailureEvolution evolution={history.evolution} />
    <GameTapeFailureSequenceView sequence={history.sequence} />
    <FailureContextEvidence evolution={history.evolution} />
    {(history.occurrences ?? []).length > 0 ? <div className="game-tape-failure-history__list">{(history.occurrences ?? []).map((occurrence) => <article key={occurrence.fleet_run_id}>
      <header><strong>{occurrence.fleet_run_id}</strong><span>{new Date(occurrence.accepted_at).toLocaleString()}</span></header>
      <dl>
        <div><dt>{t("gameTape.failureHistory.attempt")}</dt><dd>{occurrence.failure_attempt ?? "—"} / {occurrence.attempt_count}</dd></div>
        <div><dt>{t("gameTape.inspector.duration")}</dt><dd>{duration(occurrence.duration_ms)}</dd></div>
        <div><dt>{t("gameTape.failureHistory.context")}</dt><dd><ReproductionContextSummary context={occurrence.context} /></dd></div>
      </dl>
    </article>)}</div> : <p>{t("gameTape.failureHistory.none")}</p>}
    <small>{t("gameTape.failureHistory.contract")}</small>
  </section>;
}

function FailureEvolution({ evolution }: { evolution: GameTapeFailureHistory["evolution"] }) {
  const { t } = useTranslation();
  const pass = evolution.first_later_pass;
  return <div className="game-tape-failure-evolution">
    <strong>{t("gameTape.failureHistory.evolutionTitle")}</strong>
    <dl>
      <div><dt>{t("gameTape.failureHistory.exactOccurrences")}</dt><dd>{evolution.exact_occurrence_count}</dd></div>
      <div><dt>{t("gameTape.failureHistory.firstSeen")}</dt><dd><code>{evolution.first_seen_run_id}</code><small>{new Date(evolution.first_seen_at).toLocaleString()}</small></dd></div>
      <div><dt>{t("gameTape.failureHistory.lastSeen")}</dt><dd><code>{evolution.last_seen_run_id}</code><small>{new Date(evolution.last_seen_at).toLocaleString()}</small></dd></div>
      <div><dt>{t("gameTape.failureHistory.laterComparable")}</dt><dd>{evolution.later_comparable_runs}</dd></div>
      <div><dt>{t("gameTape.failureHistory.recurredAfterPass")}</dt><dd>{evolution.exact_failure_recurred_after_pass ? t("common.yes") : t("common.no")}</dd></div>
    </dl>
    {pass ? <div className="game-tape-later-pass"><strong>{t("gameTape.failureHistory.laterPass")}</strong><span><code>{pass.fleet_run_id}</code> · {new Date(pass.accepted_at).toLocaleString()} · {duration(pass.duration_ms)}</span><small><ReproductionContextSummary context={pass.context} /></small></div> : <p>{t("gameTape.failureHistory.noLaterPass")}</p>}
    <small>{t("gameTape.failureHistory.recoveryRule")}</small>
  </div>;
}
