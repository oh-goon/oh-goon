from fastapi import APIRouter, Depends
from pydantic import BaseModel

from oh_goon.api.dependencies import filesystem_service
from oh_goon.services.filesystem import FilesystemService

router = APIRouter(prefix="/filesystem", tags=["filesystem"])


class BrowseResponse(BaseModel):
    path: str
    parent: str | None
    directories: list[str]


@router.get("/browse", response_model=BrowseResponse, operation_id="browse")
def browse(
    path: str | None = None,
    service: FilesystemService = Depends(filesystem_service),
) -> BrowseResponse:
    current, directories = service.browse(path)
    parent = None if current.parent == current else str(current.parent)
    return BrowseResponse(
        path=str(current), parent=parent, directories=[str(item) for item in directories]
    )
