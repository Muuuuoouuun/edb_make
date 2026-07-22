from __future__ import annotations

from typing import Any

from PIL import Image, ImageChops, ImageFilter, ImageStat


PASSAGE_GOOD_SCORE = 0.85
PASSAGE_REVIEW_SCORE = 0.65
PASSAGE_JOIN_BLANK_BAND_REVIEW_PX = 48


def _clamp_ratio(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _foreground_ratio(image: Image.Image) -> float:
    gray = image.convert("L")
    gray.thumbnail((1200, 1200))
    histogram = gray.histogram()
    foreground = sum(histogram[:245])
    return foreground / max(1, gray.width * gray.height)


def _sharpness_rms(image: Image.Image) -> float:
    gray = image.convert("L")
    gray.thumbnail((1200, 1200))
    if gray.width > 4 and gray.height > 4:
        gray = gray.crop((2, 2, gray.width - 2, gray.height - 2))
    blurred = gray.filter(ImageFilter.GaussianBlur(radius=1.0))
    difference = ImageChops.difference(gray, blurred)
    return float(ImageStat.Stat(difference).rms[0])


def assess_passage_crop_quality(
    image: Image.Image,
    *,
    source_dpi: int | float | None = None,
    detection_confidence: int | float | None = None,
    text_line_count: int = 0,
    text_character_count: int = 0,
    text_bounds_score: int | float | None = None,
    stitch_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    width_px, height_px = image.size
    dpi = float(source_dpi) if isinstance(source_dpi, (int, float)) and source_dpi > 0 else None
    detection_score = _clamp_ratio(
        float(detection_confidence)
        if isinstance(detection_confidence, (int, float))
        else 0.9
    )
    width_score = _clamp_ratio(width_px / 720.0)
    dpi_score = _clamp_ratio(dpi / 180.0) if dpi is not None else width_score
    resolution_score = _clamp_ratio(dpi_score * 0.6 + width_score * 0.4)

    sharpness_rms = _sharpness_rms(image)
    sharpness_score = _clamp_ratio(sharpness_rms / 6.0)
    foreground_ratio = _foreground_ratio(image)
    if foreground_ratio < 0.002:
        content_score = 0.0
    elif foreground_ratio < 0.006:
        content_score = 0.5
    elif foreground_ratio > 0.55:
        content_score = 0.55
    else:
        content_score = 1.0

    normalized_line_count = max(0, int(text_line_count))
    normalized_character_count = max(0, int(text_character_count))
    text_layer_available = normalized_line_count > 0 or normalized_character_count > 0
    text_score = (
        _clamp_ratio(
            _clamp_ratio(normalized_character_count / 240.0) * 0.7
            + _clamp_ratio(normalized_line_count / 8.0) * 0.3
        )
        if text_layer_available
        else None
    )
    bounds_score = _clamp_ratio(
        float(text_bounds_score)
        if isinstance(text_bounds_score, (int, float))
        else 1.0
    )
    diagnostics = stitch_diagnostics if isinstance(stitch_diagnostics, dict) else {}
    join_count = max(0, int(diagnostics.get("join_count") or 0))
    join_blank_band_px = [
        max(0, int(value))
        for value in (diagnostics.get("join_blank_band_px") or [])
        if isinstance(value, (int, float))
    ]
    max_join_blank_band_px = max(
        join_blank_band_px,
        default=max(0, int(diagnostics.get("max_join_blank_band_px") or 0)),
    )

    weighted_scores = [
        (detection_score, 0.20),
        (resolution_score, 0.20),
        (sharpness_score, 0.20),
        (content_score, 0.15),
        (bounds_score, 0.15),
    ]
    if text_score is not None:
        weighted_scores.append((text_score, 0.10))
    weight_total = sum(weight for _score, weight in weighted_scores)
    overall_score = sum(score * weight for score, weight in weighted_scores) / max(weight_total, 0.01)

    warnings: list[str] = []
    if dpi is not None and dpi < 144:
        warnings.append("low_source_dpi")
    if width_px < 480:
        warnings.append("narrow_passage_crop")
    if sharpness_rms < 2.0:
        warnings.append("blurry_passage_crop")
    if foreground_ratio < 0.002:
        warnings.append("near_blank_passage_crop")
    elif foreground_ratio > 0.55:
        warnings.append("overdense_passage_crop")
    if detection_score < 0.9:
        warnings.append("low_passage_detection_confidence")
    if bounds_score < 0.995:
        warnings.append("passage_text_bounds_clipped")
    if join_count > 0 and max_join_blank_band_px > PASSAGE_JOIN_BLANK_BAND_REVIEW_PX:
        warnings.append("passage_join_gap_excessive")

    grade = (
        "good"
        if overall_score >= PASSAGE_GOOD_SCORE and not warnings
        else "review"
        if overall_score >= PASSAGE_REVIEW_SCORE
        else "poor"
    )
    return {
        "overall_score": round(overall_score, 4),
        "score_10": round(overall_score * 10.0, 2),
        "grade": grade,
        "detection_score": round(detection_score, 4),
        "text_layer_score": round(text_score, 4) if text_score is not None else None,
        "text_bounds_score": round(bounds_score, 4),
        "image_resolution_score": round(resolution_score, 4),
        "image_sharpness_score": round(sharpness_score, 4),
        "image_sharpness_rms": round(sharpness_rms, 3),
        "foreground_ratio": round(foreground_ratio, 5),
        "width_px": int(width_px),
        "height_px": int(height_px),
        "source_dpi": round(dpi, 2) if dpi is not None else None,
        "text_line_count": normalized_line_count,
        "text_character_count": normalized_character_count,
        "join_count": join_count,
        "join_blank_band_px": join_blank_band_px,
        "max_join_blank_band_px": max_join_blank_band_px,
        "warnings": warnings,
    }
