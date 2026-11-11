"""Schemas for kiosks (identities) and the personal address book."""
import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContactCreate(_Strict):
    name: str = Field(min_length=1, max_length=100, description="Display name.",
                      examples=["Accounts"])
    email: str = Field(max_length=255, description="Email address.",
                       examples=["accounts@example.com"])

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip()
        if not EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        return v


class ContactResponse(BaseModel):
    id: uuid.UUID = Field(description="Contact id.")
    name: str = Field(description="Display name.")
    email: str = Field(description="Email address.")
    created_at: datetime = Field(description="Creation time.")
    updated_at: datetime = Field(description="Last change time.")


class KioskCreate(_Strict):
    name: str = Field(min_length=1, max_length=100, description="Unique kiosk name.",
                      examples=["Front desk"])


class KioskUpdate(_Strict):
    name: str = Field(min_length=1, max_length=100, description="New unique kiosk name.")


class KioskResponse(BaseModel):
    id: uuid.UUID = Field(description="Kiosk id.")
    name: str = Field(description="Kiosk name.")
    token_id: uuid.UUID | None = Field(description="Id of the kiosk's API token.")
    last_seen_at: datetime | None = Field(
        description="Last authenticated request with the kiosk's token, null if never.")
    created_at: datetime = Field(description="Creation time.")
    updated_at: datetime = Field(description="Last change time.")


class KioskTokenResponse(BaseModel):
    kiosk: KioskResponse
    token: str = Field(description="Plaintext API token; shown only this once.")
