# advanced/isolated-dataflow

Demonstrate explicit inputs, native command outputs and scoped environment imports in a sequential project workflow.

**Status:** teaching example. **Effects:** Runs project scripts/tests/build and can publish.

**Prerequisites:** Project-owned preparation/publication scripts, the locked native uv/test/build tools and the declared host token. This is a teaching workflow; the project scripts can publish.

```sh
oh-goon templates init advanced/isolated-dataflow --kind goon --output ./workflow
```

Set `target.repository_id` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Expected outputs:** The prepare Step publishes `RELEASE_TAG`; following commands consume explicit output/environment bindings.

The [project fixture](../../../../template-certification/fixtures/advanced/isolated-dataflow/) supplies real preparation, pytest/pytest-timeout and native uv build behavior. Its publication command copies exactly one built wheel and source archive into a local `published/<label>/` directory. It requires the explicitly imported `RELEASE_TOKEN` for that local permission check; use a disposable non-secret value. The fixture makes no external publication request.

The ordinary verification Plan uses the registered project workspace. The package name does not supply strict isolation. Its output reference and per-Step environment import remain explicit; native test failure stops build and publication. External release workloads need their own declared domain authority.
