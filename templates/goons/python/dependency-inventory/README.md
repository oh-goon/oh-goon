# python/dependency-inventory

Observe qualified `uv.lock` inventory through Hashmarks. This read-only Step does not run uv, parse declarations or inspect installed packages.

**Status:** runnable with prerequisites. **Effects:** Reads supported dependency manifests and records inventory evidence.

**Prerequisites:** A registered repository containing manifests. This `uses: dependency_inventory` observer neither installs dependencies nor selects a uv dependency group.

```sh
oh-goon templates init python/dependency-inventory --kind goon --output ./workflow
```

Set `target.repository_id` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Expected outputs:** Dependency inventory evidence; later maintenance remains a separate explicit action.

## Result

Inspect the recorded dependency evidence in the Run. Inventory is an observation; it does not install dependencies or make a vulnerability or test-success claim.
