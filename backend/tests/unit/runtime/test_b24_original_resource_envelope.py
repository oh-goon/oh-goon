"""B24: original signed Run resource envelope, still NOT physical absence."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core.enforcer_lace_trust import canonical_bytes, digest
from oh_goon.core.ports.physical_observation import (
    UNVERIFIED_NATIVE_CLASSES,
    OriginalRecoveryPhysicalSample,
    OriginalRunPhysicalEnvelope,
    PhysicalKernelFact,
    PhysicalKernelSnapshot,
)
from oh_goon.integrations import linux_physical_observer
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    check_x0_single_execution_path,
)
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b22_original_physical_sampling import (
    mount_descriptor,
    seeded,
)


def _sample(tmp_path: Path, monkeypatch):
    conn, journal, session, owner, scope = seeded(tmp_path)
    calls = []
    def observe(s, *, proc_root):
        calls.append((s, proc_root))
        return PhysicalKernelSnapshot((
            ('mounts', PhysicalKernelFact('NOT_OBSERVED', 'observer namespace only')),
            ('process', PhysicalKernelFact('PRESENT', 'scoped PID alive')),
        ))
    monkeypatch.setattr(linux_physical_observer, 'observe_kernel', observe)
    before = journal.verify('run-one', auth=authenticator())
    sample = owner.sample_recovery_physical_unqualified(
        intent_run_seq=2, scope=scope, mount_intent=mount_descriptor(), proc_root=tmp_path)
    return conn, journal, session, before, sample, calls


def test_original_resource_envelope_binds_three_signed_ancestors_and_one_read(tmp_path, monkeypatch):
    conn, journal, session, events, sample, calls = _sample(tmp_path, monkeypatch)
    envelope = sample.envelope
    assert type(sample) is OriginalRecoveryPhysicalSample
    assert type(envelope) is OriginalRunPhysicalEnvelope
    assert envelope.signed_intent_identity == digest(
        'oh-goon/b12e/signed-intent-event/v1', canonical_bytes(events[1]))
    allocation = next(e for e in events if e['kind'] == 'RESOURCE_ALLOCATION_OBSERVED')
    assert envelope.allocation_identity == digest(
        'oh-goon/b12e/allocation-observation/v1', canonical_bytes(allocation['payload']['observation']))
    inventory = next(e for e in events if e['kind'] == 'RESOURCE_ORPHAN_INVENTORIED')
    assert envelope.inventory_identity == digest(
        'oh-goon/b12e/original-inventory/v1', canonical_bytes(inventory))
    assert envelope.native_record_identity == inventory['payload']['inventory']['native_record_identity']
    assert envelope.mount_intent_identity == mount_descriptor().resource_identity
    assert envelope.declared_mount_targets == ('/original/mount',)
    assert envelope.unverified_resource_classes == UNVERIFIED_NATIVE_CLASSES
    assert envelope.identity.startswith('sha256:') and len(envelope.identity) == 71
    assert sample.scope.mount_targets == envelope.declared_mount_targets
    assert calls == [(sample.scope, tmp_path)]
    recorded = journal.verify('run-one', auth=authenticator())
    assert session.commit_count == 2 and recorded[:-1] == events
    assert recorded[-1]['kind'] == 'RESOURCE_PHYSICAL_SAMPLE_RECORDED'
    conn.close()


@pytest.mark.parametrize('field,forged', [
    ('observer_custody', 'INDEPENDENT'), ('coverage', 'COMPLETE'),
    ('effect_permission', 'CLEANUP'), ('claim_run_seq', 2),
    ('native_record_identity', 'foreign'), ('declared_mount_targets', ('/extra', '/extra')),
    ('unverified_resource_classes', ()),
])
def test_no_envelope_can_elevate_original_authority(tmp_path, monkeypatch, field, forged):
    conn, _journal, _session, _events, sample, _calls = _sample(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='cannot grant authority'):
        replace(sample.envelope, **{field: forged})
    conn.close()


def test_scope_and_envelope_cannot_be_substituted_in_sample(tmp_path, monkeypatch):
    conn, _journal, _session, _events, sample, _calls = _sample(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='cannot mint physical authority'):
        replace(sample, envelope=replace(sample.envelope, signed_intent_identity='sha256:' + 'f'*64,
                                         run_id='foreign'))
    with pytest.raises(ValueError, match='cannot mint physical authority'):
        replace(sample, scope=replace(sample.scope, mount_targets=('/foreign',)))
    conn.close()


def test_scoped_non_observation_is_never_absence_or_quiescence(tmp_path, monkeypatch):
    conn, _journal, _session, _events, sample, _calls = _sample(tmp_path, monkeypatch)
    assert sample.envelope.observer_custody == 'UNVERIFIED'
    assert sample.envelope.coverage == 'INCOMPLETE_NATIVE_RESOURCE_SCOPE'
    assert sample.envelope.effect_permission == sample.effect_permission == 'NONE'
    assert not sample.physical_absence_proven and not sample.quiescence_proven
    assert not sample.teardown_proven
    assert sample.facts.to_mapping()['mounts']['state'] == 'NOT_OBSERVED'
    conn.close()


def test_x0_requires_one_bound_envelope_and_rejects_missing_transport(tmp_path):
    root = Path(__file__).resolve().parents[4]
    paths = (
        'backend/src/oh_goon/repository_tooling/local_lace_physical_preflight.py',
        'backend/src/oh_goon/integrations/linux_physical_observer.py',
        'backend/src/oh_goon/core/ports/physical_observation.py',
        'backend/src/oh_goon/core/ports/original_mount_intent.py',
        'backend/src/oh_goon/runtime/enforcer_lace_original_physical_sampling.py',
        'backend/src/oh_goon/runtime/enforcer_lace_fleet_resource_owner.py',
        'backend/pyproject.toml',
    )
    for p in paths:
        dest = tmp_path / p
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((root / p).read_bytes())
    from tests.factories.x0_architecture import copy_x0_architecture_sources

    copy_x0_architecture_sources(tmp_path)
    assert check_x0_single_execution_path(tmp_path) == []
    owner = tmp_path / 'backend/src/oh_goon/runtime/enforcer_lace_fleet_resource_owner.py'
    text = owner.read_text()
    assert 'before.subject_identity, scope, facts, before.envelope,' in text
    owner.write_text(text.replace('before.subject_identity, scope, facts, before.envelope,',
                                  'before.subject_identity, scope, facts, None,'))
    assert any('signed-bound once' in issue.message for issue in check_x0_single_execution_path(tmp_path))


def test_native_wsl_observer_ring_requires_b24_and_cleans_before_effects():
    root = Path(__file__).resolve().parents[4]
    script = (root / 'scripts/qualify_b12_development_wsl.sh').read_text()
    assert "embedded='.checkpoint/B24_SOURCE.sha256'" in script
    assert 'required_modules+=(--require-module test_b24_original_resource_envelope)' in script
    assert 'observer_tests+=(tests/unit/runtime/test_b24_original_resource_envelope.py)' in script
    recipe = (root / 'Makefile').read_text().split('qualify-development-wsl:', 1)[1].split('\n\n', 1)[0]
    assert recipe.index('source-transport-clean') < recipe.index('qualify_b12_development_wsl.sh')
