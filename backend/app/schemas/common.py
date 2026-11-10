from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Standard paginated list envelope."""

    items: list[T]
    total: int = Field(description="Total matching rows ignoring limit/offset.")
    limit: int = Field(description="Page size requested.")
    offset: int = Field(description="Rows skipped.")



class BulkDeleteResponse(BaseModel):
    deleted: int
