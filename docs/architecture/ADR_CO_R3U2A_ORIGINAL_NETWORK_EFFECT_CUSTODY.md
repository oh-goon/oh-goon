# ADR CO-R3U2A — Seal original restricted-egress effects, not a second LACE implementation

**Status (2026-10-15):** Implemented and hosted-tested fail-before-effect
security change. Independently enforced Linux/WSL custody: NOT PROVEN.

## Threat and decision

CO-R3U1 blocked real cgroup allocation/removal and the original journal
native orphan-reaper callback, but the normal active restricted-egress lease
still used raw `ip netns`, `ip link` and `nft` operations. Root privilege,
allocator file locks, process-bound namespaces and signed journal intent
cannot stop a hostile actor from replacing/reusing namespace and link names
during destructive operations. Cleanup after an exception was another cut.
This permitted effects without independent physical custody.

Retain the **one original network effect owner** in
`integrations/_fleet_plan_restricted_egress.py`. Import and use the **same
non-issuing** `require_independent_native_parent_custody` from the original
LACE/cgroup owner. Guard all native entrypoints BEFORE any state mutation:

| Original entry | First protected effect |
|---|---|
| `RestrictedEgressLease.create` | admission before lease construction |
| `open` | netns link creation and egress rules file |
| `_open_link` | original allocator lock, pending record and `ip netns add` |
| `_run_setup` | any `ip`/`nft` command from a direct helper call |
| `_write_egress_lease_record` | state directory + pending recovery record |
| `close` | teardown/record unlink including error rollback |
| `_cleanup_egress_resources` | direct nft/netns/link deletion |

The original journal reaper keeps its independent early gate and custody
recovery record. No second signer/authority, reaper, lookup, diagnostic,
parser, or execution path. Read-only network route enumeration/JSON parsing
remains nonauthoritative; host root checks are not issuance.

## Qualification separation

The currently non-issuing LACE gate always rejects with `FleetPlanInvalid`.
There is **no** local "custody satisfied" flag. No temporary `True` branch,
test-file/kernel detection, environment option, local pid/file lock or mock
identity may grant native work. Tests that simulate a future provisioned host
must explicitly monkeypatch the gate only to exercise existing downstream
failure/absence parsers; never count those tests as physical proof.

The CO-R3U2B host phase must independently establish exclusive parent,
namespace and kernel resource mutation custody, authenticated Run/Step/epoch
and executor-generation binding, revocation at effect-time, identity-safe
destructive calls and native hostile controls. An unqualified `rmdir` remains
unqualified regardless of repeated inode probes. After a failed native
effect, retain pending recovery; no false teardown or global absence.

**Hashmarks:** no dependency or work assigned. No Hashmarks observation can
make the physical gate pass.
