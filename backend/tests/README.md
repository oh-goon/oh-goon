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

Routine merge checks include meaningful process and concurrency regressions.
Exhaustive `slow` variants require a bounded ordinary counterpart and run through
`make release-check`; the fast local loop additionally excludes real process and
scale work. Test duration alone does not decide which contracts are required.
Repository-tooling tests, including source seals and archive transitions, live in
root `scripts/tests/` and use small real source trees.

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
symlink denial, and host-loopback denial. It does not establish a live account
or the planned native-service/private-store boundary:

```sh
OH_GOON_NATIVE_CODEX=/path/to/codex \
uv run --offline --frozen --no-sync --project backend pytest -q -m certification backend/tests/integration/services/test_native_agent_run.py -k installed_codex
```

The installed Codex service cases use only synthetic native state and a local
Responses fixture. They qualify native tool follow-up, persisted protocol resume,
explicit compaction, and an original Run with automatic compaction, frozen
Hashmarks/Skill context, independent verification and durable cleanup evidence:

```sh
OH_GOON_NATIVE_CODEX=/opt/native-codex/bin/codex \
OH_GOON_NATIVE_CODEX_RUNTIME_ROOT=/opt/native-codex \
uv run --offline --frozen --no-sync --project backend pytest -q -m certification backend/tests/integration/services/test_native_codex_service.py backend/tests/integration/services/test_native_codex_service_run.py
```

Select one node when iterating on one defect. The public runtime must remain
outside native stores. The same service Run module includes complete bounded
campaign cases: CLEAN, one or multiple independent defects repaired with real
native read-only tools and fresh re-review, and interrupted proposal with no apply.
An additional configured-MCP campaign completes the repair while the original
thread's native MCP RPC refuses the exact registered server/tool identity and
real native Code Mode attempts MCP access in every reasoning phase. Native
threadless discovery may initialize that server; workload tools remain disabled,
no MCP tool call reaches it, and its native configuration/private canary remain
unchanged.
They retain the original native
batch apply, regression, verification, linked Runs and process cleanup. These cases
establish neither real-account sign-in/refresh nor cross-Run native session adoption
or WSL2/container compatibility.
The owned Run's capability gates exclude ambient MCP/extension tool access and
refuse unproven exclusion or unsupported versions before its first model turn.

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

The installed managed-Codex review probes select a complete public native
installation and its explicit runtime root. They exercise a direct native Run and
initialized shipped Plan and Goon workflows through config inspection, validation,
lock and waited execution. Inspection, validation and locking make no model request.
Through the original Run, Bubblewrap and inference broker, a local Responses
fixture requests a real native tool and then supplies a compact complete-scope
review claim. The probes check source write,
host-private read and host-loopback denial, host-only credential isolation,
qualified Hashmarks/Skill delivery on both requests, and canonical retained
review membership:

```sh
OH_GOON_NATIVE_CODEX=/opt/native-codex/bin/codex \
OH_GOON_NATIVE_CODEX_RUNTIME_ROOT=/opt/native-codex \
uv run --offline --frozen --no-sync --project backend pytest -q -m certification backend/tests/integration/services/test_native_codex_managed_review.py
```

Keep the native distribution layout and companion programs in that public root;
never select a credential store. Missing explicit selections skip this optional
certification test. Its provider, model and host-only credential are synthetic;
no real account or inference quota is used. The synthetic model may select native
fallback metadata, so this proves installed managed transport, physical isolation
and typed review admission, not model-specific code-mode behavior, live model
quality or a successful dogfood campaign.

The backend-owned protected Codex profile has a separate installed probe. Its
managed HTTP fixture uses the selected installation's bundled `gpt-6.1-sol`
metadata and real Code Mode helper through the original typed read-only review
Run. It verifies denied source/Git mutations, absent authentication files, allowed
scratch, full instructions, native follow-up and compaction. Its host-only broker
credential is synthetic. This is separate from the legacy synthetic ChatGPT
HTTP/WebSocket matrix above; protected WebSocket is not qualified. It requires
Linux Bubblewrap and the selected complete public native installation:

```sh
OH_GOON_NATIVE_CODEX=/opt/native-codex/bin/codex \
OH_GOON_NATIVE_CODEX_RUNTIME_ROOT=/opt/native-codex \
uv run --offline --frozen --no-sync --project backend pytest -m certification backend/tests/integration/services/test_native_codex_context.py -k installed_protected_managed_codex
```

The existing OpenCode file also has an independent typed read-only review case
using its advertised native shell and the same physical source boundary:

```sh
OH_GOON_NATIVE_OPENCODE=/opt/native-tools/bin/opencode \
OH_GOON_NATIVE_OPENCODE_RUNTIME_ROOT=/opt/native-tools \
uv run --offline --frozen --no-sync --project backend pytest -m certification backend/tests/integration/services/test_native_opencode_mcp.py -k reviews_through_the_read_only_boundary
```

These synthetic probes do not access real credentials or establish live account
refresh, arbitrary final-record secrecy, complete repair convergence, WSL2 or
container compatibility. [The selected profile contract](../../docs/AGENT_PROVIDERS.md#native-owned-authentication-and-configuration)
records those boundaries. Keep the complete public installation outside the real
authentication home; never copy credentials into these tests.

The former live-account refresh helper has been removed. It opened the selected
real `auth.json` to inspect `last_refresh`, started a new account server against
that home and forced account refresh. A bounded read and boolean result still
expose native credentials to the test process; that route does not meet
native-store exclusion. The home-based refresh/sign-in recipe is withdrawn.
Existing synthetic authentication fixtures remain separate.

The [legacy live coding baseline](integration/services/test_native_codex_live.py)
retains its original Run and immutable independent verifier without that helper.
It is opt-in real inference against native Codex's existing sign-in, with no test
credential-content reads, token export, forced refresh or new account-server
process. It proves only local coding and independent verification. It is not
qualification for the new native-service/private-store contract, and has not been
rerun for that contract. Missing explicit selections still skip this optional
baseline; a selected coding failure still fails the test.

The baseline enters through `oh-goon ai` with an explicit native catalog,
caller-selected Skill and frozen reference resource. It exercises a range-merging
repair and a multi-module NDJSON reporting CLI, including lazy record validation,
aggregation, exact filtering, UTF-8 input and error exits. Each case starts with a
failing immutable verifier, then checks two ordered verification Steps with quoted
paths and literal arguments. Hashmarks follow-up, Skill delivery and native model
settings remain observable separately from independent verification results.
On Linux, file-backed stdin and file inputs also exercise the reporting CLI under
a native 64 MiB memory limit; other platforms explicitly skip that one case.

```sh
OH_GOON_NATIVE_CODEX=/opt/native-tools/bin/codex \
OH_GOON_LIVE_CODEX_HOME=/path/to/existing/.codex \
OH_GOON_LIVE_CODEX_MODEL=gpt-6-luna \
uv run --offline --frozen --no-sync --project backend pytest -m certification backend/tests/integration/services/test_native_codex_live.py
```

The selected native installation and the loaded Oh-Goon/Python runtime must be
outside the authentication-home tree, as required by the existing native tool
permissions. These cases request low reasoning effort and consume real inference;
they do not require a local model server.

The [implemented native-service adapter](../../docs/AGENT_PROVIDERS.md#native-service-adapter-design)
uses a dedicated owned process/session and has the separate synthetic installed
qualification above. Native services retain authentication, configuration and
token refresh. Real-account lifecycle qualification must use supported non-secret
native protocol facts, without file reads, token export, forced-refresh/account-server
helpers or broad configuration APIs. A cached authenticated response alone does
not establish refresh or persistence; if native lifecycle events cannot establish
those facts, record them as unverified. The withdrawn refresh helper grants no
live-account lifecycle qualification.

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
