# security/trivy-audit

Run a read-only Trivy filesystem vulnerability scan and retain its actual machine-readable report.

**Status:** runnable with prerequisites. **Effects:** Queries vulnerability data and writes the declared report.

**Prerequisites:** Installed native scanners and admitted declared online egress. Trivy report bytes require strict content-store publication.

```sh
oh-goon templates init security/trivy-audit --kind plan --output ./workflow
```

The [security/trivy-audit Plan](../../../plans/security/trivy-audit/README.md) supplies the surrounding policy and ordered work. Set `target.repository_id` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Expected outputs:** Native scanner status and the declared report; nonzero findings remain a failed Step.

Install the selected native scanner and configure its declared online access. A nonzero vulnerability result remains a failure. Trivy’s retained report requires configured strict content-store publication; the package’s retention contract explains what can be retrieved.

Oh Goon records the command, exit status, logs, and execution evidence itself. The Goon therefore does not use a Python wrapper to create a second copy of scanner status or tool-version bookkeeping.

**Artifact retention:** `trivy-audit.json` uses `content_store` because the full scan report is deliberately retained for later retrieval; command status/logs are already normal execution evidence. See the [canonical retention contract](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).

**Network authority:** `host_unrestricted` is retained intentionally because
native Trivy may fetch vulnerability databases or metadata from configured
registries, mirrors and CDNs, including on a cold cache. Disabling telemetry
and version checks is not a verified offline mode, and a complete stable
allowlist has not been proven. Operators must explicitly accept the exposure.
See the [canonical template network authority](../../../../docs/development/TEMPLATE_NETWORK_AUTHORITY.md).
