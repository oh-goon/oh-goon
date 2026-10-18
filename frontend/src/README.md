# Frontend source navigation

This is the implementation map for authored frontend source. Read `../AGENTS.md` and `../README.md` first; use this file to choose the correct source owner before opening components.

## Choose the owner

```text
Task / fact                                      Start here
App bootstrap/providers/navigation/global flow  app/
Frontend API/read/mutation/presentation facts   domain/
One user-facing capability                      features/<feature>/
Cross-feature visual primitive                  components/common/
Translations                                    i18n/
Application stylesheet ownership                styles/
```

These are the complete top-level authored source owners. Do not add a root `shared/`, `utils/`, `services/` or another generic source layer.

## Decision ladder

1. **Is the change specific to one user-facing capability?** Start in `features/<feature>/`.
2. **Is it app-wide provider, page, navigation, transport or request lifecycle composition?** Use the existing `app/` owner.
3. **Is it a framework-light frontend fact/interface used by more than presentation code?** Use the narrow existing `domain/` owner.
4. **Is it only a reusable visual primitive needed by multiple features?** Use `components/common/`; feature-private UI stays inside the feature.
5. **Is the semantic fact actually backend-owned?** Do not recreate it in frontend; consume `@generated/*` or the readable API/domain surface.


## Backend truth, browser projection

Backend models and generated API contracts own execution, verification, admission, Run status, evidence, and safety decisions. Frontend code may search, filter, preserve navigation context, request backend verification, and summarize returned facts; it must not create a parallel readiness/admission/wizard state machine or reinterpret a blocked selection as a partial execution.

For a shared run the browser flow is deliberately just:

```text
Plan → repositories → backend verification → Run
```

**Shared is the execution mode, not a second Plan type in the user vocabulary.** User-facing copy should call the reusable object a `Plan`; reserve `shared_plan` / `SharedPlan*` for existing internal/API ownership where required. `Run` is enabled only from the latest backend preview for the exact current Plan/repository selection. Changing either invalidates the displayed preview and triggers a new read-only verification. Hockey language is presentation only; operational labels use backend concepts such as Repository, Plan, Run, Goon, Step, and Game Tape.

## `features/` navigation rule

Every immediate `src/features/<feature>/` directory has exactly one top-level `*Feature.ts` or `*Feature.tsx` composition entrypoint. **Open that file first.** It shows how the capability is composed before you descend into its private `components/`, `hooks/` or `model.ts` files.

Examples:

```text
features/game-tape/GameTapeFeature.ts
features/repositories/RepositoriesFeature.ts
features/security/SecurityFeature.ts
features/schedules/SchedulesFeature.ts
```

Feature directory names are the capability catalog. Do not create a separate handwritten feature registry just for navigation.

## Owner map

### `app/`

Application-level composition. `App.tsx` owns top-level page selection, `app/api/` owns transport boundaries, `createApiClient.ts` binds generated operation identities to the readable API surface, and app read/mutation owners coordinate request lifecycle behavior.

### `domain/`

Framework-light frontend facts and interfaces for API use, reads, mutations, identity, presentation and Fleet state. Domain code must not duplicate backend execution/admission/template semantics or generated operation metadata.

### `features/`

User-facing capability ownership. A feature keeps its private components/hooks/models beside itself and exposes one obvious composition entrypoint. Cross-feature interaction should go through existing app/domain contracts rather than imports into another feature's private implementation.

### `components/common/`

Only genuinely cross-feature visual primitives. Do not move feature-specific components here merely to shorten imports.

### `i18n/` and `styles/`

Translation resources/types and focused stylesheet ownership. Keep display wording/styles here when they are not feature-private; do not hide domain behavior in translation or CSS helpers.

## Common task routes

```text
Change saved Plan browser authoring
→ app/pages/PlansPage.tsx → features/league/components/SavedPlanManager.tsx

Change shared execution input rendering
→ features/plans/PlansFeature.ts → ExecutionPlanInputFields only

Change shared-run UX
→ features/league/LeagueFeature.ts → components/SharedPlanPanel.tsx + RepositorySelectionPanel.tsx

Change readiness/empty/blocked accessibility semantics
→ owning feature component + colocated test; keep state facts framework-light when reusable inside that feature

Change Game Tape UX (execution/evidence/recovery guidance)
→ features/game-tape/GameTapeFeature.ts → private components/hooks; keep Game Tape read-only and put recovery writes in Runs/Recovery owners

Change raw HTTP behavior
→ app/api/httpTransport.ts

Change write admission/result lifecycle presentation
→ app/api/mutationTransport.ts + existing app/domain mutation owner

Change readable frontend API interface
→ domain/api/ApiClient.ts → createApiClient.ts binding

Change backend response semantics
→ stop; change backend authority and regenerate @generated/*
```

## Tests

Behavior tests stay colocated with the authored source owner. Feature tests remain inside their `features/<feature>/` directory; app/domain/common-component tests remain beside those owners. `../tests/` is support-only, with shared service-free frontend test support at `../tests/support/`.
## Change impact

Frontend changes normally stay frontend-local: run the colocated tests and `npm run check`. Escalate only for these boundaries:

```text
features/<feature>/ or components/common/ → colocated tests + frontend check
app/ navigation/providers                → app tests + frontend check
app/api transport or API binding         → app/domain tests + frontend check + root cross-owner test when the generated contract boundary changes
domain/ API/read/mutation facts          → colocated domain tests + frontend check
i18n/                                    → frontend check + npm run i18n:check
styles/                                  → frontend check
```

If a required `@generated/*` fact is missing or wrong, stop at the contract boundary: change the backend/API or generator authority, run root `make generate` / `check-generated`, then return to frontend consumption. Do not recreate the missing fact in authored frontend code. Frontend-only presentation changes do not require backend validation.

