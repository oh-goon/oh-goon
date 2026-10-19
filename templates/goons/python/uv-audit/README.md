# python/uv-audit

Audit the repository-root uv lock with native uv and record its vulnerability result.

## Before you run

The repository root contains the committed uv project and lock, and native uv supports its preview audit command. The scanner uses the explicitly declared online egress grant; configure any required native transport before running.

## Configure and compose

Inspect the current contract and copy the source when authoring your own capability:

```sh
oh-goon templates show python/uv-audit --kind goon
oh-goon plan inputs templates/goons/python/uv-audit/goon.yml
```

Add `python/uv-audit` to a Plan’s ordered `goons:` list with a plain import to keep its defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed choices or dataflow; use `pass:` when shared names need explicit recipients. Refresh the lock with the same values before running.

## Result and extension

Native uv owns dependency and vulnerability interpretation. Its JSON stdout and exit status are Run evidence. This Goon does not create a separate audit report file or rewrite the lock.

Append another Goon to the Plan to add a capability. To change this command sequence, create a project-owned Atomic Goon and import it with an explicit `from:` source. See [authoring and local imports](../../../README.md#append-your-own-capability).

Scanner database updates use unrestricted network access. See [network settings](../../../../docs/development/TEMPLATE_NETWORK_AUTHORITY.md) for the available isolation policies.
