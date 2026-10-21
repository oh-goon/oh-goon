"""CO-R01: structural evidence for canonical lifecycle call edges.

This is a fail-closed X0 *source guard*, not a second admission policy or a
proof of crash/physical lifecycle closure. It checks actual Python AST calls,
lexical containment and ordering at the reviewed canonical owner boundaries.
An unrecognized dynamic dispatch still requires a separate runtime/native test.
"""

from __future__ import annotations

import ast
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation
from oh_goon.repository_tooling.architecture.repository_layout import ROOT

_SCHEDULE = "backend/src/oh_goon/services/schedule_execution.py"
_SCHEDULE_STORE = "backend/src/oh_goon/runtime/schedule_executions.py"
_JOURNAL = "backend/src/oh_goon/runtime/journal.py"
_LIFECYCLE = "backend/src/oh_goon/services/fleet_run_lifecycle.py"
_FLEET = "backend/src/oh_goon/services/fleet_run_control_plane.py"
_WORKSPACE = "backend/src/oh_goon/runtime/workspaces.py"
_EVIDENCE = "backend/src/oh_goon/runtime/execution_evidence_acceptance.py"
_EXECUTOR = "backend/src/oh_goon/integrations/fleet_plan_run_execution.py"
_SELECTOR = "backend/src/oh_goon/integrations/fleet_plan_process_isolation.py"
_API = "backend/src/oh_goon/api/routes/fleet_runs.py"
_R3_TEST = "backend/tests/unit/runtime/test_co_r3_native_provider_conformance.py"
_R3B_TEST = "backend/tests/unit/runtime/test_co_r3b_original_failure_cuts.py"
_R3B_GUARD = "scripts/tests/test_co_r3b_original_failure_architecture.py"
_R3C_TEST = "backend/tests/unit/runtime/test_co_r3c_trust_writer_quiescence.py"
_R3C_GUARD = "scripts/tests/test_co_r3c_original_quiescence_architecture.py"
_R3D_TEST = "backend/tests/unit/integrations/test_co_r3d_original_cgroup_quiescence.py"
_R3D_GUARD = "scripts/tests/test_co_r3d_original_cgroup_architecture.py"
_R3E_TEST = "backend/tests/unit/integrations/test_co_r3e_durable_cgroup_recovery.py"
_R3E_GUARD = "scripts/tests/test_co_r3e_original_recovery_architecture.py"
_R3F_TEST = "backend/tests/unit/integrations/test_co_r3f_cgroup_native_handle.py"
_R3F_GUARD = "scripts/tests/test_co_r3f_handle_architecture.py"
_R3G_TEST = "backend/tests/unit/integrations/test_co_r3g_bound_observation_removal.py"
_R3G_GUARD = "scripts/tests/test_co_r3g_bound_observation_architecture.py"
_R3H_TEST = "backend/tests/unit/integrations/test_co_r3h_original_inode_retirement.py"
_R3H_GUARD = "scripts/tests/test_co_r3h_inode_retirement_architecture.py"
_R3I_REAPER = "backend/src/oh_goon/integrations/fleet_plan_local_lace_native_reaper.py"
_R3I_TEST = "backend/tests/unit/integrations/test_co_r3i_original_lace_cgroup_custody.py"
_R3I_GUARD = "scripts/tests/test_co_r3i_original_lace_custody_architecture.py"
_R3J_TEST = "backend/tests/unit/integrations/test_co_r3j_original_parent_lineage.py"
_R3J_GUARD = "scripts/tests/test_co_r3j_parent_lineage_architecture.py"
_R3K_TEST = "backend/tests/unit/integrations/test_co_r3k_prework_identity_rollback.py"
_R3K_GUARD = "scripts/tests/test_co_r3k_prework_architecture.py"
_R3L_TEST = "backend/tests/unit/integrations/test_co_r3l_bound_nested_attachment.py"
_R3L_GUARD = "scripts/tests/test_co_r3l_bound_attachment_architecture.py"
_R3M_TEST = "backend/tests/unit/integrations/test_co_r3m_pending_cgroup_allocation.py"
_R3M_GUARD = "scripts/tests/test_co_r3m_pending_allocation_architecture.py"
_R3N_TEST = "backend/tests/native/test_co_r3n_native_cgroup_v2_witness.py"
_R3N_GUARD = "scripts/tests/test_co_r3n_native_cgroup_qualification.py"
_R3O_GUARD = "scripts/tests/test_co_r3o_native_witness_contract.py"
_R3P_TEST = "backend/tests/unit/services/test_co_r3p_original_fleet_effect_revocation.py"
_R3P_GUARD = "scripts/tests/test_co_r3p_original_effect_epoch_architecture.py"
_R3Q_TEST = "backend/tests/unit/services/test_co_r3q_effect_revalidation.py"
_R3Q_GUARD = "scripts/tests/test_co_r3q_effect_architecture.py"
_R3L_LAUNCHER = "backend/src/oh_goon/integrations/_fleet_plan_nested_cgroup_launcher.py"
_CGROUP_OWNER = "backend/src/oh_goon/integrations/_fleet_plan_cgroup_resources.py"
_PROCESS_GROUP_OWNER = "backend/src/oh_goon/runtime/process_group.py"
_R3_QUALIFIER = "scripts/qualify_b12_development_wsl.sh"


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value)}.{node.attr}"
    return ""


def _calls(node: ast.AST, symbol: str) -> list[ast.Call]:
    return [
        item for item in ast.walk(node) if isinstance(item, ast.Call) and _name(item.func) == symbol
    ]


def _method(tree: ast.Module, owner: str | None, name: str) -> ast.FunctionDef | None:
    bodies = (
        [tree.body]
        if owner is None
        else [cls.body for cls in tree.body if isinstance(cls, ast.ClassDef) and cls.name == owner]
    )
    found = [
        node
        for body in bodies
        for node in body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    return found[0] if len(found) == 1 else None


def _write_transaction(node: ast.FunctionDef) -> list[ast.With]:
    return [
        w
        for w in ast.walk(node)
        if isinstance(w, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and _name(item.context_expr.func) == "self._database.transaction"
            and any(
                k.arg == "write" and isinstance(k.value, ast.Constant) and k.value.value is True
                for k in item.context_expr.keywords
            )
            for item in w.items
        )
    ]


def _inside(parent: ast.AST, child: ast.AST) -> bool:
    return child in ast.walk(parent)


def check_co_r01_lifecycle_edges(root: Path = ROOT) -> list[Violation]:
    """Reject bypasses at existing owner edges; leave CO-R2 runtime behavior untouched."""
    issues: list[Violation] = []
    sources: dict[str, ast.Module] = {}
    for relative in (
        _SCHEDULE,
        _SCHEDULE_STORE,
        _JOURNAL,
        _LIFECYCLE,
        _FLEET,
        _WORKSPACE,
        _EVIDENCE,
        _EXECUTOR,
        _SELECTOR,
        _API,
    ):
        path = root / relative
        try:
            sources[relative] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            issues.append(
                Violation(path, f"CO-R01 owner edge cannot be inspected: {type(exc).__name__}")
            )
    if issues:
        return issues

    def require(relative: str, condition: bool, message: str) -> None:
        if not condition:
            issues.append(Violation(root / relative, "CO-R01 " + message))

    scheduled = _method(sources[_SCHEDULE], "ScheduleExecutionEngine", "_start_repository")
    require(_SCHEDULE, scheduled is not None, "original scheduled child owner missing")
    if scheduled is not None:
        require(
            _SCHEDULE,
            len(_calls(scheduled, "self._runs.start_bound")) == 1,
            "scheduled child must call the original FleetRunControlPlane.start_bound once",
        )
        require(
            _SCHEDULE,
            len(_calls(scheduled, "self._cas")) == 1
            and len(_calls(scheduled, "mark_repository_running")) == 0,
            "schedule may not CAS-publish a second post-Fleet admission child",
        )
        require(
            _SCHEDULE,
            not _calls(scheduled, "self._lifecycle.admit")
            and not _calls(scheduled, "self._database.transaction"),
            "schedule may not acquire a second Fleet admission or SQLite write cut",
        )

    # CO-R2a: join the scheduled child to the ORIGINAL RUN_ADMITTED cut.
    journal = _method(sources[_JOURNAL], "Journal", "admit_run_in")
    binding = (
        [
            n
            for n in ast.walk(journal)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "bind_admitted_child_in"
        ]
        if journal
        else []
    )
    publications = _calls(journal, "self._facts.append_run_event") if journal else []
    require(
        _JOURNAL,
        len(binding) == len(publications) == 1 and binding[0].lineno < publications[0].lineno
        if binding and publications
        else False,
        "scheduled child must bind in original Journal RUN_ADMITTED transaction before publication",
    )
    store = _method(sources[_SCHEDULE_STORE], "ScheduleExecutionStore", "bind_admitted_child_in")
    writes = _calls(store, "self._update") if store else []
    nested = _calls(store, "self._database.transaction") if store else []
    require(
        _SCHEDULE_STORE,
        store is not None and len(writes) == 1 and not nested,
        "scheduled child binding must use supplied Fleet transaction, never a second cut",
    )
    fleet = _method(sources[_FLEET], "FleetRunControlPlane", "_start_intent")
    admit = _calls(fleet, "self._lifecycle.admit") if fleet else []
    require(
        _FLEET,
        len(admit) == 1 and any(k.arg == "schedule_run_id" for k in admit[0].keywords)
        if admit
        else False,
        "scheduled occurrence identity must enter original Fleet lifecycle",
    )
    lifecycle = _method(sources[_LIFECYCLE], "FleetRunLifecycle", "admit")
    sub_intents = _calls(lifecycle, "FleetRunAdmissionIntent") if lifecycle else []
    require(
        _LIFECYCLE,
        len(sub_intents) == 2
        and all(any(k.arg == "schedule_run_id" for k in node.keywords) for node in sub_intents),
        "direct legacy/authenticated Fleet intents must retain schedule identity",
    )
    for method_name in ("_admit_strict_in", "_admit_authenticated_strict_in"):
        method = _method(sources[_LIFECYCLE], "FleetRunLifecycle", method_name)
        candidates = _calls(method, "FleetRunAdmissionIntent") if method else []
        require(
            _LIFECYCLE,
            len(candidates) == 1 and any(k.arg == "schedule_run_id" for k in candidates[0].keywords)
            if candidates
            else False,
            f"{method_name} strict admission must retain schedule identity",
        )

    api = _method(sources[_API], None, "start_fleet_run")
    require(
        _API,
        api is not None and len(_calls(api, "control_plane.start")) == 1,
        "HTTP Fleet admission must call the existing control plane",
    )

    fleet = _method(sources[_FLEET], "FleetRunControlPlane", "_start_intent")
    require(_FLEET, fleet is not None, "original Fleet admission owner missing")
    if fleet is not None:
        with_admission = [
            w
            for w in ast.walk(fleet)
            if isinstance(w, (ast.With, ast.AsyncWith))
            and any(
                isinstance(item.context_expr, ast.Call)
                and _name(item.context_expr.func) == "self._lifecycle.serialize_admission"
                for item in w.items
            )
        ]
        admit = _calls(fleet, "self._lifecycle.admit")
        plans = _calls(fleet, "self._admission.plan")
        submit = _calls(fleet, "self._submit")
        checks = _calls(fleet, "require_scheduled_child")
        require(
            _FLEET,
            len(with_admission) == 1
            and len(admit) == 1
            and len(plans) == 1
            and len(submit) == 1
            and all(_inside(with_admission[0], node) for node in admit + plans + submit)
            if with_admission
            else False,
            "Fleet planning, admission and submit must share one serialized original lifecycle",
        )
        require(
            _FLEET,
            len(checks) >= 2
            and len(admit) == 1
            and len(plans) == 1
            and any(node.lineno < plans[0].lineno for node in checks)
            and any(plans[0].lineno < node.lineno < admit[0].lineno for node in checks),
            "scheduled binding must be revalidated after planning before original admission"
            if admit and plans
            else "scheduled binding or original admission missing",
        )
        # One and only one named lifecycle.admit call in this control plane.
        require(
            _FLEET,
            len(_calls(sources[_FLEET], "self._lifecycle.admit")) == 1,
            "no parallel Fleet lifecycle admission entrypoint",
        )

    workspace = _method(
        sources[_WORKSPACE], "RepositoryWorkspaceAuthority", "materialize_read_snapshot"
    )
    require(_WORKSPACE, workspace is not None, "original workspace publication owner missing")
    if workspace is not None:
        verify = _calls(workspace, "verify_clean_source_snapshot")
        renamed = _calls(workspace, "staging.replace")
        ready = _calls(workspace, "self._write_state")
        writes = _write_transaction(workspace)
        require(
            _WORKSPACE,
            len(verify) >= 2
            and len(renamed) == 1
            and len(ready) == 1
            and len(writes) == 1
            and verify[0].lineno < renamed[0].lineno < ready[0].lineno
            and _inside(writes[0], renamed[0])
            and _inside(writes[0], ready[0])
            if verify and renamed and ready and writes
            else False,
            "workspace READY must retain staged verification and original write-cut publication; "
            "postcommit cleanup is still CO-R2b work",
        )

    owner = "RepositoryWorkspaceAuthority"
    implementation = _method(sources[_WORKSPACE], owner, "materialize_worktree")
    recovery = _method(sources[_WORKSPACE], owner, "reconcile_worktree")
    locked_recovery = _method(sources[_WORKSPACE], owner, "_reconcile_worktree_locked")
    prepare = _method(sources[_WORKSPACE], owner, "_prepare_worktree_marker")
    receipt_lock = _method(sources[_WORKSPACE], owner, "_locked_worktree_receipt")
    release = _method(sources[_WORKSPACE], owner, "release")
    preparation = _calls(implementation, "self._prepare_worktree_marker") if implementation else []
    native_effects = _calls(implementation, "service.run") if implementation else []
    sqlite_ready = (
        _calls(implementation, "self._write_state_with_checkpoint") if implementation else []
    )
    cleared = _calls(implementation, "self._clear_worktree_marker") if implementation else []
    require(
        _WORKSPACE,
        len(preparation) == len(sqlite_ready) == len(cleared) == 1
        and len(native_effects) == 2
        and preparation[0].lineno
        < native_effects[0].lineno
        < sqlite_ready[0].lineno
        < cleared[0].lineno
        if preparation and native_effects and sqlite_ready and cleared
        else False,
        "CO-R2W2 original Git effect must be receipted before Git and READY, then cleared",
    )
    require(
        _WORKSPACE,
        prepare is not None and len(_calls(prepare, "fcntl.flock")) == 1,
        "CO-R2W2 registration must hold kernel receipt lock across Git effect",
    )
    require(
        _WORKSPACE,
        recovery is not None
        and len(_calls(recovery, "self._locked_worktree_receipt")) == 1
        and len(_calls(recovery, "self._reconcile_worktree_locked")) == 1
        and receipt_lock is not None
        and len(_calls(receipt_lock, "fcntl.flock")) == 1,
        "CO-R2W2 reconciliation must reject simultaneous live Git effects",
    )
    txs = _write_transaction(locked_recovery) if locked_recovery else []
    native = _calls(locked_recovery, "self._registered_git_worktree") if locked_recovery else []
    commit = _calls(locked_recovery, "self._write_state_with_checkpoint") if locked_recovery else []
    require(
        _WORKSPACE,
        len(txs) == 1
        and len(native) == 1
        and len(commit) == 1
        and _inside(txs[0], native[0])
        and _inside(txs[0], commit[0])
        if txs and native and commit
        else False,
        "CO-R2W2 native Git reproof and READY commit must share original write cut",
    )
    if release:
        queries = [
            n
            for n in ast.walk(release)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and "FROM workspace_worktrees" in n.value
        ]
        require(
            _WORKSPACE,
            len(queries) == 1 and len(_calls(release, "self._worktree_marker_for_id")) == 1,
            "CO-R2W2 release cannot equate missing .git with deregistration",
        )
    else:
        require(_WORKSPACE, False, "CO-R2W2 original release owner missing")

    owner = "RepositoryWorkspaceAuthority"
    retired = _method(sources[_WORKSPACE], owner, "retire_worktree")
    preflight = _method(sources[_WORKSPACE], owner, "_worktree_retirement_preflight")
    replay = _method(sources[_WORKSPACE], owner, "reconcile_worktree_retirement")
    committed = _method(sources[_WORKSPACE], owner, "_reconcile_retirement_locked")
    release = _method(sources[_WORKSPACE], owner, "release")
    preparation = _calls(retired, "self._prepare_retirement_marker") if retired else []
    native = _calls(retired, "service.run") if retired else []
    publication = _calls(retired, "self._reconcile_retirement_locked") if retired else []
    require(
        _WORKSPACE,
        retired is not None
        and len(preparation) == len(native) == len(publication) == 1
        and preparation[0].lineno < native[0].lineno < publication[0].lineno
        if preparation and native and publication
        else False,
        "CO-R2W3 Git removal must be receipted before native effect and original SQLite decision",
    )
    require(
        _WORKSPACE,
        preflight is not None
        and len(_calls(preflight, "self._require_no_unsettled_writer")) == 1
        and len(_calls(preflight, "self._require_no_ever_started_writer")) == 1,
        "CO-R2W3 original preflight must refuse unproved writer absence",
    )
    require(
        _WORKSPACE,
        replay is not None
        and len(_calls(replay, "fcntl.flock")) == 1
        and len(_calls(replay, "self._reconcile_retirement_locked")) == 1,
        "CO-R2W3 native replay must exclude live original Git removal",
    )
    transactions = _write_transaction(committed) if committed else []
    native_absence = _calls(committed, "self._registered_git_worktree") if committed else []
    released = _calls(committed, "self._write_state") if committed else []
    removed = (
        [
            n
            for n in ast.walk(committed)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and "DELETE FROM workspace_worktrees" in n.value
        ]
        if committed
        else []
    )
    require(
        _WORKSPACE,
        len(transactions) == len(native_absence) == len(released) == len(removed) == 1
        and all(_inside(transactions[0], n) for n in native_absence + released + removed)
        if transactions
        else False,
        "CO-R2W3 native absence, durable deregistration and RELEASED require original SQLite cut",
    )
    require(
        _WORKSPACE,
        committed is not None
        and len(_calls(committed, "self._require_no_ever_started_writer")) == 1
        and len(_calls(committed, "self._clear_retirement_marker")) == 1,
        "CO-R2W3 writer proof and postcommit receipt cleanup are mandatory",
    )
    require(
        _WORKSPACE,
        release is not None
        and len(_calls(release, "self.retire_worktree")) == 1
        and len(_calls(release, "self._worktree_retirement_marker_for_id")) >= 1,
        "CO-R2W3 original release must route native Git through same-owner retirement",
    )

    accept = _method(sources[_EVIDENCE], "ExecutionEvidenceAcceptanceAuthority", "accept")
    require(_EVIDENCE, accept is not None, "original evidence acceptance owner missing")
    if accept is not None:
        tx = _write_transaction(accept)
        validate = _calls(accept, "self._validate_durable_rows")
        persist = _calls(accept, "self._persist_acceptance")
        require(
            _EVIDENCE,
            len(tx) == 1
            and len(validate) == 1
            and len(persist) == 1
            and _inside(tx[0], validate[0])
            and _inside(tx[0], persist[0])
            and validate[0].lineno < persist[0].lineno
            if tx and validate and persist
            else False,
            "evidence must validate durable rows in the same owner write cut as acceptance",
        )

    # CO-R2c: the existing acceptance owner must sample time after the last
    # durable row/lineage read and use it for BOTH expiry and accepted receipt.
    # This is a structural guard, not a second expiry decision authority.
    owner = "ExecutionEvidenceAcceptanceAuthority"
    accept = _method(sources[_EVIDENCE], owner, "accept")
    durable = _method(sources[_EVIDENCE], owner, "_validate_durable_rows")
    load = _method(sources[_EVIDENCE], owner, "_load_durable_rows")
    write_cuts = _write_transaction(accept) if accept else []
    reads = _calls(accept, "self._load_durable_rows") if accept else []
    validates = _calls(accept, "self._validate_durable_rows") if accept else []
    accepts = _calls(accept, "self._new_acceptance") if accept else []
    inserts = _calls(accept, "self._persist_acceptance") if accept else []
    require(
        _EVIDENCE,
        len(write_cuts) == len(reads) == len(validates) == len(accepts) == len(inserts) == 1
        and all(_inside(write_cuts[0], n) for n in reads + validates + accepts + inserts)
        and reads[0].lineno < validates[0].lineno < accepts[0].lineno < inserts[0].lineno
        and any(
            k.arg == "accepted_at" and isinstance(k.value, ast.Name) and k.value.id == "accepted_at"
            for k in accepts[0].keywords
        )
        if write_cuts
        else False,
        "CO-R2c original acceptance must validate then timestamp/insert inside the original write cut",
    )
    clock = _calls(durable, "utc_now") if durable else []
    parse = _calls(durable, "datetime.fromisoformat") if durable else []
    lineage = _calls(durable, "self._validate_durable_producer") if durable else []
    expiry_branches = (
        [
            n
            for n in ast.walk(durable)
            if isinstance(n, ast.If)
            and isinstance(n.test, ast.Compare)
            and isinstance(n.test.left, ast.Name)
            and n.test.left.id == "accepted_at"
            and len(n.test.ops) == 1
            and isinstance(n.test.ops[0], ast.GtE)
            and len(n.test.comparators) == 1
            and isinstance(n.test.comparators[0], ast.Name)
            and n.test.comparators[0].id == "persisted_expires_at"
            and any(isinstance(x, ast.Raise) for x in n.body)
        ]
        if durable
        else []
    )
    returned = (
        [
            n
            for n in ast.walk(durable)
            if isinstance(n, ast.Return)
            and isinstance(n.value, ast.Name)
            and n.value.id == "accepted_at"
        ]
        if durable
        else []
    )
    require(
        _EVIDENCE,
        len(clock) == len(lineage) == len(expiry_branches) == len(returned) == 1
        and len(parse) == 2
        and lineage[0].lineno < clock[0].lineno < expiry_branches[0].lineno < returned[0].lineno,
        "CO-R2c durable expiry and receipt must use the one post-read write-cut clock",
    )
    queries = (
        [
            n.value
            for n in ast.walk(load)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        ]
        if load
        else []
    )
    require(
        _EVIDENCE,
        any("issued_at, expires_at" in q for q in queries),
        "CO-R2c expiry must be read from the persisted lease in the original cut",
    )

    # CO-R3A: qualify the ORIGINAL workspace/SQLite/Git implementation on its
    # existing native CORE ring; a second qualifier or source-independent suite
    # must not be able to silently claim provider conformance.
    try:
        shell = (root / _R3_QUALIFIER).read_text(encoding="utf-8")
        body = (root / _R3_TEST).read_text(encoding="utf-8")
        regression_tree = ast.parse(body)
    except (OSError, SyntaxError) as exc:
        issues.append(Violation(root / _R3_TEST, f"CO-R3A native conformance unavailable: {exc}"))
    else:
        core = shell.split("run_ring CORE " + chr(92) + chr(10), 1)
        core = core[1].split("\n# AG-02B1", 1)[0] if len(core) == 2 else ""
        require(
            _R3_QUALIFIER,
            core.count("tests/unit/runtime/test_co_r3_native_provider_conformance.py") == 1,
            "CO-R3A exact real provider conformance must run in original QENV1 CORE",
        )
        request = _method(
            regression_tree,
            None,
            "test_original_workspace_request_has_one_durable_answer_after_process_death",
        )
        native = _method(
            regression_tree,
            None,
            "test_native_git_effect_with_lost_ack_preserves_one_owner_recovery",
        )
        forbidden = _method(
            regression_tree, None, "test_unsupported_provider_refuses_before_database_publication"
        )
        replacement = _method(
            regression_tree,
            None,
            "test_native_death_then_foreign_replacement_cannot_publish_release",
        )
        child = next(
            (
                node.value.value
                for node in regression_tree.body
                if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_REQUEST_CHILD" for t in node.targets)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ),
            "",
        )
        require(
            _R3_TEST,
            request is not None
            and native is not None
            and forbidden is not None
            and replacement is not None
            and len(_calls(request, "_restart")) == 1
            and len(_calls(native, "real_run")) == 1
            and len(_calls(native, "restarted.reconcile_worktree_retirement")) == 1
            and len(_calls(replacement, "restarted.reconcile_worktree_retirement")) == 1
            and len(_calls(forbidden, "LocalDatabase")) == 1
            and any(
                isinstance(n, ast.Constant) and n.value == "UNREGISTERED_PROVIDER"
                for n in ast.walk(forbidden)
            )
            and "os._exit(181)" in child
            and "os._exit(182)" in child
            and "os._exit(183)" in body
            and sum(
                ast.unparse(call) == "pytest.mark.parametrize('mode', ['DELETE', 'WAL'])"
                for call in _calls(regression_tree, "pytest.mark.parametrize")
            )
            == 3,
            "CO-R3A must exercise original owner, real native effect, actual OS exits and admitted modes",
        )

    strict = _method(sources[_EXECUTOR], "FleetPlanRunExecutor", "prepare_strict_run")
    selector = _method(sources[_SELECTOR], None, "select_bound_process_isolation_backend")
    require(
        _EXECUTOR,
        strict is not None and len(_calls(strict, "select_bound_process_isolation_backend")) == 1,
        "strict backend must be resolved through the exact admitted selector once",
    )
    require(
        _SELECTOR,
        selector is not None
        and len(_calls(selector, "isolation_backend_evidence")) == 1
        and len(_calls(selector, "backend.capabilities.satisfies")) == 1
        and len(_calls(selector, "backend.require_available")) == 1,
        "provider cannot skip exact admitted identity, capability or availability checks",
    )
    # CO-R3B: conformance MUST exercise original SQLite/write-cut owners,
    # OS-process death, and the one existing QENV1 CORE. No second replay path.
    try:
        shell = (root / _R3_QUALIFIER).read_text(encoding="utf-8")
        body = (root / _R3B_TEST).read_text(encoding="utf-8")
        tree = ast.parse(body)
    except (OSError, SyntaxError) as exc:
        issues.append(
            Violation(root / _R3B_TEST, f"CO-R3B original-owner conformance unavailable: {exc}")
        )
    else:
        core = shell.split("run_ring CORE " + chr(92) + chr(10), 1)
        core = core[1].split("\n# AG-02B1", 1)[0] if len(core) == 2 else ""
        require(
            _R3_QUALIFIER,
            all(
                core.count(name) == 1
                for name in (
                    "tests/unit/runtime/test_co_r3b_original_failure_cuts.py",
                    "../scripts/tests/test_co_r3b_original_failure_architecture.py",
                )
            ),
            "CO-R3B must run on the one original QENV1 CORE",
        )
        child = next(
            (
                node.value.value
                for node in tree.body
                if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_CHILD" for t in node.targets)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ),
            "",
        )
        try:
            child_tree = ast.parse(child)
        except SyntaxError:
            child_tree = ast.parse("")
        child_calls = {_name(n.func) for n in ast.walk(child_tree) if isinstance(n, ast.Call)}
        child_exits = {
            n.args[0].value
            for n in ast.walk(child_tree)
            if isinstance(n, ast.Call)
            and _name(n.func) == "os._exit"
            and len(n.args) == 1
            and isinstance(n.args[0], ast.Constant)
        }
        required_tests = (
            "test_original_scheduled_admission_after_real_process_death",
            "test_original_acceptance_after_real_process_death_or_expiry",
            "test_original_backup_crash_never_admits_an_unacknowledged_candidate",
        )
        witness = _method(tree, None, "_die")
        require(
            _R3B_TEST,
            all(_method(tree, None, n) is not None for n in required_tests)
            and witness is not None
            and bool(_calls(witness, "subprocess.run"))
            and {
                "_fixture",
                "_admit",
                "_acceptance_ledger",
                "_accept",
                "owner.export_verified_backup",
            }.issubset(child_calls)
            and {191, 192, 193, 194, 195, 196, 197}.issubset(child_exits)
            and len(_calls(child_tree, "_accept")) == 2
            and len(_calls(child_tree, "_admit")) == 1
            and len(_calls(child_tree, "owner.export_verified_backup")) == 1,
            "CO-R3B original admission/acceptance/backup cuts must retain native OS death and replay",
        )
    # CO-R3C: the original process-group observer must fail closed on a
    # partial /proc inventory. The real supervisor test MUST retain an escaped
    # writer negative control, because group quiescence is not LACE custody.
    try:
        shell = (root / _R3_QUALIFIER).read_text(encoding="utf-8")
        witness = (root / _R3C_TEST).read_text(encoding="utf-8")
        owner = (root / _PROCESS_GROUP_OWNER).read_text(encoding="utf-8")
        tree = ast.parse(witness)
        owner_tree = ast.parse(owner)
    except (OSError, SyntaxError) as exc:
        issues.append(
            Violation(root / _R3C_TEST, f"CO-R3C original quiescence witness unavailable: {exc}")
        )
    else:
        core = shell.split("run_ring CORE " + chr(92) + chr(10), 1)
        core = core[1].split("\n# AG-02B1", 1)[0] if len(core) == 2 else ""
        require(
            _R3_QUALIFIER,
            all(
                core.count(name) == 1
                for name in (
                    "tests/unit/runtime/test_co_r3c_trust_writer_quiescence.py",
                    "../scripts/tests/test_co_r3c_original_quiescence_architecture.py",
                )
            ),
            "CO-R3C original CORE must include physical writer and negative-control tests",
        )
        scanner = _method(owner_tree, None, "_linux_process_group_has_live_member")
        probe = _method(owner_tree, None, "process_group_exists")
        require(
            _PROCESS_GROUP_OWNER,
            scanner is not None
            and probe is not None
            and owner.count("incomplete = True") == 3
            and "return False if inspected and not incomplete else None" in owner
            and bool(_calls(probe, "os.killpg")),
            "CO-R3C incomplete Linux proc scan must fall back to original kernel probe",
        )
        owned = _method(
            tree, None, "test_real_trust_termination_quiesces_uncooperative_owned_writer"
        )
        escaped = _method(
            tree, None, "test_escaped_writer_disproves_process_group_as_independent_absence"
        )
        partial = _method(tree, None, "test_partial_proc_inventory_never_asserts_absent")
        require(
            _R3C_TEST,
            bool(owned and escaped and partial) and len(_calls(owned, "run_command")) == 1
            if owned is not None
            else False,
            "CO-R3C must run the original supervisor with an owned writer",
        )
        require(
            _R3C_TEST,
            escaped is not None
            and len(_calls(escaped, "run_command")) == 1
            and len(_calls(escaped, "os.killpg")) == 1
            and "start_new_session=True" in witness
            and "marker.stat().st_size > before" in witness,
            "CO-R3C escaped writer must remain a witnessed negative control and be cleaned up",
        )
    # CO-R3D: negative cgroup physical-evidence test of the ORIGINAL attempt
    # resource owner. These checks never authorize independent LACE absence.
    try:
        shell = (root / _R3_QUALIFIER).read_text(encoding="utf-8")
        owner = (root / _CGROUP_OWNER).read_text(encoding="utf-8")
        witness = (root / _R3D_TEST).read_text(encoding="utf-8")
        tree = ast.parse(owner)
        ast.parse(witness)
    except (OSError, SyntaxError) as exc:
        issues.append(
            Violation(root / _R3D_TEST, f"CO-R3D original cgroup witness unavailable: {exc}")
        )
    else:
        core = shell.split("run_ring CORE " + chr(92) + chr(10), 1)
        core = core[1].split("\n# AG-02B1", 1)[0] if len(core) == 2 else ""
        require(
            _R3_QUALIFIER,
            all(
                core.count(name) == 1
                for name in (
                    "tests/unit/integrations/test_co_r3d_original_cgroup_quiescence.py",
                    "../scripts/tests/test_co_r3d_original_cgroup_architecture.py",
                )
            ),
            "CO-R3D must remain in original QENV1 CORE",
        )
        lease_open = _method(tree, "CgroupV2AttemptLease", "open")
        lease_close = _method(tree, "CgroupV2AttemptLease", "close")
        wait = _method(tree, None, "_wait_for_cgroup_empty")
        original = _method(tree, None, "_exact_cgroup_identity")
        required = _method(tree, None, "_require_original_cgroup")
        close_ops = (
            "_kill_cgroup",
            "_wait_for_cgroup_empty",
            "_read_resource_evidence",
            "_remove_cgroup",
            "write_attempt_resource_evidence",
            "remove_file_durably",
        )
        calls = (
            [next((n for n in _calls(lease_close, name)), None) for name in close_ops]
            if lease_close
            else []
        )
        require(
            _CGROUP_OWNER,
            lease_close is not None
            and len(calls) == len(close_ops)
            and all(node is not None for node in calls)
            and all(
                left.lineno < right.lineno for left, right in zip(calls, calls[1:], strict=False)
            )
            and lease_open is not None
            and len(_calls(lease_open, "_remove_cgroup")) == 1
            and len(_calls(lease_close, "_require_original_cgroup")) >= 2
            and any(k.arg == "expected_identity" for k in calls[1].keywords)
            and "self.cgroup_identity" in ast.unparse(lease_close),
            "CO-R3D original close must pin, kill, prove, remove, then publish",
        )
        require(
            _CGROUP_OWNER,
            wait is not None
            and original is not None
            and required is not None
            and len(_calls(wait, "_require_original_cgroup")) >= 2
            and (
                (
                    len(_calls(wait, "_read_regular")) == 2
                    and {ast.unparse(node.args[0]) for node in _calls(wait, "_read_regular")}
                    == {"cgroup / 'cgroup.events'", "cgroup / 'cgroup.procs'"}
                )
                or (
                    len(_calls(wait, "_read_bound_cgroup_control")) == 2
                    and {
                        ast.unparse(node.args[1])
                        for node in _calls(wait, "_read_bound_cgroup_control")
                    }
                    == {"'cgroup.events'", "'cgroup.procs'"}
                    and len(_calls(wait, "_bound_cgroup_directory")) == 1
                )
            )
            and "Fleet original cgroup observations disagree" in owner
            and len(_calls(required, "_exact_cgroup_identity")) == 1,
            "CO-R3D may not infer emptiness from missing or substituted kernel files",
        )
        require(
            _R3D_TEST,
            "test_original_owner_never_publishes_completion_if_native_remove_fails" in witness
            and "test_missing_replaced_and_symlink_cgroup_cannot_supply_original_identity"
            in witness
            and "test_real_kernel_style_empty_cgroup_requires_both_original_observations"
            in witness,
            "CO-R3D negative physical-evidence regressions must remain present",
        )
    # CO-R3E: preserve the original non-LACE orphan owner and its durable
    # kernel-identity fence. This does not create any independently signed
    # physical fact or authorize writer-bearing workspace retirement.
    try:
        shell = (root / _R3_QUALIFIER).read_text(encoding="utf-8")
        owner = (root / _CGROUP_OWNER).read_text(encoding="utf-8")
        witness = (root / _R3E_TEST).read_text(encoding="utf-8")
        tree = ast.parse(owner)
        ast.parse(witness)
    except (OSError, SyntaxError) as exc:
        issues.append(
            Violation(root / _R3E_TEST, f"CO-R3E original recovery witness unavailable: {exc}")
        )
    else:
        core = shell.split("run_ring CORE " + chr(92) + chr(10), 1)
        core = core[1].split("\n# AG-02B1", 1)[0] if len(core) == 2 else ""
        require(
            _R3_QUALIFIER,
            all(
                core.count(name) == 1
                for name in (
                    "tests/unit/integrations/test_co_r3e_durable_cgroup_recovery.py",
                    "../scripts/tests/test_co_r3e_original_recovery_architecture.py",
                )
            ),
            "CO-R3E must run inside original QENV1 CORE",
        )
        write = _method(tree, None, "_write_resource_lease")
        reconcile = _method(tree, None, "_reconcile_stale_cgroups")
        actions = (
            "_require_original_cgroup",
            "_kill_cgroup",
            "_wait_for_cgroup_empty",
            "_remove_cgroup",
        )
        calls = [next(iter(_calls(reconcile, name)), None) for name in actions] if reconcile else []
        require(
            _CGROUP_OWNER,
            reconcile is not None
            and len(_calls(reconcile, "_require_original_cgroup")) == 1
            and not any(
                _calls(reconcile, action)
                for action in (
                    "_kill_cgroup",
                    "_wait_for_cgroup_empty",
                    "_remove_cgroup",
                    "remove_file_durably",
                )
            )
            and '"lace_intent" in payload' in owner
            and "Fleet LACE-owned cgroup requires original journal recovery" in owner,
            "CO-R3T supersedes CO-R3E PID-scavenge with identity-checked quarantine",
        )
        require(
            _CGROUP_OWNER,
            write is not None
            and len(_calls(write, "_require_original_cgroup")) == 1
            and '"cgroup_dev": lease.cgroup_identity[0]' in owner
            and '"cgroup_ino": lease.cgroup_identity[1]' in owner
            and "type(device) is not int" in owner
            and "type(inode) is not int" in owner,
            "CO-R3E durable original kernel identity cannot be stripped or weakened",
        )
        require(
            _R3E_TEST,
            all(
                name in witness
                for name in (
                    "test_foreign_replacement_cannot_be_killed_or_removed",
                    "test_old_or_unpinned_orphan_fails_before_native_effect",
                    "test_original_lace_journal_ownership_still_refuses_pid_scavenging",
                    "test_original_identity_may_retire_only_bookkeeping_after_lost_ack",
                )
            ),
            "CO-R3E negative original-owner recovery evidence missing",
        )
    # CO-R3F: one original owner performs the physically destructive cgroup.kill
    # through a kernel directory descriptor pinned to durable original identity.
    # This does not certify subsequent rmdir or workspace writer absence.
    try:
        shell = (root / _R3_QUALIFIER).read_text(encoding="utf-8")
        owner_tree = ast.parse((root / _CGROUP_OWNER).read_text(encoding="utf-8"))
        witness = ast.parse((root / _R3F_TEST).read_text(encoding="utf-8"))
        ast.parse((root / _R3F_GUARD).read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        issues.append(
            Violation(root / _R3F_TEST, f"CO-R3F original kernel-handle witness unavailable: {exc}")
        )
    else:
        core = shell.split("run_ring CORE " + chr(92) + chr(10), 1)
        core = core[1].split("\n# AG-02B1", 1)[0] if len(core) == 2 else ""
        require(
            _R3_QUALIFIER,
            all(
                core.count(name) == 1
                for name in (
                    "tests/unit/integrations/test_co_r3f_cgroup_native_handle.py",
                    "../scripts/tests/test_co_r3f_handle_architecture.py",
                )
            ),
            "CO-R3F must run through original CORE qualification owner",
        )
        kill = _method(owner_tree, None, "_kill_cgroup")
        close = _method(owner_tree, "CgroupV2AttemptLease", "close")
        active_owners = (close,)
        bound_calls = [next(iter(_calls(method, "_kill_cgroup")), None) for method in active_owners]
        require(
            _CGROUP_OWNER,
            kill is not None
            and all(
                call is not None and any(k.arg == "expected_identity" for k in call.keywords)
                for call in bound_calls
            ),
            "CO-R3F original close and recovery must transport durable identity to kill",
        )
        opens = _calls(kill, "os.open") if kill is not None else []
        require(
            _CGROUP_OWNER,
            kill is not None
            and len(opens) >= 2
            and len(_calls(kill, "os.fstat")) >= 2
            and any(
                any(
                    k.arg == "dir_fd" and ast.unparse(k.value) == "descriptor"
                    for k in node.keywords
                )
                for node in opens
            )
            and any(ast.unparse(node.args[0]) == "control" for node in _calls(kill, "os.fstat"))
            and bool(_calls(kill, "os.write"))
            and bool(_calls(kill, "_require_original_cgroup"))
            and "expected_identity" in ast.unparse(kill)
            and "O_NOFOLLOW" in ast.unparse(kill),
            "CO-R3F native kill must use original opened directory identity, not a new path",
        )
        names = {node.name for node in witness.body if isinstance(node, ast.FunctionDef)}
        require(
            _R3F_TEST,
            {
                "test_replacement_between_directory_pin_and_control_write_is_refused",
                "test_replacement_at_actual_native_write_never_targets_foreign_path",
                "test_missing_or_symlink_control_refuses_pid_fallback",
            }
            <= names,
            "CO-R3F original native negative controls must be present",
        )
    # CO-R3G: original cgroup observation and recursive removal use pinned
    # native directory handles. Linux rmdir is still not identity-conditional.
    try:
        shell = (root / _R3_QUALIFIER).read_text(encoding="utf-8")
        owner = (root / _CGROUP_OWNER).read_text(encoding="utf-8")
        tree = ast.parse(owner)
        witness = ast.parse((root / _R3G_TEST).read_text(encoding="utf-8"))
        ast.parse((root / _R3G_GUARD).read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        issues.append(Violation(root / _R3G_TEST, f"CO-R3G witness missing: {exc}"))
    else:
        core = shell.split("run_ring CORE " + chr(92) + chr(10), 1)
        core = core[1].split("\n# AG-02B1", 1)[0] if len(core) == 2 else ""
        require(
            _R3_QUALIFIER,
            all(
                core.count(name) == 1
                for name in (
                    "tests/unit/integrations/test_co_r3g_bound_observation_removal.py",
                    "../scripts/tests/test_co_r3g_bound_observation_architecture.py",
                )
            ),
            "CO-R3G must remain enrolled in original QENV1 CORE",
        )
        wait = _method(tree, None, "_wait_for_cgroup_empty")
        read = _method(tree, None, "_read_bound_cgroup_control")
        remove = _method(tree, None, "_remove_cgroup")
        close = _method(tree, "CgroupV2AttemptLease", "close")
        check_methods = (close,)
        require(
            _CGROUP_OWNER,
            wait is not None
            and len(_calls(wait, "_read_bound_cgroup_control")) == 2
            and bool(_calls(wait, "_bound_cgroup_directory"))
            and read is not None
            and bool(_calls(read, "os.open"))
            and "O_NOFOLLOW" in ast.unparse(read)
            and "dir_fd=descriptor" in ast.unparse(read)
            and bool(_calls(read, "os.fstat"))
            and bool(_calls(read, "os.read")),
            "CO-R3G observations must use bounded kernel reads through pinned native directory",
        )
        require(
            _CGROUP_OWNER,
            remove is not None
            and bool(_calls(remove, "_bound_cgroup_directory"))
            and bool(_calls(remove, "os.scandir"))
            and any(k.arg == "dir_fd" for node in _calls(remove, "os.rmdir") for k in node.keywords)
            and "_require_original_cgroup" in ast.unparse(remove)
            and "Fleet native cgroup target reappeared after removal" in ast.unparse(remove)
            and len(_calls(remove, "os.stat")) >= 3
            and all(
                method is not None
                and any(
                    k.arg == "expected_identity"
                    for call in _calls(method, "_remove_cgroup")
                    for k in call.keywords
                )
                for method in check_methods
            ),
            "CO-R3G original removal must transport identity and use pinned parent handle",
        )
        names = {node.name for node in witness.body if isinstance(node, ast.FunctionDef)}
        require(
            _R3G_TEST,
            {
                "test_symlink_or_missing_observation_control_refuses_quiescence",
                "test_replacement_during_observation_cannot_yield_false_absence",
                "test_replacement_before_remove_refuses_native_effect",
                "test_symlink_child_refuses_recursive_deletion",
                "test_post_remove_foreign_reappearance_is_not_acknowledged",
                "test_child_replaced_during_directory_inventory_must_not_be_retired",
            }
            <= names,
            "CO-R3G negative native observation/removal witnesses missing",
        )
    # CO-R3H: do not mistake disappearance of a mutable pathname for kernel
    # retirement of the ORIGINAL opened directory. This post-effect evidence
    # fence cannot make Linux rmdir identity-conditional; physical LACE is open.
    try:
        shell = (root / _R3_QUALIFIER).read_text(encoding="utf-8")
        tree = ast.parse((root / _CGROUP_OWNER).read_text(encoding="utf-8"))
        witness = ast.parse((root / _R3H_TEST).read_text(encoding="utf-8"))
        ast.parse((root / _R3H_GUARD).read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        issues.append(
            Violation(root / _R3H_TEST, f"CO-R3H original inode evidence unavailable: {exc}")
        )
    else:
        core = shell.split("run_ring CORE " + chr(92) + chr(10), 1)
        core = core[1].split("\n# AG-02B1", 1)[0] if len(core) == 2 else ""
        require(
            _R3_QUALIFIER,
            all(
                core.count(name) == 1
                for name in (
                    "tests/unit/integrations/test_co_r3h_original_inode_retirement.py",
                    "../scripts/tests/test_co_r3h_inode_retirement_architecture.py",
                )
            ),
            "CO-R3H inode retirement witnesses must run in original CORE",
        )
        remove = _method(tree, None, "_remove_cgroup")
        rmdirs = _calls(remove, "os.rmdir") if remove else []
        fstats = (
            [
                call
                for call in _calls(remove, "os.fstat")
                if len(call.args) == 1 and ast.unparse(call.args[0]) == "descriptor"
            ]
            if remove
            else []
        )
        require(
            _CGROUP_OWNER,
            len(rmdirs) == 1
            and len(fstats) == 1
            and fstats[0].lineno > rmdirs[0].lineno
            and "retired.st_nlink != 0" in ast.unparse(remove)
            and any(
                isinstance(node, ast.Compare)
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.NotEq)
                and "retired.st_dev, retired.st_ino" in ast.unparse(node)
                and "expected_identity" in ast.unparse(node)
                for node in ast.walk(remove)
            )
            and "Fleet original cgroup inode is still linked after native removal"
            in ast.unparse(remove)
            if remove
            else False,
            "CO-R3H must positively verify original inode unlink after native removal",
        )
        names = {node.name for node in witness.body if isinstance(node, ast.FunctionDef)}
        require(
            _R3H_TEST,
            {
                "test_foreign_deleted_at_destructive_cut_must_not_ack_original_retirement",
                "test_original_inode_postcut_observation_failure_is_not_accepted",
                "test_nested_substitution_blocks_parent_retirement",
            }
            <= names,
            "CO-R3H post-cut negative-control witnesses missing",
        )
    # CO-R3I: journal-bound native LACE cleanup reuses the original cgroup owner.
    # This does not prove independent parent mutation custody or atomic rmdir.
    try:
        shell = (root / _R3_QUALIFIER).read_text(encoding="utf-8")
        reaper = ast.parse((root / _R3I_REAPER).read_text(encoding="utf-8"))
        witness = ast.parse((root / _R3I_TEST).read_text(encoding="utf-8"))
        ast.parse((root / _R3I_GUARD).read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        issues.append(
            Violation(root / _R3I_REAPER, f"CO-R3I native LACE custody edge unavailable: {exc}")
        )
    else:
        core = shell.split("run_ring CORE " + chr(92) + chr(10), 1)
        core = core[1].split("\n# AG-02B1", 1)[0] if len(core) == 2 else ""
        require(
            _R3_QUALIFIER,
            all(
                core.count(name) == 1
                for name in (
                    "tests/unit/integrations/test_co_r3i_original_lace_cgroup_custody.py",
                    "../scripts/tests/test_co_r3i_original_lace_custody_architecture.py",
                )
            ),
            "CO-R3I native LACE original-owner witnesses must run in CORE",
        )
        action = _method(reaper, None, "cleanup_local_orphan")
        body = ast.unparse(action) if action else ""
        require(
            _R3I_REAPER,
            action is not None
            and len(_calls(action, "_wait_for_cgroup_empty")) == 1
            and len(_calls(action, "_bound_cgroup_directory")) == 1
            and len(_calls(action, "_remove_cgroup")) == 1
            and len(_calls(action, "owner.execute_native_cleanup")) == 1
            and "original_identity = (inventory.cgroup_device, inventory.cgroup_inode)" in body
            and all(
                any(
                    keyword.arg == "expected_identity"
                    and ast.unparse(keyword.value) == "original_identity"
                    for keyword in call.keywords
                )
                for name in ("_wait_for_cgroup_empty", "_remove_cgroup")
                for call in _calls(action, name)
            )
            and not _calls(action, "cgroup.rmdir")
            and not _calls(action, "os.rmdir"),
            "CO-R3I native LACE cleanup must retain original identity-bound cgroup effect",
        )
        names = {node.name for node in witness.body if isinstance(node, ast.FunctionDef)}
        require(
            _R3I_TEST,
            {
                "test_lace_reaper_refuses_symlinked_kernel_observation",
                "test_lace_reaper_cannot_ack_foreign_directory_deleted_at_rmdir_cut",
                "test_lace_reaper_rejects_unobservable_inode_ack_after_native_effect",
            }
            <= names,
            "CO-R3I LACE recovery negative controls missing",
        )
    # CO-R3J: the SAME native deletion owner pins and rechecks cgroup parent
    # lineage, including after rmdir. Local observation does NOT constitute
    # exclusive kernel-enforced parent custody or identity-conditional deletion.
    try:
        shell = (root / _R3_QUALIFIER).read_text(encoding="utf-8")
        tree = ast.parse((root / _CGROUP_OWNER).read_text(encoding="utf-8"))
        witness = ast.parse((root / _R3J_TEST).read_text(encoding="utf-8"))
        ast.parse((root / _R3J_GUARD).read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        issues.append(
            Violation(root / _R3J_TEST, f"CO-R3J parent lineage boundary unavailable: {exc}")
        )
    else:
        core = shell.split("run_ring CORE " + chr(92) + chr(10), 1)
        core = core[1].split("\n# AG-02B1", 1)[0] if len(core) == 2 else ""
        require(
            _R3_QUALIFIER,
            all(
                core.count(name) == 1
                for name in (
                    "tests/unit/integrations/test_co_r3j_original_parent_lineage.py",
                    "../scripts/tests/test_co_r3j_parent_lineage_architecture.py",
                )
            ),
            "CO-R3J must run inside original QENV1 CORE",
        )
        removal = _method(tree, None, "_remove_cgroup")
        parent_guard = _method(tree, None, "_require_original_cgroup_parent")
        guard_calls = _calls(removal, "_require_original_cgroup_parent") if removal else []
        require(
            _CGROUP_OWNER,
            removal is not None
            and parent_guard is not None
            and len(guard_calls) >= 3
            and len(_calls(parent_guard, "os.fstat")) == 1
            and len(_calls(parent_guard, "os.stat")) == 1
            and "follow_symlinks=False" in ast.unparse(parent_guard)
            and "(current.st_dev, current.st_ino) != expected_identity" in ast.unparse(parent_guard)
            and "original_parent" in ast.unparse(removal)
            and all(call.lineno < _calls(removal, "os.rmdir")[0].lineno for call in guard_calls[:2])
            and guard_calls[-1].lineno > _calls(removal, "os.rmdir")[0].lineno,
            "CO-R3J original removal must pin parent and fence pre/post native cut",
        )
        names = {node.name for node in witness.body if isinstance(node, ast.FunctionDef)}
        require(
            _R3J_TEST,
            {
                "test_displaced_parent_is_rejected_before_destructive_cut",
                "test_parent_replaced_at_native_rmdir_cannot_ack_success",
                "test_parent_postcut_observation_failure_refuses_ack",
            }
            <= names,
            "CO-R3J parent displacement negative controls missing",
        )
    # CO-R3K: no prepublication unbound cgroup kill/remove and no controller
    # writes through re-resolved resource paths. The identity and durable
    # recovery record must precede any mutable controller effects. This is an
    # original-owner safety contract, not independently enforced parent custody.
    try:
        shell = (root / _R3_QUALIFIER).read_text(encoding="utf-8")
        tree = ast.parse((root / _CGROUP_OWNER).read_text(encoding="utf-8"))
        witness = ast.parse((root / _R3K_TEST).read_text(encoding="utf-8"))
        ast.parse((root / _R3K_GUARD).read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        issues.append(
            Violation(root / _R3K_TEST, f"CO-R3K prework owner boundary unavailable: {exc}")
        )
    else:
        core = shell.split("run_ring CORE " + chr(92) + chr(10), 1)
        core = core[1].split("\n# AG-02B1", 1)[0] if len(core) == 2 else ""
        require(
            _R3_QUALIFIER,
            all(
                core.count(name) == 1
                for name in (
                    "tests/unit/integrations/test_co_r3k_prework_identity_rollback.py",
                    "../scripts/tests/test_co_r3k_prework_architecture.py",
                )
            ),
            "CO-R3K original CORE prework witnesses must be enrolled once",
        )
        opening = _method(tree, "CgroupV2AttemptLease", "open")
        applying = _method(tree, None, "_apply_resource_limits")
        if opening is not None:
            identity = _calls(opening, "_exact_cgroup_identity")
            persist = _calls(opening, "_write_resource_lease")
            limits = _calls(opening, "_apply_resource_limits")
            kills = _calls(opening, "_kill_cgroup")
            removals = _calls(opening, "_remove_cgroup")
            checks = _calls(opening, "_require_original_cgroup")
            require(
                _CGROUP_OWNER,
                len(identity) == len(persist) == len(limits) == len(kills) == len(removals) == 1
                and identity[0].lineno < persist[0].lineno < limits[0].lineno
                and bool(checks)
                and checks[0].lineno < kills[0].lineno
                and all(
                    any(
                        k.arg == "expected_identity"
                        and ast.unparse(k.value) == "self.cgroup_identity"
                        for k in call.keywords
                    )
                    for call in (*kills, *removals, *limits)
                )
                and len(_calls(opening, "_wait_for_cgroup_empty")) == 1,
                "CO-R3K original creation must persist identity before effects and use only bound rollback",
            )
        else:
            require(_CGROUP_OWNER, False, "CO-R3K original attempt open owner missing")
        require(
            _CGROUP_OWNER,
            applying is not None
            and len(_calls(applying, "_bound_cgroup_directory")) == 1
            and len(_calls(applying, "os.open")) == 1
            and len(_calls(applying, "os.write")) == 1
            and "os.O_NOFOLLOW" in ast.unparse(applying)
            and "dir_fd=directory" in ast.unparse(applying)
            and "expected_identity" in ast.unparse(applying)
            and not _calls(applying, "_write_limit"),
            "CO-R3K original controller effects must be directory-handle-bound",
        )
        names = {node.name for node in witness.body if isinstance(node, ast.FunctionDef)}
        require(
            _R3K_TEST,
            {
                "test_creation_identity_failure_never_attempts_unbound_kill_or_remove",
                "test_replaced_path_during_initialization_retains_record_and_foreign_target",
                "test_failed_native_rollback_retains_original_recovery_record",
                "test_bound_limit_rejects_foreign_path_after_directory_pin",
            }
            <= names,
            "CO-R3K negative prework witnesses missing",
        )
    # CO-R3L: native delegated controller writes, nested/direct membership
    # effects and launcher handoff must all use creation-bound cgroup handles.
    # No independent parent custody or physical LACE writer absence is asserted.
    try:
        shell = (root / _R3_QUALIFIER).read_text(encoding="utf-8")
        owner = ast.unparse(ast.parse((root / _CGROUP_OWNER).read_text(encoding="utf-8")))
        launcher = (root / _R3L_LAUNCHER).read_text(encoding="utf-8")
        witness = ast.parse((root / _R3L_TEST).read_text(encoding="utf-8"))
        ast.parse((root / _R3L_GUARD).read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        issues.append(Violation(root / _R3L_TEST, f"CO-R3L bound attachment missing: {exc}"))
    else:
        core = shell.split("run_ring CORE " + chr(92) + chr(10), 1)
        core = core[1].split("\n# AG-02B1", 1)[0] if len(core) == 2 else ""
        require(
            _R3_QUALIFIER,
            all(
                core.count(name) == 1
                for name in (
                    "tests/unit/integrations/test_co_r3l_bound_nested_attachment.py",
                    "../scripts/tests/test_co_r3l_bound_attachment_architecture.py",
                )
            ),
            "CO-R3L native owner and X0 witnesses must be in original CORE exactly once",
        )
        require(
            _CGROUP_OWNER,
            all(
                token in owner
                for token in (
                    "nested_identity: tuple[int, int] | None",
                    "monitor_identity: tuple[int, int] | None",
                    "expected_identity=lease.cgroup_identity",
                    "_mkdir_bound_cgroup_child(",
                    "dir_fd=descriptor",
                    "'cgroup.subtree_control', payload",
                    "'cgroup.procs', f'{pid}\\n'.encode('ascii')",
                    "expected_identity: tuple[int, int]",
                    "str(self.cgroup_identity[0]), str(self.cgroup_identity[1])",
                    "os.O_NOFOLLOW",
                )
            ),
            "CO-R3L original controller/member mutation identity contract missing",
        )
        require(
            _R3L_LAUNCHER,
            all(
                token in launcher
                for token in (
                    "identity=nested_identity",
                    "identity=monitor_identity",
                    "expected_identity=identity",
                    "separator != 6",
                    "Nested strict execution launcher identity unproven",
                )
            )
            and launcher.count("identity=monitor_identity") == 2
            and launcher.count("identity=nested_identity") == 1
            and launcher.count("move_process(monitor_root,") == 2,
            "CO-R3L nested launcher may not discard transported identity",
        )
        names = {node.name for node in witness.body if isinstance(node, ast.FunctionDef)}
        require(
            _R3L_TEST,
            {
                "test_subtree_write_is_bound_to_creation_identity_and_rejects_substitution",
                "test_control_writes_refuse_symlink_and_short_write",
                "test_child_creation_rejects_parent_replacement",
                "test_direct_process_bootstrap_refuses_replaced_cgroup_and_does_not_exec",
                "test_nested_launcher_cli_rejects_unbound_or_invalid_identity",
            }
            <= names,
            "CO-R3L hostile identity regression witnesses missing",
        )
    # CO-R3M: durable unresolved intent before mkdir, never infer original
    # kernel inode from a pathname after crash. Pending records quarantine.
    try:
        shell = (root / _R3_QUALIFIER).read_text(encoding="utf-8")
        owner_source = (root / _CGROUP_OWNER).read_text(encoding="utf-8")
        owner = ast.parse(owner_source)
        witness = ast.parse((root / _R3M_TEST).read_text(encoding="utf-8"))
        ast.parse((root / _R3M_GUARD).read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        issues.append(
            Violation(
                root / _R3M_TEST, f"CO-R3M original pending allocation boundary unavailable: {exc}"
            )
        )
    else:
        core = shell.split("run_ring CORE " + chr(92) + chr(10), 1)
        core = core[1].split("\n# AG-02B1", 1)[0] if len(core) == 2 else ""
        require(
            _R3_QUALIFIER,
            all(
                core.count(name) == 1
                for name in (
                    "tests/unit/integrations/test_co_r3m_pending_cgroup_allocation.py",
                    "../scripts/tests/test_co_r3m_pending_allocation_architecture.py",
                )
            ),
            "CO-R3M original CORE pending allocation witnesses must be enrolled once",
        )
        opening = _method(owner, "CgroupV2AttemptLease", "open")
        pending = _method(owner, None, "_write_pending_resource_lease")
        publishing = _method(owner, None, "_write_resource_lease")
        recovery = _method(owner, None, "_reconcile_stale_cgroups")
        if opening is None:
            require(_CGROUP_OWNER, False, "CO-R3M original allocation owner absent")
        else:
            marker = _calls(opening, "_write_pending_resource_lease")
            effect = _calls(opening, "self.cgroup.mkdir")
            bind = _calls(opening, "_exact_cgroup_identity")
            seal = _calls(opening, "_write_resource_lease")
            require(
                _CGROUP_OWNER,
                len(marker) == len(effect) == len(bind) == len(seal) == 1
                and marker[0].lineno < effect[0].lineno < bind[0].lineno < seal[0].lineno,
                "CO-R3M pending durable intent must precede mkdir and identity sealing",
            )
        require(
            _CGROUP_OWNER,
            pending is not None
            and len(_calls(pending, "publish_text_file")) == 1
            and len(_calls(pending, "lease.state_file.exists")) == 1
            and len(_calls(pending, "lease.state_file.is_symlink")) == 1
            and "'phase': 'allocation_pending'" in ast.unparse(pending),
            "CO-R3M pending publication must be durable and refuse overwrites",
        )
        require(
            _CGROUP_OWNER,
            publishing is not None and "'phase': 'identity_bound'" in ast.unparse(publishing),
            "CO-R3M original inode publication phase missing",
        )
        require(
            _CGROUP_OWNER,
            recovery is not None
            and "if phase == 'allocation_pending'" in ast.unparse(recovery)
            and "refusing cleanup" in ast.unparse(recovery)
            and "if phase != 'identity_bound'" in ast.unparse(recovery),
            "CO-R3M unresolved allocation cannot be scavenged by PID/name",
        )
        names = {node.name for node in witness.body if isinstance(node, ast.FunctionDef)}
        require(
            _R3M_TEST,
            {
                "test_pending_identity_durably_published_before_mkdir",
                "test_pending_publication_failure_prevents_allocation",
                "test_stale_pending_quarantines_without_guessing_native_identity",
                "test_real_child_exit_between_mkdir_and_identity_publication",
            }
            <= names,
            "CO-R3M real process-exit and unresolved-recovery regressions missing",
        )
    # CO-R3N: native cgroup proof must be explicitly requested, originate in
    # the original CORE ring, and never auto-qualify on hosted/mock resources.
    try:
        shell = (root / _R3_QUALIFIER).read_text(encoding="utf-8")
        native_text = (root / _R3N_TEST).read_text(encoding="utf-8")
        witness = ast.parse(native_text)
        native_text = ast.unparse(witness)
        ast.parse((root / _R3N_GUARD).read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        issues.append(Violation(root / _R3N_TEST, f"CO-R3N native witness unavailable: {exc}"))
    else:
        core = shell.split("run_ring CORE " + chr(92) + chr(10), 1)
        core = core[1].split("\n# AG-02B1", 1)[0] if len(core) == 2 else ""
        require(
            _R3_QUALIFIER,
            core.count("../scripts/tests/test_co_r3n_native_cgroup_qualification.py") == 1
            and core.count('"${r3n_native_tests[@]}"') == 1
            and shell.count(
                "r3n_native_tests+=(tests/native/test_co_r3n_native_cgroup_v2_witness.py)"
            )
            == 1
            and shell.count("R3N_NATIVE_CGROUP_STATUS.txt")
            == (3 if "if [[ -f .checkpoint/CO_R3U2B2B2A_SOURCE.sha256 ]]; then" in shell else 2)
            and "status=NOT_PROVEN" in shell
            and 'if [[ -n "${QUALIFY_R3N_CGROUP_ROOT:-}" ]]; then'
            in (
                shell.split("# CO-R3N real cgroup-v2 evidence is opt-in", 1)[-1].split(
                    "# Fail-before-work for untrusted", 1
                )[0]
            ),
            "CO-R3N must remain opt-in, honest and enrolled exactly once in original CORE",
        )
        names = {node.name for node in witness.body if isinstance(node, ast.FunctionDef)}
        require(
            _R3N_TEST,
            {
                "_require_disposable_delegated_cgroup",
                "test_real_cgroup_v2_original_process_attachment_and_teardown",
                "test_real_cgroup_v2_pending_allocation_crash_quarantines",
            }
            <= names
            and all(
                token in native_text
                for token in (
                    "assert result.returncode == 77",
                    "cg._reconcile_stale_cgroups(lease.root, lease.state_file.parent)",
                    "lease.close()",
                    "assert not lease.state_file.exists() and (not lease.cgroup.exists())",
                    "_require_disposable_delegated_cgroup(root, Path('/proc/self/mountinfo')",
                )
            ),
            "CO-R3N missing original native effect and fail-closed pending crash proofs",
        )
        original_teardown = _method(
            witness, None, "test_real_cgroup_v2_original_process_attachment_and_teardown"
        )
        require(
            _R3N_TEST,
            original_teardown is not None and len(_calls(original_teardown, "lease.close")) == 1,
            "CO-R3N original native teardown must execute in the original native case",
        )
    # CO-R3O is a strict extension of the SAME native CORE witness; there is
    # no second qualification path, physical authority, or self-attested PASS.
    try:
        shell = (root / _R3_QUALIFIER).read_text(encoding="utf-8")
        witness_text = (root / _R3N_TEST).read_text(encoding="utf-8")
        witness = ast.parse(witness_text)
        ast.parse((root / _R3O_GUARD).read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        issues.append(Violation(root / _R3O_GUARD, f"CO-R3O original witness unavailable: {exc}"))
    else:
        core = shell.split("run_ring CORE " + chr(92) + chr(10), 1)
        core = core[1].split("\n# Do not promote REQUESTED", 1)[0] if len(core) == 2 else ""
        require(
            _R3_QUALIFIER,
            (
                core.count("../scripts/tests/test_co_r3o_native_witness_contract.py") == 1
                and core.count('"${r3n_native_tests[@]}"') == 1
                and shell.count("--require-module test_co_r3n_native_cgroup_v2_witness") == 1
                and shell.count("R3O_NATIVE_STATUS.txt") == 4
                and "status=NOT_PROVEN" in shell
                and "status=REQUESTED_UNQUALIFIED" in shell
                and "status=CORE_ACCEPTED_NATIVE_DEVELOPMENT_ONLY" in shell
                and "status=FAILED_OR_INCOMPLETE" in shell
                and 'if (( last_result == 0 )) && [[ -s "$evidence/CORE.json" ]]; then' in shell
                and shell.count(
                    "if [[ -f .checkpoint/CO_R3O_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3O_SOURCE.sha256'; fi"
                )
                == 1
            ),
            "CO-R3O native evidence must be CORE-supervised, completed and fail-closed",
        )
        names = {node.name for node in witness.body if isinstance(node, ast.FunctionDef)}
        require(
            _R3N_TEST,
            {
                "test_real_cgroup_v2_stale_identity_cannot_start_payload",
                "test_real_cgroup_v2_original_nested_delegation",
            }
            <= names
            and all(
                token in witness_text
                for token in (
                    "assert result.returncode != 0",
                    'assert "CO_R3O_PAYLOAD_EXECUTED" not in result.stdout',
                    'assert {"cpu", "memory", "pids"} <= actual',
                    "lease.nested_identity = None",
                    'with pytest.raises(FleetPlanInvalid, match="original cgroup identities"):',
                )
            ),
            "CO-R3O native stale-identity and nested-controller effects missing",
        )
    # CO-R3P: original physical resource reservation must consult the SAME
    # durable signer epoch and original signed Run lineage at its write cut.
    try:
        lifecycle = sources[_LIFECYCLE]
        method = _method(lifecycle, "FleetRunLifecycle", "require_execution_authority_in")
        guard = ast.parse((root / _R3P_GUARD).read_text(encoding="utf-8"))
        tests = ast.parse((root / _R3P_TEST).read_text(encoding="utf-8"))
        shell = (root / _R3_QUALIFIER).read_text(encoding="utf-8")
    except (OSError, SyntaxError) as exc:
        issues.append(
            Violation(root / _R3P_GUARD, f"CO-R3P original effect fence unavailable: {exc}")
        )
    else:
        calls = (
            []
            if method is None
            else [_name(call.func) for call in ast.walk(method) if isinstance(call, ast.Call)]
        )
        require(
            _LIFECYCLE,
            method is not None
            and calls.count("SignerTrustEpochAuthority") >= 2
            and "SignerTrustEpochAuthority(connection).require_in" in ast.unparse(method)
            and "require_original_admitted_signer_epoch_in" in calls
            and "binding.configured.require_unchanged" in calls
            and "self._fleet_provisioner.resolve" in calls
            and "deployment_signer_policy_snapshot" in calls
            and "current != expected" in ast.unparse(method),
            "CO-R3P original live physical-effect cut must reprove signed and durable signer epoch",
        )
        names = {node.name for node in tests.body if isinstance(node, ast.FunctionDef)}
        require(
            _R3P_TEST,
            {
                "test_real_admitted_signed_epoch_allows_original_effect_cut",
                "test_original_effect_cut_rejects_untrusted_or_stale_generation",
                "test_same_original_writer_rechecks_after_provisional_fleet_intent",
                "test_local_empty_cgroup_cannot_promote_foreign_writer_absence",
            }
            <= names,
            "CO-R3P original signer revoke/rollback regressions absent",
        )
        require(
            _R3P_GUARD,
            len(
                [
                    node
                    for node in guard.body
                    if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
                ]
            )
            >= 2,
            "CO-R3P architecture mutations must remain active",
        )
        core = shell.split("run_ring CORE " + chr(92) + chr(10), 1)
        core = core[1].split("\n# Do not promote REQUESTED", 1)[0] if len(core) == 2 else ""
        require(
            _R3_QUALIFIER,
            core.count("tests/unit/services/test_co_r3p_original_fleet_effect_revocation.py") == 1
            and core.count("../scripts/tests/test_co_r3p_original_effect_epoch_architecture.py")
            == 1
            and shell.count(
                "if [[ -f .checkpoint/CO_R3P_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3P_SOURCE.sha256'; fi"
            )
            == 1,
            "CO-R3P original CORE and exact-source enrollment required",
        )
    # CO-R3Q: the SAME original live signer/lease/intent authority must be
    # rechecked at BOTH post-reservation native-effect handoff boundaries.
    source = root / "backend/src/oh_goon/integrations/fleet_plan_bubblewrap_isolation.py"
    owner_source = root / "backend/src/oh_goon/runtime/enforcer_lace_fleet_resource_owner.py"
    try:
        backend_tree = ast.parse(source.read_text(encoding="utf-8"))
        owner_tree = ast.parse(owner_source.read_text(encoding="utf-8"))
        owner_method = _method(
            owner_tree, "OriginalFleetLocalLaceReservation", "require_effect_authority"
        )
        execute = _method(backend_tree, "BubblewrapProcessIsolation", "execute")
        shell = (root / _R3_QUALIFIER).read_text(encoding="utf-8")
        tests = ast.parse((root / _R3Q_TEST).read_text(encoding="utf-8"))
        guard = ast.parse((root / _R3Q_GUARD).read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        issues.append(Violation(source, f"CO-R3Q post-reservation effect owner missing: {exc}"))
    else:
        expressions = "" if execute is None else ast.unparse(execute)
        order = [
            expressions.find(value)
            for value in (
                "proof = request.local_resource_intent.reserve(request, self)",
                "require_effect_authority()\n        resource_lease.open()",
                "require_effect_authority()\n                network_lease.open(",
                "require_effect_authority()\n                result = run_command(",
            )
        ]
        require(
            source,
            all(n >= 0 for n in order)
            and order == sorted(order)
            and expressions.count("request.local_resource_intent.require_effect_authority(proof)")
            == 1
            and "original LACE pre-effect revocation fence missing" in expressions,
            "CO-R3Q same original effect fence must precede all native allocations/launch",
        )
        owner_text = "" if owner_method is None else ast.unparse(owner_method)
        require(
            owner_source,
            owner_method is not None
            and "self.validate_authority_in(connection)" in owner_text
            and owner_text.count("self.validate_authority_in(connection)") == 2
            and "journal.verify(self.run_id, auth=self.authenticator)" in owner_text
            and "self.session.mutate(original_cut)" in owner_text
            and "self.session.cancel_requested_in(connection)" in owner_text,
            "CO-R3Q live original authority and signed intent must be revalidated by original session",
        )
        names = {n.name for n in tests.body if isinstance(n, ast.FunctionDef)}
        require(
            root / _R3Q_TEST,
            {
                "test_original_reservation_rechecked_after_commit_with_same_signed_intent",
                "test_revocation_after_reservation_prevents_cgroup_open",
                "test_cancel_after_cgroup_open_prevents_network_effect_and_cleans_up",
                "test_revocation_after_allocation_observation_blocks_payload_launch",
            }
            <= names,
            "CO-R3Q signed reservation and effect negative controls missing",
        )
        require(
            root / _R3Q_GUARD,
            len(
                [
                    n
                    for n in guard.body
                    if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
                ]
            )
            >= 1,
            "CO-R3Q X0 negative mutation guard missing",
        )
        core = shell.split("run_ring CORE " + chr(92) + chr(10), 1)
        core = core[1].split("\n# Do not promote REQUESTED", 1)[0] if len(core) == 2 else ""
        require(
            root / _R3_QUALIFIER,
            core.count("tests/unit/services/test_co_r3q_effect_revalidation.py") == 1
            and core.count("../scripts/tests/test_co_r3q_effect_architecture.py") == 1
            and shell.count(
                "if [[ -f .checkpoint/CO_R3Q_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3Q_SOURCE.sha256'; fi"
            )
            == 1,
            "CO-R3Q original CORE and exact-source qualifier required",
        )
    # CO-R3R: preserve the one shared diagnostic observer's complete visible
    # process census.  This protects scoped NOT_OBSERVED from task churn; it
    # cannot promote independent custody or physical absence.
    issues.extend(check_co_r3r_visibility(root))
    issues.extend(check_co_r3s_observation(root))
    issues.extend(check_co_r3t_no_legacy_pid_scavenging(root))
    return issues


def check_co_r3r_visibility(root: Path = ROOT) -> list[Violation]:
    owner = root / "backend/src/oh_goon/integrations/linux_physical_observer.py"
    test = root / "scripts/tests/test_co_r3r_visible_holder_race.py"
    native = root / "backend/tests/native/test_co_r3r_real_visible_holder.py"
    guard = root / "scripts/tests/test_co_r3r_observer_architecture.py"
    shell = root / _R3_QUALIFIER
    issues: list[Violation] = []
    try:
        tree = ast.parse(owner.read_text(encoding="utf-8"))
        method = _method(tree, None, "_visible_foreign_mount_targets")
        source = "" if method is None else ast.unparse(method)
        census = (
            [
                n
                for n in ast.walk(method)
                if isinstance(n, ast.Call)
                and _name(n.func) == "os.scandir"
                and n.args
                and isinstance(n.args[0], ast.Name)
                and n.args[0].id == "proc_root"
            ]
            if method
            else []
        )
        tests = ast.parse(test.read_text(encoding="utf-8"))
        native_tests = ast.parse(native.read_text(encoding="utf-8"))
        guard_tests = ast.parse(guard.read_text(encoding="utf-8"))
        selection = shell.read_text(encoding="utf-8")
    except (OSError, SyntaxError) as exc:
        return [Violation(owner, f"CO-R3R original observer/qualification evidence missing: {exc}")]
    if (
        len(census) < 2
        or source.count("final_pids") < 2
        or "final_pids != pids" not in source
        or "raise ValueError"
        not in source.split("final_pids != pids", 1)[1].split("return _component", 1)[0]
        or source.index("final_pids != pids") > source.rindex("return _component")
    ):
        issues.append(
            Violation(
                owner, "CO-R3R original foreign mount scan must reject a changed final PID roster"
            )
        )
    names = {n.name for n in tests.body if isinstance(n, ast.FunctionDef)}
    if (
        not {
            "test_foreign_pid_roster_drift_is_unknown_at_original_kernel_observer",
            "test_stable_foreign_mount_is_positive_only_and_no_matches_are_not_absence",
        }
        <= names
    ):
        issues.append(
            Violation(test, "CO-R3R foreign process replacement negative controls absent")
        )
    if not any(
        isinstance(n, ast.FunctionDef) and n.name.startswith("test_detached_session_")
        for n in native_tests.body
    ):
        issues.append(Violation(native, "CO-R3R real-host FD holder test absent"))
    if not any(
        isinstance(n, ast.FunctionDef) and n.name.startswith("test_") for n in guard_tests.body
    ):
        issues.append(Violation(guard, "CO-R3R observer mutation guard absent"))
    core = selection.split("run_ring CORE " + chr(92) + chr(10), 1)
    core = core[1].split("\n# Do not promote REQUESTED", 1)[0] if len(core) == 2 else ""
    for path in (
        "../scripts/tests/test_co_r3r_visible_holder_race.py",
        "../scripts/tests/test_co_r3r_observer_architecture.py",
        "tests/native/test_co_r3r_real_visible_holder.py",
    ):
        if core.count(path) != 1:
            issues.append(Violation(shell, f"CO-R3R CORE witness missing or duplicated: {path}"))
    if (
        selection.count(
            "if [[ -f .checkpoint/CO_R3R_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3R_SOURCE.sha256'; fi"
        )
        != 1
    ):
        issues.append(Violation(shell, "CO-R3R exact source selection missing"))
    return issues


def check_co_r3s_observation(root: Path = ROOT) -> list[Violation]:
    """Keep CO-R3S handle-bound diagnostic observation and CORE evidence."""
    owner = root / "backend/src/oh_goon/integrations/linux_physical_observer.py"
    shell = root / _R3_QUALIFIER
    tests = root / "scripts/tests/test_co_r3s_pinned_cgroup_observation.py"
    guard = root / "scripts/tests/test_co_r3s_architecture.py"
    issues: list[Violation] = []
    try:
        tree = ast.parse(owner.read_text(encoding="utf-8"))
        observed = _method(tree, None, "_cgroup")
        reader = _method(tree, None, "_read_observed_cgroup_control")
        test_tree = ast.parse(tests.read_text(encoding="utf-8"))
        guard_tree = ast.parse(guard.read_text(encoding="utf-8"))
        selection = shell.read_text(encoding="utf-8")
        source = "" if observed is None else ast.unparse(observed)
        control = "" if reader is None else ast.unparse(reader)
    except (OSError, SyntaxError) as exc:
        return [Violation(owner, f"CO-R3S original cgroup observation evidence missing: {exc}")]
    if not (
        source.count("_read_observed_cgroup_control(") == 2
        and "os.fstat(directory_fd)" in source
        and "os.O_DIRECTORY" in source
        and "path.stat(follow_symlinks=False)" in source
        and "original cgroup path replaced during observation" in source
    ):
        issues.append(
            Violation(
                owner,
                "CO-R3S original cgroup must pin directory and reverify identity after observations",
            )
        )
    if not (
        "dir_fd=directory_fd" in control
        and "os.O_NOFOLLOW" in control
        and "os.fstat(control_fd)" in control
        and "os.read(control_fd, limit + 1)" in control
        and "cgroup.events" in control
        and "cgroup.procs" in control
    ):
        issues.append(
            Violation(
                owner, "CO-R3S both original kernel controls must be handle-bound and bounded"
            )
        )
    names = {n.name for n in test_tree.body if isinstance(n, ast.FunctionDef)}
    if (
        not {
            "test_substitution_mid_observation_refuses_false_stability",
            "test_symlink_control_and_overbudget_read_fail_closed",
        }
        <= names
    ):
        issues.append(Violation(tests, "CO-R3S replacement and control negative tests missing"))
    if not any(
        isinstance(n, ast.FunctionDef) and n.name.startswith("test_") for n in guard_tree.body
    ):
        issues.append(Violation(guard, "CO-R3S X0 mutation guard missing"))
    core = selection.split("run_ring CORE " + chr(92) + chr(10), 1)
    core = core[1].split("\n# Do not promote REQUESTED", 1)[0] if len(core) == 2 else ""
    for path in (
        "../scripts/tests/test_co_r3s_pinned_cgroup_observation.py",
        "../scripts/tests/test_co_r3s_architecture.py",
    ):
        if core.count(path) != 1:
            issues.append(
                Violation(shell, f"CO-R3S original CORE witness missing or duplicated: {path}")
            )
    marker = "if [[ -f .checkpoint/CO_R3S_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3S_SOURCE.sha256'; fi"
    if selection.count(marker) != 1:
        issues.append(Violation(shell, "CO-R3S exact-source selector missing"))
    return issues


def check_co_r3t_no_legacy_pid_scavenging(root: Path = ROOT) -> list[Violation]:
    """A dead legacy PID cannot reach network or cgroup destruction.

    This checks original owner source only, not a second admission policy.
    An independently provisioned native custody issuer is still missing.
    """
    issues: list[Violation] = []
    for relative, function in (
        (_CGROUP_OWNER, "_reconcile_stale_cgroups"),
        (
            "backend/src/oh_goon/integrations/_fleet_plan_restricted_egress.py",
            "_reconcile_stale_egress_leases",
        ),
    ):
        path = root / relative
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            original = _method(tree, None, function)
        except (SyntaxError, OSError):
            original = None
        if original is None:
            issues.append(Violation(path, "CO-R3T original legacy recovery owner missing"))
            continue
        source = ast.unparse(original)
        dangerous = {
            "_kill_cgroup",
            "_remove_cgroup",
            "_cleanup_egress_resources",
            "remove_file_durably",
            "os.rmdir",
            "run_command",
        }
        called = {_name(n.func) for n in ast.walk(original) if isinstance(n, ast.Call)}
        if called & dangerous or source.count("lacks independent physical custody") != 1:
            issues.append(
                Violation(
                    path,
                    "CO-R3T dead-PID legacy recovery must quarantine before native effects or retirement",
                )
            )
    shell = root / _R3_QUALIFIER
    selection = shell.read_text(encoding="utf-8") if shell.is_file() else ""
    core = selection.split("run_ring CORE " + chr(92) + chr(10), 1)
    core = core[1].split("\n# Do not promote REQUESTED", 1)[0] if len(core) == 2 else ""
    for name in (
        "tests/unit/integrations/test_co_r3t_legacy_orphan_fail_before_effect.py",
        "../scripts/tests/test_co_r3t_legacy_custody_architecture.py",
    ):
        if core.count(name) != 1:
            issues.append(
                Violation(
                    shell, f"CO-R3T CORE required original test missing or duplicated: {name}"
                )
            )
    marker = "if [[ -f .checkpoint/CO_R3T_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3T_SOURCE.sha256'; fi"
    if selection.count(marker) != 1:
        issues.append(Violation(shell, "CO-R3T exact-source selector missing"))
    return issues
