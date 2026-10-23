import { render, screen, within } from "@testing-library/react";
import { vi } from "vitest";

import type { GameTape, GameTapeStepExecution, PlanStepAttemptEvidence } from "@generated/models";
import { GameTapeAttempts } from "@/features/game-tape/components/GameTapeAttempts";

function attemptTape(boundary?: PlanStepAttemptEvidence["standard_isolation"], quiescent?: boolean): GameTape {
  const output = { sha256: "a".repeat(64), byte_count: 0, line_count: 0 };
  const attempt = {
    attempt: 1, argv: ["codex"], returncode: 0, duration_ms: 10,
    repository_state_before: "same", repository_state_after: "same",
    egress: { mode: "host_unrestricted", policy_id: "selected-egress", rules: [] },
    stdout_evidence: output, stderr_evidence: output,
    standard_isolation: boundary,
    supervisor: quiescent === undefined ? undefined : {
      final_process_group_quiescent: quiescent, cleanup_reason: "none",
      process_group_cleanup_requested: false,
    },
  } as PlanStepAttemptEvidence;
  const step = {
    step_execution_id: "run:repo:1", step_id: "review", kind: "agent", order: 1,
    status: "PASSED", attempt_count: 1, attempts: [attempt],
    retry_context: { classification: "NOT_RETRIED", retry_count: 0, linked_retry_count: 0, physical_context_changed: false },
  } as GameTapeStepExecution;
  return {
    fleet_run_id: "run", repositories: [{
      repository_execution_id: "repo", repository: { repository_id: "repo", name: "app" },
      fingerprint: {}, steps: [step],
    }],
  } as unknown as GameTape;
}

describe("GameTapeAttempts boundary evidence", () => {
  it("shows recorded scope, credential exposure and cleanup without treating source protection as network denial", () => {
    const boundary: NonNullable<PlanStepAttemptEvidence["standard_isolation"]> = {
      backend: { backend_id: "bubblewrap", configuration_id: "frozen", kernel_boundary: "shared_host_kernel", remote_execution: false, experimental: false },
      host_and_external_ip_networking: "host_unrestricted",
      workspace_read_only: true, scoped_write_roots: ["src"],
      credential_visibility: "codex_auth_file_read_write", preflight: "verified",
      filesystem_cleanup: "verified", broker_socket_mounted: false, resource_owner: "none",
    };
    render(<GameTapeAttempts tape={attemptTape(boundary, true)} selectedId="run:repo:1" onSelect={vi.fn()} />);
    const card = screen.getByText("Attempt #1").closest("article");
    if (!card) throw new Error("Expected physical attempt card");
    expect(within(card).getByText("host_unrestricted")).toBeVisible();
    expect(within(card).getByText("read-only")).toBeVisible();
    expect(within(card).getByText(/Writable directories: src/)).toBeVisible();
    expect(within(card).getByText("Codex auth file: readable and writable by agent/tools")).toBeVisible();
    expect(within(card).getAllByText("verified")).toHaveLength(2);
  });

  it("leaves missing historical quiescence unknown and displays known unverified cleanup", () => {
    const tape = attemptTape();
    const { rerender } = render(<GameTapeAttempts tape={tape} selectedId="run:repo:1" onSelect={vi.fn()} />);
    expect(screen.getByText("not recorded")).toBeVisible();
    expect(screen.queryByText("verified")).toBeNull();
    const boundary: NonNullable<PlanStepAttemptEvidence["standard_isolation"]> = {
      backend: { backend_id: "bubblewrap", configuration_id: "frozen", kernel_boundary: "shared_host_kernel", remote_execution: false, experimental: false },
      workspace_read_only: true, credential_visibility: "none", filesystem_cleanup: "unverified",
      broker_socket_mounted: false, resource_owner: "none",
    };
    rerender(<GameTapeAttempts tape={attemptTape(boundary, false)} selectedId="run:repo:1" onSelect={vi.fn()} />);
    expect(screen.getAllByText("unverified")).toHaveLength(2);
    expect(screen.getByText("No credential file mounted")).toBeVisible();
    expect(screen.queryByText("verified")).toBeNull();
  });
});
