"""Conservative local color-illustration candidates for raster workbook crops.

This detects substantial color patches, not semantic figure boundaries. Small
colored labels, narrow highlights and page frames stay on the chalk path.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from PIL import Image

try:
    import cv2
    import numpy as np
except ImportError:  # optional in source-only installations
    cv2 = None
    np = None


def _rgb_on_white(image: Image.Image) -> Image.Image:
    if "A" in image.getbands() or "transparency" in image.info:
        white = Image.new("RGBA", image.size, "white")
        return Image.alpha_composite(white, image.convert("RGBA")).convert("RGB")
    return image.convert("RGB")


def _color_mask(rgb):
    # Channel-wise ufuncs avoid the costly per-pixel axis reductions and a
    # full int16 copy. high >= low makes their uint8 subtraction safe.
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    high = np.maximum(np.maximum(red, green), blue)
    low = np.minimum(np.minimum(red, green), blue)
    return ((high - low >= 42) & (high >= 80) & (low <= 220)).astype(np.uint8)


def _analysis_rgb(image: Image.Image, edge: int) -> Image.Image:
    # Reduce before converting/copying the RGB channels, so large source crops
    # do not allocate another full-size RGB canvas for each parallel task.
    scale = min(1.0, edge / max(image.size))
    if scale < 1.0:
        image = image.resize((max(1, round(image.width * scale)),
                              max(1, round(image.height * scale))), Image.Resampling.BILINEAR,
                             reducing_gap=3.0)
    return _rgb_on_white(image)


def region_has_substantial_color(image: Image.Image, region: Mapping[str, Any]) -> bool:
    """Keep monochrome math figures as chalk, including in older sessions."""
    if np is None:
        return False
    box = region.get("bbox")
    if not isinstance(box, Mapping):
        return False
    try:
        left, top = float(box.get("left", 0)), float(box.get("top", 0))
        right = float(box.get("right", left + float(box.get("width", 0))))
        bottom = float(box.get("bottom", top + float(box.get("height", 0))))
        if not all(math.isfinite(v) for v in (left, top, right, bottom)):
            return False
        bounds = (max(0, math.floor(left)), max(0, math.floor(top)),
                  min(image.width, math.ceil(right)), min(image.height, math.ceil(bottom)))
    except (TypeError, ValueError, OverflowError):
        return False
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        return False
    patch = _analysis_rgb(image.crop(bounds), 384)
    mask = _color_mask(np.asarray(patch))
    return int(mask.sum()) >= max(64, int(mask.size * 0.02))


def detect_color_media_regions(image: Image.Image) -> list[dict[str, Any]]:
    """Return crop-local boxes for dense color media; never alter source pixels.

    Analysis is bounded to a 960 px edge. Ambiguous or monochrome content
    produces no candidates and keeps the existing rendering treatment.
    """
    if cv2 is None or np is None or min(image.size) < 64:
        return []
    analysis = _analysis_rgb(image, 960)
    width, height = analysis.size
    color = _color_mask(np.asarray(analysis))
    if int(color.sum()) < 200:
        return []
    grouped = cv2.dilate(color, np.ones((9, 9), dtype=np.uint8))
    count, _labels, stats, _centers = cv2.connectedComponentsWithStats(grouped, 8)
    candidates: list[dict[str, Any]] = []
    for x, y, w, h, _area in stats[1:count]:
        x, y, w, h = int(x), int(y), int(w), int(h)
        area = w * h
        if min(w, h) < 40 or max(w / h, h / w) > 4.5:
            continue
        if area > width * height * 0.38 or y < height * 0.08 or y + h > height * 0.94:
            continue
        patch = color[y:y + h, x:x + w]
        colored = int(patch.sum())
        if colored < max(200, area * 0.18):
            continue
        _n, _l, pieces, _c = cv2.connectedComponentsWithStats(patch, 8)
        if len(pieces) <= 1 or int(pieces[1:, cv2.CC_STAT_AREA].max()) < 96:
            continue
        # A small margin retains attached dark outlines/antialiasing. It is
        # deliberately bounded; no attempt is made to reconstruct lost detail.
        left = max(0, math.floor((x - 3) * image.width / width))
        top = max(0, math.floor((y - 3) * image.height / height))
        right = min(image.width, math.ceil((x + w + 3) * image.width / width))
        bottom = min(image.height, math.ceil((y + h + 3) * image.height / height))
        candidates.append({
            "kind": "image", "source": "raster_color_candidate_v1", "confidence": 0.9,
            "bbox": {"left": left, "top": top, "right": right, "bottom": bottom,
                     "width": right - left, "height": bottom - top},
        })
        if len(candidates) >= 16:
            break
    return candidates


def stage2_media_regions(
    image: Image.Image, regions: Sequence[Mapping[str, Any]], *, chalk_math: bool,
) -> list[Mapping[str, Any]]:
    return [region for region in regions
            if region.get("source") == "manual"
            or not chalk_math or region_has_substantial_color(image, region)]
