"""Schemas for the device authorization grant (RFC 8628-style)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.api_docs import ErrorResponse


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


class KioskLoginConfig(BaseModel):
    approve: bool = Field(description="Tap-to-approve sign-in is enabled.")
    pin: bool = Field(description="PIN sign-in is enabled.")
    pin_length: int = Field(description="Required number of digits in a PIN.", examples=[6])


class KioskLoginUser(BaseModel):
    id: uuid.UUID = Field(description="User id.")
    display_name: str = Field(description="User display name.")
    has_pin: bool = Field(description="The user has set a kiosk PIN.")


class KioskPinSet(BaseModel):
    pin: str = Field(description="Numeric PIN with exactly `pin_length` digits.", max_length=32)


class KioskPinStatus(BaseModel):
    enabled: bool = Field(description="PIN sign-in is enabled by the admin.")
    pin_length: int = Field(description="Required number of digits.")
    has_pin: bool = Field(description="The caller has a PIN set.")


class ApproveRequestBody(BaseModel):
    user_id: uuid.UUID = Field(description="The user to ask for approval.")
    client_name: str = Field(default="Kiosk", max_length=64,
                             description="Name shown on the user's phone.")


class ApproveRequestResponse(BaseModel):
    device_code: str = Field(description="Secret to poll `POST /auth/device/token` with.")
    match_code: int = Field(description="Two-digit number to show on the kiosk (10-99); the user "
                            "must pick it on their phone.")
    expires_in: int = Field(description="Seconds until the request expires.", examples=[300])
    interval: int = Field(description="Minimum seconds between token polls.", examples=[5])


class PendingRequest(BaseModel):
    id: uuid.UUID = Field(description="Request id.")
    client_name: str = Field(description="Requesting kiosk's name.")
    choices: list[int] = Field(description="Three two-digit numbers; one matches the kiosk.")
    expires_at: datetime = Field(description="When the request expires.")


class MatchApproveBody(BaseModel):
    match_code: int = Field(description="The number the user picked.")


class PinLoginRequest(BaseModel):
    user_id: uuid.UUID = Field(description="The user signing in.")
    pin: str = Field(description="The user's kiosk PIN.", max_length=32)
    client_name: str = Field(default="Kiosk", max_length=64, description="Kiosk name for the "
                             "audit log and token name.")


class KioskPinError(ErrorResponse):
    """Error envelope for PIN sign-in failures, with the extra counters."""

    remaining: int | None = Field(default=None, description="401: attempts left before lock.")
    retry_after: int | None = Field(default=None, description="423: seconds until the PIN "
                                    "unlocks.")
