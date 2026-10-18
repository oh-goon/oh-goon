import { describe, expect, it } from "vitest";

import { editSchedulePlanInput, schedulePlanInputValues } from "@/features/schedules/components/schedulePlanInputDraft";

describe("Schedule Plan input draft ownership (M214)", () => {
  const a = "security/standard@a";
  const b = "security/standard@b";

  it("exposes Plan defaults before a user edit without a late initialization write", () => {
    const defaults = { severity: null, scope: "internal" };
    expect(schedulePlanInputValues(a, defaults, null)).toEqual(defaults);
  });

  it("keeps the user edit when the same Plan revision is fetched again", () => {
    const defaults = { severity: null, scope: "internal" };
    const edited = editSchedulePlanInput(a, defaults, null, "severity", "critical");
    expect(schedulePlanInputValues(a, { severity: null, scope: "internal" }, edited)).toEqual({
      severity: "critical", scope: "internal",
    });
  });

  it("invalidates old draft inputs on a new Plan revision", () => {
    const edited = editSchedulePlanInput(a, { severity: null }, null, "severity", "critical");
    expect(schedulePlanInputValues(b, { severity: null }, edited)).toEqual({ severity: null });
    const next = editSchedulePlanInput(b, { severity: null }, edited, "severity", "high");
    expect(next.values).toEqual({ severity: "high" });
  });

  it("cannot borrow inputs from another selected Plan even with the same input names", () => {
    const otherPlan = "security/alternate@a";
    const edited = editSchedulePlanInput(a, { severity: null }, null, "severity", "critical");
    expect(schedulePlanInputValues(otherPlan, { severity: null }, edited)).toEqual({ severity: null });
  });

  it("updates only the edited field and preserves other defaults and explicit clearing", () => {
    const defaults = { severity: null, scope: "internal" };
    const first = editSchedulePlanInput(a, defaults, null, "severity", "critical");
    const cleared = editSchedulePlanInput(a, defaults, first, "severity", null);
    expect(cleared.values).toEqual({ severity: null, scope: "internal" });
    expect(first.values.severity).toBe("critical");
  });
});
