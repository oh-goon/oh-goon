import { expect, test } from "@playwright/test";

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
  test(`${route} preserves information access at the 200% layout zoom reflow equivalent`, async ({ page, browserName }) => {
    test.skip(browserName !== "chromium", "The deterministic 200% reflow equivalent is normalized in Chromium; Firefox/WebKit are covered by the release browser matrix.");
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

test("Game Tape reflows at the 200% layout zoom equivalent without document overflow", async ({ page, browserName }) => {
  test.skip(browserName !== "chromium", "The deterministic 200% reflow equivalent is normalized in Chromium; Firefox/WebKit are covered by the release browser matrix.");
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

