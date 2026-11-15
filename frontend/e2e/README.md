# Browser quality tests

`e2e/` owns real-browser product confidence. It mounts the real React app with a deterministic in-browser `ApiClient` fixture so Playwright exercises navigation, state transitions, responsive layout, accessibility, content visibility and the ice theme without running Docker/Git/uv workloads.

## Commands

```text
npm run browser:install             # install pinned browser binaries only; never installs OS packages
npm run browser:system-deps         # explicit host OS dependencies; may require sudo/root
npm run browser:preflight           # launch all three engines and report missing host dependencies
npm run test:browser:chromium       # normal local browser loop
npm run test:browser                # full Chromium + focused cross-engine/responsive release matrix
npm run test:browser:accessibility  # axe WCAG A/AA + keyboard navigation
npm run test:browser:visibility     # clipping, overflow and 200% reflow-equivalent gate
npm run test:visual                 # compare reviewed Chromium screenshots
npm run test:visual:update          # deliberately replace reviewed screenshot baselines
```

The release matrix runs every journey in Chromium. Firefox and WebKit rerun the
cross-engine accessibility, information-visibility and route smoke. Chromium
mobile/tablet rerun the responsive visibility, fleet-scale and route smoke.
The installed-production runtime journey remains a separate three-engine gate.
This keeps actual browser coverage without repeating every product scenario five
times.

The 200% reflow-equivalent checks halve the CSS viewport through Playwright's
shared viewport API. They run in all three desktop engines and the Chromium
mobile/tablet projects, with the same overflow, clipping and fixed chrome
assertions.

## Fixture rules

- Use realistic long repository, Plan, Goon, Step, error and evidence text.
- The browser fixture may make backend facts deterministic; it must not invent a second frontend execution/Plan/Schedule model.
- Add a browser test when the user journey crosses several components/pages or when layout/zoom/browser behavior is the risk.
- Keep component/domain behavior in colocated Vitest tests. Do not move ordinary unit tests into `e2e/`.
- Do not replace browser assertions with screenshots. Content visibility and axe are independent hard gates.
- Theme screenshots protect a few canonical surfaces only. Update them deliberately after reviewing the snow/ice/hockey identity and readability.
