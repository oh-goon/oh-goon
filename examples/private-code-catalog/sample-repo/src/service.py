"""Order submission fixture used for local repository discovery."""

from .order_status import accepted_status


def submit_order(order_id: str) -> dict[str, str]:
    """Accept a synthetic order without external I/O."""
    return {"order_id": order_id, "status": accepted_status()}
