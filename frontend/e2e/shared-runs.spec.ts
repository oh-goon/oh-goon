import { expect, test } from "@playwright/test";

import { LONG_REPOSITORY_NAME } from "./fixtures/identities";
import { SHARED_RUN_SUBMIT_RELEASE_EVENT } from "./fixtures/scenarios";
import { expectExplicitRunRoute, openFixture } from "./helpers/navigation";
import { currentRunList, currentRunSummary, gameTapeRegion, savedPlanSelect } from "./helpers/locators";

test("New shared run starts from an explicit saved Plan before repository selection", async ({ page }) => {
  await openFixture(page, "shared-runs");
  await expect(page.getByRole("group", { name: "Selected Plan", exact: true })).toBeHidden();
  await expect(page.locator(".repository-selector")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Run on/ })).toHaveCount(0);

  await savedPlanSelect(page).selectOption("python-quality");

  await expect(page.locator(".shared-plan-selected")).toContainText("python-quality");
  await expect(page.getByLabel(/coverage_threshold/)).toHaveValue("70");
  const lock = page.locator(".shared-plan-lock-preview");
  await expect(lock.getByText("goon.lock", { exact: true })).toBeVisible();
  await lock.getByText("goon.lock", { exact: true }).click();
  await expect(lock).toContainText("lock_version: 1");
  await expect(page.locator(".repository-selector")).toBeVisible();
  await page.getByRole("button", { name: "Select all 7" }).click();
  await expect(page.locator(".repository-selector")).toContainText("7 selected · 7 total");
  await expect(page.getByRole("button", { name: /Run on 7/ })).toBeEnabled();
});


test("Plan authoring is owned by Plans instead of the Shared Run workspace", async ({ page }) => {
  await openFixture(page, "shared-runs");
  await expect(page.getByLabel("plan.yml content")).toHaveCount(0);
  await page.getByRole("button", { name: "Manage Plans" }).click();
  await expect(page).toHaveURL(/#plans/);
  await expect(page.getByRole("heading", { name: "Plan Builder" })).toBeVisible();
  await expect(page.getByLabel("plan.yml content")).toHaveCount(0);
  await page.getByLabel("Saved Plan").selectOption("python-quality");
  await expect(page.getByLabel("plan.yml content")).toBeVisible();
});

test("Plan-template creation is owned by Plans, not the Shared Run workspace", async ({ page }) => {
  await openFixture(page, "shared-runs", "empty");
  await expect(page.getByText("Create Plan from template", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Manage Plans" }).click();
  const disclosure = page.getByText("Create Plan from template", { exact: true });
  await expect(disclosure).toBeVisible();
  await disclosure.click();
  await page.getByLabel("Plan template").selectOption("python/quality");
  await expect(page.getByRole("button", { name: "Create and select Plan" })).toBeEnabled();
});

test("Plan-template creation refuses to overwrite a differently revised saved Plan", async ({ page }) => {
  await openFixture(page, "plans");
  const disclosure = page.getByText("Create Plan from template", { exact: true });
  await disclosure.click();
  await page.getByLabel("Plan template").selectOption("python/quality");
  await expect(page.getByRole("button", { name: "Create and select Plan" })).toBeDisabled();
});

test("New run follows Plan → inputs → repositories → verification → result overview → Game Tape", async ({ page }) => {
  await openFixture(page, "shared-runs");
  await savedPlanSelect(page).selectOption("python-quality");
  await page.getByLabel(/coverage_threshold/).fill("85");
  await page.getByRole("checkbox", { name: new RegExp(LONG_REPOSITORY_NAME.slice(0, 40)) }).check();
  await expect(page.locator(".backend-verification--ready")).toBeVisible();
  await page.getByRole("button", { name: /Run on 1/ }).click();

  await expect(page.locator(".shared-plan-run-accepted")).toContainText("Run accepted");
  await expect(page).toHaveURL(/#shared-runs/);
  await expect(page.getByRole("heading", { name: "Run result" })).toBeVisible();
  const summary = currentRunSummary(page);
  const resultList = currentRunList(page);
  await expect(summary).toBeVisible();
  await expect(resultList).toBeVisible();
  await expect(summary.getByRole("button", { name: /1 Passed/ })).toBeVisible();
  await expect(summary.getByRole("button", { name: /Failed/ })).toBeVisible();
  await expect(summary.getByRole("button", { name: /Needs attention/ })).toHaveCount(0);
  await expect(summary.getByRole("button", { name: /Remaining/ })).toHaveCount(0);
  await expect(resultList.getByRole("button", { name: /1 Passed/ })).toHaveCount(0);
  await summary.getByRole("button", { name: /1 Passed/ }).click();
  await expect(page.getByRole("button", { name: /Show all 1 repositories/ })).toBeVisible();
  await expect(resultList.getByRole("listitem")).toContainText(LONG_REPOSITORY_NAME);
  await resultList.getByRole("listitem").getByRole("button", { name: "Open run" }).click();
  await expectExplicitRunRoute(page, { repository: true });
  await expect(page.getByText("Step inspector", { exact: true })).toBeVisible();
  await expect(page.getByRole("status", { name: "Selected Step context" })).toContainText(LONG_REPOSITORY_NAME);
  await page.goBack();
  await expect(page).toHaveURL(/#shared-runs/);
  await expect(page.locator(".shared-plan-selected")).toHaveCount(0);
  await expect(page.locator(".repository-selector")).toHaveCount(0);
});

test("Latest Shared Run opens its exact opaque Run identity in Runs", async ({ page }) => {
  await openFixture(page, "shared-runs");
  await savedPlanSelect(page).selectOption("python-quality");
  await page.getByRole("checkbox", { name: new RegExp(LONG_REPOSITORY_NAME.slice(0, 40)) }).check();
  await expect(page.locator(".backend-verification--ready")).toBeVisible();
  await page.getByRole("button", { name: /Run on 1/ }).click();

  await expect(page.getByRole("heading", { name: "Run result" })).toBeVisible();
  await page.getByRole("button", { name: "Open in Runs" }).click();
  await expectExplicitRunRoute(page);
  await expect(gameTapeRegion(page)).toBeVisible();
});

test("failed Shared Run stays summarized and drills directly into failure Game Tape", async ({ page }) => {
  await openFixture(page, "shared-runs", "failed-run");
  await savedPlanSelect(page).selectOption("python-quality");
  await page.getByRole("checkbox", { name: new RegExp(LONG_REPOSITORY_NAME.slice(0, 40)) }).check();
  await expect(page.locator(".backend-verification--ready")).toBeVisible();
  await page.getByRole("button", { name: /Run on 1/ }).click();

  await expect(page.getByRole("heading", { name: "Run result" })).toBeVisible();
  const summary = currentRunSummary(page);
  const resultList = currentRunList(page);
  await expect(summary.getByRole("button", { name: /1 Failed/ })).toBeVisible();
  await expect(resultList.getByRole("button", { name: /1 Failed/ })).toHaveCount(0);
  await summary.getByRole("button", { name: /1 Failed/ }).click();
  await expect(resultList.getByRole("listitem")).toContainText(LONG_REPOSITORY_NAME);
  await resultList.getByRole("listitem").getByRole("button", { name: "Open run" }).click();
  await expectExplicitRunRoute(page, { repository: true });
  await expect(gameTapeRegion(page)).toBeVisible();
  await expect(page.getByText("Step inspector", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Open failure evidence" }).click();
  await expect(page).toHaveURL(/view=logs/);
  const stepSummary = page.getByRole("region", { name: "Step summary", exact: true });
  await expect(stepSummary).toBeVisible();
  await expect(stepSummary).toContainText(LONG_REPOSITORY_NAME);
  await expect(stepSummary).toContainText("Failed");
  await page.goBack();
  await expectExplicitRunRoute(page, { repository: true });
  await page.goBack();
  await expect(page).toHaveURL(/#shared-runs/);
  await expect(page.locator(".shared-plan-selected")).toHaveCount(0);
  await expect(page.locator(".repository-selector")).toHaveCount(0);
});

test("New run keeps investigation history out of the creation surface", async ({ page }) => {
  await openFixture(page, "shared-runs");
  await savedPlanSelect(page).selectOption("python-production-quality-security");
  await page.getByText("Advanced: saved selections").click();
  await expect(page.getByText("Previous runs", { exact: false })).toHaveCount(0);
  await expect(gameTapeRegion(page)).toHaveCount(0);
});

test("backend blockers keep Run actionable and explain why nothing started", async ({ page }) => {
  await openFixture(page, "shared-runs", "blocked");
  await savedPlanSelect(page).selectOption("python-quality");
  const repositories = page.locator(".repository-selector");
  await repositories.getByRole("checkbox", { name: new RegExp(LONG_REPOSITORY_NAME.slice(0, 40)) }).check();
  await repositories.getByRole("checkbox", { name: /customer-portal-web/ }).check();

  const verification = page.locator(".backend-verification--blocked");
  await expect(verification).toBeVisible();
  await expect(verification.locator(".backend-verification__problems li")).toHaveCount(1);
  const run = page.getByRole("button", { name: /Run on 2/ });
  await expect(run).toBeEnabled();
  await run.click();

  await expect(page.getByRole("alert")).toContainText("The run did not start");
  await expect(page.getByRole("alert")).toContainText("Nothing was executed");
  const problems = page.locator(".backend-verification__problems");
  await expect(problems).toHaveAttribute("open", "");
  await problems.locator("summary").click();
  await expect(problems).not.toHaveAttribute("open", "");
  await run.click();
  await expect(problems).toHaveAttribute("open", "");
  const blocker = problems.locator("li").filter({ hasText: LONG_REPOSITORY_NAME.slice(0, 40) });
  await expect(blocker).toContainText("goon.lock does not match the resolved execution identity");
  await blocker.getByRole("button", { name: "Remove from this run" }).click();

  await expect(page.locator(".backend-verification--ready")).toBeVisible();
  await expect(page.locator(".backend-verification__problems li")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Run on 1/ })).toBeEnabled();
});



test("backend Shared Run rejection is never a silent no-op", async ({ page }) => {
  await openFixture(page, "shared-runs", "write-error-submit");
  await savedPlanSelect(page).selectOption("python-quality");
  await page.getByRole("checkbox", { name: new RegExp(LONG_REPOSITORY_NAME.slice(0, 40)) }).check();
  await expect(page.locator(".backend-verification--ready")).toBeVisible();
  await page.getByRole("button", { name: /Run on 1/ }).click();

  const rejection = page.locator(".shared-plan-run-failure").filter({ hasText: "The run could not start" });
  await expect(rejection).toBeVisible();
  await expect(rejection).toContainText("Nothing was started");
  await expect(rejection).toContainText("fixture-shared-run-rejected");
  await expect(page.getByRole("heading", { name: "Run result" })).toHaveCount(0);
});

test("shared-run submission disables the action and stays in the run workspace", async ({ page }) => {
  await openFixture(page, "shared-runs", "slow-submit");
  await savedPlanSelect(page).selectOption("python-quality");
  await page.getByRole("checkbox", { name: new RegExp(LONG_REPOSITORY_NAME.slice(0, 40)) }).check();
  await expect(page.locator(".backend-verification--ready")).toBeVisible();
  await page.getByRole("button", { name: /Run on 1/ }).click();
  const submitting = page.getByRole("button", { name: "Submitting…" });
  await expect(submitting).toBeDisabled();
  await expect(page.getByText("Submitting the exact verified selection…")).toBeVisible();
  await expect(page).toHaveURL(/#shared-runs/);
  await page.evaluate((eventName) => window.dispatchEvent(new Event(eventName)), SHARED_RUN_SUBMIT_RELEASE_EVENT);
  await expect(page.getByRole("heading", { name: "Run result" })).toBeVisible();
});
