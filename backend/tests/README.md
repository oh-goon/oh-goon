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

Tests requiring the real `httpx2` transport live in
[`tests/dependency_complete/`](dependency_complete/), including the native model
broker and receiver transport tests. This keeps dependency-complete HTTP imports
out of ordinary unit collection. Full backend validation includes this surface;
portable native argument and event contracts remain in `tests/unit/integrations/`.

The scoped [Scout package](../src/oh_goon/scout/) keeps projection tests in `tests/unit/core/`, persistence tests in `tests/unit/runtime/`, public use-case tests in `tests/unit/services/`, and HTTP integration in `tests/integration/api/`. [Native issuer](../src/oh_goon/native_issuer/) behavior uses `tests/unit/services/`. Repository-tooling regressions, including architecture checks and installed-wheel/source qualification, belong in root [scripts/tests/](../../scripts/tests/); their Python implementation living under the backend does not make them backend product tests.

## Backend integration tests

```text
API/HTTP integration                    tests/integration/api/
Native engine integration               tests/integration/engine/
Product/service integration             tests/integration/services/
```

These remain backend-owned because they do not require another top-level repository owner. Tests that genuinely cross backend/generated/frontend or backend/repository-tooling boundaries belong in root `../tests/integration/`.

## Optional native qualification

Ordinary backend and integration checks run repository-correctness tests without
root privileges or an installed model server. Tests that require privileged
cross-UID custody, chroot, fs-verity, or real installed model inference carry the
existing `certification` marker and run explicitly for those execution profiles.
Their portable negative and lifecycle tests remain in the ordinary checks.
Apply the marker to the tests requiring those facilities, so portable negatives
in the same module still run during ordinary validation.

Run privileged/native probes with `pytest -m certification tests/unit` on a host
providing the selected facilities. Use `scripts/qualify_agent_local_model.sh`
for actual Ollama inference. Unavailable native facilities remain unverified for
that optional profile; a portable pass grants no strict-execution qualification.

The installed OpenCode MCP probe uses the original managed Run and a deterministic
HTTP model fixture. It checks native MCP discovery/calling and descendant
isolation; it does not prove model coding quality. Select a compatible installed
OpenCode executable and its public runtime directory explicitly, on a host with
the ordinary Bubblewrap facilities:

```sh
OH_GOON_NATIVE_OPENCODE=/opt/native-tools/bin/opencode \
OH_GOON_NATIVE_OPENCODE_RUNTIME_ROOT=/opt/native-tools \
uv run --offline --frozen --no-sync --project backend pytest -q -m certification backend/tests/integration/services/test_native_opencode_mcp.py
```

The paths are public execution inputs, never credential or unrelated repository
stores. Without an explicit selection, this optional probe skips.

## Shared test support

```text
tests/factories/  named backend test-data builders
tests/fakes/      narrow backend test doubles
```

Do not add a generic helper bucket. Reuse the closest named factory/fake or keep test-specific setup inside the owning test module.

Hosted Agent staging tests select `agent_staging_scratch` from
`factories/fleet_plan_executor.py`. After their local leases end, it restores
directory delete permission within that test's pytest-owned scratch; pytest
retains deletion authority. Sealed modes remain in force during assertions,
symlinks are not followed, and cleanup errors are not suppressed.

## Finding a test

After locating implementation code, move to the matching test-owner directory first. Search inside that directory by the source concept or capability name before searching all backend tests.
