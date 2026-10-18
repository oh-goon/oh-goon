from pathlib import Path

from oh_goon.core.errors import FilesystemBrowseError


class FilesystemService:
    def browse(self, raw_path: str | None) -> tuple[Path, list[Path]]:
        try:
            path = self._resolve(raw_path)
            directories = sorted(
                (item for item in path.iterdir() if item.is_dir()),
                key=lambda item: item.name.casefold(),
            )
        except OSError as exc:
            raise FilesystemBrowseError(str(exc)) from exc
        return path, directories

    @staticmethod
    def _resolve(raw_path: str | None) -> Path:
        path = Path(raw_path).expanduser() if raw_path else Path.home()
        resolved = path.resolve()
        if not resolved.is_dir():
            raise FilesystemBrowseError(f"Not a directory: {resolved}")
        return resolved
