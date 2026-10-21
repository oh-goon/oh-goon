"""B20 X0 first-slice: prevent a development preflight becoming execution authority.

Static architecture guard, not runtime policy, authentication or certification.
It rejects direct and literal dynamically-loaded production imports of the
operator-scoped observer and makes the diagnostic CLI's ownership explicit.
"""

from __future__ import annotations

import ast
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation, project_scripts_from_text
from oh_goon.repository_tooling.architecture.repository_layout import ROOT

DIAGNOSTIC_MODULE = "oh_goon.repository_tooling.local_lace_physical_preflight"
DIAGNOSTIC_ENTRYPOINT = "oh-goon-local-lace-physical-preflight"
CONTROL_DIRS = ("services", "runtime", "integrations", "core", "api")
CONTROL_FILES = ("_application_services.py", "cli.py", "container_entrypoint.py")


def _module_reference(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            name.name == DIAGNOSTIC_MODULE or name.name.startswith(DIAGNOSTIC_MODULE + ".")
            for name in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom):
            if node.module == DIAGNOSTIC_MODULE:
                return True
            if node.module == "oh_goon.repository_tooling" and any(
                name.name == "local_lace_physical_preflight" for name in node.names
            ):
                return True
        # Detect literal dynamic-import and shell-dispatch aliases, while not
        # pretending to solve arbitrary data-dependent Python call graphs.
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
            leaf = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            if leaf in ("__import__", "import_module", "run", "Popen", "system", "execv", "execve"):
                if any(
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and (DIAGNOSTIC_MODULE in arg.value or DIAGNOSTIC_ENTRYPOINT in arg.value)
                    for arg in node.args
                ):
                    return True
    return False


def check_x0_single_execution_path(root: Path = ROOT) -> list[Violation]:
    package = root / "backend/src/oh_goon"
    preflight = package / "repository_tooling/local_lace_physical_preflight.py"
    pyproject = root / "backend/pyproject.toml"
    issues: list[Violation] = []
    try:
        scripts = project_scripts_from_text(pyproject.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [
            Violation(pyproject, f"X0 cannot resolve entrypoint ownership: {type(error).__name__}")
        ]
    if scripts.get(DIAGNOSTIC_ENTRYPOINT) != DIAGNOSTIC_MODULE + ":main":
        issues.append(
            Violation(pyproject, "X0 diagnostic CLI must have exactly its nonauthoritative owner")
        )
    if not preflight.is_file():
        issues.append(Violation(preflight, "X0 diagnostic source owner missing"))
        return issues
    try:
        tree = ast.parse(preflight.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as error:
        return issues + [
            Violation(preflight, f"X0 unreadable diagnostic owner: {type(error).__name__}")
        ]
    # B12E-C1 explicitly permits *read-only* shared observation contracts.
    # The old blanket core/integrations ban remains for every other module.
    permitted_shared = {
        "oh_goon.core.ports.physical_observation",
        "oh_goon.integrations.linux_physical_observer",
    }

    def forbidden_cli_import(node: ast.AST) -> bool:
        if isinstance(node, ast.Import):
            return any(
                alias.name.startswith(tuple("oh_goon." + owner + "." for owner in CONTROL_DIRS))
                and alias.name not in permitted_shared
                for alias in node.names
            )
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in permitted_shared:
                return False
            if module == "oh_goon.integrations":
                return any(alias.name != "linux_physical_observer" for alias in node.names)
            return module.startswith(tuple("oh_goon." + owner + "." for owner in CONTROL_DIRS))
        return False

    if any(forbidden_cli_import(node) for node in ast.walk(tree)):
        issues.append(
            Violation(preflight, "X0 diagnostic owner may not import execution/control authority")
        )
    controls = [package / file for file in CONTROL_FILES]
    controls.extend(file for folder in CONTROL_DIRS for file in (package / folder).rglob("*.py"))
    for path in controls:
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            issues.append(Violation(path, "X0 control source cannot be inspected"))
            continue
        if _module_reference(tree):
            issues.append(
                Violation(
                    path,
                    "X0 authority import/dispatch of operator-scoped physical preflight forbidden",
                )
            )
    shared = package / "integrations/linux_physical_observer.py"
    contract = package / "core/ports/physical_observation.py"
    if shared.is_file() or contract.is_file():
        if not (shared.is_file() and contract.is_file()):
            issues.append(
                Violation(shared, "X0 shared kernel mechanism and typed port must both exist")
            )
        else:
            try:
                shared_tree = ast.parse(shared.read_text(encoding="utf-8"))
                cli_tree = ast.parse(preflight.read_text(encoding="utf-8"))
                port_tree = ast.parse(contract.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                issues.append(Violation(shared, "X0 shared observation owner unreadable"))
            else:
                expected = {
                    "_read_regular",
                    "_birth",
                    "_command",
                    "_cgroup",
                    "_cgroup_subtree",
                    "_process",
                    "_mounts",
                    "_network",
                    "_mount_field",
                    "_tracked_namespace_inventory",
                    "_namespace_fd_holders",
                    "_visible_foreign_mount_targets",
                    "_visible_mount_fd_holders",
                    "observe_kernel",
                }
                functions = {
                    n.name
                    for n in shared_tree.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                if not expected <= functions:
                    issues.append(
                        Violation(
                            shared,
                            "X0 single shared Linux kernel implementation missing required primitives",
                        )
                    )
                cli_functions = {
                    n.name
                    for n in cli_tree.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                if cli_functions - {"parse_scope", "_network", "observe", "main"}:
                    issues.append(
                        Violation(
                            preflight, "X0 second physical kernel observer/parser in diagnostic"
                        )
                    )
                if any(
                    isinstance(n, (ast.Import, ast.ImportFrom))
                    and (
                        any(
                            a.name.startswith(
                                (
                                    "oh_goon.runtime",
                                    "oh_goon.services",
                                    "oh_goon.api",
                                    "oh_goon.engine",
                                )
                            )
                            for a in n.names
                        )
                        if isinstance(n, ast.Import)
                        else (n.module or "").startswith(
                            ("oh_goon.runtime", "oh_goon.services", "oh_goon.api", "oh_goon.engine")
                        )
                    )
                    for tree in (shared_tree, port_tree)
                    for n in ast.walk(tree)
                ):
                    issues.append(
                        Violation(
                            shared,
                            "X0 physical kernel mechanism must not import execution/journal authority",
                        )
                    )
                if not any(
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "observe_kernel"
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "linux"
                    for n in ast.walk(cli_tree)
                ):
                    issues.append(
                        Violation(
                            preflight, "X0 diagnostic must use shared kernel observation mechanism"
                        )
                    )
                if any(
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr in ("scandir", "readlink", "open", "fstat")
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "os"
                    for n in ast.walk(cli_tree)
                ):
                    issues.append(
                        Violation(preflight, "X0 diagnostic may not duplicate /proc kernel reader")
                    )
    # B12E-C2 development bridge: ONE original-owner entrypoint and exactly
    # one shared Linux reader. No independent publisher or CLI fallback.
    sampler = package / "runtime/enforcer_lace_original_physical_sampling.py"
    original_owner = package / "runtime/enforcer_lace_fleet_resource_owner.py"
    if sampler.is_file():
        try:
            bind_tree = ast.parse(sampler.read_text(encoding="utf-8"))
            owner_tree = ast.parse(original_owner.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            issues.append(Violation(sampler, "X0 original-owner physical sampling not inspectable"))
        else:
            owner_methods = [
                n
                for n in ast.walk(owner_tree)
                if isinstance(n, ast.FunctionDef)
                and n.name == "sample_recovery_physical_unqualified"
            ]
            kernel_calls = (
                [
                    n
                    for n in ast.walk(owner_methods[0])
                    if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "observe_kernel"
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "linux_physical_observer"
                ]
                if len(owner_methods) == 1
                else []
            )
            if len(kernel_calls) != 1:
                issues.append(
                    Violation(
                        original_owner,
                        "X0 original owner must invoke exactly one shared kernel reader",
                    )
                )
            for tree, path in ((bind_tree, sampler), (owner_tree, original_owner)):
                if _module_reference(tree):
                    issues.append(
                        Violation(path, "X0 original kernel binding cannot invoke development CLI")
                    )
            for n in ast.walk(bind_tree):
                if (
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr
                    in (
                        "_append_locked",
                        "append",
                        "publish",
                        "execute_native_cleanup",
                        "observe_kernel",
                    )
                ):
                    issues.append(
                        Violation(
                            sampler, "X0 sampling binding may not publish, execute or reobserve"
                        )
                    )
    # B23: the one original owner's scoped sampling must compare the declared
    # descriptor with the signed RESOURCE_INTENT *before* reading kernel facts.
    mount_port = package / "core/ports/original_mount_intent.py"
    if (
        package / "runtime/enforcer_lace_original_physical_sampling.py"
    ).is_file() and mount_port.is_file():
        try:
            bound_source = (
                package / "runtime/enforcer_lace_original_physical_sampling.py"
            ).read_text(encoding="utf-8")
            port_tree = ast.parse(mount_port.read_text(encoding="utf-8"))
            source_tree = ast.parse(bound_source)
        except (OSError, SyntaxError):
            issues.append(
                Violation(mount_port, "X0 signed original mount provenance not inspectable")
            )
        else:
            exact_guards = [
                node
                for node in ast.walk(source_tree)
                if isinstance(node, ast.Compare)
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.NotEq)
                and any(
                    isinstance(member, ast.Attribute) and member.attr == "resource_identity"
                    for member in ast.walk(node)
                )
                and any(
                    isinstance(member, ast.Constant) and member.value == "mounts"
                    for member in ast.walk(node)
                )
            ]
            if len(exact_guards) != 1:
                issues.append(
                    Violation(
                        sampler, "X0 original mount scope must match one signed intent digest"
                    )
                )
            if any(
                isinstance(node, (ast.Import, ast.ImportFrom))
                and (
                    (
                        isinstance(node, ast.ImportFrom)
                        and (node.module or "").startswith("oh_goon.runtime")
                    )
                    or (
                        isinstance(node, ast.Import)
                        and any(a.name.startswith("oh_goon.runtime") for a in node.names)
                    )
                )
                for node in ast.walk(port_tree)
            ):
                issues.append(
                    Violation(
                        mount_port, "X0 immutable mount descriptor must not own execution authority"
                    )
                )
            # B24: do not let the owner quietly omit signed event/record lineage
            # from the returned read-only sample, nor manufacture a second
            # resource envelope outside the original authenticated binding.
            envelope_constructors = [
                n
                for n in ast.walk(source_tree)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "OriginalRunPhysicalEnvelope"
            ]
            sample_returns = [
                n
                for n in ast.walk(owner_tree)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "OriginalRecoveryPhysicalSample"
            ]
            bound_envelopes = [
                n
                for call in sample_returns
                for n in ast.walk(call)
                if isinstance(n, ast.Attribute)
                and n.attr == "envelope"
                and isinstance(n.value, ast.Name)
                and n.value.id == "before"
            ]
            if (
                len(envelope_constructors) != 1
                or len(sample_returns) != 1
                or len(bound_envelopes) != 1
            ):
                issues.append(
                    Violation(
                        sampler,
                        "X0 original physical envelope must be signed-bound once and transported once",
                    )
                )
    # B25: exactly ONE typed shared snapshot identity, consumed by both
    # read-only projections. No second kernel digest, parser, or publisher.
    contract = package / "core/ports/physical_observation.py"
    try:
        port_tree = ast.parse(contract.read_text(encoding="utf-8"))
        cli_tree = ast.parse(preflight.read_text(encoding="utf-8"))

        def has_attr(tree, owner: str, attr: str) -> bool:
            return any(
                isinstance(n, ast.Attribute)
                and n.attr == attr
                and isinstance(n.value, ast.Name)
                and n.value.id == owner
                for n in ast.walk(tree)
            )

        identity_methods = [
            n
            for n in ast.walk(port_tree)
            if isinstance(n, ast.FunctionDef) and n.name == "identity"
        ]
        if len(identity_methods) != 2 or not has_attr(cli_tree, "snapshot", "identity"):
            issues.append(
                Violation(
                    contract,
                    "X0 B25 shared snapshot identity must have one port owner and diagnostic projection",
                )
            )
        sample_methods = [
            n
            for n in ast.walk(port_tree)
            if isinstance(n, ast.FunctionDef) and n.name == "kernel_snapshot_identity"
        ]
        if len(sample_methods) != 1 or not any(
            isinstance(n, ast.Attribute)
            and n.attr == "identity"
            and isinstance(n.value, ast.Attribute)
            and n.value.attr == "facts"
            for n in ast.walk(sample_methods[0])
        ):
            issues.append(
                Violation(
                    contract,
                    "X0 B25 original sample must project the same shared snapshot identity",
                )
            )
    except (OSError, SyntaxError):
        issues.append(Violation(contract, "X0 B25 snapshot identity contract not inspectable"))
    # B26 original observed-native process roster is constructed by the SAME
    # original signed inventory binding. No CLI, independent roster loader,
    # physical-fact publisher or second /proc scan may substitute for it.
    try:
        binder_tree = ast.parse(sampler.read_text(encoding="utf-8"))
        port_tree = ast.parse(
            (package / "core/ports/physical_observation.py").read_text(encoding="utf-8")
        )
        constructors = [
            n
            for n in ast.walk(binder_tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "OriginalRunPhysicalEnvelope"
        ]
        owner_classes = [
            n
            for n in port_tree.body
            if isinstance(n, ast.ClassDef) and n.name == "OriginalRunPhysicalEnvelope"
        ]
        fields = {
            node.target.id
            for n in owner_classes
            for node in n.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        if (
            len(constructors) != 1
            or [k.arg for k in constructors[0].keywords].count("observed_processes") != 1
            or {"observed_processes", "native_cgroup_inode", "native_cgroup_device"} - fields
        ):
            issues.append(
                Violation(
                    sampler, "X0 B26 native roster must be bound by one original source owner"
                )
            )
        if not any(
            isinstance(n, ast.Attribute) and n.attr == "observed_processes"
            for n in ast.walk(port_tree)
        ):
            issues.append(
                Violation(sampler, "X0 B26 sampled native process membership must remain checked")
            )
    except (OSError, SyntaxError):
        issues.append(Violation(sampler, "X0 B26 native roster owner not inspectable"))
    issues.extend(_check_co_r3u1_native_custody_fail_before_effect(package))
    issues.extend(_check_co_r3u2a_original_egress_custody(package))
    issues.extend(_check_co_r3u2b1_original_launch_effect_cuts(package))
    issues.extend(_check_co_r3u2b2b1_host_effect_preconditions(package))
    issues.extend(_check_co_r3u2b2b2a_native_qualification_cut(root))
    issues.extend(_check_agent_workload_canonical_owner(root, package))
    issues.extend(_check_original_agent_mount_gate(package))
    issues.extend(_check_original_resource_intent_sequence(package))
    issues.extend(_check_original_signed_mount_consumption(package))
    issues.extend(_check_original_signed_stage_mount(package))
    issues.extend(_check_signed_workspace_mount_projection(package))
    issues.extend(_check_native_custody_observer_non_authority(root))
    issues.extend(_check_p1b27a_signed_host_tool_observation(package))
    issues.extend(_check_p1b27b1_native_mount_roster(package))
    issues.extend(_check_p1b27b2a_signed_native_sources(package))
    issues.extend(_check_p1b27b2b1_kernel_fd_custody(package))
    issues.extend(_check_p1b27b2b2a_source_fd_custody(package))
    issues.extend(_check_p1b27b2b3a_mount_coverage(package))
    issues.extend(_check_p1b27b2b3b1_mount_fd_lifecycle(package))
    issues.extend(_check_p1b27b2b3b2a_mount_fd_correlation(package))
    issues.extend(_check_p1b27b2b3b2b1_holder_race_fences(package))
    issues.extend(_check_p1b27b2b3b2b2_fd_handoff(package))
    issues.extend(_check_p1b27b2b3b2b3b_fd_argv_boundary(package))
    issues.extend(_check_p1p2b1_causal_kernel_ledger(package))
    issues.extend(_check_p1p2b2a_effectful_source_provenance(root))
    issues.extend(_check_p3a_original_sample_rounds(package))
    issues.extend(_check_p3b1_original_convergence_assessment(package))
    issues.extend(_check_p3b2a_complete_postcleanup_roster(package))
    issues.extend(_check_p3b2b_original_native_lifecycle(root))
    issues.extend(_check_p1p2b2b2_canonical_runtime_root(root))
    from oh_goon.repository_tooling.architecture.canonical_database_owner_contract import (
        check_canonical_database_owner,
    )

    issues.extend(check_canonical_database_owner(root))
    return issues


# AG-00: The existing X0 gate owns this check; it is NOT a second policy engine.
# This bounded static guard covers explicitly frozen call edges plus common
# duplicate-agent-authority forms. Runtime/reflection bypasses require separate
# adversarial, provisioned execution tests in AG-02/AG-09.
def _check_agent_workload_canonical_owner(root: Path, package: Path) -> list[Violation]:
    import re

    adr = root / "docs/architecture/ADR_AGENT_WORKLOAD_CANONICAL_EXECUTION.md"
    if not adr.is_file():
        return [Violation(adr, "X0 AG-00 accepted agent ADR missing from enrolled source")]
    issues: list[Violation] = []

    # Trace semantic call targets in precisely the original owners, rather than
    # treating a comment/string containing an expected function name as proof.
    required_edges: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
        (
            "services/fleet_run_control_plane.py",
            "FleetRunControlPlane",
            "start_prepared_plan",
            ("self._start_intent",),
        ),
        (
            "services/fleet_run_control_plane.py",
            "FleetRunControlPlane",
            "start_bound",
            ("self._start_intent",),
        ),
        (
            "services/fleet_run_execution.py",
            "FleetRunExecution",
            "execute",
            ("self._lifecycle.activate", "self._run", "self._lifecycle.finish_detail"),
        ),
        (
            "services/fleet_run_execution.py",
            "FleetRunExecution",
            "_run",
            (
                "self._lifecycle.begin",
                "self._targets.execute",
                "self._lifecycle.local_lace_intent_for",
            ),
        ),
        (
            "integrations/fleet_plan_run_execution.py",
            "FleetPlanRunExecutor",
            "execute_direct_run",
            ("self._step_runtime", "FleetPlanStepSequence", "sequence.run_direct", "self._outcome"),
        ),
        (
            "integrations/fleet_plan_run_execution.py",
            "FleetPlanRunExecutor",
            "execute_strict_run",
            ("self._step_runtime", "FleetPlanStepSequence", "sequence.run_strict", "self._outcome"),
        ),
        (
            "services/fleet_run_execution.py",
            "FleetRunExecution",
            "resume_local_lace_takeover",
            (
                "self._lifecycle.local_lace_recovery_owner",
                "self._lifecycle.original_local_lace_recovery_backend",
                "coordinator.resume",
            ),
        ),
    )
    parsed: dict[Path, ast.AST] = {}
    for relative, klass, method, needed in required_edges:
        path = package / relative
        try:
            tree = parsed.setdefault(path, ast.parse(path.read_text(encoding="utf-8")))
        except (OSError, SyntaxError):
            issues.append(Violation(path, "X0 AG-00 canonical owner missing or not parseable"))
            continue
        owners = [
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == klass
        ]
        definitions = [
            node
            for owner in owners
            for node in owner.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method
        ]
        if len(definitions) != 1:
            issues.append(
                Violation(path, f"X0 AG-00 canonical method {klass}.{method} missing or duplicated")
            )
            continue
        present = {
            ast.unparse(node.func)
            for node in ast.walk(definitions[0])
            if isinstance(node, ast.Call)
        }
        absent = set(needed) - present
        if absent:
            issues.append(
                Violation(
                    path, f"X0 AG-00 canonical call edge {klass}.{method} lost: {sorted(absent)}"
                )
            )

    # New Agent-specific owner surfaces are prohibited; small post-admission
    # argument/capability adapters are permitted, but cannot create effects.
    forbidden_owner = re.compile(
        r"agent.*(runtime|scheduler|orchestrat(or|ion)|executor|execution|launcher|"
        r"admission|admitter|enforcer|journal|factpublisher|physicalobserver|"
        r"recovery|looprunner|terminal|sandboxmanager|policyengine)",
        re.I,
    )
    # These direct effects are forbidden only inside files marketed as agent
    # integrations, never across general existing Fleet backend implementations.
    effect_calls = frozenset(
        {
            "subprocess.run",
            "subprocess.Popen",
            "subprocess.call",
            "subprocess.check_call",
            "subprocess.check_output",
            "os.system",
            "os.popen",
            "os.execv",
            "os.execve",
            "os.spawnv",
            "asyncio.create_subprocess_exec",
            "asyncio.create_subprocess_shell",
            "sqlite3.connect",
            "os.fork",
        }
    )
    for path in package.rglob("*.py"):
        if not any(path.is_relative_to(package / part) for part in CONTROL_DIRS):
            continue
        try:
            source_tree = parsed.get(path) or ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            issues.append(Violation(path, "X0 AG-00 control source cannot be inspected"))
            continue
        agent_file = "agent" in path.stem.lower()
        declarations = (
            node
            for node in ast.walk(source_tree)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        )
        for declaration in declarations:
            if forbidden_owner.search(declaration.name) or (
                agent_file
                and re.search(
                    r"(?:RunExecutor|Scheduler|Enforcer|Journal|PhysicalObserver|LoopRunner|Recovery|SandboxManager|PolicyEngine)$",
                    declaration.name,
                    re.I,
                )
            ):
                issues.append(
                    Violation(
                        path,
                        f"X0 AG-00 agent-specific authority owner forbidden: {declaration.name}",
                    )
                )
        # A narrow agent *adapter* may describe a frozen request, never create
        # a direct process, database or journal bypass itself.
        if agent_file:
            imported: dict[str, str] = {}
            for node in ast.walk(source_tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported[alias.asname or alias.name] = alias.name
                if isinstance(node, ast.ImportFrom) and node.module:
                    for alias in node.names:
                        imported[alias.asname or alias.name] = node.module + "." + alias.name
            for node in ast.walk(source_tree):
                if isinstance(node, ast.Call):
                    target = ast.unparse(node.func)
                    head, sep, tail = target.partition(".")
                    target = imported.get(head, head) + (sep + tail if sep else "")
                    if target in effect_calls or target.endswith(
                        (
                            ".publish_local_resource_intent",
                            ".publish_local_resource_intent_in",
                            ".finish_detail",
                            ".append_fact",
                        )
                    ):
                        issues.append(
                            Violation(path, f"X0 AG-00 agent adapter bypass effect: {target}")
                        )
    pyproject = root / "backend/pyproject.toml"
    try:
        scripts = project_scripts_from_text(pyproject.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        issues.append(Violation(pyproject, "X0 AG-00 cannot inspect project CLI ownership"))
    else:
        for name in scripts:
            if re.search(r"(?:^|[-_])agent(?:$|[-_])", name, re.I):
                issues.append(
                    Violation(pyproject, f"X0 AG-00 separate agent CLI authority forbidden: {name}")
                )
    return issues


# AG-02B2B3B3A: the original X0 gate enforces one shared pre-effect guard;
# new Agent mount modes must not bypass the original LACE intent projection.
def _check_original_agent_mount_gate(package: Path) -> list[Violation]:
    projection_path = package / "integrations/fleet_plan_local_lace_intent.py"
    backend_path = package / "integrations/fleet_plan_bubblewrap_isolation.py"
    issues: list[Violation] = []
    try:
        projection = ast.parse(projection_path.read_text(encoding="utf-8"))
        backend = ast.parse(backend_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return [Violation(projection_path, "X0 Agent mount pre-effect owner unreadable")]

    guards = [
        n
        for n in projection.body
        if isinstance(n, ast.FunctionDef)
        and n.name == "require_original_agent_source_mount_authority"
    ]
    projection_functions = [
        n
        for n in projection.body
        if isinstance(n, ast.FunctionDef) and n.name == "local_resource_identities"
    ]
    backends = [
        n
        for n in backend.body
        if isinstance(n, ast.ClassDef) and n.name == "BubblewrapProcessIsolation"
    ]
    methods = [
        n
        for c in backends
        for n in c.body
        if isinstance(n, ast.FunctionDef) and n.name == "execute"
    ]
    builders = [
        n for n in backend.body if isinstance(n, ast.FunctionDef) and n.name == "_prepare_mounts"
    ]

    def first_guard(functions: list[ast.FunctionDef]) -> bool:
        if len(functions) != 1 or not functions[0].body:
            return False
        body = functions[0].body
        node = body[0]
        if isinstance(node, ast.If):
            # An ordinary typed request has its own proportional physical
            # branch in this same backend. It cannot loosen or skip custody
            # for the original strict request; only pure typed dispatch may
            # precede the strict guard.
            function = functions[0]
            arguments = function.args.args
            if len(arguments) < 2 or len(backends) != 1:
                return False
            receiver, request = arguments[0].arg, arguments[1].arg
            test = node.test
            returned = node.body[0] if len(node.body) == 1 else None
            call = returned.value if isinstance(returned, ast.Return) else None
            if not (
                isinstance(test, ast.Call)
                and isinstance(test.func, ast.Name)
                and test.func.id == "isinstance"
                and len(test.args) == 2
                and not test.keywords
                and isinstance(test.args[0], ast.Name)
                and test.args[0].id == request
                and isinstance(test.args[1], ast.Name)
                and test.args[1].id == "StandardIsolatedProcessRequest"
                and not node.orelse
                and isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == receiver
                and len(call.args) == 1
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id == request
            ):
                return False
            target = next(
                (
                    method
                    for method in backends[0].body
                    if isinstance(method, ast.FunctionDef) and method.name == call.func.attr
                ),
                None,
            )
            if target is None or len(target.args.args) < 2:
                return False
            annotation = target.args.args[1].annotation
            if (
                not isinstance(annotation, ast.Name)
                or annotation.id != "StandardIsolatedProcessRequest"
            ):
                return False
            if len(body) < 2:
                return False
            node = body[1]
        return (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "require_original_agent_source_mount_authority"
        )

    def has_guard(functions: list[ast.FunctionDef]) -> bool:
        return len(functions) == 1 and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "require_original_agent_source_mount_authority"
            for node in ast.walk(functions[0])
        )

    if len(guards) != 1:
        issues.append(Violation(projection_path, "X0 one Agent mount custody guard required"))
    if not has_guard(projection_functions):
        issues.append(
            Violation(
                projection_path,
                "X0 original RESOURCE_INTENT projection must reject unadmitted Agent mounts",
            )
        )
    if not first_guard(methods):
        issues.append(
            Violation(backend_path, "X0 native backend must reject Agent source before effects")
        )
    if not has_guard(builders):
        issues.append(
            Violation(
                backend_path, "X0 original Bubblewrap builder must preserve Agent source gate"
            )
        )
    return issues


def _check_original_resource_intent_sequence(package: Path) -> list[Violation]:
    """X0: protect the ONLY original Step effects predecessor check and call order.

    Not a second state machine: this statically proves that the existing
    journal writer calls its own original history/admission check before any
    RESOURCE_INTENT append. Native/provisioned tests independently test facts.
    """
    path = package / "runtime/enforcer_lace_journal.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return [Violation(path, "X0 original resource-intent order owner unreadable")]
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CausalRunJournal"]
    helpers = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and n.name == "_require_original_resource_intent_step_order"
    ]
    if len(classes) != 1 or len(helpers) != 1:
        return [Violation(path, "X0 one original resource-intent predecessor owner required")]
    writers = [
        n
        for n in classes[0].body
        if isinstance(n, ast.FunctionDef) and n.name == "publish_local_resource_intent_in"
    ]
    if len(writers) != 1:
        return [Violation(path, "X0 exactly one original resource-intent writer required")]
    nested = [n for n in writers[0].body if isinstance(n, ast.FunctionDef) and n.name == "commit"]
    if len(nested) != 1:
        return [Violation(path, "X0 original resource-intent transaction cut required")]
    commit = nested[0]
    # The gate must be a top-level unconditional call within the original
    # writer's commit function, not a branch or a decoy in dead code.
    direct = [
        n
        for n in commit.body
        if isinstance(n, ast.Expr)
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name)
        and n.value.func.id == "_require_original_resource_intent_step_order"
    ]
    append = [
        n
        for n in ast.walk(commit)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "_append_locked"
    ]
    if len(direct) != 1 or len(append) != 1 or direct[0].lineno >= append[0].lineno:
        return [
            Violation(
                path, "X0 signed Step predecessor gate must precede original RESOURCE_INTENT append"
            )
        ]
    # Authenticated original history and admitted Step meaning must be passed
    # to the ONE gate. Do not duplicate or substitute a caller-supplied flag.
    kws = {kw.arg: kw.value for kw in direct[0].value.keywords}
    if (
        set(kws) != {"step_id", "attempt_identity", "step_envelope", "original_events"}
        or not isinstance(kws["original_events"], ast.Name)
        or kws["original_events"].id != "original_events"
        or not isinstance(kws["step_envelope"], ast.Subscript)
        or ast.unparse(kws["step_envelope"]) != "fields['step_envelopes'][step_id]"
    ):
        return [
            Violation(
                path, "X0 original resource-intent order must bind admitted Step and signed history"
            )
        ]
    helper = helpers[0]
    names = [node.value for node in ast.walk(helper) if isinstance(node, ast.Constant)]
    required = {
        "agent",
        "workload_kind",
        "RUN_ADMITTED",
        "AGENT_SOURCE_PREWORK_RESERVED",
        "AGENT_SOURCE_PREWORK_STAGED",
    }
    if not required.issubset(set(names)) or any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Constant)
        and node.test.value is False
        for node in ast.walk(helper)
    ):
        return [
            Violation(path, "X0 original resource-intent order rejects unqualified Agent Steps")
        ]
    # No second module may directly INSERT a physical resource intent or
    # append the reserved journal event. Read-only journal queries remain
    # permitted; only additional publishers are forbidden.
    for other in package.rglob("*.py"):
        if other == path:
            continue
        try:
            other_tree = ast.parse(other.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue  # existing source syntax gates report unreadable modules
        for node in ast.walk(other_tree):
            if not isinstance(node, ast.Call):
                continue
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "_append_locked"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "RESOURCE_INTENT"
            ):
                return [Violation(other, "X0 parallel RESOURCE_INTENT journal publisher forbidden")]
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in {"execute", "executemany", "executescript"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and "INSERT INTO LACE_RUN_EVENTS" in node.args[0].value.upper().replace("  ", " ")
            ):
                return [Violation(other, "X0 second original Run journal publisher forbidden")]
    return []


def _check_original_signed_mount_consumption(package: Path) -> list[Violation]:
    """X0: one journal-signed mount identity reaches the one Bubblewrap builder.

    This checks existing owner edges, not a new execution state machine. Native
    mount/FD custody and physical teardown require independent evidence.
    """
    intent = package / "runtime/enforcer_lace_local_resource_intent.py"
    backend = package / "integrations/fleet_plan_bubblewrap_isolation.py"
    try:
        intent_tree = ast.parse(intent.read_text(encoding="utf-8"))
        backend_tree = ast.parse(backend.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return [Violation(intent, "X0 signed mount transport owners unreadable")]

    def function(tree: ast.AST, name: str) -> list[ast.FunctionDef]:
        return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]

    def calls(node: ast.AST, name: str) -> list[ast.Call]:
        return [
            n
            for n in ast.walk(node)
            if isinstance(n, ast.Call)
            and (
                (isinstance(n.func, ast.Name) and n.func.id == name)
                or (isinstance(n.func, ast.Attribute) and n.func.attr == name)
            )
        ]

    owners = function(intent_tree, "reserve")
    execute = function(backend_tree, "execute")
    command = function(backend_tree, "_command")
    builder = function(backend_tree, "_prepare_mounts")
    bridge = function(backend_tree, "_require_original_signed_mount_identity")
    if not all(len(nodes) == 1 for nodes in (owners, execute, command, builder, bridge)):
        return [
            Violation(
                intent, "X0 exactly one signed mount reservation and Bubblewrap consumer required"
            )
        ]

    owner, executor, command_fn, builder_fn, _ = (
        owners[0],
        execute[0],
        command[0],
        builder[0],
        bridge[0],
    )
    text = ast.unparse(owner)
    if not (
        len(calls(owner, "local_resource_identities")) == 1
        and "events[-1]['payload'].get('resources') != original_resources" in text
        and "mounts_identity=original_resources['mounts']" in text
    ):
        return [
            Violation(intent, "X0 proof must carry exact original signed journal mount identity")
        ]

    prechecks = calls(executor, "_require_original_signed_mount_identity")
    allocations = calls(executor, "create")
    reservation = calls(executor, "reserve")
    if (
        len(prechecks) != 1
        or not allocations
        or len(reservation) != 1
        or not (reservation[0].lineno < prechecks[0].lineno < min(n.lineno for n in allocations))
    ):
        return [Violation(backend, "X0 original signed mount check must precede native allocation")]
    if (
        len(calls(builder_fn, "_require_original_signed_mount_identity")) != 1
        or not calls(builder_fn, "verify_runtime_root")
        or calls(builder_fn, "_require_original_signed_mount_identity")[0].lineno
        >= calls(builder_fn, "verify_runtime_root")[0].lineno
        # Check values and their original ownership, not keyword presence:
        # `signed_mounts_identity=None` is syntactically present but severs
        # the original journal -> command -> builder correspondence.
        or not any(
            k.arg == "signed_mounts_identity"
            and isinstance(k.value, ast.Name)
            and k.value.id == "signed_mounts_identity"
            for call in calls(command_fn, "_prepare_mounts")
            for k in call.keywords
        )
        or not any(
            k.arg == "signed_mounts_identity"
            and ast.unparse(k.value) == "proof.mounts_identity if proof is not None else None"
            for call in calls(executor, "_command")
            for k in call.keywords
        )
    ):
        return [
            Violation(backend, "X0 original Bubblewrap builder must consume signed mount identity")
        ]
    return []


def _check_original_signed_stage_mount(package: Path) -> list[Violation]:
    """X0: stage sign-once carries mount meaning into the original Fleet cut.

    Only the existing AGENT_SOURCE_PREWORK_STAGED writer may sign the mount;
    the original Fleet owner must compare its live readback against it. This
    check is structural, NOT permission for an Agent Step or native mount.
    """
    journal = package / "runtime/enforcer_lace_journal.py"
    fleet = package / "services/fleet_run_lifecycle.py"
    try:
        jtree = ast.parse(journal.read_text(encoding="utf-8"))
        ftree = ast.parse(fleet.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return [Violation(journal, "X0 original signed stage mount owners unreadable")]

    def owner(tree: ast.AST, name: str) -> list[ast.FunctionDef]:
        return [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        ]

    stage = owner(jtree, "record_agent_prework_stage_in")
    verifier = owner(jtree, "verify")
    reserve = owner(ftree, "reserve_original_agent_source_for_attempt")
    inspect = owner(ftree, "inspect_original_agent_source_mount_for_attempt")
    if any(len(nodes) != 1 for nodes in (stage, verifier, reserve, inspect)):
        return [
            Violation(journal, "X0 one original signed stage and Fleet inspection owner required")
        ]
    stage_text = ast.unparse(stage[0])
    verify_text = ast.unparse(verifier[0])
    reserve_text = ast.unparse(reserve[0])
    inspect_text = ast.unparse(inspect[0])
    if not all(
        term in stage_text
        for term in (
            "source_mount: 'OriginalSandboxMount | None'",
            "binding['source_mount']",
            "source_mount.source",
            "source_mount.target",
            "source_mount.sha256",
            "auth.issue('agent-source-prework-stage', binding)",
        )
    ):
        return [Violation(journal, "X0 original stage must sign exact source mount identity once")]
    if not all(
        term in verify_text
        for term in (
            "required_binding | {'source_mount'}",
            "OriginalSandboxMount(**mount)",
            "checked_mount.target != '/opt/oh-goon/agent-source'",
        )
    ):
        return [
            Violation(journal, "X0 original journal must validate signed staged mount evidence")
        ]
    if not all(
        term in reserve_text
        for term in (
            "source_attempt._stage_agent_prework_bytes(handoff)",
            "source_attempt._inspect_agent_prework_readonly_mount(handoff, stage)",
            "source_mount=source_mount",
            "journal.record_agent_prework_stage_in(",
        )
    ):
        return [
            Violation(
                fleet, "X0 original Fleet stage must inspect before signing same journal record"
            )
        ]

    def calls(node: ast.AST, name: str) -> list[ast.Call]:
        return [
            item
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and (
                (isinstance(item.func, ast.Attribute) and item.func.attr == name)
                or (isinstance(item.func, ast.Name) and item.func.id == name)
            )
        ]

    staging = calls(reserve[0], "_stage_agent_prework_bytes")
    inspection = calls(reserve[0], "_inspect_agent_prework_readonly_mount")
    signing = calls(reserve[0], "record_agent_prework_stage_in")
    if (
        len(staging) != 1
        or len(inspection) != 1
        or len(signing) != 1
        or not staging[0].lineno < inspection[0].lineno < signing[0].lineno
    ):
        return [Violation(fleet, "X0 original stage readback and proposal must precede signing")]
    if any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Constant)
        and node.test.value is False
        for node in ast.walk(reserve[0])
    ):
        return [Violation(fleet, "X0 original signed stage cannot be hidden behind dead branches")]
    if not all(
        term in inspect_text
        for term in (
            "signed_stage.get('source_mount') !=",
            "proposal.source",
            "proposal.target",
            "proposal.sha256",
            "session.mutate(original_cut)",
        )
    ):
        return [
            Violation(fleet, "X0 original Fleet mount proposal must match signed stage exactly")
        ]
    checks = [
        node
        for node in ast.walk(inspect[0])
        if isinstance(node, ast.If) and "signed_stage.get('source_mount')" in ast.unparse(node.test)
    ]
    if len(checks) != 1 or not any(isinstance(n, ast.Raise) for n in ast.walk(checks[0])):
        return [
            Violation(fleet, "X0 original signed stage mismatch must raise before proposal return")
        ]
    # No parallel signer, publisher, or second stage journal path.
    for file in package.rglob("*.py"):
        if file == journal:
            continue
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_append_locked"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "AGENT_SOURCE_PREWORK_STAGED"
            for node in ast.walk(tree)
        ):
            return [Violation(file, "X0 parallel Agent stage journal publisher forbidden")]
    return []


def _check_signed_workspace_mount_projection(package: Path) -> list[Violation]:
    """B3B3B4: all requested read-only workspaces enter the ONE signed mount plan.

    This static check is intentionally redundant with the negative runtime tests:
    it makes removal/reordering of the source/target/identity correspondence
    an architectural regression even when the happy-path suite is not selected.
    """
    port = package / "core/ports/original_mount_intent.py"
    producer = package / "integrations/fleet_plan_local_lace_intent.py"
    consumer = package / "integrations/fleet_plan_bubblewrap_isolation.py"
    try:
        trees = [ast.parse(p.read_text(encoding="utf-8")) for p in (port, producer, consumer)]
    except (OSError, SyntaxError):
        return [Violation(producer, "X0 signed read-only workspace mount owner unreadable")]
    descriptor = [
        node
        for node in ast.walk(trees[0])
        if isinstance(node, ast.ClassDef) and node.name == "OriginalMountIntentDescriptor"
    ]
    projection = [
        node
        for node in ast.walk(trees[1])
        if isinstance(node, ast.FunctionDef) and node.name == "local_resource_identities"
    ]
    consumption = [
        node
        for node in ast.walk(trees[2])
        if isinstance(node, ast.FunctionDef) and node.name == "_prepare_mounts"
    ]
    if not all(len(group) == 1 for group in (descriptor, projection, consumption)):
        return [
            Violation(
                port, "X0 one original signed workspace descriptor/projection/consumer required"
            )
        ]
    fields = [
        node.target.id
        for node in descriptor[0].body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ]
    identity = [
        node
        for node in descriptor[0].body
        if isinstance(node, ast.FunctionDef) and node.name == "resource_identity"
    ]
    targets = [
        node
        for node in descriptor[0].body
        if isinstance(node, ast.FunctionDef) and node.name == "binding_targets"
    ]
    constructors = [
        node
        for node in ast.walk(projection[0])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "OriginalMountIntentDescriptor"
    ]
    if (
        fields.count("read_only_workspace") != 1
        or len(identity) != 1
        or len(targets) != 1
        or len(constructors) != 1
        or len([k for k in constructors[0].keywords if k.arg == "read_only_workspace"]) != 1
    ):
        return [Violation(port, "X0 missing original signed read-only workspace descriptor field")]
    descriptor_code = ast.unparse(identity[0])
    projection_code = ast.unparse(projection[0])
    consumer_code = ast.unparse(consumption[0])
    if not all(
        text in descriptor_code
        for text in (
            "members['read_only_workspace']",
            "w.source",
            "w.target",
            "w.sha256",
            "canonical_bytes(members)",
        )
    ) or "self.read_only_workspace.target" not in ast.unparse(targets[0]):
        return [
            Violation(port, "X0 signed mount digest omits read-only workspace bytes/target/source")
        ]
    if not all(
        text in projection_code
        for text in (
            "request.read_only_workspace.snapshot_path",
            "request.read_only_workspace.identity.sha256",
            "SANDBOX_PATHS.workspace",
            "if request.read_only_workspace is not None else None",
        )
    ):
        return [
            Violation(
                producer,
                "X0 workspace source identity missing from one original RESOURCE_INTENT projection",
            )
        ]
    signed = consumer_code.find("_require_original_signed_mount_identity(")
    runtime = consumer_code.find("verify_runtime_root(")
    robind = consumer_code.find("request.read_only_workspace.snapshot_path")
    if not (0 <= signed < runtime < robind and "'--ro-bind'" in consumer_code):
        return [
            Violation(
                consumer,
                "X0 Bubblewrap must verify signed workspace before native mount consumption",
            )
        ]
    return []


def _check_native_custody_observer_non_authority(root: Path) -> list[Violation]:
    """T0B2B2A: local /proc sample is never a signer, trust resolver or Gate A."""
    source = root / "backend/src/oh_goon/repository_tooling/custody_host_observation.py"
    pyproject = root / "backend/pyproject.toml"
    if not source.is_file():
        return [Violation(source, "X0 missing non-authoritative native custody observer")]
    try:
        script = project_scripts_from_text(pyproject.read_text(encoding="utf-8"))
        tree = ast.parse(source.read_text(encoding="utf-8"))
        funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        observer = funcs["observe_native_custody"]
        main = funcs["main"]
        body = ast.unparse(observer)
        main_code = ast.unparse(main)
    except (OSError, SyntaxError, ValueError, KeyError) as error:
        return [
            Violation(source, f"X0 custody observer contract unreadable: {type(error).__name__}")
        ]
    if (
        script.get("oh-goon-custody-host-observe")
        != "oh_goon.repository_tooling.custody_host_observation:main"
    ):
        return [
            Violation(pyproject, "X0 custody host observer must remain an offline registered tool")
        ]
    if (
        not all(
            part in body
            for part in (
                "'custody_proven': False",
                "'independent_custodian_proven': False",
                "'gate_a': 'BLOCKED'",
                "'runtime_authority': False",
                "'effect_permission': 'NONE'",
                "'disposition': 'UNDECIDED'",
                "register['policy_sha256']",
                "_proc_start_time(_fd_read(fd, 'stat'))",
                "('user', 'mnt', 'pid', 'net')",
                "_public_key_fingerprint(public_key)",
            )
        )
        or body.count("_proc_start_time(_fd_read(fd, 'stat'))") < 2
        or "observe_native_custody(" not in main_code
    ):
        return [
            Violation(
                source,
                "X0 native custody observation may not promote trust or skip process identity",
            )
        ]
    for subdir in ("runtime", "integrations", "services", "core"):
        for file in (root / "backend/src/oh_goon" / subdir).rglob("*.py"):
            try:
                text = file.read_text(encoding="utf-8")
            except OSError:
                continue
            if "custody_host_observation" in text or "oh-goon-custody-host-observe" in text:
                return [Violation(file, "X0 runtime may not import native custody diagnostic")]
    return []


def _check_p1b27a_signed_host_tool_observation(package: Path) -> list[Violation]:
    """P1/B27A: one signed mount descriptor owns its native target roster.

    No new observer, journal writer or consumer is introduced. Verify the same
    host-tool targets consumed by Bubblewrap are in the original physical scope.
    """
    port = package / "core/ports/original_mount_intent.py"
    consumer = package / "integrations/fleet_plan_bubblewrap_isolation.py"
    owner = package / "runtime/enforcer_lace_original_physical_sampling.py"
    try:
        port_tree = ast.parse(port.read_text(encoding="utf-8"))
        consumer_tree = ast.parse(consumer.read_text(encoding="utf-8"))
        owner_tree = ast.parse(owner.read_text(encoding="utf-8"))
        descriptor = next(
            n
            for n in port_tree.body
            if isinstance(n, ast.ClassDef) and n.name == "OriginalMountIntentDescriptor"
        )
        methods = {n.name: n for n in descriptor.body if isinstance(n, ast.FunctionDef)}
        host = ast.unparse(methods["_host_tool_targets"])
        targets = ast.unparse(methods["binding_targets"])
        validation = ast.unparse(methods["__post_init__"])
        launch = next(
            n
            for n in consumer_tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "_append_toolchain"
        )
        launch_code = ast.unparse(launch)
        sample = next(
            n
            for n in owner_tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "require_original_physical_sampling_in"
        )
        sample_code = ast.unparse(sample)
    except (OSError, SyntaxError, StopIteration, KeyError):
        return [Violation(port, "X0 B27 host-tool target owner not inspectable")]
    if not all(
        fragment in host
        for fragment in ("SANDBOX_PATHS.toolchain / tool.name", "tool.name == 'sh'", "'/bin/sh'")
    ):
        return [Violation(port, "X0 B27 signed host-tool observation targets omitted")]
    if (
        "self._host_tool_targets" not in targets
        or "return targets + self._host_tool_targets" not in targets
    ):
        return [Violation(port, "X0 B27 physical targets must use original signed descriptor")]
    if not all(
        fragment in validation
        for fragment in (
            "self.binding_targets",
            "len(declared) > 64",
            "len(set(declared)) != len(declared)",
            "self.sandbox_bindings",
            "raise ValueError('original mount source shadows a protected target')",
        )
    ):
        return [Violation(port, "X0 B27 mount target uniqueness and shadow protection removed")]
    if not all(
        fragment in launch_code
        for fragment in (
            "SANDBOX_PATHS.toolchain / binding.name",
            "binding.name == 'sh'",
            "'/bin/sh'",
        )
    ):
        return [Violation(consumer, "X0 B27 Bubblewrap target set differs from signed descriptor")]
    if not all(
        fragment in sample_code
        for fragment in (
            "scope.mount_targets != mount_intent.binding_targets",
            "declared_mount_targets=mount_intent.binding_targets",
        )
    ):
        return [Violation(owner, "X0 B27 original recovery no longer binds physical targets")]
    return []


def _check_p1b27b1_native_mount_roster(package: Path) -> list[Violation]:
    """X0: the existing Bubblewrap builder MUST validate every physical bind."""
    path = package / "integrations/fleet_plan_bubblewrap_isolation.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        methods = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        launch = ast.unparse(methods["_prepare_mounts"])
        guard = ast.unparse(methods["_require_original_native_mount_argv"])
    except (OSError, SyntaxError, KeyError):
        return [Violation(path, "X0 B27B1 native mount roster unavailable")]
    # AST call order is enforced in addition to string presence: one invocation
    # on the normal straight-line body after append_atomic_execution_mounts.
    body = methods["_prepare_mounts"].body
    calls = [n for n in body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)]
    called = [n.value.func.id for n in calls if isinstance(n.value.func, ast.Name)]
    if (
        called.count("_require_original_native_mount_argv") != 1
        or "_append_atomic_execution_mounts" not in called
        or called.index("_require_original_native_mount_argv")
        < called.index("_append_atomic_execution_mounts")
        or not any(
            isinstance(n, ast.Return) and isinstance(n.value, ast.Name) and n.value.id == "args"
            for n in body
        )
    ):
        return [Violation(path, "X0 B27B1 bypasses original prelaunch native mount roster")]
    # P1/B27B2A moved predictable bind roster construction into ONE original
    # pre-effect projection. Keep the B1 argv+FD checks, now proving that they
    # consume that exact roster instead of a second independent native list.
    native_file = package / "integrations/fleet_plan_local_lace_intent.py"
    try:
        native_tree = ast.parse(native_file.read_text(encoding="utf-8"))
        native = {n.name: n for n in native_tree.body if isinstance(n, ast.FunctionDef)}
        projected = ast.unparse(native["prepared_original_native_mounts"])
    except (OSError, SyntaxError, KeyError):
        return [Violation(native_file, "X0 B27B2A original native mount source roster missing")]
    required_native = (
        "attempt.runtime_root.root",
        "paths.etc / name",
        "paths.workspace",
        "('home', 'tmp')",
        "getattr(paths, name)",
        "paths.cache",
        "paths.config",
        "request.read_only_workspace.snapshot_path",
        "attempt.sandbox_bindings",
        "'--ro-bind'",
        "'--bind'",
        "OriginalNativeMount",
    )
    required_consumer = (
        "prepared_original_native_mounts(request)",
        "attempt.sandbox_bindings",
        "attempt.host_tools",
        "atomic_authority.bubblewrap_bindings",
        "observed != expected",
        "len(set(observed)) != len(observed)",
        "('--proc', str(SANDBOX_PATHS.proc))",
        "('--dev', str(SANDBOX_PATHS.dev))",
        "args[at - 2:at] != ['--perms', f'{f.mode:o}']",
        "raise FleetPlanInvalid",
    )
    if (
        not all(fragment in projected for fragment in required_native)
        or not all(fragment in guard for fragment in required_consumer)
        or guard.count("for f in atomic_authority.bubblewrap_bindings") < 2
        or "'--dev-bind'" not in guard
        or "if any((flag in forbidden for flag in args))" not in guard
    ):
        return [Violation(path, "X0 B27B2A native source/FD correspondence bypassed")]
    if (
        "_require_original_native_mount_argv(args, request, atomic_authority, nested_cgroup_root)"
        not in launch
    ):
        return [Violation(path, "X0 B27B1 mount roster checker receives substituted authority")]
    return []


def _check_p1b27b2a_signed_native_sources(package: Path) -> list[Violation]:
    """X0: one source projection, one original signer, one bwrap consumer."""
    port = package / "core/ports/original_mount_intent.py"
    intent = package / "integrations/fleet_plan_local_lace_intent.py"
    bwrap = package / "integrations/fleet_plan_bubblewrap_isolation.py"
    try:
        pt = ast.parse(port.read_text(encoding="utf-8"))
        it = ast.parse(intent.read_text(encoding="utf-8"))
        bt = ast.parse(bwrap.read_text(encoding="utf-8"))
        classes = {n.name: n for n in pt.body if isinstance(n, ast.ClassDef)}
        methods = {n.name: n for n in it.body if isinstance(n, ast.FunctionDef)}
        consumers = {n.name: n for n in bt.body if isinstance(n, ast.FunctionDef)}
        payload = ast.unparse(classes["OriginalMountIntentDescriptor"])
        projection = ast.unparse(methods["local_resource_identities"])
        roster = ast.unparse(methods["prepared_original_native_mounts"])
        consumer = ast.unparse(consumers["_require_original_native_mount_argv"])
        signed = ast.unparse(consumers["_require_original_signed_mount_identity"])
    except (OSError, SyntaxError, KeyError):
        return [Violation(port, "X0 signed native roster source/consumer missing")]
    required = {
        "payload": (
            "native_mounts: tuple[OriginalNativeMount, ...]",
            "members['native_mounts']",
            "'source': x.source",
            "'target': x.target",
            "'mode': x.mode",
            "canonical_bytes(members)",
        ),
        "projection": (
            "native_mounts=prepared_original_native_mounts(request)",
            "OriginalMountIntentDescriptor(",
            ".resource_identity",
        ),
        "roster": (
            "attempt.runtime_root.root",
            "paths.etc / name",
            "('home', 'tmp')",
            "paths.workspace",
            "paths.cache",
            "paths.config",
            "request.read_only_workspace.snapshot_path",
            "OriginalNativeMount",
        ),
        "consumer": (
            "prepared_original_native_mounts(request)",
            "observed != expected",
            "raise FleetPlanInvalid",
        ),
        "signed": ("local_resource_identities(request)", "signed_mounts_identity"),
    }
    values = {
        "payload": payload,
        "projection": projection,
        "roster": roster,
        "consumer": consumer,
        "signed": signed,
    }
    if any(
        not all(fragment in values[name] for fragment in parts) for name, parts in required.items()
    ):
        return [Violation(port, "X0 B27B2A original signed native roster drift")]
    # This check is not the signer. It only freezes the call graph whose
    # runtime meaning is protected by the hostile reservation regressions.
    if (
        projection.count("prepared_original_native_mounts(request)") != 1
        or consumer.count("prepared_original_native_mounts(request)") != 1
        or "native_mounts" not in payload
    ):
        return [Violation(intent, "X0 B27B2A second or missing native source owner")]
    return []


def _check_p1b27b2b1_kernel_fd_custody(package: Path) -> list[Violation]:
    """X0: original atomic lease owns the kernel FD revalidation call chain.

    Never accept a dead-branch verification or an alternate mount executor.
    Runtime regressions test real Linux descriptor swaps and seal failures.
    """
    engine = package / "engine/atomic_execution_bytes.py"
    lease_file = package / "integrations/fleet_plan_atomic_runtime_authority.py"
    bwrap = package / "integrations/fleet_plan_bubblewrap_isolation.py"
    try:
        engine_tree = ast.parse(engine.read_text(encoding="utf-8"))
        lease_tree = ast.parse(lease_file.read_text(encoding="utf-8"))
        bwrap_tree = ast.parse(bwrap.read_text(encoding="utf-8"))
        engine_verify = next(
            n
            for n in engine_tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "verify_sealed_execution_descriptor"
        )
        lease_type = next(
            n
            for n in lease_tree.body
            if isinstance(n, ast.ClassDef) and n.name == "AtomicRuntimeAuthorityLease"
        )
        verify = next(
            n
            for n in lease_type.body
            if isinstance(n, ast.FunctionDef) and n.name == "verify_kernel_custody"
        )
        rewind = next(
            n
            for n in lease_type.body
            if isinstance(n, ast.FunctionDef) and n.name == "rewind_for_bubblewrap"
        )
        bwrap_type = next(
            n
            for n in bwrap_tree.body
            if isinstance(n, ast.ClassDef) and n.name == "BubblewrapProcessIsolation"
        )
        execute = next(
            n for n in bwrap_type.body if isinstance(n, ast.FunctionDef) and n.name == "execute"
        )
        original_with = [
            n
            for n in ast.walk(execute)
            if isinstance(n, ast.With)
            and any(
                isinstance(i.context_expr, ast.Call)
                and isinstance(i.context_expr.func, ast.Name)
                and i.context_expr.func.id == "bind_atomic_runtime_authority"
                for i in n.items
            )
        ]
    except (OSError, SyntaxError, StopIteration):
        return [Violation(bwrap, "X0 original sealed runtime kernel custody unreadable")]
    e = ast.unparse(engine_verify)
    v = ast.unparse(verify)
    r = ast.unparse(rewind)
    if (
        not all(
            x in e
            for x in (
                "os.fstat(sealed.descriptor)",
                "sealed.kernel_device",
                "sealed.kernel_inode",
                "fcntl.fcntl(sealed.descriptor, get_seals)",
                "hasher.hexdigest()",
                "sealed.identity",
                "os.pread(sealed.descriptor",
                "required_seals",
            )
        )
        or "(info.st_dev, info.st_ino) != (sealed.kernel_device, sealed.kernel_inode)" not in e
        or "'sha256:' + hasher.hexdigest() != sealed.identity" not in e
        or "fcntl.fcntl(sealed.descriptor, get_seals) & required_seals != required_seals" not in e
    ):
        return [Violation(engine, "X0 original Linux memfd inode, seals or bytes not reverified")]
    if (
        not all(
            x in v
            for x in (
                "self.receipt.executable_identity",
                "self.receipt.interpreter_identity",
                "len(set(self.pass_fds)) != len(files)",
                "verify_sealed_execution_descriptor(",
                "kernel_device=runtime_file.kernel_device",
                "kernel_inode=runtime_file.kernel_inode",
            )
        )
        or "self.verify_kernel_custody()" not in r
    ):
        return [Violation(lease_file, "X0 atomic runtime lease FD handoff verification bypassed")]
    if len(original_with) != 1:
        return [Violation(bwrap, "X0 original atomic runtime lease ownership duplicated or absent")]
    body = original_with[0].body
    # These must be top-level sequential statements under the original lease,
    # not nested in a dead conditional or only present in unused helper code.
    top = [ast.unparse(n) for n in body]
    # Later FD-bind work delegates the original *same* prelaunch custody
    # checks to one reachable helper. Keep old-source inline qualification
    # valid, but reject dead branches and unchecked inheritance on new source.
    prefix = (
        "atomic_authority.verify_kernel_custody()",
        "argv, env = self._command(",
        "atomic_authority.rewind_for_bubblewrap()",
    )
    delegated = any(
        x.startswith("inherited_fds = _require_original_native_fd_handoff(") for x in top
    )
    # CO-R3Q: preserve the ORIGINAL fd-handoff adjacency while admitting
    # the new same-owner signer/lease effect fence immediately before launch.
    effect_fenced = any(item.startswith("require_effect_authority()") for item in top)
    expected = (
        *prefix,
        *(
            (
                "inherited_fds = _require_original_native_fd_handoff(",
                *(("require_effect_authority()",) if effect_fenced else ()),
                "result = run_command(",
            )
            if delegated
            else (
                "atomic_authority.verify_kernel_custody()",
                "mount_source_lease.verify_fd_argv(",
                *(("require_effect_authority()",) if effect_fenced else ()),
                "result = run_command(",
            )
        ),
    )
    if len(top) < len(expected) or any(
        not top[i].startswith(value) for i, value in enumerate(expected)
    ):
        return [
            Violation(bwrap, "X0 original FD custody must bracket mount argv and process launch")
        ]
    if delegated:
        helper = next(
            (
                n
                for n in bwrap_tree.body
                if isinstance(n, ast.FunctionDef)
                and n.name == "_require_original_native_fd_handoff"
            ),
            None,
        )
        checked = ast.unparse(helper) if helper is not None else ""
        if not all(
            x in checked
            for x in (
                "atomic_authority.verify_kernel_custody()",
                "mount_source_lease.verify_fd_argv(list(argv))",
                "len(set(inherited_fds)) != len(inherited_fds)",
            )
        ):
            return [Violation(bwrap, "X0 delegated original kernel FD custody omitted")]
    return []


def _check_p1b27b2b2a_source_fd_custody(package: Path) -> list[Violation]:
    """X0: bind the original source once and consume its FD in original bwrap.

    This guards the real launch path rather than the test-only unlaunched argv
    builder. The signed roster still comes exclusively from the original intent.
    """
    bwrap = package / "integrations/fleet_plan_bubblewrap_isolation.py"
    owner = package / "integrations/fleet_plan_bind_source_custody.py"
    try:
        btree = ast.parse(bwrap.read_text(encoding="utf-8"))
        otree = ast.parse(owner.read_text(encoding="utf-8"))
        runtime = next(
            c
            for c in btree.body
            if isinstance(c, ast.ClassDef) and c.name == "BubblewrapProcessIsolation"
        )
        methods = {n.name: n for n in runtime.body if isinstance(n, ast.FunctionDef)}
        builder = next(
            n for n in btree.body if isinstance(n, ast.FunctionDef) and n.name == "_prepare_mounts"
        )
        lease = next(
            c
            for c in otree.body
            if isinstance(c, ast.ClassDef) and c.name == "OriginalBindSourceLease"
        )
        owners = {n.name: n for n in lease.body if isinstance(n, ast.FunctionDef)}
        open_path = next(
            n
            for n in otree.body
            if isinstance(n, ast.FunctionDef) and n.name == "_open_absolute_no_symlinks"
        )
        preflight = next(
            n
            for n in otree.body
            if isinstance(n, ast.FunctionDef) and n.name == "require_bubblewrap_fd_binding"
        )
    except (OSError, SyntaxError, StopIteration):
        return [Violation(bwrap, "X0 pinned native source FD owner missing")]

    execute = methods["execute"]
    t = ast.unparse(execute)
    main = execute.body
    reserve_index = next(
        (
            i
            for i, n in enumerate(main)
            if "request.local_resource_intent.reserve(" in ast.unparse(n)
        ),
        -1,
    )
    require_index = next(
        (
            i
            for i, n in enumerate(main)
            if "require_bubblewrap_fd_binding(self.executable)" in ast.unparse(n)
        ),
        -1,
    )
    with_index = next(
        (
            i
            for i, n in enumerate(main)
            if isinstance(n, ast.With)
            and any(
                isinstance(x.context_expr, ast.Call)
                and ast.unparse(x.context_expr.func) == "OriginalBindSourceLease.pin_prepared"
                for x in n.items
            )
        ),
        -1,
    )
    if not (
        0 <= reserve_index < require_index < with_index
        and isinstance(main[require_index], ast.Expr)
        and isinstance(main[require_index].value, ast.Call)
        and ast.unparse(main[require_index].value.func) == "require_bubblewrap_fd_binding"
    ):
        return [Violation(bwrap, "X0 original reservation must precede unconditional FD preflight")]
    source_scope = main[with_index]
    body = ast.unparse(source_scope)
    if not all(
        x in body
        for x in (
            "CgroupV2AttemptLease.create(",
            "RestrictedEgressLease.create(",
            "mount_source_lease.pin_dynamic_cgroup(",
            "mount_source_lease.verify_kernel_custody()",
            "mount_source_lease=mount_source_lease",
            "result = run_command(",
        )
    ):
        return [Violation(bwrap, "X0 execution bypasses original FD source custody")]
    delegated = "_require_original_native_fd_handoff(" in body
    if delegated:
        helper = next(
            (
                node
                for node in btree.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "_require_original_native_fd_handoff"
            ),
            None,
        )
        checked = ast.unparse(helper) if helper is not None else ""
        if not all(
            x in body
            for x in (
                "pass_fds=inherited_fds",
                "inherited_fds = _require_original_native_fd_handoff(",
            )
        ) or not all(
            x in checked
            for x in (
                "mount_source_lease.verify_fd_argv(list(argv))",
                "atomic_authority.verify_kernel_custody()",
                "len(set(inherited_fds)) != len(inherited_fds)",
            )
        ):
            return [
                Violation(
                    bwrap, "X0 original FD custody must bracket mount argv and process launch"
                )
            ]
    elif not all(
        x in body
        for x in ("mount_source_lease.verify_fd_argv(list(argv))", "*mount_source_lease.pass_fds")
    ):
        return [
            Violation(bwrap, "X0 original FD custody must bracket mount argv and process launch")
        ]
    if "OriginalBindSourceLease.pin_prepared(request)" not in t:
        return [Violation(bwrap, "X0 original FD source prework missing")]
    build = ast.unparse(builder)
    if not all(
        x in build
        for x in (
            "_require_original_native_mount_argv(args, request, atomic_authority, nested_cgroup_root)",
            "mount_source_lease.consume_validated_argv(args)",
        )
    ) or build.index("_require_original_native_mount_argv(") > build.index(
        "mount_source_lease.consume_validated_argv(args)"
    ):
        return [
            Violation(bwrap, "X0 source FD rewrite precedes original argv verification or missing")
        ]
    open_code = ast.unparse(open_path)
    if not all(
        x in open_code
        for x in ("os.O_PATH", "os.O_NOFOLLOW", "dir_fd=fd", "stat.S_ISLNK", "os.fstat(fd)")
    ):
        return [Violation(owner, "X0 source FD may follow symlinks or omit kernel identity")]
    intake = ast.unparse(preflight)
    if not all(
        x in intake
        for x in ("'--bind-fd'", "'--ro-bind-fd'", "subprocess.run(", "raise FleetPlanInvalid(")
    ):
        return [Violation(owner, "X0 unsupported bwrap may fall back to pathname binds")]
    pin_code = ast.unparse(owners["pin_prepared"])
    argv_code = ast.unparse(owners["consume_validated_argv"])
    verify = ast.unparse(owners["verify_kernel_custody"])
    if not all(
        x in pin_code
        for x in (
            "prepared_original_native_mounts(request)",
            "attempt.sandbox_bindings",
            "attempt.host_tools",
            "lease._pin(",
        )
    ):
        return [Violation(owner, "X0 parallel/partial mount roster introduced")]
    if not all(
        x in argv_code
        for x in ("_BIND[option]", "self.verify_fd_argv(args)", "self.verify_kernel_custody()")
    ):
        return [Violation(owner, "X0 native FD argv not coupled to original kernel object")]
    if not all(
        x in verify
        for x in (
            "os.fstat(source.fd)",
            "source.device",
            "source.inode",
            "source.mode",
            "raise FleetPlanInvalid(",
        )
    ):
        return [Violation(owner, "X0 original bind source kernel identity not verified")]
    return []


def _check_p1b27b2b3a_mount_coverage(package: Path) -> list[Violation]:
    """X0: the original shared Linux observer must not accept partial targets.

    A bounded structural guard only. Runtime/mutation and native evidence
    remain mandatory; it does not classify containment or authorize effects.
    """
    shared = package / "integrations/linux_physical_observer.py"
    try:
        tree = ast.parse(shared.read_text(encoding="utf-8"))
        owners = [
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "_tracked_namespace_inventory"
        ]
        if len(owners) != 1:
            raise ValueError("shared tracked-namespace owner missing")
        owner = owners[0]
        predicates = [n for n in ast.walk(owner) if isinstance(n, ast.IfExp)]
        source = ast.unparse(owner)
        obligations = (
            "matched_targets.setdefault",
            "len(entries) != 1",
            "targets - matched_targets.keys()",
            "ambiguous or (matched and missing)",
            "UNKNOWN",
            "NOT_OBSERVED",
            "target_kernel_sha256",
            "foreign/detached mounts unverified",
        )
        # Require the target-matching call on the reachable branch of the
        # existing mountinfo loop, not in a dead branch or comment.
        live_match = [
            node
            for node in ast.walk(owner)
            if isinstance(node, ast.If)
            and ast.unparse(node.test) == "target in targets"
            and any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "setdefault"
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "matched_targets"
                for statement in node.body
                for n in ast.walk(statement)
            )
        ]
        if (
            not predicates
            or any(item not in source for item in obligations)
            or len(live_match) != 1
        ):
            raise ValueError("original scope no longer fails closed on incomplete mount coverage")
        if any(
            isinstance(n, (ast.Import, ast.ImportFrom))
            and (
                (isinstance(n, ast.ImportFrom) and (n.module or "").startswith("oh_goon.runtime"))
                or (
                    isinstance(n, ast.Import)
                    and any(a.name.startswith("oh_goon.runtime") for a in n.names)
                )
            )
            for n in ast.walk(tree)
        ):
            raise ValueError("shared Linux observer imports Run authority")
    except (OSError, SyntaxError, ValueError) as error:
        return [Violation(shared, f"X0 P1B27B2B3A single observer coverage drift: {error}")]
    return []


def _check_p1b27b2b3b1_mount_fd_lifecycle(package: Path) -> list[Violation]:
    """X0: FD holders cannot outrun original signed target mount coverage.

    This enforces guards within the ONE existing kernel reader; it cannot
    certify physical absence, observer custody, or actual Bubblewrap effects.
    """
    shared = package / "integrations/linux_physical_observer.py"
    try:
        module = ast.parse(shared.read_text(encoding="utf-8"))
        owners = [
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == "_visible_mount_fd_holders"
        ]
        if len(owners) != 1:
            raise ValueError("one shared mount FD census owner required")
        owner = owners[0]
        source = ast.unparse(owner)
        obligations = (
            "set(matched_targets) != targets",
            "len(entries) != 1",
            "matched_targets.setdefault",
            "target_fingerprint = hashlib.sha256",
            "target_kernel_sha256",
            "after_names != names",
            "_read_regular(original / 'mountinfo', 1048576).splitlines() != raw",
            "original_identity() != namespace",
            "NOT_OBSERVED",
            "UNKNOWN",
            "hidden tasks, detached mounts, and kernel-held references unverified",
        )
        if any(value not in source for value in obligations):
            raise ValueError("original FD holder coverage, mount identity or race guard changed")
        # Guards must be actual reachable conditional decisions in the owner,
        # not just comments or arbitrary diagnostic strings elsewhere.
        tests = [ast.unparse(node.test) for node in ast.walk(owner) if isinstance(node, ast.If)]
        if not all(
            any(value in test for test in tests)
            for value in (
                "set(matched_targets) != targets",
                "after_names != names",
                "_read_regular(original / 'mountinfo', 1048576).splitlines() != raw",
            )
        ):
            raise ValueError(
                "original FD holder fail-closed condition moved out of executable guards"
            )
    except (OSError, SyntaxError, ValueError) as error:
        return [Violation(shared, f"X0 P1B27B2B3B1 mount FD lifecycle drift: {error}")]
    return []


def _check_p1b27b2b3b2a_mount_fd_correlation(package: Path) -> list[Violation]:
    """One shared post-double-scan correspondence guard, not a second publisher."""
    source = package / "integrations/linux_physical_observer.py"
    try:
        module = ast.parse(source.read_text(encoding="utf-8"))
        functions = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}
        if not {"observe_kernel", "_correlate_original_mount_fd_snapshot"} <= functions.keys():
            raise ValueError("original correlation owner missing")
        observe = functions["observe_kernel"]
        corr = functions["_correlate_original_mount_fd_snapshot"]
        parsed = ast.unparse(corr)
        obligations = (
            "tracked_mount_namespace",
            "visible_mount_fd_holders",
            "tracked_id != fd_id",
            "tracked_id is None",
            "fd_id is None",
            "tracked['state'] != 'PRESENT'",
            "holders['state'] not in ('PRESENT', 'NOT_OBSERVED')",
            "target_kernel_sha256",
            "original_namespace",
            "UNKNOWN",
        )
        if any(value not in parsed for value in obligations):
            raise ValueError("kernel mount/FD identity comparison weakened")
        calls = [
            node
            for node in ast.walk(observe)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_correlate_original_mount_fd_snapshot"
        ]
        if (
            len(calls) != 1
            or len(calls[0].args) != 1
            or not isinstance(calls[0].args[0], ast.Name)
            or calls[0].args[0].id != "combined"
        ):
            raise ValueError("single original double-scan correlation call required")
        # Exact data-flow correspondence: the result is assigned and then the
        # same checked mapping is used for every published read-only fact.
        observe_source = ast.unparse(observe)
        if (
            "combined = _correlate_original_mount_fd_snapshot(combined)" not in observe_source
            or "value = combined[name]" not in observe_source
        ):
            raise ValueError("correlation is bypassed before snapshot projection")
        if any(
            isinstance(node, (ast.Import, ast.ImportFrom))
            and getattr(node, "module", "").startswith("oh_goon.runtime")
            for node in ast.walk(module)
        ):
            raise ValueError("observer imports runtime authority")
    except (OSError, SyntaxError, ValueError) as exc:
        return [Violation(source, f"X0 P1B27B2B3B2A original mount/FD correspondence drift: {exc}")]
    return []


def _check_p1b27b2b3b2b1_holder_race_fences(package: Path) -> list[Violation]:
    """Keep original LACE holder census race fences on both physical probes."""
    source = package / "integrations/linux_physical_observer.py"
    try:
        module = ast.parse(source.read_text(encoding="utf-8"))
        functions = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}
        if not {"_namespace_fd_holders", "_visible_mount_fd_holders"} <= functions.keys():
            raise ValueError("original shared holder observation functions missing")
        obligations = {
            "_namespace_fd_holders": (
                "final_fds != fds",
                "os.readlink(fd_dir / fd) != fd_links[fd]",
                "final_candidates != candidates",
            ),
            "_visible_mount_fd_holders": (
                "read_mount_id(fd) != mount_ids[fd]",
                "after_names != names",
                "final_pids != pids",
            ),
        }
        for name, expressions in obligations.items():
            fn = functions[name]
            tests = [ast.unparse(node.test) for node in ast.walk(fn) if isinstance(node, ast.If)]
            if any(not any(expr in test for test in tests) for expr in expressions):
                raise ValueError(f"{name}: holder race fence removed from executable condition")
        if any(
            isinstance(node, (ast.Import, ast.ImportFrom))
            and getattr(node, "module", "").startswith("oh_goon.runtime")
            for node in ast.walk(module)
        ):
            raise ValueError("shared Linux observer gained execution authority import")
    except (OSError, SyntaxError, ValueError) as exc:
        return [Violation(source, f"X0 P1B27B2B3B2B1 original holder lifecycle drift: {exc}")]
    return []


def _check_p1b27b2b3b2b2_fd_handoff(package: Path) -> list[Violation]:
    """The one original Bubblewrap run_command must receive checked combined FDs."""
    isolation = package / "integrations/fleet_plan_bubblewrap_isolation.py"
    source = package / "integrations/fleet_plan_bind_source_custody.py"
    try:
        module = ast.parse(isolation.read_text(encoding="utf-8"))
        custody = ast.parse(source.read_text(encoding="utf-8"))
        functions = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}
        verify = functions["_require_original_native_fd_handoff"]
        statements = ast.unparse(verify)
        for required in (
            "atomic_authority.verify_kernel_custody()",
            "mount_source_lease.verify_fd_argv(list(argv))",
            "atomic_authority.pass_fds",
            "mount_source_lease.pass_fds",
            "len(set(inherited_fds)) != len(inherited_fds)",
            "return inherited_fds",
        ):
            if required not in statements:
                raise ValueError(f"cross-lease inherited descriptor check missing: {required}")
        classes = [
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "BubblewrapProcessIsolation"
        ]
        execute = next(
            node
            for node in classes[0].body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "execute"
        )
        calls = [
            node
            for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        if sum(call.func.id == "_require_original_native_fd_handoff" for call in calls) != 1:
            raise ValueError("original execution handoff check missing or duplicated")
        call_source = ast.unparse(execute)
        if (
            "inherited_fds = _require_original_native_fd_handoff(argv, atomic_authority, mount_source_lease)"
            not in call_source
            or "pass_fds=inherited_fds" not in call_source
        ):
            raise ValueError("original run_command not bound to validated inherited FD roster")
        guard_call = next(
            call for call in calls if call.func.id == "_require_original_native_fd_handoff"
        )
        launch_call = next(call for call in calls if call.func.id == "run_command")
        if guard_call.lineno >= launch_call.lineno:
            raise ValueError("original FD check must precede native launch")
        owner = next(
            node
            for node in custody.body
            if isinstance(node, ast.ClassDef) and node.name == "OriginalBindSourceLease"
        )
        check = next(
            node
            for node in owner.body
            if isinstance(node, ast.FunctionDef) and node.name == "verify_kernel_custody"
        )
        pin_source = ast.unparse(check)
        for required in (
            "fcntl.F_GETFL",
            "fcntl.F_GETFD",
            "flags & os.O_PATH",
            "fd_flags & fcntl.FD_CLOEXEC",
        ):
            if required not in pin_source:
                raise ValueError(f"native O_PATH/close-on-exec FD check missing: {required}")
    except (OSError, SyntaxError, ValueError, IndexError, StopIteration) as exc:
        return [Violation(isolation, f"X0 P1B27B2B3B2B2 canonical native FD handoff drift: {exc}")]
    return []


def _check_p1b27b2b3b2b3b_fd_argv_boundary(package: Path) -> list[Violation]:
    """The original bind lease never counts Step argv as native mount options.

    This static guard complements hostile execution tests; it does not mint
    independent mount-observation or admission authority.
    """
    source = package / "integrations/fleet_plan_bind_source_custody.py"
    owner = package / "integrations/fleet_plan_bubblewrap_isolation.py"
    try:
        lease_tree = ast.parse(source.read_text(encoding="utf-8"))
        owner_tree = ast.parse(owner.read_text(encoding="utf-8"))
        lease_funcs = {n.name: n for n in lease_tree.body if isinstance(n, ast.FunctionDef)}
        lease_class = next(
            n
            for n in lease_tree.body
            if isinstance(n, ast.ClassDef) and n.name == "OriginalBindSourceLease"
        )
        methods = {n.name: n for n in lease_class.body if isinstance(n, ast.FunctionDef)}
        owner_funcs = {n.name: n for n in owner_tree.body if isinstance(n, ast.FunctionDef)}
        boundary = ast.unparse(lease_funcs["_original_bwrap_options"])
        consume = ast.unparse(methods["consume_validated_argv"])
        verify = ast.unparse(methods["verify_fd_argv"])
        handoff = ast.unparse(owner_funcs["_require_original_native_fd_handoff"])
        if (
            "args[:args.index('--')] if '--' in args else args" not in boundary
            or "_original_bwrap_options(args)" not in consume
            or "_original_bwrap_options(args)" not in verify
            or "for i in range(len(options) - 2)" not in consume
            or "for i in range(len(options) - 2)" not in verify
            or "if '--' not in argv or argv.index('--') == 0" not in handoff
            or "argv.index('--') == len(argv) - 1" not in handoff
        ):
            raise ValueError("original Bubblewrap native/child argv boundary bypassed")
    except (OSError, SyntaxError, ValueError, KeyError, StopIteration) as exc:
        return [Violation(source, f"X0 P1B27B2B3B2B3B FD mount/Step argv separation: {exc}")]
    return []


def _check_p1p2b1_causal_kernel_ledger(package: Path) -> list[Violation]:
    """X0: original positive mount claims stay Run-bound and non-authorizing."""
    sampler = package / "runtime/enforcer_lace_original_physical_sampling.py"
    port = package / "core/ports/physical_observation.py"
    owner = package / "runtime/enforcer_lace_fleet_resource_owner.py"
    try:
        source = ast.parse(sampler.read_text(encoding="utf-8"))
        typed = ast.parse(port.read_text(encoding="utf-8"))
        fleet = ast.parse(owner.read_text(encoding="utf-8"))
        methods = [
            n
            for n in ast.walk(source)
            if isinstance(n, ast.FunctionDef)
            and n.name == "require_original_sample_kernel_identity"
        ]
        sample_classes = [
            n
            for n in typed.body
            if isinstance(n, ast.ClassDef) and n.name == "OriginalRecoveryPhysicalSample"
        ]
        if len(methods) != 1 or len(sample_classes) != 1:
            raise ValueError("one original sampler/typed sample required")
        guard = ast.unparse(methods[0])
        for required in (
            "binding.envelope.declared_mount_targets",
            "visible_targets=",
            "ambiguous_target_mounts=",
            "target_mount_ids=",
            "re.findall",
            "len(rows) != 1",
            "len(set(mount_ids)) != expected",
            "raise CausalJournalError",
        ):
            if required not in guard:
                raise ValueError(f"original mount ledger positive guard removed: {required}")
        candidates = [
            n
            for n in sample_classes[0].body
            if isinstance(n, ast.FunctionDef) and n.name == "causal_observation_identity"
        ]
        if len(candidates) != 1:
            raise ValueError("Run-bound physical candidate identity missing")
        candidate = ast.unparse(candidates[0])
        for required in (
            "self.envelope.identity",
            "asdict(self.scope)",
            "self.facts.identity",
            "self.mount_intent_identity",
            "self.classification",
            "self.effect_permission",
            "oh-goon/b27/original-physical-candidate/v1",
        ):
            if required not in candidate:
                raise ValueError(f"candidate identity loses causal binding: {required}")
        original = [
            n
            for n in ast.walk(fleet)
            if isinstance(n, ast.FunctionDef) and n.name == "sample_recovery_physical_unqualified"
        ]
        if len(original) != 1:
            raise ValueError("original Fleet sampling owner missing")
        flow = ast.unparse(original[0])
        for required in (
            "require_original_sample_kernel_identity(before, facts)",
            "after = validate_cut(connection)",
            "if before != after:",
            "OriginalRecoveryPhysicalSample(",
            "journal._append_locked(self.run_id, 'RESOURCE_PHYSICAL_SAMPLE_RECORDED', payload)",
            "mutate(record_cut)",
        ):
            if required not in flow:
                raise ValueError(f"original sample lost two-cut validation: {required}")
    except (OSError, SyntaxError, ValueError) as exc:
        return [Violation(sampler, f"X0 P1P2B1 original causal kernel ledger drift: {exc}")]
    return []


def _check_p1p2b2a_effectful_source_provenance(root: Path) -> list[Violation]:
    """X0: original pinned FD identity and the two mounted kernel objects."""
    witness = root / "backend/tests/native/test_p1b27b2b3b2b3a_effectful_mount.py"
    try:
        tree = ast.parse(witness.read_text(encoding="utf-8"))
        nodes = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        pin = ast.unparse(nodes["_pinned_kernel_mount_identity"])
        shared = ast.unparse(nodes["_original_effectful_fd_mount_command"])
        case = nodes["test_original_fd_bind_argv_is_consumed_by_real_bubblewrap"]
        case_text = ast.unparse(case)
        for token in (
            "lease.verify_kernel_custody()",
            "lease.pins",
            "os.fstat(source.fd)",
            "source.device",
            "source.inode",
            "source.fd",
        ):
            if token not in pin:
                raise ValueError(f"original kernel pin witness removed {token}")
        for token in (
            "OriginalBindSourceLease.pin_prepared(request)",
            "backend._command(",
            "_pinned_kernel_mount_identity(lease",
            "pinned_home",
            "pinned_workspace",
            "atomic.rewind_for_bubblewrap()",
            "_require_original_native_fd_handoff(",
        ):
            if token not in shared:
                raise ValueError(f"original FD mount launch removed {token}")
        for token in (
            "_original_effectful_fd_mount_command(",
            "run_command(*argv,",
            "result.returncode == 0",
        ):
            if token not in case_text:
                raise ValueError(f"original mounted-source execution removed {token}")
        literals = [
            n.value
            for n in ast.walk(case)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and "stat -Lc" in n.value
        ]
        if len(literals) != 1 or literals[0].count("stat -Lc '%d:%i'") != 2:
            raise ValueError("two original FD-bind mounted identities required")
        for token in (
            'stat -Lc \'%d:%i\' /home/fleet)" = "$4"',
            'stat -Lc \'%d:%i\' /workspace)" = "$5"',
            "sealed-FD-data-v1",
            "/tmp/oh-goon-fd-proof",
            "/proc/self/mountinfo",
        ):
            if token not in literals[0]:
                raise ValueError(f"original mounted object check removed {token}")
    except (OSError, SyntaxError, ValueError, KeyError) as exc:
        return [Violation(witness, f"X0 P1P2B2A physical source provenance drift: {exc}")]
    return []


def _check_p1p2b2b1_effectful_root_scaffold(root: Path) -> list[Violation]:
    """X0: never replace the original sparse-root mount scaffold with host /."""
    witness = root / "backend/tests/native/test_p1b27b2b3b2b3a_effectful_mount.py"
    try:
        source = witness.read_text(encoding="utf-8")
        tree = ast.parse(source)
        nodes = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        names = (
            "_prepared_effectful_runtime_root",
            "test_effectful_fixture_has_original_mount_scaffold",
            "test_original_fd_bind_argv_is_consumed_by_real_bubblewrap",
        )
        if not all(name in nodes for name in names):
            raise ValueError("effectful fixture scaffold or native test missing")
        helper = ast.unparse(nodes[names[0]])
        test = ast.unparse(nodes[names[2]])
        for token in (
            "_create_mount_scaffold(",
            "PurePosixPath('/tmp/oh-goon-fd-proof')",
            "files.update(_dynamic_dependencies(",
            "shutil.copy2(",
        ):
            if token not in helper:
                raise ValueError(f"original runtime scaffold preparation missing {token}")
        for token in (
            "_prepared_effectful_runtime_root(",
            "OriginalBindSourceLease.pin_prepared(request)",
            "backend._command(",
            "run_command(*argv,",
            "result.returncode == 0",
        ):
            if token not in test:
                raise ValueError(f"original native witness path missing {token}")
        if (
            "request.attempt.runtime_root.root = Path('/')" in test
            or "monkeypatch.setattr(owner, '_prepare_mounts'" in test
        ):
            raise ValueError("host root shortcut or replaced mount builder reintroduced")
    except (OSError, SyntaxError, ValueError) as exc:
        return [Violation(witness, f"X0 P1P2B2B1 native scaffold drift: {exc}")]
    return []


def _check_p1p2b2b2_canonical_runtime_root(root: Path) -> list[Violation]:
    """X0: every mechanical witness shares the one frozen production root."""
    witness = root / "backend/tests/native/test_p1b27b2b3b2b3a_effectful_mount.py"
    owner = root / "backend/src/oh_goon/integrations/_fleet_plan_runtime_root.py"
    try:
        tree = ast.parse(witness.read_text(encoding="utf-8"))
        nodes = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        prepare = ast.unparse(nodes["_prepared_effectful_runtime_root"])
        shared = ast.unparse(nodes["_original_effectful_fd_mount_command"])
        native = ast.unparse(nodes["test_original_fd_bind_argv_is_consumed_by_real_bubblewrap"])
        for fragment in (
            "HostToolBinding(",
            "snapshot_host_path(",
            "capture_runtime_root(",
            "verify_runtime_root(runtime)",
            "('sh', 'cat', 'stat', 'awk', 'sleep')",
            "runtime_inspector=",
            "network_enabled=False",
        ):
            if fragment not in prepare:
                raise ValueError(f"canonical runtime capture missing {fragment}")
        for fragment in (
            "_prepared_effectful_runtime_root(",
            "request.attempt.runtime_root = runtime",
            "request.attempt.host_tools = tools",
            "verify_runtime_root(request.attempt.runtime_root)",
            "OriginalBindSourceLease.pin_prepared(request)",
            "backend._command(",
            "_require_original_native_fd_handoff(",
        ):
            if fragment not in shared:
                raise ValueError(f"canonical native launch missing {fragment}")
        if (
            "_original_effectful_fd_mount_command(" not in native
            or "run_command(*argv," not in native
        ):
            raise ValueError("original witness must use shared native mount launch")
        for illegal in (
            "shutil.copy2(",
            "_create_mount_scaffold(",
            "_dynamic_dependencies(",
            "Path('/')",
            "monkeypatch.setattr(",
            "lambda _: None",
            "capture_not_canonical_root(",
        ):
            if any(illegal in source for source in (prepare, shared, native)):
                raise ValueError(f"noncanonical fixture preparation/bypass: {illegal}")
        production = owner.read_text(encoding="utf-8")
        if (
            "def capture_runtime_root(" not in production
            or "identity = snapshot_identity(destination" not in production
            or "def verify_runtime_root(" not in production
        ):
            raise ValueError("original production runtime snapshot ownership changed")
    except (OSError, SyntaxError, ValueError, KeyError) as exc:
        return [Violation(witness, f"X0 P1P2B2B2 canonical runtime-root drift: {exc}")]
    return []


def _check_p3a_original_sample_rounds(package: Path) -> list[Violation]:
    """X0: one Fleet writer and one journal verifier own repeatable P3 samples."""
    sampler = package / "runtime/enforcer_lace_original_physical_sampling.py"
    owner = package / "runtime/enforcer_lace_fleet_resource_owner.py"
    journal = package / "runtime/enforcer_lace_journal.py"
    try:
        trees = [ast.parse(path.read_text(encoding="utf-8")) for path in (sampler, owner, journal)]
        functions = [
            node
            for node in ast.walk(trees[0])
            if isinstance(node, ast.FunctionDef) and node.name == "original_sample_position"
        ]
        methods = [
            node
            for node in ast.walk(trees[1])
            if isinstance(node, ast.FunctionDef)
            and node.name == "sample_recovery_physical_unqualified"
        ]
        if len(functions) != 1 or len(methods) != 1:
            raise ValueError("one canonical lifecycle-position and Fleet sampling owner required")
        positions = ast.unparse(functions[0])
        flow = ast.unparse(methods[0])
        verifier = journal.read_text(encoding="utf-8")
        for name in (
            "RESOURCE_PHYSICAL_SAMPLE_RECORDED",
            "RESOURCE_CLEANUP_PREPARED",
            "RESOURCE_CLEANUP_NATIVE_STARTED",
            "RESOURCE_CLEANUP_NATIVE_RECORDED",
            "len(samples) >= 256",
        ):
            if name not in positions:
                raise ValueError(f"original cleanup/sample ancestry missing {name}")
        for name in (
            "sample_round",
            "previous_sample_run_seq",
            "cleanup_started_run_seq",
            "cleanup_recorded_run_seq",
            "original_sample_position(",
            "after = validate_cut(connection)",
            "if before != after:",
            "journal._append_locked(self.run_id, 'RESOURCE_PHYSICAL_SAMPLE_RECORDED', payload)",
        ):
            if name not in flow:
                raise ValueError(f"original sample journal write lost {name}")
        for name in (
            "original_sample_position(",
            "supplied_position != expected_position",
            "oh-goon/original-unqualified-physical-sample/v2",
            "original physical observation round or cleanup ancestry forged",
        ):
            if name not in verifier:
                raise ValueError(f"original journal verifier bypassed {name}")
    except (OSError, SyntaxError, ValueError) as exc:
        return [Violation(sampler, f"X0 P3A canonical lifecycle observation drift: {exc}")]
    return []


def _check_p3b1_original_convergence_assessment(package: Path) -> list[Violation]:
    """X0: the original owner alone reads verified nonauthorizing P3 rounds."""
    sampler = package / "runtime/enforcer_lace_original_physical_sampling.py"
    owner = package / "runtime/enforcer_lace_fleet_resource_owner.py"
    try:
        helper_tree = ast.parse(sampler.read_text(encoding="utf-8"))
        owner_tree = ast.parse(owner.read_text(encoding="utf-8"))
        helper = [
            n
            for n in ast.walk(helper_tree)
            if isinstance(n, ast.FunctionDef)
            and n.name == "assess_original_physical_convergence_unqualified"
        ]
        methods = [
            n
            for n in ast.walk(owner_tree)
            if isinstance(n, ast.FunctionDef)
            and n.name == "assess_recovery_physical_convergence_unqualified"
        ]
        if len(helper) != 1 or len(methods) != 1:
            raise ValueError("exactly one original P3 convergence helper and owner required")
        source, method = ast.unparse(helper[0]), ast.unparse(methods[0])
        for part in (
            "original_sample_position(",
            "'CLEANUP_INTERRUPTED'",
            "'VISIBLE_RESOURCES_REMAIN'",
            "'INCOMPLETE_OR_CHANGED_SCOPE'",
            "'REPEATED_SCOPED_NONDETECTION'",
            "len(post) < 2",
            "binding.envelope.identity",
            "binding.mount_intent_identity",
            "binding.scope",
            "fact['state'] == 'PRESENT'",
            "fact['state'] == 'UNKNOWN'",
        ):
            if part not in source:
                raise ValueError(f"P3 original bounded visibility check missing {part}")
        for part in (
            "self.validate_authority_in(connection)",
            "self.session.original_fleet_lease_token()",
            "require_original_physical_sampling_in(",
            "journal.verify(self.run_id, auth=self.authenticator)",
            "assess_original_physical_convergence_unqualified(",
            "return mutate(original_cut)",
        ):
            if part not in method:
                raise ValueError(f"P3 original recovery owner guard missing {part}")
        for forbidden in ("observe_kernel(", "_append_locked(", "run_command(", "native_action("):
            if forbidden in source or forbidden in method:
                raise ValueError("P3 assessment must not add scanner, writer or effect")
    except (OSError, SyntaxError, ValueError) as exc:
        return [Violation(sampler, f"X0 P3B1 original nonauthorizing convergence drift: {exc}")]
    return []


def _check_p3b2a_complete_postcleanup_roster(package: Path) -> list[Violation]:
    """X0: only ONE port roster, ONE shared observer, ONE nonauthorizing reader."""
    port = package / "core/ports/physical_observation.py"
    observer = package / "integrations/linux_physical_observer.py"
    owner = package / "runtime/enforcer_lace_original_physical_sampling.py"
    try:
        source = port.read_text(encoding="utf-8")
        shared = observer.read_text(encoding="utf-8")
        assessor = owner.read_text(encoding="utf-8")
        trees = [ast.parse(value) for value in (source, shared, assessor)]
        definitions = [
            node
            for node in trees[0].body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "ORIGINAL_KERNEL_COMPONENTS"
                for target in node.targets
            )
        ]
        if len(definitions) != 1:
            raise ValueError("original component roster must have one port owner")
        names = ast.literal_eval(definitions[0].value)
        required = (
            "cgroup",
            "subtree",
            "process",
            "mounts",
            "network",
            "tracked_mount_namespace",
            "tracked_network_namespace",
            "visible_namespace_fd_holders",
            "visible_foreign_mount_targets",
            "visible_mount_fd_holders",
        )
        if type(names) is not tuple or names != required:
            raise ValueError("original shared kernel component roster changed")
        if (
            shared.count("ORIGINAL_KERNEL_COMPONENTS") != 2
            or "if tuple(first) != ORIGINAL_KERNEL_COMPONENTS:" not in shared
            or 'raise ValueError("original physical observer component roster changed")'
            not in shared
        ):
            raise ValueError("shared observer must enforce original port roster")
        funcs = [
            node
            for node in ast.walk(trees[2])
            if isinstance(node, ast.FunctionDef)
            and node.name == "assess_original_physical_convergence_unqualified"
        ]
        if len(funcs) != 1:
            raise ValueError("one original post-cleanup assessor required")
        method = ast.unparse(funcs[0])
        if (
            "ORIGINAL_KERNEL_COMPONENTS" not in method
            or "!= ORIGINAL_KERNEL_COMPONENTS" not in method
            or "'INCOMPLETE_OR_CHANGED_SCOPE'" not in method
            or "'REPEATED_SCOPED_NONDETECTION'" not in method
            or "'teardown_proven'" in method
        ):
            raise ValueError("original assessor roster/absence fence removed")
        for forbidden in ("observe_kernel(", "_append_locked(", "run_command("):
            if forbidden in method:
                raise ValueError("post-cleanup assessor may not observe, publish or execute")
    except (OSError, SyntaxError, ValueError, TypeError) as exc:
        return [Violation(owner, f"X0 P3B2A original post-cleanup roster drift: {exc}")]
    return []


def _check_p3b2b_original_native_lifecycle(root: Path) -> list[Violation]:
    """X0: native timeout/cancel/revoke/orphan tests consume one original launcher."""
    witness = root / "backend/tests/native/test_p1b27b2b3b2b3a_effectful_mount.py"
    try:
        tree = ast.parse(witness.read_text(encoding="utf-8"))
        methods = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        case = methods["test_original_effectful_fd_mount_lifecycle_is_supervised"]
        native = ast.unparse(case)
        shared = ast.unparse(methods["_original_effectful_fd_mount_command"])
        for token in (
            "_original_effectful_fd_mount_command(",
            "run_command(*argv,",
            "require_process_group_quiescence=True",
            "CommandCancelledError",
            "CommandAuthorityTerminatedError",
            "CommandTimeoutExpired",
            "TerminationSignal(",
            "cause='trust_revoke'",
            "process_group_cleanup_requested",
            "final_process_group_quiescent",
            "P3_NATIVE_CHILD_READY",
            "/proc/self/mountinfo",
            "stat -Lc",
            "sleep 20 &",
        ):
            if token not in native:
                raise ValueError(f"native lifecycle supervision contract removed {token}")
        if native.count("P3_NATIVE_CHILD_READY") != 2:
            raise ValueError("real child readiness and supervised observation are both required")
        shell = [
            n.value
            for n in ast.walk(case)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and "stat -Lc" in n.value
        ]
        if len(shell) != 1 or shell[0].count("stat -Lc '%d:%i'") != 2:
            raise ValueError("both real bind targets must retain kernel identity checks")
        for token in (
            "OriginalBindSourceLease.pin_prepared(request)",
            "verify_runtime_root(request.attempt.runtime_root)",
            "backend._command(",
            "_require_original_native_fd_handoff(",
        ):
            if token not in shared:
                raise ValueError(f"original kernel mount authority removed {token}")
        if (
            "subprocess.Popen(" in native
            or "subprocess.run(" in native
            or "pytest.skip(" in native
            or "monkeypatch" in native
        ):
            raise ValueError("second launcher, skipped kernel test or fake observer forbidden")
    except (OSError, SyntaxError, ValueError, KeyError) as exc:
        return [Violation(witness, f"X0 P3B2B native lifecycle owner drift: {exc}")]
    return []


def _check_co_r3u1_native_custody_fail_before_effect(package: Path) -> list[Violation]:
    """Guard ORIGINAL native effect paths; never substitute a second LACE owner."""
    native = package / "integrations/_fleet_plan_cgroup_resources.py"
    reaper = package / "integrations/fleet_plan_local_lace_native_reaper.py"
    scope = package / "core/models/original_native_custody.py"
    try:
        tree = ast.parse(native.read_text(encoding="utf-8"))
        reaper_tree = ast.parse(reaper.read_text(encoding="utf-8"))
        scope_tree = ast.parse(scope.read_text(encoding="utf-8"))
        funcs = {
            n.name: ast.unparse(n)
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        methods = {
            n.name: ast.unparse(n)
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name in {"create", "open", "close"}
        }
        reaper_methods = {
            n.name: ast.unparse(n) for n in ast.walk(reaper_tree) if isinstance(n, ast.FunctionDef)
        }
        models = [
            n
            for n in scope_tree.body
            if isinstance(n, ast.ClassDef) and n.name == "OriginalNativeCustodyRequirement"
        ]
        if len(models) != 1 or not any(
            "frozen=True" in ast.unparse(item) for item in models[0].decorator_list
        ):
            raise ValueError("missing immutable custody requirement")
        gate = funcs["require_independent_native_parent_custody"]
        if (
            "raise FleetPlanInvalid(" not in gate
            or "no independently provisioned kernel parent owner" not in gate
        ):
            raise ValueError("native custody gate must remain non-issuable until OS provisioning")
        for method, phrase in (
            (
                "create",
                "require_independent_native_parent_custody(operation='native cgroup admission')",
            ),
            (
                "open",
                "_refuse_unprovisioned_kernel_effect(self.root, operation='native cgroup allocation')",
            ),
            (
                "close",
                "_refuse_unprovisioned_kernel_effect(self.cgroup, operation='native cgroup teardown')",
            ),
        ):
            if phrase not in methods[method]:
                raise ValueError(f"original cgroup {method} bypassed custody gate")
        for method, operation in (
            ("_kill_cgroup", "native cgroup kill"),
            ("_remove_cgroup", "native cgroup removal"),
            ("_mkdir_bound_cgroup_child", "native delegated child creation"),
            ("_write_bound_cgroup_control", "native delegated control write"),
        ):
            target = "parent" if method == "_mkdir_bound_cgroup_child" else "cgroup"
            if (
                f"_refuse_unprovisioned_kernel_effect({target}, operation='{operation}')"
                not in funcs[method]
            ):
                raise ValueError(f"original {method} bypassed native effect gate")
        reaper_body = reaper_methods["execute_checked"]
        pre = reaper_body.find("require_independent_native_parent_custody(")
        effects = [
            reaper_body.find(token) for token in ("_cleanup_egress_resources(", "_remove_cgroup(")
        ]
        if (
            pre < 0
            or any(location < pre for location in effects)
            or any(location < 0 for location in effects)
        ):
            raise ValueError("original journal reaper reached effects before native custody gate")
    except (OSError, SyntaxError, ValueError, KeyError) as error:
        return [Violation(native, f"X0 CO-R3U1 native custody path drift: {error}")]
    return []


def _check_co_r3u2a_original_egress_custody(package: Path) -> list[Violation]:
    """One original network owner, same non-issuing LACE gate as cgroup.

    Check each public/direct helper at the original native-effect cut. A new
    helper, alternate issuer or local permit cannot replace physical custody.
    """
    path = package / "integrations/_fleet_plan_restricted_egress.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [n for n in tree.body if isinstance(n, ast.ImportFrom)]
        if not any(
            n.module == "oh_goon.integrations._fleet_plan_cgroup_resources"
            and any(a.name == "require_independent_native_parent_custody" for a in n.names)
            for n in imports
        ):
            raise ValueError("restricted egress must reuse the original non-issuing LACE gate")
        targets = {
            "create": "native restricted-egress admission",
            "open": "native restricted-egress allocation",
            "_open_link": "native restricted-egress link creation",
            "_run_setup": "native restricted-egress setup command",
            "close": "native restricted-egress teardown",
            "_write_egress_lease_record": "native restricted-egress pending record",
            "_cleanup_egress_resources": "native restricted-egress removal",
        }
        funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        for name, operation in targets.items():
            matches = [n for n in funcs if n.name == name]
            if len(matches) != 1:
                raise ValueError(f"original egress {name} definition changed")
            method = matches[0]
            native_cut = [
                n
                for n in ast.walk(method)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "require_independent_native_parent_custody"
                and any(
                    k.arg == "operation"
                    and isinstance(k.value, ast.Constant)
                    and k.value.value == operation
                    for k in n.keywords
                )
            ]
            if (
                len(native_cut) != 1
                or native_cut[0].lineno != method.body[0].lineno
                and (
                    name
                    not in {
                        "create",
                        "open",
                        "close",
                        "_cleanup_egress_resources",
                        "_write_egress_lease_record",
                    }
                )
            ):
                raise ValueError(f"original egress {name} has no early native custody gate")
            # The custody gate must not be under try/except, loops, if, or
            # conditional mutation work. It must precede native helper calls.
            if not any(
                isinstance(node, ast.Expr) and node.value is native_cut[0] for node in method.body
            ):
                raise ValueError(f"original egress {name} custody gate must be unconditional")
            guard_position = native_cut[0].lineno
            effects = {
                "create": ("digest",),
                "open": ("_open_link", "write_text"),
                "_open_link": ("_egress_allocation_lock", "_write_egress_lease_record"),
                "_run_setup": ("run_command",),
                "close": ("_egress_allocation_lock", "remove_file_durably"),
                "_write_egress_lease_record": ("ensure_private_directory", "publish_text_file"),
                "_cleanup_egress_resources": ("run_command",),
            }[name]
            for call in (n for n in ast.walk(method) if isinstance(n, ast.Call)):
                called = (
                    call.func.id
                    if isinstance(call.func, ast.Name)
                    else call.func.attr
                    if isinstance(call.func, ast.Attribute)
                    else None
                )
                if called in effects and call.lineno < guard_position:
                    raise ValueError(f"original egress {name} has effect before custody gate")
    except (OSError, SyntaxError, ValueError) as error:
        return [Violation(path, f"X0 CO-R3U2A restricted egress custody drift: {error}")]
    return []


def _check_co_r3u2b1_original_launch_effect_cuts(package: Path) -> list[Violation]:
    """Freeze the *existing* effect owners; do not create a second launcher/gate."""
    cg = package / "integrations/_fleet_plan_cgroup_resources.py"
    nested = package / "integrations/_fleet_plan_nested_cgroup_launcher.py"
    network = package / "integrations/_fleet_plan_restricted_egress.py"
    try:
        trees = [ast.parse(p.read_text(encoding="utf-8")) for p in (cg, nested, network)]
        funcs = [{n.name: n for n in ast.walk(t) if isinstance(n, ast.FunctionDef)} for t in trees]

        def gate_before(path: Path, function: ast.FunctionDef, gate: str, effect: str) -> None:
            """An unconditional top-level gate must precede every target effect."""
            body = [
                n
                for n in function.body
                if not (
                    isinstance(n, ast.Expr)
                    and isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, str)
                )
            ]
            cuts = [
                n
                for n in body
                if isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Call)
                and ast.unparse(n.value).startswith(gate)
            ]
            if not cuts or body[0] is not cuts[0]:
                raise ValueError(
                    f"{path.name}:{function.name}: missing earliest unconditional original gate"
                )
            calls = [
                n
                for n in ast.walk(function)
                if isinstance(n, ast.Call) and ast.unparse(n).startswith(effect)
            ]
            if calls and min(n.lineno for n in calls) <= max(n.lineno for n in cuts):
                raise ValueError(f"{path.name}:{function.name}: effect precedes native custody")

        # CO-R3U2B2A: a negative/unknown mount classification never issues an
        # effect. Guard the single ORIGINAL native effect gate structurally.
        # The first executable statement after the docstring must always call
        # the non-issuing custodian, without a filesystem-dependent branch.
        gate_before(
            cg,
            funcs[0]["_refuse_unprovisioned_kernel_effect"],
            "require_independent_native_parent_custody(",
            "os.write(",
        )
        for name, op in (
            ("command_prefix", "native cgroup process launch"),
            ("_apply_resource_limits", "native cgroup limit write"),
            ("_write_limit", "native cgroup direct limit write"),
        ):
            function = funcs[0][name]
            gate_before(cg, function, "_refuse_unprovisioned_kernel_effect(", "os.write(")
            if op not in ast.unparse(function):
                raise ValueError(f"{name}: native effect cut missing: {op}")
        source = cg.read_text(encoding="utf-8")
        if (
            '_CGROUP_EXEC = """raise RuntimeError(\'original LACE independent kernel custody NOT PROVEN before process attachment\')'
            not in source
        ):
            raise ValueError("unqualified -S child bootstrap can attach processes")
        function = funcs[1]["launch_nested_bubblewrap"]
        gate_before(nested, function, "_refuse_unprovisioned_kernel_effect(", "move_process(")
        source = ast.unparse(function)
        if (
            source.count("operation='native nested cgroup process launch'") != 1
            or source.count("operation='native monitor cgroup process launch'") != 1
        ):
            raise ValueError("nested and monitor originals must each have native effect custody")
        if source.index("native monitor cgroup process launch") > source.index("move_process("):
            raise ValueError("monitor custody must precede all nested process moves")
        imports = [n for n in trees[1].body if isinstance(n, ast.ImportFrom)]
        if not any(
            n.module == "oh_goon.integrations._fleet_plan_cgroup_resources"
            and {"_refuse_unprovisioned_kernel_effect", "_write_process_to_cgroup"}
            <= {a.name for a in n.names}
            for n in imports
        ):
            raise ValueError("nested launcher must reuse original LACE owner")
        net_prefix = funcs[2]["command_prefix"]
        gate_before(network, net_prefix, "require_independent_native_parent_custody(", "return(")
        if "native restricted-egress process launch" not in ast.unparse(net_prefix):
            raise ValueError("network process prefix lacks original native custody")
    except (OSError, SyntaxError, ValueError, KeyError) as error:
        return [Violation(cg, f"X0 CO-R3U2B1 original launch custody drift: {error}")]
    return []


def _check_co_r3u2b2b1_host_effect_preconditions(package: Path) -> list[Violation]:
    """Host mount readiness and signed pre-effect revalidation, not a new issuer."""
    cg = package / "integrations/_fleet_plan_cgroup_resources.py"
    launcher = package / "integrations/fleet_plan_bubblewrap_isolation.py"
    try:
        cgtree = ast.parse(cg.read_text(encoding="utf-8"))
        launchtree = ast.parse(launcher.read_text(encoding="utf-8"))
        methods = {n.name: n for n in ast.walk(cgtree) if isinstance(n, ast.FunctionDef)}
        host = ast.unparse(methods["_inspect_delegated_cgroup_v2_root"])
        if (
            "os.statvfs(root).f_flag" not in host
            or "filesystem_flags & getattr(os, 'ST_RDONLY', 1)" not in host
            or "root is read-only" not in host
            or host.index("os.statvfs(root).f_flag") >= host.index("os.access(root, os.W_OK)")
        ):
            raise ValueError("original kernel root cannot trust os.access or unknown mount flags")
        custody = ast.unparse(methods["require_independent_native_parent_custody"])
        if (
            "not requirement.complete_scope" not in custody
            or "no independently provisioned kernel parent owner" not in custody
            or custody.index("not requirement.complete_scope")
            >= custody.index("no independently provisioned kernel parent owner")
        ):
            raise ValueError(
                "original custody lost non-issuance or generation/identity precondition"
            )
        executions = [
            n
            for n in ast.walk(launchtree)
            if isinstance(n, ast.FunctionDef)
            and n.name == "execute"
            and any(
                isinstance(x, ast.Call)
                and isinstance(x.func, ast.Attribute)
                and x.func.attr == "create"
                and isinstance(x.func.value, ast.Name)
                and x.func.value.id == "CgroupV2AttemptLease"
                for x in ast.walk(n)
            )
        ]
        if len(executions) != 1:
            raise ValueError("original Bubblewrap LACE execution owner unavailable")
        # Check the same top-level statements inside the original pinned-mount
        # context; do not accept an authorization check nested under a branch.
        mount_context = [
            n
            for n in ast.walk(executions[0])
            if isinstance(n, ast.With)
            and any(
                isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Attribute)
                and item.context_expr.func.attr == "pin_prepared"
                for item in n.items
            )
        ]
        if len(mount_context) != 1:
            raise ValueError("one original prepared mount context required")
        statements = mount_context[0].body
        calls = [ast.unparse(n) for n in statements]

        def exactly_one_cut_before(prefix: str) -> None:
            indices = [i for i, code in enumerate(calls) if code.startswith(prefix)]
            if (
                len(indices) != 1
                or indices[0] == 0
                or calls[indices[0] - 1] != "require_effect_authority()"
            ):
                raise ValueError(
                    f"original signed effect revalidation must immediately precede {prefix}"
                )

        exactly_one_cut_before("resource_lease = CgroupV2AttemptLease.create(")
        exactly_one_cut_before("network_lease = RestrictedEgressLease.create(")
    except (OSError, SyntaxError, KeyError, ValueError) as error:
        return [Violation(cg, f"X0 CO-R3U2B2B1 original host effect preconditions drift: {error}")]
    return []


def _check_co_r3u2b2b2a_native_qualification_cut(root: Path) -> list[Violation]:
    """Original CORE qualification must not replay obsolete native positive effects."""
    qualifier = root / "scripts/qualify_b12_development_wsl.sh"
    refusal = root / "backend/tests/native/test_co_r3u2b2b2a_native_refusal.py"
    try:
        shell = qualifier.read_text(encoding="utf-8")
        code = ast.unparse(ast.parse(refusal.read_text(encoding="utf-8")))
        marker = "if [[ -f .checkpoint/CO_R3U2B2B2A_SOURCE.sha256 ]]; then"
        legacy = "# CO-R3N real cgroup-v2 evidence is opt-in inside the ORIGINAL CORE ring."
        if (
            shell.count(marker) != 1
            or shell.count(legacy) != 1
            or shell.index(marker) >= shell.index(legacy)
        ):
            raise ValueError(
                "non-issuing custody blocker must precede original positive enrollment"
            )
        block = shell.split(marker, 1)[1].split("\nfi\n", 1)[0]
        # CO-R3U2B2B2B1: rejecting a known impossible native-positive request
        # AFTER uv sync can mutate the development environment before refusal.
        # Keep the same original CORE supervisor but make the original custody
        # blocker a strict pre-environment admission gate.
        admission_anchor = "qualify_host_sample 'UV_SYNC_BEFORE'"
        latest_manifest = "if [[ -f .checkpoint/CO_R3U2B2B2B1_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3U2B2B2B1_SOURCE.sha256'; fi"
        if shell.count(latest_manifest) != 1 or shell.index(latest_manifest) >= shell.index(
            'if [[ -f "$embedded" ]] &&'
        ):
            raise ValueError(
                "current original qualifier must pin the current source manifest before work"
            )
        if not (
            shell.index('> "$evidence/PRE.txt"')
            < shell.index(marker)
            < shell.index(admission_anchor)
            < shell.index("uv sync --locked --group dev")
        ):
            raise ValueError("obsolete native positive request must refuse before environment work")
        for expected in (
            'if [[ ${QUALIFY_R3N_CGROUP_ROOT:-} != "" ]]; then',
            "BLOCKED_NON_ISSUING_LACE",
            "legacy_positive_native_witness_incompatible_with_current_physical_gate",
            "phase=BLOCKED_UNPROVISIONED_KERNEL_CUSTODY",
            "blocked_before_environment=YES",
            "exit 3",
        ):
            if expected not in block:
                raise ValueError(f"original positive kernel effects not fenced: {expected}")
        start = shell.index("run_ring CORE \\")
        end = shell.index("# Do not promote REQUESTED into evidence", start)
        core = shell[start:end]
        if (
            core.count('"${r3u2b2b2a_refusal_tests[@]}"') != 1
            or core.index('"${r3u2b2b2a_refusal_tests[@]}"')
            > core.index('"${r3n_native_tests[@]}"')
            or "tests/native/test_co_r3u2b2b2a_native_refusal.py" not in block
        ):
            raise ValueError("one original CORE must require the live kernel refusal witness")
        if code.count("cg.require_independent_native_parent_custody(") != 2:
            raise ValueError("both live-kernel host/scope non-issuance controls required")
        for required in (
            "test_live_kernel_mount_prerequisite_never_grants_effect_permission",
            "test_live_kernel_identity_scope_cannot_self_issue_custody",
            "test_live_kernel_original_lease_refuses_before_lock_and_syscall",
            "cg.require_independent_native_parent_custody(",
            "cg.CgroupV2AttemptLease(",
            "with pytest.raises(FleetPlanInvalid, match='NOT PROVEN'):",
        ):
            if required not in code:
                raise ValueError(f"real-kernel negative control missing: {required}")
    except (OSError, ValueError, SyntaxError) as error:
        return [
            Violation(
                qualifier, f"X0 CO-R3U2B2B2A current native refusal qualification drift: {error}"
            )
        ]
    return []
