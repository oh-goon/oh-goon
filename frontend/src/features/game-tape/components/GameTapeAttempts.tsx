import { useTranslation } from "react-i18next";

import type {
  GameTape,
  GameTapeAttemptTransition,
  GameTapeRepositoryExecution,
  GameTapeStepExecution,
  PlanStepAttemptEvidence,
} from "@generated/models";
import { duration, shortSha } from "@/features/game-tape/gameTapeFormat";
import { formatAttemptResourceEnvelope, formatBytes } from "@/features/game-tape/gameTapeAttemptResources";
import { findGameTapeStepContext, gameTapeStepContexts } from "@/features/game-tape/gameTapeSteps";

export function GameTapeAttempts({
  tape,
  selectedId,
  onSelect,
}: {
  tape: GameTape;
  selectedId: string | null;
  onSelect(id: string): void;
}) {
  const { t } = useTranslation();
  const contexts = gameTapeStepContexts(tape);
  const selected = findGameTapeStepContext(contexts, selectedId);
  return <div className="game-tape-attempts">
    <div className="game-tape-attempts__contract">
      <strong>{t("gameTape.attempts.contractTitle")}</strong>
      <span>{t("gameTape.attempts.contract")}</span>
    </div>
    <div className="game-tape-attempts__layout">
      <nav className="game-tape-attempts__steps" aria-label={t("gameTape.attempts.steps") }>
        {contexts.map(({ repository, step }) => <button
          type="button"
          key={step.step_execution_id}
          className={selected?.step.step_execution_id === step.step_execution_id ? "game-tape-attempt-step game-tape-attempt-step--selected" : "game-tape-attempt-step"}
          aria-current={selected?.step.step_execution_id === step.step_execution_id ? "true" : undefined}
          onClick={() => onSelect(step.step_execution_id)}
        >
          <span>{repository.repository.name}</span>
          <strong>{step.step_id}</strong>
          <small>{t("gameTape.attempts.count", { count: step.attempt_count ?? 0 })}</small>
        </button>)}
      </nav>
      <div className="game-tape-attempts__detail">
        {selected ? <AttemptDetail tape={tape} repository={selected.repository} step={selected.step} /> : <div className="game-tape-no-selection"><strong>{t("gameTape.attempts.selectStep")}</strong><span>{t("gameTape.attempts.selectStepHelp")}</span></div>}
      </div>
    </div>
  </div>;
}

function AttemptDetail({ tape, repository, step }: { tape: GameTape; repository: GameTapeRepositoryExecution; step: GameTapeStepExecution }) {
  const { t } = useTranslation();
  const fingerprint = repository.fingerprint;
  return <>
    <section className="game-tape-attempt-list game-tape-attempt-list--primary">
      <header><div><span className="eyebrow">{t("gameTape.attempts.physicalAttempts")}</span><strong>{step.step_id}</strong></div><span>{t("gameTape.attempts.count", { count: step.attempt_count ?? 0 })}</span></header>
      {(step.attempts ?? []).length > 0 ? (step.attempts ?? []).map((attempt, index) => <div className="game-tape-attempt-sequence" key={attempt.attempt}>
        {index > 0 ? <AttemptTransitionCard transition={(step.attempt_transitions ?? []).find((item) => item.to_attempt === attempt.attempt) ?? null} /> : null}
        <AttemptCard attempt={attempt} />
      </div>) : <p className="muted">{t("gameTape.attempts.none")}</p>}
    </section>
    <section className="game-tape-retry-summary">
      <strong>{t("gameTape.attempts.retryContextTitle")}</strong>
      <dl>
        <div><dt>{t("gameTape.attempts.retryContextClassification")}</dt><dd>{t(`gameTape.attempts.retryContext.${step.retry_context.classification}`)}</dd></div>
        <div><dt>{t("gameTape.attempts.retryCount")}</dt><dd>{step.retry_context.retry_count}</dd></div>
        <div><dt>{t("gameTape.attempts.linkedRetryCount")}</dt><dd>{step.retry_context.linked_retry_count}</dd></div>
        <div><dt>{t("gameTape.attempts.physicalContextChanged")}</dt><dd>{step.retry_context.physical_context_changed ? t("common.yes") : t("common.no")}</dd></div>
      </dl>
      <small>{t("gameTape.attempts.retryContextRule")}</small>
    </section>
    <details className="advanced-disclosure game-tape-reproduce-disclosure">
      <summary>{t("gameTape.attempts.reproduceEyebrow")}</summary>
      <section className="game-tape-reproduce">
        <header><div><span className="eyebrow">{t("gameTape.attempts.reproduceEyebrow")}</span><h3>{step.step_id}</h3></div></header>
        <p>{t("gameTape.attempts.reproduceRule")}</p>
        <dl>
          <div><dt>{t("gameTape.inspector.command")}</dt><dd><code>{(step.command ?? []).join(" ") || "—"}</code></dd></div>
          <div><dt>{t("gameTape.inspector.workingDirectory")}</dt><dd><code>{step.working_directory ?? "."}</code></dd></div>
          <div><dt>{t("gameTape.environment.commit")}</dt><dd><code>{fingerprint.repository_head_sha ?? "—"}</code></dd></div>
          <div><dt>{t("gameTape.environment.plan")}</dt><dd><code>{tape.execution_reference?.source.sha256 ?? "—"}</code></dd></div>
          <div><dt>{t("gameTape.environment.baseWorkspace")}</dt><dd><code>{fingerprint.base_workspace_sha256 ?? "—"}</code></dd></div>
          <div><dt>{t("gameTape.environment.runtimeRoot")}</dt><dd><code>{fingerprint.runtime_root_sha256 ?? "—"}</code></dd></div>
          <div><dt>{t("gameTape.environment.toolchain")}</dt><dd><code>{fingerprint.host_toolchain_id ?? "—"}</code></dd></div>
          <div><dt>{t("gameTape.environment.isolation")}</dt><dd>{fingerprint.isolation_backend_id ?? "—"}<small>{shortSha(fingerprint.isolation_configuration_id)}</small></dd></div>
        </dl>
        {(step.environment ?? []).length > 0 ? <div className="game-tape-reproduce__environment">
          <strong>{t("gameTape.attempts.environmentSources")}</strong>
          <ul>{(step.environment ?? []).map((binding) => <li key={binding.name}><code>{binding.name}</code><span>← {(binding.sources ?? []).join(" → ")}</span></li>)}</ul>
        </div> : null}
        <small>{t("gameTape.attempts.valuesUnavailable")}</small>
      </section>
    </details>
  </>;
}

function AttemptTransitionCard({ transition }: { transition: GameTapeAttemptTransition | null }) {
  const { t } = useTranslation();
  if (!transition) return null;
  const changes = [
    transition.returncode_changed ? t("gameTape.attempts.returncodeChanged") : null,
    transition.argv_changed ? t("gameTape.attempts.argvChanged") : null,
    transition.tool_changed ? t("gameTape.attempts.toolChanged") : null,
    transition.egress_changed ? t("gameTape.attempts.egressChanged") : null,
    transition.resource_policy_changed ? t("gameTape.attempts.resourcesChanged") : null,
    transition.cache_input_changed ? t("gameTape.attempts.cacheChanged") : null,
    transition.stdout_changed ? t("gameTape.attempts.stdoutChanged") : null,
    transition.stderr_changed ? t("gameTape.attempts.stderrChanged") : null,
  ].filter(Boolean);
  return <div className="game-tape-attempt-transition">
    <strong>{t("gameTape.attempts.transition", { from: transition.from_attempt, to: transition.to_attempt })}</strong>
    <span>{transition.retry_linked ? t("gameTape.attempts.retryLinked") : t("gameTape.attempts.retryUnlinked")}</span>
    <span>{transition.repository_state_continuity ? t("gameTape.attempts.repositoryContinuous") : t("gameTape.attempts.repositoryChanged")}</span>
    <span>{changes.join(" · ") || t("gameTape.attempts.noPhysicalChange")}</span>
  </div>;
}

function AttemptCard({ attempt }: { attempt: PlanStepAttemptEvidence }) {
  const { t } = useTranslation();
  const retry = attempt.retry;
  return <article className="game-tape-attempt-card">
    <header><strong>{t("gameTape.attempts.attempt", { number: attempt.attempt })}</strong><span>{duration(attempt.duration_ms)}</span></header>
    <dl>
      <div><dt>{t("gameTape.inspector.exitCode")}</dt><dd>{attempt.returncode}</dd></div>
      <div><dt>{t("gameTape.attempts.argv")}</dt><dd><code>{(attempt.argv ?? []).join(" ") || "—"}</code></dd></div>
      <div><dt>{t("gameTape.attempts.repositoryState")}</dt><dd><code>{shortSha(attempt.repository_state_before)}</code> → <code>{shortSha(attempt.repository_state_after)}</code></dd></div>
      <div><dt>{t("gameTape.attempts.tool")}</dt><dd>{attempt.tool ? <><b>{attempt.tool.name}</b> · {attempt.tool.origin}<small><code>{shortSha(attempt.tool.sha256)}</code> · {attempt.tool.byte_count} B</small></> : "—"}</dd></div>
      <div><dt>{t("gameTape.attempts.egress")}</dt><dd>{attempt.egress.mode}<small>{t("gameTape.attempts.rules", { count: attempt.egress.rules?.length ?? 0 })}</small></dd></div>
      <div><dt>{t("gameTape.attempts.resources")}</dt><dd>{attempt.resources ? <>{formatAttemptResourceEnvelope(attempt.resources, t("gameTape.attempts.executorAvailable"))}<small>{resourceUsage(attempt)}</small></> : "—"}</dd></div>
      <div><dt>{t("gameTape.attempts.retry")}</dt><dd>{retry ? <>{retry.disposition}<small>{retryDetail(retry)}</small></> : t("gameTape.attempts.noRetryDecision")}</dd></div>
      <div><dt>stdout</dt><dd>{attempt.stdout_evidence.line_count} {t("gameTape.logs.lines").toLowerCase()}<small><code>{shortSha(attempt.stdout_evidence.sha256)}</code></small></dd></div>
      <div><dt>stderr</dt><dd>{attempt.stderr_evidence.line_count} {t("gameTape.logs.lines").toLowerCase()}<small><code>{shortSha(attempt.stderr_evidence.sha256)}</code></small></dd></div>
    </dl>
  </article>;
}

function retryDetail(retry: NonNullable<PlanStepAttemptEvidence["retry"]>): string {
  if (retry.disposition === "SCHEDULED") return `${retry.policy_reason} · ${retry.delay_seconds}s → #${retry.next_attempt}`;
  if (retry.disposition === "ATTEMPTS_EXHAUSTED") return retry.policy_reason;
  return `exit ${retry.exit_code}`;
}

function resourceUsage(attempt: PlanStepAttemptEvidence): string {
  const resources = attempt.resources;
  if (!resources) return "";
  const facts = [
    resources.cpu_usage_usec === null || resources.cpu_usage_usec === undefined ? null : `${resources.cpu_usage_usec}µs CPU`,
    resources.memory_peak_bytes === null || resources.memory_peak_bytes === undefined ? null : `${formatBytes(resources.memory_peak_bytes)} peak`,
    resources.oom_kill_count === null || resources.oom_kill_count === undefined ? null : `${resources.oom_kill_count} OOM`,
  ].filter(Boolean);
  return facts.join(" · ");
}

