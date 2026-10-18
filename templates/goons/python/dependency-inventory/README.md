# python/dependency-inventory

Observe repository dependency manifests through the typed dependency-inventory capability.

## Before you run

Select the repository whose manifests you want to inspect. This observation is repository-wide rather than a Python environment installation.

## Configure and compose

Inspect the current contract and copy the source when authoring your own capability:

```sh
oh-goon templates show python/dependency-inventory --kind goon
oh-goon plan inputs templates/goons/python/dependency-inventory/goon.yml
```

Add `python/dependency-inventory` to a Plan’s ordered `goons:` list with a plain import to keep its defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed choices or dataflow; use `pass:` when shared names need explicit recipients. Refresh the lock with the same values before running.

## Result and extension

Inspect the recorded dependency evidence in the Run. Inventory is an observation; it does not install dependencies or make a vulnerability or test-success claim.

Append another Goon to the Plan to add a capability. To change this command sequence, create a project-owned Atomic Goon and import it with an explicit `from:` source. See [authoring and local imports](../../../README.md#append-your-own-capability).
