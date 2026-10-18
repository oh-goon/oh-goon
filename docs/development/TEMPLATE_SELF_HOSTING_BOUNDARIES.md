# Template self-hosting boundaries — distinct evidence, not command-string deduplication

This is the M138 authoring explanation for internal `oh-goon/*` verification Goons.
Path-owned catalog identity, the existing `GoonTemplateStore`/`FleetPlanResolver`,
Step workspace provenance, and native Make/npm targets own the machine contract.
README prose explains why two delegations differ; it never creates another
resolver, execution gate, Make target, or parallel browser preflight.

## The integration pair: same command, different admitted source

| Goon | Command | Workspace authority | Evidence meaning |
| --- | --- | --- | --- |
| [`oh-goon/integration-tests`](../../templates/goons/oh-goon/integration-tests/goon.yml) | `make test-integration` | Default **repository** source: the admitted checkout | Integration suite against the admitted repository |
| [`oh-goon/integration-contract`](../../templates/goons/oh-goon/integration-contract/goon.yml) | `make test-integration` | **step** source: required `source_workspace` input bound explicitly to an earlier producer | The same native integration suite against a producer-published source tree |

The name `integration-contract` does **not** mean a narrower generated-API-only
suite. The actual `make test-integration` target owns the whole integration suite.
The semantic difference is its **source** and therefore the meaning of the
result/evidence, not which pytest selectors Make happens to call today.
Do not collapse these Goons by command equality, remove `source_workspace`,
or pretend an upstream workspace exists implicitly. A caller must provide a
valid earlier Step workspace reference; the canonical resolver/admission
validates the producer and consumer binding **before Step work**. The standalone
`integration-tests` Goon has no such producer binding, and an admitted
repository is not interchangeable with a synthesized earlier Step snapshot.

The shipped `oh-goon/self-verification` Plan selects `integration-tests`;
`integration-contract` remains an independently selectable internal capability.
Nothing here adds a hidden dependency between them or executes tests during
catalog startability certification.

## Four browser preflight boundaries

Each preflight that consumes `source_workspace` explicitly requires a real
upstream published workspace in a composed Plan. All four Goons delegate
one native Make target. They have **different failure meanings**:

| Goon | Make target | Independent question answered |
| --- | --- | --- |
| [`browser-localhost-transport-preflight`](../../templates/goons/oh-goon/browser-localhost-transport-preflight/goon.yml) | `browser-localhost-transport-preflight` | Does the Node/Playwright TCP stack observe localhost connect and post-close refusal? |
| [`browser-engine-preflight`](../../templates/goons/oh-goon/browser-engine-preflight/goon.yml) | `browser-preflight` | Can the configured Playwright engines launch with available host libraries? |
| [`browser-harness-preflight`](../../templates/goons/oh-goon/browser-harness-preflight/goon.yml) | `browser-harness-preflight` | Can Playwright load its configuration and enumerate specs without running journeys? |
| [`browser-runtime-preflight`](../../templates/goons/oh-goon/browser-runtime-preflight/goon.yml) | `browser-runtime-preflight` | Is the browser/backend runtime free of stale port ownership and unintended server reuse? |

These are **not** alternative names for one check. In particular, a healthy
localhost TCP stack says nothing about browser engine installation, harness
spec discovery or stale runtime listeners. Passing harness listing does not
prove browser launch or runtime port cleanliness. Preserve distinct failure
identities in Game Tape/evidence; do not merge for YAML DRY.

Native frontend `package.json` scripts for the engine, harness and runtime
preflights first run `browser:transport-preflight`, then their own separate
checks. That is a **native prerequisite**, not a fifth implicit Goon edge or
permission to remove the separate transport capability. `Makefile` also makes
`browser-targeted-certification` and `browser-required-certification` depend
on `browser-runtime-preflight`. Those Make prerequisites are owned by Make/npm
and must not be reconstructed as a second orchestration graph in Goon YAML.

## Review and validation rule

1. Compare canonical resolved **argv, source workspace, required inputs,
   downstream references, and failure meaning** before proposing a merge.
2. A shared native command is insufficient to prove duplicate capabilities.
3. `from: step` and its `source_workspace` are real dataflow authority, not
   decoration. Missing producers fail through the canonical resolver/admission.
4. Keep Make/npm as one native implementation of their prerequisites. Do not
   duplicate prerequisite Steps or new top-level verification switches.
5. Tests may inspect the existing catalog and native targets for drift, but
   cannot become a second resolver or execution-policy owner. No Step work or
   real browser launch is required for a static authoring regression.

See [input resolution and explicit transport](TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md),
[Goon authoring](GOON_AUTHORING.md), [browser quality matrix](BROWSER_QUALITY_MATRIX.md),
[canonical workflow](CANONICAL_WORKFLOW.md), and [fail before work](FAIL_BEFORE_WORK.md).
