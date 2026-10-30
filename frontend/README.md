# Frontend

Start with `AGENTS.md`. This README is the frontend navigation map: it shows where authored UI behavior lives and how to validate a change without reading Python internals.

Schedule input editing preserves typed Plan values when the *same* Plan revision refreshes; a different Plan or revision starts from that document's defaults and requires fresh backend preview. The Schedule frontend never confers Plan validity or execution authority.

After frontend installation, run `make frontend-schedule-plan-input-regression`
at the repository root for focused Schedule input regression checks. The
`make qualify-native-wsl` runner also uses this Vitest target before its broader
Python and frontend checks. Use `npm run verify:functional` for the full frontend
functional suite.

## First minute

From the repository root, materialize the authoritative lock once with:

```bash
make frontend-install
```

Then from `frontend/`:

```bash
npm run test
```

`make frontend-install` delegates directly to native `npm ci`. The same native npm ownership applies during APP certification; Oh-Goon does not maintain a separate certification dependency cache or offline-install authority.

Before finishing a frontend change, run:

```bash
npm run check
```

For a focused functional diagnostic, use:

```bash
npm run verify:functional
```

This runs typecheck, architecture, i18n parity, bounded unit tests, and the
production build. It does not cover the lint and coverage checks in
`npm run check`, so report which command was actually run.

If the generated API contract is stale, fix/regenerate it from repository authority with `make -C .. generate`; never edit `../generated/frontend/` directly.

## Source map

```text
src/
├── main.tsx           browser bootstrap and provider composition
├── app/               app composition, navigation, API transport/controllers/providers
├── domain/            framework-light frontend types and presentation/read/mutation facts
├── features/          product UX grouped by user-facing capability
├── components/common/ shared visual primitives with narrow ownership
├── i18n/              translation setup/resources/types
└── styles/            focused application stylesheets
tests/support/          shared frontend-only test harnesses/fakes
```

Do not create a new `shared/` or generic `utils/` layer. Reuse the narrow existing owner or create a task-specific module beside its owner. For the `app/` vs `domain/` vs `features/` decision ladder and feature entrypoint rule, read `src/README.md`.

## Entry points

- `src/main.tsx` — browser bootstrap and top-level provider wiring.
- `src/app/App.tsx` — top-level page selection/composition.
- `src/app/createApiClient.ts` — binds generated operations to the readable frontend API surface.
- `src/app/api/httpTransport.ts` — raw HTTP transport owner.
- `src/app/api/mutationTransport.ts` — mutation transport/lifecycle boundary.
- `src/domain/api/ApiClient.ts` — framework-agnostic frontend API interface.

A feature change should normally start in `src/features/<feature>/`; cross-feature app behavior starts in `src/app/`.


### CI/CD Flow

Game Tape opens on the read-only **Steps** review tab, which contains the **CI/CD Flow** visualization. It renders the admitted Plan as repository lanes and groups contiguous Steps by recorded source provenance; a `goon.yml` source becomes a visible Goon group, while missing/other provenance stays explicitly labeled rather than guessed. Multi-repository runs add lane navigation, each lane ends in its canonical repository outcome, and recorded Step statuses provide a scoreboard plus lane-local review focus for failed/interrupted/attention/running work. **Find in CI/CD Flow** searches repository/Goon/source/Step context and highlights matches without hiding the rest of the admitted pipeline. Once a Step is selected, a compact Repository → Goon/source → Step context provides lane-local Previous/Next navigation and a Jump to selected action. Those are review projections only: the chart remains sequential presentation—never a DAG scheduler or execution authority. `gameTapeFlowModel.ts` owns lossless grouping/status review helpers and `gameTapeFlow.css` owns the rink-style presentation.

Raw stdout/stderr rendering is separately bounded to at most 120 matching chunks at once even when the backend retains more. Search and stdout/stderr filtering still apply to the full loaded Step output; Earlier/Newer controls and canonical failure navigation keep every loaded matching chunk reachable without an unbounded DOM.
Durable Runs/Game Tape URL state is owned by `app/pages/runReviewState.ts` and is normalized only after the requested canonical Run is loaded. Stale Step/baseline/lane/source identities are removed with replace-style history; browser review preferences do not become execution authority.
## Visual system

`src/app/theme/themeTokens.ts` is the one tuning surface for the snow/ice/hockey palette and shared visual primitives.
Feature CSS uses semantic CSS variables from that owner; raw colors and undefined CSS variables fail architecture.
`src/styles/iceSurface.css` adds only the restrained rink/glass presentation layer. Change tokens before scattering
feature-local styling, and keep status meaning independent from decoration.

## Generated contract

```text
@generated/* → ../generated/frontend/*
@test/*      → tests/support/*
@/*          → authored src/*
```

Generated code is an input to frontend compilation, not authored frontend source.

## Tests

Behavior tests stay colocated beside the authored source they exercise. `tests/` is support-only; shared service-free test infrastructure belongs in `tests/support/`. Read `tests/README.md` before adding shared test infrastructure.

`e2e/` is the separate real-browser quality owner. It uses Playwright with deterministic API fixtures for the four everyday journeys plus Advanced route smoke. Browser quality has independent gates for content visibility/reflow, axe WCAG automation, keyboard/focus behavior and snow/ice theme integrity; read `e2e/README.md`. The ordinary frontend completion gate remains deterministic and browser-independent. Release acceptance runs every journey in Chromium plus focused Firefox/WebKit compatibility and Chromium mobile/tablet responsive coverage.
## When to escalate beyond frontend-check

Stay frontend-local for presentation and interaction changes. If the change depends on a different public API contract, return to repository authority: update backend/generation source, then run `make -C .. generate`, `check-generated`, and `test-integration` before validating the frontend again. See `src/README.md` for the source-owner impact map.


## Golden execution loop

For a frontend-authored feature/presentation change:

```text
edit the existing app/domain/feature owner
→ npm run test -- src/features/<feature>
→ npm run check
→ stop
```

Use the nearest colocated test path for app/domain/common-component work instead of the feature placeholder. Stop on the focused failure before running the complete frontend gate. If the required `@generated/*` contract is wrong or missing, leave this loop and fix the backend/generator authority first; do not recreate the fact in frontend source.

Artifact evidence stays under the canonical Step that produced or declared it. The shared artifact component exposes PRESENT/MISSING, path/size/digest and backend-recorded retained-object identity progressively; it does not guess MIME or content previews from filenames.
An explicit **Check artifact availability** action requests one bounded backend
observation for the selected Run. Outputs, Logs/Evidence and the Step inspector
share that snapshot, including refusals and omitted artifacts. Ordinary reads,
navigation and refresh invalidation do not scan retained bytes. The browser
renders an already supplied observation without checking the bytes again and
matches observations to the recorded repository, Step, path, hash and reference;
availability describes the check time and never rewrites collection-time evidence.

## Durable Run review state

Runs browser review context is owned by `src/app/pages/runReviewState.ts`. It parses, validates, patches and serializes the existing `#runs?...` presentation contract so Run/Step/view/comparison/history/Logs/Flow/disclosure state can survive reload/share/Back without duplicating backend execution semantics. Feature components still validate canonical identities such as comparison Runs, repository executions and source-segment Step anchors before using restored presentation state.

Raw-log review also preserves the current bounded `log_page` window. This keeps older-output review stable across reload/share while search/stream/Step changes deliberately reset to newest; no stdout/stderr content is stored in browser state.
