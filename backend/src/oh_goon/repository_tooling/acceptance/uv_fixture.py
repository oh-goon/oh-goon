from __future__ import annotations


def editable_uv_lock(
    distribution_name: str, version: str, *, include_dev_group: bool = True
) -> str:
    metadata = (
        "\n[package.metadata]\n\n[package.metadata.requires-dev]\ndev = []\n"
        if include_dev_group
        else ""
    )
    return (
        'version = 1\nrevision = 3\nrequires-python = ">=3.11"\n\n'
        "[[package]]\n"
        f'name = "{distribution_name}"\n'
        f'version = "{version}"\n'
        'source = { editable = "." }\n'
        f"{metadata}"
    )


def editable_console_build_backend(
    *, distribution_name: str, module_name: str, command_name: str, version: str
) -> str:
    wheel_name = distribution_name.replace("-", "_")
    metadata_name = f"Name: {distribution_name}\n"
    entry_point = f"[console_scripts]\n{command_name} = {module_name}:main\n"
    return f"""from __future__ import annotations

import base64
import csv
import hashlib
import io
from pathlib import Path
import zipfile

NAME = {wheel_name!r}
VERSION = {version!r}
DIST_INFO = f"{{NAME}}-{{VERSION}}.dist-info"


def _digest(payload: bytes) -> tuple[str, str]:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
    return f"sha256={{encoded}}", str(len(payload))


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    files = {{
        {module_name + ".py"!r}: Path({module_name + ".py"!r}).read_bytes(),
        f"{{DIST_INFO}}/METADATA": (
            "Metadata-Version: 2.1\\n"
            {metadata_name!r}
            f"Version: {{VERSION}}\\n"
        ).encode(),
        f"{{DIST_INFO}}/WHEEL": (
            "Wheel-Version: 1.0\\n"
            "Generator: oh-goon-real-world-acceptance\\n"
            "Root-Is-Purelib: true\\n"
            "Tag: py3-none-any\\n"
        ).encode(),
        f"{{DIST_INFO}}/entry_points.txt": (
            {entry_point!r}
        ).encode(),
    }}
    rows = []
    for path, payload in files.items():
        digest, size = _digest(payload)
        rows.append((path, digest, size))
    rows.append((f"{{DIST_INFO}}/RECORD", "", ""))
    record = io.StringIO()
    csv.writer(record, lineterminator="\\n").writerows(rows)
    files[f"{{DIST_INFO}}/RECORD"] = record.getvalue().encode()
    name = f"{{NAME}}-{{VERSION}}-py3-none-any.whl"
    destination = Path(wheel_directory) / name
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, payload in files.items():
            archive.writestr(path, payload)
    return name


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    return build_wheel(wheel_directory, config_settings, metadata_directory)
"""
