"""Load a PDF or an image as page images, and straighten slightly rotated scans."""
from pathlib import Path

import numpy as np
from PIL import Image

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
SUPPORTED = IMAGE_EXT | {".pdf"}


def load(path, dpi=200):
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(path))
        try:
            return [pdf[i].render(scale=dpi / 72).to_pil().convert("RGB") for i in range(len(pdf))]
        finally:
            pdf.close()
    if ext in IMAGE_EXT:
        with Image.open(path) as im:
            return [im.convert("RGB")]
    raise ValueError(f"unsupported file type: {ext}")


def skew_angle(img, max_deg=3.0, step=0.1):
    """Angle (degrees) that makes text rows horizontal: the rotation whose row
    profile is the sharpest. Run on a reduced copy, so it costs ~0.1 s per page."""
    small = img.convert("L")
    small = small.resize((small.width // 4, small.height // 4))
    ink = Image.fromarray(((np.asarray(small) < 150) * 255).astype(np.uint8))
    best, best_score = 0.0, -1.0
    for a in np.arange(-max_deg, max_deg + step / 2, step):
        prof = np.asarray(ink.rotate(float(a), resample=Image.NEAREST)).sum(axis=1, dtype=np.float64)
        score = float(np.square(np.diff(prof)).sum())
        if score > best_score:
            best, best_score = float(a), score
    return round(best, 2)


def deskew(img):
    a = skew_angle(img)
    if abs(a) < 0.05:
        return img, 0.0
    return img.rotate(a, resample=Image.BICUBIC, fillcolor=(255, 255, 255)), a
