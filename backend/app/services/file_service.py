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


_MAX_FILENAME_BYTES = 180


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename to prevent path traversal and other issues.

    Two length guards, in order (F72):
      1. Character clamp: keep at most `max(1, 200 - len(ext))` name
         characters before reattaching the extension. `max(1, ...)` matters
         because a pathological "extension" longer than 200 chars used to
         drive the slice negative (`name[:-101]`), silently keeping the
         *whole* name and returning something longer than the 200-char cap
         it was meant to enforce.
      2. Byte clamp: the character clamp alone doesn't bound encoded size for
         multibyte (e.g. CJK) names, and `get_upload_path` prefixes a 33-char
         uuid — so the reassembled name is trimmed to at most
         `_MAX_FILENAME_BYTES` UTF-8 bytes, cutting the stem on a character
         boundary (never splitting a multibyte codepoint) and always
         preserving the extension.
    """
    # Remove path components
    filename = os.path.basename(filename)
    # Remove non-alphanumeric characters except dots, hyphens, underscores
    filename = re.sub(r"[^\w.\-]", "_", filename)
    # Limit length (characters)
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        keep = max(1, 200 - len(ext))
        filename = name[:keep] + ext
    return _trim_to_byte_limit(filename, _MAX_FILENAME_BYTES)


def _trim_to_byte_limit(filename: str, max_bytes: int) -> str:
    """Trim `filename` to at most `max_bytes` UTF-8 bytes, preserving the
    extension and cutting the stem on a character boundary."""
    if len(filename.encode("utf-8")) <= max_bytes:
        return filename

    name, ext = os.path.splitext(filename)
    budget = max(0, max_bytes - len(ext.encode("utf-8")))
    stem_bytes = name.encode("utf-8")[:budget]
    # Dropping trailing bytes one at a time until the slice decodes cleanly
    # guarantees we never split a multibyte codepoint in half.
    while stem_bytes:
        try:
            return stem_bytes.decode("utf-8") + ext
        except UnicodeDecodeError:
            stem_bytes = stem_bytes[:-1]
    return ext


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
    """Delete a file and its preview/thumbnail caches if they exist.

    Covers the office-doc chain too: `<file>.preview.pdf` (the cached
    LibreOffice conversion) and `<file>.preview.pdf.thumb.jpg` (the thumbnail
    generated *from* that preview PDF), not just `<file>.thumb.jpg`.
    """
    if not filepath:
        return
    preview = filepath + ".preview.pdf"
    derivatives = (
        filepath,
        preview,
        filepath + ".thumb.jpg",
        preview + ".thumb.jpg",
    )
    for path in derivatives:
        try:
            if os.path.exists(path):
                os.unlink(path)
        except OSError:
            pass
