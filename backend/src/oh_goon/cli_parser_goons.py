from __future__ import annotations

import argparse
from pathlib import Path


def add_goon_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    templates = subparsers.add_parser(
        "templates", help="Browse shipped Plans and Goons without runtime setup"
    )
    template_commands = templates.add_subparsers(dest="template_command", required=True)
    listing = template_commands.add_parser(
        "list", help="List canonical template identities and descriptions"
    )
    listing.add_argument("--kind", choices=("plan", "goon", "all"), default="all")
    listing.add_argument("--json", action="store_true", dest="as_json")
    show = template_commands.add_parser(
        "show", help="Print the exact template YAML for copying or inspection"
    )
    show.add_argument("key")
    show.add_argument("--kind", choices=("plan", "goon"), required=True)
    init = template_commands.add_parser(
        "init", help="Create a minimal workflow selecting a shipped Plan or Goon"
    )
    init.add_argument("key")
    init.add_argument("--kind", choices=("plan", "goon"), required=True)
    init.add_argument("--output", type=Path, required=True)
    init.add_argument(
        "--repository",
        action="append",
        type=Path,
        metavar="PATH",
        help="Select a registered League repository path; repeat for an exact target set",
    )
    init.add_argument(
        "--review",
        action="append",
        default=[],
        metavar="ALIAS=SKILL",
        help="Author an explicit ordered reviewer occurrence in a project-owned Composite Goon",
    )
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
