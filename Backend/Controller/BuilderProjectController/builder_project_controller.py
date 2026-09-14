"""HTTP routes for builder projects — the Builder Projects page, where
properties are added by hand rather than captured from WhatsApp. Thin by
design; everything lives in Service/BuilderProjectService/.
"""

from typing import Any, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from Middleware import http_cache
from Model.BuilderProjectModel.builder_project import BuilderProjectRecord
from Service.BuilderProjectService import builder_project_service

router = APIRouter(prefix="/builder-projects", tags=["builder-projects"])


class BuilderProjectContentFields(BaseModel):
    """What the Add/Edit dialog exposes — exactly the Properties page's own
    field set (Controller/WhatsAppDataFetchingController/property_controller.py's
    PropertyContentFields), since it is the same dialog. Every field is
    optional: a project can be saved with as little or as much detail as is
    known right now."""

    property_type: Optional[str] = None
    bhk: Optional[str] = None
    society_name: Optional[str] = None
    area_name: Optional[str] = None
    address: Optional[str] = None
    carpet_area_sqft: Optional[float] = None
    carpet_area_unit: Optional[str] = None
    super_built: Optional[str] = None
    price_text: Optional[str] = None
    price_amount_inr: Optional[float] = None
    price_per_unit_text: Optional[str] = None
    price_per_unit_amount_inr: Optional[float] = None
    listing_type: Literal["Sale", "Rent"] = "Sale"
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    description: Optional[str] = None
    instagram_reel_url: Optional[str] = None
    image_urls: List[str] = Field(default_factory=list)


class BuilderProjectUpdateRequest(BuilderProjectContentFields):
    """Only the fields actually present in the JSON body are applied (see
    the route's exclude_unset) — so an edit whose photos were never loaded
    leaves `image_urls` out entirely and can never cost the project its
    photos, exactly as on the Properties page."""

    listing_type: Optional[Literal["Sale", "Rent"]] = None
    image_urls: Optional[List[str]] = None


class BuilderProjectImages(BaseModel):
    image_urls: List[str]


@router.get("", response_model=list[BuilderProjectRecord])
def get_builder_projects(request: Request, response: Response, limit: int = 500) -> Any:
    """Served from the in-memory cache, and conditional on top of that: a
    browser that already holds the current list gets a bodyless 304, and
    proving nothing changed costs no database work either."""
    etag = http_cache.build_etag("builder-projects", limit, builder_project_service.get_builder_projects_version())
    unchanged = http_cache.conditional(request, response, etag)
    return unchanged if unchanged is not None else builder_project_service.get_builder_projects(limit=limit)


@router.get("/{record_id}/images", response_model=BuilderProjectImages)
def get_builder_project_images(record_id: str, request: Request, response: Response) -> Any:
    """This project's photos — the only place its image data leaves the
    database, and only when someone presses Show photos. Tagged with the
    project's own updated_at, so every view after the first costs about a
    hundred bytes until the project is actually edited."""
    version = builder_project_service.get_builder_project_version(record_id)
    etag = None if version is None else http_cache.build_etag("builder-project-images", record_id, version)
    unchanged = http_cache.conditional(request, response, etag)
    if unchanged is not None:
        return unchanged
    images = builder_project_service.get_builder_project_images(record_id)
    if images is None:
        raise HTTPException(status_code=404, detail="Builder project not found")
    return BuilderProjectImages(image_urls=images)


@router.post("", response_model=BuilderProjectRecord, status_code=201)
def create_builder_project(body: BuilderProjectContentFields) -> BuilderProjectRecord:
    return builder_project_service.create_builder_project(body.model_dump())


@router.patch("/{record_id}", response_model=BuilderProjectRecord)
def update_builder_project(record_id: str, body: BuilderProjectUpdateRequest) -> BuilderProjectRecord:
    updated = builder_project_service.update_builder_project(record_id, body.model_dump(exclude_unset=True))
    if updated is None:
        raise HTTPException(status_code=404, detail="Builder project not found")
    return updated


@router.delete("/{record_id}", status_code=204)
def delete_builder_project(record_id: str) -> None:
    if not builder_project_service.delete_builder_project(record_id):
        raise HTTPException(status_code=404, detail="Builder project not found")
