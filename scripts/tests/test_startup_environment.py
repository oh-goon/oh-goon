from __future__ import annotations

import json
import subprocess
from pathlib import Path


from oh_goon.repository_tooling import startup_environment


class FakeRunner:
    def __init__(self, versions: dict[str, str]) -> None:
        self.versions = versions
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv, **_kwargs):
        command = tuple(argv)
        self.calls.append(command)
        name = command[0]
        if name not in self.versions:
            raise FileNotFoundError(name)
        return subprocess.CompletedProcess(
            command, 0, stdout=self.versions[name] + "\n", stderr=""
        )


def _root(tmp_path: Path) -> Path:
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "backend/pyproject.toml").write_text(
        '[project]\nrequires-python=">=3.12"\n[tool.uv]\nrequired-version=">=0.10,<0.13"\n',
        encoding="utf-8",
    )
    (tmp_path / "frontend/package.json").write_text(
        json.dumps(
            {
                "engines": {"node": ">=22.16.0", "npm": ">=10.9.2"},
                "devDependencies": {"vite": ">=8.1.5"},
                "packageManager": "npm@10.9.2",
            }
        ),
        encoding="utf-8",
    )
    return tmp_path




def test_startup_checks_only_standard_tool_boundaries(tmp_path: Path) -> None:
    runner = FakeRunner({"uv": "uv 0.12.5", "node": "v22.16.0", "npm": "10.9.2"})
    rows = startup_environment.startup_report(_root(tmp_path), runner=runner)
    assert all(row[2] for row in rows)
    python_row = next(row for row in rows if row[0] == "Python")
    assert python_row == (
        "Python",
        "uv-managed",
        True,
        "native uv owns requires-python >=3.12",
    )
    assert runner.calls == [
        ("uv", "--version"),
        ("node", "--version"),
        ("npm", "--version"),
    ]
    assert not any(call[0].startswith("python") for call in runner.calls)



def test_startup_does_not_reimplement_uv_required_version_semantics(tmp_path: Path) -> None:
    root = _root(tmp_path)
    for version in ("uv 0.9.99", "uv 0.12.5", "uv 0.13.0"):
        rows = startup_environment.startup_report(
            root,
            runner=FakeRunner({"uv": version}),
            components=("uv",),
        )
        uv_row = next(row for row in rows if row[0] == "uv")
        assert uv_row == (
            "uv",
            version,
            True,
            "native tool owns backend/pyproject.toml [tool.uv].required-version >=0.10,<0.13",
        )


def test_startup_reports_uv_requirement_without_parsing_its_shape(tmp_path: Path) -> None:
    root = _root(tmp_path)
    pyproject = root / "backend/pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            'required-version=">=0.10,<0.13"',
            'required-version=">=0.10,!=0.12.7,<0.13"',
        ),
        encoding="utf-8",
    )
    rows = startup_environment.startup_report(
        root,
        runner=FakeRunner({"uv": "uv 0.12.5"}),
        components=("uv",),
    )
    assert rows[0][2] is True
    assert ">=0.10,!=0.12.7,<0.13" in rows[0][3]

def test_startup_reports_python_selected_by_uv_without_second_version_check(tmp_path: Path) -> None:
    runner = FakeRunner({"uv": "uv 0.12.5", "node": "v22.16.0", "npm": "10.9.2"})
    rows = startup_environment.startup_report(
        _root(tmp_path), runner=runner, resolved_python_version="3.13.5"
    )
    python_row = next(row for row in rows if row[0] == "Python")
    assert python_row == (
        "Python",
        "3.13.5",
        True,
        "native uv owns requires-python >=3.12",
    )


def test_startup_fails_only_when_required_native_tool_is_missing(tmp_path: Path) -> None:
    runner = FakeRunner({"uv": "uv 0.9.9", "node": "v22.12.0"})
    rows = startup_environment.startup_report(_root(tmp_path), runner=runner)
    status = {name: ok for name, _version, ok, _detail in rows}
    assert status["uv"] is True
    assert status["node"] is True
    assert status["npm"] is False


def test_startup_reports_npm_engine_declaration_without_reimplementing_it(tmp_path: Path) -> None:
    runner = FakeRunner({"uv": "uv 0.12.5", "node": "v22.16.0", "npm": "10.9.1"})
    rows = startup_environment.startup_report(_root(tmp_path), runner=runner)
    npm_row = next(row for row in rows if row[0] == "npm")
    assert npm_row == (
        "npm",
        "10.9.1",
        True,
        "native tool owns frontend/package.json engines.npm >=10.9.2",
    )


def test_make_and_frontend_tooling_do_not_invoke_host_python_names() -> None:
    root = Path(__file__).resolve().parents[2]
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    dev = (root / "scripts/dev.sh").read_text(encoding="utf-8")
    release = (root / "scripts/release/build_release.sh").read_text(encoding="utf-8")
    package = json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))
    surfaces = [makefile, dev, release, "\n".join(package["scripts"].values())]
    for text in surfaces:
        for line in text.splitlines():
            stripped = line.strip()
            import re

            if re.search(r"(^|[;&|]\s*|\t)python(?:3(?:\.\d+)?)?\s", stripped) is None:
                continue
            assert "uv run" in stripped or "uv pip install --python" in stripped


def test_init_is_consumer_only_and_diagnostics_are_explicit() -> None:
    root = Path(__file__).resolve().parents[2]
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    init = makefile.split("init:", 1)[1].split("\n\n", 1)[0]
    dev = makefile.split("\ndev:", 1)[1].split("\n\n", 1)[0]
    assert "uv sync --project backend --locked" in init
    assert "npm ci --prefer-offline --ignore-scripts --no-audit --no-fund" in init
    assert "diagnostic-startup-check:" in makefile
    assert "diagnostic-dependency-check:" in makefile
    assert "backend-lock-check:" in makefile
    assert "diagnostic-system-check:" in makefile
    assert "\nstartup-check:" not in makefile
    assert "\ndependency-check:" not in makefile
    assert "\ndiagnostic-backend-lock-convergence-check:" not in makefile
    assert "\nsystem-check:" not in makefile
    assert "\nsetup-lock-check:" not in makefile
    assert dev.strip() == "bash scripts/dev.sh"
    assert "certification-dependencies-" not in init
    assert "CERTIFICATION_" not in init
    assert "NETWORK_RETRY_TOOL" not in init
    assert "browser:install" not in init
    assert "USE_CERTIFICATION_CACHE" not in makefile
    assert "UV_CONCURRENT_DOWNLOADS" not in makefile


def test_python_request_tracks_declared_python_minimum() -> None:
    root = Path(__file__).resolve().parents[2]
    python_spec, _ = startup_environment.backend_requirements(root)
    minimum = python_spec.split(",", 1)[0].strip()
    assert minimum.startswith(">=")
    assert (root / "backend/.python-version").read_text(
        encoding="utf-8"
    ).strip() == minimum


def test_ecosystem_startup_checks_do_not_probe_unrelated_tools(tmp_path: Path) -> None:
    root = _root(tmp_path)
    backend_runner = FakeRunner({"uv": "uv 0.12.5"})
    backend = startup_environment.startup_report(
        root,
        runner=backend_runner,
        resolved_python_version="3.13.5",
        components=("Python", "uv"),
    )
    assert {row[0] for row in backend} == {"Python", "uv"}
    assert backend_runner.calls == [("uv", "--version")]

    frontend_runner = FakeRunner({"node": "v22.16.0", "npm": "10.9.2"})
    frontend = startup_environment.startup_report(
        root,
        runner=frontend_runner,
        components=("node", "npm"),
    )
    assert {row[0] for row in frontend} == {"node", "npm"}
    assert frontend_runner.calls == [("node", "--version"), ("npm", "--version")]


def test_backend_component_check_does_not_read_frontend_manifest(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "frontend/package.json").unlink()
    runner = FakeRunner({"uv": "uv 0.12.5"})

    rows = startup_environment.startup_report(
        root,
        runner=runner,
        resolved_python_version="3.13.5",
        components=("Python", "uv"),
    )

    assert all(row[2] for row in rows)
    assert runner.calls == [("uv", "--version")]


def test_frontend_component_check_does_not_read_backend_manifest(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "backend/pyproject.toml").unlink()
    runner = FakeRunner({"node": "v22.16.0", "npm": "10.9.2"})

    rows = startup_environment.startup_report(
        root,
        runner=runner,
        components=("node", "npm"),
    )

    assert all(row[2] for row in rows)
    assert runner.calls == [("node", "--version"), ("npm", "--version")]


def test_cli_component_filter_supports_bootstrap_only_check(monkeypatch, tmp_path) -> None:
    root = _root(tmp_path)
    monkeypatch.setattr(startup_environment, "ROOT", root)
    seen: list[tuple[str, ...] | None] = []

    def fake_report(_root_arg, *, components=None, **_kwargs):
        seen.append(components)
        return (("uv", "uv 0.12.5", True, "requires >=0.10,<0.13"),)

    monkeypatch.setattr(startup_environment, "startup_report", fake_report)
    assert startup_environment.main(["check", "uv"]) == 0
    assert seen == [("uv",)]
