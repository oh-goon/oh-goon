import type { Locator, Page } from "@playwright/test";

export function gameTapeRegion(page: Page): Locator {
  return page.getByRole("region", { name: "Game Tape", exact: true });
}

export function gameTapeFlow(page: Page): Locator {
  return gameTapeRegion(page).getByRole("region", { name: "CI/CD execution flowchart", exact: true });
}

export function gameTapeInspector(page: Page): Locator {
  return gameTapeRegion(page).getByRole("complementary", { name: "Step inspector", exact: true });
}

export function gameTapeFlowSearch(page: Page): Locator {
  return gameTapeFlow(page).getByRole("search", { name: "CI/CD Flow finder", exact: true });
}

export function runHistoryRegion(page: Page): Locator {
  return page.getByRole("region", { name: "Run history", exact: true });
}

export function savedPlanSelect(page: Page): Locator {
  return page.getByRole("combobox", { name: "Saved Plan", exact: true });
}

export function currentRunSummary(page: Page): Locator {
  return page.getByTestId("current-run-summary");
}

export function currentRunList(page: Page): Locator {
  return page.getByTestId("current-run-list");
}
