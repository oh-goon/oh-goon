# Backend test navigation

Backend tests mirror the implementation owner they primarily prove. Dependencies may cross owners; the test location follows the behavior under test, not every imported collaborator.

## Unit tests

```text
Implementation owner                    Test owner
src/oh_goon/api/                        tests/unit/api/
src/oh_goon/core/                       tests/unit/core/
src/oh_goon/engine/                     tests/unit/engine/
src/oh_goon/integrations/               tests/unit/integrations/
src/oh_goon/runtime/                    tests/unit/runtime/
src/oh_goon/services/                   tests/unit/services/
cli*.py and application.py              tests/unit/entrypoints/
```

There are no flat `tests/unit/test_*.py` files. If a test proves a service that calls runtime and integrations, it still belongs in `services/` when the service behavior is the subject.

## Backend integration tests

```text
API/HTTP integration                    tests/integration/api/
Native engine integration               tests/integration/engine/
Product/service integration             tests/integration/services/
```

These remain backend-owned because they do not require another top-level repository owner. Tests that genuinely cross backend/generated/frontend or backend/repository-tooling boundaries belong in root `../tests/integration/`.

## Shared test support

```text
tests/factories/  named backend test-data builders
tests/fakes/      narrow backend test doubles
```

Do not add a generic helper bucket. Reuse the closest named factory/fake or keep test-specific setup inside the owning test module.

## Finding a test

After locating implementation code, move to the matching test-owner directory first. Search inside that directory by the source concept or capability name before searching all backend tests.
