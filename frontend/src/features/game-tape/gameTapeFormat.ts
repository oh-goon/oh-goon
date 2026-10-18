import { GameTapeStatus } from "@generated/enums";
import type { GameTapeStepExecution } from "@generated/models";

export type GameTapeTone = "neutral" | "info" | "success" | "warning" | "danger";

export function statusTone(status: string): GameTapeTone {
  if (status === GameTapeStatus.PASSED) return "success";
  if (status === GameTapeStatus.FAILED || status === GameTapeStatus.INTERRUPTED) return "danger";
  if (status === GameTapeStatus.ATTENTION_REQUIRED || status === GameTapeStatus.CANCELLED) return "warning";
  if (status === GameTapeStatus.RUNNING) return "info";
  return "neutral";
}

export function statusSymbol(status: string): string {
  if (status === GameTapeStatus.PASSED) return "✓";
  if (status === GameTapeStatus.FAILED || status === GameTapeStatus.INTERRUPTED) return "×";
  if (status === GameTapeStatus.ATTENTION_REQUIRED) return "!";
  if (status === GameTapeStatus.RUNNING) return "●";
  if (status === GameTapeStatus.CANCELLED) return "−";
  if (status === GameTapeStatus.NOT_RUN) return "○";
  if (status === GameTapeStatus.SKIPPED) return "↷";
  if (status === GameTapeStatus.ADMITTED) return "◆";
  if (status === GameTapeStatus.PLANNED) return "◇";
  return "•";
}

export function duration(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (Math.abs(value) < 1_000) return `${value} ms`;
  const sign = value < 0 ? "−" : "";
  const absolute = Math.abs(value);
  if (absolute < 60_000) return `${sign}${(absolute / 1_000).toFixed(2)} s`;
  const minutes = Math.floor(absolute / 60_000);
  const seconds = Math.round((absolute % 60_000) / 1_000);
  return `${sign}${minutes}m ${seconds}s`;
}

export function signedDuration(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value === 0) return "0 ms";
  return `${value > 0 ? "+" : ""}${duration(value)}`;
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { timeStyle: "medium" }).format(new Date(value));
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" }).format(new Date(value));
}

export function command(step: GameTapeStepExecution): string {
  const argv = step.command ?? [];
  return argv.length > 0 ? argv.join(" ") : "—";
}

export function shortSha(value: string | null | undefined): string {
  if (!value) return "—";
  return value.slice(0, 12);
}

export function outputEvidence(step: GameTapeStepExecution, stream: "stdout" | "stderr") {
  return stream === "stdout" ? step.stdout_evidence : step.stderr_evidence;
}

export function failureCopyText(step: GameTapeStepExecution): string | null {
  if (!step.failure && !step.reason && (step.exit_code === null || step.exit_code === undefined)) return null;
  return [
    step.reason ? `Reason: ${step.reason}` : null,
    step.failure?.reason_code ? `Reason code: ${step.failure.reason_code}` : null,
    step.failure ? `Exit code: ${step.failure.exit_code}` : step.exit_code !== null && step.exit_code !== undefined ? `Exit code: ${step.exit_code}` : null,
    step.failure?.signature_id ? `Signature: ${step.failure.signature_id}` : null,
  ].filter((value): value is string => value !== null).join("\n");
}
