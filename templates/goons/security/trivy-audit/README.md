# security/trivy-audit

Run a read-only Trivy filesystem vulnerability scan and retain its actual machine-readable report.

## Use this Goon

```sh
oh-goon templates show security/trivy-audit --kind goon
oh-goon plan inputs templates/goons/security/trivy-audit/goon.yml
```

Use `goons: [security/trivy-audit]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

Install the selected native scanner and configure its declared online access. A nonzero vulnerability result remains a failure. Trivy’s retained report requires configured strict content-store publication; the package’s retention contract explains what can be retrieved.

Oh Goon records the command, exit status, logs, and execution evidence itself. The Goon therefore does not use a Python wrapper to create a second copy of scanner status or tool-version bookkeeping.

**Artifact retention:** `trivy-audit.json` uses `content_store` because the full scan report is deliberately retained for later retrieval; command status/logs are already normal execution evidence. See the [canonical retention contract](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).

**Network authority:** `host_unrestricted` is retained intentionally because
native Trivy may fetch vulnerability databases or metadata from configured
registries, mirrors and CDNs, including on a cold cache. Disabling telemetry
and version checks is not a verified offline mode, and a complete stable
allowlist has not been proven. Operators must explicitly accept the exposure.
See the [canonical template network authority](../../../../docs/development/TEMPLATE_NETWORK_AUTHORITY.md).
