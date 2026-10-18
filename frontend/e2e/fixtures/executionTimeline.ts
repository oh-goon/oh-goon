export function fixtureObservationWindows(fromAt: string, toAt: string, bucket: "day" | "week" | "month") {
  const windows: { start_at: string; end_at: string }[] = [];
  const endLimit = new Date(toAt);
  let start = fixtureBucketStart(new Date(fromAt), bucket);
  while (start < endLimit && windows.length < 366) {
    const end = fixtureBucketEnd(start, bucket);
    windows.push({ start_at: start.toISOString(), end_at: end.toISOString() });
    start = end;
  }
  return windows;
}

function fixtureBucketStart(value: Date, bucket: "day" | "week" | "month"): Date {
  const day = new Date(Date.UTC(value.getUTCFullYear(), value.getUTCMonth(), value.getUTCDate()));
  if (bucket === "day") return day;
  if (bucket === "week") return new Date(day.getTime() - ((day.getUTCDay() + 6) % 7) * 86400000);
  return new Date(Date.UTC(value.getUTCFullYear(), value.getUTCMonth(), 1));
}

function fixtureBucketEnd(start: Date, bucket: "day" | "week" | "month"): Date {
  if (bucket === "day") return new Date(start.getTime() + 86400000);
  if (bucket === "week") return new Date(start.getTime() + 7 * 86400000);
  return new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 1));
}
