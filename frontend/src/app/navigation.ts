import { useCallback, useEffect, useState } from "react";

const APP_PAGES = [
  "overview", "failures", "repositories", "dependencies", "plans", "shared-runs", "schedules",
  "runs", "scout", "recovery", "tools", "settings", "help",
] as const;

export type AppPage = (typeof APP_PAGES)[number];
export type LocationWriteMode = "push" | "replace";

const PAGES: ReadonlySet<string> = new Set(APP_PAGES);

function isAppPage(value: string): value is AppPage {
  return PAGES.has(value);
}

function pageFromHash(hash: string): AppPage {
  const value = hash.replace(/^#\/?/, "").split("?", 1)[0] ?? "";
  return isAppPage(value) ? value : "overview";
}

export function readLocationHash(): string {
  return window.location.hash;
}

export function queryParamsFromHash(hash: string = readLocationHash()): URLSearchParams {
  return new URLSearchParams(hash.split("?", 2)[1] ?? "");
}

export function appLocationHash(page: AppPage, query?: Readonly<Record<string, string>>): string {
  const params = query ? new URLSearchParams(query).toString() : "";
  return params ? `#${page}?${params}` : `#${page}`;
}

export function writeLocationHash(mode: LocationWriteMode, hash: string): void {
  window.history[mode === "push" ? "pushState" : "replaceState"](window.history.state, "", hash);
  window.dispatchEvent(new Event("popstate"));
}

export function writeAppLocation(mode: LocationWriteMode, page: AppPage, query?: Readonly<Record<string, string>>): void {
  writeLocationHash(mode, appLocationHash(page, query));
}

export interface AppNavigationState {
  page: AppPage;
  navigate(page: AppPage, query?: Readonly<Record<string, string>>): void;
}

export function useAppNavigation(): AppNavigationState {
  const [hash, setHash] = useState(readLocationHash);
  const page = pageFromHash(hash);

  useEffect(() => {
    const onLocationChange = () => setHash(readLocationHash());
    window.addEventListener("hashchange", onLocationChange);
    window.addEventListener("popstate", onLocationChange);
    return () => { window.removeEventListener("hashchange", onLocationChange); window.removeEventListener("popstate", onLocationChange); };
  }, []);

  const navigate = useCallback((next: AppPage, query?: Readonly<Record<string, string>>) => {
    const nextHash = appLocationHash(next, query);
    writeLocationHash("push", nextHash);
    // Own in-app navigation synchronously as well as observing browser history.
    // A busy render must not be able to lose the synthetic popstate notification.
    setHash(nextHash);
  }, []);

  return { page, navigate };
}
