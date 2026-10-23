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

The installed managed-Codex review probe selects a complete public native
installation and its explicit runtime root. Through the original Run, Bubblewrap
and inference broker, a local Responses fixture requests a real native tool and
then supplies a compact complete-scope review claim. It checks source write,
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

The backend-owned protected Codex profile has a separate installed probe. It uses
synthetic ChatGPT credentials, the selected installation's bundled
`gpt-6.1-sol` metadata and real Code Mode helper, through the original typed
read-only review Run. Both HTTP and WebSocket cases verify denied source/Git
mutations, private auth-file-only binding, allowed scratch, full instructions,
native follow-up and compaction. It requires Linux Bubblewrap, OpenSSL and
Python 3.14 for native compressed requests:

```sh
OH_GOON_NATIVE_CODEX=/opt/native-codex/bin/codex \
OH_GOON_NATIVE_CODEX_RUNTIME_ROOT=/opt/native-codex \
uv run --offline --frozen --no-sync --project backend pytest -m certification backend/tests/integration/services/test_native_codex_context.py -k installed_protected_codex
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
container compatibility. [The selected profile contract](../../docs/AGENT_PROVIDERS.md#select-backend-owned-protected-codex-execution)
records those boundaries. Keep the complete public installation outside the real
authentication home; never copy credentials into these tests.

The separate live-account probe uses real ChatGPT authentication and live model
inference. Create a dedicated private Codex home outside the repository and native
tool roots, and complete native `codex login` there; never copy the desktop auth
cache or put credentials into the synthetic fixture. The installed executable
and its runtime directory must remain in a separate filesystem tree from that
home. For an app-bundled executable beneath that home, select a public
installation in a separate tree while keeping credentials in place. Choose an
available model explicitly when the default is unavailable:

```sh
CODEX_HOME=/private/dedicated-codex-login /path/to/codex login
OH_GOON_NATIVE_CODEX=/path/to/codex \
OH_GOON_LIVE_CODEX_HOME=/private/dedicated-codex-login \
OH_GOON_LIVE_CODEX_MODEL=gpt-6-luna \
uv run --offline --frozen --no-sync --project backend pytest -q -m certification backend/tests/integration/services/test_native_codex_live.py
```

An existing CLI sign-in home may instead be selected in place; do not copy its
credentials or alter its authored configuration. This requires file-backed
native-managed ChatGPT authentication. The account probe explicitly selects the
built-in OpenAI provider and requests native `getAuthStatus` with
`includeToken=false`,
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
state; never archive the credential home as evidence. This cleanup applies only
to a temporary dedicated home.

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
