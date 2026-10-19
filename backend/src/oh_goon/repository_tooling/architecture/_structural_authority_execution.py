from __future__ import annotations

import re
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import (
    Violation,
    dependency_policy_bootstrap_source_path,
)
from oh_goon.repository_tooling.architecture.shell_semantics import (
    make_semantic_facts,
    shell_semantic_commands,
)


def _check_development_test_runner_boundary(root: Path) -> list[Violation]:
    path = root / "scripts" / "development_test.sh"
    if not path.is_file():
        return [Violation(path, "development test runner must exist as the make test owner")]
    text = path.read_text(encoding="utf-8")
    commands = shell_semantic_commands(text)

    def make_target(argv: tuple[str, ...]) -> str | None:
        if not argv or Path(argv[0]).name != "make":
            return None
        index = 1
        while index < len(argv) and argv[index].startswith("-"):
            index += 1
        return argv[index] if index < len(argv) else None

    scheduled = [
        target
        for command in commands
        if (target := make_target(command.argv)) is not None and not target.startswith("<VAR:")
    ]
    violations: list[Violation] = []
    required = {
        "test-python",
        "test-tooling",
        "test-integration",
        "test-frontend",
        "frontend-typecheck",
    }
    missing = sorted(required - set(scheduled))
    if missing:
        violations.append(
            Violation(
                path, f"development test runner is missing fast code checks: {', '.join(missing)}"
            )
        )
    forbidden = {
        "certify",
        "browser-test",
        "browser-required-certification",
        "browser-targeted-certification",
        "backend-check",
        "tooling-check",
        "frontend-check",
        "format",
        "format-check",
        "lint",
        "quality-check",
        "quality-raise",
        "verify-p1",
        "local-runtime-verification",
        "strict-local-runtime-acceptance",
        "backend-install",
        "frontend-install",
        "qualification-preflight",
        "frontend-dependencies-check",
    }
    leaked = sorted(forbidden & set(scheduled))
    if leaked:
        violations.append(
            Violation(
                path,
                "development test runner must not schedule later quality/certification or acquisition gates: "
                + ", ".join(leaked),
            )
        )
    ordered = ("test-python-core-diagnostic", "test-python-dependency-complete", "check-generated")
    if all(target in scheduled for target in ordered):
        positions = tuple(scheduled.index(target) for target in ordered)
        if positions != tuple(sorted(positions)):
            violations.append(
                Violation(
                    path,
                    "development tests must run dependency-light diagnostics before dependency-complete/generated checks",
                )
            )
    authority_tokens = {token for command in commands for token in command.argv}
    if any(
        Path(token).name == "oh-goon-workflow" or token in {"systemd-run", "Delegate=yes"}
        for token in authority_tokens
    ):
        violations.append(
            Violation(
                path,
                "development test runner must not depend on APP certification or physical executor establishment",
            )
        )
    return violations


def _check_backend_dependency_complete_partition(root: Path) -> list[Violation]:
    """Leaf HTTP-client compatibility must not block unrelated unit collection."""
    violations: list[Violation] = []
    product = root / "backend/src/oh_goon/integrations/gitlab_ci_component.py"
    if not product.is_file():
        return violations
    normal_units = root / "backend/tests/unit"
    dependency_complete = root / "backend/tests/dependency_complete"
    diagnostic = root / "scripts/backend_core_test_diagnostic.sh"
    makefile = root / "Makefile"
    pyproject = root / "backend/pyproject.toml"

    if product.is_file():
        text = product.read_text(encoding="utf-8")
        if any(
            line == "import httpx2" or line.startswith("from httpx2") for line in text.splitlines()
        ):
            violations.append(
                Violation(
                    product,
                    "httpx2 must stay a leaf/lazy GitLab transport dependency; module import must not block unrelated tests",
                )
            )

    early_surfaces = (normal_units, root / "backend/tests/integration", root / "scripts/tests")
    for surface in early_surfaces:
        if not surface.is_dir():
            continue
        for path in surface.rglob("test_*.py"):
            text = path.read_text(encoding="utf-8")
            if any(
                line == "import httpx2" or line.startswith("from httpx2")
                for line in text.splitlines()
            ):
                violations.append(
                    Violation(
                        path,
                        "pre-dependency test surfaces must not import dependency-complete httpx2 directly",
                    )
                )

    late_httpx2 = (
        tuple(
            path
            for path in dependency_complete.rglob("*.py")
            if "import httpx2" in path.read_text(encoding="utf-8")
        )
        if dependency_complete.is_dir()
        else ()
    )
    if not late_httpx2:
        violations.append(
            Violation(
                dependency_complete,
                "dependency-complete unit surface must own the real httpx2 transport contract",
            )
        )

    violations.extend(_check_hosted_diagnostic_contract(diagnostic))

    if makefile.is_file():
        facts = make_semantic_facts(makefile.read_text(encoding="utf-8"))

        def payload(argv: tuple[str, ...]) -> tuple[str, ...]:
            values = list(argv)
            if "--" in values:
                values = values[values.index("--") + 1 :]
            if values[:1] == ["env"]:
                values.pop(0)
                while values and "=" in values[0] and not values[0].startswith("-"):
                    values.pop(0)
            return tuple(values)

        def target_payloads(target: str) -> tuple[tuple[str, ...], ...]:
            return tuple(payload(command.argv) for command in facts.target_commands.get(target, ()))

        diagnostic_payloads = target_payloads("test-python-core-diagnostic")
        if not any(
            argv[-2:] == ("bash", "scripts/backend_core_test_diagnostic.sh")
            for argv in diagnostic_payloads
        ):
            violations.append(
                Violation(
                    makefile,
                    "backend unit dependency partition is missing: hosted diagnostic owner",
                )
            )

        bounded = {
            "test-python-dependency-complete": ("backend/tests/dependency_complete", "60"),
            "test-python": ("backend/tests/unit", "120"),
            "test-python-complete": ("backend/tests/unit", "180"),
        }
        for target, (surface, timeout) in bounded.items():
            matched = False
            for argv in target_payloads(target):
                if not argv or Path(argv[0]).name != "oh-goon-test-batches" or surface not in argv:
                    continue
                try:
                    timeout_index = argv.index("--batch-timeout")
                except ValueError:
                    continue
                if timeout_index + 1 < len(argv) and argv[timeout_index + 1] == timeout:
                    matched = True
                    break
            if not matched:
                violations.append(
                    Violation(
                        makefile,
                        f"backend unit dependency partition is missing semantic {target} surface/timeout ownership",
                    )
                )

    violations.extend(_check_pytest_testpaths(pyproject))
    return violations


_EXPECTED_PYTEST_TESTPATHS = (
    "tests/unit",
    "tests/dependency_complete",
    "tests/integration",
)


def _check_pytest_testpaths(pyproject: Path) -> list[Violation]:
    if not pyproject.is_file():
        return []
    try:
        payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return [
            Violation(
                pyproject,
                "backend pyproject.toml must remain parseable for pytest testpaths authority",
            )
        ]

    tool = payload.get("tool")
    pytest_cfg = tool.get("pytest") if isinstance(tool, dict) else None
    ini_options = pytest_cfg.get("ini_options") if isinstance(pytest_cfg, dict) else None
    testpaths = ini_options.get("testpaths") if isinstance(ini_options, dict) else None
    semantic_testpaths = (
        tuple(item for item in testpaths if isinstance(item, str))
        if isinstance(testpaths, list) and all(isinstance(item, str) for item in testpaths)
        else ()
    )
    if semantic_testpaths == _EXPECTED_PYTEST_TESTPATHS:
        return []
    return [
        Violation(
            pyproject,
            "pytest testpaths must retain dependency-complete unit ownership in full environments",
        )
    ]


def _check_host_python_selector_contract(selector: Path) -> list[Violation]:
    if not selector.is_file():
        return [Violation(selector, "host interpreter selector is missing")]
    text = selector.read_text(encoding="utf-8")
    # Candidate-selection semantics are executable behavior, not source spelling.
    # Comments, aliases, and private helper organization are representation-only.
    violations: list[Violation] = []
    for command in shell_semantic_commands(text):
        if _classify_hosted_action(list(command.argv)) == "acquisition":
            violations.append(
                Violation(
                    selector,
                    "host interpreter selector must never acquire dependencies: "
                    + " ".join(command.argv[:3]),
                )
            )
    return violations


@dataclass(frozen=True)
class _HostedDiagnosticAction:
    order: tuple[int, int]
    kind: str
    argv: tuple[str, ...]
    environment: dict[str, str]


def _strip_shell_comment(line: str) -> str:
    """Remove an unquoted shell comment without interpreting shell syntax."""
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index]
    return line


def _join_shell_lines(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    joined: list[tuple[int, str]] = []
    pending = ""
    pending_line = 0
    for line_number, raw in lines:
        line = _strip_shell_comment(raw).rstrip()
        if not line and not pending:
            continue
        if not pending:
            pending_line = line_number
        if line.endswith("\\"):
            pending += line[:-1].rstrip() + " "
            continue
        pending += line
        if pending.strip():
            joined.append((pending_line, pending.strip()))
        pending = ""
    if pending.strip():
        joined.append((pending_line, pending.strip()))
    return joined


def _shell_functions(text: str) -> tuple[dict[str, list[tuple[int, str]]], list[tuple[int, str]]]:
    functions: dict[str, list[tuple[int, str]]] = {}
    top_level: list[tuple[int, str]] = []
    current_name: str | None = None
    current_body: list[tuple[int, str]] = []
    function_start = re.compile(r"^(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*\{\s*$")
    for line_number, raw in enumerate(text.splitlines(), start=1):
        stripped = _strip_shell_comment(raw).strip()
        if current_name is None:
            match = function_start.match(stripped)
            if match is not None:
                current_name = match.group(1)
                current_body = []
                continue
            top_level.append((line_number, raw))
            continue
        if stripped == "}":
            functions[current_name] = _join_shell_lines(current_body)
            current_name = None
            current_body = []
            continue
        current_body.append((line_number, raw))
    return functions, _join_shell_lines(top_level)


_SHELL_VARIABLE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_SHELL_ARRAY = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\[@\]\}$")


def _resolve_shell_value(value: str, scalars: dict[str, str]) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]

    def replacement(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        return scalars.get(name, f"<VAR:{name}>")

    previous = None
    for _ in range(8):
        if value == previous:
            break
        previous = value
        value = _SHELL_VARIABLE.sub(replacement, value)
    return value


def _parse_shell_words(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def _apply_shell_assignment(
    line: str,
    scalars: dict[str, str],
    arrays: dict[str, tuple[str, ...]],
    exports: set[str],
) -> bool:
    candidate = line.strip()
    exported = False
    local = False
    if candidate.startswith("export "):
        exported = True
        candidate = candidate[7:].strip()
    elif candidate.startswith("local "):
        local = True
        candidate = candidate[6:].strip()
    if exported and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", candidate):
        exports.add(candidate)
        return True
    array_match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=\((.*)\)", candidate)
    if array_match is not None:
        name, body = array_match.groups()
        words = tuple(_resolve_shell_value(word, scalars) for word in _parse_shell_words(body))
        arrays[name] = words
        if exported:
            exports.add(name)
        return True
    scalar_match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", candidate)
    if scalar_match is None:
        return False
    if len(_parse_shell_words(candidate)) != 1:
        # A leading NAME=value may be a command-local environment assignment,
        # e.g. ``PYTHONNOUSERSITE=1 "$PYTHON_BIN" ...``.  That is executable
        # choreography, not a persistent shell assignment.
        return False
    name, raw_value = scalar_match.groups()
    if name == "ROOT" and "BASH_SOURCE" in raw_value:
        value = "<ROOT>"
    elif name == "PYTHON_BIN" and "select_host_python" in raw_value:
        value = "<HOST_PYTHON>"
    elif "mktemp" in raw_value:
        value = "<TMP>"
    else:
        value = _resolve_shell_value(raw_value, scalars)
    scalars[name] = value
    if exported:
        exports.add(name)
    del local
    return True


def _expand_shell_command(
    line: str,
    scalars: dict[str, str],
    arrays: dict[str, tuple[str, ...]],
) -> tuple[list[str], dict[str, str]]:
    words = _parse_shell_words(line)
    expanded: list[str] = []
    command_environment: dict[str, str] = {}
    for word in words:
        array_match = _SHELL_ARRAY.fullmatch(word)
        if array_match is not None:
            expanded.extend(arrays.get(array_match.group(1), (word,)))
            continue
        expanded.append(_resolve_shell_value(word, scalars))
    while expanded and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", expanded[0]):
        name, value = expanded.pop(0).split("=", 1)
        command_environment[name] = value
    return expanded, command_environment


def _argument(argv: list[str] | tuple[str, ...], option: str) -> str | None:
    try:
        index = argv.index(option)
    except ValueError:
        return None
    if index + 1 >= len(argv):
        return None
    return argv[index + 1]


def _classify_hosted_action(argv: list[str]) -> str | None:
    if not argv:
        return None
    if argv[0] == "<HOST_PYTHON>" and "-I" in argv and "--output" in argv:
        return "capability"
    if (
        argv[0] == "<HOST_PYTHON>"
        and "-P" in argv
        and "--granularity" in argv
        and "--batch-size" in argv
        and "--workers" in argv
        and "--batch-timeout" in argv
        and "--report-path" in argv
    ):
        return "workload"
    if (
        argv[0] == "<HOST_PYTHON>"
        and "-P" in argv
        and "--host-capability" in argv
        and "--manifest" in argv
        and "--output" in argv
    ):
        return "receipt"
    if len(argv) >= 2 and argv[0] == "uv" and argv[1] in {"sync", "lock", "pip"}:
        return "acquisition"
    if len(argv) >= 2 and argv[0] in {"pip", "pip3"} and argv[1] == "install":
        return "acquisition"
    if len(argv) >= 2 and argv[0] == "npm" and argv[1] in {"install", "ci"}:
        return "acquisition"
    return None


def _scan_hosted_shell(
    text: str,
) -> tuple[list[_HostedDiagnosticAction], dict[str, str], dict[str, list[tuple[int, str]]]]:
    functions, top_level = _shell_functions(text)
    scalars: dict[str, str] = {"ROOT": "<ROOT>"}
    arrays: dict[str, tuple[str, ...]] = {}
    exports: set[str] = set()
    actions: list[_HostedDiagnosticAction] = []

    def scan_lines(
        lines: list[tuple[int, str]],
        order_override: int | None = None,
        inherited_scalars: dict[str, str] | None = None,
        inherited_arrays: dict[str, tuple[str, ...]] | None = None,
        inherited_exports: set[str] | None = None,
    ) -> None:
        local_scalars = scalars if inherited_scalars is None else inherited_scalars
        local_arrays = arrays if inherited_arrays is None else inherited_arrays
        local_exports = exports if inherited_exports is None else inherited_exports
        for sub_index, (line_number, line) in enumerate(lines):
            if _apply_shell_assignment(line, local_scalars, local_arrays, local_exports):
                continue
            argv, command_environment = _expand_shell_command(line, local_scalars, local_arrays)
            if not argv:
                continue
            command = argv[0]
            if command in functions:
                nested_scalars = dict(local_scalars)
                nested_arrays = dict(local_arrays)
                nested_exports = set(local_exports)
                scan_lines(
                    functions[command],
                    line_number if order_override is None else order_override,
                    nested_scalars,
                    nested_arrays,
                    nested_exports,
                )
                continue
            kind = _classify_hosted_action(argv)
            if kind is None:
                continue
            environment = {name: local_scalars.get(name, f"<VAR:{name}>") for name in local_exports}
            environment.update(command_environment)
            actions.append(
                _HostedDiagnosticAction(
                    order=(line_number if order_override is None else order_override, sub_index),
                    kind=kind,
                    argv=tuple(argv),
                    environment=environment,
                )
            )

    scan_lines(top_level)
    return sorted(actions, key=lambda action: action.order), dict(scalars), functions


def _surface_contract_violations(
    diagnostic: Path,
    functions: dict[str, list[tuple[int, str]]],
) -> list[Violation]:
    expected = {
        "backend-unit": "backend/tests/unit",
        "backend-integration": "backend/tests/integration",
        "repository-tooling": "scripts/tests",
    }
    lines = functions.get("surface_config", [])
    active = "\n".join(line for _, line in lines)
    violations: list[Violation] = []
    for surface, path in expected.items():
        pattern = rf"(?m)^\s*{re.escape(surface)}\)\s*[^\n]*\b{re.escape(path)}\b"
        if re.search(pattern, active) is None:
            violations.append(
                Violation(
                    diagnostic,
                    f"hosted diagnostic surface {surface} must select {path}",
                )
            )
    return violations


def _check_hosted_diagnostic_contract(diagnostic: Path) -> list[Violation]:
    if not diagnostic.is_file():
        return [Violation(diagnostic, "pre-dependency diagnostic runner is missing")]
    text = diagnostic.read_text(encoding="utf-8")
    actions, scalars, functions = _scan_hosted_shell(text)
    violations: list[Violation] = []

    if "diagnostic only" not in text.lower():
        violations.append(
            Violation(diagnostic, "hosted execution surface must remain diagnostic only")
        )
    violations.extend(_check_host_python_selector_contract(diagnostic.parent / "host_python.sh"))
    violations.extend(_surface_contract_violations(diagnostic, functions))

    base_marker = scalars.get("BASE_MARKER", "")
    required_exclusions = {
        "certification",
        "slow",
        "process",
        "scale",
        "host_dns",
        "dependency_complete",
    }
    exclusions = set(re.findall(r"\bnot\s+([A-Za-z_][A-Za-z0-9_]*)\b", base_marker))
    for missing in sorted(required_exclusions - exclusions):
        violations.append(
            Violation(diagnostic, f"hosted diagnostic marker exclusion is missing: {missing}")
        )

    acquisitions = [action for action in actions if action.kind == "acquisition"]
    for action in acquisitions:
        violations.append(
            Violation(
                diagnostic,
                "hosted diagnostic must not perform dependency acquisition: "
                + " ".join(action.argv[:3]),
            )
        )

    capabilities = [action for action in actions if action.kind == "capability"]
    workloads = [action for action in actions if action.kind == "workload"]
    receipts = [action for action in actions if action.kind == "receipt"]
    if not capabilities:
        violations.append(
            Violation(diagnostic, "hosted capability classification action is missing")
        )
    if not workloads:
        violations.append(
            Violation(diagnostic, "hosted dependency-light workload action is missing")
        )
    if not receipts:
        violations.append(Violation(diagnostic, "hosted diagnostic receipt action is missing"))

    if capabilities and workloads and capabilities[0].order >= workloads[0].order:
        violations.append(
            Violation(
                diagnostic,
                "hosted capability classification must complete before hosted workload execution",
            )
        )
    if receipts and workloads and receipts[-1].order <= workloads[-1].order:
        violations.append(
            Violation(
                diagnostic,
                "hosted diagnostic receipt must be produced after hosted workload execution",
            )
        )

    capability_output = _argument(capabilities[0].argv, "--output") if capabilities else None
    if capabilities:
        capability = capabilities[0]
        if capability.environment.get("PYTHONNOUSERSITE") != "1":
            violations.append(
                Violation(
                    diagnostic, "hosted capability classification must disable user-site imports"
                )
            )
        try:
            script_index = capability.argv.index("-I") + 1
            capability_script = capability.argv[script_index]
        except (ValueError, IndexError):
            capability_script = ""
        if capability_script.startswith("<ROOT>/"):
            capability_path = diagnostic.parent.parent / capability_script[len("<ROOT>/") :]
            if not capability_path.is_file():
                violations.append(
                    Violation(capability_path, "hosted dependency capability owner is missing")
                )

    for workload in workloads:
        if workload.environment.get("PYTHONPATH") != "<ROOT>/backend/src":
            violations.append(
                Violation(
                    diagnostic,
                    "hosted workload must replace PYTHONPATH with the repository backend/src source root; "
                    "ambient ${PYTHONPATH:+...} extension is forbidden",
                )
            )
        if workload.environment.get("PYTHONNOUSERSITE") != "1":
            violations.append(
                Violation(diagnostic, "hosted workload must disable user-site imports")
            )
        if workload.environment.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") != "1":
            violations.append(
                Violation(diagnostic, "hosted workload must disable ambient pytest plugin autoload")
            )
        marker = _argument(workload.argv, "--marker") or ""
        marker_exclusions = set(re.findall(r"\bnot\s+([A-Za-z_][A-Za-z0-9_]*)\b", marker))
        for missing in sorted(required_exclusions - marker_exclusions):
            violations.append(
                Violation(diagnostic, f"hosted workload marker exclusion is missing: {missing}")
            )
        workers = _argument(workload.argv, "--workers")
        if workers is None or not workers.isdigit() or int(workers) > 1:
            violations.append(
                Violation(diagnostic, "hosted workload worker bound must remain at most 1")
            )
        timeout = _argument(workload.argv, "--batch-timeout")
        if timeout is None or not timeout.isdigit() or int(timeout) > 60:
            violations.append(
                Violation(
                    diagnostic, "hosted workload timeout bound must remain at most 60 seconds"
                )
            )
        if "--no-timeout-bisection" not in workload.argv:
            violations.append(
                Violation(diagnostic, "hosted workload must keep timeout bisection disabled")
            )

    if capability_output is not None:
        for receipt in receipts:
            if _argument(receipt.argv, "--host-capability") != capability_output:
                violations.append(
                    Violation(
                        diagnostic,
                        "hosted diagnostic receipt must bind the classified host capability artifact",
                    )
                )

    active_text = "\n".join(
        line for _, line in _join_shell_lines(list(enumerate(text.splitlines(), start=1)))
    )
    for token in (
        "--system-site-packages",
        "RETAINED_HASHMARKS",
        "HASHMARKS_SITE",
        "zipfile -e",
        "--exclude-dependency",
    ):
        if token in active_text:
            violations.append(
                Violation(
                    diagnostic,
                    f"diagnostic runner must never become dependency/bootstrap authority: {token}",
                )
            )
    return violations


def _check_local_executor_control_state(root: Path) -> list[Violation]:
    """Local strict executor bookkeeping must never require system-wide /run ownership."""
    isolation = (
        root / "backend" / "src" / "oh_goon" / "integrations" / "fleet_plan_bubblewrap_isolation.py"
    )
    violations: list[Violation] = []
    if isolation.is_file():
        text = isolation.read_text(encoding="utf-8")
        for token in ("/run/oh-goon", "/run/lock/oh-goon"):
            if token in text:
                violations.append(
                    Violation(
                        isolation,
                        "local Bubblewrap locks/recovery metadata must use application-owned private state rather than system-wide /run",
                    )
                )
                break
    return violations


def _check_certification_storage_loss_boundary(root: Path) -> list[Violation]:
    """Keep storage ownership and fail-closed repair boundaries structural.

    Which private helper performs a probe or formats an error is behavioral and is
    covered by runtime tests.  Architecture only owns the module/frontdoor split,
    native materialization delegation, and the prohibition on host repair magic.
    """
    violations: list[Violation] = []
    pyproject = root / "backend" / "pyproject.toml"
    pyproject_text = pyproject.read_text(encoding="utf-8") if pyproject.is_file() else ""
    dependency_policy_source = dependency_policy_bootstrap_source_path(root, pyproject_text)
    owners = (
        root / "backend/src/oh_goon/services/certification_bootstrap.py",
        root / "backend/src/oh_goon/cli.py",
        root / "backend/src/oh_goon/workflow.py",
        root / "scripts/run_logged.sh",
        root / "backend/src/oh_goon/runtime/storage_health.py",
        root / "scripts/init_doctor.sh",
        *((dependency_policy_source,) if dependency_policy_source is not None else ()),
    )
    if dependency_policy_source is None:
        violations.append(
            Violation(
                pyproject,
                "pre-environment dependency bootstrap capability must map to one :main project-script source module",
            )
        )
    for path in owners:
        if not path.is_file():
            violations.append(
                Violation(path, "certification/bootstrap storage-loss owner is missing")
            )

    makefile = root / "Makefile"
    if makefile.is_file():
        make_text = makefile.read_text(encoding="utf-8")
        init_match = re.search(r"(?m)^init:(?P<prerequisites>[^\n]*)$", make_text)
        init_doctor_start = make_text.find("\ninit-doctor:\n")
        if init_match is None:
            violations.append(Violation(makefile, "make init bootstrap owner is missing"))
        else:
            init_start = init_match.start()
            init_end = make_text.find("\n\n", init_start + 1)
            if init_end < 0:
                init_end = len(make_text)
            init_body = make_text[init_start:init_end]
            if "uv sync --project backend --locked" not in init_body:
                violations.append(
                    Violation(
                        makefile,
                        "make init must delegate backend materialization directly to native uv locked sync so stale lock authority is rejected",
                    )
                )
            if "npm ci --prefer-offline" not in init_body:
                violations.append(
                    Violation(
                        makefile,
                        "make init must delegate frontend materialization directly to native npm ci",
                    )
                )
            if "INIT_DOCTOR" in init_body or "init_doctor.sh" in init_body:
                violations.append(
                    Violation(
                        makefile,
                        "make init must not preflight host storage; init-doctor is explicit diagnostic-only tooling",
                    )
                )
        if dependency_policy_source is not None:
            capability_path = dependency_policy_source.relative_to(root).as_posix()
            policy_match = re.search(
                r"(?m)^DEPENDENCY_POLICY\s*=\s*(?P<command>[^\n]+)$", make_text
            )
            policy_command = policy_match.group("command") if policy_match is not None else ""
            if capability_path not in policy_command:
                violations.append(
                    Violation(
                        makefile,
                        "Oh-Goon public-lock policy must project the explicit pre-environment dependency-policy capability separately from native uv semantics",
                    )
                )
        if (
            "INIT_DOCTOR = sh scripts/init_doctor.sh" not in make_text
            or init_doctor_start < 0
            or "$(INIT_DOCTOR)" not in make_text[init_doctor_start:]
        ):
            violations.append(
                Violation(
                    makefile,
                    "make init-doctor must expose the explicit diagnostic-only host-storage probe",
                )
            )
    else:
        violations.append(Violation(makefile, "make init/bootstrap diagnostic boundary is missing"))

    forbidden = ("mount -o remount,rw", "sudo mount", "chmod -R 777")
    for path in owners:
        if path.is_file() and any(token in path.read_text(encoding="utf-8") for token in forbidden):
            violations.append(
                Violation(
                    path,
                    "certification/bootstrap diagnostics must never repair host filesystem writability by remounting or permission broadening",
                )
            )
    return violations
