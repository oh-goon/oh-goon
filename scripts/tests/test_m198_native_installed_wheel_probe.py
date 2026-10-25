"""M198: installed-wheel issuer attestation is fail-closed and alias preserving."""

from __future__ import annotations

import ast
from collections.abc import Callable
from importlib.metadata import Distribution
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from oh_goon.repository_tooling import native_issuer_wheel_probe as probe


class FakeDistribution:
    version = "1267.0.1052"

    def __init__(self, root: Path, *, missing: str = "") -> None:
        self.root = root
        members = ["oh_goon/native_issuer/__init__.py"] + [
            f"oh_goon/native_issuer/{name}.py" for name in probe.ISSUER_MODULES
        ]
        self.files = tuple(Path(part) for part in members if part != missing)
        for part in self.files:
            target = root / part
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# dummy installed owner\n", encoding="utf-8")

    def locate_file(self, path: str) -> Path:
        return self.root / path


def _imports(
    root: Path, *, different_alias: str = "", forged_origin: str = ""
) -> Callable[[str], SimpleNamespace]:
    owners = {
        name: SimpleNamespace(
            __file__=str(root / "oh_goon/native_issuer" / f"{name}.py")
        )
        for name in probe.ISSUER_MODULES
    }

    def load(name: str) -> SimpleNamespace:
        module = name.rsplit(".", 1)[-1]
        if name.startswith("oh_goon.native_issuer."):
            if module == forged_origin:
                return SimpleNamespace(__file__=str(root / "wrong-source.py"))
            return owners[module]
        if module == different_alias:
            return SimpleNamespace(__file__=owners[module].__file__)
        return owners[module]

    return load


def test_exact_ten_installed_owners_and_aliases_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dist = FakeDistribution(tmp_path)
    monkeypatch.setattr(probe, "import_module", _imports(tmp_path))
    report = probe.prove_wheel_owner(
        distribution=cast(Distribution, dist), expected_version="1267.0.1052"
    )
    assert report["issuer_count"] == 10
    assert report["installed_wheel_import_identity"] == "PASS"
    assert report["authenticated_replay_authority"] == "NOT_ESTABLISHED"
    assert report["fleet_enforcer_provisioning"] == "NOT_ESTABLISHED"


@pytest.mark.parametrize(
    "missing",
    [
        "oh_goon/native_issuer/__init__.py",
        "oh_goon/native_issuer/enforcer_lace_signer_custody.py",
    ],
)
def test_missing_installed_owner_is_not_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing: str
) -> None:
    dist = FakeDistribution(tmp_path, missing=missing)
    monkeypatch.setattr(probe, "import_module", _imports(tmp_path))
    with pytest.raises(probe.WheelAuthorityFailure, match="missing"):
        probe.prove_wheel_owner(
            distribution=cast(Distribution, dist), expected_version="1267.0.1052"
        )


def test_wrong_installed_version_fails_before_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dist = FakeDistribution(tmp_path)
    monkeypatch.setattr(probe, "import_module", lambda name: pytest.fail("no imports"))
    with pytest.raises(probe.WheelAuthorityFailure, match="version"):
        probe.prove_wheel_owner(
            distribution=cast(Distribution, dist), expected_version="1267.0.9999"
        )


@pytest.mark.parametrize("attack", ["alias", "source"])
def test_competing_alias_or_source_overlay_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, attack: str
) -> None:
    dist = FakeDistribution(tmp_path)
    name = "enforcer_lace_native_issuer_suite"
    monkeypatch.setattr(
        probe,
        "import_module",
        _imports(
            tmp_path,
            different_alias=name if attack == "alias" else "",
            forged_origin=name if attack == "source" else "",
        ),
    )
    with pytest.raises(probe.WheelAuthorityFailure, match="origin"):
        probe.prove_wheel_owner(
            distribution=cast(Distribution, dist), expected_version="1267.0.1052"
        )


def test_missing_wheel_metadata_cannot_count_as_positive(tmp_path: Path) -> None:
    class FakeNoRecord:
        version = "1267.0.1052"
        files = None

    with pytest.raises(probe.WheelAuthorityFailure, match="RECORD"):
        probe.prove_wheel_owner(
            distribution=cast(Distribution, FakeNoRecord()),
            expected_version="1267.0.1052",
        )


def test_explicit_fleet_provisioner_cannot_be_inferred_from_wheel() -> None:
    from dataclasses import MISSING, fields

    from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner

    required = {
        part.name for part in fields(FleetPreworkProvisioner) if part.default is MISSING
    }
    assert required == {
        "enrolled",
        "executor_inventory_file",
        "trusted_key_directory",
        "issuer_references",
    }


def test_native_wheel_qualification_script_never_reuses_checkout_imports() -> None:
    """M201: a dependency-free target install is safe only with native lock authority.

    --no-deps is deliberate: `make sync` has already installed the project lock
    in backend/.venv, and a second offline resolution would require unavailable
    cached registry wheels. Wheel ownership is proven independently by RECORD
    and real module origins, not by the package being importable somewhere.
    """
    root = Path(__file__).resolve().parents[2]
    source = (root / "scripts/qualify_native_issuer_wheel.sh").read_text(
        encoding="utf-8"
    )
    probe_source = (
        root / "backend/src/oh_goon/repository_tooling/native_issuer_wheel_probe.py"
    ).read_text(encoding="utf-8")

    assert '[[ ! -x "$ROOT/backend/.venv/bin/python" ]]' in source
    assert "oh-goon-release-wheel-build --offline" in source
    assert (
        'uv pip install --offline --no-deps --target "$WORK/wheel-site" "${wheels[0]}"'
        in source
    )
    assert 'cd "$WORK/isolated"' in source
    assert "unset PYTHONPATH PYTHONHOME" in source
    assert (
        'uv run --no-project --offline --python "$ROOT/backend/.venv/bin/python"'
        in source
    )
    assert "python -I -c" in source
    assert "sys.path.insert(0, str(wheel_site))" in source
    assert '"$WORK/wheel-site" "$VERSION"' in source
    assert "--expected-version" in source
    assert "export PYTHONPATH" not in source
    assert "--editable" not in source
    assert "--system" not in source
    assert 'uv pip install --offline "${wheels[0]}"' not in source
    assert '"$WORK/venv/bin/oh-goon-native-issuer-wheel-probe"' not in source

    # This probe, not --no-deps, owns the installed-wheel positive decision.
    assert 'distribution=metadata.distribution("oh-goon")' in probe_source
    assert "members = distribution.files" in probe_source
    origin_assignments = [
        node.value
        for node in ast.walk(ast.parse(probe_source))
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "resolve"
        and isinstance(node.value.func.value, ast.Call)
        and isinstance(node.value.func.value.func, ast.Name)
        and node.value.func.value.func.id == "Path"
    ]
    installed_origin = next(
        value
        for value in origin_assignments
        if any(
            keyword.arg == "strict"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in value.keywords
        )
    )
    assert isinstance(installed_origin.func, ast.Attribute)
    assert isinstance(installed_origin.func.value, ast.Call)
    assert len(installed_origin.func.value.args) == 1
    located = installed_origin.func.value.args[0]
    if (
        isinstance(located, ast.Call)
        and isinstance(located.func, ast.Name)
        and located.func.id == "str"
    ):
        assert len(located.args) == 1 and not located.keywords
        located = located.args[0]
    assert ast.dump(located) == ast.dump(
        ast.parse("distribution.locate_file(relative)", mode="eval").body
    )
    assert "actual != path or compatibility is not owner" in probe_source
    assert '"authenticated_replay_authority": "NOT_ESTABLISHED"' in probe_source
    assert '"fleet_enforcer_provisioning": "NOT_ESTABLISHED"' in probe_source
