# Private code discovery → reviewed Backstage catalog

Classification: **runnable with prerequisites, synthetic example**. Hashmarks supplies repository evidence, native Git supplies commit facts, a human approves catalog ownership and metadata, and Oh-Goon executes and retains the result. There is no model/provider Step or automatic Backstage publication. This is a CLI demonstration with the existing Game Tape; the guided UI is [HUG-208](https://linear.app/hugloss/issue/HUG-208/showcase-p1-guide-private-code-discovery-catalog-review-and-original).

## Operator guide

Start in an Oh-Goon checkout after `make init`, with native Git. Commands use that prepared environment. Five minutes is a target after preparation, not a measured promise. **Stop on any failed command, wrong target, unapproved value or ambiguous owner.** A missing hit is not proof of absence; a Git author is not the catalog owner.

### Prepare the synthetic target and capture its review identity

```sh
export PATH="$PWD/backend/.venv/bin:$PATH"
export HASHMARKS_NO_UPDATE_CHECK=1 UV_OFFLINE=1
CATALOG_DEMO_ROOT=$(mktemp -d /tmp/oh-goon-catalog.XXXXXX)
export OH_GOON_DB="$CATALOG_DEMO_ROOT/state/state.sqlite"
export OH_GOON_LEAGUE_FILE="$CATALOG_DEMO_ROOT/state/league.yml"
mkdir -p "$CATALOG_DEMO_ROOT/evidence"
cp -R examples/private-code-catalog "$CATALOG_DEMO_ROOT/workflow"
cd "$CATALOG_DEMO_ROOT/workflow"
git -C sample-repo init -q --template=
git -C sample-repo add .
git -C sample-repo -c user.name="Demo Contributor" -c user.email="demo@example.invalid" -c core.hooksPath=/dev/null -c commit.gpgsign=false commit -qm "Seed synthetic repository"
python - <<'PY'
from pathlib import Path
import yaml
path = Path("run.yml")
selection = yaml.safe_load(path.read_text())
selection["target"]["repository"] = str(Path("sample-repo").resolve())
path.write_text(yaml.safe_dump(selection, sort_keys=False))
PY
oh-goon league add-folder "$PWD"
oh-goon league scan --json
hashmarks --workspace sample-repo --state-dir "$CATALOG_DEMO_ROOT/hashmarks" map sync
oh-goon workflow inspect ./run.yml --review-source --json > "$CATALOG_DEMO_ROOT/evidence/source-review.json"
git -C sample-repo rev-parse HEAD > "$CATALOG_DEMO_ROOT/evidence/git-revision"
```

The source observation precedes discovery/review. Do not edit the repository during review. Hashmarks state and receipts stay outside authored source. `--review-source` requires one included registered target and observes existing source authority without admitting execution. The selection and lock also stay outside the reviewed target, as in this fixture: embedding the review inside its own source would invalidate it.

### Discover, distinguish the owners, and review every exported value

```sh
hashmarks --workspace sample-repo --state-dir "$CATALOG_DEMO_ROOT/hashmarks" orient
hashmarks --workspace sample-repo --state-dir "$CATALOG_DEMO_ROOT/hashmarks" find submit_order --limit 8
hashmarks --workspace sample-repo --state-dir "$CATALOG_DEMO_ROOT/hashmarks" deps src/service.py
git -C sample-repo log -1 --format='%H%n%an%n%aI' "$(cat "$CATALOG_DEMO_ROOT/evidence/git-revision")" -- src/service.py
cat sample-repo/.github/CODEOWNERS
python -m json.tool generator/catalog-input.json
```

Expected: observed Python and TypeScript; a localized `submit_order` hit; a **static** import to `src/order_status.py`; native last-matching commit ID, author and date without email. Preserve generation, freshness, ambiguity and coverage labels. Hashmarks 0.36.2 synchronized search can still report freshness `unknown` and negative evidence `not-admissible`; it does not prove complete dependencies or repository-wide absence.

CODEOWNERS is an inspected **human declaration** here, not automated ownership inference. Confirm the hypothetical `group:default/platform-team`, or select an explicit valid group/user reference. Review all six candidate values, including descriptions and team names: allowed keys do not sanitize sensitive strings. Edit `generator/catalog-input.json` only before approval. The next step binds these captured input bytes to the observed source. If either changes, repeat discovery and review rather than silently refreshing authority.

### Bind the review, execute, inspect, and deliberately export

```sh
python - "$CATALOG_DEMO_ROOT/evidence/source-review.json" <<'PY'
import json
from pathlib import Path
import sys
import yaml
path = Path("run.yml")
selection = yaml.safe_load(path.read_text())
selection["target"]["reviewed_source"] = json.loads(Path(sys.argv[1]).read_text())["target"]["source_authority"]
path.write_text(yaml.safe_dump(selection, sort_keys=False))
PY
oh-goon workflow prepare ./run.yml --no-input
oh-goon workflow validate ./run.yml --json
oh-goon run ./run.yml --json > "$CATALOG_DEMO_ROOT/evidence/run.json"
CATALOG_RUN_ID=$(python -c 'import json,sys; from pathlib import Path; print(json.loads(Path(sys.argv[1]).read_text())["fleet_run_id"])' "$CATALOG_DEMO_ROOT/evidence/run.json")
oh-goon runs tape "$CATALOG_RUN_ID" --json
oh-goon runs steps "$CATALOG_RUN_ID" --json > "$CATALOG_DEMO_ROOT/evidence/steps.json"
oh-goon runs outputs "$CATALOG_RUN_ID" --json
CATALOG_STEP_ID=$(python -c 'import json,sys; from pathlib import Path; print(json.loads(Path(sys.argv[1]).read_text())["repositories"][0]["steps"][0]["step_id"])' "$CATALOG_DEMO_ROOT/evidence/steps.json")
mkdir "$CATALOG_DEMO_ROOT/export"
oh-goon runs export-artifact "$CATALOG_RUN_ID" --step "$CATALOG_STEP_ID" --path catalog-info.yaml --output "$CATALOG_DEMO_ROOT/export/catalog-info.yaml" --json
cmp expected/catalog-info.yaml "$CATALOG_DEMO_ROOT/export/catalog-info.yaml"
git -C sample-repo status --porcelain
```

Expect a successful original Run, one command Step, a present retained artifact, byte-identical export for the supplied candidate, and an unchanged target. For deliberately changed approved metadata, inspect its exact output instead of comparing with the supplied golden. A second export to the same destination must refuse overwrite. No catalog file is added to the target; commit, push and Backstage registration remain separate effects.

Exact `run.yml` and captured independent application bytes (including candidate metadata) are bound by `goon.lock`. Admission compares `target.reviewed_source` with its already observed repository source before admitting a Run. Source edits or repository replacement refuse stale review; metadata edits invalidate the lock. `prepare` does not refresh source approval. Later caller-worktree edits do not rewrite live retained artifacts or original Game Tape. Retention is bounded; released content correctly becomes unavailable.

## Implementation, proof and qualification limits

The [requirements](../../docs/development/PRIVATE_CODE_INTELLIGENCE_SHOWCASE_REQUIREMENTS.md) define the intended product. This example supplies separate native discovery, human review, a strict renderer and one original Plan → Goon → Step → Run/artifact path. The renderer accepts exactly six bounded fields; duplicate/unknown fields, invalid values and existing output files are refused. It reads only approved metadata, never source, Git or CODEOWNERS. It uses the standard library and copies approved strings without content redaction. See [Backstage's descriptor format](https://backstage.io/docs/features/software-catalog/descriptor-format/).

From the Oh-Goon checkout, run the focused native proof:

```sh
uv run --project backend --offline --frozen --no-sync pytest -c backend/pyproject.toml tests/integration/test_private_code_catalog_discovery.py tests/integration/test_private_code_catalog_renderer.py tests/integration/test_private_code_catalog_example.py
```

This covers installed Hashmarks/Git evidence, deterministic bounded serialization, duplicate/invalid input refusal, denied source/environment sentinels, metadata/source drift before Run admission, two repositories' candidates finishing in reverse preparation order, fresh-process export, no target mutation, path/overwrite refusal and zero requests to an ambient model endpoint. Shared lifecycle owners retain their existing cancellation, recovery, repository-scope, partial-retention and browser-persistence proofs. No alternate runner or broad gate is added.

**No-provider proof is separate from network qualification.** Prepare dependencies before measurement. `UV_OFFLINE=1` disables uv acquisition; Git commands above use no remote; the Hashmarks update check is disabled. These settings do not enforce a host-wide network policy. The showcase is **NOT egress-qualified** until the protocol below is executed. CLI regressions do not establish a live-browser storage-sentinel journey, public screenshot/video qualification or unfamiliar-human comprehension. Game Tape transient logs may be unavailable; their absence does not prove live-output inspection.

For stronger egress qualification, select an independently enforced native boundary for the exact platform and record:

1. Prepared tool versions and exact source revision; bootstrap/acquisition are outside measurement.
2. Native denial configuration, permitted loopback, interface/DNS/endpoint scope and an independently exercised denial canary.
3. Execute discovery/review/Run/inspect/export inside that boundary, with bounded file-backed outbound-attempt observation and independent provider instrumentation. Include all worker/child processes.
4. Any unexpected external attempt fails qualification even if the Run succeeds. Retain its bounded scope/result; unavailable enforcement or observation is `NOT_RUN` / `NOT QUALIFIED`.

Do not advertise trusted-local execution as deny-all isolation, or zero model calls as zero egress. Real team names, descriptions, paths or secrets in allowed fields are exported unchanged. Keep public demonstrations synthetic. Future providers/publication need separate disclosure and effect authority.

See [source and lock authority](../../docs/goon-lock.md), [Hashmarks boundary](../../docs/architecture/hashmarks-oh-goon-authority-boundary.md), [privacy](../../docs/PRIVACY.md), [independent application authoring](../../docs/development/GOON_AUTHORING.md) and [frozen local search](../agent-skill-local-search/README.md).
