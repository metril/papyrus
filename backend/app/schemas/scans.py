import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ScanRequest(BaseModel):
    resolution: int = Field(default=300, ge=75, le=600)
    mode: str = Field(default="Color", pattern="^(Color|Gray|Lineart)$")
    format: str = Field(default="pdf", pattern="^(png|jpeg|tiff|pdf)$")
    source: str = Field(default="Flatbed", pattern="^(Flatbed|ADF)$")
    # F26: lets the client generate the id up front and subscribe to its
    # progress WebSocket *before* POSTing, instead of only learning the id
    # after the (synchronous) scan has already finished. Backend-generated
    # when omitted.
    scan_id: uuid.UUID | None = None


class ScanBatchRequest(ScanRequest):
    source: str = "ADF"


class ScanResponse(BaseModel):
    id: int
    scan_id: str
    status: str
    resolution: int
    mode: str
    format: str
    source: str
    page_count: int
    file_size: int | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None
    duplex_merged: bool = False
    duplex_source_scan_id: str | None = None

    model_config = {"from_attributes": True}


def serialize_scan_job(scan) -> dict:
    """Serialize a ScanJob ORM object into the exact JSON shape returned by the
    scan list endpoint (``ScanResponse``).

    Reused for WebSocket broadcasts so realtime payloads match the REST shape
    field-for-field.
    """
    return ScanResponse.model_validate(scan, from_attributes=True).model_dump(mode="json")


class ScanProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    resolution: int = Field(default=300, ge=75, le=600)
    color_mode: str = Field(default="Color", pattern="^(Color|Gray|Lineart)$")
    format: str = Field(default="pdf", pattern="^(png|jpeg|tiff|pdf)$")
    source: str = Field(default="Flatbed", pattern="^(Flatbed|ADF)$")
    ocr_enabled: bool = False


class ScanProfileResponse(BaseModel):
    id: int
    name: str
    resolution: int
    color_mode: str
    format: str
    source: str
    ocr_enabled: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class CollateRequest(BaseModel):
    scan_ids: list[str] = Field(min_length=1, max_length=50)
    output_filename: str = "merged.pdf"


class BulkDeleteScansRequest(BaseModel):
    scan_ids: list[str] = Field(min_length=1, max_length=100)
