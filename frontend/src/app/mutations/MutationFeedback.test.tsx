import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MutationFeedback } from "@/app/mutations/MutationFeedback";

describe("MutationFeedback", () => {
  it("acknowledges a saved action without claiming execution completion", () => {
    const onDismiss = vi.fn();
    render(<MutationFeedback result={{ outcome: "saved", value: {}, causalityRevision: 2 }} onDismiss={onDismiss} />);

    expect(screen.getByText("Action accepted")).toBeTruthy();
    expect(screen.getByText(/backend source of truth/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});
