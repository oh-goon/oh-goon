# security/trivy-audit

Scan repository files for HIGH/CRITICAL vulnerabilities with native Trivy.

**Status:** runnable with prerequisites. **Effects:** Makes declared online queries and retains the JSON report.

**Prerequisites:** Native Trivy and its declared online egress; configured strict content-store publication for the report. A missing scanner or unavailable network cannot establish a scan result. Configure the admitted strict executor first; see the [execution contract](../../../../docs/CI_CD_CONTRACT.md).

```sh
oh-goon init --template security/trivy-audit --kind plan --output ./workflow --draft
```

This explicit draft is NOT_READY. Set `target.repository` to your exact registered repository path and fill the required caller choices in `workflow/run.yml`. Omitted options keep their Goon-owned defaults:

```yaml
version: 2
source: {kind: catalog-plan, name: security/trivy-audit}
target: {repository: /absolute/path/to/project}
```

```sh
oh-goon workflow inspect ./workflow/run.yml
oh-goon workflow validate ./workflow/run.yml
oh-goon workflow prepare ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** `trivy-audit.json` uses content-store retention. Findings, missing capability and command failure remain distinct actual outcomes.
