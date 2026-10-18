"""P1/B27A: original signed host-tool mount targets cannot escape recovery scope."""
from __future__ import annotations

from pathlib import Path
from dataclasses import replace

import pytest

from oh_goon.core.ports.original_mount_intent import (
    OriginalHostToolMount, OriginalMountIntentDescriptor, OriginalSandboxMount,
)
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_p1b27a_signed_host_tool_observation,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.integrations import linux_physical_observer
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.unit.runtime.test_b22_original_physical_sampling import seeded
from tests.unit.core.test_enforcer_lace_trust import authenticator

PACKAGE = ROOT / 'backend/src/oh_goon'

def _intent():
    return OriginalMountIntentDescriptor(
        (OriginalSandboxMount('/source', '/inputs/data', 'a'*64),),
        (OriginalHostToolMount('sh', '/host/sh', 'b'*64),
         OriginalHostToolMount('git', '/host/git', 'c'*64)),
    )

def test_host_tools_and_shell_alias_are_in_original_physical_scope():
    intent = _intent()
    assert intent.binding_targets == (
        '/inputs/data', '/toolchain/bin/sh', '/toolchain/bin/git', '/bin/sh',
    )
    # B3B3B2 signed wire is unchanged; no separate physical identity.
    from oh_goon.core.enforcer_lace_trust import canonical_bytes, digest
    assert intent.resource_identity == digest('oh-goon/b12c/mounts/v1', canonical_bytes({
        'sandbox_bindings': [{'source':'/source','target':'/inputs/data','sha256':'a'*64}],
        'host_tools': [
            {'name':'sh','source':'/host/sh','sha256':'b'*64},
            {'name':'git','source':'/host/git','sha256':'c'*64}],
    }))

def test_workspace_is_in_same_original_scope():
    intent = OriginalMountIntentDescriptor(
        (), (OriginalHostToolMount('git', '/bin/git-snapshot', 'a'*64),),
        OriginalSandboxMount('/source', '/workspace', 'b'*64))
    assert intent.binding_targets == ('/workspace', '/toolchain/bin/git')

@pytest.mark.parametrize('name', ['.', '..', 'x/y', '../sh', 'x\\y', '', 'x\x00y', '-trick'])
def test_host_tool_names_cannot_steer_native_mount_targets(name):
    with pytest.raises(ValueError, match='noncanonical'):
        OriginalHostToolMount(name, '/host/snapshot', 'a'*64)

@pytest.mark.parametrize('target', [
    '/toolchain/bin', '/toolchain/bin/sh', '/toolchain/bin/sh/child',
    '/toolchain/bin/git', '/bin', '/bin/sh', '/workspace', '/workspace/child',
])
def test_managed_mount_cannot_shadow_signed_tool_or_workspace(target):
    with pytest.raises(ValueError, match='shadow|ambiguous'):
        OriginalMountIntentDescriptor(
            (OriginalSandboxMount('/source', target, 'a'*64),),
            (OriginalHostToolMount('sh','/sh','b'*64),
             OriginalHostToolMount('git','/git','c'*64)),
            OriginalSandboxMount('/workspace-snapshot', '/workspace', 'd'*64),
        )

def test_original_observer_limit_fails_before_any_oversized_scope():
    tools = tuple(OriginalHostToolMount(f'tool-{i}', f'/tool-{i}', 'a'*64) for i in range(64))
    with pytest.raises(ValueError, match='observation scope'):
        OriginalMountIntentDescriptor((OriginalSandboxMount('/source', '/input', 'b'*64),), tools)

def test_original_owner_rejects_omitted_signed_tool_before_kernel_read(tmp_path, monkeypatch):
    conn, journal, _session, owner, scope = seeded(tmp_path)
    from oh_goon.core.ports.original_mount_intent import OriginalMountIntentDescriptor
    tool_intent = OriginalMountIntentDescriptor(
        (OriginalSandboxMount('/source/snapshot', '/original/mount', 'a'*64),),
        (OriginalHostToolMount('git', '/tool/bin', 'b'*64),))
    calls = []
    monkeypatch.setattr(linux_physical_observer, 'observe_kernel',
                        lambda *a, **kw: calls.append(True))
    with pytest.raises(CausalJournalError, match='mount targets|RESOURCE_INTENT'):
        owner.sample_recovery_physical_unqualified(
            intent_run_seq=2, scope=scope, mount_intent=tool_intent, proc_root=tmp_path)
    assert calls == []
    conn.close()

def test_normal_x0_guard_owns_this_contract():
    assert _check_p1b27a_signed_host_tool_observation(PACKAGE) == []

@pytest.mark.parametrize('mutation', ['omit-tools', 'omit-shell', 'omit-roster', 'omit-alias', 'omit-shadow', 'omit-consumer', 'omit-owner'])
def test_x0_rejects_physical_target_bypass(tmp_path, mutation):
    files = {
        'core/ports/original_mount_intent.py': PACKAGE / 'core/ports/original_mount_intent.py',
        'integrations/fleet_plan_bubblewrap_isolation.py': PACKAGE / 'integrations/fleet_plan_bubblewrap_isolation.py',
        'runtime/enforcer_lace_original_physical_sampling.py': PACKAGE / 'runtime/enforcer_lace_original_physical_sampling.py',
    }
    for rel, source in files.items():
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        code = source.read_text(encoding='utf-8')
        if mutation == 'omit-tools' and rel.startswith('core/'):
            code = code.replace('return targets + self._host_tool_targets', 'return targets', 1)
        if mutation == 'omit-shell' and rel.startswith('core/'):
            code = code.replace("tool.name == 'sh'", "tool.name == 'not-sh'", 1)
        if mutation == 'omit-roster' and rel.startswith('core/'):
            code = code.replace('len(set(declared)) != len(declared)', 'False', 1)
        if mutation == 'omit-alias' and rel.startswith('integrations/'):
            code = code.replace('binding.name == "sh"', 'binding.name == "not-sh"', 1)
        if mutation == 'omit-shadow' and rel.startswith('core/'):
            code = code.replace("raise ValueError('original mount source shadows a protected target')", 'pass', 1)
        if mutation == 'omit-consumer' and rel.startswith('integrations/'):
            code = code.replace('SANDBOX_PATHS.toolchain / binding.name', 'SANDBOX_PATHS.workspace / binding.name', 1)
        if mutation == 'omit-owner' and rel.startswith('runtime/'):
            code = code.replace('scope.mount_targets != mount_intent.binding_targets', 'False', 1)
        dst.write_text(code, encoding='utf-8')
    assert _check_p1b27a_signed_host_tool_observation(tmp_path)


def test_normal_wsl_qualifier_selects_p1b27a_and_enrolls_physical_ring():
    from oh_goon.repository_tooling.architecture.qualification_environment_contract import (
        check_qualification_environment_contract,
    )
    assert not check_qualification_environment_contract(ROOT)
    text = (ROOT / 'scripts/qualify_b12_development_wsl.sh').read_text()
    selector = "if [[ -f .checkpoint/P1B27A_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27A_SOURCE.sha256'; fi"
    assert text.count(selector) == 1
    # P1B27A remains present; newer P1B27B1 source must take precedence.
    assert selector in text.split('if [[ -f "$embedded" ]]', 1)[0]
    assert 'required_modules+=(--require-module test_p1b27a_signed_host_tool_custody)' in text
    assert 'observer_tests+=(tests/unit/runtime/test_p1b27a_signed_host_tool_custody.py)' in text
    assert 'UV_NO_SYNC=1 UV_OFFLINE=1 PIP_NO_INDEX=1' in text


def test_qenv1_rejects_removed_p1b27a_selection(tmp_path):
    from oh_goon.repository_tooling.architecture.qualification_environment_contract import (
        check_qualification_environment_contract,
    )
    shell = (ROOT / 'scripts/qualify_b12_development_wsl.sh').read_text()
    selected = "if [[ -f .checkpoint/P1B27A_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27A_SOURCE.sha256'; fi"
    assert selected in shell
    (tmp_path / 'scripts').mkdir()
    (tmp_path / '.checkpoint').mkdir()
    (tmp_path / 'Makefile').write_bytes((ROOT / 'Makefile').read_bytes())
    (tmp_path / '.checkpoint/P1B27A_SOURCE.sha256').write_text('fixture')
    (tmp_path / 'scripts/qualify_b12_development_wsl.sh').write_text(shell.replace(selected, '', 1))
    assert any('current manifest' in issue.message
               for issue in check_qualification_environment_contract(tmp_path))
