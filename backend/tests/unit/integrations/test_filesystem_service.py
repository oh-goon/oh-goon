from pathlib import Path

from oh_goon.services.filesystem import FilesystemService


def test_browse_lists_directories(tmp_path: Path) -> None:
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")

    current, directories = FilesystemService().browse(str(tmp_path))

    assert current == tmp_path.resolve()
    assert [path.name for path in directories] == ["alpha", "beta"]
