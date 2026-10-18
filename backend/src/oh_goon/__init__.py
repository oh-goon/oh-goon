"""oh-goon application package."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _product_version() -> str:
    # Development/checkpoint execution is source-backed. VERSION is the product
    # identity owner, while pyproject/package-manager versions remain their own
    # release metadata. Installed distribution artifacts without a checkout VERSION
    # fall back to native package metadata.
    checkout_version = Path(__file__).resolve().parents[3] / "VERSION"
    try:
        if checkout_version.is_file() and not checkout_version.is_symlink():
            value = checkout_version.read_text(encoding="utf-8").strip()
            if value:
                return value
    except OSError:
        pass
    try:
        return version("oh-goon")
    except PackageNotFoundError:
        return "0.0.0"


__version__ = _product_version()
