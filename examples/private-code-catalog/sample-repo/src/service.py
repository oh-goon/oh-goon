"""Order submission fixture used for local repository discovery."""


def submit_order(order_id: str) -> dict[str, str]:
    """Accept a synthetic order without external I/O."""
    return {"order_id": order_id, "status": "accepted"}
