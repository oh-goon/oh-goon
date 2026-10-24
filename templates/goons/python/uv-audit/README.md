# python/uv-audit

Audit the repository-root uv lock with native uv and record its vulnerability result.

**Status:** runnable with prerequisites. **Effects:** Queries vulnerability data for the repository-root uv lock.

**Prerequisites:** Native uv with audit support, a repository-root uv lock and declared `host_unrestricted` egress. No dependency-group installation is declared.

```sh
oh-goon templates init python/uv-audit --kind goon --output ./workflow
```

Set `target.repository_id` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Expected outputs:** Native JSON audit output and actual Step status.

## Result

Native uv owns dependency and vulnerability interpretation. Its JSON stdout and exit status are Run evidence. This Goon does not create a separate audit report file or rewrite the lock.

Scanner database updates use unrestricted network access. See [network settings](../../../../docs/development/TEMPLATE_NETWORK_AUTHORITY.md) for the available isolation policies.
