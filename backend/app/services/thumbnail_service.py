"""Thumbnail generation service — cached small previews for scan files.

Scan previews used to require fetching the full scan (a 600-DPI scan can be
several MB). This module produces a small cached JPEG sibling
(``<file>.thumb.jpg``) so list/grid views can load a cheap preview instead.

Images are resized with PIL in a worker thread (``asyncio.to_thread``). PDFs
are rendered to a PNG via a ghostscript subprocess (argument list, never
``shell=True``) — copy of the async-subprocess pattern used in
``app.services.convert_service`` — then resized the same way as images.

Caching is mtime-based: if the cached thumbnail is at least as new as the
source file, it's reused as-is; otherwise it's regenerated. This keeps the
cache correct even for files that get rewritten in place (e.g. image
enhancement/deskew, OCR) without every caller having to know about
thumbnails. Callers that rewrite scan files in place additionally call
``invalidate_thumbnail`` right after the rewrite so the cache doesn't serve a
stale thumbnail within the same filesystem-mtime tick.
"""

import asyncio
import os
import uuid

from PIL import Image

from app.exceptions import PapyrusError

THUMBNAIL_MAX_DIM = 320
THUMBNAIL_SUFFIX = ".thumb.jpg"
THUMBNAIL_JPEG_QUALITY = 80

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif"}
_PDF_RENDER_DPI = 150


class ThumbnailError(PapyrusError):
    status_code = 502


def _thumbnail_path(path: str) -> str:
    return path + THUMBNAIL_SUFFIX


def _resize_to_thumbnail_sync(src_path: str, thumb_path: str) -> None:
    """Resize `src_path` (any PIL-readable image) into a JPEG thumbnail.

    Writes to a unique temp file first, then atomically replaces `thumb_path`,
    so a concurrent reader never sees a partially-written thumbnail.
    """
    img = Image.open(src_path)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((THUMBNAIL_MAX_DIM, THUMBNAIL_MAX_DIM), Image.LANCZOS)

    tmp_path = f"{thumb_path}.tmp.{uuid.uuid4().hex}"
    try:
        img.save(tmp_path, format="JPEG", quality=THUMBNAIL_JPEG_QUALITY)
        os.replace(tmp_path, thumb_path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


async def _render_pdf_first_page(pdf_path: str, out_png: str) -> None:
    """Render page 1 of a PDF to a PNG via ghostscript."""
    process = await asyncio.create_subprocess_exec(
        "gs",
        "-dNOPAUSE",
        "-dBATCH",
        "-dSAFER",
        "-dQUIET",
        "-sDEVICE=png16m",
        f"-r{_PDF_RENDER_DPI}",
        "-dFirstPage=1",
        "-dLastPage=1",
        f"-sOutputFile={out_png}",
        pdf_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode().strip() if stderr else "Unknown error"
        raise ThumbnailError(f"ghostscript render failed: {error_msg}")
    if not os.path.exists(out_png):
        raise ThumbnailError("ghostscript produced no output file")


async def get_or_create_thumbnail(path: str) -> str:
    """Return the path to a cached thumbnail for the file at `path`.

    Generates it on first call, or regenerates it if the source file has been
    modified since the thumbnail was cached (mtime comparison). Images are
    resized directly; PDFs have their first page rendered to PNG via
    ghostscript, then resized the same way.

    Raises:
        FileNotFoundError: if `path` does not exist.
        ThumbnailError: if the file type is unsupported or generation fails.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    thumb_path = _thumbnail_path(path)
    if os.path.exists(thumb_path) and os.path.getmtime(thumb_path) >= os.path.getmtime(path):
        return thumb_path

    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        rendered_png = f"{path}.thumbsrc.{uuid.uuid4().hex}.png"
        try:
            await _render_pdf_first_page(path, rendered_png)
            await asyncio.to_thread(_resize_to_thumbnail_sync, rendered_png, thumb_path)
        except ThumbnailError:
            raise
        except Exception as exc:
            raise ThumbnailError(f"PDF thumbnail failed: {exc}") from exc
        finally:
            if os.path.exists(rendered_png):
                os.unlink(rendered_png)
    elif ext in _IMAGE_EXTENSIONS:
        try:
            await asyncio.to_thread(_resize_to_thumbnail_sync, path, thumb_path)
        except Exception as exc:
            raise ThumbnailError(f"Image thumbnail failed: {exc}") from exc
    else:
        raise ThumbnailError(f"Unsupported file type for thumbnail: {ext}")

    return thumb_path


def invalidate_thumbnail(path: str | None) -> None:
    """Delete the cached thumbnail sibling for `path`, if any.

    Call this right after rewriting a scan file in place (enhance, deskew,
    OCR) so a stale thumbnail is never served due to same-tick mtime
    collisions with the mtime-based cache check in `get_or_create_thumbnail`.
    """
    if not path:
        return
    thumb_path = _thumbnail_path(path)
    try:
        if os.path.exists(thumb_path):
            os.unlink(thumb_path)
    except OSError:
        pass
