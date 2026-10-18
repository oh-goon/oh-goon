import { expect, test } from "@playwright/test";

const repositoryPath = process.env.OH_GOON_RUNTIME_REPOSITORY_PATH;
const runId = process.env.OH_GOON_RUNTIME_RUN_ID;

test("production bundle uses the real API and primary navigation", async ({ page, request }) => {
  const errors: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  page.on("pageerror", (error) => errors.push(error.message));

  const health = await request.get("/api/health");
  expect(health.ok()).toBeTruthy();

  await page.goto("/#repositories");
  await expect(page.getByRole("heading", { level: 1, name: "Repositories" })).toBeVisible();
  await page.getByRole("button", { name: "Scout" }).click();
  await expect(page).toHaveURL(/#scout/);
  await expect(page.getByRole("heading", { level: 1, name: "The Scout" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Needs Scouting" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Run CI\/CD/i })).toHaveCount(0);

  await page.getByRole("button", { name: "Runs" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Runs" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: "Runs" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("runtime repository location is persisted through the real HTTP API", async ({ request }) => {
  test.skip(repositoryPath === undefined, "acceptance caller did not supply a server-visible repository path");
  const add = await request.post("/api/locations", { data: { path: repositoryPath } });
  expect(add.ok()).toBeTruthy();
  const listed = await request.get("/api/locations");
  expect(listed.ok()).toBeTruthy();
  const payload = await listed.json() as { locations: string[] };
  expect(payload.locations).toContain(repositoryPath);
});


test("real acceptance run is visible in Game Tape", async ({ page, request }) => {
  test.skip(runId === undefined, "acceptance caller did not supply a real Fleet Run ID");
  const detail = await request.get(`/api/fleet/runs/${runId}`);
  expect(detail.ok()).toBeTruthy();
  const payload = await detail.json() as { outcome: string; game_tape: unknown };
  expect(payload.outcome).toBe("SUCCESS");
  expect(JSON.stringify(payload.game_tape)).toContain("acceptance-step");

  await page.goto(`/#runs?run=${runId}`);
  await expect(page.getByRole("heading", { level: 1, name: "Runs" })).toBeVisible();
  await expect(page.getByText("acceptance-step", { exact: true }).first()).toBeVisible();
});
