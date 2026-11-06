# Private code discovery -> Backstage catalog (Xirp-like walkthrough)

Classification: **runnable with prerequisites, synthetic example**. This is a demonstration of existing, separate authority owners, **not** a Spotify Xirp implementation, a conversational Oh-Goon search UI, or a guarantee of network isolation.

## The five-minute story

A new engineer asks: "Where is order submission implemented, what language is it written in, who last changed it, who owns it, and can I register it in Backstage without uploading source code?"

| Question | Demonstrated answer | Authority / qualification |
| --- | --- | --- |
| What languages and projects exist? | Local repository orientation | Hashmarks CodeMap; respect reported coverage/freshness |
| Where is order submission implemented? | Bounded search hits, paths and source references | Hashmarks search; no claim that missing hits prove absence |
| Who last changed a file? | Git commit author for the selected path | Native Git history; NOT team ownership or authorship of all code |
| Who owns the service? | Synthetic CODEOWNERS declaration, reviewed by a human | Repository-declared policy; not inferred from recent commits |
| Can we create catalog-info.yaml? | A deterministic, schema-limited descriptor from approved metadata | Independent renderer, admitted Oh-Goon Plan/Goon/Step, retained artifact |

The developer approves catalog metadata; **Git's last commit author is never silently mapped into Backstage spec.owner**. The supplied synthetic metadata produces YAML with no source snippets, commit author emails, absolute paths, or runtime secrets. The renderer copies approved strings without redacting their contents, so review every exported value before sharing it. In a real repository, any private team name, description, path, or secret placed in those fields is information disclosure.

## 1. Work only with the synthetic repository

Prerequisites: initialized Oh-Goon checkout/CLI (see the root README), native Git, Python 3, and a locally installed Hashmarks CLI. The fixture is intentionally small; no real private code, account, endpoint, or credential belongs in this public example.

Copy this whole directory outside the checkout and work in the copy, for example:

    cp -R examples/private-code-catalog /tmp/oh-goon-private-code-catalog
    cd /tmp/oh-goon-private-code-catalog
    git -C sample-repo init -q
    git -C sample-repo add .
    git -C sample-repo -c user.name="Demo Contributor" -c user.email="demo@example.invalid" commit -qm "Seed synthetic repository"

Discover code **locally**, without a model or provider call:

    export HASHMARKS_NO_UPDATE_CHECK=1
    hashmarks --workspace sample-repo map sync
    hashmarks --workspace sample-repo orient
    hashmarks --workspace sample-repo find "where is order submission implemented?"

The orientation and search results are producer evidence; read their freshness and coverage fields instead of inventing conclusions. The fixture includes Python service code and a TypeScript client. The exact presentation may change with the pinned Hashmarks version.

Check Git authorship separately from ownership:

    git -C sample-repo log -1 --format='%an <%ae>' -- src/service.py
    cat sample-repo/.github/CODEOWNERS

For this **one synthetic commit**, Git reports Demo Contributor. CODEOWNERS declares a hypothetical team. These are different claims: Git author = commit metadata; CODEOWNERS = a proposed owning group. An effective ownership decision for a real repo needs its own policy/approval. Neither a Git author nor a parser's language guess can grant permissions.

## 2. Review a minimal, explicit export

Edit generator/catalog-input.json **only after reviewing** repository evidence and confirming the owning team. In this synthetic fixture the approved values are already supplied; they are not automatically derived from filenames or guessed by Oh-Goon.

The renderer is a small independent application, not a second repository index. It never scans the source repository or interprets CODEOWNERS/Git/Hashmarks packets. It validates an exact allowlist of fields and creates only a Backstage Component descriptor. Inputs above 16 KiB, unknown fields, malformed owners, unexpected language tags, and existing outputs fail closed. Field validation does not detect sensitive content inside approved strings.

Inspect the candidate with native Python. The repository's merge checks run the
[renderer regressions](../../tests/integration/test_private_code_catalog_renderer.py),
including the input-size and process-memory boundaries.

    (cd generator && python3 generate_catalog.py && diff -u ../expected/catalog-info.yaml catalog-info.yaml && rm catalog-info.yaml)

The checked-in expected/catalog-info.yaml is **illustrative expected output**, not an authoritative catalog entry for the Oh-Goon project. It contains the synthetic team reference group:default/platform-team.

## 3. Run the exact renderer through Oh-Goon

The supplied plan.yml imports the single Atomic Goon at goons/catalog/generate/goon.yml. That Goon selects an **independent local application** (generator/) and invokes its native Python command. There is no parallel execution system, model harness, remote provider, new catalog DB, or repository-intelligence parser.

Register the synthetic repository, then replace REPOSITORY_PATH in the supplied run.yml with its exact registered absolute path:

    oh-goon league add-folder "$PWD"
    oh-goon league scan --json
    # Edit run.yml target.repository to the registered sample-repo path
    oh-goon workflow inspect ./run.yml
    oh-goon workflow validate ./run.yml
    oh-goon workflow prepare ./run.yml
    oh-goon run ./run.yml --json

Use the resulting actual Run ID and inspect the original evidence before exporting:

    oh-goon runs tape RUN_ID --json
    oh-goon runs steps RUN_ID --json
    oh-goon runs outputs RUN_ID --json
    mkdir -p ../private-code-catalog-output
    oh-goon runs export-artifact RUN_ID --step STEP_ID_FROM_RUNS_STEPS --path catalog-info.yaml --output ../private-code-catalog-output/catalog-info.yaml

The export is a **deliberate** action. The durable artifact is retained from the admitted Step; an arbitrary later edit to the generator or source does not rewrite that Run's outcome. Use the exact Step ID returned by your Run rather than guessing it. This example has not been live-qualified against every installed Oh-Goon/Hashmarks version, OS or isolation mode.

Backstage normally consumes a catalog-info.yaml from a repository. Registering, committing, pushing or importing the exported descriptor is a separate, explicitly approved action; the demo does **not** upload or register it. See the [Backstage descriptor format](https://backstage.io/docs/features/software-catalog/descriptor-format/).

## What stays local, and what remains unproven

- Hashmarks processes the selected repository locally. Disable its interactive public release check as above; MCP/CI and noninteractive operations already skip the automatic check. Hashmarks itself does not send repository source to an AI model.
- Oh-Goon's Plan has no model/agent/provider Step and the independent renderer uses only the Python standard library. The default Oh-Goon UI/API is local-first with no built-in source upload or telemetry.
- There is **no claim that trusted-local execution is a deny-all network sandbox**. Installation, dependency acquisition, Git remote operations, explicit provider selections, or separately enabled integrations can access networks. If zero egress is a contractual requirement, run with independently qualified deny-all network enforcement and capture actual network/provider evidence. Do not infer it merely from this example.
- An exported catalog descriptor exposes all approved metadata to whomever receives it. This fixture includes no source code; caller-supplied strings are exported unchanged. A remote Backstage registration and any provider-backed future conversational UI need separate disclosure approval and tests.
- Do not report "owner found" or "catalog ready" from stale, incomplete or ambiguous producer evidence. Refuse promotion or request human review instead.

## Product path from this demo

This proves the **composition story**, not an already shipped Xirp-equivalent UX. A future Oh-Goon discovery UI can ask the same questions while consuming Hashmarks facts through the public oh_goon.integrations.hashmarks API, obtaining native Git commit metadata from Git, requiring explicit approval of Backstage ownership, and using the same admitted Step/artifact path. Any missing typed owner/language evidence belongs in Hashmarks; no Oh-Goon shadow parser or third source of truth should fill the gap.

See [Hashmarks authority boundary](../../docs/architecture/hashmarks-oh-goon-authority-boundary.md), [local privacy](../../docs/PRIVACY.md), [independent application authoring](../../docs/development/GOON_AUTHORING.md), and the existing [frozen local search](../agent-skill-local-search/README.md).
