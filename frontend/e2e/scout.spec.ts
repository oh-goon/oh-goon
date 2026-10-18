import { expect, test } from "@playwright/test";

import { LONG_GOON_NAME, LONG_PLAN_NAME, LONG_REPOSITORY_NAME, LONG_STEP_NAME } from "./fixtures/identities";
import { openFixture } from "./helpers/navigation";

test("Scout overview stays simple and drills from Heavy Hitters to Game Tape", async ({ page }) => {
  await openFixture(page, "scout");
  await expect(page.getByRole("heading", { level: 1, name: "The Scout" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Repository activity", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Needs Scouting" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Heavy Hitters" })).toBeVisible();

  await page.getByRole("button", { name: new RegExp(LONG_REPOSITORY_NAME.slice(0, 32)) }).first().click();
  await expect(page).toHaveURL(/#scout\?.*view=repository/);
  await expect(page.getByRole("heading", { name: LONG_REPOSITORY_NAME })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Plans" })).toBeVisible();
  await expect(page.getByText(LONG_STEP_NAME, { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Open in Game Tape →" }).first().click();
  await expect(page.getByRole("heading", { level: 1, name: "Runs" })).toBeVisible();
  await page.goBack();
  await expect(page.getByRole("heading", { level: 1, name: "The Scout" })).toBeVisible();
  await expect(page).toHaveURL(/#scout\?.*view=repository/);
});

test("Scout finding exposes comparison evidence before handoff", async ({ page }) => {
  await openFixture(page, "scout");
  await page.getByRole("button", { name: /CPU Outliers/ }).click();
  await expect(page).toHaveURL(/category=CPU_OUTLIER/);
  await expect(page.getByText(/Median CPU\/run is 2\.9× the peer median/)).toBeVisible();
  const evidenceDetails = page.locator("details", { hasText: "Evidence details" }).first();
  await evidenceDetails.getByText("Evidence details").click();
  await expect(evidenceDetails.getByText("Comparable executions", { exact: true })).toBeVisible();
  await expect(evidenceDetails.getByText("Telemetry coverage", { exact: true })).toBeVisible();
  await page.getByText("Comparable environment").click();
  await expect(page.getByText("Enforced resource envelope")).toBeVisible();
  await expect(page.getByText("Isolation backend")).toBeVisible();
});

test("Scout Goon-first navigation is paginated and drillable", async ({ page }) => {
  await openFixture(page, "scout");
  await page.getByRole("button", { name: "Goon Leaders →" }).click();
  await expect(page).toHaveURL(/view=goon-leaders/);
  await expect(page.getByText("1–20 of 25")).toBeVisible();
  await page.getByRole("button", { name: "Next →" }).click();
  await expect(page).toHaveURL(/offset=20/);
  await expect(page.getByText("21–25 of 25")).toBeVisible();
  await page.getByRole("button", { name: new RegExp(`${LONG_GOON_NAME}-21`) }).click();
  await expect(page).toHaveURL(/view=goon/);
  await expect(page.getByRole("heading", { name: new RegExp(LONG_GOON_NAME.slice(0, 28)) })).toBeVisible();
});

test("Scout search reaches Repository, Plan and Goon identities", async ({ page }) => {
  await openFixture(page, "scout");
  const search = page.getByLabel("Search Scout");

  await search.fill(LONG_PLAN_NAME.slice(0, 24));
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByText(LONG_PLAN_NAME, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: new RegExp(LONG_PLAN_NAME.slice(0, 32)) }).click();
  await expect(page.getByRole("heading", { name: LONG_PLAN_NAME })).toBeVisible();
  const repositoriesPanel = page.getByRole("heading", { name: "Repositories" }).locator("xpath=ancestor::section[contains(@class,'panel')][1]");
  await expect(repositoriesPanel.getByText("1–1 of 5000")).toBeVisible();
  await repositoriesPanel.getByRole("button", { name: "Next →" }).click();
  await expect(page).toHaveURL(/section=repositories.*offset=100/);
  await expect(page.getByText("service-00100", { exact: true })).toBeVisible();
  await expect(repositoriesPanel.getByText("101–200 of 5000")).toBeVisible();

  await page.getByLabel("Search Scout").fill(LONG_GOON_NAME.slice(0, 24));
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByText(LONG_GOON_NAME, { exact: true })).toBeVisible();

  await page.getByLabel("Search Scout").fill(LONG_REPOSITORY_NAME.slice(0, 24));
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByText(LONG_REPOSITORY_NAME, { exact: true })).toBeVisible();
});

test("Scout custom window is URL durable", async ({ page }) => {
  await openFixture(page, "scout");
  await page.getByRole("button", { name: "Custom…" }).click();
  await page.getByLabel("Custom hours (1–2160)").fill("48");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page).toHaveURL(/hours=48/);
  await page.reload();
  await expect(page.getByLabel("Time window")).toHaveValue("custom-current");
  await expect(page.getByRole("option", { name: "Custom: 48h" })).toBeAttached();
});


test("Scout Heavy Hitters browse-all path reaches findings after the overview bound", async ({ page }) => {
  await openFixture(page, "scout", "scale");
  await page.getByRole("button", { name: "Heavy Hitters →" }).click();
  await expect(page).toHaveURL(/view=findings.*category=HEAVY_HITTER/);
  await expect(page.getByText("1–20 of 25")).toBeVisible();
  await page.getByRole("button", { name: "Next →" }).click();
  await expect(page).toHaveURL(/offset=20/);
  await expect(page.getByText("21–25 of 25")).toBeVisible();
  await expect(page.getByText("service-00020", { exact: true })).toBeVisible();
});
