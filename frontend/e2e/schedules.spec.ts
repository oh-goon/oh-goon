import { expect, test } from "@playwright/test";

import { LONG_PLAN_NAME, LONG_REPOSITORY_NAME } from "./fixtures/identities";
import { openFixture } from "./helpers/navigation";

test("Schedule creation and editing use human frequency choices", async ({ page }) => {
  await openFixture(page, "schedules");
  await page.getByRole("button", { name: "New Schedule" }).click();
  await page.getByLabel("Display name").fill("Weekly production quality review");
  await page.getByLabel("Plan").selectOption("python-production-quality-security");
  const repositories = page.getByRole("group", { name: "Repositories available for this run", exact: true });
  await repositories.getByRole("checkbox", { name: new RegExp(LONG_REPOSITORY_NAME.slice(0, 40)) }).check();
  await expect(page.getByText(LONG_REPOSITORY_NAME, { exact: false })).toBeVisible();
  await expect(page.getByText(LONG_PLAN_NAME, { exact: false })).toBeVisible();
  await page.getByLabel("Frequency").selectOption("weekly");
  await page.getByRole("button", { name: "Create Schedule" }).click();
  await expect(page.getByText("Action accepted")).toBeVisible();
  await expect(page.getByText("Weekly production quality review")).toBeVisible();

  const card = page.locator(".schedule-card").filter({ hasText: "Weekly production quality review" });
  await card.locator("summary", { hasText: "Manage schedule" }).click();
  await card.getByRole("button", { name: "Edit" }).click();
  await card.getByLabel("Frequency").selectOption("custom");
  await card.getByLabel("Interval (minutes)").fill("90");
  await card.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Action accepted")).toBeVisible();
});
