"""Read-only CLI presentation of the existing canonical template stores."""

from __future__ import annotations

import argparse
import json
import sys

from oh_goon.core.errors import FleetDomainError
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore


def run_template_command(args: argparse.Namespace) -> int:
    stores = {"plan": PlanTemplateStore, "goon": GoonTemplateStore}
    try:
        if args.template_command == "show":
            print(stores[args.kind]().read(args.key).content, end="")
            return 0
        entries = [
            {"kind": kind, **item.model_dump(mode="json")}
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
            print(f"{item['kind']:4}  {item['template_key']} — {item['description']}")
    return 0
