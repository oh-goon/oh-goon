import type { PlanResourceEvidence } from "@generated/models";

export function formatAttemptResourceEnvelope(
  resources: PlanResourceEvidence | null | undefined,
  executorAvailable: string,
): string {
  if (!resources) return "—";
  const cpu = resources.cpu_millis === null || resources.cpu_millis === undefined
    ? `${executorAvailable} CPU`
    : `${resources.cpu_millis}m CPU`;
  const memory = resources.memory_bytes === null || resources.memory_bytes === undefined
    ? `${executorAvailable} RAM`
    : `${formatBytes(resources.memory_bytes)} RAM`;
  const processes = resources.max_processes === null || resources.max_processes === undefined
    ? null
    : `${resources.max_processes} processes`;
  return [cpu, memory, processes].filter(Boolean).join(" · ");
}

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GiB`;
}
