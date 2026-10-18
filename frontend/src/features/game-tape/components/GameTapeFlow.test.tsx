import { type ComponentProps, useState } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { GameTape, GameTapeRepositoryExecution, GameTapeStepExecution } from "@generated/models";
import { GameTapeFlow } from "@/features/game-tape/components/GameTapeFlow";

function flowStep(repositoryId: string, id: string, order: number, status: string, source: string | null, attemptCount = 1): GameTapeStepExecution {
  return {
    step_execution_id: `${repositoryId}:step:${order}`,
    step_id: id,
    kind: "command",
    order,
    status,
    duration_ms: 1200,
    attempt_count: attemptCount,
    retry_context: { classification: "NOT_RETRIED", retry_count: 0, linked_retry_count: 0, physical_context_changed: false },
    why: {
      declaration: source ? `${source}:10` : "resolved-plan",
      source_location: source ? { source, line: 10, column: 3 } : null,
      reached: true,
      explanation: "Reached in admitted order.",
    },
  } as GameTapeStepExecution;
}

function lane(id: string, name: string, status: string, steps: GameTapeStepExecution[], bottleneckStepId: string | null = null, bottleneckPercent: number | null = null): GameTapeRepositoryExecution {
  return {
    repository_execution_id: id,
    repository: { repository_id: id, name, path: `/workspace/${name}` },
    status,
    fingerprint: {},
    bottleneck_step_id: bottleneckStepId,
    bottleneck_percent: bottleneckPercent,
    steps,
  } as unknown as GameTapeRepositoryExecution;
}

type GameTapeFlowProps = ComponentProps<typeof GameTapeFlow>;

function ControlledGameTapeFlow(props: GameTapeFlowProps) {
  const [query, setQuery] = useState(props.query ?? "");
  const [collapsedLaneIds, setCollapsedLaneIds] = useState<readonly string[]>(props.collapsedLaneIds ?? []);
  const [sourceSegmentStepExecutionId, setSourceSegmentStepExecutionId] = useState<string | null>(props.sourceSegmentStepExecutionId ?? null);
  return <GameTapeFlow
    {...props}
    query={query}
    collapsedLaneIds={collapsedLaneIds}
    sourceSegmentStepExecutionId={sourceSegmentStepExecutionId}
    onQueryChange={(value) => { setQuery(value); props.onQueryChange?.(value); }}
    onCollapsedLaneIdsChange={(ids, historyMode) => { setCollapsedLaneIds(ids); props.onCollapsedLaneIdsChange?.(ids, historyMode); }}
    onSourceSegmentChange={(id, historyMode) => { setSourceSegmentStepExecutionId(id); props.onSourceSegmentChange?.(id, historyMode); }}
  />;
}

function tape(repositories: GameTapeRepositoryExecution[], status = "PASSED"): GameTape {
  const steps = repositories.flatMap((repository) => repository.steps ?? []);
  return {
    fleet_run_id: "run:flow",
    status,
    execution_reference: {
      plan_id: "plan:flow",
      plan_name: "Production CI/CD",
      source: { kind: "plan_file", sha256: "a".repeat(64) },
      identity: { digest: `sha256:${"b".repeat(64)}` },
    },
    summary: {
      repositories: repositories.length,
      passed: repositories.filter((repository) => repository.status === "PASSED").length,
      failed: repositories.filter((repository) => repository.status === "FAILED").length,
      attention_required: 0,
      not_run: 0,
      planned_steps: steps.length,
      executed_steps: steps.length,
      passed_steps: steps.filter((step) => step.status === "PASSED").length,
      failed_steps: steps.filter((step) => step.status === "FAILED").length,
      skipped_steps: 0,
      not_run_steps: 0,
    },
    repositories,
  } as unknown as GameTape;
}

describe("GameTapeFlow", () => {
  it("shows multi-repository navigation, recorded status counts and explicit repository outcomes", () => {
    const first = lane("repo:api", "api", "PASSED", [flowStep("repo:api", "format", 1, "PASSED", "templates/goons/python/python-quality/goon.yml")]);
    const second = lane("repo:web", "web", "PASSED", [flowStep("repo:web", "test", 1, "PASSED", "templates/goons/python/python-quality/goon.yml")]);
    render(<ControlledGameTapeFlow tape={tape([first, second])} selectedId={null} onSelect={vi.fn()} />);

    expect(screen.getByRole("navigation", { name: "Repository lane navigation" })).toBeTruthy();
    expect(screen.getByText("2 Passed")).toBeTruthy();
    expect(screen.getAllByText("Repository outcome")).toHaveLength(2);
  });

  it("omits redundant repository navigation for a single lane without hiding the lane", () => {
    const repository = lane("repo:api", "api", "FAILED", [
      flowStep("repo:api", "format", 1, "PASSED", "templates/goons/python/python-quality/goon.yml"),
      flowStep("repo:api", "security-audit", 2, "FAILED", "templates/goons/security/trivy-audit/goon.yml"),
    ]);
    render(<ControlledGameTapeFlow tape={tape([repository], "FAILED")} selectedId={null} onSelect={vi.fn()} failureFocus />);

    expect(screen.queryByRole("navigation", { name: "Repository lane navigation" })).toBeNull();
    expect(screen.getByText("Repository outcome")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Step 1: format/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Step 2: security-audit/ })).toBeTruthy();
  });

  it("marks a failing Step as lane-local review focus while keeping Step selection explicit", () => {
    const onSelect = vi.fn();
    const failed = flowStep("repo:api", "security-audit", 2, "FAILED", "templates/goons/security/trivy-audit/goon.yml");
    const repository = lane("repo:api", "api", "FAILED", [flowStep("repo:api", "format", 1, "PASSED", "templates/goons/python/python-quality/goon.yml"), failed]);
    render(<ControlledGameTapeFlow tape={tape([repository], "FAILED")} selectedId={null} onSelect={onSelect} />);

    expect(screen.getByText("Review focus")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Jump to Failed Step" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Step 2: security-audit/ }));
    expect(onSelect).toHaveBeenCalledWith(failed.step_execution_id);
  });
  it("highlights matching Step context without hiding non-matching admitted Steps", async () => {
    const source = "templates/goons/security/trivy-audit/goon.yml";
    const repository = lane("repo:api", "api", "PASSED", [flowStep("repo:api", "format", 1, "PASSED", "templates/goons/python/python-quality/goon.yml"), flowStep("repo:api", "audit", 2, "PASSED", source)]);
    render(<ControlledGameTapeFlow tape={tape([repository])} selectedId={null} onSelect={vi.fn()} />);

    expect(screen.getByRole("search", { name: "CI/CD Flow finder" })).toBeTruthy();
    fireEvent.change(screen.getByRole("searchbox", { name: "Find in CI/CD Flow" }), { target: { value: "trivy-audit" } });
    expect(await screen.findByText("Matches: 1")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Step 1: format/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Step 2: audit/ }).className).toContain("game-tape-flowchart__step--match");
  });

  it("restores and reports the Flow finder query without hiding non-matching Steps", async () => {
    const onQueryChange = vi.fn();
    const repository = lane("repo:api", "api", "PASSED", [
      flowStep("repo:api", "format", 1, "PASSED", "templates/goons/python/python-quality/goon.yml"),
      flowStep("repo:api", "audit", 2, "PASSED", "templates/goons/security/trivy-audit/goon.yml"),
    ]);
    render(<ControlledGameTapeFlow tape={tape([repository])} selectedId={null} onSelect={vi.fn()} query="trivy-audit" onQueryChange={onQueryChange} />);
    expect((screen.getByRole("searchbox", { name: "Find in CI/CD Flow" }) as HTMLInputElement).value).toBe("trivy-audit");
    expect(screen.getByText("Matches: 1")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Step 1: format/ })).toBeTruthy();
    fireEvent.change(screen.getByRole("searchbox", { name: "Find in CI/CD Flow" }), { target: { value: "python-quality" } });
    expect(onQueryChange).toHaveBeenCalledWith("python-quality");
    expect(await screen.findByText("Matches: 1")).toBeTruthy();
  });

  it("restores a canonical source-segment anchor without changing selected Step identity", async () => {
    const onSelect = vi.fn();
    const onSourceSegmentChange = vi.fn();
    const repository = lane("repo:api", "api", "PASSED", [
      flowStep("repo:api", "format", 1, "PASSED", "templates/goons/python/python-quality/goon.yml"),
      flowStep("repo:api", "audit", 2, "PASSED", "templates/goons/security/trivy-audit/goon.yml"),
    ]);
    render(<ControlledGameTapeFlow tape={tape([repository])} selectedId={null} onSelect={onSelect} sourceSegmentStepExecutionId="repo:api:step:2" onSourceSegmentChange={onSourceSegmentChange} />);
    const nav = screen.getByRole("navigation", { name: "Goon/source segments for api" });
    await waitFor(() => expect(nav.getElementsByTagName("button")[1]?.getAttribute("aria-current")).toBe("location"));
    expect(onSelect).not.toHaveBeenCalled();
    expect(onSourceSegmentChange).not.toHaveBeenCalledWith("repo:api:step:2", "push");
  });

  it("reports source-segment navigation as an explicit browser checkpoint", () => {
    const onSourceSegmentChange = vi.fn();
    const repository = lane("repo:api", "api", "PASSED", [
      flowStep("repo:api", "format", 1, "PASSED", "templates/goons/python/python-quality/goon.yml"),
      flowStep("repo:api", "audit", 2, "PASSED", "templates/goons/security/trivy-audit/goon.yml"),
    ]);
    render(<ControlledGameTapeFlow tape={tape([repository])} selectedId={null} onSelect={vi.fn()} onSourceSegmentChange={onSourceSegmentChange} />);
    fireEvent.click(screen.getByRole("button", { name: /2 · trivy-audit/ }));
    expect(onSourceSegmentChange).toHaveBeenLastCalledWith("repo:api:step:2", "push");
  });

  it("shows selected Repository → source → Step context with lane-local boundaries", () => {
    const firstStep = flowStep("repo:api", "format", 1, "PASSED", "templates/goons/python/python-quality/goon.yml");
    const secondStep = flowStep("repo:api", "audit", 2, "PASSED", "templates/goons/security/trivy-audit/goon.yml");
    const repository = lane("repo:api", "api", "PASSED", [firstStep, secondStep]);
    render(<ControlledGameTapeFlow tape={tape([repository])} selectedId={secondStep.step_execution_id} onSelect={vi.fn()} />);

    const context = screen.getByRole("status", { name: "Selected Step context" });
    expect(context.textContent).toContain("api → trivy-audit → audit");
    expect(context.textContent).toContain("Step 2 of 2 in this repository lane");
    expect((screen.getByRole("button", { name: "Previous Step" }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "Next Step" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows only factual source summaries, bottleneck evidence and multi-attempt counts", () => {
    const source = "templates/goons/python/python-quality/goon.yml";
    const first = flowStep("repo:api", "format", 1, "PASSED", source);
    const second = flowStep("repo:api", "test", 2, "FAILED", source, 2);
    const repository = lane("repo:api", "api", "FAILED", [first, second], "test", 61);
    render(<ControlledGameTapeFlow tape={tape([repository], "FAILED")} selectedId={null} onSelect={vi.fn()} />);

    const segmentStatuses = screen.getByRole("group", { name: "Recorded Step statuses for this source segment" });
    expect(segmentStatuses.textContent).toContain("1 Failed");
    expect(segmentStatuses.textContent).toContain("1 Passed");
    expect(segmentStatuses.querySelectorAll(".status-badge")).toHaveLength(2);
    expect(screen.getByText("Bottleneck · 61% of repository duration")).toBeTruthy();
    expect(screen.getByText("2 recorded attempts")).toBeTruthy();
  });

  it("navigates recorded source segments without hiding or crossing repository lanes", () => {
    const onSelect = vi.fn();
    const first = flowStep("repo:api", "format", 1, "PASSED", "templates/goons/python/python-quality/goon.yml");
    const second = flowStep("repo:api", "audit", 2, "PASSED", "templates/goons/security/trivy-audit/goon.yml");
    const third = flowStep("repo:api", "report", 3, "PASSED", "templates/goons/general/report/goon.yml");
    const repository = lane("repo:api", "api", "PASSED", [first, second, third]);
    render(<ControlledGameTapeFlow tape={tape([repository])} selectedId={second.step_execution_id} onSelect={onSelect} />);

    expect(screen.getByRole("navigation", { name: "Goon/source segments for api" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /1 · python-quality/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /2 · trivy-audit/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /3 · report/ })).toBeTruthy();
    const context = screen.getByRole("status", { name: "Selected Step context" });
    expect(context.textContent).toContain("Repository lane 1 of 1");
    expect(context.textContent).toContain("Source segment 2 of 3 in this repository lane");
    fireEvent.click(screen.getByRole("button", { name: "Previous source segment" }));
    expect(onSelect).toHaveBeenCalledWith(first.step_execution_id);
    fireEvent.click(screen.getByRole("button", { name: "Next source segment" }));
    expect(onSelect).toHaveBeenCalledWith(third.step_execution_id);
  });

  it("uses the Plan status scoreboard as non-destructive Step navigation", () => {
    const onSelect = vi.fn();
    const repository = lane("repo:api", "api", "FAILED", [
      flowStep("repo:api", "first-failure", 1, "FAILED", "templates/goons/python/python-quality/goon.yml"),
      flowStep("repo:api", "passed", 2, "PASSED", "templates/goons/python/python-quality/goon.yml"),
      flowStep("repo:api", "second-failure", 3, "FAILED", "templates/goons/security/trivy-audit/goon.yml"),
    ]);
    const { rerender } = render(<ControlledGameTapeFlow tape={tape([repository], "FAILED")} selectedId={null} onSelect={onSelect} />);
    const failedNav = screen.getByRole("button", { name: "Jump through 2 Failed Steps" });
    fireEvent.click(failedNav);
    expect(onSelect).toHaveBeenCalledWith("repo:api:step:1");
    rerender(<ControlledGameTapeFlow tape={tape([repository], "FAILED")} selectedId="repo:api:step:1" onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: "Jump through 2 Failed Steps" }));
    expect(onSelect).toHaveBeenLastCalledWith("repo:api:step:3");
    expect(screen.getByRole("button", { name: /Step 2: passed/ })).toBeTruthy();
  });

  it("keeps keyboard Step traversal inside the focused repository lane", () => {
    const onSelect = vi.fn();
    const first = flowStep("repo:api", "one", 1, "PASSED", "templates/goons/python/python-quality/goon.yml");
    const second = flowStep("repo:api", "two", 2, "PASSED", "templates/goons/security/trivy-audit/goon.yml");
    const third = flowStep("repo:api", "three", 3, "PASSED", "templates/goons/general/report/goon.yml");
    const other = flowStep("repo:web", "other", 1, "PASSED", "templates/goons/general/report/goon.yml");
    render(<ControlledGameTapeFlow tape={tape([lane("repo:api", "api", "PASSED", [first, second, third]), lane("repo:web", "web", "PASSED", [other])])} selectedId={second.step_execution_id} onSelect={onSelect} />);
    const current = screen.getByRole("button", { name: /Step 2: two/ });
    fireEvent.keyDown(current, { key: "ArrowRight" });
    expect(onSelect).toHaveBeenLastCalledWith(third.step_execution_id);
    fireEvent.keyDown(current, { key: "ArrowLeft" });
    expect(onSelect).toHaveBeenLastCalledWith(first.step_execution_id);
    fireEvent.keyDown(current, { key: "Home" });
    expect(onSelect).toHaveBeenLastCalledWith(first.step_execution_id);
    fireEvent.keyDown(current, { key: "End" });
    expect(onSelect).toHaveBeenLastCalledWith(third.step_execution_id);
    expect(onSelect).not.toHaveBeenCalledWith(other.step_execution_id);
  });

  it("repository lane navigation selects one canonical Step in the target lane", () => {
    const onSelect = vi.fn();
    const first = flowStep("repo:api", "format", 1, "PASSED", "templates/goons/python/python-quality/goon.yml");
    const second = flowStep("repo:api", "audit", 2, "PASSED", "templates/goons/security/trivy-audit/goon.yml");
    const other = flowStep("repo:web", "other", 1, "PASSED", "templates/goons/general/report/goon.yml");
    const repositories = [lane("repo:api", "api", "PASSED", [first, second]), lane("repo:web", "web", "PASSED", [other])];
    const { rerender } = render(<ControlledGameTapeFlow tape={tape(repositories)} selectedId={second.step_execution_id} onSelect={onSelect} />);

    const laneNavigation = screen.getByRole("navigation", { name: "Repository lane navigation" });
    fireEvent.click(within(laneNavigation).getByRole("button", { name: /Lane 1 · api/ }));
    expect(onSelect).toHaveBeenLastCalledWith(second.step_execution_id);

    fireEvent.click(within(laneNavigation).getByRole("button", { name: /Lane 2 · web/ }));
    expect(onSelect).toHaveBeenLastCalledWith(other.step_execution_id);

    rerender(<ControlledGameTapeFlow tape={tape(repositories)} selectedId={other.step_execution_id} onSelect={onSelect} />);
    expect(within(screen.getByRole("navigation", { name: "Repository lane navigation" })).getByRole("button", { name: /Lane 2 · web/ }).getAttribute("aria-current")).toBe("true");
  });

  it("aligns selected repository, source segment and Step from one canonical selection", () => {
    const first = flowStep("repo:api", "format", 1, "PASSED", "templates/goons/python/python-quality/goon.yml");
    const second = flowStep("repo:api", "audit", 2, "PASSED", "templates/goons/security/trivy-audit/goon.yml");
    const other = flowStep("repo:web", "other", 1, "PASSED", "templates/goons/general/report/goon.yml");
    render(<ControlledGameTapeFlow tape={tape([lane("repo:api", "api", "PASSED", [first, second]), lane("repo:web", "web", "PASSED", [other])])} selectedId={second.step_execution_id} onSelect={vi.fn()} />);

    const laneNavigation = screen.getByRole("navigation", { name: "Repository lane navigation" });
    expect(within(laneNavigation).getByRole("button", { name: /Lane 1 · api/ }).getAttribute("aria-current")).toBe("true");
    const sourceNavigation = screen.getByRole("navigation", { name: "Goon/source segments for api" });
    expect(within(sourceNavigation).getByRole("button", { name: /2 · trivy-audit/ }).getAttribute("aria-current")).toBe("location");
    expect(screen.getByRole("button", { name: /Step 2: audit/ }).className).toContain("game-tape-flowchart__step--selected");
  });

  it("shows exact canonical Step-status counts in each repository lane header", () => {
    const repo = lane("repo:api", "api", "FAILED", [
      flowStep("repo:api", "ok", 1, "PASSED", "templates/goons/a/goon.yml"),
      flowStep("repo:api", "bad", 2, "FAILED", "templates/goons/b/goon.yml"),
    ]);
    render(<ControlledGameTapeFlow tape={tape([repo], "FAILED")} selectedId={null} onSelect={vi.fn()} />);
    const summary = screen.getByRole("group", { name: "Recorded Step statuses for this repository lane" });
    expect(summary).toHaveTextContent("1 Failed");
    expect(summary).toHaveTextContent("1 Passed");
  });

  it("renders a decorative repository-scope fan-out for the exact lane count", () => {
    const first = lane("repo:api", "api", "PASSED", [flowStep("repo:api", "one", 1, "PASSED", "templates/goons/a/goon.yml")]);
    const second = lane("repo:web", "web", "PASSED", [flowStep("repo:web", "two", 1, "PASSED", "templates/goons/b/goon.yml")]);
    const { container } = render(<ControlledGameTapeFlow tape={tape([first, second])} selectedId={null} onSelect={vi.fn()} />);
    expect(container.querySelector('svg.game-tape-flowchart__scope-connector[data-lanes="2"]')).toBeTruthy();
    expect(screen.getByRole("group", { name: "Repository scope junction" })).toHaveTextContent("No cross-repository execution order is asserted by this view.");
  });

  it("keeps repository lane details open by default and folds them only by explicit user action", () => {
    const first = lane("repo:api", "api", "PASSED", [flowStep("repo:api", "one", 1, "PASSED", "templates/goons/python/python-quality/goon.yml")]);
    const second = lane("repo:web", "web", "PASSED", [flowStep("repo:web", "two", 1, "PASSED", "templates/goons/general/report/goon.yml")]);
    render(<ControlledGameTapeFlow tape={tape([first, second])} selectedId={null} onSelect={vi.fn()} />);

    expect(screen.getByRole("button", { name: /Step 1: one/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Step 1: two/ })).toBeTruthy();
    expect(screen.getByText("Repository lane 1 of 2")).toBeTruthy();
    expect(screen.getByText("Repository lane 2 of 2")).toBeTruthy();
    expect(screen.getAllByText(/Goon · segment 1 of 1/)).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Collapse all lane details" }));
    expect(screen.queryByRole("button", { name: /Step 1: one/ })).toBeNull();
    expect(screen.getAllByRole("button", { name: "Show lane details" })).toHaveLength(2);
    expect(screen.getByRole("status", { name: "Hidden lane summary for api" })).toHaveTextContent("1 Steps remain available across 1 recorded source segments");
    expect(screen.getByRole("status", { name: "Hidden lane summary for api" })).toHaveTextContent("Details hidden by you");
    expect(screen.getByRole("status", { name: "Hidden lane summary for api" })).toHaveTextContent("1 Passed");
    fireEvent.click(screen.getByRole("button", { name: "Expand all lane details" }));
    expect(screen.getByRole("button", { name: /Step 1: one/ })).toBeTruthy();
  });

  it("renders only canonical collapsed lane ids supplied by the review-state owner", () => {
    const onCollapsedLaneIdsChange = vi.fn();
    const first = lane("repo:api", "api", "PASSED", [flowStep("repo:api", "one", 1, "PASSED", "templates/goons/python/python-quality/goon.yml")]);
    const second = lane("repo:web", "web", "PASSED", [flowStep("repo:web", "two", 1, "PASSED", "templates/goons/general/report/goon.yml")]);
    render(<ControlledGameTapeFlow tape={tape([first, second])} selectedId={null} onSelect={vi.fn()} collapsedLaneIds={["repo:api"]} onCollapsedLaneIdsChange={onCollapsedLaneIdsChange} />);

    expect(screen.queryByRole("button", { name: /Step 1: one/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Step 1: two/ })).toBeTruthy();
    expect(onCollapsedLaneIdsChange).not.toHaveBeenCalled();
  });

  it("reports explicit lane disclosure actions as browser-history checkpoints", () => {
    const onCollapsedLaneIdsChange = vi.fn();
    const first = lane("repo:api", "api", "PASSED", [flowStep("repo:api", "one", 1, "PASSED", "templates/goons/python/python-quality/goon.yml")]);
    const second = lane("repo:web", "web", "PASSED", [flowStep("repo:web", "two", 1, "PASSED", "templates/goons/general/report/goon.yml")]);
    render(<ControlledGameTapeFlow tape={tape([first, second])} selectedId={null} onSelect={vi.fn()} onCollapsedLaneIdsChange={onCollapsedLaneIdsChange} />);

    fireEvent.click(screen.getByRole("button", { name: "Collapse all lane details" }));
    expect(onCollapsedLaneIdsChange).toHaveBeenLastCalledWith(["repo:api", "repo:web"], "push");
    fireEvent.click(screen.getByRole("button", { name: "Expand all lane details" }));
    expect(onCollapsedLaneIdsChange).toHaveBeenLastCalledWith([], "push");
  });

  it("reopens a user-collapsed lane when canonical status navigation selects its Step", () => {
    const onSelect = vi.fn();
    const first = lane("repo:api", "api", "FAILED", [flowStep("repo:api", "failed", 1, "FAILED", "templates/goons/python/python-quality/goon.yml")]);
    const second = lane("repo:web", "web", "PASSED", [flowStep("repo:web", "passed", 1, "PASSED", "templates/goons/general/report/goon.yml")]);
    render(<ControlledGameTapeFlow tape={tape([first, second], "FAILED")} selectedId={null} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: "Collapse all lane details" }));
    fireEvent.click(screen.getByRole("button", { name: "Jump through 1 Failed Steps" }));
    expect(onSelect).toHaveBeenCalledWith("repo:api:step:1");
    expect(screen.getByRole("button", { name: /Step 1: failed/ })).toBeTruthy();
  });

  it("shows exact recorded source provenance for the selected Step", () => {
    const source = "templates/goons/security/a-deliberately-long-recorded-source-path/trivy-audit/goon.yml";
    const repo = lane("repo:api", "api", "PASSED", [flowStep("repo:api", "scan", 1, "PASSED", source)]);
    render(<ControlledGameTapeFlow tape={tape([repo], "PASSED")} selectedId="repo:api:step:1" onSelect={vi.fn()} />);
    const selectedContext = screen.getByRole("status", { name: "Selected Step context" });
    expect(within(selectedContext).getByText(source)).toBeTruthy();
    expect(within(selectedContext).getByText("Recorded source:")).toBeTruthy();
    expect(within(selectedContext).getByText("Repository")).toBeTruthy();
    expect(within(selectedContext).getByText("Source segment")).toBeTruthy();
    expect(within(selectedContext).getByText("Step position")).toBeTruthy();
  });

  it("copies the exact recorded source path without interpreting it", async () => {
    const source = "templates/goons/security/trivy-audit/goon.yml";
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const repo = lane("repo:api", "api", "PASSED", [flowStep("repo:api", "scan", 1, "PASSED", source)]);
    render(<ControlledGameTapeFlow tape={tape([repo], "PASSED")} selectedId="repo:api:step:1" onSelect={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy source path" }));
    expect(writeText).toHaveBeenCalledWith(source);
    expect(await screen.findByRole("button", { name: "Copied" })).toBeTruthy();
  });

  it("delegates exact Step-link copying to the Runs-owned callback", async () => {
    const onCopyStepLink = vi.fn().mockResolvedValue(true);
    const repo = lane("repo:api", "api", "PASSED", [flowStep("repo:api", "scan", 1, "PASSED", "templates/goons/security/trivy-audit/goon.yml")]);
    render(<ControlledGameTapeFlow tape={tape([repo], "PASSED")} selectedId="repo:api:step:1" onSelect={vi.fn()} onCopyStepLink={onCopyStepLink} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy Step link" }));
    expect(onCopyStepLink).toHaveBeenCalledWith("repo:api:step:1");
    expect(await screen.findByRole("button", { name: "Link copied" })).toBeTruthy();
  });

  it("states when the selected resolved Step has no recorded source path", () => {
    const repo = lane("repo:api", "api", "PASSED", [flowStep("repo:api", "resolved", 1, "PASSED", null)]);
    render(<ControlledGameTapeFlow tape={tape([repo], "PASSED")} selectedId="repo:api:step:1" onSelect={vi.fn()} />);
    expect(screen.getByText("No recorded source path")).toBeTruthy();
  });

  it("collapses only canonical PASSED lanes when explicitly requested", () => {
    const passed = lane("repo:passed", "passed", "PASSED", [flowStep("repo:passed", "ok", 1, "PASSED", "templates/goons/python/python-quality/goon.yml")]);
    const failed = lane("repo:failed", "failed", "FAILED", [flowStep("repo:failed", "bad", 1, "FAILED", "templates/goons/security/trivy-audit/goon.yml")]);
    render(<ControlledGameTapeFlow tape={tape([passed, failed], "FAILED")} selectedId={null} onSelect={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Collapse passed lane details" }));
    expect(screen.queryByRole("button", { name: /Step 1: ok/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Step 1: bad/ })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Show lane details" })).toHaveLength(1);
  });

  it("shows a symbol, text badge and status-tinted Step surface so color is never the only meaning", () => {
    const repo = lane("repo:api", "api", "FAILED", [
      flowStep("repo:api", "passed", 1, "PASSED", "templates/goons/a/goon.yml"),
      flowStep("repo:api", "failed", 2, "FAILED", "templates/goons/b/goon.yml"),
    ]);
    const { container } = render(<ControlledGameTapeFlow tape={tape([repo], "FAILED")} selectedId={null} onSelect={vi.fn()} />);
    const passed = screen.getByRole("button", { name: /Step 1: passed/ });
    const failed = screen.getByRole("button", { name: /Step 2: failed/ });
    expect(passed.textContent).toContain("✓");
    expect(passed.textContent).toContain("Passed");
    expect(failed.textContent).toContain("×");
    expect(failed.textContent).toContain("Failed");
    expect(container.querySelector('.game-tape-flowchart__step[data-status="PASSED"]')).toBeTruthy();
    expect(container.querySelector('.game-tape-flowchart__step[data-status="FAILED"]')).toBeTruthy();
  });

  it("marks explicit failure focus without removing canonical Passed Steps", () => {
    const repo = lane("repo:api", "api", "FAILED", [
      flowStep("repo:api", "ok", 1, "PASSED", "templates/goons/a/goon.yml"),
      flowStep("repo:api", "bad", 2, "FAILED", "templates/goons/b/goon.yml"),
    ]);
    const { container } = render(<ControlledGameTapeFlow tape={tape([repo], "FAILED")} selectedId={null} onSelect={vi.fn()} failureFocus />);
    expect(container.querySelector('.game-tape-flowchart[data-failure-focus="true"]')).toBeTruthy();
    expect(screen.getByRole("button", { name: /Step 1: ok/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Step 2: bad/ })).toBeTruthy();
  });

});
