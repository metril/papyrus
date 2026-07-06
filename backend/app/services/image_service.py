"""Image enhancement service for scanned documents."""

import asyncio
import logging
import os

import numpy as np
from PIL import Image, ImageEnhance

from app.exceptions import PapyrusError

logger = logging.getLogger(__name__)

# Longest-side cap for the downsampled image used during skew detection.
# The rotation angle is continuous, so downsampling costs almost no angular
# accuracy while making each candidate rotation dramatically cheaper.
_DESKEW_MAX_DIM = 1000


class ImageError(PapyrusError):
    status_code = 502


def detect_skew_angle(img: Image.Image) -> float:
    """Estimate the correcting skew angle (degrees) for a scanned page.

    Downsamples and binarizes the image once, then scores candidate angles by
    the variance of the binarized row-sum projection profile (higher variance =
    text rows better aligned with image rows). Uses a coarse pass (-5..+5 in 1
    steps) followed by a fine pass (best +/- 0.5 in 0.1 steps) around the best
    coarse angle, rotating only the small binary image. This mirrors the prior
    full-resolution 0.1-step sweep but evaluates ~22 rotations instead of 101.

    Returns 0.0 for uniform/blank pages (no content to align).
    """
    gray = img.convert("L")
    width, height = gray.size
    scale = min(1.0, _DESKEW_MAX_DIM / max(width, height))
    if scale < 1.0:
        small = gray.resize((max(1, int(width * scale)), max(1, int(height * scale))))
    else:
        small = gray

    arr = np.asarray(small)
    threshold = arr.mean()
    # Pre-binarize once: 0 = content (below threshold), 255 = background. The
    # fill color for rotation is 255 so filled corners never count as content.
    binary = Image.fromarray(np.where(arr < threshold, 0, 255).astype(np.uint8), "L")

    def _score(angle: float) -> float:
        rotated = binary.rotate(angle, expand=False, fillcolor=255)
        rot_bin = (np.asarray(rotated) < 128).astype(np.int32)
        return float(np.var(rot_bin.sum(axis=1)))

    best_angle = 0.0
    best_score = 0.0
    for step in range(-5, 6):  # coarse: -5..+5 degrees, 1-degree steps
        angle = float(step)
        score = _score(angle)
        if score > best_score:
            best_score = score
            best_angle = angle

    center = best_angle
    for step in range(-5, 6):  # fine: center +/- 0.5 degrees, 0.1-degree steps
        angle = round(center + step / 10.0, 1)
        score = _score(angle)
        if score > best_score:
            best_score = score
            best_angle = angle

    return best_angle


class ImageService:
    """Apply image enhancements to scanned images."""

    async def deskew(self, filepath: str) -> str:
        """Auto-deskew a scanned image using projection profiling.

        Detects the skew angle by analyzing horizontal line projections
        of the binarized image and rotates to correct it.
        """
        ext = os.path.splitext(filepath)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".tiff", ".tif"):
            return filepath

        def _deskew():
            img = Image.open(filepath)
            best_angle = detect_skew_angle(img)

            if abs(best_angle) > 0.1:
                img = img.rotate(best_angle, expand=True, fillcolor=255)
                img.save(filepath)

        try:
            await asyncio.to_thread(_deskew)
        except Exception as exc:
            raise ImageError(f"Deskew failed: {exc}") from exc

        return filepath

    async def enhance(
        self,
        filepath: str,
        brightness: float = 1.0,
        contrast: float = 1.0,
        rotation: int = 0,
        auto_crop: bool = False,
        deskew: bool = False,
    ) -> str:
        """Apply enhancements to an image file in-place."""
        # Skip if no enhancements requested
        if brightness == 1.0 and contrast == 1.0 and rotation == 0 and not auto_crop and not deskew:
            return filepath

        ext = os.path.splitext(filepath)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".tiff", ".tif"):
            logger.info("Skipping image enhancement for non-image format: %s", ext)
            return filepath

        def _process():
            img = Image.open(filepath)

            if brightness != 1.0:
                img = ImageEnhance.Brightness(img).enhance(brightness)

            if contrast != 1.0:
                img = ImageEnhance.Contrast(img).enhance(contrast)

            if rotation:
                # PIL rotates counter-clockwise, negate for intuitive clockwise rotation
                img = img.rotate(-rotation, expand=True)

            if auto_crop:
                img = _auto_crop(img)

            img.save(filepath)

        if deskew:
            await self.deskew(filepath)
            return filepath

        try:
            await asyncio.to_thread(_process)
        except Exception as exc:
            raise ImageError(f"Image enhancement failed: {exc}") from exc

        return filepath


def _auto_crop(img: Image.Image, threshold: int = 240) -> Image.Image:
    """Crop white/near-white borders from an image.

    Finds the bounding box of content pixels (grayscale value strictly below
    ``threshold``) via a numpy mask, then crops with a 10px margin. The crop
    box uses the inclusive last-content index plus the margin as an exclusive
    upper bound, so the effective bottom/right margin is one pixel smaller than
    the top/left margin (behavior preserved from the original implementation).
    Returns the image unchanged when there is no content, or when content spans
    only a single column or row (strict ``right > left`` / ``bottom > top``).
    """
    gray = img.convert("L")
    arr = np.asarray(gray)
    mask = arr < threshold
    if not mask.any():
        return img

    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    top, bottom = int(rows[0]), int(rows[-1])
    left, right = int(cols[0]), int(cols[-1])

    if right > left and bottom > top:
        margin = 10
        width, height = gray.size
        bbox = (
            max(0, left - margin),
            max(0, top - margin),
            min(width, right + margin),
            min(height, bottom + margin),
        )
        return img.crop(bbox)
    return img


image_service = ImageService()
