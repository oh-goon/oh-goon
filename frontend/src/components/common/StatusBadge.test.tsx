import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "@/components/common/StatusBadge";

describe("StatusBadge", () => {
  it("renders its label", () => {
    render(<StatusBadge label="READY" tone="success" />);
    expect(screen.getByText("READY")).toBeTruthy();
  });
});
