"""DB0: bound SQLite ownership census, enforced by the existing X0 gate.

This is a structural development guard, not a second transaction controller.
Ownership exceptions are explicit; a newly introduced operational writer must
be reviewed, migrated into LocalDatabase, or fail architecture admission.
"""
from __future__ import annotations

import ast
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation

# These owners have different, explicitly limited capabilities. None is a
# second authority for committing operational Run admission.
CANONICAL_SQLITE_OWNER = "runtime/database.py"
DERIVED_SCOUT = frozenset({"scout/database.py", "scout/store.py"})
PRELAUNCH_PROBES = frozenset({"runtime/storage_health.py", "runtime/_database_policy.py"})
SCHEMA_PARTICIPANTS = frozenset({"runtime/durable_revision.py", "runtime/exclusions.py"})

_CONNECTION_OWNERS = frozenset({CANONICAL_SQLITE_OWNER, *DERIVED_SCOUT, *PRELAUNCH_PROBES})
_TX_OWNERS = frozenset({CANONICAL_SQLITE_OWNER, *DERIVED_SCOUT, "runtime/storage_health.py"})
_SCRIPT_OWNERS = frozenset({CANONICAL_SQLITE_OWNER, *DERIVED_SCOUT, *SCHEMA_PARTICIPANTS, "runtime/enforcer_lace_journal.py"})


def database_authority_census(root: Path) -> list[tuple[str, int, str]]:
    """Return reproducible exact-file evidence for engine connection/commit sites."""
    package = root / "backend/src/oh_goon"
    findings: list[tuple[str, int, str]] = []
    for path in sorted(package.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(package).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = {
            alias.asname or alias.name for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names if alias.name == "sqlite3"
        }
        direct_connect = {
            alias.asname or alias.name for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "sqlite3"
            for alias in node.names if alias.name == "connect"
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in direct_connect:
                findings.append((relative, node.lineno, "connection"))
            if not isinstance(func, ast.Attribute):
                continue
            if (func.attr == "connect" and isinstance(func.value, ast.Name)
                    and func.value.id in imports):
                findings.append((relative, node.lineno, "connection"))
            if func.attr == "executescript":
                findings.append((relative, node.lineno, "schema_script"))
            if func.attr in {"commit", "rollback"}:
                findings.append((relative, node.lineno, "transaction"))
            if func.attr == "execute" and node.args:
                sql = node.args[0]
                # A conditional read/write BEGIN is also an owned transaction.
                expressions = (sql.body, sql.orelse) if isinstance(sql, ast.IfExp) else (sql,)
                for expr in expressions:
                    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
                        token = expr.value.strip().split(None, 1)[0].upper() if expr.value.strip() else ""
                        if token in {"BEGIN", "COMMIT", "ROLLBACK"}:
                            findings.append((relative, node.lineno, "transaction"))
                            break
    return sorted(set(findings))


def check_canonical_database_owner(root: Path) -> list[Violation]:
    package = root / "backend/src/oh_goon"
    if not (package / CANONICAL_SQLITE_OWNER).is_file():
        return [Violation(package / CANONICAL_SQLITE_OWNER, "DB0 missing canonical operational SQLite owner")]
    findings: list[Violation] = []
    try:
        census = database_authority_census(root)
    except (OSError, SyntaxError) as exc:
        return [Violation(package, f"DB0 database authority inventory unreadable: {exc}")]
    for relative, line, category in census:
        allowed = {
            "connection": _CONNECTION_OWNERS,
            "transaction": _TX_OWNERS,
            "schema_script": _SCRIPT_OWNERS,
        }[category]
        if relative not in allowed:
            findings.append(Violation(package / relative,
                                      f"DB0 unowned {category} at line {line}; LocalDatabase owns operational SQLite"))
    database_path = package / CANONICAL_SQLITE_OWNER
    database_tree = ast.parse(database_path.read_text(encoding="utf-8"))
    classes = [node for node in database_tree.body if isinstance(node, ast.ClassDef)
               and node.name == "LocalDatabase"]
    methods = {node.name: node for node in classes[0].body if isinstance(node, ast.FunctionDef)} if len(classes) == 1 else {}
    required = {
        "require_atomic_write_cut": ("active_cut", "connection.in_transaction", "original LocalDatabase atomic write cut required"),
        "_transaction_cut": ("active_cut", "BEGIN IMMEDIATE", "_prove_admitted_sqlite_identity", "nested operational database transaction forbidden"),
        "transaction": ("transaction_connection.commit()", "_publish_durable_revision"),
    }
    for name, tokens in required.items():
        method = methods.get(name)
        source = ast.unparse(method) if method else ""
        if any(token not in source for token in tokens):
            findings.append(Violation(database_path, f"DB1 original SQLite {name} transaction guarantee removed"))
    # The live original Fleet reservation may not be replaced with raw BEGIN.
    fleet = package / "services/fleet_admission.py"
    if fleet.is_file():
        source = fleet.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(fleet))
        methods = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                   and node.name == "publish_atomic_fleet_prework_ingress"]
        if len(methods) != 1 or "operational_journal._database.require_atomic_write_cut(connection)" not in ast.unparse(methods[0]):
            findings.append(Violation(fleet, "DB1 original Fleet admission must prove LocalDatabase write-cut ownership"))
    # The same X0 gate checks the named domain cut; no second architecture owner.
    methods = {n.name: n for n in classes[0].body if isinstance(n, ast.FunctionDef)} if len(classes) == 1 else {}
    requirements = {
        "_guard_atomic_domain_participant": ("require_atomic_write_cut", "SQLITE_TRANSACTION", "set_authorizer(None)", "set_authorizer(deny_transaction)"),
        "enroll_signer_trust_atomically": ("transaction(write=True)", "_guard_atomic_domain_participant", "enroll_in", "owner=self"),
        "revoke_signer_trust_atomically": ("transaction(write=True)", "_guard_atomic_domain_participant", "revoke_in", "owner=self"),
        "rotate_signer_trust_atomically": ("transaction(write=True)", "_guard_atomic_domain_participant", "rotate_in", "owner=self"),
        "publish_causal_admission_atomically": ("transaction(write=True)", "_guard_atomic_domain_participant", "publish_admission_in"),
    }
    for name, tokens in requirements.items():
        node = methods.get(name)
        source = ast.unparse(node) if node else ""
        if any(token not in source for token in tokens):
            findings.append(Violation(database_path, f"DB1B1 named SQLite domain operation {name} ownership removed"))
    signer_path = package / "runtime/enforcer_lace_signer_trust_epoch.py"
    if not signer_path.is_file():
        findings.append(Violation(signer_path, "DB1B1 original signer owner missing"))
    else:
        signer_source = signer_path.read_text(encoding="utf-8")
        signer_tree = ast.parse(signer_source)
        signer_class = [n for n in signer_tree.body if isinstance(n, ast.ClassDef) and n.name == "SignerTrustEpochAuthority"]
        signer_methods = {n.name: n for n in signer_class[0].body if isinstance(n, ast.FunctionDef)} if len(signer_class) == 1 else {}
        signer_cut = signer_methods.get("_require_participant_cut")
        if signer_cut is None or "require_atomic_write_cut" not in ast.unparse(signer_cut):
            findings.append(Violation(signer_path, "DB1B1 signer participant cut proof missing"))
        for participant in ("enroll_in", "revoke_in", "rotate_in"):
            node = signer_methods.get(participant)
            if node is None or "_require_participant_cut" not in ast.unparse(node):
                findings.append(Violation(signer_path, f"DB1B1 signer {participant} must use original cut"))
    journal_path = package / "runtime/enforcer_lace_journal.py"
    if not journal_path.is_file() or "def publish_admission_in(" not in journal_path.read_text(encoding="utf-8"):
        findings.append(Violation(journal_path, "DB1B1 must reuse original causal journal participant"))
    # No production code may silently reintroduce fixture-owned SQLite
    # BEGIN/COMMIT. Historical standalone behavior exists under tests ONLY.
    fixture_import = "tests.fixtures" + ".db1b2_legacy_sqlite"
    for path in package.rglob("*.py"):
        if fixture_import in path.read_text(encoding="utf-8"):
            findings.append(Violation(path, "DB1B2 test-only SQLite transaction fixture imported by operational source"))
    for name, cls_name, message in (
        ("enforcer_lace_journal.py", "CausalRunJournal", "standalone causal transaction forbidden"),
        ("enforcer_lace_signer_trust_epoch.py", "SignerTrustEpochAuthority", "standalone signer transaction forbidden"),
    ):
        path = package / "runtime" / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name), None)
        fn = next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_transaction"), None) if cls else None
        if fn is None or message not in ast.unparse(fn):
            findings.append(Violation(path, "DB1B2 production legacy transaction must fail before work"))
    required = {
        "transfer_causal_control_atomically": ("transaction(write=True)", "require_in", "transfer_control_in", "_guard_atomic_domain_participant"),
        "publish_causal_admission_atomically": ("_verify_exact_causal_admission", "operational_committed", "ALREADY_COMMITTED"),
        "_verify_exact_causal_admission": ("journal", "set_authorizer", "SELECT", "operational_committed"),
    }
    for name, tokens in required.items():
        node = methods.get(name)
        source = ast.unparse(node) if node else ""
        if any(token not in source for token in tokens):
            findings.append(Violation(database_path, f"DB1B2 {name} original SQLite ownership/reread proof missing"))
    # One owner must protect every operational transaction against a
    # downgraded connection and a changed filesystem identity.
    required_db2 = {
        "_transaction_cut": ("_require_durable_connection_policy", "BEGIN IMMEDIATE", "_require_path_identity"),
        "_require_durable_connection_policy": ("PRAGMA synchronous", "PRAGMA foreign_keys", "PRAGMA query_only", "DatabasePathIdentityLost"),
        "_require_path_identity": ("current.filesystem", "expected.filesystem", "DatabasePathIdentityLost"),
    }
    for name, tokens in required_db2.items():
        node = methods.get(name)
        source = ast.unparse(node) if node else ""
        if any(token not in source for token in tokens):
            findings.append(Violation(database_path, f"DB2 {name} original SQLite durability authority missing"))
    node = methods.get("export_verified_backup")
    source = ast.unparse(node) if node else ""
    for token in (
        "sqlite_authority_gate", "_require_path_identity", "_require_durable_connection_policy",
        "_prove_admitted_sqlite_identity", "inspect_database_path",
        "os.link", "os.fsync", "FileExistsError", "_require_private_backup_directory",
    ):
        if token not in source:
            findings.append(Violation(database_path, f"DB3 backup owner missing {token}"))
    has_vacuum_into = node is not None and any(
        isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
        and call.func.attr == "execute" and call.args
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == "VACUUM INTO ?"
        for call in ast.walk(node)
    )
    if not has_vacuum_into:
        findings.append(Violation(database_path, "DB3 SQLite-native VACUUM INTO backup call missing"))
    scout_path = package / "scout/database.py"
    if not scout_path.is_file():
        findings.append(Violation(scout_path, "DB3 disposable Scout source missing"))
    else:
        scout_tree = ast.parse(scout_path.read_text(encoding="utf-8"))
        scout_cls = next((n for n in scout_tree.body if isinstance(n, ast.ClassDef)
                          and n.name == "ScoutAnalyticalDatabase"), None)
        scout_methods = {n.name: n for n in scout_cls.body if isinstance(n, ast.FunctionDef)} if scout_cls else {}
        for name, tokens in {
            "_require_disposable_identity": ("operational_path", "_sidecar_files", "st_ino"),
            "_connect": ("_require_disposable_identity",),
            "remove": ("_require_disposable_identity",),
        }.items():
            node = scout_methods.get(name)
            source = ast.unparse(node) if node else ""
            if any(token not in source for token in tokens):
                findings.append(Violation(scout_path, f"DB3 disposable Scout owner missing {name} identity fence"))
    export = methods.get("export_verified_backup")
    verifier = methods.get("verify_offline_backup")
    exported = ast.unparse(export) if export else ""
    verified = ast.unparse(verifier) if verifier else ""
    if "self.verify_offline_backup(LocalStateBackupReceipt(" not in exported:
        findings.append(Violation(database_path, "DB4 original backup must verify snapshot before publication"))
    for token in ("inspect_database_path", "sqlite3.connect", "receipt.sha256", "read_durable_revision", "application_id", "O_NOFOLLOW", "st_ino"):
        if token not in verified:
            findings.append(Violation(database_path, f"DB4 original offline read-only verifier missing {token}"))
    export = methods.get("export_verified_backup")
    source = ast.unparse(export) if export else ""
    for token in ("published_identity", "_sync_backup_directory", "BackupPublicationUncertain",
                  "self.verify_offline_backup(receipt)", "target.unlink()"):
        if token not in source:
            findings.append(Violation(database_path, f"DB5 original backup failure fence missing {token}"))
    return findings
