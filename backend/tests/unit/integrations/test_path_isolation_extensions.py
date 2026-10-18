import os
import subprocess
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_resources import ExtensionCatalog, ExtensionDeclaration
from oh_goon.engine.fleet_extensions import FleetExtensionStore
from tests.factories.path_authority import TEST_PATH_AUTHORITY


def test_local_extension_source_cannot_escape_catalog_root(tmp_path: Path) -> None:
    catalog_root = tmp_path / "repo-a"
    sibling = tmp_path / "repo-b"
    catalog_root.mkdir()
    sibling.mkdir()
    store = FleetExtensionStore(
        tmp_path / "fleet-data" / "extensions", path_authority=TEST_PATH_AUTHORITY
    )
    catalog = ExtensionCatalog(
        extensions=(
            ExtensionDeclaration(
                name="sibling",
                source="../repo-b",
                revision="a" * 40,
                commands=("quality",),
            ),
        )
    )

    with pytest.raises(FleetPlanInvalid, match="traversal|escapes repository boundary"):
        store.sync_catalog(catalog, catalog_root=catalog_root)


def test_remote_extension_acquisition_uses_native_user_git_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.engine import fleet_extensions as module

    source = tmp_path / "native-source"
    source.mkdir()

    def git(*args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(tmp_path / "bootstrap-home"),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_AUTHOR_NAME": "Oh-Goon Test",
                "GIT_AUTHOR_EMAIL": "test@example.invalid",
                "GIT_COMMITTER_NAME": "Oh-Goon Test",
                "GIT_COMMITTER_EMAIL": "test@example.invalid",
            },
        )
        return result.stdout.strip()

    git("init", "--quiet", str(source))
    (source / "pyproject.toml").write_text(
        '[project]\nname = "native-git-extension"\nversion = "0.1.0"\n'
        '[project.scripts]\nquality = "quality:main"\n',
        encoding="utf-8",
    )
    (source / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    git("add", ".", cwd=source)
    git("commit", "--quiet", "-m", "extension", cwd=source)
    revision = git("rev-parse", "HEAD", cwd=source)

    native_home = tmp_path / "native-home"
    native_home.mkdir()
    source_url = source.resolve().as_uri()
    (native_home / ".gitconfig").write_text(
        '[protocol "file"]\n\tallow = always\n'
        f'[url "{source_url}"]\n\tinsteadOf = https://private.example.invalid/quality.git\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(native_home))
    monkeypatch.setenv("USERPROFILE", str(native_home))
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.setattr(module, "_ensure_environment", lambda *_args, **_kwargs: None)

    store = FleetExtensionStore(
        tmp_path / "fleet-data" / "extensions", path_authority=TEST_PATH_AUTHORITY
    )
    declaration = ExtensionDeclaration(
        name="quality",
        source="https://private.example.invalid/quality.git",
        revision=revision,
        commands=("quality",),
    )

    managed = store.sync(declaration, catalog_root=tmp_path)

    assert managed.revision == revision
    assert managed.source == "https://private.example.invalid/quality.git"
    assert managed.project_path.is_dir()


def test_remote_extension_transport_and_checkout_use_distinct_authorities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.core.models.fleet_plan import ManagedExtension
    from oh_goon.engine import fleet_extensions as module
    from oh_goon.engine.command import CommandResult

    declaration = ExtensionDeclaration(
        name="quality",
        source="https://private.example.invalid/quality.git",
        revision="a" * 40,
        commands=("quality",),
    )
    observed: list[tuple[tuple[str, ...], dict[str, str]]] = []

    class FakeGit:
        def run(self, *argv: str, **kwargs: object) -> CommandResult:
            environment = dict(kwargs["env"])  # type: ignore[arg-type]
            observed.append((tuple(argv), environment))
            if "clone" in argv:
                Path(argv[-1]).mkdir()
            return CommandResult(returncode=0, stdout="", stderr="", argv=tuple(argv))

    def validate(
        _self: FleetExtensionStore,
        project_path: Path,
        _declaration: ExtensionDeclaration,
        source: str,
        extension_id: str,
    ) -> ManagedExtension:
        return ManagedExtension(
            extension_id=extension_id,
            source=source,
            revision=declaration.revision,
            project_name="quality",
            project_path=project_path,
            project_fingerprint="fingerprint",
            pyproject_sha256="b" * 64,
            uv_lock_sha256="c" * 64,
            scripts=("quality",),
        )

    monkeypatch.setenv("HOME", "/home/native-operator")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/run/native-ssh-agent.sock")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/home/native-operator/.gitconfig")
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)
    monkeypatch.setattr(module, "_git_service", lambda _authority: FakeGit())
    monkeypatch.setattr(FleetExtensionStore, "_validate_checkout", validate)
    monkeypatch.setattr(module, "_ensure_environment", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "sync_prepared_path", lambda _path: None)
    monkeypatch.setattr(
        module,
        "publish_prepared_directory",
        lambda staging, destination: staging.rename(destination),
    )

    store = FleetExtensionStore(
        tmp_path / "fleet-data" / "extensions", path_authority=TEST_PATH_AUTHORITY
    )
    store.sync(declaration, catalog_root=tmp_path)

    assert observed[0][0][:5] == (
        "-c",
        "core.hooksPath=/dev/null",
        "clone",
        "--no-checkout",
        "--template=",
    )
    assert observed[0][1]["HOME"] == "/home/native-operator"
    assert observed[0][1]["SSH_AUTH_SOCK"] == "/run/native-ssh-agent.sock"
    assert observed[0][1]["GIT_CONFIG_GLOBAL"] == "/home/native-operator/.gitconfig"
    assert observed[0][1]["GIT_TERMINAL_PROMPT"] == "0"
    assert "GIT_CONFIG_NOSYSTEM" not in observed[0][1]

    assert observed[1][0][:4] == ("-c", "core.hooksPath=/dev/null", "checkout", "--detach")
    assert observed[1][1]["HOME"] != "/home/native-operator"
    assert "SSH_AUTH_SOCK" not in observed[1][1]
    assert "GIT_CONFIG_GLOBAL" not in observed[1][1]
    assert observed[1][1]["GIT_TERMINAL_PROMPT"] == "0"
    assert observed[1][1]["GIT_CONFIG_NOSYSTEM"] == "1"


def test_remote_extension_native_hooks_cannot_run_during_fleet_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.engine import fleet_extensions as module

    source = tmp_path / "hook-source"
    source.mkdir()
    bootstrap_home = tmp_path / "bootstrap-home"
    bootstrap_home.mkdir()

    def git(*args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(bootstrap_home),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_AUTHOR_NAME": "Oh-Goon Test",
                "GIT_AUTHOR_EMAIL": "test@example.invalid",
                "GIT_COMMITTER_NAME": "Oh-Goon Test",
                "GIT_COMMITTER_EMAIL": "test@example.invalid",
            },
        )
        return result.stdout.strip()

    git("init", "--quiet", str(source))
    (source / "pyproject.toml").write_text(
        '[project]\nname = "hook-extension"\nversion = "0.1.0"\n'
        '[project.scripts]\nquality = "quality:main"\n',
        encoding="utf-8",
    )
    (source / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    git("add", ".", cwd=source)
    git("commit", "--quiet", "-m", "extension", cwd=source)
    revision = git("rev-parse", "HEAD", cwd=source)

    transport_marker = tmp_path / "reference-transaction-ran"
    checkout_marker = tmp_path / "post-checkout-ran"
    hooks = tmp_path / "native-hooks"
    hooks.mkdir()
    transport_hook = hooks / "reference-transaction"
    transport_hook.write_text(
        f'#!/bin/sh\nprintf hook > "{transport_marker}"\ncat >/dev/null\n',
        encoding="utf-8",
    )
    transport_hook.chmod(0o755)
    checkout_hook = hooks / "post-checkout"
    checkout_hook.write_text(f'#!/bin/sh\nprintf hook > "{checkout_marker}"\n', encoding="utf-8")
    checkout_hook.chmod(0o755)

    template = tmp_path / "native-template"
    template_hooks = template / "hooks"
    template_hooks.mkdir(parents=True)
    template_hook = template_hooks / "post-checkout"
    template_hook.write_text(
        f'#!/bin/sh\nprintf template > "{checkout_marker}"\n', encoding="utf-8"
    )
    template_hook.chmod(0o755)

    native_home = tmp_path / "native-home"
    native_home.mkdir()
    source_url = source.resolve().as_uri()
    (native_home / ".gitconfig").write_text(
        '[protocol "file"]\n\tallow = always\n'
        f'[url "{source_url}"]\n\tinsteadOf = https://private.example.invalid/hooks.git\n'
        f"[core]\n\thooksPath = {hooks}\n"
        f"[init]\n\ttemplateDir = {template}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(native_home))
    monkeypatch.setenv("USERPROFILE", str(native_home))
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(template))
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.setattr(module, "_ensure_environment", lambda *_args, **_kwargs: None)

    store = FleetExtensionStore(
        tmp_path / "fleet-data" / "extensions", path_authority=TEST_PATH_AUTHORITY
    )
    managed = store.sync(
        ExtensionDeclaration(
            name="hooks",
            source="https://private.example.invalid/hooks.git",
            revision=revision,
            commands=("quality",),
        ),
        catalog_root=tmp_path,
    )

    assert managed.revision == revision
    assert not transport_marker.exists()
    assert not checkout_marker.exists()
    assert not (managed.project_path / ".git" / "hooks" / "post-checkout").exists()


def test_remote_extension_ambient_repository_state_cannot_redirect_fleet_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.engine import fleet_extensions as module

    source = tmp_path / "redirect-source"
    source.mkdir()
    bootstrap_home = tmp_path / "bootstrap-home"
    bootstrap_home.mkdir()

    def git(*args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(bootstrap_home),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_AUTHOR_NAME": "Oh-Goon Test",
                "GIT_AUTHOR_EMAIL": "test@example.invalid",
                "GIT_COMMITTER_NAME": "Oh-Goon Test",
                "GIT_COMMITTER_EMAIL": "test@example.invalid",
            },
        )
        return result.stdout.strip()

    git("init", "--quiet", str(source))
    (source / "pyproject.toml").write_text(
        '[project]\nname = "redirect-extension"\nversion = "0.1.0"\n'
        '[project.scripts]\nquality = "quality:main"\n',
        encoding="utf-8",
    )
    (source / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (source / "payload.txt").write_text("one\n", encoding="utf-8")
    git("add", ".", cwd=source)
    git("commit", "--quiet", "-m", "one", cwd=source)
    admitted_revision = git("rev-parse", "HEAD", cwd=source)
    (source / "payload.txt").write_text("two\n", encoding="utf-8")
    git("commit", "--quiet", "-am", "two", cwd=source)
    newest_revision = git("rev-parse", "HEAD", cwd=source)

    foreign = tmp_path / "foreign"
    git("clone", "--quiet", str(source), str(foreign))
    git("checkout", "--quiet", "--detach", newest_revision, cwd=foreign)
    foreign_before = git("rev-parse", "HEAD", cwd=foreign)

    native_home = tmp_path / "native-home"
    native_home.mkdir()
    monkeypatch.setenv("HOME", str(native_home))
    monkeypatch.setenv("USERPROFILE", str(native_home))
    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(foreign))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "foreign.index"))
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)
    monkeypatch.setattr(module, "_ensure_environment", lambda *_args, **_kwargs: None)

    store = FleetExtensionStore(
        tmp_path / "fleet-data" / "extensions", path_authority=TEST_PATH_AUTHORITY
    )
    managed = store.sync(
        ExtensionDeclaration(
            name="redirect",
            source="redirect-source",
            revision=admitted_revision,
            commands=("quality",),
        ),
        catalog_root=tmp_path,
    )

    assert managed.revision == admitted_revision
    assert git("rev-parse", "HEAD", cwd=managed.project_path) == admitted_revision
    assert git("rev-parse", "HEAD", cwd=foreign) == foreign_before
