import { useTranslation } from "react-i18next";

import type {
  GameTapeDurationComparison,
  GameTapePerformanceReport,
  ScoutRunEvidence,
} from "@generated/models";
import { duration, signedDuration } from "@/features/game-tape/gameTapeFormat";

function TimingFacts({ timing }: { timing: GameTapeDurationComparison }) {
  const { t } = useTranslation();
  const baseline = timing.baseline;
  return <>
    <span>{duration(timing.current_duration_ms)}</span>
    <span>{duration(baseline.median_duration_ms)}</span>
    <span>{baseline.sample_count > 0
      ? `${duration(baseline.minimum_duration_ms)} – ${duration(baseline.maximum_duration_ms)}`
      : "—"}</span>
    <span data-direction={timing.direction}>
      {signedDuration(timing.delta_ms)}
      {timing.delta_percent === null || timing.delta_percent === undefined
        ? null
        : <small>{t("gameTape.performance.percent", { percent: timing.delta_percent })}</small>}
    </span>
    <span>{t("gameTape.performance.samples", { count: baseline.sample_count })}</span>
  </>;
}

export function GameTapePerformance({
  report,
  loading,
  scout,
  scoutLoading,
  onSelect,
}: {
  report: GameTapePerformanceReport | null;
  loading: boolean;
  scout: ScoutRunEvidence | null;
  scoutLoading: boolean;
  onSelect(stepExecutionId: string): void;
}) {
  const { t } = useTranslation();
  if (loading && report === null) return <p className="muted">{t("gameTape.performance.loading")}</p>;
  if (report === null) return <p className="muted">{t("gameTape.performance.unavailable")}</p>;

  return <div className="game-tape-performance">
    <details className="advanced-disclosure game-tape-performance__contract">
      <summary>{t("gameTape.performance.contractTitle")}</summary>
      <span>{t("gameTape.performance.contract")}</span>
      <small>{t("gameTape.performance.rule")}</small>
    </details>
    <section className="game-tape-performance__run-summary">
      <header className="game-tape-performance__header">
        <div>
          <span className="eyebrow">{t("gameTape.performance.runEyebrow")}</span>
          <h3>{t("gameTape.performance.runTitle")}</h3>
        </div>
        <small>{t("gameTape.performance.candidates", {
          comparable: report.comparable_plan_runs,
          considered: report.history_candidates_considered,
        })}</small>
      </header>
      <div className="game-tape-performance__table">
        <div className="game-tape-performance__head">
          <span>{t("gameTape.performance.item")}</span>
          <span>{t("gameTape.performance.current")}</span>
          <span>{t("gameTape.performance.median")}</span>
          <span>{t("gameTape.performance.range")}</span>
          <span>{t("gameTape.performance.change")}</span>
          <span>{t("gameTape.performance.baseline")}</span>
        </div>
        <div className="game-tape-performance__row game-tape-performance__row--run">
          <strong>{t("gameTape.performance.total")}</strong>
          <TimingFacts timing={report.run_timing} />
        </div>
      </div>
    </section>
    <section className="game-tape-resource-evidence">
      <header className="game-tape-performance__header"><div><span className="eyebrow">{t("gameTape.performance.resourcesEyebrow")}</span><h3>{t("gameTape.performance.resourcesTitle")}</h3></div></header>
      {scoutLoading && scout === null ? <p className="muted">{t("gameTape.performance.resourcesLoading")}</p> : null}
      {!scoutLoading && scout === null ? <p className="muted">{t("gameTape.performance.resourcesUnavailable")}</p> : null}
      {(scout?.repositories ?? []).map((repository) => <div key={repository.repository_id} className="game-tape-resource-repository">
        <strong>{repository.repository_name}</strong>
        <div className="game-tape-performance__table">
          <div className="game-tape-performance__head"><span>{t("gameTape.performance.item")}</span><span>{t("gameTape.performance.cpu")}</span><span>{t("gameTape.performance.memory")}</span><span>{t("gameTape.performance.duration")}</span><span>{t("gameTape.performance.comparison")}</span><span>{t("gameTape.performance.baseline")}</span></div>
          {(repository.steps ?? []).map((step) => <div className="game-tape-performance__row game-tape-performance__row--step" key={`${repository.repository_id}:${step.step_id}`}>
            <strong>{step.goon_name} / {step.step_id}</strong>
            <span>{formatNumber(step.cpu_seconds, "s")}</span>
            <span>{formatBytes(step.peak_memory_bytes)}</span>
            <span>{formatNumber(step.duration_seconds, "s")}</span>
            <span>{formatDifference(step.cpu_difference_percent, t("gameTape.performance.cpu"))} · {formatDifference(step.memory_difference_percent, t("gameTape.performance.memory"))} · {formatDifference(step.duration_difference_percent, t("gameTape.performance.duration"))}</span>
            <span>{t("gameTape.performance.samples", { count: step.comparable_runs })}</span>
          </div>)}
        </div>
      </div>)}
    </section>
    {(report.repositories ?? []).map((repository) => <section key={repository.repository_execution_id}>
      <header className="game-tape-performance__header">
        <div>
          <span className="eyebrow">{t("gameTape.performance.repository")}</span>
          <h3>{repository.repository_name}</h3>
        </div>
        {repository.largest_slowdown_step_id && repository.largest_slowdown_percent !== null
          ? <strong className="game-tape-performance__slowdown">{t("gameTape.performance.slowestChange", {
            step: repository.largest_slowdown_step_id,
            percent: repository.largest_slowdown_percent,
          })}</strong>
          : null}
      </header>
      <div className="game-tape-performance__table">
        <div className="game-tape-performance__head">
          <span>{t("gameTape.performance.item")}</span>
          <span>{t("gameTape.performance.current")}</span>
          <span>{t("gameTape.performance.median")}</span>
          <span>{t("gameTape.performance.range")}</span>
          <span>{t("gameTape.performance.change")}</span>
          <span>{t("gameTape.performance.baseline")}</span>
        </div>
        <div className="game-tape-performance__row game-tape-performance__row--repository">
          <strong>{t("gameTape.performance.repositoryTotal")}</strong>
          <TimingFacts timing={repository.timing} />
        </div>
        {(repository.steps ?? []).map((step) => <button
          className="game-tape-performance__row game-tape-performance__row--step"
          type="button"
          key={step.step_execution_id}
          onClick={() => onSelect(step.step_execution_id)}
        >
          <strong>{step.step_id}</strong>
          <TimingFacts timing={step.timing} />
        </button>)}
      </div>
    </section>)}
  </div>;
}


function formatNumber(value: number | null | undefined, suffix: string): string {
  return value === null || value === undefined ? "—" : `${value.toFixed(2)} ${suffix}`;
}
function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const gib = value / (1024 ** 3);
  return `${gib.toFixed(2)} GiB`;
}
function formatDifference(value: number | null | undefined, label: string): string {
  if (value === null || value === undefined) return `${label} —`;
  return `${label} ${value >= 0 ? "+" : ""}${value.toFixed(0)}%`;
}
