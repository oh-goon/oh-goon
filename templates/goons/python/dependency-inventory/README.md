# python/dependency-inventory

Observe qualified `uv.lock` inventory through Hashmarks. This read-only Step does not run uv, parse declarations or inspect installed packages.

**Status:** runnable with prerequisites. **Effects:** Reads supported dependency manifests and records inventory evidence.

**Prerequisites:** A registered repository containing manifests. This `uses: dependency_inventory` observer neither installs dependencies nor selects a uv dependency group.

```sh
oh-goon init --template python/dependency-inventory --kind goon --output ./workflow --draft
```

This explicit draft is NOT_READY and has no lock. Complete `target.repository` and required inputs or Agent/repair fields, then prepare it. For a complete first run, use [normal initialization](../../../README.md).

Set `target.repository` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Expected outputs:** Dependency inventory evidence; later maintenance remains a separate explicit action.

## Result

Inspect the recorded dependency evidence in the Run. Inventory is an observation; it does not install dependencies or make a vulnerability or test-success claim.
