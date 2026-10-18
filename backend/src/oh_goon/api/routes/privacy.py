from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from oh_goon.runtime.database import default_database_path

router = APIRouter(prefix="/privacy", tags=["privacy"])


class PrivacyResponse(BaseModel):
    mode: Literal["LOCAL_ONLY"] = "LOCAL_ONLY"
    loopback_only: bool = True
    browser_preferences: bool = True
    persistent_repository_state: bool = True
    local_database_storage: bool = True
    local_database_file: str
    telemetry: bool = False
    source_upload: bool = False


@router.get("", response_model=PrivacyResponse, operation_id="privacy_status")
def privacy_status() -> PrivacyResponse:
    return PrivacyResponse(local_database_file=str(default_database_path()))
