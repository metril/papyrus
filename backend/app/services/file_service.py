import mimetypes
import os
import re
import uuid

from starlette.datastructures import UploadFile

# Re-exported for backward compatibility: the canonical definition now lives in
# app.exceptions so it participates in the domain-exception hierarchy (413).
from app.exceptions import UploadTooLargeError

_STREAM_CHUNK_BYTES = 1024 * 1024  # 1 MiB


async def save_upload_streaming(upload_file: UploadFile, dest_path: str, max_bytes: int) -> int:
    """Stream an upload to `dest_path` in 1 MiB chunks without buffering it in RAM.

    Raises UploadTooLargeError the moment the running total exceeds `max_bytes`.
    On ANY failure (size cap, disk error, client disconnect/cancellation, ...)
    the partial file is removed before the exception propagates. Returns the
    total number of bytes written on success.
    """
    total = 0
    try:
        with open(dest_path, "wb") as f:
            while True:
                chunk = await upload_file.read(_STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise UploadTooLargeError(
                        f"Upload exceeds maximum allowed size of {max_bytes} bytes"
                    )
                f.write(chunk)
    except BaseException:
        # BaseException so asyncio.CancelledError (client disconnect) is included.
        try:
            os.unlink(dest_path)
        except FileNotFoundError:
            pass
        raise
    return total


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename to prevent path traversal and other issues."""
    # Remove path components
    filename = os.path.basename(filename)
    # Remove non-alphanumeric characters except dots, hyphens, underscores
    filename = re.sub(r"[^\w.\-]", "_", filename)
    # Limit length
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[:200 - len(ext)] + ext
    return filename


def get_upload_path(filename: str, upload_dir: str = "/app/data/uploads") -> str:
    """Generate a unique upload path for a file."""
    safe_name = sanitize_filename(filename)
    unique_name = f"{uuid.uuid4().hex}_{safe_name}"
    return os.path.join(upload_dir, unique_name)


def get_scan_path(scan_id: str, fmt: str, scan_dir: str = "/app/data/scans") -> str:
    """Generate the file path for a scan."""
    return os.path.join(scan_dir, f"{scan_id}.{fmt}")


def detect_mime_type(filename: str) -> str:
    """Detect MIME type from filename."""
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type or "application/octet-stream"


def cleanup_file(filepath: str | None) -> None:
    """Delete a file and its preview/thumbnail caches if they exist."""
    if not filepath:
        return
    try:
        if os.path.exists(filepath):
            os.unlink(filepath)
        preview = filepath + ".preview.pdf"
        if os.path.exists(preview):
            os.unlink(preview)
        thumbnail = filepath + ".thumb.jpg"
        if os.path.exists(thumbnail):
            os.unlink(thumbnail)
    except OSError:
        pass
