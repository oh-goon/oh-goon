"""AG-02B2B1 original causal-journal identity-only prework reservation.

Development fixtures use TEST keys. No physical, provisioned or effect permission.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from oh_goon.core.enforcer_lace_trust import AdmittedRunContract, admission_receipt, digest
from oh_goon.runtime.enforcer_lace_admitted_signer_epoch import (
    append_original_admitted_signer_epoch_in,
)
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from oh_goon.runtime.enforcer_lace_signer_trust_epoch import SignerTrustGrant
from oh_goon.services.agent_authoring_sources import reserve_verified_agent_source_in
from tests.fixtures.db1b2_legacy_sqlite import CausalRunJournal
from tests.unit.core.test_enforcer_lace_trust import authenticator, authority
from tests.unit.services.test_ag02b_typed_agent_prework_handoff import _case


def fixture(tmp_path: Path, *, unsigned=False, effect_step=None,
            envelope_effects="match", effect_limits="match"):
    if effect_step is None:
        resolver, plan, kw, _, _ = _case(tmp_path)
    else:
        resolver, plan, kw, _, _ = _case(tmp_path, step=effect_step)
    handoff = resolver.prepare_agent_step_handoff(plan, **kw)
    packet = handoff.encode_prework_delivery()
    path = tmp_path / "authority.sqlite"
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    journal = CausalRunJournal(conn)
    journal.initialize_schema()
    conn.executescript("""
        CREATE TABLE execution_lease (singleton INTEGER PRIMARY KEY, lease_token TEXT,
            kind TEXT, fleet_run_id TEXT, cancel_requested_at TEXT, owner_session_id TEXT);
        INSERT INTO execution_lease VALUES (1,'lease-one','FLEET','run-one',NULL,'session-one');
        CREATE TABLE lace_signer_trust_epoch (singleton INTEGER PRIMARY KEY,
            trust_epoch INTEGER, policy_identity TEXT, root_identity TEXT, state TEXT);
    """)
    grant = SignerTrustGrant(1, digest('test-policy',b'dev'), digest('test-roots',b'dev'))
    conn.execute("INSERT INTO lace_signer_trust_epoch VALUES (1,?,?,?,'ACTIVE')",
                 (grant.epoch, grant.policy_identity, grant.root_identity))
    original = authority('run-one')
    envelope = {'workload_kind': 'agent', 'agent_handoff_identity': handoff.handoff_identity}
    if envelope_effects == "match":
        if effect_step is not None:
            envelope['agent_requested_effects'] = handoff.requested_effects.model_dump(mode='json')
    elif envelope_effects != "missing":
        envelope['agent_requested_effects'] = envelope_effects
    # Independent Enforcer ceiling is a separate, signed issuer choice. The
    # ordinary test fixture chooses a matching ceiling; adversarial tests can
    # omit or narrow it, regardless of the AG-02 requested-effect bytes.
    if effect_limits == "match":
        if effect_step is not None:
            envelope['agent_effect_limits'] = handoff.requested_effects.model_dump(mode='json')
    elif effect_limits != "missing":
        envelope['agent_effect_limits'] = effect_limits
    original['step_envelopes'] = {handoff.step_id: envelope}
    original['step_teardown_envelopes'] = {handoff.step_id: {'network':'none'}}
    contract = AdmittedRunContract.freeze(original)
    auth = authenticator()
    conn.execute('BEGIN IMMEDIATE')
    journal.publish_admission_in(conn, contract, admission_receipt(auth, contract), auth,
                                 control_epoch=1, lease_identity='lease-one',
                                 operational_admission=lambda _: None)
    if not unsigned:
        append_original_admitted_signer_epoch_in(journal, run_id='run-one', auth=auth, grant=grant)
    conn.execute('COMMIT')
    args = dict(epoch=1, lease_identity='lease-one', auth=auth, signer_grant=grant,
                step_id=handoff.step_id, attempt_identity='sha256:'+'a'*64,
                executor_generation=8, packet=packet,
                expected_handoff_identity=handoff.handoff_identity)
    return conn, journal, args, packet


def reserve(conn, journal, args, *, fail_after=False):
    conn.execute('BEGIN IMMEDIATE')
    try:
        seq = reserve_verified_agent_source_in(journal, 'run-one', **args)
        if fail_after:
            raise RuntimeError('crash')
    except BaseException:
        conn.execute('ROLLBACK')
        raise
    else:
        conn.execute('COMMIT')
        return seq


def test_original_journal_reserves_signed_identity_once_without_plaintext(tmp_path):
    conn, journal, args, packet = fixture(tmp_path)
    assert reserve(conn, journal, args) == 3
    events = journal.verify('run-one', auth=args['auth'])
    binding = events[-1]['payload']['binding']
    assert events[-1]['kind'] == 'AGENT_SOURCE_PREWORK_RESERVED'
    assert binding['packet_identity'] == digest('oh-goon/agent-source-prework-packet/v1', packet)
    assert binding['handoff_identity'] == args['expected_handoff_identity']
    assert binding['authority'] == 'PREWORK_NOT_ADMITTED'
    assert binding['effect_permission'] == 'NONE'
    assert b'private source' not in b''.join(r[0] for r in conn.execute('SELECT event_payload FROM lace_run_events'))
    with pytest.raises(CausalJournalError, match='replay denied'):
        reserve(conn, journal, args)
    assert len(journal.verify('run-one', auth=args['auth'])) == 3
    conn.close()
    reopened = sqlite3.connect(tmp_path/'authority.sqlite', isolation_level=None)
    assert CausalRunJournal(reopened).verify('run-one', auth=args['auth'])[-1]['payload']['binding'] == binding
    reopened.close()


@pytest.mark.parametrize('attack', [
    'unsigned', 'stale-epoch', 'wrong-lease', 'wrong-run', 'cancelled', 'recovery-only',
    'generation', 'step', 'changed-expected', 'changed-packet', 'wrong-signer',
    'revoked-signer', 'wrong-attempt', 'no-lease',
])
def test_original_custody_negative_vectors_fail_before_any_journal_change(tmp_path, attack):
    conn, journal, args, packet = fixture(tmp_path, unsigned=attack=='unsigned')
    if attack == 'stale-epoch': args['epoch'] = 2
    if attack == 'wrong-lease': args['lease_identity'] = 'foreign'
    if attack == 'wrong-run': conn.execute("UPDATE execution_lease SET fleet_run_id='other'")
    if attack == 'cancelled': conn.execute("UPDATE execution_lease SET cancel_requested_at='now'")
    if attack == 'recovery-only': conn.execute("UPDATE execution_lease SET owner_session_id='recovery:session'")
    if attack == 'generation': args['executor_generation'] = 9
    if attack == 'step': args['step_id'] = 'another-step'
    if attack == 'changed-expected': args['expected_handoff_identity'] = 'agent-step-prework-handoff-v1-'+'0'*64
    if attack == 'changed-packet': args['packet'] = packet.replace(b'"data_b64":"', b'"data_b64":"Z', 1)
    if attack == 'wrong-signer': args['auth'] = authenticator('another-key')
    if attack == 'revoked-signer': conn.execute("UPDATE lace_signer_trust_epoch SET state='REVOKED'")
    if attack == 'wrong-attempt': args['attempt_identity'] = 'other'
    if attack == 'no-lease': conn.execute('DELETE FROM execution_lease')
    before = tuple(conn.execute('SELECT event_kind,event_payload FROM lace_run_events ORDER BY run_seq'))
    with pytest.raises(Exception):
        reserve(conn, journal, args)
    after = tuple(conn.execute('SELECT event_kind,event_payload FROM lace_run_events ORDER BY run_seq'))
    assert before == after
    conn.close()


def test_caller_transaction_and_crash_roll_back(tmp_path):
    conn, journal, args, _ = fixture(tmp_path)
    with pytest.raises(CausalJournalError, match='transaction'):
        reserve_verified_agent_source_in(journal, 'run-one', **args)
    with pytest.raises(RuntimeError, match='crash'):
        reserve(conn, journal, args, fail_after=True)
    assert len(journal.verify('run-one', auth=args['auth'])) == 2
    assert reserve(conn, journal, args) == 3
    conn.close()


def test_tampering_original_source_signature_is_rejected_on_journal_verify(tmp_path):
    conn, journal, args, _ = fixture(tmp_path)
    reserve(conn, journal, args)
    payload = conn.execute('SELECT event_payload FROM lace_run_events WHERE run_seq=3').fetchone()[0]
    # Native SQLite malicious modification without a valid MAC must not authenticate.
    import json

    from oh_goon.core.enforcer_lace_trust import canonical_bytes
    from oh_goon.runtime.enforcer_lace_journal import _event_identity
    altered = json.loads(payload)
    altered['binding']['packet_bytes'] += 1
    raw = canonical_bytes(altered)
    prev = conn.execute('SELECT previous_identity FROM lace_run_events WHERE run_seq=3').fetchone()[0]
    ident = _event_identity('run-one', 3, 'AGENT_SOURCE_PREWORK_RESERVED', raw, prev)
    conn.execute('UPDATE lace_run_events SET event_payload=?,event_identity=? WHERE run_seq=3',(raw, ident))
    conn.execute('UPDATE lace_run_authority SET head_identity=? WHERE run_id=?',(ident,'run-one'))
    with pytest.raises(Exception, match='authentication'):
        journal.verify('run-one', auth=args['auth'])
    conn.close()


def test_x0_single_original_source_receiver_and_single_journal_publisher():
    """Reproducible regression: never duplicate the decode or signed fact owner."""
    import ast

    from oh_goon.repository_tooling.architecture.repository_layout import ROOT
    base = ROOT / 'backend/src/oh_goon'
    service = base / 'services/agent_authoring_sources.py'
    journal_path = base / 'runtime/enforcer_lace_journal.py'
    service_tree = ast.parse(service.read_text())
    journal_tree = ast.parse(journal_path.read_text())
    bridge = [n for n in service_tree.body if isinstance(n, ast.FunctionDef)
              and n.name == 'reserve_verified_agent_source_in']
    assert len(bridge) == 1
    calls = [ast.unparse(n.func) for n in ast.walk(bridge[0]) if isinstance(n, ast.Call)]
    imports = {
        alias.asname or alias.name: f"{node.module}.{alias.name}"
        for node in ast.walk(service_tree) if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    resolved_calls = [
        imports.get(call.split('.', 1)[0], call.split('.', 1)[0])
        + ('.' + call.split('.', 1)[1] if '.' in call else '')
        for call in calls
    ]
    assert resolved_calls.count(
        'oh_goon.core.models.agent_source.FrozenAgentStepHandoff.decode_prework_delivery'
    ) == 1
    assert calls.count('journal.reserve_agent_source_prework_in') == 1
    journals = [n for n in journal_tree.body if isinstance(n, ast.ClassDef)
                and n.name == 'CausalRunJournal']
    assert len(journals) == 1
    methods = [n for n in journals[0].body if isinstance(n, ast.FunctionDef)
               and n.name == 'reserve_agent_source_prework_in']
    assert len(methods) == 1
    assert [ast.unparse(n.func) for n in ast.walk(methods[0])
            if isinstance(n, ast.Call)].count('self._append_locked') == 1
    assert not any(isinstance(n, ast.ImportFrom) and (n.module or '').startswith('oh_goon.services')
                   for n in ast.walk(journal_tree))
    # No other original production owner publishes this event or calls its API.
    callers = []
    publishers = []
    for directory in ('services', 'integrations', 'runtime', 'api'):
        for file in (base / directory).rglob('*.py'):
            tree = ast.parse(file.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'reserve_agent_source_prework_in':
                    callers.append(file.relative_to(base).as_posix())
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == '_append_locked' and any(
                    isinstance(a, ast.Constant) and a.value == 'AGENT_SOURCE_PREWORK_RESERVED' for a in node.args
                ):
                    publishers.append(file.relative_to(base).as_posix())
    assert callers == ['services/agent_authoring_sources.py']
    assert publishers == ['runtime/enforcer_lace_journal.py']
