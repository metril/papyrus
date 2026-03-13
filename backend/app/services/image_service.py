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

    async def enhance(
        self,
        filepath: str,
        brightness: float = 1.0,
        contrast: float = 1.0,
        rotation: int = 0,
        auto_crop: bool = False,
    ) -> str:
        """Apply enhancements to an image file in-place.

        Args:
            filepath: Path to the image file (png, jpeg, tiff).
            brightness: Brightness factor (1.0 = no change, >1 brighter).
            contrast: Contrast factor (1.0 = no change, >1 more contrast).
            rotation: Rotation angle in degrees (0, 90, 180, 270).
            auto_crop: Whether to auto-crop whitespace borders.

        Returns the filepath (unchanged, enhanced in-place).
        """
        # Skip if no enhancements requested
        if brightness == 1.0 and contrast == 1.0 and rotation == 0 and not auto_crop:
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
