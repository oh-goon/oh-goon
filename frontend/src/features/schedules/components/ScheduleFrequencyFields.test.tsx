import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ScheduleFrequencyFields } from "@/features/schedules/components/ScheduleFrequencyFields";

describe("ScheduleFrequencyFields", () => {
  it("keeps raw minutes behind the explicit Custom choice", () => {
    const onFrequencyChange = vi.fn();
    render(<ScheduleFrequencyFields
      frequency="daily"
      customInterval="90"
      frequencyLabelKey="schedules.edit.frequency"
      intervalLabelKey="schedules.edit.interval"
      onFrequencyChange={onFrequencyChange}
      onCustomIntervalChange={vi.fn()}
    />);

    expect(screen.queryByLabelText("Interval (minutes)")).toBeNull();
    fireEvent.change(screen.getByLabelText("Frequency"), { target: { value: "custom" } });
    expect(onFrequencyChange).toHaveBeenCalledWith("custom");
  });
});
