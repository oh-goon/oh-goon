# GitLab CI migration

GitLab CI is **one-way migration input**, not an Oh Goon runtime or template type.

Executable GitLab CI behavior compiles to an **Atomic Goon**, because executable implementation belongs in Goons and Plans may never contain Steps.

## Command

```bash
oh-goon goon import-gitlab .gitlab-ci.yml \
  --variable CI_SERVER_HOST=gitlab.company.example \
  --variable COMPONENT_VERSION=4.2.0 \
  --output goon.yml
```

For private GitLab components, `--token-env GITLAB_TOKEN` is read only while source components are fetched and is never written into generated YAML.

The generated file is a repository/root Goon with explicit execution policy, requirements, and Steps. It must parse as a Goon and must be rejected as a Plan because it contains executable behavior.

## Exact destination updates

Creating a new destination requires no prior revision. Re-importing over an existing `goon.yml` requires the SHA-256 of the exact current bytes:

```bash
REVISION=$(sha256sum goon.yml | cut -d" " -f1)
oh-goon goon import-gitlab .gitlab-ci.yml \
  --output goon.yml \
  --expected-revision "$REVISION"
```

If the destination changed, import fails and leaves the existing Goon untouched. There is no overwrite/last-writer-wins path.

## Faithful subset only

The importer is a closed-world compiler. Unsupported or ambiguous semantics block output rather than being guessed or silently normalized. Examples include concurrent job graphs that cannot preserve order, multi-command shared-shell semantics that cannot be represented faithfully, ambient/unknown variables, unknown job/top-level fields, or source deadlines that cannot be represented exactly.

There is no GitLab executor and no `uses: gitlab` Step.

## Reuse rule

If migration can prove exact semantic equivalence to an existing canonical Goon, compiler code may map to that capability explicitly. There is no fuzzy matching by filename/component suffix/directory name.

Migration-specific pseudo-templates do not belong under `templates/`.
