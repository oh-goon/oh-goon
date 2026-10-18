import { beforeEach, expect, test, vi } from "vitest";

import { ensureLocalAccess } from "@/app/api/localAccess";

beforeEach(() => {
  window.history.replaceState({}, "", "/");
  vi.unstubAllGlobals();
});

test("launch fragment is exchanged once and removed before normal API use", async () => {
  window.history.replaceState({}, "", "/#access=launch-secret");
  const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetch);

  await expect(ensureLocalAccess()).resolves.toEqual({ status: "ready" });

  expect(fetch).toHaveBeenCalledWith("/api/local-session", {
    method: "POST",
    headers: { "X-Oh-Goon-Local-Access": "launch-secret" },
    credentials: "same-origin",
    signal: expect.any(AbortSignal),
  });
  expect(window.location.hash).toBe("");
});

test("existing browser session is checked without exposing a token to JavaScript", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetch);

  await expect(ensureLocalAccess()).resolves.toEqual({ status: "ready" });

  expect(fetch).toHaveBeenCalledWith("/api/local-session", {
    method: "GET",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    signal: expect.any(AbortSignal),
  });
});

test("invalid launch capability fails closed and is removed from browser history", async () => {
  window.history.replaceState({}, "", "/#access=wrong-secret");
  const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
  vi.stubGlobal("fetch", fetch);

  await expect(ensureLocalAccess()).resolves.toEqual({ status: "required" });
  expect(window.location.hash).toBe("");
});
