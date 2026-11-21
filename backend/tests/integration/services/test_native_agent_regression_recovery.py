"""Native Codex-protocol processes recover through the original protected Runs.

AGENT_HARNESS_COMPATIBILITY disposition: retain the current protected stdin,
typed JSONL, scoped apply, isolation and settlement adaptations unchanged. The
public executable is synthetic; this proves native transport and physical
phase custody, not an installed Codex version, model quality or live account.
This bounded case protects the original native Bubblewrap recovery boundary;
the provider counterpart separately protects HTTP reasoning delivery.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentProvidersConfig,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.integrations.agent_native_execution import native_executable_sha256
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from tests.integration.services.test_agent_regression_recovery import exercise_regression_recovery

pytestmark = [
    pytest.mark.process,
    pytest.mark.no_resume,
    pytest.mark.skipif(
        sys.platform != "linux" or shutil.which("bwrap") is None,
        reason="native protected recovery requires Linux Bubblewrap",
    ),
]

_NATIVE_RECOVERY = r"""#!/usr/bin/python3
import json,os,pathlib,sys
prompt=sys.stdin.buffer.read(); data=json.loads(prompt)
for index,argument in enumerate(sys.argv):
    if argument=='-c' and sys.argv[index+1].startswith('model_instructions_file='):
        assert pathlib.Path(json.loads(sys.argv[index+1].split('=',1)[1])).read_bytes()==prompt
identity=data['identity']; context=data['context']; contract=data['output_contract']
for root in (pathlib.Path(data['working_workspace']['root']),pathlib.Path('/workspace')):
    for target in ('src/code.txt','tests/regression.txt','.git/config','outside-agent-effect'):
        try: (root/target).write_text('UNAUTHORIZED')
        except OSError: pass
        else: raise AssertionError('native reasoning obtained source writes')
assert not (pathlib.Path(os.environ['CODEX_HOME'])/'auth.json').exists()
assert 'NATIVE_RECOVERY_HOST_SECRET' not in os.environ
pathlib.Path(os.environ['TMPDIR'],'native-disposable-state').write_text('scratch')
captured=pathlib.Path(data['captured_repository'])
code=(captured/'src/code.txt').read_text()
stage=code.strip()
manifest=json.loads(pathlib.Path(data['complete_source_manifest']).read_text())
members={item['path']:item for item in manifest['repository']}
member=members['repo/src/code.txt']
if stage=='introduced-regression':
    feedback=context['regression_feedback']
    assert feedback['candidate_identity']==identity['candidate_identity']
    assert feedback['scope_identity']==identity['scope_identity']
    assert feedback['fleet_run_id'] and feedback['operation_id']
    entry=feedback['feedback'][0]
    assert entry['observed_returncode']==1 and entry['attempt']==1
    assert entry['physical_execution_id']
    assert 'Regression assertion failed' in entry['text']
    assert 'introduced-regression' in entry['text']
else:
    assert 'regression_feedback' not in context
if 'oh-goon.agent-review/v1' in contract:
    clean=stage=='repaired'
    record=dict(schema='oh-goon.agent-review/v1',**identity,
        verdict='CLEAN' if clean else 'FINDING',summary='Current protected candidate review',
        reviewed_paths={'complete_scope':identity['scope_identity']},
        findings=[] if clean else [dict(finding_id='original' if stage=='broken' else 'introduced',
            explanation='Original defect' if stage=='broken' else 'Introduced regression',
            evidence=[dict(path=member['path'],sha256=member['sha256'],line=1)])])
elif 'oh-goon.agent-adjudication/v1' in contract:
    refs=[dict(reviewer=r['reviewer'],finding_id=f['finding_id'])
        for r in context['reviews'] for f in r['findings']]
    record=dict(schema='oh-goon.agent-adjudication/v1',
        candidate_identity=identity['candidate_identity'],scope_identity=identity['scope_identity'],
        dispositions=[dict(**ref,decision='repair',rationale='One current scoped defect') for ref in refs],
        queue=[dict(queue_id='current',findings=refs,rationale='One current defect',
            regression_expectation='Verify repaired code and single-line behavior')])
else:
    first=stage=='broken'
    regression='#!/bin/sh\n[ "$(cat src/code.txt)" = repaired ]\n'
    if not first:
        regression+='[ "$(wc -l < src/code.txt | tr -d \' \')" = 1 ]\n'
    test=members.get('repo/tests/regression.txt')
    record=dict(schema='agent-patch-batch-v1',candidate_identity=identity['candidate_identity'],
        summary='Introduce a regression' if first else 'Repair the diagnosed regression',
        regressions=[dict(queue_id='current',path='repo/tests/regression.txt',
            expectation='Verify code and single-line behavior')],
        operations=[dict(action='update',path='repo/src/code.txt',source_sha256=member['sha256'],
            replacement='introduced-regression\n' if first else 'repaired\n'),
            dict(action='create' if first else 'update',path='repo/tests/regression.txt',
                source_sha256=None if test is None else test['sha256'],replacement=regression)])
print('RAW_NATIVE_RECOVERY_PROTOCOL_CANARY',file=sys.stderr)
print(json.dumps(dict(type='turn.started')))
print(json.dumps(dict(type='item.completed',item=dict(type='agent_message',text=json.dumps(record)))))
print(json.dumps(dict(type='turn.completed')))
"""


def test_native_harness_recovers_failed_regression_through_fresh_protected_phases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public = tmp_path / "public-native"
    public.mkdir()
    program = public / "codex"
    program.write_text(_NATIVE_RECOVERY)
    program.chmod(0o755)
    backend = BubblewrapProcessIsolation(shutil.which("bwrap") or "")
    monkeypatch.setenv("NATIVE_RECOVERY_HOST_SECRET", "synthetic-host-only-recovery-secret")
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="protected-regression-recovery-fixture",
        definition=NativeAgentDefinition(executable=str(program), runtime_roots=(str(public),)),
        profile=NativeAgentProfile(
            backend="bubblewrap",
            sandbox_owner="execution_backend",
            allowed_agents=("codex",),
            inference_profile="selected",
        ),
        executable_sha256=native_executable_sha256(program),
        connection=AgentConnection.select(
            AgentProvidersConfig.model_validate(
                {
                    "providers": {
                        "synthetic": {
                            "api": "openai-responses",
                            "base_url": "http://127.0.0.1:12345/v1",
                            "api_key_env": "NATIVE_RECOVERY_HOST_SECRET",
                        }
                    },
                    "models": {"selected": {"provider": "synthetic", "model": "synthetic-native"}},
                }
            ),
            "selected",
        ),
        backend_configuration_id=backend.configuration_identity(),
    )
    report = exercise_regression_recovery(
        tmp_path, monkeypatch, native_agent=selected, process_isolation=backend
    )
    assert report["stop_reason"] == report["projection"]["result"] == "converged"
    assert report["phases"] == [
        "review",
        "adjudicate",
        "repair",
        "regression",
        "review",
        "adjudicate",
        "repair",
        "regression",
        "review",
        "verification",
    ]
    assert len(set(report["run_ids"])) == 10
    assert report["failed_step"]["status"] == "FAILED"
    assert report["failed_step"]["settlement"]["observed_returncode"] == 1
    assert report["candidate"] == "repaired\n" and report["fresh_process_readback"]
    assert report["fresh_process_campaign_agreement"]
    attempts = report["native_attempts"]
    assert len(attempts) == 12
    agents = [attempt for attempt in attempts if attempt["kind"] == "agent"]
    assert len(agents) == 7
    assert all(attempt["physical_execution_id"] for attempt in attempts)
    assert len({attempt["physical_execution_id"] for attempt in attempts}) == 12
    assert report["requests"] == []
    print(
        "NATIVE_AGENT_REGRESSION_RECOVERY="
        + json.dumps(
            {
                "scope": "synthetic public native Codex protocol; no installed-version/model-quality claim",
                "executable_sha256": selected.executable_sha256,
                "run_ids": report["run_ids"],
                "phases": report["phases"],
                "failed_run": report["failed_run"],
                "native_step_count": len(attempts),
                "native_agent_count": len(agents),
                "fresh_process_readback": True,
            },
            sort_keys=True,
        ),
        flush=True,
    )
