# Hashmarks dependency consumption

Hashmarks release construction, qualification, checksums, publication, and standalone installation are owned by Hashmarks. Oh-Goon does not vendor, reacquire, repackage, privately materialize, or reconstruct that release authority.

The Oh-Goon backend consumes the supported Hashmarks Python API through `oh_goon.integrations.hashmarks`. Dependency intent lives in `backend/pyproject.toml`; exact selected artifact/resolution authority lives in `backend/uv.lock`; native `uv` alone authors that lock.

To update Hashmarks, change the released wheel selected by `backend/pyproject.toml`, run the normal native dependency-authoring workflow (`make sync`), review the resulting `uv.lock`, and then run normal qualification. No Hashmarks-specific downloader or retained wheel is part of Oh-Goon.

Hashmarks standalone CLI/MCP installation is an external host concern and must not become a second backend dependency path. Agent hosts use the Hashmarks-owned installer and registration commands; the product backend uses the uv-owned Python dependency.

Evidence compatibility remains strict: evidence producer implementation identity is compared with the identity reported by the actually loaded Hashmarks runtime. Oh-Goon source code does not copy the current Hashmarks release/version/commit/tree/artifact identity as a parallel authority.

Independent merge validation runs the reviewed base controller. It uses native uv
to materialize the candidate's committed lock in a disposable dependency-only
environment, then inspects that Hashmarks package as data through the base's
supported public artifact-identity API. The candidate project, interpreter and
package initializers do not execute. The resulting identity must exactly match
the candidate evidence, including for changes to bytes under the same version.
The base controller's own older installed Hashmarks is not the candidate's
dependency selection authority. See the [trusted merge contract](../CI_CD_CONTRACT.md)
for source, native-lock and package-confinement checks.

Changes to this independent admission control must be reviewed and merged before
an upgrade can rely on them. Producer adoption still requires qualified consumer
evidence within an explicitly admitted scope and normal execution proof;
matching package identity alone does not qualify a dependency change.

## Bounded Hashmarks upgrade proof

The development verification owner admits a Hashmarks-only release replacement
when both backend dependency files change and the manifest changes only its
canonical released Hashmarks wheel reference. Hashmarks' native uv adapter and
semantic delta must show one selected Hashmarks replacement, unchanged other
packages, and unchanged dependency relationships except for that selection's
identity. Other dependency, environment, source-declaration or frontend lock
changes remain `DEPENDENCY_CONSUMER_SCOPE_UNVERIFIED`.

The existing native uv integration separately exports both captured locks as
`pylock.toml`, with all groups and extras, excluding only the workspace and
Hashmarks package itself. The frozen, offline native exports must be identical.
This preserves artifact URLs and hashes for every other dependency; a semantic
package-selection delta alone cannot prove those artifact bytes unchanged.
The native exporter owns this comparison input, with no second uv lock parser.

`integrations/hashmarks/dependency_verification.py` captures exact base/current
lock evidence and the actually loaded wheel's distribution metadata. The native
producer owns package selection, module ownership, source discovery, static
imports and source-to-verifier nominations. The adapter requalifies its current
projection and rechecks the lock, discovered sources, metadata and loaded
implementation before returning it.

The named policy requires complete, current and untruncated evidence for the
lock and the admitted Python source members in `integrations/hashmarks/`, with
one selected package and no unavailable source members. The existing architecture
owner separately enforces that static production Hashmarks imports enter through
this boundary. Native discovery must exactly cover the integration Python members
in the existing canonical public-source inventory; a context rule cannot silently
hide a consumer from the accepted scope. For every producer-observed consumer,
the policy selects its first available native direct verifier, or its first available bounded indirect
verifier when no direct verifier exists. An unlinked consumer refuses admission.
The full native observations and advisory nominations remain in the plan; the
composed dependency-input observations name their package-to-source-to-verifier
basis explicitly.

This is a bounded sufficiency decision owned by Oh-Goon. It does not establish
the absence of dynamic imports, custom loaders or consumers outside the admitted
scope, and it does not turn bounded owner search into complete owner coverage.
Hashmarks' global consumer incompleteness and inadmissible negative evidence are
retained. The original native pytest execution and independent base controller
still require the selected proofs to execute successfully. The existing native
package check also runs `backend-lock-check` before building the package, so uv
remains the manifest-to-lock consistency owner.
