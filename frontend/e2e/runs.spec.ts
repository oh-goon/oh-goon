import { expect, test } from "@playwright/test";

import { LONG_GOON_NAME, LONG_PLAN_NAME, LONG_REPOSITORY_NAME, LONG_STEP_NAME } from "./fixtures/identities";
import { DEFAULT_RUN_ID, FAILED_RUN_ID } from "./fixtures/runIds";
import { openFixture, openRunFixture } from "./helpers/navigation";
import { gameTapeFlow, gameTapeFlowSearch, gameTapeInspector, gameTapeRegion, runHistoryRegion } from "./helpers/locators";

test("Run configuration explanation exposes captured provenance through a read-only disclosure", async ({ page }) => {
  await openRunFixture(page);
  const tape = gameTapeRegion(page);
  await tape.getByRole("button", { name: "Overview", exact: true }).click();
  await tape.locator("summary", { hasText: "Configuration explanation" }).click();
  const explanation = tape.locator("details", { has: page.locator("summary", { hasText: "Configuration explanation" }) });
  await expect(explanation.getByText("<root>.input.project", { exact: true })).toBeVisible();
  await expect(explanation.getByText("Owning Goon default", { exact: false })).toBeVisible();
  await expect(explanation.getByText(/Values are withheld/)).toBeVisible();
  await expect(explanation.getByRole("button")).toHaveCount(0);
  await page.setViewportSize({ width: 375, height: 800 });
  await expect(explanation.getByText("<root>.input.project", { exact: true })).toBeVisible();
  expect(await explanation.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
});

test("Runs opens as history without implicitly selecting the latest Run", async ({ page }) => {
  await openFixture(page, "runs");
  await expect(runHistoryRegion(page)).toBeVisible();
  await expect(gameTapeRegion(page)).toHaveCount(0);
  await expect(page.getByLabel("Loaded runs")).toHaveCount(0);
  await expect(page).not.toHaveURL(/(?:[?&])run=/);
});

test("history-only Runs canonicalizes away hidden Run-review state", async ({ page }) => {
  await page.goto("/e2e/fixture.html?scenario=default#runs?view=logs&focus=failures&log_q=warning&raw=1&history_q=failed");
  await page.locator("main h1").waitFor({ state: "visible" });

  await expect(page).toHaveURL(/#runs\?history_q=failed$/);
  await expect(runHistoryRegion(page)).toBeVisible();
  await expect(gameTapeRegion(page)).toHaveCount(0);
});

test("Run-again mutation hands review ownership to the accepted canonical child Run", async ({ page }) => {
  await openRunFixture(page);
  await expect(page).toHaveURL(new RegExp(`run=${DEFAULT_RUN_ID}`));

  await runHistoryRegion(page).getByRole("button", { name: /^Run .* again$/ }).click();

  await expect(page).not.toHaveURL(new RegExp(`run=${DEFAULT_RUN_ID}(?:&|$)`));
  await expect(page).toHaveURL(/run=fleet-[0-9a-f]{32}/);
  await expect(page).toHaveURL(/view=summary/);
  await expect(gameTapeRegion(page).getByRole("button", { name: "Overview" })).toHaveAttribute("aria-pressed", "true");
  await expect(page).not.toHaveURL(/(?:[?&])(step|focus|baseline|log_q|log_stream|raw|tech|flow_q|fold|segment)=/);
});

test("Run picker changes the canonical Run identity and starts a fresh review context", async ({ page }) => {
  await openRunFixture(page);
  await gameTapeRegion(page).getByRole("button", { name: "Logs", exact: true }).click();
  await expect(page).toHaveURL(/view=logs/);

  await page.getByLabel("Loaded runs").selectOption(FAILED_RUN_ID);

  await expect(page).toHaveURL(new RegExp(`run=${FAILED_RUN_ID}`));
  await expect(page).toHaveURL(/view=summary/);
  await expect(page).not.toHaveURL(/(?:[?&])(step|focus|log_q|log_stream|raw|tech|flow_q|fold|segment)=/);
  await expect(gameTapeRegion(page).getByRole("button", { name: "Overview" })).toHaveAttribute("aria-pressed", "true");
});

test("Run review leads with a navigable Plan → repository → Goon → Step → outcome CI/CD Flow", async ({ page }) => {
  await openRunFixture(page);
  await expect(gameTapeRegion(page).getByRole("button", { name: "Steps", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(gameTapeFlow(page).getByText(LONG_PLAN_NAME, { exact: true })).toBeVisible();
  await expect(gameTapeFlow(page).getByText("2 repositories · 8 planned Steps", { exact: false })).toBeVisible();
  await expect(gameTapeRegion(page).getByText("Recorded duration: 1m 42s").first()).toBeVisible();
  const repositoryScope = gameTapeFlow(page).getByRole("group", { name: "Repository scope junction", exact: true });
  await expect(repositoryScope).toContainText("2 repository lanes");
  await expect(repositoryScope).toContainText("No cross-repository execution order is asserted by this view.");
  await expect(gameTapeFlow(page).getByText(LONG_GOON_NAME, { exact: true }).first()).toBeVisible();
  await expect(gameTapeRegion(page).getByText(LONG_STEP_NAME, { exact: true }).first()).toBeVisible();
  const laneNavigation = gameTapeFlow(page).getByRole("navigation", { name: "Repository lane navigation" });
  await expect(laneNavigation).toBeVisible();
  await expect(laneNavigation.getByRole("button")).toHaveCount(2);
  await expect(laneNavigation.getByRole("button").first()).toContainText("Lane 1 ·");
  await expect(gameTapeFlow(page).getByText("Repository outcome", { exact: true })).toHaveCount(2);
  await expect(gameTapeFlow(page).getByRole("group", { name: "Recorded Step statuses for this repository lane", exact: true })).toHaveCount(2);
  await expect(gameTapeFlow(page).getByText("8 Passed", { exact: true })).toBeVisible();
  const firstLane = laneNavigation.getByRole("button", { name: new RegExp(LONG_REPOSITORY_NAME.slice(0, 36)) });
  await firstLane.click();
  await expect(firstLane).toHaveAttribute("aria-current", "true");
  const selectedLaneStep = gameTapeFlow(page).getByRole("button", { name: new RegExp(`Step 2: ${LONG_STEP_NAME}`) }).first();
  await expect(selectedLaneStep).toBeVisible();
  await selectedLaneStep.click();

  await gameTapeRegion(page).getByRole("button", { name: "Timeline" }).click();
  await expect(gameTapeRegion(page).getByText(LONG_STEP_NAME, { exact: true }).first()).toBeVisible();
  await gameTapeRegion(page).getByRole("button", { name: "Logs", exact: true }).click();
  await expect(gameTapeRegion(page).getByText(LONG_STEP_NAME, { exact: true }).first()).toBeVisible();
  await gameTapeRegion(page).getByRole("button", { name: "Overview" }).click();
  await page.locator("summary", { hasText: "Technical evidence and receipts" }).click();
  await expect(gameTapeRegion(page).getByText("Run and Plan identity")).toBeVisible();
});

test("failed Run exposes a lane-local review focus without changing execution order", async ({ page }) => {
  await openRunFixture(page, { runId: FAILED_RUN_ID });
  await expect(gameTapeRegion(page).getByText(/deliberately long failure message/)).toBeVisible();
  await expect(gameTapeRegion(page).getByText("Review focus", { exact: true })).toBeVisible();
  await gameTapeRegion(page).getByRole("button", { name: "Jump to Failed Step" }).click();
  await expect(gameTapeInspector(page)).toBeVisible();
  await expect(gameTapeRegion(page).getByRole("heading", { name: LONG_STEP_NAME })).toBeVisible();
});

test("Failure focus is explicit, reversible and never filters Passed Steps", async ({ page }) => {
  await openRunFixture(page, { runId: FAILED_RUN_ID });
  await expect(page).toHaveURL(new RegExp(`run=${FAILED_RUN_ID}`));

  const tape = gameTapeRegion(page);
  const recovery = tape.getByRole("region", { name: "Recovery", exact: true });
  await expect(recovery.getByText(/deliberately long failure message/)).toBeVisible();

  const issues = tape.getByRole("alert", { name: "Start with these failures", exact: true });
  await expect(issues.getByText(/exact Step failure text is intentionally long/)).toBeVisible();
  const focusFailures = issues.getByRole("button", { name: "Focus failures", exact: true });
  await expect(focusFailures).toHaveAttribute("aria-pressed", "false");
  await focusFailures.click();

  const showAllSteps = issues.getByRole("button", { name: "Show all Steps", exact: true });
  await expect(showAllSteps).toHaveAttribute("aria-pressed", "true");
  await expect(page).toHaveURL(/focus=failures/);
  await expect(page.locator('.game-tape-flowchart[data-failure-focus="true"]')).toBeVisible();

  const focusedFlow = gameTapeFlow(page);
  // The failed-run fixture owns one repository, so the Flow intentionally omits
  // redundant lane navigation. Failure focus must preserve that lane and every
  // admitted Step rather than inventing navigation or filtering Passed Steps.
  await expect(focusedFlow.getByRole("navigation", { name: "Repository lane navigation" })).toHaveCount(0);
  await expect(focusedFlow.getByText("Repository outcome", { exact: true })).toHaveCount(1);
  await expect(focusedFlow.getByRole("button", { name: /Step 1: ruff-format-and-static-quality/ })).toBeVisible();
  await expect(focusedFlow.getByRole("button", { name: new RegExp(`Step 2: ${LONG_STEP_NAME}`) })).toBeVisible();

  await tape.getByRole("button", { name: "Timeline" }).click();
  await expect(page.locator('.game-tape-timeline[data-failure-focus="true"]')).toBeVisible();
  await expect(tape.getByText("ruff-format-and-static-quality", { exact: true }).first()).toBeVisible();
  await page.reload();
  await expect(gameTapeRegion(page).getByRole("button", { name: "Timeline" })).toHaveAttribute("aria-pressed", "true");
  await expect(gameTapeRegion(page).getByRole("button", { name: "Show all Steps", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator('.game-tape-timeline[data-failure-focus="true"]')).toBeVisible();
  await gameTapeRegion(page).getByRole("button", { name: "Show all Steps", exact: true }).click();
  await expect(page).not.toHaveURL(/focus=failures/);
  await expect(page.locator('[data-failure-focus="true"]')).toHaveCount(0);
});
test("Failed Step inspector explains exact canonical recurrence before raw history", async ({ page }) => {
  await openRunFixture(page, { runId: FAILED_RUN_ID });
  await gameTapeRegion(page).getByRole("button", { name: "Jump to Failed Step" }).click();
  const inspector = gameTapeInspector(page);
  await expect(inspector.getByText("Seen before", { exact: true })).toBeVisible();
  await expect(inspector.getByText("Exact occurrences", { exact: true })).toBeVisible();
  await expect(inspector.getByText("3", { exact: true })).toBeVisible();
  await expect(inspector.getByText("Exact failure reappeared after a pass", { exact: true })).toBeVisible();
});
test("Failed Step inspector exposes fast canonical failure actions before raw logs", async ({ page }) => {
  await openRunFixture(page, { runId: FAILED_RUN_ID });
  await gameTapeRegion(page).getByRole("button", { name: "Jump to Failed Step" }).click();
  const inspector = gameTapeInspector(page);
  await expect(inspector.getByRole("button", { name: "Copy error" })).toBeVisible();
  await expect(inspector.getByRole("button", { name: "Open Logs" })).toBeVisible();
  await expect(inspector.getByRole("button", { name: "Previous failure" })).toBeDisabled();
  await expect(inspector.getByRole("button", { name: "Next failure" })).toBeDisabled();
  await inspector.getByRole("button", { name: "Open Logs" }).click();
  await expect(gameTapeRegion(page).getByRole("button", { name: "Logs", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(gameTapeRegion(page).locator("summary", { hasText: "Raw stdout/stderr" })).toBeVisible();
});
test("Step inspector explains first and keeps exact technical truth one disclosure deeper", async ({ page }) => {
  await openRunFixture(page);
  await gameTapeFlow(page).getByRole("button", { name: new RegExp(`Step 2: ${LONG_STEP_NAME}`) }).first().click();
  const inspector = gameTapeInspector(page);
  await expect(inspector.getByText("Execution reached this declared Step in admitted Plan order.")).toBeVisible();
  await expect(inspector.getByText("Technical Step details")).toBeVisible();
  const technicalDetails = inspector.locator("details.game-tape-inspector__technical");
  const exactCommand = technicalDetails.locator("code").filter({ hasText: "tests/with/a/realistically/long/path/that/must/remain-visible/test_browser_quality.py" });
  await expect(exactCommand).toBeHidden();
  await technicalDetails.locator("summary").click();
  await expect(page).toHaveURL(/tech=1/);
  await expect(exactCommand).toBeVisible();
  await page.reload();
  const technical = page.locator("details.game-tape-inspector__technical");
  await expect(technical).toHaveAttribute("open", "");
  await technical.locator("summary").click();
  await expect(page).not.toHaveURL(/tech=1/);
});
test("CI/CD Flow lane navigation respects reduced-motion preference", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await openRunFixture(page);
  const laneNavigation = gameTapeFlow(page).getByRole("navigation", { name: "Repository lane navigation" });
  await laneNavigation.getByRole("button").last().click();
  await expect(laneNavigation).toBeVisible();
});
test("Find in CI/CD Flow highlights and navigates matches without filtering the pipeline", async ({ page }) => {
  await openRunFixture(page);
  const search = gameTapeFlowSearch(page);
  const finder = search.getByRole("searchbox", { name: "Find in CI/CD Flow", exact: true });
  await finder.fill(LONG_GOON_NAME);
  await expect(page).toHaveURL(/flow_q=/);
  await expect(gameTapeFlow(page).getByText("Matches: 4", { exact: true })).toBeVisible();
  await page.reload();
  await expect(gameTapeFlowSearch(page).getByRole("searchbox", { name: "Find in CI/CD Flow", exact: true })).toHaveValue(LONG_GOON_NAME);
  await expect(gameTapeFlow(page).getByText("Repository outcome", { exact: true })).toHaveCount(2);
  await gameTapeFlowSearch(page).getByRole("button", { name: "Next", exact: true }).click();
  await expect(gameTapeInspector(page)).toBeVisible();
  await expect(gameTapeFlow(page).getByRole("navigation", { name: "Repository lane navigation" }).getByRole("button").first()).toHaveAttribute("aria-current", "true");
  await finder.fill("does-not-exist-in-the-admitted-pipeline");
  await expect(gameTapeFlow(page).getByText("Matches: 0", { exact: true })).toBeVisible();
  await expect(gameTapeFlowSearch(page).getByRole("button", { name: "Next", exact: true })).toBeDisabled();
  await expect(gameTapeFlow(page).getByText(LONG_PLAN_NAME, { exact: true })).toBeVisible();
});
test("Selected Step context walks the admitted sequence only inside its repository lane", async ({ page }) => {
  await openRunFixture(page);
  await gameTapeFlow(page).getByRole("button", { name: new RegExp(`Step 2: ${LONG_STEP_NAME}`) }).first().click();
  const context = gameTapeFlow(page).getByRole("status", { name: "Selected Step context" });
  await expect(context).toContainText(LONG_REPOSITORY_NAME);
  await expect(context).toContainText(LONG_GOON_NAME);
  await expect(context).toContainText("Recorded source:");
  await expect(context).toContainText(`templates/goons/${LONG_GOON_NAME}/goon.yml`);
  await expect(context).toContainText("Step 2 of 4 in this repository lane");
  await context.getByRole("button", { name: "Next Step" }).click();
  await expect(context).toContainText("trivy-container-and-filesystem-audit");
  await context.getByRole("button", { name: "Next Step" }).click();
  await expect(context).toContainText("Step 4 of 4 in this repository lane");
  await expect(context.getByRole("button", { name: "Next Step" })).toBeDisabled();
  await expect(gameTapeFlow(page).getByRole("navigation", { name: "Repository lane navigation" }).getByRole("button").first()).toHaveAttribute("aria-current", "true");
});
test("CI/CD Flow shows factual operational signals without inventing a critical path", async ({ page }) => {
  await openRunFixture(page);
  await expect(gameTapeFlow(page).getByText("Bottleneck · 61% of repository duration").first()).toBeVisible();
  await expect(gameTapeFlow(page).getByRole("group", { name: "Recorded Step statuses for this source segment", exact: true }).first()).toBeVisible();
  await expect(gameTapeRegion(page).getByText(/critical path/i)).toHaveCount(0);
});
test("CI/CD Flow source-segment navigation stays lane-local and keeps every Step rendered", async ({ page }) => {
  await openRunFixture(page);
  const segmentNavigation = gameTapeFlow(page).getByRole("navigation", { name: new RegExp(`Goon/source segments for ${LONG_REPOSITORY_NAME.slice(0, 32)}`) }).first();
  await expect(segmentNavigation).toBeVisible();
  await expect(segmentNavigation.getByRole("button")).toHaveCount(4);
  await gameTapeFlow(page).getByRole("button", { name: new RegExp(`Step 2: ${LONG_STEP_NAME}`) }).first().click();
  const context = gameTapeFlow(page).getByRole("status", { name: "Selected Step context" });
  await expect(context).toContainText("Source segment 2 of 4 in this repository lane");
  await context.getByRole("button", { name: "Next source segment" }).click();
  await expect(context).toContainText("trivy-container-and-filesystem-audit");
  await expect(gameTapeFlow(page).getByRole("button", { name: /Step 1: ruff-format-and-static-quality/ }).first()).toBeVisible();
  await expect(gameTapeFlow(page).getByRole("button", { name: /Step 4: publish-quality-and-security-report/ }).first()).toBeVisible();
});
test("Plan status navigation cycles canonical matching Steps without filtering the Flow", async ({ page }) => {
  await openRunFixture(page, { runId: FAILED_RUN_ID });
  const failedStatus = gameTapeFlow(page).getByRole("button", { name: /Jump through 1 Failed Steps/ });
  await failedStatus.click();
  await expect(gameTapeFlow(page).getByRole("status", { name: "Selected Step context" })).toContainText(LONG_STEP_NAME);
  await expect(gameTapeFlow(page).getByRole("button", { name: /Step 1: ruff-format-and-static-quality/ })).toBeVisible();
  await expect(gameTapeFlow(page).getByRole("button", { name: /Step 4: publish-quality-and-security-report/ })).toBeVisible();
});
test("Runs deep link restores and follows canonical selected Step identity", async ({ page }) => {
  const runId = DEFAULT_RUN_ID;
  const initialStep = `${runId}:repo-payments:step:2`;
  await openRunFixture(page, { runId, stepId: initialStep });
  const context = gameTapeFlow(page).getByRole("status", { name: "Selected Step context" });
  await expect(context).toContainText(LONG_STEP_NAME);
  await expect(page).toHaveURL(new RegExp(`run=${encodeURIComponent(runId)}.*step=${encodeURIComponent(initialStep)}`));
  await gameTapeFlow(page).getByRole("button", { name: /Step 3: trivy-container-and-filesystem-audit/ }).first().click();
  await expect(page).toHaveURL(/step=.*step%3A3/);
});
test("focused CI/CD Flow Steps support lane-local keyboard traversal", async ({ page }) => {
  await openRunFixture(page);
  const stepTwo = gameTapeFlow(page).getByRole("button", { name: new RegExp(`Step 2: ${LONG_STEP_NAME}`) }).first();
  await stepTwo.focus();
  await page.keyboard.press("ArrowRight");
  await expect(gameTapeFlow(page).getByRole("button", { name: /Step 3: trivy-container-and-filesystem-audit/ }).first()).toBeFocused();
  await page.keyboard.press("End");
  await expect(gameTapeFlow(page).getByRole("button", { name: /Step 4: publish-quality-and-security-report/ }).first()).toBeFocused();
  await page.keyboard.press("ArrowRight");
  await expect(gameTapeFlow(page).getByRole("button", { name: /Step 4: publish-quality-and-security-report/ }).first()).toBeFocused();
  await page.keyboard.press("Home");
  await expect(gameTapeFlow(page).getByRole("button", { name: /Step 1: ruff-format-and-static-quality/ }).first()).toBeFocused();
});
test("durable source-segment orientation restores without selecting a Step", async ({ page }) => {
  await openRunFixture(page);
  const sourceNavigation = gameTapeFlow(page).getByRole("navigation", { name: new RegExp(`Goon/source segments for ${LONG_REPOSITORY_NAME.slice(0, 32)}`) }).first();
  await sourceNavigation.getByRole("button", { name: new RegExp(`2 · ${LONG_GOON_NAME.slice(0, 24)}`) }).click();
  await expect(page).toHaveURL(/segment=/);
  await expect(gameTapeFlow(page).getByRole("status", { name: "Selected Step context" })).toHaveCount(0);
  await page.reload();
  const restoredNavigation = gameTapeFlow(page).getByRole("navigation", { name: new RegExp(`Goon/source segments for ${LONG_REPOSITORY_NAME.slice(0, 32)}`) }).first();
  await expect(restoredNavigation.getByRole("button", { name: new RegExp(`2 · ${LONG_GOON_NAME.slice(0, 24)}`) })).toHaveAttribute("aria-current", "location");
});

test("selected Step aligns repository lane and source-segment navigation", async ({ page }) => {
  await openRunFixture(page);
  await gameTapeFlow(page).getByRole("button", { name: new RegExp(`Step 2: ${LONG_STEP_NAME}`) }).first().click();
  const laneNavigation = gameTapeFlow(page).getByRole("navigation", { name: "Repository lane navigation" });
  await expect(laneNavigation.getByRole("button").first()).toHaveAttribute("aria-current", "true");
  const sourceNavigation = gameTapeFlow(page).getByRole("navigation", { name: new RegExp(`Goon/source segments for ${LONG_REPOSITORY_NAME.slice(0, 32)}`) }).first();
  await expect(sourceNavigation.getByRole("button", { name: new RegExp(`2 · ${LONG_GOON_NAME.slice(0, 24)}`) })).toHaveAttribute("aria-current", "location");
  const selectedStep = gameTapeFlow(page).getByRole("button", { name: new RegExp(`Step 2: ${LONG_STEP_NAME}`) }).first();
  const sourceCard = selectedStep.locator("xpath=ancestor::article[contains(@class, 'game-tape-flowchart__source')]");
  const laneCard = selectedStep.locator("xpath=ancestor::section[contains(@class, 'game-tape-flowchart__lane')]");
  await expect.poll(async () => sourceCard.evaluate((element) => getComputedStyle(element).boxShadow)).not.toBe("none");
  await expect.poll(async () => laneCard.evaluate((element) => getComputedStyle(element).borderColor)).not.toBe("");
});
test("repository lanes stay open by default and explicit folding never traps navigation", async ({ page }) => {
  await openRunFixture(page);
  await expect(gameTapeFlow(page).getByText("Repository lane 1 of 2")).toBeVisible();
  await expect(gameTapeFlow(page).getByText("Repository lane 2 of 2")).toBeVisible();
  const sourceNavigation = gameTapeFlow(page).getByRole("navigation", { name: /Goon\/source segments for/ }).first();
  await expect(sourceNavigation.getByRole("button")).toHaveCount(4);
  await expect(gameTapeFlow(page).getByRole("button", { name: new RegExp(`Step 2: ${LONG_STEP_NAME}`) }).first()).toBeVisible();
  await gameTapeFlow(page).getByRole("button", { name: "Collapse all lane details" }).click();
  await expect(gameTapeFlow(page).getByRole("button", { name: "Show lane details" })).toHaveCount(2);
  await expect(gameTapeFlow(page).getByText("Lane details hidden").first()).toBeVisible();
  await expect(gameTapeFlow(page).getByText(/Steps remain available across .* recorded source segments/).first()).toBeVisible();
  await expect(gameTapeFlow(page).getByText("Details hidden by you. Use Show lane details to reopen the complete recorded pipeline.").first()).toBeVisible();
  await gameTapeFlow(page).getByRole("button", { name: "Jump through 8 Passed Steps" }).click();
  await expect(gameTapeFlow(page).getByRole("status", { name: "Selected Step context" })).toBeVisible();
  const selectedContext = gameTapeFlow(page).getByRole("status", { name: "Selected Step context" });
  await expect(selectedContext).toContainText("Repository lane 1 of 2");
  await expect(selectedContext.getByText("Repository", { exact: true })).toBeVisible();
  await expect(selectedContext.getByText("Source segment", { exact: true })).toBeVisible();
  await expect(selectedContext.getByText("Step position", { exact: true })).toBeVisible();
  await expect(gameTapeFlow(page).getByRole("button", { name: /Step 1: ruff-format-and-static-quality/ }).first()).toBeVisible();
  await gameTapeFlow(page).getByRole("button", { name: "Expand all lane details" }).click();
  await expect(gameTapeFlow(page).getByRole("button", { name: new RegExp(`Step 2: ${LONG_STEP_NAME}`) }).first()).toBeVisible();
});
test("durable repository lane folding survives reload and Back", async ({ page }) => {
  await openRunFixture(page);
  await gameTapeFlow(page).getByRole("button", { name: "Collapse all lane details" }).click();
  await expect(page).toHaveURL(/fold=/);
  await expect(gameTapeFlow(page).getByRole("button", { name: "Show lane details" })).toHaveCount(2);
  await page.reload();
  await expect(gameTapeFlow(page).getByRole("button", { name: "Show lane details" })).toHaveCount(2);
  await gameTapeFlow(page).getByRole("button", { name: "Expand all lane details" }).click();
  await expect(page).not.toHaveURL(/fold=/);
  await page.goBack();
  await expect(gameTapeFlow(page).getByRole("button", { name: "Show lane details" })).toHaveCount(2);
});

test("Collapse passed lane details is explicit and reversible", async ({ page }) => {
  await openRunFixture(page);
  await gameTapeFlow(page).getByRole("button", { name: "Collapse passed lane details" }).click();
  await expect(gameTapeFlow(page).getByRole("button", { name: "Show lane details" })).toHaveCount(2);
  await gameTapeFlow(page).getByRole("button", { name: "Expand all lane details" }).click();
  await expect(gameTapeFlow(page).getByRole("button", { name: new RegExp(`Step 2: ${LONG_STEP_NAME}`) }).first()).toBeVisible();
});


test("Flow and Timeline make Step outcome immediately understandable without color alone", async ({ page }) => {
  await openRunFixture(page);
  const passedStep = gameTapeFlow(page).getByRole("button", { name: /Step 1: ruff-format-and-static-quality/ }).first();
  await expect(passedStep).toContainText("✓");
  await expect(passedStep).toContainText("Passed");
  await gameTapeRegion(page).getByRole("button", { name: "Timeline" }).click();
  await expect(gameTapeRegion(page).getByRole("group", { name: "Recorded Step wall-clock X axis", exact: true })).toBeVisible();
  await expect(gameTapeRegion(page).getByText(/Start \d/).first()).toBeVisible();
  await expect(gameTapeRegion(page).getByText(/Scale \+/).first()).toBeVisible();
  await expect(gameTapeRegion(page).getByRole("button", { name: /ruff-format-and-static-quality — Passed/ }).first()).toContainText("✓");
});

test("Timeline selected Step focus aligns exact timing with the shared X axis", async ({ page }) => {
  await openRunFixture(page);
  await gameTapeFlow(page).getByRole("button", { name: /Step 2:/ }).first().click();
  await gameTapeRegion(page).getByRole("button", { name: "Timeline" }).click();
  const focus = gameTapeRegion(page).getByRole("complementary", { name: "Selected Step timing", exact: true });
  await expect(focus).toBeVisible();
  await expect(focus).toContainText("Start");
  await expect(focus).toContainText("End");
  await expect(focus).toContainText("Recorded duration");
  await expect(page.locator(".game-tape-timeline-axis__ruler .game-tape-timeline-cursor")).toHaveCount(1);
});

test("Timeline explains recorded run timing and status colors before Step selection", async ({ page }) => {
  await openRunFixture(page);
  await gameTapeRegion(page).getByRole("button", { name: "Timeline" }).click();
  const summary = gameTapeRegion(page).getByRole("region", { name: "Recorded run timing", exact: true });
  await expect(summary).toBeVisible();
  await expect(summary).toContainText("Recorded Run duration");
  await expect(summary).toContainText("Passed");
});

test("Timeline bars end with redundant status pucks", async ({ page }) => {
  await openRunFixture(page);
  await gameTapeRegion(page).getByRole("button", { name: "Timeline" }).click();
  await expect(page.locator('.game-tape-timeline-track i[data-status="PASSED"] .game-tape-timeline-endmark').first()).toContainText("✓");
});

test("Logs keep the same symbol plus text Step-status language as Flow and Timeline", async ({ page }) => {
  await openRunFixture(page);
  await gameTapeRegion(page).getByRole("button", { name: "Logs", exact: true }).click();
  const passed = page.locator('.game-tape-log-step').filter({ hasText: 'ruff-format-and-static-quality' }).first();
  await expect(passed).toContainText("✓");
  await expect(passed).toContainText("Passed");
});

test("Timeline uses a bounded elapsed scale instead of stretching every Run to its own end", async ({ page }) => {
  await openRunFixture(page);
  await gameTapeRegion(page).getByRole("button", { name: "Timeline" }).click();
  const summary = gameTapeRegion(page).getByRole("region", { name: "Recorded run timing", exact: true });
  await expect(summary).toContainText("X-axis scale:");
  await expect(summary).toContainText("fixed chart width");
  await expect(gameTapeRegion(page).getByText(/Scale \+/).first()).toBeVisible();
});

test("Run Diff is first-class and uses one shared Run and Step duration scale", async ({ page }) => {
  await openRunFixture(page);
  const advancedViews = gameTapeRegion(page).locator("details.game-tape-more-views");
  await advancedViews.locator("summary").click();
  await expect(advancedViews.getByRole("button", { name: "Run Diff" })).toBeVisible();
  await advancedViews.getByRole("button", { name: "Run Diff" }).click();
  await gameTapeRegion(page).getByLabel("Compare with historical run").selectOption(FAILED_RUN_ID);
  await expect(page).toHaveURL(/view=diff/);
  await expect.poll(() => new URLSearchParams(new URL(page.url()).hash.split("?", 2)[1] ?? "").get("baseline")).toBe(FAILED_RUN_ID);
  await page.reload();
  const restoredAdvancedViews = gameTapeRegion(page).locator("details.game-tape-more-views");
  await restoredAdvancedViews.locator("summary").click();
  await expect(restoredAdvancedViews.getByRole("button", { name: "Run Diff" })).toHaveAttribute("aria-pressed", "true");
  await expect(gameTapeRegion(page).getByLabel("Compare with historical run")).toHaveValue(FAILED_RUN_ID);
  const scale = gameTapeRegion(page).getByRole("region", { name: "Shared duration scale", exact: true });
  await expect(scale).toBeVisible();
  await expect(scale).toContainText("Baseline and current use exactly the same X-axis scale");
  await expect(scale.locator(".game-tape-timeline-track i")).toHaveCount(2);
  const stepScale = gameTapeRegion(page).getByRole("region", { name: "Shared Step duration scale", exact: true });
  await expect(stepScale).toContainText("No Step pair is independently stretched");
  await expect(gameTapeRegion(page).getByText("↑ Slower").first()).toBeVisible();
  const stepTracks = page.locator(".game-tape-diff__row .game-tape-timeline-track i");
  await expect(stepTracks).toHaveCount(6);
});

test("Run Diff rejects a stale URL baseline once complete history proves it absent", async ({ page }) => {
  await page.goto(`/e2e/fixture.html?scenario=default#runs?run=${DEFAULT_RUN_ID}&view=diff&baseline=stale-baseline`);
  await page.locator("main h1").waitFor({ state: "visible" });
  await gameTapeRegion(page).waitFor({ state: "visible" });

  await expect.poll(() => new URLSearchParams(new URL(page.url()).hash.split("?", 2)[1] ?? "").get("baseline")).toBeNull();
  const advancedViews = gameTapeRegion(page).locator("details.game-tape-more-views");
  await advancedViews.locator("summary").click();
  await expect(advancedViews.getByRole("button", { name: "Run Diff" })).toHaveAttribute("aria-pressed", "true");
  await expect(gameTapeRegion(page).getByLabel("Compare with historical run")).toHaveValue("");
});

test("Logs explain the selected Step before raw stdout/stderr", async ({ page }) => {
  await openRunFixture(page);
  await gameTapeRegion(page).getByRole("button", { name: "Logs", exact: true }).click();
  await page.locator(".game-tape-log-step").filter({ hasText: "ruff-format-and-static-quality" }).first().click();
  const summary = gameTapeRegion(page).getByRole("region", { name: "Step summary", exact: true });
  await expect(summary).toBeVisible();
  await expect(summary).toContainText("Passed");
  const raw = page.locator('details[aria-label="Raw stdout/stderr"]');
  await expect(raw).not.toHaveAttribute("open", "");
  await raw.locator("summary").click();
  await expect(raw).toHaveAttribute("open", "");
  await expect(page).toHaveURL(/raw=1/);
  await page.reload();
  await expect(page.locator('details[aria-label="Raw stdout/stderr"]')).toHaveAttribute("open", "");
  await page.locator('details[aria-label="Raw stdout/stderr"] summary').click();
  await expect(page).not.toHaveURL(/raw=1/);
});

test("Raw output supports text and stream filters without inventing severity", async ({ page }) => {
  await openRunFixture(page);
  await gameTapeRegion(page).getByRole("button", { name: "Logs", exact: true }).click();
  await page.locator(".game-tape-log-step").filter({ hasText: "ruff-format-and-static-quality" }).first().click();
  const raw = page.locator('details[aria-label="Raw stdout/stderr"]');
  await raw.locator("summary").click();
  await expect(raw.getByText("Showing 3 of 3 chunks")).toBeVisible();
  await raw.getByLabel("Search raw output").fill("warning");
  await expect(page).toHaveURL(/log_q=warning/);
  await expect(raw.locator(".game-tape-live-line")).toHaveCount(1);
  await expect(raw.locator(".game-tape-live-line").first()).toContainText("diagnostic warning");
  await raw.getByLabel("Stream").selectOption("stdout");
  await expect(page).toHaveURL(/log_stream=stdout/);
  await expect(raw.getByText("No retained output matches these filters.")).toBeVisible();
  await page.reload();
  const restoredRaw = page.locator('details[aria-label="Raw stdout/stderr"]');
  await expect(restoredRaw).toHaveAttribute("open", "");
  await expect(restoredRaw.getByLabel("Search raw output")).toHaveValue("warning");
  await expect(restoredRaw.getByLabel("Stream")).toHaveValue("stdout");
  await page.goBack();
  const previousRaw = page.locator('details[aria-label="Raw stdout/stderr"]');
  await expect(previousRaw).toHaveAttribute("open", "");
  await expect(previousRaw.getByLabel("Search raw output")).toHaveValue("warning");
  await expect(previousRaw.getByLabel("Stream")).toHaveValue("all");
});

test("Run history exposes a secondary loaded-facts grid without pretending Step history is loaded", async ({ page }) => {
  await openRunFixture(page);
  const grid = page.locator('details[aria-label="Run grid (loaded summary)"]');
  await expect(grid).toBeVisible();
  await grid.locator("summary").click();
  await expect(grid).toContainText("Secondary comparison of loaded Run facts only");
  await expect(grid.getByRole("row", { name: /Recorded duration/ })).toBeVisible();
  await expect(grid.getByRole("row", { name: /Recorded failure/ })).toBeVisible();
});

test("Run history search and backend outcome filter survive reload and Back", async ({ page }) => {
  await openFixture(page, "runs");
  const history = runHistoryRegion(page);
  const search = history.getByLabel("Find runs");
  await search.fill(FAILED_RUN_ID);
  await expect.poll(() => new URLSearchParams(new URL(page.url()).hash.split("?", 2)[1] ?? "").get("history_q")).toBe(FAILED_RUN_ID);
  const filters = history.getByRole("group", { name: "Run filters", exact: true });
  const failed = filters.getByRole("button", { name: "Failed", exact: true });
  await failed.click();
  await expect(page).toHaveURL(/history_filter=failed/);
  await expect(page).toHaveURL(/history_outcome=FAILED/);
  await expect(history.locator(".history-row")).toHaveCount(1);
  await page.reload();
  await expect(history.getByLabel("Find runs")).toHaveValue(FAILED_RUN_ID);
  await expect(filters.getByRole("button", { name: "Failed", exact: true })).toHaveClass(/filter-chip--active/);
  await page.goBack();
  await expect(history.getByLabel("Find runs")).toHaveValue(FAILED_RUN_ID);
  await expect(filters.getByRole("button", { name: "All", exact: true })).toHaveClass(/filter-chip--active/);
});

test("Run review URL preserves Game Tape view and selected Step across reload and Back", async ({ page }) => {
  await openRunFixture(page);
  await gameTapeRegion(page).getByRole("button", { name: "Timeline" }).click();
  await page.locator(".game-tape-timeline-row").filter({ hasText: "ruff-format-and-static-quality" }).first().click();
  await expect(page).toHaveURL(/view=timeline/);
  await expect(page).toHaveURL(/step=/);
  await page.reload();
  await expect(gameTapeRegion(page).getByRole("button", { name: "Timeline" })).toHaveAttribute("aria-pressed", "true");
  await expect(gameTapeInspector(page)).toBeVisible();
  await gameTapeRegion(page).getByRole("button", { name: "Logs", exact: true }).click();
  await expect(page).toHaveURL(/view=logs/);
  await page.goBack();
  await expect(gameTapeRegion(page).getByRole("button", { name: "Timeline" })).toHaveAttribute("aria-pressed", "true");
});

test("Step drill-down moves focus into the inspector and Escape restores its trigger", async ({ page }) => {
  await openRunFixture(page);
  const step = gameTapeFlow(page).getByRole("button", { name: /Step 1: ruff-format-and-static-quality/ }).first();
  await step.click();
  await expect(gameTapeInspector(page)).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(gameTapeInspector(page)).toHaveCount(0);
  await expect(step).toBeFocused();
});
