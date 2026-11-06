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
an upgrade can rely on them. Producer adoption still requires the selected
qualification checks and original native package validation; matching package
identity alone does not prove that unrelated dependencies stayed unchanged.

## Native uv equivalence for Hashmarks-only upgrades

When the backend manifest changes only its Hashmarks requirement, the candidate
must also change `backend/uv.lock`. The existing native uv adapter receives
bounded, captured base and candidate manifest/lock bytes and exports each in
a separate disposable **input-only** project:

```text
uv export --frozen --offline --no-config --no-python-downloads --no-build \
  --format pylock.toml --all-groups --all-extras \
  --no-emit-workspace --no-header --no-annotate \
  --no-emit-package hashmarks
```

The two complete exports must match byte for byte. Omitting Hashmarks from
emission does not prune its transitive dependencies. Therefore a changed
non-Hashmarks package, selected transitive dependency, wheel/sdist URL, or
artifact hash in the exported dependency scope makes this *Hashmarks-only*
upgrade ineligible. Extra dependency groups and optional extras are included.
The check is performed without resolving, installing, or executing candidate
packages; native uv remains the sole artifact interpreter.

Once this named upgrade route is admitted, its manifest and lock inputs do not
also require generic verification-support nominations merely because the manifest
contains pytest configuration. Raw producer roles and uncertainty remain intact;
the required native artifact, smoke, package and policy proofs still apply.
Other changed verification-support sources retain their own qualified consumer
obligation. A pytest-configuration change cannot use the Hashmarks-only route.

Fast admission retains bounded hashes, byte counts, uv version and export scope,
not multi-megabyte export contents. The independently checked-out **trusted
base controller** repeats the native export from the candidate's committed
inputs and compares the retained result, rather than accepting the candidate's
claim. Missing uv, malformed locks, timeouts, truncated output or mismatched
exports refuse the special upgrade path; there is no alternate lock parser,
parallel evidence authority or green fallback.

This proves the equivalence of **uv's exported locked artifact references**
within the all-groups/all-extras scope. It does not claim that downloaded
artifacts were fetched and byte-verified, that unused/orphan lock entries are
covered, or that global runtime impact is complete. Changes outside that
bounded scope need their own independently qualified dependency change, not
a relaxed Hashmarks-only exception.
