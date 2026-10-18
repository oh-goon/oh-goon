import { expect, test } from "@playwright/test";

import { openFixture } from "./helpers/navigation";
import { savedPlanSelect } from "./helpers/locators";

/** Browser presentation, not backend semantics or APP PASS. Native browser run required. */
test("T6 Plan input error → correction removes stale diagnostic without launching work", async ({ page }) => {
  await openFixture(page, "shared-runs");
  await savedPlanSelect(page).selectOption("python-quality");
  const value = page.getByLabel(/coverage_threshold/);
  await expect(value).toHaveValue("70");
  await value.fill("101");
  const diagnostic = page.getByTestId("plan-input-diagnostic");
  await expect(diagnostic).toContainText("INVALID_INPUT_VALUE");
  await expect(diagnostic).toContainText("coverage_threshold");
  await expect(diagnostic).not.toContainText("101");
  await expect(diagnostic).toContainText("No execution was started");
  await value.fill("85");
  await expect(diagnostic).toHaveCount(0);
  await expect(page.locator(".shared-plan-run-accepted")).toHaveCount(0);
});
