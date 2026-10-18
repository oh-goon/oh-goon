"""Opt-in, actual Linux UID/thread process smoke (not WSL/host attestation).

Never uses production keys. Runs the original read-only keyring and custody
observer under a real separate unprivileged Linux process, not monkeypatches.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.process

# A fresh non-root OS principal must be available. UID 65534 is reserved for
# this disposable Linux process; no real deployment issuer identities are used.
_SMOKE = '''
import os
import sys
from pathlib import Path
from oh_goon.core.enforcer_lace_issuer_evidence import AUTHORITY_FIELDS
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.runtime.enforcer_lace_issuer_keyring import IssuerKeyReference, load_explicit_issuer_verifier
from oh_goon.services.fleet_prework_provisioner import _native_unprivileged_signer_identity
root = Path(sys.argv[1])
try:
    observation = _native_unprivileged_signer_identity()
    verifier = load_explicit_issuer_verifier(root, {
        field: IssuerKeyReference('test-'+str(n), str(n)+'.pub')
        for n, field in enumerate(sorted(AUTHORITY_FIELDS))
    }, public_root_owner_uid=0)
    assert len(verifier.public_roots()) == 6
    assert not os.access(root / '0.pub', os.W_OK)
    assert observation[0] == 65534 and observation[-5:] == (1, 0, 0, 0, 0)
except TrustProtocolError:
    sys.exit(19)
'''


def test_real_linux_distinct_uid_no_new_privs_zero_caps_and_six_roots():
    if (not sys.platform.startswith("linux") or os.geteuid() != 0
            or shutil.which("setpriv") is None or shutil.which("uv") is None):
        pytest.skip("real non-root Linux credential execution requires root and setpriv/uv")
    # /opt parents are root-managed; avoid `/tmp` which is writable by all.
    if not Path("/opt").is_dir() or not os.access("/opt", os.W_OK):
        pytest.skip("no root-owned fixture ancestry available")
    with tempfile.TemporaryDirectory(prefix="oh-goon-a1h-", dir="/opt") as dirname:
        directory = Path(dirname)
        directory.chmod(0o755)
        for n in range(6):
            (directory / f"{n}.pub").write_bytes(bytes([n + 1]) * 32)
            (directory / f"{n}.pub").chmod(0o644)
        # Use uv as the Python entrypoint in a NEW unprivileged process.
        command = ["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups",
                   "--no-new-privs", "uv", "run", "--no-project", "--offline",
                   "python", "-c", _SMOKE, str(directory)]
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "backend" / "src")
                   + ":" + str(Path(__file__).resolve().parents[2] / "backend"),
                   UV_PYTHON_PREFERENCE="only-system", UV_CACHE_DIR="/tmp/oh-goon-a1h-testcache")
        positive = subprocess.run(command, capture_output=True, text=True,
                                  env=env, timeout=40, check=False)
        assert positive.returncode == 0, positive.stdout + positive.stderr
        no_nnp = subprocess.run([item for item in command if item != "--no-new-privs"],
                                capture_output=True, text=True, env=env, timeout=40, check=False)
        assert no_nnp.returncode == 19, no_nnp.stdout + no_nnp.stderr

# A1k: real kernel counterexample, not just monkeypatched /proc samples.
# Linux PR_SET_NO_NEW_PRIVS is per-thread. A process can have a trustworthy
# *calling* thread even while a sibling retains the ability to gain privileges
# across exec. This is precisely why the original prework observer uses a
# process-wide census instead of /proc/thread-self/status alone.
_SPLIT_NNP_SMOKE = r'''
import ctypes
import os
import sys
import threading
from pathlib import Path
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.services.fleet_prework_provisioner import _native_unprivileged_signer_identity

ready = threading.Event()
stop = threading.Event()
identities = []
def sibling():
    identities.append(threading.get_native_id())
    ready.set()
    stop.wait(15)

worker = threading.Thread(target=sibling)
worker.start()
try:
    if not ready.wait(5):
        raise AssertionError('sibling was not scheduled')
    libc = ctypes.CDLL(None, use_errno=True)
    # PR_SET_NO_NEW_PRIVS = 38. Change only the caller after the sibling has
    # started; a pre-exec setpriv flag would be inherited by *all* threads.
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        raise AssertionError('native PR_SET_NO_NEW_PRIVS failed')
    def nnp(tid):
        lines = Path(f'/proc/self/task/{tid}/status').read_text().splitlines()
        values = [line.split(':', 1)[1].strip() for line in lines
                  if line.startswith('NoNewPrivs:')]
        assert len(values) == 1
        return values[0]
    caller = threading.get_native_id()
    assert nnp(caller) == '1' and nnp(identities[0]) == '0', 'no split native NNP'
    try:
        _native_unprivileged_signer_identity()
    except TrustProtocolError as exc:
        assert 'signer thread retains independent OS authority' in str(exc), str(exc)
        print('REAL_SPLIT_NNP_REJECTED_BEFORE_WORK', flush=True)
        sys.exit(19)
    else:
        print('ERROR_SPLIT_NNP_ADMITTED', flush=True)
        sys.exit(20)
finally:
    stop.set()
    worker.join(timeout=5)
'''


def test_real_linux_nonprivileged_caller_rejects_privileged_sibling_thread():
    """A1k: observable kernel test of process-wide isolation; NOT host attestation."""
    if (not sys.platform.startswith("linux") or os.geteuid() != 0
            or shutil.which("setpriv") is None or shutil.which("uv") is None):
        pytest.skip("real Linux split-thread custody requires root, setpriv and uv")
    command = ["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups",
               "uv", "run", "--no-project", "--offline", "python", "-c", _SPLIT_NNP_SMOKE]
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "backend" / "src")
               + ":" + str(Path(__file__).resolve().parents[2] / "backend"),
               UV_PYTHON_PREFERENCE="only-system", UV_CACHE_DIR="/tmp/oh-goon-a1k-testcache")
    result = subprocess.run(command, capture_output=True, text=True,
                            env=env, timeout=45, check=False)
    assert result.returncode == 19, result.stdout + result.stderr
    assert "REAL_SPLIT_NNP_REJECTED_BEFORE_WORK" in result.stdout

# A1k: a thread-local raw Linux setresuid/setresgid is deliberately used here.
# The NPTL libc wrappers coordinate credential changes across all threads and
# would erase precisely the counterexample this kernel-level regression needs.
_SPLIT_UID_SMOKE = r'''
import ctypes
import os
import sys
import threading
from pathlib import Path
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.services.fleet_prework_provisioner import _native_unprivileged_signer_identity

libc = ctypes.CDLL(None, use_errno=True)
ready = threading.Event()
stop = threading.Event()
sibling_tid = []
def sibling():
    # Both tasks get NoNewPrivs=1, so this specifically catches a saved-root
    # sibling rather than relying on the distinct NoNewPrivs fixture above.
    assert libc.prctl(38, 1, 0, 0, 0) == 0
    sibling_tid.append(threading.get_native_id())
    ready.set()
    stop.wait(15)

worker = threading.Thread(target=sibling)
worker.start()
try:
    assert ready.wait(5)
    assert libc.prctl(38, 1, 0, 0, 0) == 0
    # x86-64 SYS_setresgid=119 and SYS_setresuid=117; raw syscalls affect the
    # calling task without the glibc setxid broadcast to the privileged sibling.
    assert libc.syscall(119, 65534, 65534, 65534) == 0
    assert libc.syscall(117, 65534, 65534, 65534) == 0
    assert os.getresuid() == (65534,) * 3
    assert os.getresgid() == (65534,) * 3
    def uid_nnp(tid):
        fields = {'Uid': [], 'NoNewPrivs': []}
        for line in Path(f'/proc/self/task/{tid}/status').read_text().splitlines():
            key, sep, value = line.partition(':')
            if sep and key in fields:
                fields[key].append(value.strip())
        return fields
    caller = uid_nnp(threading.get_native_id())
    peer = uid_nnp(sibling_tid[0])
    assert caller['Uid'] == ['65534\t65534\t65534\t65534'], caller
    assert peer['Uid'] == ['0\t0\t0\t0'], peer
    assert caller['NoNewPrivs'] == peer['NoNewPrivs'] == ['1']
    try:
        _native_unprivileged_signer_identity()
    except TrustProtocolError as exc:
        assert 'signer thread retains independent OS authority' in str(exc), str(exc)
        print('REAL_SAVED_ROOT_SIBLING_REJECTED_BEFORE_WORK', flush=True)
        sys.exit(19)
    else:
        print('ERROR_ROOT_SIBLING_ADMITTED', flush=True)
        sys.exit(20)
finally:
    stop.set()
    worker.join(timeout=5)
'''


def test_real_linux_caller_drops_root_but_sibling_retains_root():
    """A1k: root sibling remains in the same process; caller-only checks fail."""
    if (not sys.platform.startswith("linux") or os.geteuid() != 0
            or os.uname().machine != "x86_64" or shutil.which("uv") is None):
        pytest.skip("real Linux thread-scoped UID test needs root/x86_64/uv")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "backend" / "src")
               + ":" + str(Path(__file__).resolve().parents[2] / "backend"),
               UV_PYTHON_PREFERENCE="only-system", UV_CACHE_DIR="/tmp/oh-goon-a1k-testcache")
    result = subprocess.run(["uv", "run", "--no-project", "--offline", "python",
                             "-c", _SPLIT_UID_SMOKE], capture_output=True, text=True,
                            env=env, timeout=45, check=False)
    assert result.returncode == 19, result.stdout + result.stderr
    assert "REAL_SAVED_ROOT_SIBLING_REJECTED_BEFORE_WORK" in result.stdout
