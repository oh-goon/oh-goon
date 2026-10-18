import { expect, test } from "@playwright/test";

import { expectNoAutomaticAccessibilityViolations } from "./helpers/accessibility";
import { LONG_REPOSITORY_NAME } from "./fixtures/identities";
import { openFixture } from "./helpers/navigation";
import { gameTapeRegion, runHistoryRegion, savedPlanSelect } from "./helpers/locators";

for (const route of ["overview", "repositories", "shared-runs", "schedules", "runs", "dependencies"] as const) {
  test(`${route} has no automatic WCAG A/AA violations`, async ({ page }) => {
    await openFixture(page, route);
    await expectNoAutomaticAccessibilityViolations(page);
  });
}

test("keyboard navigation exposes Skip to content and a visible route focus target", async ({ page }) => {
  await openFixture(page, "repositories");
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeVisible();
  await skip.press("Enter");
  await expect(page).toHaveURL(/#main-content$/);
  await page.locator(".app-nav").getByRole("button", { name: "Runs", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Runs" })).toBeFocused();
});


test("execution grid is keyboard-operable and preserves canonical drill-down", async ({ page }) => {
  await openFixture(page, "overview");
  const grid = page.getByRole("region", { name: "Execution grid", exact: true });
  const cell = grid.locator("button.execution-timeline__cell").first();
  await cell.focus();
  await expect(cell).toBeFocused();
  await cell.press("Enter");
  await expect(page).toHaveURL(/#runs\?.*repository=.*history_from=.*history_to=/);
  const history = runHistoryRegion(page);
  await expect(gameTapeRegion(page)).toHaveCount(0);
  const run = history.locator(".history-row").first();
  await run.focus();
  await expect(run).toBeFocused();
  await run.press("Enter");
  await expect(page).toHaveURL(/#runs\?.*run=.*repository=.*history_from=.*history_to=/);
  await expect(gameTapeRegion(page)).toBeVisible();
});
test("selected, checked and blocked interaction states keep automatic WCAG coverage", async ({ page }) => {
  await openFixture(page, "shared-runs");
  await savedPlanSelect(page).selectOption("python-quality");
  await page.getByRole("checkbox", { name: new RegExp(LONG_REPOSITORY_NAME.slice(0, 40)) }).check();
  await expect(page.locator(".backend-verification--ready")).toBeVisible();
  await page.getByRole("button", { name: /Run on 1/ }).hover();
  await expectNoAutomaticAccessibilityViolations(page);

  await openFixture(page, "repositories");
  await page.getByRole("button", { name: "Run CI/CD" }).first().click();
  await expectNoAutomaticAccessibilityViolations(page);
});


test("dependency evidence drill-down keeps automatic WCAG coverage", async ({ page }) => {
  await openFixture(page, "dependencies");
  await page.getByLabel("Dependency", { exact: true }).fill("mlflow");
  await page.getByRole("button", { name: "Search" }).click();
  await page.getByRole("button", { name: /mlflow/i }).click();
  await page.getByRole("button", { name: /No current dependency evidence/ }).first().click();
  await expect(page.getByRole("heading", { name: "Repositories needing current dependency evidence" })).toBeVisible();
  await expectNoAutomaticAccessibilityViolations(page);
});
