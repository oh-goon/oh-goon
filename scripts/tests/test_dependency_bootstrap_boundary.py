import ast
import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_DIR = ROOT / "backend/src/oh_goon/repository_tooling"


def _bootstrap_entry() -> Path:
    payload = tomllib.loads(
        (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    )
    target = payload["project"]["scripts"]["oh-goon-dependency-policy"]
    module, separator, callable_name = target.partition(":")
    assert separator and callable_name == "main"
    return ROOT / "backend/src" / Path(*module.split(".")).with_suffix(".py")


BOOTSTRAP_ENTRY = _bootstrap_entry()


def _local_module_path(module: str) -> Path | None:
    if not module or "." in module:
        return None
    candidate = BOOTSTRAP_DIR / f"{module}.py"
    return candidate if candidate.is_file() else None


def _import_dependencies(node: ast.Import, stdlib: set[str]) -> tuple[Path, ...]:
    dependencies: list[Path] = []
    for alias in node.names:
        local = _local_module_path(alias.name)
        if local is None:
            assert alias.name.split(".", 1)[0] in stdlib
        else:
            dependencies.append(local)
    return tuple(dependencies)


def _from_import_dependencies(
    node: ast.ImportFrom, stdlib: set[str]
) -> tuple[Path, ...]:
    module = node.module or ""
    local = _local_module_path(module)
    if node.level:
        assert local is not None, (
            f"bootstrap relative import escapes owned closure: {module!r}"
        )
    elif local is None:
        assert module.split(".", 1)[0] in stdlib
    return () if local is None else (local,)


def _bootstrap_import_dependencies(path: Path, stdlib: set[str]) -> tuple[Path, ...]:
    dependencies: list[Path] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            dependencies.extend(_import_dependencies(node, stdlib))
        elif isinstance(node, ast.ImportFrom):
            dependencies.extend(_from_import_dependencies(node, stdlib))
    return tuple(dependencies)


def test_pre_environment_bootstrap_dependency_closure_is_stdlib_or_bootstrap_owned() -> (
    None
):
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    pending = [BOOTSTRAP_ENTRY]
    seen: set[Path] = set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        pending.extend(_bootstrap_import_dependencies(path, stdlib))
    assert BOOTSTRAP_ENTRY in seen
    assert BOOTSTRAP_DIR / "_dependency_resolution_primitives.py" in seen
    assert BOOTSTRAP_DIR / "_dependency_lock_portability.py" in seen


def test_direct_bootstrap_starts_without_pythonpath_site_packages_or_installed_oh_goon() -> (
    None
):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    result = subprocess.run(
        [sys.executable, "-S", str(BOOTSTRAP_ENTRY), "--help"],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "oh-goon-dependency-policy" in result.stdout
    assert "ModuleNotFoundError" not in result.stderr


def test_bootstrap_import_mode_is_explicit_not_exception_driven() -> None:
    for path in (BOOTSTRAP_ENTRY, BOOTSTRAP_DIR / "_dependency_lock_portability.py"):
        source = path.read_text(encoding="utf-8")
        assert "except ModuleNotFoundError" not in source
        assert "except ImportError" not in source
