"""Project-owned preparation and local artifact export for the dataflow example."""

from __future__ import annotations

import argparse
import os
import re
import shutil
from pathlib import Path


def release_label(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value) is None:
        raise ValueError("release label must be a bounded filename component")
    return value


def prepare() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True, type=release_label)
    args = parser.parse_args()
    output = Path(".fleet")
    output.mkdir(exist_ok=True)
    (output / "release-tag.txt").write_text(args.tag + "\n", encoding="utf-8")
    return 0


def publish() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag", type=release_label)
    args = parser.parse_args()
    if not os.environ.get("RELEASE_TOKEN"):
        parser.error("RELEASE_TOKEN must be explicitly supplied")
    wheel = tuple(Path("dist").glob("*.whl"))
    source = tuple(Path("dist").glob("*.tar.gz"))
    if len(wheel) != 1 or len(source) != 1:
        parser.error("local export requires exactly one built wheel and source archive")
    destination = Path("published") / args.tag
    destination.mkdir(parents=True)
    for artifact in (*wheel, *source):
        shutil.copyfile(artifact, destination / artifact.name)
    (destination / "RELEASE_TAG").write_text(args.tag + "\n", encoding="utf-8")
    return 0
