# Shipped OCI input ownership and registry identities

This is the M135 owner audit of the actual shipped OCI templates. It is **not** another
input schema. `templates/plans/oci/**/plan.yml` owns the caller's projected input
interface, the imported Goons own declarations/defaults/constraints, and the canonical
`FleetPlanResolver` owns resolution and explicit transport. See the
[general input resolution contract](TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md).

## Registry identity is intentionally two different inputs

| Input | Owner / destination | Meaning |
| --- | --- | --- |
| `base_registry_hostname` | `oci/image` → `oci_build.registries[].hostname` | Which TLS registry the builder is permitted to pull **digest-pinned base images** from |
| `publish_registry_hostname` | `oci/publish` → `oci_publish.target.registry.hostname` | Which TLS registry the publisher writes the resulting OCI archive to |
| `publish_repository` | `oci/publish` → `oci_publish.target.repository` | Destination repository path within the publication registry, without a mutable tag |

The base-image registry is not the publication registry by default. They may have the
same hostname in a particular run, but that equality is the caller's choice, **not**
a resolver-derived relationship or an alias. Neither hostname is inferred from
`containerfile`, `publish_repository`, local credentials, or an ambient registry setting.
`oci/image` performs no publication, and `oci/publish` accepts the earlier builder's
explicitly published workspace instead of rebuilding the image. Host-backed
`OCI_REGISTRY_USERNAME` / `OCI_REGISTRY_PASSWORD` are secret capability inputs for
publication, never Plan `inputs`.

## Boundary audit: keep choices rather than inventing a parameter cap

These roles are reviewed against the shipped
[`oci/release-deploy` Plan](../../templates/plans/oci/release-deploy/plan.yml),
[`oci/release` Plan](../../templates/plans/oci/release/plan.yml), and
[`oci/publish-image` Goon](../../templates/goons/oci/publish-image/goon.yml).

| Input | Classification | Owning contract and why it stays visible |
| --- | --- | --- |
| `coverage_threshold` | A: caller choice | `python/test`, explicitly forwarded through `python/quality`; not a fixed global threshold |
| `base_registry_hostname` | A: caller choice | `oci/image`; permitted external digest-pinned source registry |
| `publish_registry_hostname` | A: caller choice | `oci/publish`; independent publication destination |
| `publish_repository` | A: caller choice | `oci/publish`; destination namespace/repository |
| `containerfile` | A: caller override with child default | `oci/image`; repository path, default `Dockerfile` |
| `context` | A: caller override with child default | `oci/image`; repository path, default `.` |
| `platform` | A: caller override with child default | `oci/image`; platform choice, default `linux/amd64` |
| `kubernetes_api_hostname` | A: caller choice | `kubernetes/set-image` and `kubernetes/rollout-verify`; chosen cluster/API |
| `namespace` | A: caller choice | Kubernetes release's selected namespace |
| `deployment` | A: caller choice | Kubernetes release's selected Deployment |
| `container` | A: caller choice | Kubernetes release's selected container |

A = genuine Plan-boundary choice. B = derivable from a different canonical input;
C = stable repository configuration; D = child-only implementation detail;
E = an upstream output. **No B/C/D input was found that could be removed
without changing the released contract or guessing a new authority.** Optional
A inputs remain overrideable even when their owning leaf Goon has a default.

An E value **is** already handled correctly: `kubernetes/release.image` is not a
caller input on `oci/release-deploy`. It is structurally bound to
`${{ goons.oci-publish-image.outputs.OCI_REFERENCE }}`; that exact digest-qualified
result exists only after publication. Likewise `oci/publish.source_workspace` is
transported from `${{ goons.oci-image.workspace }}`, not exposed as a caller-supplied
filesystem path. Both are resolved as producer-reference *identities* before work;
actual producer values are not invented during admission.

| Shipped Plan | Caller interface | Reason |
| --- | --- | --- |
| `oci/image` | Build choices only | Does not publish |
| `oci/publish-image` | Build choices + publication coordinates | No quality gate or Kubernetes target |
| `oci/release-candidate` | Build choices + quality threshold | Does not publish |
| `oci/release` | Build, publication, quality | No Kubernetes target |
| `oci/release-deploy` | Build, publication, quality, Kubernetes target | Deployment image supplied by upstream publication output |
| `oci/tag-gate` | Registry/repository/tag/expected digest | **Read-only assertion** of an existing tag; not a publish credential alias or mutable tag creation |

There is deliberately **no arbitrary maximum input count**. A reviewer must show
which single authority makes an input redundant before deleting it, and must
prove negative admission and Step-work isolation for any changed binding. Do not
replace explicit forwarding with implicit inheritance or introduce environment
fallbacks. Resolved egress and typed executor targets must show the exact chosen
source/destination, including when the two registry hostnames differ.
