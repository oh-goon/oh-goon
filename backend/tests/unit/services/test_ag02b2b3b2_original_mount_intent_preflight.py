"""AG-02B2B3B2: original Fleet signed-stage -> mount-descriptor preflight.

This is *not* an effectful mount, physical Gate B, or Agent execution test.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
from oh_goon.core.path_authority import storage_component
from oh_goon.core.ports.original_mount_intent import OriginalMountIntentDescriptor
from oh_goon.core.vocabulary import FleetRunEventType
from tests.unit.runtime.test_ag02b2b3_original_agent_attempt_staging import attempt
from tests.unit.services.test_ag02b2b2_original_owned_attempt_source import make_case


def attempt_owner_for_renamed_root(owner, root):
    from dataclasses import replace

    from oh_goon.core.path_layouts import AttemptPathLayout
    return replace(owner, paths=AttemptPathLayout.from_root(root))

def live_case(tmp_path, monkeypatch):
    conn, journal, lifecycle, session, expected, kw, events, attempt_id, args = make_case(
        tmp_path, monkeypatch,
    )
    handoff = FrozenAgentStepHandoff.decode_prework_delivery(
        kw['packet'], expected_handoff_identity=kw['expected_handoff_identity'],
    )
    owner = attempt(tmp_path, handoff)
    parent = owner.paths.root.parent
    parent.rename(parent.with_name(storage_component(handoff.step_id)))
    owner = attempt_owner_for_renamed_root(owner, parent.with_name(storage_component(handoff.step_id)) / "01")
    kw['source_attempt'] = owner
    assert lifecycle.reserve_original_agent_source_for_attempt(session, expected, **kw) == 3
    assert [e['kind'] for e in journal.verify('run-one', auth=args['auth'])][-2:] == [
        'AGENT_SOURCE_PREWORK_RESERVED', 'AGENT_SOURCE_PREWORK_STAGED',
    ]
    inspect = {key: kw[key] for key in ('repository_id', 'operation_id', 'step_id', 'attempt_number')}
    inspect.update(source_attempt=owner, handoff=handoff)
    return conn, journal, lifecycle, session, expected, inspect, events, args


def test_signed_original_step_stage_produces_only_readonly_descriptor(tmp_path, monkeypatch):
    conn, journal, lifecycle, session, expected, inspect, events, args = live_case(tmp_path, monkeypatch)
    before = tuple(conn.execute('SELECT event_kind,event_payload FROM lace_run_events ORDER BY run_seq'))
    descriptor = lifecycle.inspect_original_agent_source_mount_for_attempt(session, expected, **inspect)
    assert type(descriptor) is OriginalMountIntentDescriptor
    assert descriptor.binding_targets == ('/opt/oh-goon/agent-source',)
    assert descriptor.sandbox_bindings[0].source == str(inspect['source_attempt'].paths.root / 'agent-source-prework')
    assert descriptor == lifecycle.inspect_original_agent_source_mount_for_attempt(session, expected, **inspect)
    assert tuple(conn.execute('SELECT event_kind,event_payload FROM lace_run_events ORDER BY run_seq')) == before
    assert not (inspect['source_attempt'].paths.root / 'agent-source-execution').exists()
    conn.close()


@pytest.mark.parametrize('attack', [
    'cancelled', 'revoked', 'foreign-lease', 'authority-changed',
    'missing-operation-attempt', 'terminal', 'wrong-attempt-number',
    'wrong-repository', 'wrong-step', 'staging-file-tamper',
    'extra-file', 'stage-substitution', 'foreign-handoff',
    'missing-stage', 'missing-reservation', 'changed-generation',
])
def test_no_mount_intent_on_bad_original_authority_or_staging(tmp_path, monkeypatch, attack):
    conn, journal, lifecycle, session, expected, inspect, events, args = live_case(tmp_path, monkeypatch)
    root = inspect['source_attempt'].paths.root
    if attack == 'cancelled':
        conn.execute("UPDATE execution_lease SET cancel_requested_at='now'")
    elif attack == 'revoked':
        conn.execute("UPDATE lace_signer_trust_epoch SET state='REVOKED'")
    elif attack == 'foreign-lease':
        session._lease = type(session._lease)(token='other', kind='FLEET', fleet_run_id='run-one')
    elif attack == 'authority-changed':
        lifecycle.require_execution_authority_in = lambda *_: (_ for _ in ()).throw(RuntimeError('revoke'))
    elif attack == 'missing-operation-attempt':
        events.clear()
    elif attack == 'terminal':
        events.append(SimpleNamespace(event_type=FleetRunEventType.STEP_FAILED,
                                      step_id=inspect['step_id'], sequence=2))
    elif attack == 'wrong-attempt-number':
        inspect['attempt_number'] += 1
    elif attack == 'wrong-repository':
        inspect['repository_id'] = 'other-repository'
    elif attack == 'wrong-step':
        inspect['step_id'] = 'wrong-step'
    elif attack == 'staging-file-tamper':
        file = next(p for p in (root / 'agent-source-prework' / 'repository').rglob('*') if p.is_file())
        original_mode = file.stat().st_mode & 0o777
        file.chmod(original_mode | 0o200)
        file.write_bytes(b'tampered')
        file.chmod(original_mode)
    elif attack == 'extra-file':
        skill = root / 'agent-source-prework' / 'skill'
        original_mode = skill.stat().st_mode & 0o777
        skill.chmod(original_mode | 0o200)
        (skill / 'bad.txt').write_text('exfiltrate')
        skill.chmod(original_mode)
    elif attack == 'stage-substitution':
        staged = root / 'agent-source-prework'
        staged.rename(root / 'old-stage')
        staged.mkdir(mode=0o700)
    elif attack == 'foreign-handoff':
        inspect['handoff'] = SimpleNamespace(step_id=inspect['step_id'])
    elif attack == 'missing-stage':
        # Cannot delete a chain element and keep signed journal valid.
        conn.execute("DELETE FROM lace_run_events WHERE run_id='run-one' AND event_kind='AGENT_SOURCE_PREWORK_STAGED'")
    elif attack == 'missing-reservation':
        conn.execute("DELETE FROM lace_run_events WHERE run_id='run-one' AND event_kind='AGENT_SOURCE_PREWORK_RESERVED'")
    elif attack == 'changed-generation':
        expected.execution.placement.executor_capacity.executor_generation = 99
    before = tuple(conn.execute('SELECT event_payload FROM lace_run_events ORDER BY run_seq'))
    with pytest.raises(Exception):
        lifecycle.inspect_original_agent_source_mount_for_attempt(session, expected, **inspect)
    assert tuple(conn.execute('SELECT event_payload FROM lace_run_events ORDER BY run_seq')) == before
    conn.close()


def test_single_original_fleet_owner_and_no_native_effects():
    import ast

    from oh_goon.repository_tooling.architecture.repository_layout import ROOT
    file = ROOT / 'backend/src/oh_goon/services/fleet_run_lifecycle.py'
    tree = ast.parse(file.read_text())
    cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == 'FleetRunLifecycle')
    methods = [x for x in cls.body if isinstance(x, ast.FunctionDef)
               and x.name == 'inspect_original_agent_source_mount_for_attempt']
    assert len(methods) == 1
    calls = [ast.unparse(n.func) for n in ast.walk(methods[0]) if isinstance(n, ast.Call)]
    assert calls.count('session.mutate') == 1
    assert calls.count('journal.verify') == 1
    assert calls.count('source_attempt._inspect_agent_prework_readonly_mount') == 1
    assert not any(any(bad in c for bad in (
        'subprocess', 'os.system', 'os.exec', 'socket.', 'sqlite3.connect',
        'publish_local_resource_intent', 'bubblewrap', '.execute(',
    )) for c in calls)
