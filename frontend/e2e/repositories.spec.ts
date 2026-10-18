import { expect, test } from "@playwright/test";

import { LONG_REPOSITORY_NAME } from "./fixtures/identities";
import { openFixture } from "./helpers/navigation";

test("repository-owned CI/CD is reachable from the browser", async ({ page }) => {
  await openFixture(page, "repositories");
  await expect(page.locator(".league-map-panel").getByText(LONG_REPOSITORY_NAME, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Run CI/CD" }).first().click();
  await expect(page.getByRole("heading", { name: "Current run" })).toBeVisible();
  await expect(page.getByText(/New run actions stay disabled/)).toBeVisible();
  await page.getByRole("button", { name: "Open in Runs" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Runs" })).toBeVisible();
});

test("large repository search can recover from no matches", async ({ page }) => {
  await openFixture(page, "repositories");
  await page.getByLabel("Find repository").fill("no-such-repository");
  await expect(page.getByText("No repositories match this search.")).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page.locator(".league-map-panel").getByText(LONG_REPOSITORY_NAME, { exact: true })).toBeVisible();
});
