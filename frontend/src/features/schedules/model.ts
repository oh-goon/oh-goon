import { ScheduleExecutionStatus } from "@generated/enums";

export const SCHEDULE_FREQUENCY_MINUTES = {
  hourly: 60,
  sixHours: 360,
  daily: 1440,
  weekly: 10080,
} as const;

export type ScheduleFrequencyPreset = keyof typeof SCHEDULE_FREQUENCY_MINUTES;

export function scheduleFrequencyMinutes(frequency: ScheduleFrequencyPreset | "custom", customInterval: string): number {
  return frequency === "custom" ? Number(customInterval) : SCHEDULE_FREQUENCY_MINUTES[frequency];
}

export function scheduleKeyFromName(name: string): string {
  return name
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function scheduleFrequencyPreset(minutes: number): ScheduleFrequencyPreset | "custom" {
  const entry = Object.entries(SCHEDULE_FREQUENCY_MINUTES).find(([, value]) => value === minutes);
  return (entry?.[0] as ScheduleFrequencyPreset | undefined) ?? "custom";
}

export type ScheduleExecutionStatusValue =
  (typeof ScheduleExecutionStatus)[keyof typeof ScheduleExecutionStatus];

export function isScheduleExecutionActive(
  status: ScheduleExecutionStatusValue | null | undefined,
): boolean {
  return status === ScheduleExecutionStatus.RUNNING;
}
