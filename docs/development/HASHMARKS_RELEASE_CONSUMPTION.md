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
an upgrade can rely on them. Producer adoption still requires complete qualified
consumer evidence and normal execution proof; matching package identity alone
does not qualify an otherwise incomplete dependency change.
