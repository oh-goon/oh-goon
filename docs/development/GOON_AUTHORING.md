# Authoring a Goon

Oh Goon deliberately keeps normal authoring smaller than the resolved execution model.

```text
Repository → Plan → Goon → Step → Execution / Verification / Evidence
```

An Atomic Goon owns ordered `steps:`. A Composite Goon owns ordered `goons:`. It never owns both. There is no DAG, inheritance, YAML macro system, patch/override model or second dependency model.

## Normal Python Goon

```yaml
version: 1
uv:
  project: backend
  group: dev
steps:
- run: uv run pytest
```

That is the baseline when the locked project lives under `backend`; omit `project` when
`pyproject.toml` and `uv.lock` are at repository root. `uv:` delegates project validity,
materialization and console-entrypoint execution to native uv. `uv.lock` remains native
dependency authority. Do not copy package lists, lock hashes, cache keys, warm-up Steps,
`.venv` paths or `uv lock --check` into a Goon.

`run:` is parsed into argv and executed directly without a shell. It rejects shell operators, redirects, command substitution, multiline shell and inline programs such as `python -c` or `sh -c`. If a command needs real logic, expose it through the repository's real CLI/script, for example `uv run verify-release`. Generic reusable logic belongs in a real tool or a backend-owned typed capability, not a hidden template scripts library.

Step IDs are optional. Declare `id:` only when later dataflow refers to that Step or when a semantic identity materially improves evidence.

## Executable authority

Ordinary typed/native operations derive their executable need from the operation itself.
Canonical Oh-Goon `make` workloads also derive child-executable need from the selected Make target,
owned scripts, npm package scripts and locked package-bin providers. Canonical Goon YAML therefore
**does not repeat that derived set in `tools.allow`**.

For canonical Make workloads the repository-owned `backend/goon-execution-requirements.json`
contains only independent workload permission policy, keyed by the derived
`make:<target>` owner. Its `workload_tool_permissions` mapping supplies the ceiling;
it does not repeat Step IDs, kinds, imports, environment, egress, workspace/cache, or
artifact/output semantics. Static admission proves:

```text
derived need ⊆ independent permission
```

and `backend/src/oh_goon/canonical_goon_tool_authority.json` transports the exact derived set into
the packaged runtime Step contract. That generated file grants nothing: `make check-generated` and
static admission reject it when it no longer matches native/Make/npm derivation. A candidate adding
`curl` to a Make target therefore discovers a new requirement but cannot authorize `curl`.

`execution.shell` remains readable explicit execution intent. For canonical workloads it does not
bypass the independent permission ceiling; the admitted exact Step authority still comes from the
proved requirement/permission closure.

For non-canonical single-Step Atomic Goons, `tools.allow` remains the explicit positive permission
form when repository-owned command delegation really requires authority-bearing child executables.
It is intentionally limited to one Step so a convenient header cannot broaden siblings. Do not use
it to mirror package-local executables already owned by `uv`/npm/native typed operations.

An admitted shell receives Oh-Goon's fixed **sandbox shell-support toolset** (for example `cat`,
`cp`, `cut`, `mkdir`, `rm`, and `sha256sum`) without making authors enumerate every core utility.
Those support executables are not ambient PATH: Fleet resolves, freezes, hashes, and evidences each
one and exposes them only inside the already-bounded sandbox. The fixed support set never includes
authority-bearing tools such as `curl`, `git`, `npm`, or `uv`. Changing the support set changes
execution-authority identity.

## Inputs

Use ordinary scalar types first:

```yaml
inputs:
  coverage_threshold:
    type: integer
    min: 70
    max: 100
    default: 70
```

A few domain types exist so authors do not copy large validation regexes:

- `hostname`
- `repository_path`
- `git_revision`
- `digest`
- `semver`
- `duration`
- `kubernetes_name`
- `kubernetes_deployment_name` (DNS subdomain, up to 253 characters; unlike the single-label `kubernetes_name`)
- `oci_repository`
- `oci_reference`
- `choice`

`pattern:` remains an advanced escape hatch for unusual string contracts. Core/simple templates should almost never need it.

## Explicit dataflow without ceremony

Use inputs and Step outputs directly where possible:

```yaml
steps:
- id: prepare
  run: uv run prepare-release
  outputs:
    RELEASE_TAG: .fleet/release-tag.txt
- run: uv run publish ${{ steps.prepare.outputs.RELEASE_TAG }}
```

Do not create `variables` merely to alias an input, env value or Step output. Do not route a Step output through `env` simply so another command can reference it. Outputs should exist only when later work really consumes them or when a result must be published.

Host-backed environment is different because it is an authority boundary. Secrets remain explicitly declared and imported only into the Step that needs them:

```yaml
env:
  REGISTRY_TOKEN:
    from: host
steps:
- run: uv run publish
  imports:
    env:
    - REGISTRY_TOKEN
```

Ambient host environment is not implicit pipeline state.

## Native command ownership

A Goon invokes native tools; it does not reproduce their semantics in YAML, shell, Make, or helper code. Package managers, Docker/BuildKit, Kubernetes, Git, test/lint/type tools, and platform commands remain semantic owners of the operations they expose. Add only the Oh-Goon-specific inputs, isolation, executable/environment authority, sequencing, and evidence required by the Step. See [Native tool authority](NATIVE_TOOL_AUTHORITY.md).

## Requirements and execution plumbing

Do not declare facts the execution boundary already knows. In particular, a CI/CD Repository target is already required to be a Git repository. Normal command Goons should not repeat repository identity, executor-capacity inheritance, default timeouts, command exit status, stdout/stderr evidence or UV cache lineage.

Repository-specific requirements remain valid when they are real capability prerequisites. Typed observation/infrastructure capabilities may also require explicit security or mutation details. Do not remove those boundaries merely to make YAML shorter.

## Shipped template identity

A shipped package is exactly:

```text
templates/goons/<category>/<name>/
├── goon.yml
└── README.md
```

Shipped packages omit `name:` and `description:` from YAML. The package path
owns the exact logical identity, such as `python/test`, and `README.md` owns
the human description. The general Goon compiler accepts optional readable
metadata, but that does not change the shipped catalog's omission policy or
introduce another package lookup identity.

A Composite Goon is simply:

```yaml
version: 1
goons:
- python/ruff
- python/typecheck
- python/test
```

Imports use exact `category/name` package identities. There is no package alias,
fuzzy lookup, recursive search, inheritance, overlay, or deduplication. Written
order and duplicate imports are preserved.

## Import occurrences

A Plan or Composite Goon may give an individual import a compile-time occurrence
name using `as:`:

```yaml
goons:
- import: python/test
  as: unit-tests
  with:
    coverage_threshold: 85
```

The package still resolves as `python/test`; `unit-tests` identifies this exact
occurrence in compiled Steps and provenance. `as:` belongs only on an `import:`
mapping in `goons:`, never on an Atomic Step or package definition. Names begin
with a lowercase letter, contain lowercase letters, digits, and internal
hyphens, end with an alphanumeric character, and have at most 40 characters.
`inputs`, `outputs`, `goons`, `steps`, `self`, and YAML-like literals are reserved.

Explicit names must not collide with any explicit or generated import ID in
the same list, regardless of ordering. Unnamed repeated imports keep their
existing ordinal numbering even when an intervening occurrence has `as:`.
Names are local to the parent; nested qualified IDs retain the 80-character
limit. Producer references use the exact local occurrence, such as
`${{ goons.update.outputs.KUBERNETES_GENERATION }}` when `update` names the
actual producing import.

Changing `as:` changes compiled identity and related provenance. Re-resolve the
edited source and its lock through the existing resolver; do not rename an
already admitted Run or Schedule. Input routing and source-location inspection
consume those same compiled IDs.

Public outputs remain explicit `outputs:` mappings. There is no wildcard or
automatic exposure of new child outputs. Any proposed shorthand needs actual
authoring evidence and the same visibility and permission boundaries; see
[template UX evaluation](../templates/TEMPLATE_UX_D_EVALUATION.md).

## Plans expose; Goons own

A shipped Plan can expose a Goon input without copying its schema:

```yaml
version: 1
inputs:
- coverage_threshold
goons:
- python/quality
```

The selected Goon owns the input contract. Projection is explicit and must resolve to exactly one directly selected Goon. `with:` is reserved for concrete binding or dataflow between capabilities; it is not schema inheritance and does not rename a Plan-owned schema.

## Typed infrastructure Goons

OCI and Kubernetes Goons may be more detailed than Python command Goons because credentials, registries, mutation targets, optimistic concurrency and other security boundaries are real semantics. Those typed capabilities must not define the mental model for ordinary Goon authoring.

## Review rule

When reviewing YAML, ask: **does this declaration express user intent/security/domain semantics, or is it backend execution bookkeeping?** Keep the first. Move the second behind the execution boundary.

For shipped catalog changes, run the template/parser/resolver tests before broader backend acceptance. Guardrails should stay small and structural; do not grow another release-gate framework around the simple contract.

## Composite inputs expose child contracts

A Composite Goon does not own another copy of a child input schema. It exposes the names it wants callers to see and forwards them explicitly:

```yaml
version: 1
inputs:
- base_registry_hostname
- containerfile
- context
- platform
goons:
- import: oci/image
  with:
    base_registry_hostname: ${{ inputs.base_registry_hostname }}
    containerfile: ${{ inputs.containerfile }}
    context: ${{ inputs.context }}
    platform: ${{ inputs.platform }}
```

The child Goon remains authority for type, default and validation. If one exposed parent input is forwarded to more than one child, those child contracts must be identical or resolution fails. Exposure never implies forwarding.

On a Composite Goon's recipient import, `pass: [containerfile, context]` is a
shorthand for explicit same-name parent-input `with:` bindings. The existing
compiler expands it before source-graph materialization. `pass:` and `with:`
may share an import only when their child targets are disjoint. Producer
outputs and workspace references stay in `with:`; `pass:` adds no inheritance,
value source, caller-level fan-out, or Step-time resolver. See
[input resolution and transport](TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md).

## Resolve external values before execution

External CI names are boundary names, not reusable Goon input names. Bind them to one canonical child input with `from:` and validate the resolved value against the child contract before any Step runs:

```yaml
- import: kubernetes/deploy
  with:
    timeout:
      from:
      - env: HELM_TIMEOUT_OVERRIDE
      - env: HELM_TIMEOUT
```

Sources are tried in order. The first defined value wins. If no source is defined, the child's declared default applies; otherwise a required input fails resolution. This is deliberately only resolution, not an expression language.

Repository YAML may be read without `yq` or shell code:

```yaml
- import: release/metadata
  with:
    version:
      from:
        yaml:
          path: project.yml
          key: project.version
```

TOML works the same way, which is useful for values already owned by
`pyproject.toml`:

```yaml
- import: release/metadata
  with:
    version:
      from:
        toml:
          path: pyproject.toml
          key: project.version
```

For a repository file whose entire contents are one scalar value, use the
deliberately smaller `text` source:

```yaml
- import: release/metadata
  with:
    version:
      from:
        text:
          path: VERSION
```

`text` trims surrounding whitespace and accepts exactly one non-empty line.
It is not a mechanism for importing scripts or arbitrary text blocks.

The path is repository-relative and cannot escape the repository boundary. Git identity is read through the backend Git observation boundary:

```yaml
- import: release/metadata
  with:
    revision:
      from:
        git: commit_short_sha
```

`env`, repository `yaml`, repository `toml`, one-line repository `text`, and
verified `git` metadata are the complete safe-source set. Structured file
sources use simple dotted keys only. Do not add jq/yq expressions, templating,
conditionals, concatenation, file-format autodetection, or shell evaluation to
input resolution.

Input resolution is for ordinary non-secret values. Credentials and tokens remain on the existing explicit secret/environment authority path so their bytes do not become resolved Plan inputs or lock/evidence content.

## Small validation contracts

Prefer readable constraints and visible defaults:

```yaml
inputs:
  image_name:
    type: string
    starts_with: my-image-storage/
  platform:
    type: choice
    choices:
    - linux/amd64
    - linux/arm64
    default: linux/amd64
  timeout:
    type: duration
    default: 5m
```

An input with neither `default` nor `optional: true` is required. Do not author `required: true`, `non_empty: true`, and `not_null: true` ceremony for ordinary values. Small domain types such as `semver`, `duration`, `kubernetes_name`, `kubernetes_deployment_name`, `oci_repository`, and `oci_reference` exist only where they replace repeated domain validation without creating a new validation language. Kubernetes namespace/container values use a single DNS label (`kubernetes_name`), while Deployment resource names permit DNS subdomains (`kubernetes_deployment_name`). Generation and image values produced by upstream Kubernetes Steps remain structural output bindings: compile-time typing must not pretend those runtime-produced bytes are present at admission.

## Resolution is declarative; computation is code

Simple fallback belongs to input resolution. Project-specific computation does not. For example, release-name composition belongs in a normal tested project function:

```python
def resolve_release(release: str, *, prefix: str = "", suffix: str = "", override: str | None = None) -> str:
    if override:
        return override
    return "-".join(value for value in (prefix, release, suffix) if value)
```

Expose it with normal project tooling:

```toml
[project.scripts]
resolve-helm-release = "project.deploy:resolve_helm_release"
```

and keep the Goon small:

```yaml
steps:
- run: uv run resolve-helm-release
```

**Validation/resolution can be declarative. Business/project computation belongs in project code.** Do not grow `if`, `case`, `concat`, `format`, template expressions, or another scripting language in Goon YAML.

## Artifact retention

`steps[].artifacts[]` declares actual produced files, not command logs or
execution metadata. Use [template artifact retention](TEMPLATE_ARTIFACT_RETENTION.md)
for the backend-owned `evidence` versus `content_store` distinction, strict
publication requirements, retained-reference lifetime and catalog examples.

## Shipped network authority

Default Steps have `egress.mode: none`; a native executable/tool grant is not
network permission. For the exceptional online security-audit Goons and for
choices among destination allowlists and host-unrestricted access, use the
[canonical template network authority guide](TEMPLATE_NETWORK_AUTHORITY.md).
A shipped unrestricted Step must spell out `capabilities.egress.mode` and
its own README must document why a narrower, verifiable policy is not yet
possible. Do not infer outbound authority from `network: true` shorthand.

## Internal self-hosting and browser preflight semantics

[Self-hosting verification boundaries](TEMPLATE_SELF_HOSTING_BOUNDARIES.md)
explains the difference between an admitted repository and an explicit upstream
published source workspace, and why browser transport, engine, harness and runtime
checks must not be merged merely because native commands share prerequisites.
