import { useTranslation } from "react-i18next";

import { StatusBadge } from "@/components/common/StatusBadge";
import type { GameTapeRunDiff } from "@generated/models";
import { duration, shortSha, signedDuration, statusTone } from "@/features/game-tape/gameTapeFormat";
import { gameTapeElapsedWidthPercent, gameTapeSharedElapsedScale } from "@/features/game-tape/gameTapeTimelineModel";

export interface GameTapeComparisonOption {
  readonly runId: string;
  readonly label: string;
}

export function GameTapeDiff({
  diff,
  options,
  baselineRunId,
  previousComparableRunId,
  loading,
  onBaselineChange,
}: {
  diff: GameTapeRunDiff | null;
  options: readonly GameTapeComparisonOption[];
  baselineRunId: string | null;
  previousComparableRunId: string | null;
  loading: boolean;
  onBaselineChange(runId: string | null): void;
}) {
  const { t } = useTranslation();
  const sharedScale = diff ? gameTapeSharedElapsedScale([diff.base_duration_ms, diff.compared_duration_ms]) : null;
  const stepSharedScale = diff ? gameTapeSharedElapsedScale((diff.repositories ?? []).flatMap((repository) => (repository.steps ?? []).flatMap((step) => [step.base_duration_ms, step.compared_duration_ms]))) : null;
  return <div className="game-tape-diff">
    <div className="game-tape-diff__controls"><label className="game-tape-diff__selector"><span>{t("gameTape.diff.compareWith")}</span><select value={baselineRunId ?? ""} onChange={(event) => onBaselineChange(event.target.value || null)}><option value="">{t("gameTape.diff.choose")}</option>{options.map((option) => <option key={option.runId} value={option.runId}>{option.label}</option>)}</select></label>{previousComparableRunId ? <button type="button" onClick={() => onBaselineChange(previousComparableRunId)}>{t("gameTape.diff.previousComparable")}</button> : null}</div>
    {loading ? <p className="muted">{t("gameTape.diff.loading")}</p> : null}
    {diff ? <>
      <dl className="game-tape-diff__summary">
        <div><dt>{t("gameTape.diff.base")}</dt><dd>{diff.base_run_id}<small>{shortSha(diff.base_plan_id)}</small></dd></div>
        <div><dt>{t("gameTape.diff.current")}</dt><dd>{diff.compared_run_id}<small>{shortSha(diff.compared_plan_id)}</small></dd></div>
        <div><dt>{t("gameTape.diff.total")}</dt><dd>{duration(diff.base_duration_ms)} → {duration(diff.compared_duration_ms)}<small>{signedDuration(diff.duration_delta_ms)}</small></dd></div>
        <div><dt>{t("gameTape.diff.plan")}</dt><dd>{diff.plan_changed ? t("gameTape.diff.changed") : t("gameTape.diff.same")}</dd></div>
      </dl>
      {sharedScale ? <section className="compact-card" aria-label={t("gameTape.diff.sharedScaleTitle")}>
        <div><span className="eyebrow">{t("gameTape.diff.sharedScaleTitle")}</span><strong>{t("gameTape.diff.sharedScaleRange", { duration: duration(sharedScale.spanMs) })}</strong><small>{t("gameTape.diff.sharedScaleTruth")}</small></div>
        <div className="game-tape-timeline-axis" aria-hidden="true"><span>{t("gameTape.diff.run")}</span><ElapsedRuler spanMs={sharedScale.spanMs} ticks={sharedScale.ticks} /><span>{t("gameTape.timeline.durationColumn")}</span></div>
        <ComparisonDurationRow label={t("gameTape.diff.base")} value={diff.base_duration_ms} spanMs={sharedScale.spanMs} />
        <ComparisonDurationRow label={t("gameTape.diff.current")} value={diff.compared_duration_ms} spanMs={sharedScale.spanMs} />
      </section> : null}
      {stepSharedScale ? <section className="compact-card game-tape-diff__step-scale-note" aria-label={t("gameTape.diff.stepScaleTitle")}><div><span className="eyebrow">{t("gameTape.diff.stepScaleTitle")}</span><strong>{t("gameTape.diff.sharedScaleRange", { duration: duration(stepSharedScale.spanMs) })}</strong><small>{t("gameTape.diff.stepScaleTruth")}</small></div></section> : null}
      {(diff.repositories ?? []).map((repository) => <section key={repository.repository_id}>
        <header><strong>{repository.repository_name}</strong><span><code>{shortSha(repository.base_head_sha)}</code> → <code>{shortSha(repository.compared_head_sha)}</code></span></header>
        <div className="game-tape-diff__branch">{repository.base_branch ?? "—"} → {repository.compared_branch ?? "—"}</div>
        <div className="game-tape-diff__environment">{[
          repository.workspace_changed ? t("gameTape.diff.workspaceChanged") : null,
          repository.runtime_changed ? t("gameTape.diff.runtimeChanged") : null,
          repository.toolchain_changed ? t("gameTape.diff.toolchainChanged") : null,
          repository.isolation_changed ? t("gameTape.diff.isolationChanged") : null,
        ].filter(Boolean).join(" · ") || t("gameTape.diff.environmentSame")}</div>
        <div className="game-tape-diff__table">
          <div className="game-tape-diff__head"><span>{t("gameTape.diff.step")}</span><span>{t("gameTape.diff.base")}</span><span>{t("gameTape.diff.current")}</span><span>{t("gameTape.diff.delta")}</span><span>{t("gameTape.diff.facts")}</span></div>
          {(repository.steps ?? []).map((step) => <div className="game-tape-diff__row" key={step.step_id}>
            <strong>{step.step_id}</strong>
            <StepDurationCell status={step.base_status} value={step.base_duration_ms} spanMs={stepSharedScale?.spanMs ?? null} />
            <StepDurationCell status={step.compared_status} value={step.compared_duration_ms} spanMs={stepSharedScale?.spanMs ?? null} />
            <DurationDelta value={step.duration_delta_ms} />
            <span>{[
              step.command_changed ? t("gameTape.diff.commandChanged") : null,
              step.provenance_changed ? t("gameTape.diff.provenanceChanged") : null,
              step.attempt_count_changed ? t("gameTape.diff.attemptsChanged", { base: step.base_attempt_count, current: step.compared_attempt_count }) : null,
              step.tool_changed ? t("gameTape.diff.toolChanged") : null,
              step.egress_changed ? t("gameTape.diff.egressChanged") : null,
              step.resource_policy_changed ? t("gameTape.diff.resourcesChanged") : null,
              step.failure_signature_changed ? t("gameTape.diff.failureChanged") : null,
            ].filter(Boolean).join(" · ") || t("gameTape.diff.noFactChange")}</span>
          </div>)}
        </div>
      </section>)}
    </> : null}
  </div>;
}

function StepDurationCell({ status, value, spanMs }: { status: string | null | undefined; value: number | null | undefined; spanMs: number | null }) {
  const { t } = useTranslation();
  const widthPercent = spanMs ? gameTapeElapsedWidthPercent(value, { spanMs, ticks: [] }) : null;
  return <span>{status ? <StatusBadge label={t(`gameTape.status.${status}`)} tone={statusTone(status)} /> : "—"}<span className="game-tape-timeline-track" aria-hidden="true">{widthPercent !== null ? <i data-status={status ?? undefined} style={{ left: 0, width: `${widthPercent}%` }} /> : null}</span><small>{duration(value)}</small></span>;
}

function DurationDelta({ value }: { value: number | null | undefined }) {
  const { t } = useTranslation();
  const direction = value === null || value === undefined || value === 0 ? "same" : value > 0 ? "slower" : "faster";
  const symbol = direction === "slower" ? "↑" : direction === "faster" ? "↓" : "→";
  const tone: "danger" | "success" | "neutral" = direction === "slower" ? "danger" : direction === "faster" ? "success" : "neutral";
  return <span><StatusBadge label={`${symbol} ${t(`gameTape.diff.${direction}`)}`} tone={tone} /><small>{signedDuration(value)}</small></span>;
}

function ComparisonDurationRow({ label, value, spanMs }: { label: string; value: number | null | undefined; spanMs: number }) {
  const { t } = useTranslation();
  const scale = { spanMs, ticks: [] };
  const widthPercent = gameTapeElapsedWidthPercent(value, scale);
  return <div className="game-tape-timeline-row" style={{ cursor: "default" }}>
    <span><strong>{label}</strong></span>
    <span className="game-tape-timeline-track" aria-hidden="true">{widthPercent !== null ? <i style={{ left: 0, width: `${widthPercent}%` }} /> : <em>{t("gameTape.diff.noDuration")}</em>}</span>
    <small>{duration(value)}</small>
  </div>;
}

function ElapsedRuler({ spanMs, ticks }: { spanMs: number; ticks: readonly number[] }) {
  const { t } = useTranslation();
  return <div className="game-tape-timeline-axis__ruler">{ticks.map((tick, index) => <span className="game-tape-timeline-axis__tick" data-edge={index === 0 ? "start" : index === ticks.length - 1 ? "end" : undefined} key={tick} style={{ left: `${(tick / spanMs) * 100}%` }}><i /><small>{index === 0 ? "0" : index === ticks.length - 1 ? t("gameTape.diff.sharedScaleEnd", { duration: duration(spanMs) }) : `+${duration(tick)}`}</small></span>)}</div>;
}
