"""T0B2A X0 guard: offline review intake cannot become runtime trust authority."""
from __future__ import annotations

import ast
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation
from oh_goon.repository_tooling.architecture.repository_layout import ROOT

_SOURCE = "backend/src/oh_goon/repository_tooling/offline_custody_review.py"
_TEST = "backend/tests/unit/repository_tooling/test_t0b2_offline_custody_review.py"
_DOSSIER_TEST = "backend/tests/unit/repository_tooling/test_t0b2b1_custody_dossier.py"


def check_offline_custody_review_contract(root: Path = ROOT) -> list[Violation]:
    issues: list[Violation] = []
    file = root / _SOURCE

    def reject(path: Path, reason: str) -> None:
        issues.append(Violation(path, "T0B2A offline intake boundary: " + reason))

    try:
        text = file.read_text(encoding="utf-8")
        tree = ast.parse(text)
        methods = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        for name, required in {
            "inspect_packet": ("_read_external_key", "_read_bounded", "_verify_review_signature", "expected_subjects"),
            "_verify_review_signature": ("verify", "from_public_bytes"),
            "_read_bounded": ("open", "fstat", "read"),
            "inspect_dossier": ("inspect_packet", "expected_subjects", "listdir"),
            "compare_review_dossiers": ("inspect_dossier",),
        }.items():
            method = methods.get(name)
            if not method:
                reject(file, f"missing original {name} preflight")
                continue
            called = {
                n.func.id if isinstance(n.func, ast.Name) else n.func.attr
                for n in ast.walk(method) if isinstance(n, ast.Call)
                if isinstance(n.func, (ast.Name, ast.Attribute))
            }
            for method_name in required:
                if method_name not in called:
                    reject(file, f"{name} lacks required {method_name} verification")
        inspect = methods.get("inspect_packet")
        if inspect is not None:
            def direct_calls(body: list[ast.stmt]) -> list[ast.Expr]:
                # Only a straight-line path through try bodies is accepted;
                # never count a call hidden inside if/loop/handler/function.
                found: list[ast.Expr] = []
                for stmt in body:
                    if isinstance(stmt, ast.Try):
                        found.extend(direct_calls(stmt.body))
                    elif (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                          and isinstance(stmt.value.func, ast.Name)
                          and stmt.value.func.id == "_verify_review_signature"):
                        found.append(stmt)
                return found

            direct_signature_calls = direct_calls(inspect.body)
            if len(direct_signature_calls) != 1:
                reject(file, "signature verification must be unconditional in the original intake path")
        for token in (
            '"AUTHENTICATED_REVIEW_PACKET_NOT_CUSTODY_PROOF"',
            '"gate_a": "BLOCKED"', '"runtime_authority": False',
            '"T1_migration_authorized": False', "os.O_NOFOLLOW", "before.st_nlink != 1",
            "_DOMAIN + packet", "expected_key_sha256", "_REGISTRY",
        ):
            if token not in text:
                reject(file, f"required fail-closed review invariant missing: {token}")
        project = (root / "backend/pyproject.toml").read_text(encoding="utf-8")
        if 'oh-goon-offline-custody-review = "oh_goon.repository_tooling.offline_custody_review:main"' not in project:
            reject(root / "backend/pyproject.toml", "offline reviewer must be declared in pyproject")
        shell = (root / "scripts/qualify_b12_development_wsl.sh").read_text(encoding="utf-8")
        for expected in (
            "if [[ -f .checkpoint/T0B2A_SOURCE.sha256 ]]; then embedded='.checkpoint/T0B2A_SOURCE.sha256'; fi",
            "tests/unit/repository_tooling/test_t0b2_offline_custody_review.py",
            "--require-module test_t0b2_offline_custody_review",
        ):
            if expected not in shell:
                reject(root / "scripts/qualify_b12_development_wsl.sh", "offline reviewer not in canonical qualification")
        if not (root / _TEST).is_file():
            reject(root / _TEST, "missing hostile review tests")
        if not (root / _DOSSIER_TEST).is_file():
            reject(root / _DOSSIER_TEST, "missing dossier completeness/hostile tests")
        for token in (
            '"COMPLETE_AUTHENTICATED_REVIEW_SET_NOT_CUSTODY_PROOF"',
            '"independent_custody_verified": False',
            'set(os.listdir(subjects_fd)) != set(names)',
            'set(os.listdir(fd)) != members',
            'packet_fd=fd',
            '"AUTHENTICATED_REVIEW_AGREEMENT_NOT_CUSTODY_PROOF"',
            'primary["proposed_dispositions"] != secondary["proposed_dispositions"]',
        ):
            if token not in text:
                reject(file, f"T0B2B1 dossier invariant missing: {token}")
        shell = (root / "scripts/qualify_b12_development_wsl.sh").read_text(encoding="utf-8")
        for expected in (
            "if [[ -f .checkpoint/T0B2B1_SOURCE.sha256 ]]; then embedded='.checkpoint/T0B2B1_SOURCE.sha256'; fi",
            '--require-module test_t0b2b1_custody_dossier',
        ):
            if expected not in shell:
                reject(root / "scripts/qualify_b12_development_wsl.sh", "dossier regression/manifest not enrolled")
        for sub in ("runtime", "services", "integrations", "native_issuer"):
            folder = root / "backend/src/oh_goon" / sub
            for path in folder.rglob("*.py"):
                if "offline_custody_review" in path.read_text(encoding="utf-8"):
                    reject(path, "offline review imported into production authority")
    except (OSError, SyntaxError) as exc:
        reject(file, f"cannot verify offline review architecture: {exc}")
    return issues
