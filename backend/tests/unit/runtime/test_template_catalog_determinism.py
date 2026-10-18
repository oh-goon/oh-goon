"""M139: exact catalog identity comes from the existing production stores."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanSourceNotFound
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore


def _package(root: Path, key: str, kind: str) -> Path:
    directory = root.joinpath(*key.split("/"))
    directory.mkdir(parents=True)
    (directory / "README.md").write_text(f"# {key}\n\nHuman description.\n", encoding="utf-8")
    source = directory / ("goon.yml" if kind == "Goon" else "plan.yml")
    source.write_bytes(
        b"version: 1\nsteps:\n- run: 'true'\n"
        if kind == "Goon"
        else b"version: 1\ngoons:\n- general/test\n"
    )
    return directory


@pytest.mark.parametrize(
    "kind,store_cls", [("Goon", GoonTemplateStore), ("Plan", PlanTemplateStore)]
)
def test_catalog_order_and_exact_identity_independent_of_creation_order(
    tmp_path: Path, kind: str, store_cls: type[GoonTemplateStore] | type[PlanTemplateStore]
) -> None:
    for key in ("python/z-last", "general/test", "python/a-first"):
        _package(tmp_path, key, kind)
    store = store_cls(tmp_path)
    expected = ("general/test", "python/a-first", "python/z-last")
    assert store.keys() == expected
    assert tuple(item.template_key for item in store.catalog().templates) == expected
    assert store.catalog() == store.catalog()
    for summary in store.catalog().templates:
        document = store.read(summary.template_key)
        snapshot = store.source(summary.template_key)
        assert summary.sha256 == document.sha256 == snapshot.sha256
        assert summary.sha256 == hashlib.sha256(snapshot.path.read_bytes()).hexdigest()
        assert document.content.encode("utf-8") == snapshot.path.read_bytes()
        assert summary.template_path == document.template_path


def test_goon_source_mutation_changes_identity_but_readme_does_not(tmp_path: Path) -> None:
    directory = _package(tmp_path, "general/test", "Goon")
    store = GoonTemplateStore(tmp_path)
    before = store.catalog().templates[0]
    (directory / "README.md").write_text("# Different explanation\n", encoding="utf-8")
    after_readme = store.catalog().templates[0]
    assert after_readme.sha256 == before.sha256
    assert after_readme.description != before.description

    path = directory / "goon.yml"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    after_source = store.catalog().templates[0]
    assert after_source.sha256 != before.sha256
    assert after_source.sha256 == store.read("general/test").sha256


@pytest.mark.parametrize(
    "kind,store_cls", [("Goon", GoonTemplateStore), ("Plan", PlanTemplateStore)]
)
def test_ambiguous_case_alias_fails_discovery_not_silently_selected(
    tmp_path: Path, kind: str, store_cls: type[GoonTemplateStore] | type[PlanTemplateStore]
) -> None:
    _package(tmp_path, "general/test", kind)
    _package(tmp_path, "general/Test", kind)
    with pytest.raises(FleetPlanSourceNotFound, match="lowercase"):
        store_cls(tmp_path).catalog()
    with pytest.raises(FleetPlanSourceNotFound, match="category/name"):
        store_cls(tmp_path).read("general/TEST")


def test_catalog_rejects_symlink_package_instead_of_following_alias(tmp_path: Path) -> None:
    _package(tmp_path, "general/test", "Goon")
    (tmp_path / "general" / "alias").symlink_to(
        tmp_path / "general" / "test", target_is_directory=True
    )
    with pytest.raises(FleetPlanSourceNotFound, match="package directories"):
        GoonTemplateStore(tmp_path).catalog()


def test_shipped_goon_catalog_exposes_byte_identity_for_every_entry() -> None:
    store = GoonTemplateStore()
    catalog = store.catalog()
    assert tuple(item.template_key for item in catalog.templates) == store.keys()
    assert len(catalog.templates) >= 40
    for item in catalog.templates:
        assert item.sha256 == store.source(item.template_key).sha256
        assert item.sha256 == store.read(item.template_key).sha256
