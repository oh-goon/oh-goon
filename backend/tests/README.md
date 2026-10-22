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

The installed Codex permission probe uses a synthetic authentication home and no
model connection. It checks the exact native tool policy used by explicit
`codex_login` profiles: workspace writes, read-only evidence, private-path and
symlink denial, and host-loopback denial. Saved-login coding demonstrations are
separate, explicitly selected live model evidence:

```sh
OH_GOON_NATIVE_CODEX=/path/to/codex \
uv run --offline --frozen --no-sync --project backend pytest -q -m certification backend/tests/integration/services/test_native_agent_run.py -k installed_codex
```

The installed Codex context probe drives the real executable through the original
Run and an independent verification Step. A bounded local Responses fixture
requests real tools, reports enough usage to trigger native automatic compaction,
and checks frozen task, Skill and qualified Hashmarks instructions through the
observed response chain and the continuation carrying the returned compaction item.
The duplicate user prompt cannot satisfy that privileged instruction check.

The four cases select API-key or ChatGPT authentication with HTTP or WebSocket
transport. The ChatGPT cases use synthetic tokens, account discovery and a local
HTTPS backend with an ephemeral test CA and server certificate (`openssl` required
for the fixture). ChatGPT HTTP requests retain native Zstandard compression; that
case requires Python 3.14's `compression.zstd` and skips explicitly on older Python.
Discovery and inference use distinct local HTTPS origins; inference must reach
the discovered backend.
The WebSocket cases require successful upgrades and actual model frames, check
privileged warm-up instructions and native incremental `previous_response_id`
links, and reject HTTP inference
fallback as WebSocket proof. These tests do not prove model reasoning quality or
live account access. On Linux, an isolated network namespace keeps the entire
fixture local (requires native `unshare` and `ip`, with the committed uv
environment already materialized):

```sh
OH_GOON_NATIVE_CODEX=/path/to/codex \
unshare --user --map-root-user --net sh -c \
  'ip link set lo up && exec uv run --offline --frozen --no-sync --project backend pytest -q -m certification backend/tests/integration/services/test_native_codex_context.py'
```

Select the installed version to qualify explicitly; this conformance check is
not an additional ordinary startup prerequisite. The native client owns transport
retries and compaction. Reconnect diagnostics may recover only through a later
native completion; explicit failed turns, missing completion and physical command
failure still cannot pass.

The separate live-account probe uses real ChatGPT authentication and live model
inference. Create a dedicated private Codex home outside the repository and native
tool roots, and complete native `codex login` there; never copy the desktop auth
cache or put credentials into the synthetic fixture. The installed executable
and its runtime directory must remain in a separate filesystem tree from that
home. Choose an available model explicitly when the default is unavailable:

```sh
CODEX_HOME=/private/dedicated-codex-login /path/to/codex login
OH_GOON_NATIVE_CODEX=/path/to/codex \
OH_GOON_LIVE_CODEX_HOME=/private/dedicated-codex-login \
OH_GOON_LIVE_CODEX_MODEL=gpt-6-luna \
uv run --offline --frozen --no-sync --project backend pytest -q -m certification backend/tests/integration/services/test_native_codex_live.py
```

This requires a file-backed native-managed ChatGPT sign-in home without user
configuration. The probe requests native `getAuthStatus` with `includeToken=false`,
rejects API-key or externally managed token modes, then requests native
`account/read` refresh. A cached account response alone cannot prove refresh:
the bounded host-only checker privately verifies that native `last_refresh`
advanced during the request and that account identity remained unchanged. It
returns only boolean facts and never exports credentials, token hashes or raw
account/RPC output. Product authentication remains opaque to Oh-Goon.

The refreshed credentials must then work in a fresh installed-Codex process
through the original managed Run. The live coding task repairs a real defect;
an immutable verifier outside the native writable workspace fails before the
repair and must pass as an independent Step afterward. No endpoint override or
simulated model response is involved. Missing explicit selections skip; a
selected account, refresh or coding failure fails qualification. This consumes
live account quota and requires external networking. After qualification, use
native `codex logout` for the dedicated home and remove its owned temporary
state; never archive the credential home as evidence.

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
