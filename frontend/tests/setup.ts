import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach } from "vitest";

import "@/i18n/setup";

if (typeof HTMLElement.prototype.scrollIntoView !== "function") {
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: () => undefined,
  });
}

Object.defineProperty(window, "scrollTo", {
  configurable: true,
  value: () => undefined,
});

Object.defineProperty(window, "matchMedia", {
  configurable: true,
  value: (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => true,
  }),
});
let originalConsoleError: typeof console.error;
let reactActWarnings: string[] = [];

beforeEach(() => {
  originalConsoleError = console.error;
  reactActWarnings = [];
  console.error = (...args: unknown[]) => {
    const message = args.map((value) => typeof value === "string" ? value : String(value)).join(" ");
    if (message.includes("not wrapped in act(")) reactActWarnings.push(message);
    originalConsoleError(...args);
  };
});

afterEach(() => {
  console.error = originalConsoleError;
  if (reactActWarnings.length === 0) return;
  throw new Error(`React test emitted ${reactActWarnings.length} unwrapped act() warning(s):\n${reactActWarnings.join("\n---\n")}`);
});
