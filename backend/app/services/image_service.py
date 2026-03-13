"""Image enhancement service for scanned documents."""

import asyncio
import logging
import os

from PIL import Image, ImageEnhance

logger = logging.getLogger(__name__)


class ImageError(Exception):
    pass


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
            import numpy as np

            img = Image.open(filepath)
            gray = img.convert("L")
            arr = np.array(gray)

            # Binarize (Otsu-like threshold)
            threshold = arr.mean()
            binary = (arr < threshold).astype(np.int32)

            # Try angles from -5 to +5 degrees in 0.1 increments
            best_angle = 0.0
            best_score = 0.0
            for angle_10x in range(-50, 51):
                angle = angle_10x / 10.0
                rotated = img.rotate(angle, expand=False, fillcolor=255)
                rot_arr = np.array(rotated.convert("L"))
                rot_bin = (rot_arr < threshold).astype(np.int32)
                # Score = variance of row sums (higher = more aligned text)
                row_sums = rot_bin.sum(axis=1)
                score = float(np.var(row_sums))
                if score > best_score:
                    best_score = score
                    best_angle = angle

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
    """Crop white/near-white borders from an image."""
    gray = img.convert("L")
    # Find bounding box of non-white content
    bbox = None
    pixels = gray.load()
    if pixels is None:
        return img
    width, height = gray.size
    left = width
    top = height
    right = 0
    bottom = 0
    for y in range(height):
        for x in range(width):
            if pixels[x, y] < threshold:
                left = min(left, x)
                top = min(top, y)
                right = max(right, x)
                bottom = max(bottom, y)
    if right > left and bottom > top:
        # Add small margin
        margin = 10
        bbox = (
            max(0, left - margin),
            max(0, top - margin),
            min(width, right + margin),
            min(height, bottom + margin),
        )
        return img.crop(bbox)
    return img


image_service = ImageService()
