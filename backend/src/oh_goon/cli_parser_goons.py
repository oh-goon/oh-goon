from __future__ import annotations

import argparse
from pathlib import Path


def add_goon_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    goon = subparsers.add_parser(
        "goon",
        help="Create or migrate executable Goon capabilities",
    )
    commands = goon.add_subparsers(dest="goon_command", required=True)
    migrate = commands.add_parser(
        "import-gitlab",
        help="One-way import GitLab CI/components into one Atomic repository Goon",
    )
    migrate.add_argument("source", type=Path)
    migrate.add_argument("--output", type=Path, required=True)
    migrate.add_argument(
        "--expected-revision",
        help="Exact SHA-256 of the existing Goon output when replacing it",
    )
    migrate.add_argument(
        "--variable", action="append", dest="gitlab_variables", metavar="NAME=VALUE"
    )
    migrate.add_argument(
        "--token-env",
        help="Environment variable containing a GitLab private token used only during import",
    )
