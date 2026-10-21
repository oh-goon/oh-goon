import { expect, test } from "@playwright/test";

import { LONG_NATIVE_INPUT_IDENTITY } from "./fixtures/identities";
import { FAILED_RUN_ID } from "./fixtures/runIds";
import { gameTapeInspector, gameTapeRegion } from "./helpers/locators";
import { openFixture, openRunFixture } from "./helpers/navigation";
import { expectHeadingClearOfFixedChrome, expectNoDocumentHorizontalOverflow, expectNoSilentTextClipping, expectPrimaryActionsClearOfFixedChrome, setReflowZoomEquivalent } from "./helpers/visibility";

test("silent clipping inspection tolerates visible SVG descendants", async ({ page }) => {
  await openFixture(page, "overview");
  await page.evaluate(() => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", "120");
    svg.setAttribute("height", "24");
    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    text.textContent = "visible svg evidence";
    text.setAttribute("x", "0");
    text.setAttribute("y", "18");
    svg.append(text);
    document.body.append(svg);
  });
  await expectNoSilentTextClipping(page);
});

for (const route of ["overview", "repositories", "shared-runs", "schedules", "runs"] as const) {
  test(`${route} preserves critical information at the supported viewport`, async ({ page }) => {
    await openFixture(page, route);
    await expectNoDocumentHorizontalOverflow(page);
    await expectNoSilentTextClipping(page);
    await expectHeadingClearOfFixedChrome(page);
    await expectPrimaryActionsClearOfFixedChrome(page);
  });
}

for (const route of ["overview", "repositories", "shared-runs", "schedules", "runs"] as const) {
  test(`${route} preserves information access at the 200% layout zoom reflow equivalent`, async ({ page }) => {
    await openFixture(page, route);
    await setReflowZoomEquivalent(page, 2);
    await expectNoDocumentHorizontalOverflow(page);
    await expectNoSilentTextClipping(page);
    await expectPrimaryActionsClearOfFixedChrome(page);
  });
}
test("Game Tape long operational evidence remains readable in the CI/CD Flow and every primary review view", async ({ page }) => {
  await openRunFixture(page);
  await expectNoDocumentHorizontalOverflow(page);
  const flowTrack = page.locator(".game-tape-flowchart__track").first();
  const scrollOwnership = await flowTrack.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
    overflowX: getComputedStyle(element).overflowX,
    documentClientWidth: document.documentElement.clientWidth,
    documentScrollWidth: document.documentElement.scrollWidth,
  }));
  expect(scrollOwnership.documentScrollWidth).toBeLessThanOrEqual(scrollOwnership.documentClientWidth + 1);
  expect(scrollOwnership.overflowX).toBe("auto");
  expect(scrollOwnership.scrollWidth).toBeGreaterThan(scrollOwnership.clientWidth);
  await expectNoSilentTextClipping(page);
  await expectPrimaryActionsClearOfFixedChrome(page);
  await page.getByRole("region", { name: "Game Tape", exact: true }).getByRole("button", { name: "Timeline", exact: true }).click();
  await expectNoDocumentHorizontalOverflow(page);
  await expectNoSilentTextClipping(page);
  await page.getByRole("region", { name: "Game Tape", exact: true }).getByRole("button", { name: "Logs", exact: true }).click();
  await expectNoDocumentHorizontalOverflow(page);
  await expectNoSilentTextClipping(page);
  await page.getByRole("region", { name: "Game Tape", exact: true }).getByRole("button", { name: "Overview", exact: true }).click();
  await page.locator("summary", { hasText: "Technical evidence and receipts" }).click();
  await expectNoDocumentHorizontalOverflow(page);
  await expectNoSilentTextClipping(page);
});
test("selected Flow context keeps long Repository/Goon/Step identity readable", async ({ page }) => {
  await openRunFixture(page);
  await page.getByRole("button", { name: /Step 2: validate-generated-contract/ }).first().click();
  await expectNoDocumentHorizontalOverflow(page);
  await expectNoSilentTextClipping(page);
});

test("Game Tape reflows at the 200% layout zoom equivalent without document overflow", async ({ page }) => {
  await openRunFixture(page);
  await setReflowZoomEquivalent(page, 2);
  await expectNoDocumentHorizontalOverflow(page);
  const flowTrack = page.locator(".game-tape-flowchart__track").first();
  const scrollOwnership = await flowTrack.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
    overflowX: getComputedStyle(element).overflowX,
    laneOverflowX: getComputedStyle(element.closest(".game-tape-flowchart__lane") as HTMLElement).overflowX,
    flowchartOverflowX: getComputedStyle(element.closest(".game-tape-flowchart") as HTMLElement).overflowX,
    flowchartRight: Math.round((element.closest(".game-tape-flowchart") as HTMLElement).getBoundingClientRect().right),
    documentClientWidth: document.documentElement.clientWidth,
  }));
  expect(scrollOwnership.overflowX).toBe("auto");
  expect(scrollOwnership.laneOverflowX).toBe("clip");
  expect(scrollOwnership.flowchartOverflowX).toBe("clip");
  expect(scrollOwnership.flowchartRight).toBeLessThanOrEqual(scrollOwnership.documentClientWidth + 1);
  expect(scrollOwnership.scrollWidth).toBeGreaterThan(scrollOwnership.clientWidth);
  await expectNoSilentTextClipping(page);
  await expectPrimaryActionsClearOfFixedChrome(page);
});

test("native Agent claims remain disclosable on mobile without changing recorded failure", async ({ page }) => {
  await openRunFixture(page, { runId: FAILED_RUN_ID });
  await gameTapeRegion(page).getByRole("button", { name: "Jump to Failed Step" }).click();
  const inspector = gameTapeInspector(page);
  await expect(inspector).toHaveAttribute("data-status", "FAILED");
  await expect(inspector.getByText("exit 1 · FIXTURE_LONG_FAILURE", { exact: true })).toBeVisible();
  const claims = inspector.getByRole("region", { name: "Native agent observations", exact: true });
  await expect(claims.getByText(/Native events are agent claims\. Process exit, cancellation and verification retain their recorded Step results/)).toBeVisible();
  const identity = claims.getByText(LONG_NATIVE_INPUT_IDENTITY, { exact: true });
  await expect(identity).toBeHidden();
  await claims.locator("summary", { hasText: "pi · isolated-managed · report" }).click();
  await expect(identity).toBeVisible();
  await expect(claims.getByText("Native conversation settled", { exact: true }).locator("..").getByText("yes", { exact: true })).toBeVisible();
  await expect(claims.getByText("Native protocol reported failure", { exact: true }).locator("..").getByText("no", { exact: true })).toBeVisible();
  await expect(claims).toContainText("pi: auto_compaction_end");
  await expect(claims).toContainText("6003");
  await expect(claims).toContainText("5874");
  await expect(inspector).toHaveAttribute("data-status", "FAILED");
  await expect(inspector.getByText("exit 1 · FIXTURE_LONG_FAILURE", { exact: true })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await identity.scrollIntoViewIfNeeded();
  await expect(identity).toBeVisible();
  const readable = await identity.evaluate((element) => {
    const range = document.createRange();
    range.selectNodeContents(element);
    if (range.getClientRects().length > 1) return true;
    let owner: HTMLElement | null = element as HTMLElement;
    while (owner && !owner.classList.contains("game-tape-inspector")) {
      if (["auto", "scroll"].includes(getComputedStyle(owner).overflowX) && owner.scrollWidth > owner.clientWidth + 1) return true;
      owner = owner.parentElement;
    }
    return false;
  });
  expect(readable, "Full native identity must wrap or remain reachable through local scrolling").toBe(true);
  await expectNoDocumentHorizontalOverflow(page);
  await expectNoSilentTextClipping(page);
});

