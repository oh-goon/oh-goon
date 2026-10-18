function randomHex(): string {
  return globalThis.crypto.randomUUID().replaceAll("-", "");
}

export function newFleetRunId(): string {
  return `fleet-${randomHex()}`;
}

export function newScheduleRunId(): string {
  return `schedule-run-${randomHex()}`;
}
