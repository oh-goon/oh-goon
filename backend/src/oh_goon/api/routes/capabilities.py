from fastapi import APIRouter

from oh_goon.core.capability_registry import capability_info
from oh_goon.core.models.capability import CapabilityInfo

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


@router.get("", response_model=list[CapabilityInfo], operation_id="capabilities")
def capabilities() -> list[CapabilityInfo]:
    """Return the canonical backend capability registry for UI/discovery clients."""
    return capability_info(fleet_only=True)
