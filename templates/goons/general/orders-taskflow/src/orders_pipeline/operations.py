"""Engine-independent operations adapted from Airflow's official TaskFlow tutorial."""

import json
from decimal import Decimal, InvalidOperation
from importlib.resources import as_file, files
from pathlib import Path


def extract(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf8"))
    if not isinstance(value, dict):
        raise ValueError("Expected object of orders")
    return value


def extract_fixture(selection: str) -> dict:
    """Read a named fixture from this installed package's resources."""
    if selection not in {"official", "changed", "invalid"}:
        raise ValueError("Unknown fixture")
    resource = files("orders_pipeline").joinpath("data", selection + ".json")
    with as_file(resource) as path:
        return extract(path)


def transform(orders: dict) -> str:
    if not isinstance(orders, dict):
        raise ValueError("Expected object of orders")
    if not orders:
        raise ValueError("Empty orders")
    total = Decimal(0)
    for key, raw in orders.items():
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
            raise ValueError(f"Invalid amount for {key}")
        try:
            amount = Decimal(str(raw))
        except InvalidOperation as err:
            raise ValueError(f"Invalid amount for {key}") from err
        if not amount.is_finite():
            raise ValueError(f"Invalid amount for {key}")
        total += amount
    return str(total.quantize(Decimal("0.01")))


def load(total: str) -> dict:
    value = Decimal(total)
    if not value.is_finite():
        raise ValueError("Invalid total")
    return {"total_order_value": str(value.quantize(Decimal("0.01")))}
