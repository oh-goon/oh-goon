"""Original Enforcer-only conditional Agent tool grant; not LACE execution authority."""
from __future__ import annotations

import json

import pytest

from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.unit.runtime.test_skill03c2d1_original_tool_effect_cut import _staged
from tests.unit.runtime.test_b12_local_resource_intent import _resources


def _grant(conn, journal, args, **changes):
    kwargs = dict(epoch=1, lease_identity='lease-one', auth=args['auth'],
                  step_id=args['step_id'],
                  operational_attempt_identity=args['attempt_identity'],
                  operational_attempt_root=args['operational_attempt_root'],
                  attempt_number=1, command_ordinal=1,
                  argv=('/toolchain/bin/pytest', '--version'),
                  mounted_tool_names=('pytest',),
                  actual_egress={'mode': 'none'}, read_only_workspace=True)
    kwargs.update(changes)
    conn.execute('BEGIN IMMEDIATE')
    try:
        seq = journal.issue_original_agent_tool_launch_grant_in('run-one', **kwargs)
        conn.execute('COMMIT')
        return seq
    except BaseException:
        conn.execute('ROLLBACK')
        raise


def test_original_signed_positive_scope_grant_is_not_native_effect_authority(tmp_path):
    conn, journal, args = _staged(tmp_path)
    before = journal.verify('run-one', auth=args['auth'])
    seq = _grant(conn, journal, args)
    events = journal.verify('run-one', auth=args['auth'])
    assert len(events) == len(before) + 1
    assert events[-1]['run_seq'] == seq
    assert events[-1]['kind'] == 'AGENT_TOOL_LAUNCH_GRANTED'
    binding = events[-1]['payload']['binding']
    assert binding['authority'] == 'ENFORCER_GRANT_REQUIRES_LACE_RESOURCE_INTENT'
    assert binding['effect_permission'] == 'NONE_UNTIL_LACE'
    assert binding['command_ordinal'] == 1
    assert binding['source_reservation_seq'] < binding['source_stage_seq'] < seq
    # Even a signed Enforcer grant is unusable until real physical RESOURCE_INTENT.
    conn.execute('BEGIN IMMEDIATE')
    try:
        with pytest.raises(CausalJournalError, match='no qualified signed RESOURCE_INTENT'):
            journal.publish_local_resource_intent_in(
                'run-one', epoch=1, lease_identity='lease-one', auth=args['auth'],
                step_id=args['step_id'], attempt_identity=args['attempt_identity'],
                execution_environment_identity='sha256:' + 'a' * 64,
                backend_capability_manifest_identity='sha256:' + 'b' * 64,
                resources=_resources())
    finally:
        conn.execute('ROLLBACK')
    assert journal.verify('run-one', auth=args['auth']) == events
    conn.close()


def test_original_agent_tool_grant_survives_sqlite_reopen_without_reinterpretation(tmp_path):
    import sqlite3
    from oh_goon.runtime.enforcer_lace_journal import CausalRunJournal

    conn, journal, args = _staged(tmp_path)
    _grant(conn, journal, args)
    expected = journal.verify('run-one', auth=args['auth'])
    conn.close()
    reopened = sqlite3.connect(tmp_path / 'authority.sqlite', isolation_level=None)
    assert CausalRunJournal(reopened).verify('run-one', auth=args['auth']) == expected
    reopened.close()


@pytest.mark.parametrize('changes', [
    {'command_ordinal': 0}, {'attempt_number': 0}, {'attempt_number': 2},
    {'operational_attempt_identity': 'sha256:' + 'f'*64},
    {'argv': ('/bin/sh', '-c', 'pytest')},
    {'mounted_tool_names': ('ruff',)},
    {'read_only_workspace': False},
    {'actual_egress': {'mode': 'host_unrestricted', 'rules': []}},
    {'epoch': 2}, {'lease_identity': 'other'},
])
def test_unscoped_or_stale_effect_cannot_issue_signed_grant(tmp_path, changes):
    conn, journal, args = _staged(tmp_path)
    before = journal.verify('run-one', auth=args['auth'])
    with pytest.raises(CausalJournalError):
        _grant(conn, journal, args, **changes)
    assert journal.verify('run-one', auth=args['auth']) == before
    conn.close()


def test_exact_grant_ordinal_is_single_use_and_distinct_ordinal_can_be_issued(tmp_path):
    conn, journal, args = _staged(tmp_path)
    _grant(conn, journal, args)
    before = journal.verify('run-one', auth=args['auth'])
    with pytest.raises(CausalJournalError, match='replayed'):
        _grant(conn, journal, args)
    assert journal.verify('run-one', auth=args['auth']) == before
    _grant(conn, journal, args, command_ordinal=2)
    assert len(journal.verify('run-one', auth=args['auth'])) == len(before) + 1
    conn.close()


def test_journal_rejects_re_signed_scope_grant_with_changed_source_link(tmp_path):
    from oh_goon.core.enforcer_lace_trust import canonical_bytes
    from oh_goon.runtime.enforcer_lace_journal import _event_identity
    conn, journal, args = _staged(tmp_path)
    seq = _grant(conn, journal, args)
    item = conn.execute('SELECT event_payload,previous_identity FROM lace_run_events WHERE run_id=? AND run_seq=?',
                        ('run-one', seq)).fetchone()
    payload = json.loads(item[0])
    payload['binding']['source_stage_seq'] = seq + 10
    receipt = args['auth'].issue('agent-tool-launch-grant', payload['binding'])
    payload['signature'] = receipt.mac_hex
    encoded = canonical_bytes(payload)
    identity = _event_identity('run-one', seq, 'AGENT_TOOL_LAUNCH_GRANTED', encoded, item[1])
    conn.execute('UPDATE lace_run_events SET event_payload=?,event_identity=? WHERE run_id=? AND run_seq=?',
                 (encoded, identity, 'run-one', seq))
    conn.execute('UPDATE lace_run_authority SET head_identity=? WHERE run_id=?', (identity,'run-one'))
    with pytest.raises(CausalJournalError, match='lacks original source and stage'):
        journal.verify('run-one', auth=args['auth'])
    conn.close()


def test_original_tool_grant_is_issued_only_by_one_original_owner():
    import ast
    from oh_goon.repository_tooling.architecture.repository_layout import ROOT
    owner = ROOT / 'backend/src/oh_goon/runtime/enforcer_lace_fleet_resource_owner.py'
    tree = ast.parse(owner.read_text())
    methods = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'reserve']
    target = next(n for n in methods if any(isinstance(x, ast.Name) and x.id == 'LocalLaceJournalReservation' for x in ast.walk(n)))
    calls = [n.func.attr for n in ast.walk(target) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert calls.count('issue_original_agent_tool_launch_grant_in') == 1
    assert calls.count('mutate') == 1
    assert calls.count('reserve') == 1
