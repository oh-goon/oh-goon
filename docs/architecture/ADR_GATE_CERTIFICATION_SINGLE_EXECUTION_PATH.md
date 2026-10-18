# ADR — Certification on the one existing execution path

**Status:** Accepted as a development architecture/acceptance contract, **not**
Gate A/B/C approval, independent sign-off, or permission to run Agents.
**Parent:** P1B27B2B3B2B3B. **Current development bundle:** P1B27_GATE_P1P2A.

## Decision

The original Resolver → Enforcer RESOURCE_INTENT → Fleet/LACE sandbox →
original Linux shared read-only observation → original LACE fact owner →
Enforcer classifier → terminal seal → Game Tape projection is the **only**
implementation and qualification path. Diagnostic preflights and native
mechanical tests may supply bounded observations; they never publish original
signed facts, re-decide resource meaning, declare absence, or authorize work.

**No "certification harness" may build a parallel resource allocator, policy
resolver, signer, mount scanner, teardown state machine or Gate B authority.**
The existing `QUALIFY_B27_EFFECTFUL=1` mode exercises original Bubblewrap FD
mount building. Its native execution result is a *mechanical witness*, not a
certificate of provenance, custody, teardown or network isolation.

## Boundaries and minimum independent receipts

| Gate | Original owner / evidence | Required before PASS | Current |
|---|---|---|---|
| Trust / Gate A | Enforcer trust resolver + independently provisioned custodians | Independently anchored Trust Authority Registry, human-reviewed T0B2 identity/custody and exclusive T1/T2 real authority provisioning + revocation/rotation evidence | BLOCKED; local source census is not deployment custody |
| Physical Gate B | Original Fleet/LACE owner, same signed RESOURCE_INTENT, actual Linux sandbox | Effective `--bind-fd` and sealed FD consumption, signed attempted Run/Step resource-to-kernel mount/namespace/cgroup/network correspondence, complete live holder set, independent physical-fact custody; real cancel/crash/revoke, surviving-holder and bounded teardown disappearance with no UNKNOWN; exact host+source receipts | BLOCKED; P1P2A only checks *positive* kernel-snapshot consistency against the signed native inventory |
| Agent AG-03/04 | Existing Enforcer/LACE workload envelope and physical sandbox | Before work: complete model/provider/MCP/tool disclosure, permitted capabilities, egress rules, DNS/secret/exfiltration negative tests, cutover/revocation; no prompt-only containment | BLOCKED; Agent effects denied |
| Outcome Gate C | Existing Enforcer classifier and one terminal seal | Proven precedence, binding failure/teardown classification, no competing terminal writer; independently tested failure/recovery paths and exact signed facts from Gate B | BLOCKED |
| APP PASS | Existing product acceptance | Full exact-source test selection, required front/backend/UI/integration, native physical qualification and release inputs; auditable JUnit/host/source identity, no substitution of older PASS | BLOCKED |
| Release | Existing release acceptance + independent human decision | Gate A/B/C, AG-03/04, APP PASS, consistent manifest, signed authorized release, independent retained receipts | NOT RELEASE |

**Independent requirements stay independent.** Shared source changes and one
verification path can bundle P1 (resource lineage) and P2 (physical observation
contract), but P3 (real teardown absence), T0B2 (custody), AG-03/04 (physical
egress), Gate C and release require distinct acceptance evidence. A component
with `NOT_OBSERVED` is *not* absent; synthetic/fault-injection passes are *not*
physical proof; a successful local `uv`/pytest does *not* prove native WSL.

## This bundle: P1/P2A narrow cut

The original signed Run/Step/native inventory already constrains a typed
physical sampling envelope. The original shared Linux reader can report
`tracked_mount_namespace` and `visible_mount_fd_holders` as independently
stable facts. The original Fleet owner now cross-checks any *positive* mount
namespace claim against the signed native PID/birth/mount namespace roster,
and cross-checks any non-UNKNOWN FD probe's positive kernel target fingerprint
and namespace against that same tracked mount claim. Contradiction rejects
before projection and before the second Fleet validation cut. The same
snapshot, same observer and same immutable envelope remain in force.

A missing/unavailable/UNKNOWN native probe remains unqualified and can never
be interpreted as absence, independent provenance or effects permission.
No additional effectful work, signing, journal mutation, scanner or observer
custodian is introduced. The ordinary original consumer remains the owner.

## Required next physical P1/P2/P3 work

1. Freeze actual original FD source inode/kernel mount ID at the instant the
   existing native Bubblewrap command allocates mounts, with the same signed
   RESOURCE_INTENT and unique original Step attempt. Reject missing or replayed
   identities before effects. Real kernel evidence must be captured from
   the original execution, not reconstructed by an unsanctioned diagnostic.
2. Extend the original signed Run resource envelope and original single LACE
   physical-fact publisher to cover complete mounted runtime/writable/tool/
   agent-source paths, surviving holders, namespace/cgroup/network obligations,
   and exact kernel provenance. Preserve independently held observer custody.
3. Execute exact-source real Linux/WSL hostile tests: argv spoofing, native FD
   substitution, crash, orphan, external abort, revocation and surviving
   mount/namespace/FD holders. After bounded teardown, independently corroborate
   absence. No UNKNOWN, no old-source promotion, no diagnostic fact signer.
4. Maintain the separate T0B2/T1/T2 custody and provisioning path; certify
   Agent disclosure and egress physically rather than by prompting; complete
   existing Gate C, APP and release acceptance without new semantic owners.

## Evidence commands and failure behavior

From a fresh candidate extraction on the target WSL/Linux host:

```bash
QUALIFY_B27_EFFECTFUL=1 make qualify-development-wsl \
  QUALIFY_EVIDENCE_DIR=../OH_GOON_P1B27_GATE_P1P2A_NATIVE_EVIDENCE
```

The command must fail, not skip, if effectful required `bwrap` or namespace
support is missing. Keep the full native receipt zip with exact source hash,
locked environment, test selection and per-file hashes. A hosted runner without
`bwrap` must report `NOT_RUN`, never Gate B PASS. Source-only review does not
provision independent keys or replace security gates.
