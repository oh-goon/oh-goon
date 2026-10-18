import { type ComponentProps, useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";

import type { GameTapeLiveOutputBatch } from "@generated/models";
import { GameTapeLiveLogs } from "@/features/game-tape/components/GameTapeLiveLogs";

type LiveLogsProps = ComponentProps<typeof GameTapeLiveLogs>;

function ControlledLiveLogs({ query: initialQuery = "", stream: initialStream = "all", pageFromEnd: initialPageFromEnd = 0, ...props }: LiveLogsProps) {
  const [query, setQuery] = useState(initialQuery);
  const [stream, setStream] = useState(initialStream);
  const [pageFromEnd, setPageFromEnd] = useState(initialPageFromEnd);
  return <GameTapeLiveLogs
    {...props}
    query={query}
    stream={stream}
    pageFromEnd={pageFromEnd}
    onQueryChange={(value) => { setQuery(value); props.onQueryChange?.(value); }}
    onStreamChange={(value) => { setStream(value); props.onStreamChange?.(value); }}
    onPageChange={(value) => { setPageFromEnd(value); props.onPageChange?.(value); }}
  />;
}

function batch(count = 250): GameTapeLiveOutputBatch {
  return {
    fleet_run_id: "run-logs",
    state: "SEALED",
    available: true,
    next_sequence: count + 1,
    retained_from_sequence: 1,
    dropped_chunk_count: 0,
    expires_at: "2026-08-22T10:00:00Z",
    failure_navigation_state: "AVAILABLE",
    chunks: Array.from({ length: count }, (_, index) => ({
      sequence: index + 1,
      repository_id: "repo-1",
      step_id: "pytest",
      attempt: 1,
      stream: index % 2 === 0 ? "stdout" : "stderr",
      line_start: index + 1,
      line_end: index + 1,
      text: index === 9 ? "needle-old canonical output" : `chunk ${index + 1}`,
      emitted_at: "2026-08-22T08:00:00Z",
    })),
    failure_anchors: [],
  } as unknown as GameTapeLiveOutputBatch;
}

describe("GameTapeLiveLogs bounded review", () => {
  it("renders at most 120 chunks and pages through all loaded matches", async () => {
    const { container } = render(<ControlledLiveLogs batch={batch()} repositoryId="repo-1" stepId="pytest" />);
    expect(container.querySelectorAll(".game-tape-live-line")).toHaveLength(120);
    expect(screen.getByText("Showing 120 of 250 chunks")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show 120 earlier chunks" }));
    expect(container.querySelectorAll(".game-tape-live-line")).toHaveLength(120);
    const newer = screen.getByRole("button", { name: "Show 120 newer chunks" });
    expect(newer).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show 10 earlier chunks" }));
    expect(container.querySelectorAll(".game-tape-live-line")).toHaveLength(10);
    expect(screen.getByText("Showing 10 of 250 chunks")).toBeTruthy();
  });

  it("searches the entire loaded Step output even when the match is outside the initial render window", async () => {
    const { container } = render(<ControlledLiveLogs batch={batch()} repositoryId="repo-1" stepId="pytest" />);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "needle-old" } });
    expect(screen.getByText(/needle-old canonical output/)).toBeTruthy();
    expect(container.querySelectorAll(".game-tape-live-line")).toHaveLength(1);
    expect(screen.getByText("Showing 1 of 1 chunks")).toBeTruthy();
  });

  it("opens the bounded page containing the canonical failure anchor before focusing it", async () => {
    const { container } = render(<ControlledLiveLogs batch={batch()} repositoryId="repo-1" stepId="pytest" focusSequence={10} />);
    await waitFor(() => expect(container.querySelector('[data-sequence="10"]')).toBeTruthy());
    expect(container.querySelectorAll(".game-tape-live-line").length).toBeLessThanOrEqual(120);
  });

  it("copies only the currently rendered bounded window", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<ControlledLiveLogs batch={batch()} repositoryId="repo-1" stepId="pytest" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy visible output" }));
    expect(await screen.findByRole("button", { name: "Copied" })).toBeTruthy();
    expect(writeText).toHaveBeenCalledOnce();
    const firstCall = writeText.mock.calls.at(0);
    if (!firstCall) throw new Error("Expected clipboard write");
    const copied = String(firstCall[0]);
    expect(copied.split("\n")).toHaveLength(120);
    expect(copied).toContain("chunk 250");
    expect(copied).not.toContain("chunk 1\n");
  });
  it("restores an explicit bounded window without auto-jumping a failure anchor", () => {
    const pages: number[] = [];
    const { container } = render(<ControlledLiveLogs batch={batch()} repositoryId="repo-1" stepId="pytest" focusSequence={10} pageFromEnd={1} onPageChange={(page) => pages.push(page)} />);
    expect(container.querySelectorAll(".game-tape-live-line")).toHaveLength(120);
    expect(container.querySelector('[data-sequence="10"]')).toBeNull();
    expect(pages).not.toContain(0);
  });

  it("does not attach a late copy completion to a newly selected Step", async () => {
    let resolveCopy: () => void = () => {};
    const writeText = vi.fn(() => new Promise<void>((resolve) => { resolveCopy = resolve; }));
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const { rerender } = render(<GameTapeLiveLogs batch={batch()} repositoryId="repo-1" stepId="pytest" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy visible output" }));
    rerender(<GameTapeLiveLogs batch={batch()} repositoryId="repo-1" stepId="other-step" />);
    resolveCopy();
    await Promise.resolve();
    expect(screen.queryByRole("button", { name: "Copied" })).toBeNull();
  });

});
