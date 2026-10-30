import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiClientProvider } from "@/app/ApiClientProvider";
import { ReadController } from "@/domain/concurrency/ReadController";
import { readScopesInvalidatedByMutation } from "@/domain/reads/readResources";
import { useGameTapeArtifactObservation } from "@/features/game-tape/hooks/useGameTapeArtifactObservation";
import { GameTapeStepArtifacts } from "@/features/game-tape/components/GameTapeStepArtifacts";
import { OperationAction } from "@generated/enums";
import type { FleetRunDetail, GameTape } from "@generated/models";
import { InMemoryApiClient } from "@test/InMemoryApiClient";

const time = "2026-10-28T12:00:00Z";

function recordedDetail(runId: string, digest = "a".repeat(64)): FleetRunDetail {
  const repository = { repository_id: "repo-a", name: "subject", path: "/subject" };
  return {
    run: {
      fleet_run_id: runId, status: "COMPLETED", accepted_at: time, started_at: time, finished_at: time,
      capability: { capability_id: "fleet-plan-execute", policy_fingerprint: "recorded-policy", action: OperationAction.EXECUTE_PLAN, target_kind: "FLEET_PLAN", safety: "OPAQUE_MUTATION" },
      repository_scope: { kind: "EXPLICIT", repository_ids: [repository.repository_id] }, targets: [repository],
      progress: { total: 1, succeeded: 1, failed: 0, completed: 1 },
    },
    execution: { kind: "direct" }, outcome: "SUCCESS", repository_admissions: [], rerun_supported: false, plan_trust_receipts: [],
    game_tape: {
      fleet_run_id: runId, status: "PASSED", execution: { kind: "direct" }, hidden_issue_count: 0,
      receipt: { accepted_at: time, duration_ms: 0 }, storage: { maintenance_cuts: 0, pressure: "NO_EVIDENCE" },
      observability: { durable_evidence: [], transient_evidence: [], raw_output_max_chunks: 40, raw_output_max_chunk_chars: 16000, terminal_handoff_seconds: 30 },
      summary: { repositories: 1, passed: 1, failed: 0, attention_required: 0, not_run: 0, planned_steps: 1, executed_steps: 1, passed_steps: 1, failed_steps: 0, skipped_steps: 0, not_run_steps: 0 },
      team: { repository_count: 1, timed_repository_count: 1, observed_peak_concurrency: 1, overlap_observed: false, duration_ms: 0 },
      repositories: [{
        repository_execution_id: `${runId}:repo`, repository, status: "PASSED", fingerprint: {},
        bottleneck_step_id: null, bottleneck_percent: null, duration_ms: 0,
        steps: [{
          step_execution_id: `${runId}:step`, step_id: "train", kind: "command", order: 1, status: "PASSED", attempt_count: 1,
          retry_context: { classification: "NOT_RETRIED", retry_count: 0, linked_retry_count: 0, physical_context_changed: false },
          why: { declaration: "train", reached: true, explanation: "Recorded training" },
          artifacts: [{ status: "PRESENT", path: "model.json", sha256: digest, byte_count: 4, retained_object: { kind: "file", sha256: "b".repeat(64), byte_count: 4, entry_count: 1, object_id: `file-sha256-${"b".repeat(64)}`, reference_id: "ref-recorded", owner_repository_id: repository.repository_id } }],
        }],
      }],
      artifact_observation: {
        schema_name: "oh-goon.artifact-availability/v1", fleet_run_id: runId, started_at: time, finished_at: time,
        artifact_limit: 32, byte_budget: 1024, time_budget_seconds: 1, total_artifacts: 1, omitted_artifacts: 0, bytes_scheduled: 4,
        observations: [{ repository_execution_id: `${runId}:repo`, repository_id: repository.repository_id, step_execution_id: `${runId}:step`, step_id: "train", path: "model.json", recorded_status: "PRESENT", recorded_sha256: digest, recorded_byte_count: 4, reference_id: "ref-recorded", status: "AVAILABLE", reason: "VERIFIED", checked_at: time, staging_cleanup: "VERIFIED" }],
      },
    },
  };
}

describe("explicit Game Tape artifact observation", () => {
  it("renders a supplied observation without checking the retained bytes again", () => {
    const client = new InMemoryApiClient();
    const detail = recordedDetail("run-a");
    const repository = detail.game_tape.repositories[0]!;
    const step = repository.steps![0]!;
    const read = vi.spyOn(client, "fleetRunDetail");
    function Probe() {
      const { resource } = useGameTapeArtifactObservation(detail.game_tape);
      return <GameTapeStepArtifacts artifacts={step.artifacts ?? []} artifactOwner={{ repository_execution_id: repository.repository_execution_id, repository_id: repository.repository.repository_id, step_execution_id: step.step_execution_id, step_id: step.step_id }} observation={resource.data} />;
    }
    render(<ApiClientProvider client={client}><Probe /></ApiClientProvider>);
    expect(screen.getByText("Available when checked")).toBeTruthy();
    expect(screen.getByText("Checked at " + time)).toBeTruthy();
    expect(read).not.toHaveBeenCalled();
  });

  it("ignores a supplied observation for another Run and never reuses a seed after its record changes", () => {
    const client = new InMemoryApiClient();
    const original = recordedDetail("run-a");
    const changed = recordedDetail("run-a", "c".repeat(64));
    const foreign = recordedDetail("run-b");
    const read = vi.spyOn(client, "fleetRunDetail");
    let handle: ReturnType<typeof useGameTapeArtifactObservation> | undefined;
    function Probe({ tape }: { tape: GameTape }) { handle = useGameTapeArtifactObservation(tape); return null; }
    const view = render(<ApiClientProvider client={client}><Probe tape={original.game_tape} /></ApiClientProvider>);
    expect(handle?.resource.data?.fleet_run_id).toBe("run-a");
    view.rerender(<ApiClientProvider client={client}><Probe tape={changed.game_tape} /></ApiClientProvider>);
    expect(handle?.resource.data).toBeNull();
    view.rerender(<ApiClientProvider client={client}><Probe tape={foreign.game_tape} /></ApiClientProvider>);
    expect(handle?.resource.data).toBeNull();
    view.rerender(<ApiClientProvider client={client}><Probe tape={original.game_tape} /></ApiClientProvider>);
    expect(handle?.resource.data).toBeNull();
    view.unmount();
    render(<ApiClientProvider client={client}><Probe tape={{ ...original.game_tape, artifact_observation: foreign.game_tape.artifact_observation }} /></ApiClientProvider>);
    expect(handle?.resource.data).toBeNull();
    expect(read).not.toHaveBeenCalled();
  });

  it("checks only on request and never scans after invalidation or browser resume", async () => {
    const reads = new ReadController();
    const client = new InMemoryApiClient();
    const detail = recordedDetail("run-a");
    const read = vi.spyOn(client, "fleetRunDetail").mockResolvedValue(detail);
    let handle: ReturnType<typeof useGameTapeArtifactObservation> | undefined;
    function Probe() { handle = useGameTapeArtifactObservation(detail.game_tape); return null; }
    render(<ApiClientProvider client={client} readController={reads}><Probe /></ApiClientProvider>);
    expect(read).not.toHaveBeenCalled();
    await act(async () => { await handle?.refresh(); });
    expect(read).toHaveBeenCalledTimes(1);
    expect(read.mock.calls[0]?.[0]).toBe("run-a");
    expect(read.mock.calls[0]?.[1]?.signal).toBeInstanceOf(AbortSignal);
    expect(read.mock.calls[0]?.[2]).toBe(true);
    expect(handle?.resource.data?.observations[0]?.status).toBe("AVAILABLE");
    await act(async () => {
      reads.invalidateMany(readScopesInvalidatedByMutation("fleet-run"));
      window.dispatchEvent(new Event("online"));
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    expect(read).toHaveBeenCalledTimes(1);
    read.mockRejectedValueOnce(new Error("Artifact observation unavailable"));
    await act(async () => { await handle?.refresh(); });
    expect(handle?.resource.data).toBeNull();
    expect(handle?.resource.status).toBe("read-error");
    await act(async () => { window.dispatchEvent(new Event("online")); await Promise.resolve(); });
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("discards an old same-Run response when the recorded artifact changes", async () => {
    const client = new InMemoryApiClient();
    const original = recordedDetail("run-a");
    const changed = recordedDetail("run-a", "c".repeat(64));
    let release: (value: FleetRunDetail) => void = () => {};
    const pending = new Promise<FleetRunDetail>((resolve) => { release = resolve; });
    const read = vi.spyOn(client, "fleetRunDetail").mockReturnValueOnce(pending).mockResolvedValue(changed);
    let handle: ReturnType<typeof useGameTapeArtifactObservation> | undefined;
    function Probe({ tape }: { tape: GameTape }) { handle = useGameTapeArtifactObservation(tape); return null; }
    const view = render(<ApiClientProvider client={client}><Probe tape={original.game_tape} /></ApiClientProvider>);
    await act(async () => { void handle?.refresh(); await Promise.resolve(); });
    view.rerender(<ApiClientProvider client={client}><Probe tape={changed.game_tape} /></ApiClientProvider>);
    await act(async () => { release(original); await pending; });
    expect(handle?.resource.data).toBeNull();
    expect(read).toHaveBeenCalledTimes(1);
    await act(async () => { await handle?.refresh(); });
    expect(handle?.resource.data?.observations[0]?.recorded_sha256).toBe("c".repeat(64));
  });

  it("rejects an observation response belonging to a different Run", async () => {
    const client = new InMemoryApiClient();
    const selected = recordedDetail("run-a");
    vi.spyOn(client, "fleetRunDetail").mockResolvedValue(recordedDetail("run-b"));
    let handle: ReturnType<typeof useGameTapeArtifactObservation> | undefined;
    function Probe() { handle = useGameTapeArtifactObservation(selected.game_tape); return null; }
    render(<ApiClientProvider client={client}><Probe /></ApiClientProvider>);
    await act(async () => { await handle?.refresh(); });
    expect(handle?.resource.data).toBeNull();
    expect(handle?.resource.status).toBe("read-error");
  });
});
