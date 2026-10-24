# security/trivy-audit

Scan repository files for HIGH/CRITICAL vulnerabilities with native Trivy.

**Status:** runnable with prerequisites. **Effects:** Makes declared online queries and retains the JSON report.

**Prerequisites:** Native Trivy and its declared online egress; configured strict content-store publication for the report. A missing scanner or unavailable network cannot establish a scan result. Configure the admitted strict executor first; see the [execution contract](../../../../docs/CI_CD_CONTRACT.md).

```sh
oh-goon templates init security/trivy-audit --kind plan --output ./workflow
```

Edit `workflow/run.yml` with your registered target and caller choices. Omitted options keep their Goon-owned defaults:

```yaml
version: 1
source: {kind: catalog-plan, name: security/trivy-audit}
target: {repository_id: REPOSITORY_ID}
```

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** `trivy-audit.json` uses content-store retention. Findings, missing capability and command failure remain distinct actual outcomes.
