"""B12E-C2: declared sandbox mount plan checked against original signed intent.

An exact signed *plan* is not native mount allocation, observer custody,
physical absence, or proof that all mounts have been enumerated.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re

from oh_goon.core.path_layouts import SANDBOX_PATHS

from oh_goon.core.enforcer_lace_trust import canonical_bytes, digest


def _path(value: str) -> bool:
    return (type(value) is str and value.startswith('/') and len(value) <= 4096
            and '\x00' not in value and not any(ord(c) < 32 for c in value)
            and not any(item in {'.', '..'} for item in value.split('/'))
            and str(PurePosixPath(value)) == value)


def _hash(value: str) -> bool:
    return type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def require_unshadowed_agent_mount_targets(targets: tuple[str, ...], agent_target: str) -> None:
    """One canonical native mount-tree rule for the Agent's read-only source.

    Root ('/') is the base filesystem and must precede every other bind.
    Every other ancestor or descendant of Agent source would shadow its
    source or enable a writable child overlay, even with a read-only parent.
    """
    for target in targets:
        if target in (agent_target, '/'):
            continue
        if (target.startswith(agent_target + '/')
                or agent_target.startswith(target.rstrip('/') + '/')):
            raise ValueError('original Agent source mount must not be shadowed')


@dataclass(frozen=True, slots=True)
class OriginalSandboxMount:
    source: str
    target: str
    sha256: str

    def __post_init__(self) -> None:
        if not (_path(self.source) and _path(self.target) and _hash(self.sha256)):
            raise ValueError('noncanonical original sandbox mount binding')


@dataclass(frozen=True, slots=True)
class OriginalHostToolMount:
    name: str
    source: str
    sha256: str

    def __post_init__(self) -> None:
        if (type(self.name) is not str or not self.name or len(self.name) > 256
                or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,255}', self.name) is None
                or self.name in {'.', '..'}
                or not _path(self.source) or not _hash(self.sha256)):
            raise ValueError('noncanonical original host tool snapshot')


@dataclass(frozen=True, slots=True)
class OriginalNativeMount:
    """Pre-effect source/target/mode of one native bind, not proof of a kernel mount."""

    source: str
    target: str
    mode: str

    def __post_init__(self) -> None:
        if not (_path(self.source) and _path(self.target)
                and self.mode in ('--bind', '--ro-bind')):
            raise ValueError('noncanonical original native source mount')


@dataclass(frozen=True, slots=True)
class OriginalMountIntentDescriptor:
    """Immutable original request descriptor; must hash to signed RESOURCE_INTENT.

    Covers managed inputs, frozen host tools (including /bin/sh alias),
    optional read-only workspace and signed predictable native root/writable/etc
    sources. It does NOT claim FD-backed/dynamic mounts or kernel object identity.
    """
    sandbox_bindings: tuple[OriginalSandboxMount, ...]
    host_tools: tuple[OriginalHostToolMount, ...]
    read_only_workspace: OriginalSandboxMount | None = None
    native_mounts: tuple[OriginalNativeMount, ...] = ()
    agent_source_mount: OriginalSandboxMount | None = None

    def __post_init__(self) -> None:
        if (type(self.sandbox_bindings) is not tuple or type(self.host_tools) is not tuple
                or len(self.sandbox_bindings) > 64 or len(self.host_tools) > 64
                or any(type(x) is not OriginalSandboxMount for x in self.sandbox_bindings)
                or any(type(x) is not OriginalHostToolMount for x in self.host_tools)
                or len({x.target for x in self.sandbox_bindings}) != len(self.sandbox_bindings)
                or len({x.name for x in self.host_tools}) != len(self.host_tools)
                or (self.read_only_workspace is not None and (
                    type(self.read_only_workspace) is not OriginalSandboxMount
                    or self.read_only_workspace.target != '/workspace'))):
            raise ValueError('ambiguous or unbounded original mount intent')
        # P1/B27B2A: the same original signed mount identity also commits
        # predictable native root, writable and generated /etc sources. These
        # are NOT a physical mount observation and do not include dynamic FDs.
        if (type(self.native_mounts) is not tuple or len(self.native_mounts) > 24
                or any(type(x) is not OriginalNativeMount for x in self.native_mounts)
                or len({x.target for x in self.native_mounts}) != len(self.native_mounts)
                or (set(x.target for x in self.native_mounts)
                    & set(x.target for x in self.sandbox_bindings))
                or (self.read_only_workspace is not None
                    and any(x.target == '/workspace'
                            and (x.source != self.read_only_workspace.source or x.mode != '--ro-bind')
                            for x in self.native_mounts))):
            raise ValueError('ambiguous or unbounded original native mount authority')
        if self.agent_source_mount is not None:
            a = self.agent_source_mount
            if (type(a) is not OriginalSandboxMount
                    or a.target != '/opt/oh-goon/agent-source'
                    or sum(m.target == a.target and m.source == a.source
                           and m.mode == '--ro-bind' for m in self.native_mounts) != 1
                    or any(m.target == a.target and
                           (m.source != a.source or m.mode != '--ro-bind')
                           for m in self.native_mounts)):
                raise ValueError('Agent source requires original exact read-only native mount')
            require_unshadowed_agent_mount_targets(
                tuple(m.target for m in self.native_mounts)
                + tuple(s.target for s in self.sandbox_bindings)
                + self._host_tool_targets
                + ((self.read_only_workspace.target,) if self.read_only_workspace else ()),
                a.target,
            )
            if any(s.target == a.target or s.target.startswith(a.target + '/')
                   or a.target.startswith(s.target.rstrip('/') + '/')
                   for s in self.sandbox_bindings):
                raise ValueError('Agent source conflicts with managed sandbox mounts')
        elif any(m.target == '/opt/oh-goon/agent-source'
                 or m.target.startswith('/opt/oh-goon/agent-source/')
                 for m in self.native_mounts):
            raise ValueError('unsigned Agent source cannot enter native mounts')
        # P1/B27A: recovery MUST inspect all signed host-tool targets. The
        # Bubblewrap mount builder also creates /bin/sh for an admitted 'sh'
        # tool, so its alias is part of the same immutable observation scope.
        # No separate diagnostic namespace or second mount identity is allowed.
        declared = self.binding_targets
        if len(declared) > 64 or len(set(declared)) != len(declared):
            raise ValueError('ambiguous or unbounded original mount observation scope')
        # A managed directory mount may not shadow the toolchain, its children,
        # /bin/sh, or an optional workspace. Reject before signed admission.
        tool_targets = self._host_tool_targets
        protected = (*tool_targets, *(('/workspace',) if self.read_only_workspace else ()))
        for mount in self.sandbox_bindings:
            for target in protected:
                if (mount.target == target
                        or target.startswith(mount.target.rstrip('/') + '/')
                        or mount.target.startswith(target.rstrip('/') + '/')):
                    raise ValueError('original mount source shadows a protected target')

    @property
    def _host_tool_targets(self) -> tuple[str, ...]:
        paths = tuple(str(SANDBOX_PATHS.toolchain / tool.name) for tool in self.host_tools)
        if any(tool.name == 'sh' for tool in self.host_tools):
            paths += ('/bin/sh',)
        return paths

    @property
    def binding_targets(self) -> tuple[str, ...]:
        targets = tuple(x.target for x in self.sandbox_bindings)
        if self.read_only_workspace is not None:
            targets += (self.read_only_workspace.target,)
        if self.agent_source_mount is not None:
            targets += (self.agent_source_mount.target,)
        # Same exact Bubblewrap --ro-bind targets as _append_toolchain.
        return targets + self._host_tool_targets

    @property
    def resource_identity(self) -> str:
        # Existing command Step identities retain their exact original wire form
        # when no read-only source workspace is requested. When it is requested,
        # its physical Bubblewrap --ro-bind source MUST be committed as well.
        members: dict[str, object] = {
            'sandbox_bindings': [
                {'source': x.source, 'target': x.target, 'sha256': x.sha256}
                for x in self.sandbox_bindings],
            'host_tools': [
                {'name': x.name, 'source': x.source, 'sha256': x.sha256}
                for x in self.host_tools],
        }
        if self.read_only_workspace is not None:
            w = self.read_only_workspace
            members['read_only_workspace'] = {
                'source': w.source, 'target': w.target, 'sha256': w.sha256,
            }
        if self.agent_source_mount is not None:
            a = self.agent_source_mount
            members['agent_source_mount'] = {
                'source': a.source, 'target': a.target, 'sha256': a.sha256,
            }
        if self.native_mounts:
            members['native_mounts'] = [
                {'source': x.source, 'target': x.target, 'mode': x.mode}
                for x in self.native_mounts
            ]
        return digest('oh-goon/b12c/mounts/v1', canonical_bytes(members))
