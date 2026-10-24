# security/trivy-audit

Run a read-only Trivy filesystem vulnerability scan and retain its actual machine-readable report.

**Status:** runnable with prerequisites. **Effects:** Queries vulnerability data and writes the declared report.

**Prerequisites:** Installed native scanners and admitted declared online egress. Trivy report bytes require strict content-store publication.

**What to edit / settings owner:** select caller values in your workflow `run.yml`. [`goon.yml`](goon.yml) owns input types/defaults, Steps, requirements, effects and outputs. Select this capability through a Plan; use [the common setup and customization guide](../../../README.md) for inspect → validate → lock → run.

**Expected outputs:** Native scanner status and the declared report; nonzero findings remain a failed Step.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

**Customize:** change caller inputs in `run.yml`; use ordinary `with:`/`pass:` bindings and ordered imports in a project Plan/Composite for structural changes. Native tools keep their own configuration.

Install the selected native scanner and configure its declared online access. A nonzero vulnerability result remains a failure. Trivy’s retained report requires configured strict content-store publication; the package’s retention contract explains what can be retrieved.

Oh Goon records the command, exit status, logs, and execution evidence itself. The Goon therefore does not use a Python wrapper to create a second copy of scanner status or tool-version bookkeeping.

**Artifact retention:** `trivy-audit.json` uses `content_store` because the full scan report is deliberately retained for later retrieval; command status/logs are already normal execution evidence. See the [canonical retention contract](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).

**Network authority:** `host_unrestricted` is retained intentionally because
native Trivy may fetch vulnerability databases or metadata from configured
registries, mirrors and CDNs, including on a cold cache. Disabling telemetry
and version checks is not a verified offline mode, and a complete stable
allowlist has not been proven. Operators must explicitly accept the exposure.
See the [canonical template network authority](../../../../docs/development/TEMPLATE_NETWORK_AUTHORITY.md).
