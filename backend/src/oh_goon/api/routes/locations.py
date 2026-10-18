from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from oh_goon.api.dependencies import location_service
from oh_goon.services.locations import LocationService

router = APIRouter(prefix="/locations", tags=["locations"])


class LocationRequest(BaseModel):
    path: str


class RemoveLocationRequest(BaseModel):
    path: str
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class LocationsResponse(BaseModel):
    locations: list[str]
    unavailable: list[str]
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")


def _locations_response(service: LocationService) -> LocationsResponse:
    locations, unavailable, revision = service.snapshot()
    return LocationsResponse(
        locations=[str(path) for path in locations],
        unavailable=[str(path) for path in unavailable],
        revision=revision,
    )


@router.get("", response_model=LocationsResponse, operation_id="list_locations")
def list_locations(service: LocationService = Depends(location_service)) -> LocationsResponse:
    return _locations_response(service)


@router.post("", response_model=LocationsResponse, operation_id="add_location")
def add_location(
    request: LocationRequest,
    service: LocationService = Depends(location_service),
) -> LocationsResponse:
    service.add(Path(request.path))
    return _locations_response(service)


@router.delete("", response_model=LocationsResponse, operation_id="remove_location")
def remove_location(
    request: RemoveLocationRequest,
    service: LocationService = Depends(location_service),
) -> LocationsResponse:
    service.remove(Path(request.path), expected_revision=request.expected_revision)
    return _locations_response(service)
