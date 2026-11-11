"""Scan visibility rule shared by the REST routes and the `scans` WebSocket fan-out."""
import uuid


def scan_access(user, perms: list[str] | None, owner_id: uuid.UUID | None) -> bool:
    """Whether `user` (session when `perms` is None, else a token's permission list) may see a
    scan owned by `owner_id`. Null-owner (eSCL) scans: sessions only, never API tokens."""
    if owner_id is None:
        return perms is None
    if owner_id == user.id:
        return True
    return user.role == "admin" and (perms is None or "admin" in perms)
