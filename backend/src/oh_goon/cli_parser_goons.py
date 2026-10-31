from __future__ import annotations

import argparse
from pathlib import Path


def add_init_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    init = subparsers.add_parser(
        "init",
        help="Register exact repositories if needed and write a prepared workflow and lock",
        description="Create a workflow directory and first lock; may register only the supplied exact repositories. --draft writes incomplete authoring files without registration or preparation.",
    )
    init.set_defaults(template_command="init")
    init.add_argument("--template", dest="key", required=True, metavar="CATEGORY/NAME")
    init.add_argument("--kind", choices=("plan", "goon"), default="plan")
    init.add_argument(
        "--output",
        type=Path,
        default=Path("workflow"),
        help="New workflow directory (default: workflow)",
    )
    init.add_argument(
        "--repository",
        action="append",
        type=Path,
        metavar="PATH",
        help="Exact repository required for ready initialization; repeat for an exact target set",
    )
    init.add_argument(
        "--draft",
        action="store_true",
        help="Explicitly author an incomplete draft without registration or preparation",
    )
    init.add_argument(
        "--input", action="append", dest="input_values", default=[], metavar="NAME=VALUE"
    )
    init.add_argument(
        "--input-null", action="append", dest="input_nulls", default=[], metavar="NAME"
    )
    init.add_argument(
        "--no-input", action="store_true", help="Refuse missing values without prompting"
    )
    init.add_argument("--json", action="store_true", dest="as_json")
    init.add_argument(
        "--review",
        action="append",
        default=[],
        metavar="ALIAS=SKILL",
        help="Author an explicit ordered reviewer occurrence in a project-owned Composite Goon",
    )


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
