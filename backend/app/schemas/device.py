"""Schemas for the device authorization grant (RFC 8628-style)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class DeviceCodeRequest(BaseModel):
    client_name: str | None = Field(
        default=None, max_length=64, description="Name shown to the approving user.",
        examples=["Front-desk kiosk"],
    )


class DeviceCodeResponse(BaseModel):
    device_code: str = Field(description="Secret the device polls with; never shown to the user.")
    user_code: str = Field(description="Short code the user confirms, formatted XXXX-XXXX.",
                           examples=["WDJB-MJHT"])
    verification_uri: str = Field(description="Page where the user enters the code.")
    verification_uri_complete: str = Field(description="Verification URI with the code embedded "
                                           "(for a QR code).")
    interval: int = Field(description="Minimum seconds between token polls.", examples=[5])
    expires_in: int = Field(description="Seconds until the request expires.", examples=[300])


class DeviceLookupResponse(BaseModel):
    client_name: str = Field(description="Name of the requesting device.")
    scopes: list[str] = Field(description="Permissions the issued token will carry.")
    token_ttl_minutes: int = Field(description="Lifetime of the issued token in minutes.")
    expires_at: datetime = Field(description="When the pending request expires.")
    status: str = Field(description="pending, approved, denied or consumed.")


class DeviceApproveRequest(BaseModel):
    user_code: str = Field(description="The code shown on the device (dash optional).",
                           max_length=16)
    approve: bool = Field(description="True to approve, false to deny.")


class DeviceApproveResponse(BaseModel):
    status: str = Field(description="approved or denied.")


class DeviceTokenRequest(BaseModel):
    device_code: str = Field(description="The device_code from the code request.", max_length=256)


class DeviceTokenUser(BaseModel):
    id: uuid.UUID = Field(description="User id.")
    display_name: str = Field(description="User display name.")
    email: str = Field(description="User email.")


class DeviceTokenResponse(BaseModel):
    access_token: str = Field(description="Short-lived API token owned by the approving user.")
    token_id: uuid.UUID = Field(description="Id of the token, for DELETE /auth/tokens/{id}.")
    expires_at: datetime = Field(description="Token expiry.")
    scopes: list[str] = Field(description="Permissions granted.")
    user: DeviceTokenUser = Field(description="The approving user.")
