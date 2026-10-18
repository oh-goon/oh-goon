"""B11 development-only pytest supervisor: exact exit + JUnit + OS-process receipts.

This is NOT an issuer, certification evaluator, or physical LACE attestation.
Run via ``uv run --locked --group dev python -m oh_goon.repository_tooling.b11_ring_supervisor``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Sequence
import xml.etree.ElementTree as ET


def _digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def _junit_evidence(path: Path) -> dict[str, object]:
    """Never infer a pass from a green-looking or incomplete JUnit document."""
    result: dict[str, object] = {'valid': False, 'sha256': _digest(path)}
    if result['sha256'] is None:
        result['reason'] = 'MISSING_JUNIT'
        return result
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        suites = [root] if root.tag == 'testsuite' else (
            list(root.findall('testsuite')) if root.tag == 'testsuites' else []
        )
        if not suites:
            raise ValueError('no pytest suites')
        totals = {key: sum(int(s.attrib.get(key, '0')) for s in suites)
                  for key in ('tests', 'errors', 'failures', 'skipped')}
        all_cases = [case for suite in suites for case in suite.findall('testcase')]
        cases = len(all_cases)
        identities = [(case.attrib.get('classname', ''), case.attrib.get('name', ''))
                      for case in all_cases]
        if len(set(identities)) != len(identities) or any(not name for _, name in identities):
            raise ValueError('duplicate or unnamed JUnit test identity')
        identity_bytes = json.dumps(identities, sort_keys=True).encode('utf-8')
        result['case_identity_sha256'] = hashlib.sha256(identity_bytes).hexdigest()
        result['unique_case_identities'] = len(identities)
        if totals['tests'] <= 0 or totals['tests'] != cases:
            raise ValueError('empty or mismatched test inventory')
        if any(s.find('failure') is not None or s.find('error') is not None
               or s.find('skipped') is not None for suite in suites
               for s in suite.findall('testcase')):
            if not (totals['errors'] or totals['failures'] or totals['skipped']):
                raise ValueError('case-level failure omitted from suite totals')
        result.update(totals)
        result['testcases'] = cases
        result['valid'] = True
    except (ET.ParseError, ValueError, TypeError, OSError) as exc:
        result['reason'] = 'INVALID_JUNIT_' + type(exc).__name__
    return result


def _live_group_members(pgid: int) -> list[int]:
    """Observe Linux process group members even if pytest parent already exited."""
    live: list[int] = []
    if not Path('/proc').is_dir():
        raise RuntimeError('Linux /proc process observation required')
    for item in Path('/proc').iterdir():
        if not item.name.isdecimal():
            continue
        try:
            # /proc/PID/stat has an arbitrary parenthesized command name.
            fields = (item / 'stat').read_text().rsplit(')', 1)[1].split()
            state, group = fields[0], int(fields[2])
            if group == pgid and state not in ('Z', 'X', 'x'):
                live.append(int(item.name))
        except (FileNotFoundError, ProcessLookupError, PermissionError, IndexError, ValueError):
            continue
    return sorted(live)


def _wait_for_quiescence(pgid: int, *, grace_seconds: float = 2.0) -> list[int]:
    """Allow a brief, bounded post-exit drain (forkserver/resource_tracker)."""
    deadline = time.monotonic() + grace_seconds
    while True:
        observed = _live_group_members(pgid)
        if not observed or time.monotonic() >= deadline:
            return observed
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


def _process_details(pids: list[int]) -> list[dict[str, object]]:
    """Minimal OS facts; never disclose test child command arguments."""
    details: list[dict[str, object]] = []
    for pid in pids:
        try:
            raw = Path(f'/proc/{pid}/stat').read_text()
            fields = raw.rsplit(')', 1)[1].split()
            details.append({'pid': pid, 'comm': raw.split('(', 1)[1].rsplit(')', 1)[0],
                            'state': fields[0], 'parent_pid': int(fields[1]),
                            'process_group_id': int(fields[2]),
                            'session_id': int(fields[3]),
                            'birth_ticks': int(fields[19])})
        except (OSError, IndexError, ValueError):
            details.append({'pid': pid, 'observation': 'PROCESS_DISAPPEARED'})
    return details


def _signal_group(pgid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


def _stop_group(process: subprocess.Popen[bytes], grace: float = 2.0) -> list[str]:
    """Signal only the freshly started pytest session, never the caller's group."""
    escalation = ['SIGTERM']
    _signal_group(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    if _live_group_members(process.pid):
        escalation.append('SIGKILL')
        _signal_group(process.pid, signal.SIGKILL)
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    return escalation


class _RingInterrupted(Exception):
    """Supervisor cancellation must still terminate its owned child group."""


def supervise(*, name: str, command: Sequence[str], directory: Path,
              timeout_seconds: float, required_modules: Sequence[str] = ()) -> dict[str, object]:
    """Run an isolated OS group, always write an atomic machine-readable receipt."""
    if not name or not name.replace('_', '').isalnum() or timeout_seconds <= 0:
        raise ValueError('invalid ring name or deadline')
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / f'{name}.log'
    junit = directory / f'{name}.xml'
    receipt = directory / f'{name}.json'
    if receipt.exists() or log.exists() or junit.exists():
        raise FileExistsError('ring evidence must not be overwritten')
    started = time.monotonic()
    returncode: int | None = None
    timed_out = False
    lingering: list[int] = []
    lingering_details: list[dict[str, object]] = []
    lingering_after_cleanup: list[int] = []
    teardown_signals: list[str] = []
    initial_leader_identity: list[dict[str, object]] = []
    leader_identity_changed = False
    interrupted = False
    spawn_error: str | None = None
    process: subprocess.Popen[bytes] | None = None
    previous_term = signal.getsignal(signal.SIGTERM)

    def _on_term(_number: int, _frame: object) -> None:
        raise _RingInterrupted()

    signal.signal(signal.SIGTERM, _on_term)
    try:
        with log.open('xb') as stream:
            try:
                env = dict(os.environ)
                env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
                process = subprocess.Popen(list(command), stdin=subprocess.DEVNULL,
                                           stdout=stream, stderr=subprocess.STDOUT,
                                           start_new_session=True, env=env)
                initial_leader_identity = _process_details([process.pid])
                try:
                    returncode = process.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    teardown_signals.extend(_stop_group(process))
                    returncode = process.poll()
            except (KeyboardInterrupt, SystemExit, _RingInterrupted):
                interrupted = True
            except OSError as exc:
                spawn_error = type(exc).__name__
            finally:
                if process is not None:
                    # Test success is not accepted when descendants survive pytest.
                    after_leader_identity = _process_details([process.pid])
                    if initial_leader_identity and after_leader_identity:
                        before_birth = initial_leader_identity[0].get('birth_ticks')
                        after_birth = after_leader_identity[0].get('birth_ticks')
                        leader_identity_changed = (before_birth is not None and
                                                   after_birth is not None and
                                                   before_birth != after_birth)
                    lingering = _wait_for_quiescence(process.pid)
                    lingering_details = _process_details(lingering)
                    if lingering or process.poll() is None:
                        teardown_signals.extend(_stop_group(process))
                    lingering_after_cleanup = _wait_for_quiescence(process.pid, grace_seconds=0.3)
                    # PID reuse cannot confer process-clean evidence: observed
                    # surviving descendants are always a failing result.
    finally:
        signal.signal(signal.SIGTERM, previous_term)
    junit_data = _junit_evidence(junit)
    # A passing pytest exit/JUnit does not prove that each explicit native
    # observer test file actually contributed a case to the ring.
    observed_modules: set[str] = set()
    if junit_data.get('valid') and required_modules:
        try:
            root = ET.parse(junit).getroot()
            observed_modules = {
                case.attrib.get('classname', '').rsplit('.', 1)[-1]
                for case in root.iter('testcase')
            }
        except (ET.ParseError, OSError):
            observed_modules = set()
    missing_modules = sorted(set(required_modules) - observed_modules)
    junit_data['required_modules'] = sorted(set(required_modules))
    junit_data['missing_required_modules'] = missing_modules
    junit_data['required_modules_valid'] = not missing_modules
    accepted = (not missing_modules and not timed_out and not interrupted and not leader_identity_changed
                and not lingering
                and not lingering_after_cleanup
                and returncode == 0 and junit_data.get('valid') is True
                and all(junit_data.get(k) == 0 for k in ('errors', 'failures', 'skipped')))
    if timed_out:
        verdict = 'TIMEOUT'
    elif interrupted:
        verdict = 'INTERRUPTED'
    elif spawn_error:
        verdict = 'START_FAILED'
    elif leader_identity_changed:
        verdict = 'PROCESS_IDENTITY_CHANGED'
    elif lingering or lingering_after_cleanup:
        verdict = 'LEFTOVER_PROCESSES'
    elif returncode != 0:
        verdict = 'PROCESS_FAILED'
    elif not junit_data.get('valid'):
        verdict = 'INVALID_JUNIT'
    elif missing_modules:
        verdict = 'REQUIRED_TEST_MODULES_MISSING'
    elif not accepted:
        verdict = 'TESTS_NOT_CLEAN'
    else:
        verdict = 'PASS'
    result: dict[str, object] = {
        'schema': 'oh-goon.development-process-ring/v1',
        'classification': 'DEVELOPMENT_ONLY', 'name': name,
        'accepted': accepted, 'verdict': verdict, 'process_exit_code': returncode,
        'timed_out': timed_out, 'interrupted': interrupted, 'spawn_error': spawn_error,
        'process_group_id': process.pid if process else None,
        'initial_process_identity': initial_leader_identity,
        'leader_identity_changed': leader_identity_changed,
        'teardown_signals': teardown_signals,
        'lingering_processes_after_teardown': lingering_after_cleanup,
        'lingering_processes_before_teardown': lingering,
        'lingering_observations': lingering_details,
        'elapsed_seconds': round(time.monotonic() - started, 3),
        'log_sha256': _digest(log), 'junit': junit_data,
        'external_issuer': 'NOT_PROVEN', 'physical_lace': 'NOT_PROVEN',
        'release_authority': 'NONE',
    }
    temporary = directory / f'.{name}.json.tmp'
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write('\n')
    temporary.replace(receipt)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='B11 development-only process-ring supervision')
    parser.add_argument('--name', required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--require-module', action='append', default=[])
    parser.add_argument('pytest_args', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    pytest_args = args.pytest_args
    if pytest_args and pytest_args[0] == '--':
        pytest_args = pytest_args[1:]
    if not pytest_args or args.timeout < 1 or args.timeout > 600:
        parser.error('pytest selection required; timeout must be 1..600 seconds')
    junit_path = args.evidence / f'{args.name}.xml'
    command = [sys.executable, '-m', 'pytest', '-q', *pytest_args,
               f'--junitxml={junit_path}']
    result = supervise(name=args.name, command=command,
                       directory=args.evidence, timeout_seconds=args.timeout,
                       required_modules=args.require_module)
    print('RING_VERDICT=' + result['verdict'] + ' RING_RECEIPT=' + str(args.evidence / f'{args.name}.json'),
          flush=True)
    return 0 if result['accepted'] else 1


if __name__ == '__main__':
    sys.exit(main())
