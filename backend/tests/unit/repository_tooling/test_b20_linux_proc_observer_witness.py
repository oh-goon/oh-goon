"""Read-only native kernel probes; no synthetic success on missing Linux facts."""
import os
from pathlib import Path

from oh_goon.repository_tooling import local_lace_physical_preflight as preflight


def test_actual_linux_proc_birth_boot_and_namespace_handles():
    proc = Path('/proc')
    assert proc.is_dir(), 'BLOCKED: real Linux /proc required'
    boot = (proc / 'sys/kernel/random/boot_id').read_text().strip()
    assert len(boot) == 36, 'BLOCKED: original kernel boot identity unreadable'
    stat = (proc / 'self/stat').read_text()
    assert preflight._birth(stat).isdigit(), 'BLOCKED: process birth unavailable'
    for kind in ('mnt', 'net', 'pid'):
        target = os.readlink(proc / 'self/ns' / kind)
        assert target.startswith(f'{kind}:[') and target.endswith(']')
    assert (proc / 'self/mountinfo').read_text().strip(), 'BLOCKED: mount inventory unavailable'
    assert (proc / 'self/cgroup').read_text().strip(), 'BLOCKED: cgroup inventory unavailable'


def test_real_proc_fdinfo_contains_kernel_mount_reference():
    with Path('/proc/self/mountinfo').open('rb') as stream:
        fdinfo = Path(f'/proc/self/fdinfo/{stream.fileno()}').read_text()
        assert any(line.startswith('mnt_id:') and line.split(':', 1)[1].strip().isdigit()
                   for line in fdinfo.splitlines()), 'BLOCKED: real kernel mnt_id unavailable'
