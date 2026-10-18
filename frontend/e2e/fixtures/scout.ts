import { GameTapeStatus } from "@generated/enums";
import type { RepositoryIdentity } from "@generated/models";

import { LONG_GOON_NAME, LONG_STEP_NAME, PRIMARY_REPOSITORY } from "./identities";
import { FIXTURE_SHA as SHA } from "./plans";
import { DEFAULT_RUN_ID } from "./runIds";
import { FIXTURE_NOW as NOW } from "./time";

export function scoutWindow(hours: number) {
  const end = new Date("2026-08-22T12:00:00Z");
  return {
    start_at: new Date(end.getTime() - hours * 60 * 60 * 1000).toISOString(),
    end_at: end.toISOString(),
    hours,
    run_assignment: "start_timestamp" as const,
  };
}

export function scoutRepositoryRow(repository: RepositoryIdentity = PRIMARY_REPOSITORY) {
  return {
    entity_kind: "REPOSITORY" as const,
    key: repository.repository_id,
    display_name: repository.name,
    repository_id: repository.repository_id,
    run_count: 1482,
    repository_count: 1,
    attempt_count: 1520,
    total_cpu_seconds: 1504800,
    median_cpu_seconds_per_run: 1014,
    median_duration_seconds_per_run: 1180,
    peak_memory_bytes: 5153960755,
    failed_cpu_seconds: 79200,
    cancelled_cpu_seconds: 18000,
    cpu_coverage: { observed: 1512, eligible: 1520, percent: 99.5 },
    memory_coverage: { observed: 1380, eligible: 1520, percent: 90.8 },
    representative_run_id: DEFAULT_RUN_ID,
  };
}

export function scoutPlanSummary() {
  return {
    plan_id: "plan-python-standard",
    plan_name: "python-standard",
    run_count: 1482,
    attempt_count: 1520,
    total_cpu_seconds: 1504800,
    median_cpu_seconds_per_run: 1014,
    median_duration_seconds_per_run: 1180,
    peak_memory_bytes: 5153960755,
    cpu_coverage: { observed: 1512, eligible: 1520, percent: 99.5 },
    memory_coverage: { observed: 1380, eligible: 1520, percent: 90.8 },
    representative_run_id: DEFAULT_RUN_ID,
  };
}

export function scoutGoonRow() {
  return {
    entity_kind: "GOON" as const,
    key: `${LONG_GOON_NAME}:${SHA}`,
    display_name: LONG_GOON_NAME,
    goon_name: LONG_GOON_NAME,
    goon_source_sha256: SHA,
    run_count: 1471,
    repository_count: 1219,
    attempt_count: 1499,
    total_cpu_seconds: 1072800,
    median_cpu_seconds_per_run: 726,
    peak_memory_bytes: 4294967296,
    failed_cpu_seconds: 61000,
    cancelled_cpu_seconds: 9000,
    cpu_coverage: { observed: 1492, eligible: 1499, percent: 99.5 },
    memory_coverage: { observed: 1440, eligible: 1499, percent: 96.1 },
    representative_run_id: DEFAULT_RUN_ID,
  };
}

export function scoutComparisonEnvironment() {
  return {
    isolation_backend_id: "local",
    isolation_configuration_id: "isolation-v3",
    runtime_root_sha256: "c".repeat(64),
    host_toolchain_id: "host-tools-v1",
    resource_envelopes: [{ cpu_millis: 2000, memory_bytes: 536870912 }],
  };
}

export function scoutStepSummary() {
  return {
    step_id: LONG_STEP_NAME,
    goon_name: LONG_GOON_NAME,
    goon_source_sha256: SHA,
    run_count: 944,
    attempt_count: 972,
    total_cpu_seconds: 668400,
    median_cpu_seconds_per_run: 121,
    median_duration_seconds_per_run: 134,
    peer_median_cpu_seconds_per_run: 42,
    cpu_ratio_to_peer_median: 2.88,
    peer_median_duration_seconds_per_run: 47,
    duration_ratio_to_peer_median: 2.85,
    peak_memory_bytes: 5153960755,
    peer_median_peak_memory_bytes: 1825361100,
    memory_ratio_to_peer_median: 2.82,
    peer_execution_count: 944,
    peer_repository_count: 1821,
    peer_cpu_coverage: { observed: 943, eligible: 944, percent: 99.9 },
    peer_memory_coverage: { observed: 920, eligible: 944, percent: 97.5 },
    comparison_environment: scoutComparisonEnvironment(),
    cpu_coverage: { observed: 970, eligible: 972, percent: 99.8 },
    memory_coverage: { observed: 940, eligible: 972, percent: 96.7 },
    representative_run_id: DEFAULT_RUN_ID,
  };
}

export function scoutRunSample() {
  return {
    run_id: DEFAULT_RUN_ID,
    started_at: NOW,
    status: GameTapeStatus.PASSED,
    cpu_seconds: 121,
    duration_seconds: 134,
    peak_memory_bytes: 5153960755,
    goon_name: LONG_GOON_NAME,
    step_id: LONG_STEP_NAME,
  };
}
