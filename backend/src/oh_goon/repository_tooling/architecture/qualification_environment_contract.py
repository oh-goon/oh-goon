"""QENV1: one existing qualification path owns dependency preparation.

Static anti-drift policy, not a replacement for the original WSL witness,
source audit, supervisor, or release/security qualification.
"""

from __future__ import annotations

import re
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation
from oh_goon.repository_tooling.architecture.repository_layout import ROOT


def check_qualification_environment_contract(root: Path = ROOT) -> list[Violation]:
    witness = root / "scripts/qualify_b12_development_wsl.sh"
    makefile = root / "Makefile"
    issues: list[Violation] = []
    try:
        shell = witness.read_text(encoding="utf-8")
        make = makefile.read_text(encoding="utf-8")
    except OSError:
        return [Violation(witness, "QENV1 original qualifier/Makefile missing or unreadable")]

    # The Make target is the sole front door. No alternate inlined recipe may
    # prepare or repair dependencies outside the canonical owner.
    block = re.search(r"(?m)^qualify-development-wsl:\s*\n((?:[\t ].*\n)+)", make)
    if (
        not block
        or "source-transport-clean" not in block.group(1)
        or ("scripts/qualify_b12_development_wsl.sh" not in block.group(1))
        or re.search(r"\b(?:uv|pip|poetry|pipenv)\s+(?:sync|install|run|add)\b", block.group(1))
    ):
        issues.append(Violation(makefile, "QENV1 requires the one canonical make front door"))

    try:
        setup = shell.split("# Canonical qualification environment transaction:", 1)[1].split(
            "# The ring is supervised", 1
        )[0]
        ring = shell.split("run_ring() {", 1)[1].split("\nlast_result=0", 1)[0]
    except IndexError:
        return issues + [Violation(witness, "QENV1 missing explicit setup and ring boundaries")]

    # Ignore shell comment lines when inspecting executable phase statements.
    setup = "\n".join(line for line in setup.splitlines() if not line.lstrip().startswith("#"))
    ring = "\n".join(line for line in ring.splitlines() if not line.lstrip().startswith("#"))
    sync_cmd = "uv sync --locked --group dev"
    verify_cmd = "uv sync --check --locked --offline --group dev"
    if setup.count(sync_cmd) != 1 or setup.count(verify_cmd) != 1:
        issues.append(Violation(witness, "QENV1 must prepare exactly once and check exactly once"))
    if not (0 <= setup.find(sync_cmd) < setup.find(verify_cmd) < setup.find("UV_ENV_VERIFIED.txt")):
        issues.append(Violation(witness, "QENV1 prepare/verify/receipt ordering was changed"))
    for required in (
        'qualification_python="$root/backend/.venv/bin/python"',
        '"$qualification_python" -c',
        "-m oh_goon.repository_tooling.check_architecture",
        "UV_ENV_VERIFY=",
        "lock_sha256=",
        "pyproject_sha256=",
        "if (( uv_verify_rc != 0 )); then",
    ):
        if required not in setup:
            issues.append(Violation(witness, f"QENV1 missing environment verification: {required}"))
    # Treat project uv entrypoints as setup operations, even when --no-sync is
    # present: ring processes must not ask uv to decide anything at all.
    if re.search(r"\b(?:uv|pip|pip3|poetry|pipenv)\b|\s-m\s+pip\b", ring):
        issues.append(Violation(witness, "QENV1 dependency manager invoked inside test ring"))
    if '"$qualification_python" -m oh_goon.repository_tooling.b11_ring_supervisor' not in ring:
        issues.append(Violation(witness, "QENV1 ring must execute verified interpreter directly"))
    for defense in ("PYTHONNOUSERSITE=1", "UV_NO_SYNC=1", "UV_OFFLINE=1", "PIP_NO_INDEX=1"):
        if defense not in ring:
            issues.append(Violation(witness, f"QENV1 missing ring no-sync defense: {defense}"))
    if "INFRA_FAILURE.txt" not in ring or "missing_receipt" not in ring:
        issues.append(Violation(witness, "QENV1 ring must fail closed on infrastructure loss"))
    if 'source_transport_audit > "$evidence/TRANSPORT_POST.json"' not in shell:
        issues.append(Violation(witness, "QENV1 must preserve POST source-integrity witness"))
    # Exact packaged source selection is part of qualification authority.
    # A new candidate MUST override older checkpoint manifests rather than
    # quietly selecting an obsolete digest and failing on the native host.
    # Preserve the older B3B4 attack fixture while enforcing the exact newest
    # packaged manifest on current source. Never demote a present checkpoint.
    checkpoint = next(
        (
            name
            for name in (
                "CO_R3U2B2B2B1",
                "CO_R3T",
                "CO_R3S",
                "CO_R3R",
                "CO_R3Q",
                "CO_R3P",
                "CO_R3O",
                "CO_R3N",
                "CO_R3M",
                "CO_R3L",
                "CO_R3K",
                "CO_R3J",
                "CO_R3I",
                "CO_R3H",
                "CO_R3G",
                "CO_R3F",
                "CO_R3E",
                "CO_R3D",
                "CO_R3C",
                "CO_R3B",
                "CO_R3A",
                "CO_R2C_Q1",
                "CO_R2C",
                "CO_R2W3",
                "CO_R2W2",
                "CO_R2W1",
                "CO_R01",
                "DB6",
                "DB5",
                "DB4",
                "DB3",
                "DB2",
                "DB1B2",
                "DB1B1",
                "DB0DB1A",
                "P1B27_GATE_P3B2B",
                "P1B27_GATE_P3B2A",
                "P1B27_GATE_P3B1",
                "P1B27_GATE_P3A",
                "P1B27_GATE_P1P2B2B3",
                "P1B27_GATE_P1P2B2B2",
                "P1B27_GATE_P1P2B2B1",
                "P1B27_GATE_P1P2B2B",
                "P1B27_GATE_P1P2B2A",
                "P1B27_GATE_P1P2B1",
                "P1B27_GATE_P1P2A1",
                "P1B27_GATE_P1P2A",
                "P1B27B2B3B2B3B",
                "P1B27B2B3B2B3A",
                "P1B27B2B3B2B2",
                "P1B27B2B3B2B1",
                "P1B27B2B3B2A",
                "P1B27B2B3B1A",
                "P1B27B2B3B1",
                "P1B27B2B3A",
                "P1B27B2B2A",
                "P1B27B2B1",
                "P1B27B2A",
                "P1B27B1",
                "P1B27A",
                "T0B2B2A",
                "T0B2B1",
                "T0B2A",
                "T0B1",
                "T0A",
                "B3B3B4",
            )
            if (root / f".checkpoint/{name}_SOURCE.sha256").is_file()
        ),
        None,
    )
    # Preserve the original B3B3B4 regression even when the historical
    # AG02 selector's manifest is absent in the attack fixture: a future
    # codebase might restore it, so a reordered selector is never acceptable.
    historical_anchor = (
        "if [[ -f .checkpoint/AG02B2B3B3B3_SOURCE.sha256 ]]; "
        "then embedded='.checkpoint/AG02B2B3B3B3_SOURCE.sha256'; fi"
    )
    b3b4_selector = (
        "if [[ -f .checkpoint/B3B3B4_SOURCE.sha256 ]]; "
        "then embedded='.checkpoint/B3B3B4_SOURCE.sha256'; fi"
    )
    if (
        historical_anchor in shell
        and b3b4_selector in shell
        and shell.index(historical_anchor) > shell.index(b3b4_selector)
    ):
        issues.append(
            Violation(witness, "QENV1 current manifest must override all older source manifests")
        )
    latest = (
        root / f".checkpoint/{checkpoint}_SOURCE.sha256"
        if checkpoint
        else root / ".checkpoint/MISSING"
    )
    if latest.is_file():
        selector = (
            f"if [[ -f .checkpoint/{checkpoint}_SOURCE.sha256 ]]; "
            f"then embedded='.checkpoint/{checkpoint}_SOURCE.sha256'; fi"
        )
        selection = shell.split("# Packaged-source exact-member gate", 1)[-1].split(
            'if [[ -f "$embedded" ]]', 1
        )[0]
        # Later known selectors may be shipped before their manifest exists,
        # but no *present* manifest may supersede the currently selected one.
        lines = [line.strip() for line in selection.splitlines() if line.strip()]
        chosen_at = lines.index(selector) if selector in lines else -1
        later = lines[chosen_at + 1 :] if chosen_at >= 0 else []
        superseded_by_present = any(
            line.startswith("if [[ -f .checkpoint/")
            and (root / ".checkpoint" / line.split(".checkpoint/", 1)[1].split(" ", 1)[0]).is_file()
            for line in later
        )
        if selection.count(selector) != 1 or superseded_by_present:
            issues.append(
                Violation(
                    witness, "QENV1 current manifest must override all older source manifests"
                )
            )
    if (root / ".checkpoint/DB0DB1A_SOURCE.sha256").is_file():
        core_owner_ring = shell.split("run_ring CORE \\", 1)
        if len(core_owner_ring) != 2 or any(
            core_owner_ring[1].split("\n# AG-02B1", 1)[0].count(module) != 1
            for module in (
                "tests/unit/runtime/test_db0db1a_canonical_database_owner.py",
                "../scripts/tests/test_db0db1a_database_owner_architecture.py",
                "tests/unit/services/test_db0db1a_atomic_fleet_owner_cut.py",
            )
        ):
            issues.append(
                Violation(witness, "QENV1 canonical CORE database owner test coverage removed")
            )
    if (root / ".checkpoint/DB1B1_SOURCE.sha256").is_file():
        core = shell.split("run_ring CORE", 1)
        if (
            len(core) != 2
            or core[1]
            .split("\n# AG-02B1", 1)[0]
            .count("tests/unit/runtime/test_db1b1_atomic_domain_operations.py")
            != 1
        ):
            issues.append(
                Violation(witness, "QENV1 DB1B1 canonical SQLite domain tests missing from CORE")
            )
    if (root / ".checkpoint/DB1B2_SOURCE.sha256").is_file():
        core = shell.split("run_ring CORE", 1)
        if len(core) != 2 or any(
            core[1].split("\n# AG-02B1", 1)[0].count(module) != 1
            for module in (
                "tests/unit/runtime/test_db1b2_transaction_owner_and_replay.py",
                "../scripts/tests/test_db1b2_database_owner_architecture.py",
            )
        ):
            issues.append(
                Violation(witness, "QENV1 DB1B2 owner/reread modules missing from original CORE")
            )
    if (root / ".checkpoint/DB2_SOURCE.sha256").is_file():
        core = shell.split("run_ring CORE", 1)
        if (
            len(core) != 2
            or core[1]
            .split("\n# AG-02B1", 1)[0]
            .count("tests/unit/runtime/test_db2_sqlite_admission_and_faults.py")
            != 1
        ):
            issues.append(
                Violation(
                    witness, "QENV1 DB2 SQLite native-fault/identity regression missing from CORE"
                )
            )
    if (root / ".checkpoint/DB3_SOURCE.sha256").is_file():
        core = shell.split("run_ring CORE", 1)
        required = (
            "tests/unit/runtime/test_db3_scout_backup_owner.py",
            "tests/unit/engine/test_commands.py",
            "../scripts/tests/test_db3_backup_owner_architecture.py",
        )
        if len(core) != 2 or any(
            core[1].split("\n# AG-02B1", 1)[0].count(module) != 1 for module in required
        ):
            issues.append(
                Violation(witness, "QENV1 DB3 backup/Scout/live-output tests missing from CORE")
            )
    if (root / ".checkpoint/DB4_SOURCE.sha256").is_file():
        core = shell.split("run_ring CORE", 1)
        if (
            len(core) != 2
            or core[1]
            .split("\n# AG-02B1", 1)[0]
            .count("tests/unit/runtime/test_db4_offline_recovery_certification.py")
            != 1
        ):
            issues.append(
                Violation(witness, "QENV1 DB4 offline recovery/integration tests missing from CORE")
            )
    if (root / ".checkpoint/DB5_SOURCE.sha256").is_file():
        core = shell.split("run_ring CORE", 1)
        if (
            len(core) != 2
            or core[1]
            .split("\n# AG-02B1", 1)[0]
            .count("tests/unit/runtime/test_db5_backup_publication_failures.py")
            != 1
        ):
            issues.append(
                Violation(witness, "QENV1 DB5 publication failure regressions missing from CORE")
            )
    # QENV1 ring coverage: required-module attestation MUST be owned by the
    # same existing ring that actually selects the test file. The exact
    # P1B27B2B2A native evidence demonstrated an AGENT_PREWORK false negative:
    # 770 zero-failure testcases, but three mount modules selected only in
    # PHYSICAL_OBSERVER. Keep the original supervisor strict; repair ownership.
    try:
        physical_requirement = shell.split('if [[ "$name" == PHYSICAL_OBSERVER* ]]; then', 1)[
            1
        ].split('if [[ "$name" == AGENT_PREWORK* ]]; then', 1)[0]
        agent_requirement = shell.split('if [[ "$name" == AGENT_PREWORK* ]]; then', 1)[1].split(
            'qualify_host_sample "${name}_BEFORE"', 1
        )[0]
        selected_physical = shell.split("observer_tests=()", 1)[1].split(
            'run_ring "PHYSICAL_OBSERVER${suffix}"', 1
        )[0]
        selected_agent = shell.split("run_ring AGENT_PREWORK", 1)[1].split("\nfi", 1)[0]
    except IndexError:
        issues.append(Violation(witness, "QENV1 missing original physical/Agent ring boundaries"))
    else:
        for mark, module in (
            ("P1B27B1", "test_p1b27b1_native_mount_roster"),
            ("P1B27B2A", "test_p1b27b2a_signed_native_sources"),
            ("P1B27B2B2A", "test_p1b27b2b2a_pinned_bind_source"),
            ("P1B27B2B3A", "test_p1b27b2b3a_kernel_mount_coverage"),
            ("P1B27B2B3B1", "test_p1b27b2b3b1_mount_fd_lifecycle"),
            ("P1B27B2B3B2A", "test_p1b27b2b3b2a_original_mount_fd_correlation"),
            ("P1B27B2B3B2B1", "test_p1b27b2b3b2b1_holder_race_fences"),
            ("P1B27B2B3B2B2", "test_p1b27b2b3b2b2_native_fd_handoff"),
            ("P1B27B2B3B2B3B", "test_p1b27b2b3b2b3b_fd_argv_boundary"),
            ("P1B27_GATE_P1P2A", "test_p1b27_gate_p1p2_original_sample_correspondence"),
            ("P1B27_GATE_P1P2A1", "test_p1b27_gate_p1p2a1_native_seal_fixture"),
            ("P1B27_GATE_P1P2B1", "test_p1b27_gate_p1p2b1_original_causal_mount_ledger"),
            ("P1B27_GATE_P1P2B2A", "test_p1b27_gate_p1p2b2a_native_kernel_provenance"),
            ("P1B27_GATE_P1P2B2B", "test_p1b27_gate_p1p2b2b_original_journal_sample"),
            ("P1B27_GATE_P1P2B2B1", "test_p1b27_gate_p1p2b2b1_mount_fixture"),
            ("P1B27_GATE_P1P2B2B2", "test_p1b27_gate_p1p2b2b2_canonical_root"),
            ("P1B27_GATE_P3A", "test_p3a_original_sample_rounds"),
            ("P1B27_GATE_P3B1", "test_p3b1_original_convergence_assessment"),
            ("P1B27_GATE_P3B2A", "test_p3b2a_complete_postcleanup_roster"),
            ("P1B27_GATE_P3B2B", "test_p3b2b_original_native_lifecycle"),
        ):
            requirement = f"required_modules+=(--require-module {module})"
            selected = f"observer_tests+=(tests/unit/runtime/{module}.py)"
            marker = f"if [[ -f .checkpoint/{mark}_SOURCE.sha256 ]]; then"
            # Enrollment is conditional on exactly the same checkpoint marker;
            # reject missing paths, duplicate requirements or wrong ring.
            for snippet, where, description in (
                (requirement, physical_requirement, "physical requirement"),
                (selected, selected_physical, "physical selection"),
            ):
                if where.count(snippet) != 1 or not re.search(
                    re.escape(marker) + r"\s*" + re.escape(snippet), where
                ):
                    issues.append(Violation(witness, f"QENV1 {mark} {description} mismatch"))
            if requirement in agent_requirement or f"/{module}.py" in selected_agent:
                issues.append(Violation(witness, f"QENV1 {mark} native ring ownership mismatch"))
    # P1B27B2B3B2B3A uses the existing PHYSICAL_OBSERVER ring, never a
    # second native qualification front door or silent optional skip.
    if (root / ".checkpoint/P1B27B2B3B2B3A_SOURCE.sha256").is_file():
        try:
            physical = shell.split('if [[ "$name" == PHYSICAL_OBSERVER* ]]; then', 1)[1].split(
                'if [[ "$name" == AGENT_PREWORK* ]]; then', 1
            )[0]
            selection = shell.split("observer_tests=()", 1)[1].split(
                'run_ring "PHYSICAL_OBSERVER${suffix}"', 1
            )[0]
        except IndexError:
            issues.append(Violation(witness, "B27 effectful original ring missing"))
        else:
            required = "required_modules+=(--require-module test_p1b27b2b3b2b3a_effectful_mount)"
            selected = "observer_tests+=(tests/native/test_p1b27b2b3b2b3a_effectful_mount.py)"
            selector = '[[ "${QUALIFY_B27_EFFECTFUL:-0}" == 1 ]]'
            if (
                required not in physical
                or selected not in selection
                or selector not in physical
                or selector not in selection
                or "BLOCKED: effectful B27 witness requires real bwrap" not in shell
            ):
                issues.append(
                    Violation(
                        witness, "B27 effectful witness may silently bypass real physical test"
                    )
                )
            test = root / "backend/tests/native/test_p1b27b2b3b2b3a_effectful_mount.py"
            try:
                content = test.read_text(encoding="utf-8")
            except OSError:
                content = ""
            for required_owner in (
                "OriginalBindSourceLease.pin_prepared(request)",
                "backend._command(",
                "_require_original_native_fd_handoff(",
                "run_command(*argv,",
                "/proc/self/mountinfo",
            ):
                if required_owner not in content:
                    issues.append(
                        Violation(
                            test, f"B27 physical test bypassed original owner: {required_owner}"
                        )
                    )
    # Exact B3B WSL evidence discovered that CPython 3.14.7 omits optional
    # fcntl sealing names. The opt-in physical fixture must consume the ONE
    # production Linux UAPI compatibility rule, never fork another seal table.
    if (root / ".checkpoint/P1B27_GATE_P1P2A1_SOURCE.sha256").is_file():
        effectful = root / "backend/tests/native/test_p1b27b2b3b2b3a_effectful_mount.py"
        content = effectful.read_text(encoding="utf-8") if effectful.is_file() else ""
        if (
            "from oh_goon.engine.atomic_execution_bytes import _linux_seal_operations"
            not in content
            or content.count("_linux_seal_operations()") < 2
            or "fcntl.F_ADD_SEALS" in content
            or "fcntl.F_GET_SEALS" in content
        ):
            issues.append(
                Violation(
                    effectful,
                    "B27 effectful fixture bypasses canonical Linux seal UAPI compatibility",
                )
            )
    if (root / ".checkpoint/DB6_SOURCE.sha256").is_file():
        core = shell.split("run_ring CORE", 1)
        if (
            len(core) != 2
            or core[1]
            .split("\n# AG-02B1", 1)[0]
            .count("tests/unit/runtime/test_db6_native_fault_preflight.py")
            != 1
        ):
            issues.append(
                Violation(witness, "QENV1 DB6 preflight regressions missing from canonical CORE")
            )
        for token in (
            "QUALIFY_DB6_ENOSPC_ROOT",
            "db6_native_tests+=(tests/native/test_db6_kernel_enospc_witness.py)",
            '"${db6_native_tests[@]}"',
        ):
            if token not in shell:
                issues.append(
                    Violation(
                        witness, f"QENV1 DB6 original opt-in kernel fault witness missing {token}"
                    )
                )

    if (root / ".checkpoint/CO_R01_SOURCE.sha256").is_file():
        core = shell.split("run_ring CORE", 1)
        if len(core) != 2 or any(
            core[1].split("\n# AG-02B1", 1)[0].count(module) != 1
            for module in (
                "../scripts/tests/test_co_r01_lifecycle_architecture.py",
                "../scripts/tests/test_co_r01_roadmap_architecture.py",
            )
        ):
            issues.append(
                Violation(
                    witness, "QENV1 CO-R01 owner lifecycle mutants missing from original CORE"
                )
            )
    if (root / ".checkpoint/CO_R2W1_SOURCE.sha256").is_file():
        core = shell.split("run_ring CORE", 1)
        if (
            len(core) != 2
            or core[1]
            .split("\n# AG-02B1", 1)[0]
            .count("tests/unit/runtime/test_co_r2b_workspace_recovery.py")
            != 1
        ):
            issues.append(
                Violation(
                    witness, "QENV1 CO-R2W1 crash recovery regressions missing from original CORE"
                )
            )
    if (root / ".checkpoint/CO_R2W2_SOURCE.sha256").is_file():
        core = shell.split("run_ring CORE", 1)
        if len(core) != 2 or any(
            core[1].split("\n# AG-02B1", 1)[0].count(name) != 1
            for name in (
                "tests/unit/runtime/test_co_r2w2_native_git_recovery.py",
                "../scripts/tests/test_co_r2w2_owner_architecture.py",
            )
        ):
            issues.append(
                Violation(
                    witness,
                    "QENV1 CO-R2W2 native Git recovery regressions missing from original CORE",
                )
            )
    if (root / ".checkpoint/CO_R2W3_SOURCE.sha256").is_file():
        core = shell.split("run_ring CORE", 1)
        if len(core) != 2 or any(
            core[1].split("\n# AG-02B1", 1)[0].count(name) != 1
            for name in (
                "tests/unit/runtime/test_co_r2w3_git_retirement.py",
                "../scripts/tests/test_co_r2w3_owner_architecture.py",
            )
        ):
            issues.append(
                Violation(
                    witness,
                    "QENV1 CO-R2W3 native retirement regressions missing from canonical CORE",
                )
            )
    if (root / ".checkpoint/CO_R2C_SOURCE.sha256").is_file():
        core = shell.split("run_ring CORE", 1)
        if len(core) != 2 or any(
            core[1].split("\n# AG-02B1", 1)[0].count(name) != 1
            for name in (
                "tests/unit/runtime/test_co_r2c_evidence_acceptance_cut.py",
                "../scripts/tests/test_co_r2c_acceptance_architecture.py",
            )
        ):
            issues.append(
                Violation(
                    witness, "QENV1 CO-R2c original acceptance write-cut tests missing from CORE"
                )
            )
    if (root / ".checkpoint/CO_R2C_Q1_SOURCE.sha256").is_file():
        core = shell.split("run_ring CORE", 1)
        if (
            len(core) != 2
            or core[1]
            .split("\n# AG-02B1", 1)[0]
            .count("../scripts/tests/test_co_r2c_q1_isolated_core_collection.py")
            != 1
        ):
            issues.append(
                Violation(
                    witness,
                    "CO-R2c-Q1 independent CORE collection regression missing from original CORE",
                )
            )
    return issues
