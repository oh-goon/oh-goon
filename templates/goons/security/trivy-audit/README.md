# security/trivy-audit

Run a read-only Trivy filesystem vulnerability scan and retain its actual machine-readable report.

Oh Goon records the command, exit status, logs, and execution evidence itself. The Goon therefore does not use a Python wrapper to create a second copy of scanner status or tool-version bookkeeping.

**Artifact retention:** `trivy-audit.json` uses `content_store` because the full scan report is deliberately retained for later retrieval; command status/logs are already normal execution evidence. See the [canonical retention contract](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).

**Network authority:** `host_unrestricted` is retained intentionally because
native Trivy may fetch vulnerability databases or metadata from configured
registries, mirrors and CDNs, including on a cold cache. Disabling telemetry
and version checks is not a verified offline mode, and a complete stable
allowlist has not been proven. Operators must explicitly accept the exposure.
See the [canonical template network authority](../../../../docs/development/TEMPLATE_NETWORK_AUTHORITY.md).
