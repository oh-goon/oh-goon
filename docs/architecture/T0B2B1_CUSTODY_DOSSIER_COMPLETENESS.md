# T0B2B1 — Complete offline custody review dossiers (not custody certification)

## Architectural owner and boundary

The existing `oh-goon-offline-custody-review` command remains the **sole offline
cryptographic review intake**. Its `inspect_packet()` remains the sole signature,
registered subject, external pin, policy/register and FD-relative evidence verifier.

The new `inspect_dossier()` checks that a review submission covers **every subject
in the ONE source-backed `backend/trust-authorities.json` registry**, including
pending external roles. It derives every directory name as
`sha256(subject.encode('utf-8')).hexdigest()` and requires the exact set of such
directories beneath `DOSSIER/subjects/`. Packet directory FDs are opened relative
to the already-open `subjects` FD using `O_DIRECTORY|O_NOFOLLOW`, passed to the
existing `inspect_packet` verifier and checked for exactly the members in each
signed evidence manifest. Missing, unexpected, swapped, aliased or extra entries
fail closed. No new reviewer signer or second packet-verification implementation
exists.

## File layout

```
/secure/external-dossier/
    subjects/
        <sha256(exact registered subject)>/
            review.json
            review.sig
            <signed evidence member(s)>
        ... one directory for every registered subject
```

All `review.json` bytes and evidence files follow the **unchanged T0B2A packet
contract**. Source and trust-policy files are not modified by inspecting a dossier.

Prepare the locked backend environment once (outside individual test rings) and run:

```sh
uv run --no-sync oh-goon-offline-custody-review \
  --dossier-dir /secure/external-dossier \
  --reviewer-public-key /secure/out-of-band/reviewer.pub \
  --expected-reviewer-key-sha256 <independently-pinned-hex>
```

For offline reviewer disagreement detection only, add:

```sh
  --corroboration-dossier-dir /secure/second-dossier \
  --corroborator-public-key /secure/out-of-band/second-reviewer.pub \
  --expected-corroborator-key-sha256 <independently-pinned-second-hex>
```

The comparison requires distinct pinned public-key digests, two complete
signed review sets and exact agreement of proposed dispositions. Any conflict
is rejected for human adjudication. **Two different public keys do not establish
independent people, organizations, OS security boundaries or deployment custody.**
These checks are anti-drift and intake completeness, not a new requirement for
multiple runtime trust roots or signers.

## Never an authorization

The only successful outcomes are
`COMPLETE_AUTHENTICATED_REVIEW_SET_NOT_CUSTODY_PROOF` and
`AUTHENTICATED_REVIEW_AGREEMENT_NOT_CUSTODY_PROOF`. All outputs explicitly say:

- `gate_a=BLOCKED`; `runtime_authority=false`;
- `approval=NOT_GRANTED`; `T1_migration_authorized=false`;
- `independent_custody_verified=false`.

No signer, root, policy, admitted Run, LACE fact, issuer requirement, Gate A,
Gate B, Gate C or release decision is changed. The source-backed trust registry
and pending custody register remain unchanged. There is one existing architecture
checker, one existing native WSL qualifier and one original Enforcer trust path.

## Next independent work

Actual custody corroboration still needs **externally observed** private-key
holders, public-root custodians, genuine OS/process-principal isolation,
compromise boundaries, rotation/revocation exercises, incident assumptions,
reviewer independence, and adjudicated semantic dispositions. No such real
facts can be established by a synthetic test packet. After an authenticated,
externally approved census, T1 may implement an **exclusive** registry-backed
runtime trust migration, and T2 must qualify Gate A native authority.

X0 and AGENT_PREWORK regressions forbid weakening signature checks, accepting
partial review coverage, tolerating unlisted evidence, allowing contradictory
review decisions to pass or treating the offline utility as runtime authority.
