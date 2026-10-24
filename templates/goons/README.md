# Shipped Goons

Goons are Oh Goon capabilities. Each package is exactly `templates/goons/<category>/<name>/{goon.yml,README.md}`.

An **Atomic Goon** declares `steps:`. A **Composite Goon** declares `goons:`. A Goon declares exactly one of those forms, never both. Composite imports use exact logical `category/name` identifiers and explicit `pass:`/`with:` forwarding where needed. Requirements remain owned by the Goon that declares them.

There is no authored catalog, recursive lookup, alias, inheritance, merge, patch, or implicit input propagation.

## Resolved inputs

An Atomic Goon owns its input schema; a Composite Goon forwards explicit bindings or
projects a unique child contract. See the
[template input resolution and transport guide](../../docs/development/TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md)
for `from:` fallback, workspace/output edges, and the distinction between resolution,
admission, and execution. No Step may reopen these external input sources at runtime.

## OCI registry authority

The [OCI input and registry identity audit](../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md)
explains why the base-image pull registry and the publication destination are
independent, and why producer workspaces/digest outputs travel by explicit binding.

## Artifact retention authority

Choose `evidence` for a real file whose hash/size and presence are the result,
and `content_store` only when independently retained file bytes are required.
The [canonical artifact retention guide](../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md)
explains strict publication, reference ownership, maintenance and the shipped
catalog's deliberate exceptions. Do not duplicate an artifact path to request
both: content-store publication already produces evidence.

## Network capabilities

Every ordinary Step starts with deny-all network authority. The
[canonical template network guide](../../docs/development/TEMPLATE_NETWORK_AUTHORITY.md)
documents the audited online scanner exceptions and their explicit
`host_unrestricted` grants, plus the supported restricted modes. Tool
availability never grants egress automatically.

## Internal self-hosting verification boundaries

The [self-hosting boundary guide](../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md)
explains why two Goons may call the same `make` command with different workspace
provenance, and why browser transport/engine/harness/runtime preflights remain
distinct without inventing duplicate Make/npm prerequisite graphs.

## Start and customize

Initialize one Goon with `oh-goon templates init python/ruff --kind goon --output ./workflow`. This creates a minimal project Plan and its selected `run.yml`; edit only the target and caller choices before inspection and execution. Use `--kind plan` to select a complete catalog workflow directly.

Some Goons need an earlier producer, an ordered phase context or explicit strict execution policy. Their package guides start from an existing Plan that owns those decisions. Use the [local Python journey](../README.md) to run, configure and extend a Plan. `oh-goon templates list --kind goon` lists the actual catalog. Atomic Goons own typed input descriptions; one selected `run.yml` supplies caller choices. Local `from:` imports use the same package shape and compiler.
