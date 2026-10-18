# agents/agentic-review-repair-loop

Run the native bounded Agentic Review–Repair Loop.

Example caller values for your registered repository:

```yaml
inputs:
  repo: local:repo
  regression_command: scripts/regression
  verification_command: scripts/verify
```

The imported Goon owns the public input schema and ordered phases; this Plan adds no forwarding. See [the Goon](../../../goons/agents/agentic-review-repair-loop/README.md) and [project setup](../../../../examples/agentic-review-repair-loop/README.md). Override unique public inputs with `--input`; replace or reorder imports in a project-owned Composite Goon for structural changes.
