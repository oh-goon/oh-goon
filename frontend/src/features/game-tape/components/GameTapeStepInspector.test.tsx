import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";

import type { GameTapeFailureHistory, GameTapeStepExecution } from "@generated/models";
import { GameTapeStepInspector } from "@/features/game-tape/components/GameTapeStepInspector";

function failedStep(): GameTapeStepExecution {
  return {
    step_execution_id: "run:repo:step:2",
    step_id: "pytest",
    kind: "command",
    order: 2,
    status: "FAILED",
    started_at: "2026-08-22T06:00:00Z",
    finished_at: "2026-08-22T06:00:20Z",
    duration_ms: 20_000,
    command: ["uv", "run", "pytest"],
    working_directory: ".",
    exit_code: 1,
    output_names: [],
    attempt_count: 1,
    attempts: [],
    attempt_transitions: [],
    environment: [],
    retry_context: { classification: "NOT_RETRIED", retry_count: 0, linked_retry_count: 0, physical_context_changed: false },
    why: { declaration: "templates/goons/python/tests/goon.yml:12", source_location: { source: "templates/goons/python/tests/goon.yml", line: 12, column: 5 }, reached: true, explanation: "Execution reached this Step." },
    stdout_evidence: null,
    stderr_evidence: null,
    reason: "pytest command failed",
    failure: { exit_code: 1, reason_code: "COMMAND_FAILED", signature_id: "sig-123", raw_log_available: false },
  } as unknown as GameTapeStepExecution;
}

describe("GameTapeStepInspector failure actions", () => {
  it("navigates failures and copies only canonical failure facts", async () => {
    const previous = vi.fn();
    const next = vi.fn();
    const openLogs = vi.fn();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<GameTapeStepInspector step={failedStep()} onPreviousFailure={previous} onNextFailure={next} onOpenEvidence={openLogs} />);
    fireEvent.click(screen.getByRole("button", { name: "Previous failure" }));
    fireEvent.click(screen.getByRole("button", { name: "Next failure" }));
    fireEvent.click(screen.getByRole("button", { name: "Copy error" }));
    fireEvent.click(screen.getByRole("button", { name: "Open Logs" }));
    expect(previous).toHaveBeenCalledOnce();
    expect(next).toHaveBeenCalledOnce();
    expect(openLogs).toHaveBeenCalledOnce();
    expect(await screen.findByRole("button", { name: "Error copied" })).toBeTruthy();
    expect(writeText).toHaveBeenCalledWith("Reason: pytest command failed\nReason code: COMMAND_FAILED\nExit code: 1\nSignature: sig-123");
  });
  it("keeps explanation visible while exact execution details are disclosed explicitly", async () => {
    render(<GameTapeStepInspector step={failedStep()} />);
    expect(screen.getByText("Execution reached this Step.")).toBeTruthy();
    expect(screen.getByText("Technical Step details")).toBeTruthy();
    const command = screen.getByText("uv run pytest");
    expect(command.closest("details")?.open).toBe(false);
    fireEvent.click(screen.getByText("Technical Step details"));
    await waitFor(() => expect(command.closest("details")?.open).toBe(true));
  });

  it("keeps failure keyboard traversal scoped to the focused inspector", () => {
    const previous = vi.fn();
    const next = vi.fn();
    const close = vi.fn();
    render(<GameTapeStepInspector step={failedStep()} onPreviousFailure={previous} onNextFailure={next} onClose={close} />);
    const inspector = screen.getByRole("complementary", { name: "Step inspector" });
    fireEvent.keyDown(inspector, { key: "ArrowUp", altKey: true });
    fireEvent.keyDown(inspector, { key: "ArrowDown", altKey: true });
    fireEvent.keyDown(inspector, { key: "Escape" });
    expect(previous).toHaveBeenCalledOnce();
    expect(next).toHaveBeenCalledOnce();
    expect(close).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Previous failure" })).toHaveAttribute("aria-keyshortcuts", "Alt+ArrowUp");
    expect(screen.getByRole("button", { name: "Next failure" })).toHaveAttribute("aria-keyshortcuts", "Alt+ArrowDown");
  });

  it("moves keyboard focus into the inspector when drill-down opens", async () => {
    render(<GameTapeStepInspector step={failedStep()} />);
    await waitFor(() => expect(screen.getByRole("complementary", { name: "Step inspector" })).toHaveFocus());
  });

  it("restores explicit technical disclosure state and reports later disclosure changes", async () => {
    const onTechnicalOpenChange = vi.fn();
    render(<GameTapeStepInspector step={failedStep()} technicalOpen onTechnicalOpenChange={onTechnicalOpenChange} />);
    const details = screen.getByText("Technical Step details").closest("details");
    expect(details?.open).toBe(true);
    fireEvent.click(screen.getByText("Technical Step details"));
    await waitFor(() => expect(onTechnicalOpenChange).toHaveBeenCalledWith(false));
  });

  it("shows only canonical failure recurrence facts before technical details", () => {
    const history = { evolution: { exact_occurrence_count: 3, first_seen_run_id: "run-earlier", first_seen_at: "2026-08-20T15:00:00Z", last_seen_run_id: "run-current", last_seen_at: "2026-08-22T06:00:00Z", exact_failure_recurred_after_pass: true } } as unknown as GameTapeFailureHistory;
    render(<GameTapeStepInspector step={failedStep()} failureHistory={history} />);
    expect(screen.getByText("Seen before")).toBeTruthy();
    expect(screen.getByText("Exact occurrences")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.getByText("Exact failure reappeared after a pass")).toBeTruthy();
    expect(screen.getByText("yes", { exact: false })).toBeTruthy();
  });

  it("offers previous exact comparison only when Game Tape provides an eligible comparison action", () => {
    const compare = vi.fn();
    const history = { evolution: { exact_occurrence_count: 2, first_seen_run_id: "run-earlier", first_seen_at: "2026-08-20T15:00:00Z", last_seen_run_id: "run-current", last_seen_at: "2026-08-22T06:00:00Z", exact_failure_recurred_after_pass: false } } as unknown as GameTapeFailureHistory;
    const { rerender } = render(<GameTapeStepInspector step={failedStep()} failureHistory={history} />);
    expect(screen.queryByRole("button", { name: "Compare previous exact occurrence" })).toBeNull();
    rerender(<GameTapeStepInspector step={failedStep()} failureHistory={history} onComparePreviousOccurrence={compare} />);
    fireEvent.click(screen.getByRole("button", { name: "Compare previous exact occurrence" }));
    expect(compare).toHaveBeenCalledOnce();
  });

  it("shows Step-local artifacts before technical execution details", () => {
    const artifact = { status: "PRESENT", path: "reports/junit.xml", sha256: "a".repeat(64), byte_count: 4096, retained_object: null } as const;
    render(<GameTapeStepInspector step={failedStep()} artifacts={[artifact]} />);
    const artifactPath = screen.getAllByText("reports/junit.xml").at(0);
    if (!artifactPath) throw new Error("Expected rendered artifact path");
    const technical = screen.getByText("Technical Step details");
    expect(artifactPath.compareDocumentPosition(technical) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(artifactPath.closest(".game-tape-artifacts")).toBeTruthy();
  });

  it("discloses recorded native claims and omissions without replacing the failed Step", () => {
    const step = failedStep();
    step.native_agent_observations = [{
      kind: "native_agent", agent: "pi", profile: "isolated-managed", role: "report",
      implementation_identity: "pi:sha256:" + "1".repeat(64),
      input_identity: "sha256:" + "2".repeat(64), source_identity: "sha256:" + "3".repeat(64),
      occurrence: "agent", native_event_count: 140, omitted_observations: 12,
      settled: true, failed: false, authority: "UNTRUSTED_HARNESS_CLAIM",
      events: [{ kind: "context", summary: "pi: auto_compaction_end" }],
    }];
    const { rerender } = render(<GameTapeStepInspector step={step} />);
    expect(screen.getByText("Native agent observations")).toBeVisible();
    expect(screen.getByText(/Native events are agent claims/)).toBeVisible();
    expect(screen.getByText("pi: auto_compaction_end")).not.toBeVisible();
    fireEvent.click(screen.getByText("pi · isolated-managed · report"));
    expect(screen.getByText("pi: auto_compaction_end")).toBeVisible();
    expect(screen.getByText("12")).toBeVisible();
    expect(screen.getByText("pytest command failed", { exact: false })).toBeVisible();
    rerender(<GameTapeStepInspector step={failedStep()} />);
    expect(screen.queryByText("Native agent observations")).toBeNull();
    expect(screen.queryByText("pi: auto_compaction_end")).toBeNull();
  });

  it("shows derived materialization, runtime, atomic handoff and economics evidence", async () => {
    const step = failedStep() as unknown as Record<string, unknown>;
    step.execution_authority = {
      materialization: {
        receipt_identity: "sha256:" + "1".repeat(64),
        logical_sources: ["Makefile"],
        package_executables: ["vitest"],
        owned_scripts: ["scripts/test.sh"],
        direct_workspace_executable: null,
      },
      runtime: {
        receipt_identity: "sha256:" + "2".repeat(64),
        executable_path: "/toolchain/bin/sh",
        executable_identity: "sha256:" + "3".repeat(64),
        path_authority_identity: "sha256:" + "4".repeat(64),
        environment_identity: "sha256:" + "5".repeat(64),
        provider_identity: "sha256:" + "6".repeat(64),
        runtime_identity: "sha256:" + "7".repeat(64),
        interpreter_path: null,
        interpreter_identity: null,
        execution_materialization_identity: "sha256:" + "1".repeat(64),
      },
      atomic_handoff: { state: "PROVEN", mechanism: "sealed_memfd_bind", terminal_proof_bytes: 4096 },
    };
    step.attempts = [{
      execution_authority_economics: [{
        scope: "runtime-launch", candidate_units: 1, reused_units: 1, recomputed_units: 0,
        reused_bytes: 4096, recomputed_bytes: 0, terminal_proof_bytes: 4096,
        reuse_provenance: ["prepared-host-tool:sh"], reuse_identities: ["sha256:" + "3".repeat(64)],
        recompute_reasons: [], receipt_identity: "sha256:" + "8".repeat(64),
      }],
    }];
    render(<GameTapeStepInspector step={step as unknown as GameTapeStepExecution} technicalOpen />);
    expect(screen.getByText("Execution authority")).toBeTruthy();
    expect(screen.getByText("PROVEN · sealed_memfd_bind")).toBeTruthy();
    expect(screen.getByText("/toolchain/bin/sh")).toBeTruthy();
    expect(screen.getByText("vitest")).toBeTruthy();
    expect(screen.getByText(/reused 1 · recomputed 0/)).toBeTruthy();
  });

});
