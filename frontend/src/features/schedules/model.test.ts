import { describe, expect, it } from "vitest";

import {
  scheduleFrequencyMinutes,
  scheduleFrequencyPreset,
  scheduleKeyFromName,
} from "@/features/schedules/model";

describe("schedule presentation model", () => {
  it("derives a visible stable key from a human display name", () => {
    expect(scheduleKeyFromName(" Nightly Security / Backend ")).toBe("nightly-security-backend");
    expect(scheduleKeyFromName("Nattlig kvalitet")).toBe("nattlig-kvalitet");
  });

  it("recognizes the human frequency presets without changing backend minutes", () => {
    expect(scheduleFrequencyPreset(60)).toBe("hourly");
    expect(scheduleFrequencyPreset(1440)).toBe("daily");
    expect(scheduleFrequencyPreset(90)).toBe("custom");
  });

  it("maps friendly create/edit choices back to the exact backend interval", () => {
    expect(scheduleFrequencyMinutes("sixHours", "999")).toBe(360);
    expect(scheduleFrequencyMinutes("custom", "90")).toBe(90);
  });
});
