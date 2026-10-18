# Select a pinned agentsCookbook Skill

Use a real upstream Skill as caller-selected input to OH-Goon. This recipe runs
in **your own workflow repository**, after `make init` has initialized your
OH-Goon checkout. It adds no cookbook checkout, adapter, or catalog to OH-Goon.

The example selects
[`codebase-finding-derivation`](https://github.com/Hugloss/agentsCookbook/blob/40be826e6883d819ee6cf5c10946c793f0ef0f5f/skills/codebase-finding-derivation/SKILL.md)
at commit `40be826e6883d819ee6cf5c10946c793f0ef0f5f`, verified from the public
repository on 2026-10-18. The Skill is maintained by agentsCookbook. OH-Goon owns
source capture and the existing Agent Step execution contract.

## Pin the upstream source with Git

From the root of your Git workflow repository, using an unused submodule path:

```sh
git submodule add https://github.com/Hugloss/agentsCookbook.git libs/agents-cookbook
git -C libs/agents-cookbook checkout --detach 40be826e6883d819ee6cf5c10946c793f0ef0f5f
```

Add this section to `.goonskills` beside your root Plan or Goon. Preserve any
other registry entries:

```ini
[skill "cookbook/codebase-finding-derivation"]
source = gitlink
path = libs/agents-cookbook/skills/codebase-finding-derivation
gitlink = libs/agents-cookbook
revision = 40be826e6883d819ee6cf5c10946c793f0ef0f5f
```

Commit the registry and the actual Gitlink together. OH-Goon validates the
committed superproject entry, not just the submodule's current checkout:

```sh
git add .gitmodules .goonskills libs/agents-cookbook
git commit -m "Pin cookbook finding-derivation Skill"
```

On a fresh clone of that workflow repository, native
`git submodule update --init -- libs/agents-cookbook` materializes the recorded
commit. Git owns acquisition and updates. A missing or dirty submodule is a
source-selection error; OH-Goon does not fetch or repair it during a Run.

## Inspect the selected source

Your authored Plan/Goon must already declare `repo: repository_reference`,
`skill: skill_reference`, and `acceptance: acceptance_profile` inputs for an
Agent Step. The commands below use `goon.yml` as that root entrypoint and
`my-project` as a repository beneath the explicitly authorized repository root.
Replace the paths and names with your own. Keep the workflow root as the
current directory:

```sh
OH_GOON_SOURCE=/absolute/path/to/oh-goon
REPOSITORY_ROOT=/absolute/path/to/approved-repositories
uv run --project "$OH_GOON_SOURCE/backend" --offline --frozen --no-sync oh-goon plan inputs goon.yml --json
```

Copy the reported `root_source_sha256` into `SOURCE_SHA` and inspect the complete
source selection:

```sh
SOURCE_SHA=the_reported_root_source_sha256
uv run --project "$OH_GOON_SOURCE/backend" --offline --frozen --no-sync oh-goon plan inputs goon.yml --source-closure --expect-sha256 "$SOURCE_SHA" --repository-root "$REPOSITORY_ROOT" --skill-root . --allow-acceptance review --input repo=local:my-project --input skill=cookbook/codebase-finding-derivation --input acceptance=review
```

The report's Skill reference binds the exact Gitlink commit and Skill path;
its members include the upstream `SKILL.md`. This verifies source selection and
capture. It does not run a review or establish model quality.

For execution, select an existing supported Agent harness, its permitted model
recipient and installed model, and a narrow task suited to that harness. Reuse
ordinary `goon.lock` and Fleet admission with `--agent-skill-root` pointing to
this workflow root. The current bounded Skill loop permits up to four read-only
tool actions followed by evaluation; a general review may require a larger
inspection scope than that permits. See [Agent Skills](../../docs/AGENT_SKILLS.md)
for execution options and native qualification. Skill prose never expands the
harness's tools, permissions, action budget, or authority to report PASS.
