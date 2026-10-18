# Adding a capability — golden path

Do not begin by searching for every `OperationAction` use. Ask the repository to show the path first:

```bash
uv run --offline --frozen --no-sync --project backend oh-goon dev capability show initialize
```

For a new capability, generate an implementation packet:

```bash
uv run --offline --frozen --no-sync --project backend oh-goon dev capability scaffold \
  --id hello-world \
  --action status \
  --runner ENGINE \
  --label "Hello world" \
  --description "Demonstrate the capability golden path." \
  --output /tmp/hello-world-capability
```

The packet contains the exact definition owner, implementation owner, runtime-composition owner, target-selection owner, definition snippet, focused test skeleton and completion checklist.

When `--output` is used, scaffold publication is **create-only**: oh-goon stages the complete packet beside the requested directory and publishes it together. Each staged file uses the shared prepared-file publisher, then the completed sibling directory is renamed into place and the destination parent directory is fsynced where supported. A real final directory-sync error is reported rather than silently claiming crash durability; unsupported directory fsync falls back to the existing atomic-rename guarantee. It never overwrites an existing scaffold directory, because those files are intended to be edited by the contributor after generation. Choose a new output path, or deliberately remove the old scaffold before generating again.

## Normal steps

Before adding an adapter/helper around any external CLI or platform operation, apply [Native tool authority](NATIVE_TOOL_AUTHORITY.md). A new capability may add an Oh-Goon-owned authority/isolation/evidence boundary, but it must not reimplement the native tool operation it invokes.

1. Add/choose typed target vocabulary only when the capability truly needs a new target shape.
2. Register one immutable `CapabilityDefinition` in the narrow owning module:
   - `capability_engine_definitions.py`
   - `capability_plan_definitions.py`
   - `capability_recipe_definitions.py`
   - `capability_migration_definitions.py`
3. Reuse an existing runner when the execution semantics match. Add a new runner kind only when the execution semantics are genuinely different; do not add pass-through adapters.
4. Reuse an existing `FleetTargetSelector` rule where possible; add a selector only for a new domain eligibility rule.
5. Implement effects in the owning engine/integration layer.
6. Add focused registry/domain/execution tests.
7. Use `uv run --offline --frozen --no-sync --project backend oh-goon dev capability show <new-id>` to verify the resolved ownership path.
8. Run `make architecture`; regenerate contracts only when public API models changed.

## What a normal capability must not require

- a new branch in `FleetService`;
- a new branch in `FleetRunControlPlane`;
- a special Journal commit path;
- a special frontend concurrency path;
- colocating `CapabilityDefinition` with an effectful runner;
- adding a capability-specific durable settlement path instead of using the one `RepositoryEffectExecution` owner-atomic lifecycle.

If one of those appears necessary, stop and inspect the architecture rule/decision before adding the exception.
