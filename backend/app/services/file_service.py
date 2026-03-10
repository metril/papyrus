import mimetypes
import os
import re
import uuid

from app.config import settings


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


def get_upload_path(filename: str) -> str:
    """Generate a unique upload path for a file."""
    safe_name = sanitize_filename(filename)
    unique_name = f"{uuid.uuid4().hex}_{safe_name}"
    return os.path.join(settings.upload_dir, unique_name)


def get_scan_path(scan_id: str, fmt: str) -> str:
    """Generate the file path for a scan."""
    return os.path.join(settings.scan_dir, f"{scan_id}.{fmt}")


def detect_mime_type(filename: str) -> str:
    """Detect MIME type from filename."""
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type or "application/octet-stream"


def validate_upload_size(size: int) -> bool:
    """Check if file size is within limits."""
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    return size <= max_bytes


def cleanup_file(filepath: str) -> None:
    """Delete a file if it exists."""
    try:
        if os.path.exists(filepath):
            os.unlink(filepath)
    except OSError:
        pass
