"""Read-only CLI presentation of the existing canonical template stores."""

from __future__ import annotations

import argparse
import json
import shlex
import sys

from oh_goon.core.errors import FleetDomainError
from oh_goon.core.template_identity import template_usage
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore


def run_template_command(args: argparse.Namespace) -> int:
    stores = {"plan": PlanTemplateStore, "goon": GoonTemplateStore}
    try:
        if args.template_command == "init":
            from oh_goon.services.template_plan_drafts import initialize_run_configuration

            reviewers = []
            for value in args.review:
                if "=" not in value:
                    raise ValueError("--review requires ALIAS=SKILL")
                alias, skill = value.split("=", 1)
                reviewers.append((alias, skill))
            destination = initialize_run_configuration(
                args.key,
                args.output,
                kind=args.kind,
                reviewers=reviewers,
                repository=args.repository,
            )
            print(f"Created: {destination}")
            print(
                "Edit the required inputs in the created run.yml, if any."
                if args.repository is not None
                else "Set target.repository_id and required inputs in the created run.yml. "
                "Use --repository PATH when initializing to select a registered repository by path."
            )
            print("Next: " + shlex.join(("oh-goon", "lock", str(destination))))
            print("Then: " + shlex.join(("oh-goon", "run", str(destination))))
            print(
                "Optional inspection: "
                + shlex.join(("oh-goon", "config", "inspect", str(destination)))
            )
            return 0
        if args.template_command == "show":
            print(stores[args.kind]().read(args.key).content, end="")
            return 0
        entries = [
            {"kind": kind, **item.model_dump(mode="json"), **template_usage(item.classification)}
            for kind, factory in stores.items()
            if args.kind in {kind, "all"}
            for item in factory().catalog().templates
        ]
    except (FleetDomainError, OSError, UnicodeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps({"schema": "oh-goon.template-catalog/v1", "templates": entries}, indent=2))
    else:
        for item in entries:
            print(
                f"{item['kind']:4}  {item['template_key']} — {item['description']} | "
                f"{item['usage']} | readiness={item['readiness']} | "
                f"prerequisites={item['prerequisites']}"
            )
    return 0
