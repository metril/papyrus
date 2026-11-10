import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class PrintJobUpload(BaseModel):
    copies: int = Field(default=1, ge=1, le=99)
    duplex: bool = False
    media: str = "A4"
    hold: bool = True


class PrintJobResponse(BaseModel):
    id: int
    user_id: uuid.UUID | None = None
    cups_job_id: int | None
    title: str
    filename: str
    file_size: int
    mime_type: str
    status: str
    copies: int
    duplex: bool
    media: str
    source_type: str
    printer_id: int | None = None
    has_pin: bool = False
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}

    @model_validator(mode="wrap")
    @classmethod
    def _compute_has_pin(cls, values, handler):
        obj = handler(values)
        # Compute has_pin from the source ORM object or dict
        if hasattr(values, "release_pin"):
            obj.has_pin = bool(values.release_pin)
        elif isinstance(values, dict) and "release_pin" in values:
            obj.has_pin = bool(values["release_pin"])
        return obj


def serialize_print_job(job) -> dict:
    """Serialize a PrintJob ORM object into the exact JSON shape returned by the
    print-job list/detail endpoints (``PrintJobResponse``).

    Reused for WebSocket broadcasts so realtime payloads match the REST shape
    field-for-field. ``release_pin`` is deliberately never included — only the
    computed ``has_pin`` bool is exposed — so broadcasting a full job to every
    connected client can never leak a PIN.
    """
    return PrintJobResponse.model_validate(job, from_attributes=True).model_dump(mode="json")


class CopyRequest(BaseModel):
    resolution: int = Field(default=300, ge=75, le=600)
    mode: str = Field(default="Color", pattern="^(Color|Gray|Lineart)$")
    source: str = Field(default="Flatbed", pattern="^(Flatbed|ADF)$")
    copies: int = Field(default=1, ge=1, le=99)
    duplex: bool = False
    media: str = "A4"


class MarkerLevel(BaseModel):
    name: str
    level: int  # 0-100, -1 = unknown
    color: str


class PrinterStatus(BaseModel):
    state: int  # 3=idle, 4=printing, 5=stopped
    state_message: str
    accepting_jobs: bool
    markers: list[MarkerLevel] = []
    state_reasons: list[str] = []


class PrinterSettings(BaseModel):
    media: str = "A4"
    duplex: bool = False
    quality: str = "normal"


class BulkDeleteJobsRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=100)
