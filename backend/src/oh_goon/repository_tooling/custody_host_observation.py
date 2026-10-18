"""T0B2B2A: bounded, read-only Linux custody *observation*, never attestation.

Only records a selected PID as seen by this host at this instant. The tool
cannot establish that the PID controls a signing key, belongs to an external
custodian, or is independent from the operator who invoked it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key, load_ssh_public_key


SCHEMA = 'oh-goon/trust-custody-native-observation/v1'
MAX_PROC_BYTES = 32768
MAX_PUBLIC_KEY_BYTES = 8192


def _fd_read(directory: int, name: str, *, limit: int = MAX_PROC_BYTES) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('non-regular or oversized observation input')
        raw = os.read(fd, limit + 1)
        if len(raw) > limit:
            raise ValueError('oversized observation input')
        return raw
    finally:
        os.close(fd)


def _proc_start_time(blob: bytes) -> int:
    # /proc/<pid>/stat field 2 (comm) may contain parentheses and spaces.
    close = blob.rfind(b')')
    if close < 0 or not re.match(rb'^\d+ \(', blob):
        raise ValueError('invalid /proc stat')
    tail = blob[close + 2:].split()  # starts at field 3
    if len(tail) <= 19 or not tail[19].isdigit():
        raise ValueError('incomplete /proc stat')
    return int(tail[19])


def _principal(blob: bytes) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for line in blob.decode('utf-8', 'strict').splitlines():
        for field in ('Uid', 'Gid'):
            if line.startswith(field + ':'):
                parts = line.split(':', 1)[1].split()
                if len(parts) != 4 or not all(x.isascii() and x.isdigit() for x in parts):
                    raise ValueError('incomplete /proc credential identity')
                result[field.lower()] = [int(x) for x in parts]
    if set(result) != {'uid', 'gid'}:
        raise ValueError('missing /proc credential identity')
    return result


def _public_key_fingerprint(path: Path) -> dict[str, object]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        st_before = os.fstat(fd)
        if not stat.S_ISREG(st_before.st_mode) or st_before.st_nlink != 1 or st_before.st_size > MAX_PUBLIC_KEY_BYTES:
            raise ValueError('public key must be a single regular bounded file')
        blob = os.read(fd, MAX_PUBLIC_KEY_BYTES + 1)
        if len(blob) > MAX_PUBLIC_KEY_BYTES:
            raise ValueError('public key too large')
        if blob.startswith(b'-----BEGIN'):
            if not blob.startswith(b'-----BEGIN PUBLIC KEY-----'):
                raise ValueError('only public-key material is accepted')
            key = load_pem_public_key(blob)
        else:
            try:
                key = load_ssh_public_key(blob)
            except (ValueError, UnsupportedAlgorithm) as exc:
                raise ValueError('invalid Ed25519 public key') from exc
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError('only Ed25519 public keys accepted; private material prohibited')
        st_after = os.fstat(fd)
        if (st_before.st_dev, st_before.st_ino, st_before.st_mtime_ns, st_before.st_size) != (
            st_after.st_dev, st_after.st_ino, st_after.st_mtime_ns, st_after.st_size):
            raise ValueError('public key changed during observation')
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        raw = key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        return {'ed25519_public_key_sha256': hashlib.sha256(raw).hexdigest(),
                'file_uid': st_after.st_uid, 'file_gid': st_after.st_gid,
                'file_mode': stat.S_IMODE(st_after.st_mode)}
    finally:
        os.close(fd)


def observe_native_custody(*, root: Path, subject: str, pid: int,
                           public_key: Path | None = None, proc_root: Path = Path('/proc')) -> dict[str, object]:
    """Sample real Linux facts without making a custody or trust decision."""
    if type(pid) is not int or pid <= 0 or pid > 2**31 - 1:
        raise ValueError('PID must be a positive numeric process identifier')
    policy_blob = (root / 'backend/trust-authorities.json').read_bytes()
    register_blob = (root / 'backend/trust-custody-evidence.json').read_bytes()
    policy = json.loads(policy_blob)
    register = json.loads(register_blob)
    if (register['policy_sha256'] != hashlib.sha256(policy_blob).hexdigest()
            or subject not in register['subjects']
            or register['subjects'][subject]['review_state'] != 'UNREVIEWED'):
        raise ValueError('unregistered or already changed custody evidence subject')
    fd = os.open(proc_root / str(pid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st_before = os.fstat(fd)
        start_before = _proc_start_time(_fd_read(fd, 'stat'))
        principal = _principal(_fd_read(fd, 'status'))
        namespaces = {}
        for name in ('user', 'mnt', 'pid', 'net'):
            # These are kernel procfs namespace links; reading the target does
            # not inspect any private data or grant a namespace handle.
            value = os.readlink(f'ns/{name}', dir_fd=fd)
            if re.fullmatch(re.escape(name) + r':\[[0-9]+\]', value) is None:
                raise ValueError('invalid namespace identity')
            namespaces[name] = value
        start_after = _proc_start_time(_fd_read(fd, 'stat'))
        st_after = os.fstat(fd)
        if (start_before != start_after or st_before.st_dev != st_after.st_dev
                or st_before.st_ino != st_after.st_ino):
            raise ValueError('PID identity changed during observation')
    finally:
        os.close(fd)
    # Bind to a boot incarnation; PID+start_time alone can repeat after reboot.
    boot_id = (proc_root / 'sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
    if not re.fullmatch(r'[0-9a-fA-F-]{36}', boot_id):
        raise ValueError('missing kernel boot identity')
    return {
        'schema': SCHEMA, 'classification': 'UNTRUSTED_LOCAL_PROCESS_OBSERVATION',
        'effect_permission': 'NONE', 'runtime_authority': False,
        'custody_proven': False, 'independent_custodian_proven': False,
        'gate_a': 'BLOCKED', 'disposition': 'UNDECIDED',
        'policy_sha256': hashlib.sha256(policy_blob).hexdigest(),
        'register_sha256': hashlib.sha256(register_blob).hexdigest(),
        'subject': subject, 'pid': pid, 'boot_id': boot_id,
        'pid_starttime_ticks': start_before, 'principals': principal,
        'namespace_links': namespaces,
        'public_key': None if public_key is None else _public_key_fingerprint(public_key),
        'limitations': ('Locally sampled process identity only; does not attest signer possession, '
                        'independent OS custody, compromise isolation, provisioning or revocation'),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Read-only native trust-custody host observation (NOT Gate A proof)')
    parser.add_argument('--subject', required=True)
    parser.add_argument('--pid', required=True, type=int)
    parser.add_argument('--public-key', type=Path)
    args = parser.parse_args()
    try:
        data = observe_native_custody(root=Path(__file__).resolve().parents[4],
                                     subject=args.subject, pid=args.pid, public_key=args.public_key)
    except (OSError, ValueError, KeyError, UnicodeError, TypeError) as exc:
        print(f'BLOCKED: native custody observation unavailable: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(data, sort_keys=True, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
