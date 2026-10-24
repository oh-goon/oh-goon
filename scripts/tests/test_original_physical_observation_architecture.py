"""Small public-protocol fixtures for the canonical physical-observation rules.

These fixtures exercise structural ownership, not native execution qualification.
Runtime tests retain signed lineage, kernel races and journal-result proof.
"""

import ast
from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_original_physical_observation_ownership,
    _check_p1p2b1_causal_kernel_ledger,
    _check_runtime_root_ownership,
)
from tests.factories.x0_architecture import write_x0_rule_sources

PACKAGE = "backend/src/oh_goon/"
DIAGNOSTIC = "repository_tooling/local_lace_physical_preflight.py"
SAMPLER = "runtime/enforcer_lace_original_physical_sampling.py"
OWNER = "runtime/enforcer_lace_fleet_resource_owner.py"
PORT = "core/ports/physical_observation.py"
KERNEL = "integrations/linux_physical_observer.py"
RUNTIME_ROOT = "integrations/_fleet_plan_runtime_root.py"

SOURCES = {
    KERNEL: """
def observe_kernel(scope):
    return scope

def renamed_private_helper():
    pass
""",
    DIAGNOSTIC: """
from oh_goon.integrations import linux_physical_observer as reader

def observe(scope):
    received = reader.observe_kernel(scope)
    return {"kernel_snapshot_identity": received.identity}
""",
    SAMPLER: """
from oh_goon.core.ports.physical_observation import OriginalRunPhysicalEnvelope

def bind(signed, declaration, roster):
    if signed["resources"]["mounts"] != declaration.resource_identity:
        raise ValueError()
    return OriginalRunPhysicalEnvelope(observed_processes=roster)

def require_original_sample_kernel_identity(binding, facts):
    pass
""",
    OWNER: """
from oh_goon.integrations import linux_physical_observer as reader
from oh_goon.core.ports.physical_observation import OriginalRecoveryPhysicalSample
from oh_goon.runtime.enforcer_lace_original_physical_sampling import (
    require_original_sample_kernel_identity as validate_identity,
)

class Owner:
    def sample_recovery_physical_unqualified(self, captured, scope, journal):
        observed = reader.observe_kernel(scope)
        validate_identity(captured, observed)
        result = OriginalRecoveryPhysicalSample(captured.envelope)
        journal.record("RESOURCE_PHYSICAL_SAMPLE_RECORDED", result)
        return result
""",
    "core/ports/original_mount_intent.py": "class OriginalMountIntent: pass\n",
    PORT: """
from oh_goon.core.enforcer_lace_trust import canonical_bytes as encode, digest as identify

class PhysicalKernelSnapshot:
    @property
    def identity(self):
        return "snapshot"

class OriginalRunPhysicalEnvelope:
    observed_processes: tuple
    native_cgroup_inode: int
    native_cgroup_device: int

    @property
    def identity(self):
        return "envelope"

    def membership(self):
        return self.observed_processes

class OriginalRecoveryPhysicalSample:
    @property
    def kernel_snapshot_identity(self):
        return self.facts.identity

    @property
    def causal_observation_identity(self):
        return identify("oh-goon/b27/original-physical-candidate/v1", encode({
            "envelope_identity": self.envelope.identity,
            "scope": self.scope,
            "kernel_snapshot_identity": self.facts.identity,
            "mount_intent_identity": self.mount_intent_identity,
            "classification": self.classification,
            "effect_permission": self.effect_permission,
        }))
""",
}


@pytest.fixture
def physical_source(tmp_path: Path) -> Path:
    package = write_x0_rule_sources(
        tmp_path, {PACKAGE + relative: source for relative, source in SOURCES.items()}
    )
    assert _check_original_physical_observation_ownership(tmp_path) == []
    assert _check_p1p2b1_causal_kernel_ledger(package) == []
    return package


def _mutate(path: Path, defect: str) -> None:
    """Introduce a public contract defect independently of private local spelling."""
    tree = ast.parse(path.read_text())
    changed = False

    class Mutation(ast.NodeTransformer):
        def visit_Constant(self, node: ast.Constant) -> ast.AST:
            nonlocal changed
            if (
                defect == "wrong_causal_domain"
                and node.value == "oh-goon/b27/original-physical-candidate/v1"
            ):
                changed = True
                return ast.Constant("different-domain")
            return node

        def visit_Call(self, node: ast.Call) -> ast.AST:
            nonlocal changed
            self.generic_visit(node)
            if (
                defect == "duplicate_reader"
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "observe_kernel"
            ):
                changed = True
                return ast.Tuple(elts=[node, node], ctx=ast.Load())
            if (
                defect == "omit_roster"
                and isinstance(node.func, ast.Name)
                and node.func.id == "OriginalRunPhysicalEnvelope"
            ):
                node.keywords = [
                    k for k in node.keywords if k.arg != "observed_processes"
                ]
                changed = True
            if (
                defect == "omit_kernel_validation"
                and isinstance(node.func, ast.Name)
                and node.func.id == "validate_identity"
            ):
                changed = True
                return ast.Constant(None)
            if defect == "omit_journal_event" and any(
                isinstance(arg, ast.Constant)
                and arg.value == "RESOURCE_PHYSICAL_SAMPLE_RECORDED"
                for arg in node.args
            ):
                changed = True
                return ast.Constant(None)
            if (
                defect == "uncanonical_payload"
                and isinstance(node.func, ast.Name)
                and node.func.id == "encode"
            ):
                changed = True
                return node.args[0]
            return node

        def visit_Compare(self, node: ast.Compare) -> ast.AST:
            nonlocal changed
            if defect == "accept_mismatched_mount" and any(
                isinstance(member, ast.Attribute) and member.attr == "resource_identity"
                for member in ast.walk(node)
            ):
                changed = True
                node.ops = [ast.Eq()]
            return self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
            nonlocal changed
            if (
                (defect == "omit_envelope" and node.attr == "envelope")
                or (defect == "forge_snapshot_identity" and node.attr == "identity")
                or (defect == "omit_membership" and node.attr == "observed_processes")
            ):
                changed = True
                return ast.Constant(None)
            return self.generic_visit(node)

        def visit_Dict(self, node: ast.Dict) -> ast.AST:
            nonlocal changed
            if defect == "forge_causal_envelope":
                for index, key in enumerate(node.keys):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == "envelope_identity"
                    ):
                        changed = True
                        node.values[index] = ast.Constant("forged")
            return self.generic_visit(node)

    Mutation().visit(tree)
    if defect == "parked_causal_domain":
        method = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "causal_observation_identity"
        )
        returned = next(node for node in method.body if isinstance(node, ast.Return))
        assert isinstance(returned.value, ast.Call)
        returned.value.args[0] = ast.Constant("foreign/v1")
        method.body.insert(
            0, ast.Expr(ast.Constant("oh-goon/b27/original-physical-candidate/v1"))
        )
        changed = True
    assert changed, f"fixture does not exercise {defect}"
    path.write_text(ast.unparse(ast.fix_missing_locations(tree)) + "\n")


def test_local_aliases_and_private_helpers_do_not_decide_ownership(
    tmp_path: Path, physical_source: Path
) -> None:
    # The valid fixture deliberately uses different import aliases, local names
    # and helper names from the production sources.
    assert _check_original_physical_observation_ownership(tmp_path) == []


@pytest.mark.parametrize("defect", ["missing_owner", "unreadable_owner"])
def test_uninspectable_owner_is_a_violation(
    tmp_path: Path, physical_source: Path, defect: str
) -> None:
    if defect == "missing_owner":
        (physical_source / "core/ports/original_mount_intent.py").unlink()
    else:
        (physical_source / OWNER).write_text("def invalid syntax\n")
    issues = _check_original_physical_observation_ownership(tmp_path)
    assert len(issues) == 1
    assert "missing" in issues[0].message or "not inspectable" in issues[0].message


@pytest.mark.parametrize(
    "relative,defect,reason",
    [
        (OWNER, "duplicate_reader", "exactly one shared kernel reader"),
        (SAMPLER, "accept_mismatched_mount", "signed intent digest"),
        (OWNER, "omit_envelope", "transported once"),
        (DIAGNOSTIC, "forge_snapshot_identity", "diagnostic projection"),
        (SAMPLER, "omit_roster", "native roster"),
        (PORT, "omit_membership", "membership must remain checked"),
    ],
)
def test_physical_owner_rejects_protocol_drift(
    tmp_path: Path, physical_source: Path, relative: str, defect: str, reason: str
) -> None:
    _mutate(physical_source / relative, defect)
    issues = _check_original_physical_observation_ownership(tmp_path)
    assert len(issues) == 1
    assert reason in issues[0].message


@pytest.mark.parametrize("relative", [PORT, KERNEL])
def test_read_only_observation_cannot_import_execution_authority(
    tmp_path: Path, physical_source: Path, relative: str
) -> None:
    target = physical_source / relative
    target.write_text(target.read_text() + "\nimport oh_goon.runtime\n")
    issues = _check_original_physical_observation_ownership(tmp_path)
    assert len(issues) == 1
    assert "must not import execution/journal authority" in issues[0].message


@pytest.mark.parametrize(
    "source,reason",
    [
        (
            SOURCES[DIAGNOSTIC]
            + "\nfrom os import scandir as inspect_directory\ninspect_directory('/proc')\n",
            "may not duplicate /proc kernel reader",
        ),
        ("def observe(scope): return scope\n", "must use shared kernel observation"),
    ],
)
def test_diagnostic_cannot_own_a_second_kernel_reader(
    tmp_path: Path, physical_source: Path, source: str, reason: str
) -> None:
    (physical_source / DIAGNOSTIC).write_text(source)
    assert any(
        reason in issue.message
        for issue in _check_original_physical_observation_ownership(tmp_path)
    )


@pytest.mark.parametrize(
    "relative,defect,reason",
    [
        (PORT, "forge_causal_envelope", "candidate identity loses causal binding"),
        (PORT, "wrong_causal_domain", "public digest domain"),
        (PORT, "parked_causal_domain", "public digest domain"),
        (PORT, "uncanonical_payload", "canonical public payload"),
        (OWNER, "omit_kernel_validation", "consume one kernel identity validation"),
        (OWNER, "omit_journal_event", "exactly one physical sample event"),
    ],
)
def test_original_causal_identity_and_event_transport_are_preserved(
    physical_source: Path, relative: str, defect: str, reason: str
) -> None:
    _mutate(physical_source / relative, defect)
    issues = _check_p1p2b1_causal_kernel_ledger(physical_source)
    assert len(issues) == 1
    assert reason in issues[0].message


@pytest.mark.parametrize("defect", [None, "capture", "verify"])
def test_runtime_root_keeps_native_snapshot_authority(
    tmp_path: Path, defect: str | None
) -> None:
    package = write_x0_rule_sources(
        tmp_path,
        {
            PACKAGE + RUNTIME_ROOT: """
from oh_goon.integrations.content_snapshot import snapshot_identity as identify, verify_snapshot as verify

def capture_runtime_root(destination):
    return identify(destination)

def verify_runtime_root(binding):
    verify(binding.root, binding.identity)
"""
        },
    )
    assert _check_runtime_root_ownership(package) == []
    if defect:
        target = package / RUNTIME_ROOT
        tree = ast.parse(target.read_text())
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == f"{defect}_runtime_root"
        )
        function.body = [ast.Pass()]
        target.write_text(ast.unparse(ast.fix_missing_locations(tree)))
        issues = _check_runtime_root_ownership(package)
        assert len(issues) == 1
        assert "native snapshot" in issues[0].message
