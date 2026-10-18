from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles


def built_ui_path() -> Path:
    return Path(__file__).resolve().parents[1] / "static"


def built_ui_available() -> bool:
    return (built_ui_path() / "index.html").is_file()


def mount_built_ui(app: FastAPI) -> None:
    static_dir = built_ui_path()
    if built_ui_available():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="oh-goon-ui")
