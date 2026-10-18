# oh-goon/template-startability

Run the catalog-wide shipped-template startability proof through the repository-owned certification harness.

The harness discovers the shipped Plan catalog through `PlanTemplateStore`, resolves every Plan through the canonical production resolver, binds the smallest declared real repository fixture, proves required native/project execution authority, and drives production admission only through `RUN_ADMITTED`.

It must stop before Run/Step workload execution. This Goon delegates to the focused repository target; `make certify` remains the release-critical authority because the canonical APP certification Plan orders this Goon.
