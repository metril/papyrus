"""Build small real PDFs whose pages are identifiable by their width."""
import io

import img2pdf
from PIL import Image, ImageDraw
from pypdf import PdfReader

BASE_WIDTH = 300
HEIGHT = 400


def page_image(tag: int, kind: str = "text") -> Image.Image:
    img = Image.new("L", (BASE_WIDTH + tag, HEIGHT), 255)
    draw = ImageDraw.Draw(img)
    if kind == "text":
        draw.rectangle((40, 100, 250, 300), fill=0)
    elif kind == "line":
        draw.rectangle((60, 190, 180, 196), fill=0)
    elif kind == "dark":
        draw.rectangle((0, 0, BASE_WIDTH + tag, HEIGHT), fill=20)
    elif kind == "blank":
        pass
    elif kind == "edge":
        draw.rectangle((0, 0, 6, HEIGHT), fill=0)
    elif kind == "noise":
        import numpy as np
        rng = np.random.default_rng(tag)
        arr = 250 - rng.integers(0, 9, size=(HEIGHT, BASE_WIDTH + tag), dtype=np.uint8)
        img = Image.fromarray(arr, "L")
    return img


def make_pdf(path, specs: list[tuple[int, str]]) -> None:
    """specs: [(tag, kind), ...] one page each."""
    chunks = []
    for tag, kind in specs:
        buf = io.BytesIO()
        page_image(tag, kind).save(buf, format="PNG", dpi=(72, 72))
        chunks.append(buf.getvalue())
    with open(path, "wb") as f:
        f.write(img2pdf.convert(chunks))


def tags(path) -> list[int]:
    return [round(float(p.mediabox.width)) - BASE_WIDTH for p in PdfReader(path).pages]
