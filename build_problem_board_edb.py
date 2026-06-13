#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from PIL import Image, ImageFilter, ImageOps, ImageStat

try:
    import numpy as np  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    np = None

from build_structured_page_json import build_page_models_for_prepared_pages, resolve_recognition_worker_count
from assemble_page import extract_set_problem_range
from segment import draw_segment_debug
from edb_builder import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    CROP_FORMAT_V1,
    CROP_FORMAT_V2,
    DEFAULT_CROP_FORMAT,
    ImageRecordSpec,
    TextRecordSpec,
    V2_TARGET_IMAGE_WIDTH_PX,
    build_edb,
    build_image_record,
    build_preview_image_bytes,
    build_text_record,
    build_tight_crop_image_bytes,
    header_flag_for_crop_format,
    normalize_height_px,
    normalize_width_px,
    normalize_x_px,
    normalize_y_px,
    version_string_for_crop_format,
    write_edb,
)
from layout_template_schema import LayoutTemplate, ProblemLayoutInput
from image_reconstruction_backend import clean_problem_image_transparency
from page_repair import AIFallbackConfig, build_ai_fallback_config as build_page_ai_fallback_config
from page_repair import DEFAULT_GEMINI_REPAIR_MODEL
from placement_engine import place_problems
from preprocess import PreparedPage, prepare_source_pages
from structured_schema import BlockType, Box, ContentBlock, PageModel, ProblemUnit, Subject, save_pages_json


LEFT_MARGIN_PX = 84.0
TOP_PADDING_PX = 20.0
RIGHT_PADDING_PX = 54.0
PROBLEM_PADDING_PX = 18.0
MIN_HEIGHT_PAGES = 0.72
MAX_HEIGHT_PAGES = 4.8
PLACEMENT_SCALE_MIN = 0.6
PLACEMENT_SCALE_MAX = 1.6
MIN_PROBLEM_AREA_RATIO = 0.12
DOCUMENT_BAND_TOP_PADDING_PX = 14.0
DOCUMENT_BAND_BOTTOM_PADDING_PX = 8.0
DOCUMENT_BAND_NEXT_PROBLEM_GAP_PX = 6.0
V1_LAYOUT_MARGIN_X_PX = 24.0
V1_LAYOUT_MARGIN_Y_PX = 24.0
V1_LAYOUT_MAX_HEIGHT_PAGES = 1.08
V1_DEFAULT_DISPLAY_WIDTH_PX = 540.0
ONE_PROBLEM_SLOT_HEIGHT_PAGES = 1.2
CHOICE_BOTTOM_SAFE_PADDING_PX = 28.0
PROBLEM_CROP_BOTTOM_SAFE_PADDING_PX = 28
PROCESSING_STEP_RAW = "raw"
PROCESSING_STEP_ORIGINAL = "s1"
PROCESSING_STEP_CHALK = "s2"
PROCESSING_STEP_RECONSTRUCT = "s3"
PROCESSING_STEPS = {
    PROCESSING_STEP_RAW,
    PROCESSING_STEP_ORIGINAL,
    PROCESSING_STEP_CHALK,
    PROCESSING_STEP_RECONSTRUCT,
}
CLASSIN_PREFLIGHT_MIN_IMAGE_WIDTH_PX = 240
CLASSIN_PREFLIGHT_MIN_IMAGE_HEIGHT_PX = 120
CLASSIN_PREFLIGHT_INK_THRESHOLD = 245
CLASSIN_PREFLIGHT_MIN_DARK_PIXEL_RATIO = 0.002
CLASSIN_PREFLIGHT_PLACEMENT_OVERLAP_TOLERANCE_PAGES = 0.01
CLASSIN_PREFLIGHT_SOURCE_BBOX_OVERLAP_RATIO = 0.65
CLASSIN_PREFLIGHT_MAX_ISSUES = 50
CLASSIN_PREFLIGHT_NON_ACTIONABLE_REVIEW_RISK_FLAGS = {
    "fallback_grouping",
    "marker_document_continuation",
}
PASSAGE_CROSS_PAGE_MERGE_CHECK_RISK_FLAG = "passage_cross_page_merge_check"
RECONSTRUCT_TARGET_MIN_WIDTH_PX = 1600
RECONSTRUCT_MAX_UPSCALE = 3.5
# Brightness above this value (0-255) is treated as a light background that
# should be removed from the exported problem image.
DARK_BOARD_BRIGHTNESS_THRESHOLD = 160
DEFAULT_BOARD_THEME = "charcoal"
BOARD_THEME_PALETTES: dict[str, dict[str, tuple[int, int, int]]] = {
    "black": {
        "background": (10, 10, 12),
        "chalk": (250, 250, 248),
    },
    "charcoal": {
        "background": (24, 28, 32),
        "chalk": (248, 249, 246),
    },
    "green": {
        "background": (18, 42, 36),
        "chalk": (244, 248, 241),
    },
}


def _clamp_placement_x_ratio(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(1.0, float(value)))


def _clamp_placement_y_ratio(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(1.0, float(value)))


def _clamp_placement_scale_ratio(value: float | None) -> float | None:
    if value is None:
        return None
    return max(PLACEMENT_SCALE_MIN, min(PLACEMENT_SCALE_MAX, float(value)))


def _problem_origin_x_px(entry: "ProblemEntry", rendered_width_px: float) -> float:
    ratio = _clamp_placement_x_ratio(entry.placement_x_ratio)
    if ratio is None:
        return LEFT_MARGIN_PX
    max_x_px = max(LEFT_MARGIN_PX, CANVAS_HEIGHT - RIGHT_PADDING_PX - rendered_width_px)
    return LEFT_MARGIN_PX + ratio * (max_x_px - LEFT_MARGIN_PX)


def _problem_origin_y_px(entry: "ProblemEntry", placement: "ProblemPlacement", rendered_height_px: float) -> float:
    base_y_px = placement.start_y_pages * CANVAS_WIDTH + TOP_PADDING_PX
    ratio = _clamp_placement_y_ratio(entry.placement_y_ratio)
    if ratio is None:
        return base_y_px
    slot_bottom_y_px = placement.snapped_next_start_y_pages * CANVAS_WIDTH
    max_y_px = max(base_y_px, slot_bottom_y_px - rendered_height_px)
    return base_y_px + ratio * (max_y_px - base_y_px)


def _problem_scale_ratio(
    entry: "ProblemEntry",
    placement: "ProblemPlacement",
    rendered_width_px: float,
    rendered_height_px: float,
) -> float:
    requested = _clamp_placement_scale_ratio(entry.placement_scale_ratio)
    if requested is None:
        return 1.0
    max_width_scale = (CANVAS_HEIGHT - LEFT_MARGIN_PX - RIGHT_PADDING_PX) / max(rendered_width_px, 1.0)
    slot_height_px = max(
        rendered_height_px,
        (placement.snapped_next_start_y_pages - placement.start_y_pages) * CANVAS_WIDTH,
    )
    max_height_scale = slot_height_px / max(rendered_height_px, 1.0)
    max_scale = max(
        PLACEMENT_SCALE_MIN,
        min(PLACEMENT_SCALE_MAX, max_width_scale, max_height_scale),
    )
    return max(PLACEMENT_SCALE_MIN, min(max_scale, requested))


def _resolve_board_theme(board_theme: str | None) -> str:
    normalized = (board_theme or "").strip().lower()
    if normalized in BOARD_THEME_PALETTES:
        return normalized
    return DEFAULT_BOARD_THEME


def _resolve_chalk_color(board_theme: str | None) -> tuple[int, int, int]:
    """Return the chalk RGB for ``board_theme``. ClassIn paints its own dark
    chalkboard under transparent PNGs, so the only color we need to bake into
    each problem image is this chalk tone."""
    return BOARD_THEME_PALETTES[_resolve_board_theme(board_theme)]["chalk"]


def _normalize_processing_step(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in PROCESSING_STEPS else PROCESSING_STEP_RAW


# Minimum width (px) for a problem crop before chalk rendering. Smaller crops
# get upscaled with LANCZOS so OCR and the dark-board composite have enough
# pixel-detail to render legibly. Chosen empirically: 1024 px wide is roughly
# the width of a printed Korean exam problem at 200 DPI.
PROBLEM_CROP_TARGET_MIN_WIDTH_PX = 1024
PROBLEM_CROP_MAX_UPSCALE = 2.6
EDGE_GUIDE_SCAN_RATIO = 0.16
EDGE_GUIDE_SCAN_MAX_PX = 120
EDGE_GUIDE_DARK_THRESHOLD = 200
EDGE_GUIDE_MIN_COLUMN_RATIO = 0.55
EDGE_GUIDE_CLUSTER_MIN_COLUMN_RATIO = 0.035
EDGE_GUIDE_CLUSTER_MIN_COVERAGE_RATIO = 0.55
EDGE_GUIDE_CLUSTER_MAX_WIDTH_PX = 24
EDGE_GUIDE_CLUSTER_GAP_PX = 2
EDGE_GUIDE_TRIM_PADDING_PX = 4


def _trim_edge_vertical_guides(image: Image.Image) -> Image.Image:
    """Remove page/column guide lines that hug the left or right crop edge.

    We only trim nearly full-height dark vertical strokes inside the outer
    edge band so internal diagram axes and graph lines stay intact.
    """
    width, height = image.size
    if width <= 12 or height <= 12:
        return image

    gray = image.convert("L")
    if ImageStat.Stat(gray).mean[0] <= DARK_BOARD_BRIGHTNESS_THRESHOLD:
        return image

    pixels = gray.load()
    scan_width = min(int(round(width * EDGE_GUIDE_SCAN_RATIO)), EDGE_GUIDE_SCAN_MAX_PX)
    if scan_width <= 0:
        return image

    min_dark_pixels = int(round(height * EDGE_GUIDE_MIN_COLUMN_RATIO))
    min_cluster_column_pixels = max(4, int(round(height * EDGE_GUIDE_CLUSTER_MIN_COLUMN_RATIO)))
    min_cluster_coverage = int(round(height * EDGE_GUIDE_CLUSTER_MIN_COVERAGE_RATIO))

    def is_guide_column(x: int) -> bool:
        dark_count = 0
        for y in range(height):
            if pixels[x, y] <= EDGE_GUIDE_DARK_THRESHOLD:
                dark_count += 1
        return dark_count >= min_dark_pixels

    def find_slanted_guide_cluster(x_values: range) -> tuple[int, int] | None:
        candidates: list[tuple[int, int, set[int]]] = []
        for x in x_values:
            dark_rows: set[int] = set()
            for y in range(height):
                if pixels[x, y] <= EDGE_GUIDE_DARK_THRESHOLD:
                    dark_rows.add(y)
            if len(dark_rows) >= min_cluster_column_pixels:
                candidates.append((x, len(dark_rows), dark_rows))

        clusters: list[list[tuple[int, int, set[int]]]] = []
        current: list[tuple[int, int, set[int]]] = []
        for candidate in candidates:
            if current and candidate[0] > current[-1][0] + EDGE_GUIDE_CLUSTER_GAP_PX + 1:
                clusters.append(current)
                current = []
            current.append(candidate)
        if current:
            clusters.append(current)

        valid_clusters: list[tuple[int, int]] = []
        for cluster in clusters:
            start_x = min(item[0] for item in cluster)
            end_x = max(item[0] for item in cluster)
            if end_x - start_x + 1 > EDGE_GUIDE_CLUSTER_MAX_WIDTH_PX:
                continue
            covered_rows: set[int] = set()
            for item in cluster:
                covered_rows.update(item[2])
            if len(covered_rows) >= min_cluster_coverage:
                valid_clusters.append((start_x, end_x))

        if not valid_clusters:
            return None
        if x_values.start <= 0:
            return min(valid_clusters, key=lambda item: item[0])
        return max(valid_clusters, key=lambda item: item[1])

    left_trim = 0
    for x in range(scan_width):
        if is_guide_column(x):
            left_trim = max(left_trim, x + EDGE_GUIDE_TRIM_PADDING_PX + 1)
    left_cluster = find_slanted_guide_cluster(range(scan_width))
    if left_cluster is not None:
        left_trim = max(left_trim, left_cluster[1] + EDGE_GUIDE_TRIM_PADDING_PX + 1)

    right_trim = width
    for x in range(width - scan_width, width):
        if is_guide_column(x):
            right_trim = min(right_trim, x - EDGE_GUIDE_TRIM_PADDING_PX)
    right_cluster = find_slanted_guide_cluster(range(width - scan_width, width))
    if right_cluster is not None:
        right_trim = min(right_trim, right_cluster[0] - EDGE_GUIDE_TRIM_PADDING_PX)

    if left_trim <= 0 and right_trim >= width:
        return image
    if right_trim - left_trim < width * 0.75:
        return image
    return image.crop((max(0, left_trim), 0, min(width, right_trim), height))


def _pad_problem_crop_bottom(image: Image.Image, padding_px: int = PROBLEM_CROP_BOTTOM_SAFE_PADDING_PX) -> Image.Image:
    if padding_px <= 0 or image.width <= 0 or image.height <= 0:
        return image
    if "A" in image.getbands():
        fill = (255, 255, 255, 0)
        mode = "RGBA"
    else:
        fill = (255, 255, 255)
        mode = "RGB"
    converted = image.convert(mode)
    padded = Image.new(mode, (converted.width, converted.height + padding_px), fill)
    padded.paste(converted, (0, 0))
    return padded


def _enhance_problem_crop(
    image: Image.Image,
    *,
    target_min_width_px: int = PROBLEM_CROP_TARGET_MIN_WIDTH_PX,
    max_upscale: float = PROBLEM_CROP_MAX_UPSCALE,
) -> Image.Image:
    """Upscale small crops and sharpen ink so the chalk render reads cleanly.

    Run BEFORE alpha-extraction so the upscale uses the original ink edges
    rather than a binary cutout. Returns a new image; the input is not
    mutated.
    """
    if image.width <= 0 or image.height <= 0:
        return image

    has_alpha = "A" in image.getbands()
    converted = image.convert("RGBA" if has_alpha else "RGB")
    alpha = converted.getchannel("A") if has_alpha else None
    rgb = converted.convert("RGB")
    if rgb.width < target_min_width_px:
        scale = min(
            max_upscale,
            target_min_width_px / max(rgb.width, 1),
        )
        if scale > 1.05:
            new_size = (int(round(rgb.width * scale)), int(round(rgb.height * scale)))
            rgb = rgb.resize(new_size, Image.Resampling.LANCZOS)
            if alpha is not None:
                alpha = alpha.resize(new_size, Image.Resampling.LANCZOS)

    # Unsharp mask brings ink-on-paper transitions back after upscale and also
    # helps thin-stroke text survive the alpha-mask threshold inside
    # _extract_problem_cutout.
    sharpened = rgb.filter(ImageFilter.UnsharpMask(radius=1.4, percent=140, threshold=2))
    if alpha is not None:
        rgba = sharpened.convert("RGBA")
        rgba.putalpha(alpha)
        return rgba
    return sharpened


def _extract_problem_cutout(
    image: Image.Image,
    *,
    chalk_color: tuple[int, int, int] | None = None,
) -> Image.Image:
    """Recolor problem ink as chalk on a transparent canvas.

    The output is an RGBA PNG where ``alpha`` encodes how much ink the source
    crop has at each pixel and the RGB channels are uniformly the chalk
    color. ClassIn paints its own chalkboard background under transparent
    PNGs, so shipping the chalk-on-transparent cutout directly removes the
    legacy "composite onto a fake dark board" step — the original ink color
    would otherwise be invisible against ClassIn's real chalkboard.
    """
    resolved_chalk = (
        tuple(int(c) for c in chalk_color)
        if chalk_color is not None
        else BOARD_THEME_PALETTES[DEFAULT_BOARD_THEME]["chalk"]
    )

    original_alpha = image.getchannel("A") if "A" in image.getbands() else None
    has_existing_transparency = bool(original_alpha and original_alpha.getextrema()[0] < 245)
    cleaned, clean_stats = clean_problem_image_transparency(
        image,
        transparent_background=not has_existing_transparency,
        remove_corner_page_artifacts=True,
    )
    if "A" in cleaned.getbands():
        alpha_mask = cleaned.getchannel("A")
        alpha_min, alpha_max = alpha_mask.getextrema()
        if alpha_min < 245 and alpha_max > 12:
            # Reuse the model/paper background removal directly. This catches
            # both black model backgrounds and white PDF paper, and it removes
            # small lower-corner page number badges before they get encoded.
            if np is not None:
                alpha_array = np.asarray(alpha_mask, dtype=np.float32) / 255.0
                if clean_stats.get("background_kind") == "light":
                    dilated = np.copy(alpha_array)
                    for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                        shifted = np.roll(np.roll(alpha_array, dy, axis=0), dx, axis=1)
                        if dy < 0:
                            shifted[-1, :] = 0.0
                        elif dy > 0:
                            shifted[0, :] = 0.0
                        if dx < 0:
                            shifted[:, -1] = 0.0
                        elif dx > 0:
                            shifted[:, 0] = 0.0
                        dilated = np.maximum(dilated, shifted)
                    alpha_array = np.clip((0.75 * alpha_array) + (0.25 * dilated), 0.0, 1.0)
                return _compose_chalk_rgba(alpha_array, resolved_chalk)
            return _compose_chalk_rgba_pil(alpha_mask, cleaned.size, resolved_chalk)

    rgb = image.convert("RGB")
    gray = ImageOps.autocontrast(rgb.convert("L"))
    stat = ImageStat.Stat(gray)
    mean_brightness = stat.mean[0]

    if mean_brightness <= DARK_BOARD_BRIGHTNESS_THRESHOLD:
        # Source is already dark-background (e.g. a screen capture of a
        # chalkboard) — bright pixels are the ink so alpha follows brightness.
        if np is not None:
            np_alpha = np.asarray(gray, dtype=np.float32) / 255.0
            return _compose_chalk_rgba(np_alpha, resolved_chalk)
        return _compose_chalk_rgba_pil(gray, image.size, resolved_chalk)

    if np is None:
        mask = gray.point(lambda px: 255 if px < 242 else 0, mode="L")
        mask_dilated = mask.filter(ImageFilter.MaxFilter(3))
        mask = Image.blend(mask, mask_dilated, 0.35)
        return _compose_chalk_rgba_pil(mask, image.size, resolved_chalk)

    rgb_array = np.asarray(rgb, dtype=np.float32) / 255.0
    gray_array = np.asarray(gray, dtype=np.float32)
    darkness = 255.0 - gray_array

    # Thicken thin math lines/symbols via a fast 4-connectivity morphological dilation in NumPy
    dilated_darkness = np.copy(darkness)
    for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        shifted = np.roll(np.roll(darkness, dy, axis=0), dx, axis=1)
        if dy < 0:
            shifted[-1, :] = 0.0
        elif dy > 0:
            shifted[0, :] = 0.0
        if dx < 0:
            shifted[:, -1] = 0.0
        elif dx > 0:
            shifted[:, 0] = 0.0
        dilated_darkness = np.maximum(dilated_darkness, shifted)

    # Smooth blend to keep edge anti-aliasing while keeping thin lines bright
    darkness = 0.65 * darkness + 0.35 * dilated_darkness

    noise_floor = max(10.0, float(np.percentile(darkness, 62)) + 4.0)
    alpha_strength = np.clip((darkness - noise_floor) / max(1.0, 255.0 - noise_floor), 0.0, 1.0)
    alpha_strength = np.power(np.clip(alpha_strength * 1.45, 0.0, 1.0), 0.7)

    max_channel = rgb_array.max(axis=2)
    whiteness = gray_array / 255.0
    color_distance = np.linalg.norm(1.0 - rgb_array, axis=2) / np.sqrt(3.0)
    keep_color = np.clip((color_distance - 0.035) / 0.42, 0.0, 1.0)
    keep_dark = np.clip((1.0 - whiteness - 0.08) / 0.7, 0.0, 1.0)
    alpha = np.maximum(alpha_strength, np.maximum(keep_color * 0.92, keep_dark))
    alpha = np.where(max_channel > 0.985, alpha * 0.08, alpha)

    return _compose_chalk_rgba(alpha, resolved_chalk)


def _compose_chalk_rgba(alpha_array, chalk_color: tuple[int, int, int]) -> Image.Image:
    """RGBA from a numpy alpha array (values 0..1) + uniform chalk RGB."""
    if np is None:
        raise RuntimeError("numpy is required for _compose_chalk_rgba")
    height, width = alpha_array.shape
    rgba = np.empty((height, width, 4), dtype=np.uint8)
    rgba[..., 0] = int(chalk_color[0])
    rgba[..., 1] = int(chalk_color[1])
    rgba[..., 2] = int(chalk_color[2])
    rgba[..., 3] = np.clip(alpha_array * 255.0, 0.0, 255.0).astype(np.uint8)
    return Image.fromarray(rgba, mode="RGBA")


def _compose_chalk_rgba_pil(
    alpha_mask: Image.Image, size: tuple[int, int], chalk_color: tuple[int, int, int]
) -> Image.Image:
    """PIL-only fallback for :func:`_compose_chalk_rgba`."""
    chalk = Image.new("RGBA", size, chalk_color + (0,))
    alpha = alpha_mask if alpha_mask.size == size else alpha_mask.resize(size)
    chalk.putalpha(alpha)
    return chalk


def _prepare_image_for_dark_board(image: Image.Image, *, board_theme: str = DEFAULT_BOARD_THEME) -> Image.Image:
    """Deprecated. ClassIn paints its own chalkboard background under
    transparent PNGs, so we ship the chalk-on-transparent cutout directly.
    Kept as a thin shim so any external callers keep working."""
    return _extract_problem_cutout(_enhance_problem_crop(image), chalk_color=_resolve_chalk_color(board_theme))


def _write_render_image(image: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
    return path


def _composite_on_board_background(image: Image.Image, *, board_theme: str = DEFAULT_BOARD_THEME) -> Image.Image:
    rgba = image.convert("RGBA")
    background = Image.new(
        "RGBA",
        rgba.size,
        BOARD_THEME_PALETTES[_resolve_board_theme(board_theme)]["background"] + (255,),
    )
    background.alpha_composite(rgba)
    return background.convert("RGB")


def _load_board_export_image(
    board_render_path: Path,
    crop_image: Image.Image,
    *,
    board_theme: str = DEFAULT_BOARD_THEME,
    target_size: tuple[int, int] | None = None,
) -> Image.Image:
    try:
        rendered = Image.open(board_render_path)
    except OSError:
        rendered = None

    if rendered is not None:
        if target_size is not None and rendered.size != target_size:
            rendered = rendered.resize(target_size, Image.Resampling.LANCZOS)
        if "A" in rendered.getbands():
            cleaned, _stats = clean_problem_image_transparency(
                rendered,
                transparent_background=rendered.getchannel("A").getextrema()[0] >= 245,
                remove_corner_page_artifacts=True,
            )
            return cleaned
        rendered_rgb = rendered.convert("RGB")
        mean_brightness = ImageStat.Stat(rendered_rgb.convert("L")).mean[0]
        if mean_brightness <= DARK_BOARD_BRIGHTNESS_THRESHOLD:
            return _extract_problem_cutout(
                rendered_rgb,
                chalk_color=_resolve_chalk_color(board_theme),
            )

    cutout = _extract_problem_cutout(
        _enhance_problem_crop(crop_image),
        chalk_color=_resolve_chalk_color(board_theme),
    )
    if target_size is not None and cutout.size != target_size:
        cutout = cutout.resize(target_size, Image.Resampling.LANCZOS)
    return cutout


def _build_transparent_reconstruction_image(
    crop_image: Image.Image,
    *,
    board_theme: str = DEFAULT_BOARD_THEME,
) -> Image.Image:
    enhanced_crop = _enhance_problem_crop(
        crop_image,
        target_min_width_px=RECONSTRUCT_TARGET_MIN_WIDTH_PX,
        max_upscale=RECONSTRUCT_MAX_UPSCALE,
    )
    return _extract_problem_cutout(enhanced_crop, chalk_color=_resolve_chalk_color(board_theme))


def _encode_image_bytes(image: Image.Image, quality: int = 92) -> tuple[bytes, str]:
    """Encode a PIL image for use in an EDB image record."""
    buf = io.BytesIO()
    has_alpha = "A" in image.getbands()
    if has_alpha:
        image.save(buf, format="PNG")
        return buf.getvalue(), "PNG"
    image.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue(), "JPEG"


TEXT_ELIGIBLE_BLOCK_TYPES = {
    BlockType.TITLE,
    BlockType.SECTION,
    BlockType.STEM,
    BlockType.CHOICE,
    BlockType.EXPLANATION,
    BlockType.NOTE,
}
IMAGE_ONLY_BLOCK_TYPES = {
    BlockType.IMAGE,
    BlockType.DIAGRAM,
    BlockType.TABLE,
    BlockType.DECORATION,
    BlockType.FORMULA,
}


@dataclass(slots=True)
class ProblemEntry:
    problem_id: str
    title: str
    problem_number: int | None
    subject: Subject
    source_page_id: str
    source_path: str
    prepared_page: PreparedPage
    bounds: Box
    crop_path: Path
    board_render_path: Path
    blocks: list[ContentBlock]
    actual_height_pages: float
    overflow_allowed: bool
    reading_heavy: bool
    risk_flags: list[str]
    placement_x_ratio: float | None = None
    placement_y_ratio: float | None = None
    placement_scale_ratio: float | None = None
    processing_step: str = PROCESSING_STEP_RAW


@dataclass(slots=True)
class _ProblemAssetTask:
    source_image: Image.Image
    bounds: Box
    crop_path: Path
    board_render_path: Path
    chalk_color: tuple[int, int, int]


@dataclass(slots=True)
class _ProblemEntryDraft:
    problem_id: str
    title: str
    problem_number: int | None
    subject: Subject
    source_page_id: str
    source_path: str
    prepared_page: PreparedPage
    bounds: Box
    crop_path: Path
    board_render_path: Path
    blocks: list[ContentBlock]
    overflow_allowed: bool
    reading_heavy: bool
    risk_flags: list[str]
    asset_task: _ProblemAssetTask


def _resolve_problem_asset_worker_count(task_count: int) -> int:
    if task_count <= 1:
        return 1
    return min(4, task_count)


def _render_problem_asset(task: _ProblemAssetTask) -> tuple[int, int]:
    crop = task.source_image.crop(
        (
            int(task.bounds.left),
            int(task.bounds.top),
            int(task.bounds.right),
            int(task.bounds.bottom),
        )
    )
    crop = _trim_edge_vertical_guides(crop)
    crop = _pad_problem_crop_bottom(crop)
    task.crop_path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(task.crop_path)
    enhanced_crop = _enhance_problem_crop(crop)
    cutout_image = _extract_problem_cutout(enhanced_crop, chalk_color=task.chalk_color)
    _write_render_image(cutout_image, task.board_render_path)
    return crop.size


def _render_problem_assets(tasks: list[_ProblemAssetTask]) -> list[tuple[int, int]]:
    worker_count = _resolve_problem_asset_worker_count(len(tasks))
    if worker_count <= 1:
        return [_render_problem_asset(task) for task in tasks]
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        return list(executor.map(_render_problem_asset, tasks))


def resolve_subject(name: str | None) -> Subject:
    if not name:
        return Subject.UNKNOWN
    try:
        return Subject(name.lower())
    except ValueError:
        return Subject.UNKNOWN


def iter_problem_block_ids(page: PageModel, problem: ProblemUnit) -> list[str]:
    ordered: list[str] = []
    for block_id in (
        *problem.stem_block_ids,
        *problem.choice_block_ids,
        *problem.explanation_block_ids,
        *problem.figure_block_ids,
    ):
        if block_id not in ordered:
            ordered.append(block_id)
    if ordered:
        return ordered
    return [block.block_id for block in page.blocks]


def merge_boxes(
    boxes: list[Box],
    *,
    page_width: int,
    page_height: int,
    padding_px: int = PROBLEM_PADDING_PX,
    top_padding_px: int | None = None,
    bottom_padding_px: int | float | None = None,
) -> Box:
    left = min(box.left for box in boxes)
    top = min(box.top for box in boxes)
    right = max(box.right for box in boxes)
    bottom = max(box.bottom for box in boxes)
    resolved_top_padding = padding_px if top_padding_px is None else top_padding_px
    resolved_bottom_padding = padding_px if bottom_padding_px is None else bottom_padding_px
    return Box.from_points(
        max(0.0, left - float(padding_px)),
        max(0.0, top - float(resolved_top_padding)),
        min(float(page_width), right + float(padding_px)),
        min(float(page_height), bottom + float(resolved_bottom_padding)),
    )


def estimate_height_pages(image_size: tuple[int, int], template: LayoutTemplate) -> float:
    width_px, height_px = image_size
    available_width_px = CANVAS_HEIGHT * template.fixed_left_zone_ratio - LEFT_MARGIN_PX - RIGHT_PADDING_PX
    scaled_height_px = available_width_px * (height_px / max(width_px, 1))
    estimated = scaled_height_px / CANVAS_WIDTH
    return max(MIN_HEIGHT_PAGES, min(MAX_HEIGHT_PAGES, estimated))


def build_pages(
    source: str | Path,
    *,
    subject: Subject,
    ocr_mode: str,
    ai_fallback_config: dict[str, Any] | None,
    pdf_dpi: int,
    detect_perspective: bool,
    deskew: bool,
    crop_margins: bool,
    max_dimension: int | None,
    debug_segments_dir: Path | None = None,
) -> tuple[list[PreparedPage], list[PageModel]]:
    prepared_pages = prepare_source_pages(
        source,
        pdf_dpi=pdf_dpi,
        detect_perspective=detect_perspective,
        deskew=deskew,
        crop_margins=crop_margins,
        max_dimension=max_dimension,
    )
    page_ai_config = _to_page_ai_config(ai_fallback_config)
    page_models = build_page_models_for_prepared_pages(
        prepared_pages,
        subject=subject,
        ocr_mode=ocr_mode,
        ai_config=page_ai_config,
    )
    if debug_segments_dir is not None:
        for prepared_page, page in zip(prepared_pages, page_models):
            debug_path = debug_segments_dir / f"{page.page_id}_segments.png"
            draw_segment_debug(prepared_page, page.blocks, debug_path)
    return prepared_pages, page_models


def _force_single_problem_per_page(pages: list[PageModel], *, input_intent: str) -> list[PageModel]:
    forced_pages: list[PageModel] = []
    title_prefix = "페이지" if input_intent == "page-as-is" else "문항"

    for index, page in enumerate(pages, start=1):
        ordered_blocks = page.sorted_blocks()
        block_ids = [block.block_id for block in ordered_blocks]
        unit_metadata: dict[str, Any] = {
            "grouping_source": "user_intent",
            "grouping_reason": [input_intent],
            "force_full_page_bounds": True,
            "input_intent": input_intent,
        }
        if input_intent == "single-problem":
            unit_metadata["problem_number"] = index
            unit_metadata["problem_number_source"] = "user_intent"

        forced_problem = ProblemUnit(
            unit_id=f"{page.page_id}-problem-1",
            subject=page.subject,
            title=f"{title_prefix} {index}",
            stem_block_ids=block_ids,
            metadata=unit_metadata,
        )
        page_metadata = dict(page.metadata)
        page_metadata.pop("route_decision", None)
        page_metadata["input_intent"] = input_intent
        page_metadata["grouping_source"] = "user_intent"
        page_metadata["grouping_mode"] = "single_page"
        page_metadata["forced_single_problem"] = True
        forced_pages.append(
            PageModel(
                page_id=page.page_id,
                width_px=page.width_px,
                height_px=page.height_px,
                subject=page.subject,
                source_path=page.source_path,
                blocks=list(page.blocks),
                problems=[forced_problem],
                metadata=page_metadata,
            )
        )
    return forced_pages


def _iter_problem_block_ids_raw(problem: ProblemUnit) -> list[str]:
    """Like iter_problem_block_ids but without the page-level fallback —
    used for purely problem-owned blocks (for top-y bookkeeping)."""
    ordered: list[str] = []
    for block_id in (
        *problem.stem_block_ids,
        *problem.choice_block_ids,
        *problem.explanation_block_ids,
        *problem.figure_block_ids,
    ):
        if block_id not in ordered:
            ordered.append(block_id)
    return ordered


def _problem_top_y(problem: ProblemUnit, block_by_id: dict[str, ContentBlock]) -> float:
    ids = _iter_problem_block_ids_raw(problem)
    blocks = [block_by_id[bid] for bid in ids if bid in block_by_id]
    if not blocks:
        return 0.0
    return min(block.bbox.top for block in blocks)


def _coerce_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _coerce_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _problem_first_block(problem: ProblemUnit, block_by_id: dict[str, ContentBlock]) -> ContentBlock | None:
    ids = _iter_problem_block_ids_raw(problem)
    blocks = [block_by_id[bid] for bid in ids if bid in block_by_id]
    if not blocks:
        return None
    return min(blocks, key=lambda block: (block.bbox.top, block.bbox.left, block.reading_order))


def _problem_column_value(problem: ProblemUnit, block_by_id: dict[str, ContentBlock]) -> int | None:
    value = _coerce_int(problem.metadata.get("column_index"))
    if value is not None:
        return value
    first_block = _problem_first_block(problem, block_by_id)
    if first_block is None:
        return None
    return _coerce_int(first_block.metadata.get("column_index"))


def _block_column_value(block: ContentBlock) -> int | None:
    return _coerce_int(block.metadata.get("column_index"))


def _problem_band_value(problem: ProblemUnit, block_by_id: dict[str, ContentBlock]) -> int:
    value = _coerce_int(problem.metadata.get("question_band_index"))
    if value is not None:
        return value
    first_block = _problem_first_block(problem, block_by_id)
    if first_block is None:
        return 0
    return _coerce_int(first_block.metadata.get("question_band_index")) or 0


def _problem_left_x(problem: ProblemUnit, block_by_id: dict[str, ContentBlock]) -> float:
    first_block = _problem_first_block(problem, block_by_id)
    return first_block.bbox.left if first_block is not None else 0.0


def _problem_order_key(problem: ProblemUnit, block_by_id: dict[str, ContentBlock]) -> tuple[object, ...]:
    raw_number = problem.metadata.get("problem_number")
    if isinstance(raw_number, int):
        return (0, raw_number, problem.unit_id)
    if isinstance(raw_number, str) and raw_number.isdigit():
        return (0, int(raw_number), problem.unit_id)

    column_value = _problem_column_value(problem, block_by_id) or 0
    band_value = _problem_band_value(problem, block_by_id)
    return (1, column_value, band_value, _problem_top_y(problem, block_by_id), problem.unit_id)


def _build_crop_next_problem_map(
    problems: list[ProblemUnit],
    block_by_id: dict[str, ContentBlock],
) -> dict[str, ProblemUnit]:
    """Map each problem to the next problem in the same detected column.

    Final output remains number-ordered, but crop boundaries are spatial: in a
    two-column worksheet, problem 9 should not use problem 10's top as its
    lower boundary just because 10 is the next number.
    """
    grouped: dict[int | None, list[ProblemUnit]] = {}
    for problem in problems:
        grouped.setdefault(_problem_column_value(problem, block_by_id), []).append(problem)

    next_by_id: dict[str, ProblemUnit] = {}
    for group in grouped.values():
        spatial_order = sorted(
            group,
            key=lambda problem: (
                _problem_top_y(problem, block_by_id),
                _problem_band_value(problem, block_by_id),
                _problem_left_x(problem, block_by_id),
                problem.unit_id,
            ),
        )
        for current, next_problem in zip(spatial_order, spatial_order[1:]):
            next_by_id[current.unit_id] = next_problem
    return next_by_id


def _expand_problem_blocks_by_gap(
    page: PageModel,
    problem: ProblemUnit,
    next_problem: ProblemUnit | None,
    block_by_id: dict[str, ContentBlock],
    other_problem_block_ids: set[str],
) -> list[ContentBlock]:
    """Include every page block whose vertical centre falls between this
    problem's start and the next problem's start.

    The grouping pass leaves choice / figure blocks orphaned when segmentation
    splits them into separate bands or columns. Filling the vertical gap
    between consecutive problem-starts ensures the crop captures the full
    question + figure + choices set.
    """
    own_ids = set(_iter_problem_block_ids_raw(problem))
    own_blocks = [block_by_id[bid] for bid in own_ids if bid in block_by_id]
    if not own_blocks:
        return []

    start_y = min(block.bbox.top for block in own_blocks)
    if next_problem is None:
        end_y = float(page.height_px)
    else:
        next_top = _problem_top_y(next_problem, block_by_id)
        end_y = next_top if next_top > start_y else float(page.height_px)

    problem_column = _problem_column_value(problem, block_by_id)
    included: list[ContentBlock] = []
    for block in page.blocks:
        if block.block_id in own_ids:
            included.append(block)
            continue
        if block.block_id in other_problem_block_ids:
            continue
        block_column = _block_column_value(block)
        if problem_column is not None and block_column is not None and block_column != problem_column:
            continue
        centre = (block.bbox.top + block.bbox.bottom) / 2.0
        if start_y <= centre < end_y:
            included.append(block)
    return included


def _clamp_box_to_next_problem(
    box: Box,
    next_problem: ProblemUnit | None,
    block_by_id: dict[str, ContentBlock],
    *,
    min_bottom: float | None = None,
) -> Box:
    if next_problem is None:
        return box
    next_top = _problem_top_y(next_problem, block_by_id)
    if next_top <= box.top + 1.0:
        return box
    limit = max(box.top + 1.0, next_top - DOCUMENT_BAND_NEXT_PROBLEM_GAP_PX)
    if min_bottom is not None:
        limit = max(limit, min(float(min_bottom), box.bottom))
    if box.bottom <= limit:
        return box
    return Box.from_points(box.left, box.top, box.right, limit)


def _problem_metadata_bbox(problem: ProblemUnit, page: PageModel) -> Box | None:
    raw = problem.metadata.get("bbox_px")
    if not isinstance(raw, dict):
        ai_unit = problem.metadata.get("ai_problem_unit")
        if isinstance(ai_unit, dict):
            raw = ai_unit.get("bbox_px")
    if not isinstance(raw, dict):
        return None

    try:
        left = float(raw.get("left", 0.0))
        top = float(raw.get("top", 0.0))
        width = float(raw.get("width", 0.0))
        height = float(raw.get("height", 0.0))
    except (TypeError, ValueError):
        return None
    if width <= 1.0 or height <= 1.0:
        return None

    right = min(float(page.width_px), max(0.0, left + width))
    bottom = min(float(page.height_px), max(0.0, top + height))
    left = max(0.0, min(float(page.width_px), left))
    top = max(0.0, min(float(page.height_px), top))
    if right <= left + 1.0 or bottom <= top + 1.0:
        return None
    return Box.from_points(left, top, right, bottom)


def _should_prefer_problem_metadata_bbox(problem: ProblemUnit) -> bool:
    if problem.metadata.get("grouping_source") != "ai_fallback":
        return False
    flags: list[str] = []
    raw_flags = problem.metadata.get("review_flags")
    if isinstance(raw_flags, list):
        flags.extend(str(flag) for flag in raw_flags)
    ai_unit = problem.metadata.get("ai_problem_unit")
    if isinstance(ai_unit, dict) and isinstance(ai_unit.get("review_flags"), list):
        flags.extend(str(flag) for flag in ai_unit["review_flags"])
    return "uncertain_bbox" not in {flag.strip() for flag in flags}


def _append_problem_block_ids(target: list[str], values: Sequence[str]) -> None:
    for value in values:
        if value and value not in target:
            target.append(value)


def _merge_pre_question_passage_continuations(
    target: ProblemUnit,
    continuations: Sequence[ProblemUnit],
) -> None:
    merged_block_ids: list[str] = []
    for continuation in continuations:
        _append_problem_block_ids(target.stem_block_ids, continuation.stem_block_ids)
        _append_problem_block_ids(target.choice_block_ids, continuation.choice_block_ids)
        _append_problem_block_ids(target.explanation_block_ids, continuation.explanation_block_ids)
        _append_problem_block_ids(target.figure_block_ids, continuation.figure_block_ids)
        _append_problem_block_ids(merged_block_ids, _iter_problem_block_ids_raw(continuation))
    if merged_block_ids:
        target.metadata["passage_pre_question_continuation_block_ids"] = merged_block_ids


def _problem_has_number(problem: ProblemUnit) -> bool:
    raw = problem.metadata.get("problem_number")
    return (isinstance(raw, int) and raw >= 1) or (isinstance(raw, str) and raw.isdigit())


def _problem_is_spatially_before(
    candidate: ProblemUnit,
    target: ProblemUnit,
    block_by_id: dict[str, ContentBlock],
) -> bool:
    target_top = _problem_top_y(target, block_by_id)
    candidate_top = _problem_top_y(candidate, block_by_id)
    if candidate_top >= target_top:
        return False
    target_column = _problem_column_value(target, block_by_id)
    candidate_column = _problem_column_value(candidate, block_by_id)
    return target_column is None or candidate_column is None or target_column == candidate_column


def _drop_pre_first_problem_headers(
    problems: list[ProblemUnit],
    block_by_id: dict[str, ContentBlock],
) -> list[ProblemUnit]:
    """Strip page-chrome ProblemUnits that precede the first numbered problem.

    Cover-page titles, exam form fields (성명 / 수험번호), and subject
    headers (물리학I / 과학탐구 / 수학II) often surface as their own
    ProblemUnits — especially in fallback grouping where every band becomes a
    pseudo-problem. They land above the first real numbered problem and have
    no ``problem_number``, so we trim them. If the page has no numbered
    problem at all we leave the list untouched: that's the best-effort
    behaviour while OCR is unavailable.
    """
    if not problems:
        return problems

    first_numbered_index: int | None = None
    for index, problem in enumerate(problems):
        if _problem_has_number(problem):
            first_numbered_index = index
            break

    if first_numbered_index is None:
        return problems

    first_numbered = problems[first_numbered_index]
    if _problem_passage_continues_across_pages(first_numbered.metadata):
        continuation_problems = [
            problem
            for index, problem in enumerate(problems)
            if index != first_numbered_index
            and not _problem_has_number(problem)
            and _problem_is_spatially_before(problem, first_numbered, block_by_id)
        ]
        if continuation_problems:
            _merge_pre_question_passage_continuations(first_numbered, continuation_problems)
            continuation_ids = {id(problem) for problem in continuation_problems}
            return [problem for problem in problems if id(problem) not in continuation_ids]

    if first_numbered_index == 0:
        return problems

    return problems[first_numbered_index:]


def _problem_metadata_number(problem: ProblemUnit) -> int | None:
    raw = problem.metadata.get("problem_number")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int) and raw >= 1:
        return raw
    if isinstance(raw, str) and raw.isdigit():
        number = int(raw)
        return number if number >= 1 else None
    return None


def _page_has_own_pdf_problem_markers(metadata: dict[str, Any]) -> bool:
    if str(metadata.get("segmenter") or "") == "pdf-text-markers":
        return True
    marker_count = _coerce_int(metadata.get("pdf_text_marker_count"))
    if marker_count is not None and marker_count > 0:
        return True
    markers = metadata.get("pdf_problem_markers")
    return isinstance(markers, list) and len(markers) > 0


def _hwp_conversion_has_pdf_problem_markers(metadata: dict[str, Any]) -> bool:
    quality = metadata.get("hwp_conversion_quality")
    if not isinstance(quality, dict):
        return False
    if "pdf_text_markers_reliable" in quality:
        return bool(quality.get("pdf_text_markers_reliable"))
    if bool(quality.get("has_pdf_text_markers")):
        return True
    marker_count = _coerce_int(quality.get("pdf_text_marker_count"))
    if marker_count is not None and marker_count > 0:
        return True
    hwp_layout_marker_count = _coerce_int(quality.get("hwp_layout_problem_marker_count"))
    return hwp_layout_marker_count is not None and hwp_layout_marker_count > 0


def _is_document_band_problem(
    problem: ProblemUnit,
    block_by_id: dict[str, ContentBlock],
) -> bool:
    block_ids = _iter_problem_block_ids_raw(problem)
    if not block_ids:
        return False
    blocks = [block_by_id[block_id] for block_id in block_ids if block_id in block_by_id]
    if not blocks:
        return False
    return all(
        block.metadata.get("segmenter") == "document-bands"
        or "question_band_index" in block.metadata
        for block in blocks
    )


def _is_marker_document_continuation_page(
    page: PageModel,
    problems: list[ProblemUnit],
    block_by_id: dict[str, ContentBlock],
) -> bool:
    if not problems:
        return False
    if not _hwp_conversion_has_pdf_problem_markers(page.metadata):
        return False
    if _page_has_own_pdf_problem_markers(page.metadata):
        return False
    if any(_problem_metadata_number(problem) is not None for problem in problems):
        return False
    return all(_is_document_band_problem(problem, block_by_id) for problem in problems)


def _problem_has_fallback_grouping(problem: ProblemUnit) -> bool:
    return bool(problem.metadata.get("fallback_grouping"))


def _continuation_block_ids_by_role(page: PageModel) -> tuple[list[str], list[str], list[str], list[str]]:
    stem_ids: list[str] = []
    choice_ids: list[str] = []
    explanation_ids: list[str] = []
    figure_ids: list[str] = []

    for block in page.sorted_blocks():
        if block.block_type == BlockType.CHOICE:
            choice_ids.append(block.block_id)
        elif block.block_type == BlockType.EXPLANATION:
            explanation_ids.append(block.block_id)
        elif block.block_type in IMAGE_ONLY_BLOCK_TYPES:
            figure_ids.append(block.block_id)
        else:
            stem_ids.append(block.block_id)

    return stem_ids, choice_ids, explanation_ids, figure_ids


def _collapse_marker_document_continuation_page(
    page: PageModel,
    problems: list[ProblemUnit],
    block_by_id: dict[str, ContentBlock],
) -> list[ProblemUnit] | None:
    if not _is_marker_document_continuation_page(page, problems, block_by_id):
        return None

    page.metadata["marker_document_continuation_detected"] = True
    if (
        len(problems) == 1
        and problems[0].unit_id == f"{page.page_id}-continuation"
        and _is_marker_document_continuation_problem(problems[0])
    ):
        page.metadata["marker_document_continuation_preserved"] = True
        page.metadata.pop("problem_entry_skip_reason", None)
        page.metadata.pop("problem_crop_skip_reason", None)
        return problems

    if not any(_problem_has_fallback_grouping(problem) for problem in problems):
        page.metadata["problem_entry_skip_reason"] = "marker_document_continuation"
        return []

    stem_ids, choice_ids, explanation_ids, figure_ids = _continuation_block_ids_by_role(page)
    if not any((stem_ids, choice_ids, explanation_ids, figure_ids)):
        page.metadata["problem_entry_skip_reason"] = "marker_document_continuation"
        return []

    page.metadata["marker_document_continuation_preserved"] = True
    page.metadata.pop("problem_entry_skip_reason", None)
    page.metadata.pop("problem_crop_skip_reason", None)
    return [
        ProblemUnit(
            unit_id=f"{page.page_id}-continuation",
            subject=page.subject,
            title="이어지는 자료",
            stem_block_ids=stem_ids,
            choice_block_ids=choice_ids,
            explanation_block_ids=explanation_ids,
            figure_block_ids=figure_ids,
            metadata={
                "fallback_grouping": True,
                "grouping_mode": "fallback",
                "grouping_source": "marker_document_continuation",
                "marker_document_continuation": True,
                "source_problem_ids": [problem.unit_id for problem in problems],
                "force_full_page_bounds": True,
                "bbox_px": {
                    "left": 0.0,
                    "top": 0.0,
                    "width": float(page.width_px),
                    "height": float(page.height_px),
                },
                "risk_flags": ["marker_document_continuation"],
            },
        )
    ]


def _marker_document_dedupe_scope(page: PageModel) -> str:
    for key in ("source_hwp_path", "original_source_path", "source_pdf_path", "converted_pdf_path"):
        value = page.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(page.source_path or "__unknown_source__")


def _hwp_text_signal_count_for_dedupe(page: PageModel) -> int:
    quality = page.metadata.get("hwp_conversion_quality")
    if not isinstance(quality, dict):
        return 0
    numbered = _coerce_int(quality.get("hwp_text_numbered_problem_count")) or 0
    stem = _coerce_int(quality.get("hwp_text_stem_problem_count")) or 0
    return max(numbered, stem)


def _marker_document_duplicate_number_scopes_to_preserve(pages: list[PageModel]) -> set[str]:
    by_scope: dict[str, dict[str, Any]] = {}
    for page in pages:
        if not _hwp_conversion_has_pdf_problem_markers(page.metadata):
            continue
        scope = _marker_document_dedupe_scope(page)
        bucket = by_scope.setdefault(scope, {"numbers": [], "signal": 0})
        bucket["signal"] = max(int(bucket["signal"]), _hwp_text_signal_count_for_dedupe(page))
        for problem in page.problems:
            number = _problem_metadata_number(problem)
            if number is not None:
                bucket["numbers"].append(number)

    preserve: set[str] = set()
    for scope, bucket in by_scope.items():
        numbers = list(bucket["numbers"])
        signal = int(bucket["signal"])
        if signal > len(set(numbers)):
            preserve.add(scope)
    return preserve


def _remove_duplicate_marker_document_problem_numbers(pages: list[PageModel]) -> None:
    preserve_duplicate_scopes = _marker_document_duplicate_number_scopes_to_preserve(pages)
    seen_by_scope: dict[str, set[int]] = {}
    for page in pages:
        if not _hwp_conversion_has_pdf_problem_markers(page.metadata):
            continue
        scope = _marker_document_dedupe_scope(page)
        if scope in preserve_duplicate_scopes:
            page.metadata["duplicate_problem_numbers_preserved"] = True
            continue
        seen = seen_by_scope.setdefault(scope, set())
        retained: list[ProblemUnit] = []
        skipped: list[int] = []
        for problem in page.problems:
            number = _problem_metadata_number(problem)
            if number is not None and number in seen:
                skipped.append(number)
                continue
            if number is not None:
                seen.add(number)
            retained.append(problem)
        if skipped:
            page.problems = retained
            page.metadata["duplicate_problem_numbers_skipped"] = skipped


def _has_hwp_template_instruction_text(page: PageModel) -> bool:
    if page.metadata.get("source_type") != "hwp":
        return False
    text = str(page.metadata.get("hwp_preview_text") or "")
    if not text:
        return False
    markers = (
        "개요 번호 모양",
        "Ctrl+3",
        "Ctrl+4",
        "복사 붙여넣",
        "위 네모칸",
    )
    return sum(1 for marker in markers if marker in text) >= 2


def _remove_hwp_template_instruction_problems(pages: list[PageModel]) -> None:
    for page in pages:
        if not _has_hwp_template_instruction_text(page):
            continue
        if _hwp_conversion_has_pdf_problem_markers(page.metadata):
            continue
        if len(page.problems) < 2:
            continue

        block_by_id = {block.block_id: block for block in page.blocks}
        ordered = sorted(page.problems, key=lambda problem: _problem_order_key(problem, block_by_id))
        retained: list[ProblemUnit] = []
        skipped_ids: list[str] = []
        skipped_tops: list[float] = []
        for index, problem in enumerate(ordered):
            if index == 0:
                retained.append(problem)
                continue
            if _problem_metadata_number(problem) is not None:
                retained.append(problem)
                continue
            if not _is_document_band_problem(problem, block_by_id):
                retained.append(problem)
                continue
            for block_id in _iter_problem_block_ids_raw(problem):
                block = block_by_id.get(block_id)
                if block is not None:
                    skipped_tops.append(block.bbox.top)
            skipped_ids.append(problem.unit_id)

        if skipped_ids:
            if retained and skipped_tops:
                first_problem = retained[0]
                first_blocks = [
                    block_by_id[block_id]
                    for block_id in _iter_problem_block_ids_raw(first_problem)
                    if block_id in block_by_id
                ]
                if first_blocks:
                    crop_bottom = max(block.bbox.bottom for block in first_blocks)
                    first_skipped_top = min(skipped_tops)
                    if first_skipped_top > crop_bottom + 8.0:
                        left = min(block.bbox.left for block in first_blocks)
                        top = min(block.bbox.top for block in first_blocks)
                        right = max(block.bbox.right for block in first_blocks)
                        first_problem.metadata["bbox_px"] = {
                            "left": round(max(0.0, left), 2),
                            "top": round(max(0.0, top), 2),
                            "width": round(min(float(page.width_px), right) - max(0.0, left), 2),
                            "height": round(min(float(page.height_px), first_skipped_top) - max(0.0, top), 2),
                        }
                        first_problem.metadata["bbox_source"] = "hwp_template_instruction_boundary"
            page.problems = retained
            page.metadata["template_instruction_problem_ids_skipped"] = skipped_ids


def _fill_missing_problem_numbers(problems: list[ProblemUnit]) -> None:
    """Patch in problem_number for entries whose marker OCR failed.

    Strategy: when leading problems have no number but later ones do,
    extrapolate backwards from the first known number. Forward-fill
    sequential gaps from the previous known number + 1.
    """
    numbers: list[int | None] = []
    for problem in problems:
        raw = problem.metadata.get("problem_number")
        if isinstance(raw, int):
            numbers.append(raw)
        elif isinstance(raw, str) and raw.isdigit():
            numbers.append(int(raw))
        else:
            numbers.append(None)

    if not any(n is not None for n in numbers):
        return

    first_known_index = next((i for i, n in enumerate(numbers) if n is not None), None)
    if first_known_index is not None and first_known_index > 0:
        anchor = numbers[first_known_index]
        for offset in range(1, first_known_index + 1):
            candidate = anchor - offset
            if candidate >= 1:
                numbers[first_known_index - offset] = candidate

    for index in range(1, len(numbers)):
        if numbers[index] is None and numbers[index - 1] is not None:
            numbers[index] = numbers[index - 1] + 1

    for problem, number in zip(problems, numbers):
        if number is None:
            continue
        existing = problem.metadata.get("problem_number")
        if isinstance(existing, int):
            continue
        if isinstance(existing, str) and existing.isdigit():
            continue
        problem.metadata["problem_number"] = number
        problem.metadata.setdefault("problem_number_source", "inferred_sequence")


def _passage_range_tuple(metadata: dict[str, Any]) -> tuple[int, int] | None:
    value = metadata.get("passage_range")
    if not isinstance(value, dict):
        return None
    start = _coerce_int(value.get("start"))
    end = _coerce_int(value.get("end"))
    if start is None or end is None or start <= 0 or end < start:
        return None
    return start, end


def _passage_child_numbers(metadata: dict[str, Any], start: int, end: int) -> list[int]:
    value = metadata.get("passage_child_problem_numbers")
    if isinstance(value, list):
        numbers: list[int] = []
        for raw in value:
            number = _coerce_int(raw)
            if number is not None and start <= number <= end:
                numbers.append(number)
        if numbers:
            return list(dict.fromkeys(numbers))
    return list(range(start, end + 1))


def _append_unique_string(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _metadata_string_list(metadata: dict[str, Any], key: str) -> list[str]:
    value = metadata.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _refresh_cross_page_passage_group(group: dict[str, Any]) -> None:
    source_page_ids = list(group["source_page_ids"])
    continues_across_pages = len(source_page_ids) > 1
    for member in group["members"]:
        metadata = member.metadata
        metadata["passage_source_page_ids"] = list(source_page_ids)
        metadata["passage_continues_across_pages"] = continues_across_pages
        metadata["passage_fragment_count"] = len(source_page_ids)


def _apply_cross_page_passage_group(
    group: dict[str, Any],
    *,
    page_id: str,
    problem: ProblemUnit,
) -> None:
    metadata = problem.metadata
    metadata.setdefault("passage_group_id", group["group_id"])
    metadata.setdefault("passage_range", {"start": group["start"], "end": group["end"]})
    metadata.setdefault("passage_role", "child_question")
    if group["shared_block_ids"]:
        metadata.setdefault("shared_passage_block_ids", list(group["shared_block_ids"]))
    metadata.setdefault("passage_child_problem_numbers", list(group["child_numbers"]))

    _append_unique_string(group["source_page_ids"], page_id)
    if not any(member is problem for member in group["members"]):
        group["members"].append(problem)
    _refresh_cross_page_passage_group(group)


def _seed_cross_page_passage_group(
    active_groups: dict[str, dict[str, Any]],
    *,
    page_id: str,
    problem: ProblemUnit,
) -> dict[str, Any] | None:
    metadata = problem.metadata
    group_id = str(metadata.get("passage_group_id") or "").strip()
    passage_range = _passage_range_tuple(metadata)
    if not group_id or passage_range is None:
        return None

    start, end = passage_range
    group = active_groups.get(group_id)
    if group is None:
        group = {
            "group_id": group_id,
            "start": start,
            "end": end,
            "shared_block_ids": _metadata_string_list(metadata, "shared_passage_block_ids"),
            "child_numbers": _passage_child_numbers(metadata, start, end),
            "source_page_ids": [],
            "members": [],
        }
        active_groups[group_id] = group
    else:
        group["start"] = min(int(group["start"]), start)
        group["end"] = max(int(group["end"]), end)
        for block_id in _metadata_string_list(metadata, "shared_passage_block_ids"):
            _append_unique_string(group["shared_block_ids"], block_id)
        for number in _passage_child_numbers(metadata, start, end):
            if number not in group["child_numbers"]:
                group["child_numbers"].append(number)
        group["child_numbers"].sort()

    _apply_cross_page_passage_group(group, page_id=page_id, problem=problem)
    return group


def _hwp_preview_text_values(metadata: dict[str, Any]) -> list[str]:
    values: list[str] = []
    raw = metadata.get("hwp_preview_text")
    if isinstance(raw, str) and raw.strip():
        values.append(raw)
    quality = metadata.get("hwp_conversion_quality")
    if isinstance(quality, dict):
        for key in ("hwp_preview_text", "preview_text"):
            nested = quality.get(key)
            if isinstance(nested, str) and nested.strip():
                values.append(nested)
    return values


def _hwp_preview_passage_ranges(pages: Sequence[PageModel]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for page in pages:
        for text in _hwp_preview_text_values(page.metadata):
            for line in text.splitlines():
                passage_range = extract_set_problem_range(line)
                if passage_range is None:
                    continue
                start, end = passage_range
                if end <= start or (start, end) in seen:
                    continue
                seen.add((start, end))
                ranges.append((start, end))
    ranges.sort()
    return ranges


def _annotate_hwp_preview_passage_ranges(pages: Sequence[PageModel]) -> None:
    ranges = _hwp_preview_passage_ranges(pages)
    if not ranges:
        return
    for page in pages:
        for problem in page.problems:
            if problem.metadata.get("passage_group_id"):
                continue
            problem_number = _problem_metadata_number(problem)
            if problem_number is None:
                continue
            for start, end in ranges:
                if start <= problem_number <= end:
                    problem.metadata.update(
                        {
                            "passage_group_id": f"hwp-preview-passage-{start}-{end}",
                            "passage_range": {"start": start, "end": end},
                            "passage_role": "child_question",
                            "passage_child_problem_numbers": list(range(start, end + 1)),
                            "passage_grouping_source": "hwp_preview_text",
                        }
                    )
                    break


def _following_numbered_problem_run(
    page: PageModel,
    *,
    min_length: int = 2,
) -> list[ProblemUnit]:
    numbered = [
        (number, problem)
        for problem in page.problems
        if (number := _problem_metadata_number(problem)) is not None
    ]
    if len(numbered) < min_length:
        return []
    numbered.sort(key=lambda item: (item[0], item[1].unit_id))
    run: list[tuple[int, ProblemUnit]] = [numbered[0]]
    previous = numbered[0][0]
    for number, problem in numbered[1:]:
        if number != previous + 1:
            break
        run.append((number, problem))
        previous = number
    if len(run) < min_length:
        return []
    return [problem for _, problem in run]


def _next_numbered_problem_run(
    pages: Sequence[PageModel],
    start_index: int,
    *,
    min_length: int = 2,
) -> list[ProblemUnit]:
    reading_subjects = {Subject.KOREAN, Subject.ENGLISH, Subject.SOCIAL, Subject.SCIENCE}
    for next_page in pages[start_index + 1:]:
        if next_page.subject not in reading_subjects:
            return []
        next_run = _following_numbered_problem_run(next_page, min_length=min_length)
        if next_run:
            return next_run
        if any(_problem_metadata_number(problem) is not None for problem in next_page.problems):
            return []
    return []


def _annotate_marker_continuation_pages_to_following_groups(pages: Sequence[PageModel]) -> None:
    for index, page in enumerate(pages):
        if page.subject not in {Subject.KOREAN, Subject.ENGLISH, Subject.SOCIAL, Subject.SCIENCE}:
            continue
        if len(page.problems) != 1:
            continue
        continuation = page.problems[0]
        if not _is_marker_document_continuation_problem(continuation):
            continue
        if continuation.metadata.get("passage_group_id"):
            continue

        following_run = _next_numbered_problem_run(pages, index, min_length=1)
        if len(following_run) < 1:
            continue

        first_problem = following_run[0]
        first_number = _problem_metadata_number(first_problem)
        last_number = _problem_metadata_number(following_run[-1])
        if first_number is None or last_number is None:
            continue
        existing_range = _passage_range_tuple(first_problem.metadata)
        start, end = existing_range or (first_number, last_number)
        child_numbers = _passage_child_numbers(first_problem.metadata, start, end)
        group_id = str(first_problem.metadata.get("passage_group_id") or "").strip()
        if not group_id:
            number_label = str(start) if start == end else f"{start}-{end}"
            group_id = f"hwp-continuation-passage-{number_label}"

        common_metadata = {
            "passage_group_id": group_id,
            "passage_range": {"start": start, "end": end},
            "passage_child_problem_numbers": child_numbers,
            "passage_grouping_source": "marker_document_continuation",
        }
        child_number_set = set(child_numbers)
        continuation.metadata.update(
            {
                **common_metadata,
                "passage_role": "passage_fragment",
                "passage_fragment_source": "marker_document_continuation",
            }
        )
        for problem in following_run:
            problem_number = _problem_metadata_number(problem)
            if problem_number not in child_number_set:
                continue
            if not problem.metadata.get("passage_group_id"):
                problem.metadata.update(
                    {
                        **common_metadata,
                        "passage_role": "child_question",
                    }
                )


def _annotate_cross_page_passage_groups(pages: Sequence[PageModel]) -> None:
    _annotate_hwp_preview_passage_ranges(pages)
    _annotate_marker_continuation_pages_to_following_groups(pages)
    active_groups: dict[str, dict[str, Any]] = {}
    for page in pages:
        ordered_problems = sorted(
            page.problems,
            key=lambda problem: (_problem_metadata_number(problem) or 10**9, problem.unit_id),
        )
        for problem in ordered_problems:
            problem_number = _problem_metadata_number(problem)
            if problem_number is not None:
                for group_id, group in list(active_groups.items()):
                    if problem_number > int(group["end"]):
                        active_groups.pop(group_id, None)

            seeded_group = _seed_cross_page_passage_group(
                active_groups,
                page_id=page.page_id,
                problem=problem,
            )
            if seeded_group is not None or problem_number is None:
                continue

            if problem.metadata.get("passage_group_id"):
                continue
            for group in active_groups.values():
                if int(group["start"]) <= problem_number <= int(group["end"]):
                    _apply_cross_page_passage_group(group, page_id=page.page_id, problem=problem)
                    break


def _collect_problem_risk_flags(problem: ProblemUnit) -> list[str]:
    flags: list[str] = []
    if _problem_passage_continues_across_pages(problem.metadata):
        flags.append(PASSAGE_CROSS_PAGE_MERGE_CHECK_RISK_FLAG)
    if problem.metadata.get("fallback_grouping"):
        flags.append("fallback_grouping")
    if problem.metadata.get("merged_problem_block"):
        flags.append("merged_problem_block")
    if problem.metadata.get("marker_conflict"):
        flags.append("marker_conflicts")

    for key in ("review_flags", "risk_flags"):
        values = problem.metadata.get(key)
        if isinstance(values, list):
            flags.extend(str(value) for value in values if value)

    ai_unit = problem.metadata.get("ai_problem_unit")
    if isinstance(ai_unit, dict):
        values = ai_unit.get("review_flags")
        if isinstance(values, list):
            flags.extend(str(value) for value in values if value)

    return list(dict.fromkeys(flags))


def _problem_passage_continues_across_pages(metadata: dict[str, Any] | None) -> bool:
    if not isinstance(metadata, dict):
        return False
    if bool(metadata.get("passage_continues_across_pages")):
        return True
    source_page_ids = metadata.get("passage_source_page_ids")
    if not isinstance(source_page_ids, list):
        return False
    return len({str(page_id) for page_id in source_page_ids if str(page_id)}) > 1


def build_problem_entries(
    prepared_pages: list[PreparedPage],
    pages: list[PageModel],
    output_dir: Path,
    template: LayoutTemplate,
    *,
    board_theme: str = DEFAULT_BOARD_THEME,
) -> list[ProblemEntry]:
    crop_dir = output_dir / "problem_crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    cutout_dir = output_dir / "problem_cutouts"
    cutout_dir.mkdir(parents=True, exist_ok=True)
    chalk_color = _resolve_chalk_color(board_theme)
    prepared_by_page_id = {page.page_id: page for page in prepared_pages}
    drafts: list[_ProblemEntryDraft] = []
    _remove_duplicate_marker_document_problem_numbers(pages)
    _remove_hwp_template_instruction_problems(pages)
    _annotate_cross_page_passage_groups(pages)

    for page in pages:
        prepared_page = prepared_by_page_id.get(page.page_id)
        if prepared_page is None:
            continue
        prepared_page.image.load()
        block_by_id = {block.block_id: block for block in page.blocks}

        # Reorder problems by their first block's top y so the "next problem"
        # boundary used for gap-filling matches reading order even when the
        # grouping pass produced them out of order.
        ordered_problems = sorted(
            page.problems,
            key=lambda p: _problem_order_key(p, block_by_id),
        )

        # Drop pre-first-problem header bands (e.g. cover-page title, 성명 /
        # 수험번호 form, "물리학I" / "과학탐구" subject header). When the page
        # contains a numbered problem, anything that lands above the first
        # numbered problem with no number of its own and no choice marker is
        # treated as page chrome — it would otherwise get bundled into the
        # first problem's crop (or worse, surface as its own pseudo-problem).
        ordered_problems = _drop_pre_first_problem_headers(ordered_problems, block_by_id)

        continuation_problems = _collapse_marker_document_continuation_page(page, ordered_problems, block_by_id)
        if continuation_problems is not None:
            if not continuation_problems:
                page.problems = []
                continue
            page.problems = continuation_problems
            ordered_problems = continuation_problems

        _fill_missing_problem_numbers(ordered_problems)
        next_problem_for_crop = _build_crop_next_problem_map(ordered_problems, block_by_id)

        all_assigned_ids: set[str] = set()
        for prob in ordered_problems:
            all_assigned_ids.update(_iter_problem_block_ids_raw(prob))

        for problem in ordered_problems:
            next_problem = next_problem_for_crop.get(problem.unit_id)
            own_ids = set(_iter_problem_block_ids_raw(problem))
            other_problem_block_ids = all_assigned_ids - own_ids
            problem_block_ids = iter_problem_block_ids(page, problem)
            own_blocks = [block_by_id[block_id] for block_id in problem_block_ids if block_id in block_by_id]
            gap_filled = _expand_problem_blocks_by_gap(
                page, problem, next_problem, block_by_id, other_problem_block_ids
            )
            blocks = gap_filled if gap_filled else own_blocks
            raw_problem_number = problem.metadata.get("problem_number")
            if isinstance(raw_problem_number, int):
                problem_number = raw_problem_number
            elif isinstance(raw_problem_number, str) and raw_problem_number.isdigit():
                problem_number = int(raw_problem_number)
            else:
                problem_number = None
            if problem.metadata.get("force_full_page_bounds"):
                blocks = list(page.sorted_blocks())
                merged_box = Box(left=0.0, top=0.0, width=float(page.width_px), height=float(page.height_px))
            else:
                boxes = [block.bbox for block in blocks]
                metadata_box = _problem_metadata_bbox(problem, page)
                if metadata_box is not None:
                    if _should_prefer_problem_metadata_bbox(problem):
                        boxes = [metadata_box]
                    else:
                        boxes.append(metadata_box)
                if not boxes:
                    boxes = [Box(left=0.0, top=0.0, width=float(page.width_px), height=float(page.height_px))]
                has_document_band_metadata = any("question_band_index" in block.metadata for block in blocks)
                has_choice_blocks = any(block.block_type == BlockType.CHOICE for block in blocks)
                bottom_padding_px = (
                    max(DOCUMENT_BAND_BOTTOM_PADDING_PX, CHOICE_BOTTOM_SAFE_PADDING_PX)
                    if has_choice_blocks
                    else DOCUMENT_BAND_BOTTOM_PADDING_PX
                    if has_document_band_metadata
                    else PROBLEM_PADDING_PX
                )
                content_bottom = max(box.bottom for box in boxes)
                min_bottom = (
                    min(float(page.height_px), content_bottom + float(bottom_padding_px))
                    if has_choice_blocks
                    else None
                )
                merged_box = merge_boxes(
                    boxes,
                    page_width=page.width_px,
                    page_height=page.height_px,
                    top_padding_px=int(DOCUMENT_BAND_TOP_PADDING_PX) if has_document_band_metadata else PROBLEM_PADDING_PX,
                    bottom_padding_px=bottom_padding_px,
                )
                if has_document_band_metadata:
                    merged_box = _clamp_box_to_next_problem(
                        merged_box,
                        next_problem,
                        block_by_id,
                        min_bottom=min_bottom,
                    )
                if not has_document_band_metadata and merged_box.area < float(page.width_px * page.height_px) * MIN_PROBLEM_AREA_RATIO:
                    merged_box = Box(left=0.0, top=0.0, width=float(page.width_px), height=float(page.height_px))
                    blocks = list(page.sorted_blocks())

            entry_index = len(drafts) + 1
            crop_name = f"problem_{entry_index:03d}_{hashlib.sha1(problem.unit_id.encode('utf-8', errors='ignore')).hexdigest()[:8]}.png"
            crop_path = crop_dir / crop_name
            board_render_path = cutout_dir / crop_name
            reading_heavy = problem.subject in {Subject.KOREAN, Subject.ENGLISH, Subject.SOCIAL, Subject.SCIENCE}
            problem_title = problem.title or (f"\ubb38\ud56d {problem_number}" if problem_number is not None else f"\ubb38\ud56d {entry_index}")
            drafts.append(
                _ProblemEntryDraft(
                    problem_id=problem.unit_id,
                    title=problem_title,
                    problem_number=problem_number,
                    subject=problem.subject,
                    source_page_id=page.page_id,
                    source_path=prepared_page.source_path,
                    prepared_page=prepared_page,
                    bounds=merged_box,
                    crop_path=crop_path,
                    board_render_path=board_render_path,
                    blocks=sorted(blocks, key=lambda block: (block.reading_order, block.bbox.top, block.bbox.left)),
                    overflow_allowed=reading_heavy,
                    reading_heavy=reading_heavy,
                    risk_flags=_collect_problem_risk_flags(problem),
                    asset_task=_ProblemAssetTask(
                        source_image=prepared_page.image,
                        bounds=merged_box,
                        crop_path=crop_path,
                        board_render_path=board_render_path,
                        chalk_color=chalk_color,
                    ),
                )
            )

    crop_sizes = _render_problem_assets([draft.asset_task for draft in drafts])
    entries: list[ProblemEntry] = []
    for draft, crop_size in zip(drafts, crop_sizes):
        entries.append(
            ProblemEntry(
                problem_id=draft.problem_id,
                title=draft.title,
                problem_number=draft.problem_number,
                subject=draft.subject,
                source_page_id=draft.source_page_id,
                source_path=draft.source_path,
                prepared_page=draft.prepared_page,
                bounds=draft.bounds,
                crop_path=draft.crop_path,
                board_render_path=draft.board_render_path,
                blocks=draft.blocks,
                actual_height_pages=estimate_height_pages(crop_size, template),
                overflow_allowed=draft.overflow_allowed,
                reading_heavy=draft.reading_heavy,
                risk_flags=draft.risk_flags,
            )
        )
    return entries


def _to_file_uri(path: str | Path | None) -> str | None:
    if path is None:
        return None
    return Path(path).resolve().as_uri()


def _build_ai_fallback_config(
    *,
    enabled: bool,
    mode: str | None,
    provider: str,
    model: str,
    prompt: str,
    max_tokens: int | None,
    temperature: float | None,
    threshold: float,
    max_regions: int,
    timeout_ms: int,
    save_debug: bool,
    fail_on_error: bool,
) -> dict[str, Any] | None:
    threshold = 0.72 if threshold is None else float(threshold)
    max_tokens = 4096 if max_tokens is None else int(max_tokens)
    max_regions = 48 if max_regions is None else int(max_regions)
    timeout_ms = 30000 if timeout_ms is None else int(timeout_ms)
    resolved_mode = (mode or "").strip().lower() or ("auto" if enabled else "off")
    if resolved_mode not in {"off", "auto", "force"}:
        resolved_mode = "auto" if enabled else "off"
    effective_enabled = resolved_mode != "off"
    if (
        not effective_enabled
        and provider in {"openai", "gemini"}
        and not model
        and not prompt
        and max_tokens == 4096
        and temperature is None
        and threshold == 0.72
        and max_regions == 48
        and timeout_ms == 30000
        and not save_debug
        and not fail_on_error
    ):
        return None
    return {
        "enabled": effective_enabled,
        "mode": resolved_mode,
        "provider": provider or "gemini",
        "model": model or DEFAULT_GEMINI_REPAIR_MODEL,
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "threshold": threshold,
        "max_regions": max_regions,
        "timeout_ms": timeout_ms,
        "save_debug": save_debug,
        "fail_on_error": fail_on_error,
    }


def _to_page_ai_config(ai_fallback_config: dict[str, Any] | None) -> AIFallbackConfig:
    if not ai_fallback_config:
        return build_page_ai_fallback_config()
    return build_page_ai_fallback_config(
        mode=str(ai_fallback_config.get("mode") or ("auto" if bool(ai_fallback_config.get("enabled")) else "off")),
        provider=str(ai_fallback_config.get("provider") or "gemini"),
        model=str(ai_fallback_config.get("model") or ""),
        threshold=float(ai_fallback_config.get("threshold") or 0.72),
        max_regions=int(ai_fallback_config.get("max_regions") or 48),
        max_tokens=int(ai_fallback_config.get("max_tokens") or 4096),
        timeout_ms=int(ai_fallback_config.get("timeout_ms") or 30000),
        save_debug=bool(ai_fallback_config.get("save_debug")),
        fail_on_error=bool(ai_fallback_config.get("fail_on_error")),
    )


def _summarize_ocr_usage(pages: list[PageModel]) -> dict[str, Any]:
    """Report which OCR backend(s) actually ran. Exposes the common 'auto
    silently resolved to NoOcrBackend' failure so the UI/log can flag it."""
    backend_counts: dict[str, int] = {}
    empty_text_count = 0
    populated_text_count = 0
    total_blocks = 0
    for page in pages:
        for block in page.blocks:
            total_blocks += 1
            backend = str(block.metadata.get("ocr_backend") or "unknown")
            backend_counts[backend] = backend_counts.get(backend, 0) + 1
            if block.metadata.get("ocr_empty_text"):
                empty_text_count += 1
            elif block.text and block.text.strip():
                populated_text_count += 1
    primary_backend = (
        max(backend_counts.items(), key=lambda item: item[1])[0]
        if backend_counts
        else "unknown"
    )
    return {
        "resolved_backend": primary_backend,
        "backend_counts": backend_counts,
        "block_count": total_blocks,
        "empty_text_block_count": empty_text_count,
        "populated_text_block_count": populated_text_count,
        "no_ocr_fallback_active": primary_backend == "none",
    }


def _summarize_ai_fallback_usage(pages: list[PageModel], ai_fallback_config: dict[str, Any] | None) -> dict[str, Any] | None:
    if not ai_fallback_config:
        return None
    attempted_page_count = 0
    applied_page_count = 0
    ai_cache_hit_count = 0
    ocr_cache_hit_count = 0
    ocr_cache_miss_count = 0
    status_counts: dict[str, int] = {}
    route_counts: dict[str, int] = {}
    route_tier_counts: dict[str, int] = {}
    model_fallbacks: list[dict[str, Any]] = []

    for page in pages:
        ai_summary = page.metadata.get("ai_fallback")
        if not isinstance(ai_summary, dict):
            ai_summary = {}
        if ai_summary.get("attempted"):
            attempted_page_count += 1
        if ai_summary.get("applied"):
            applied_page_count += 1
        if ai_summary.get("cache_hit"):
            ai_cache_hit_count += 1
        status = str(ai_summary.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        model_fallback = ai_summary.get("model_fallback")
        if isinstance(model_fallback, dict):
            model_fallbacks.append(dict(model_fallback))

        route_decision = page.metadata.get("route_decision")
        if isinstance(route_decision, dict):
            route = str(route_decision.get("route") or "unknown")
            route_counts[route] = route_counts.get(route, 0) + 1
            profile = route_decision.get("profile")
            if isinstance(profile, dict):
                tier = str(profile.get("tier") or "unknown")
                route_tier_counts[tier] = route_tier_counts.get(tier, 0) + 1

        for block in page.blocks:
            if block.metadata.get("ocr_cache_hit"):
                ocr_cache_hit_count += 1
            if block.metadata.get("ocr_cache_miss"):
                ocr_cache_miss_count += 1

    return {
        "requested": bool(ai_fallback_config.get("enabled")),
        "mode": ai_fallback_config.get("mode"),
        "provider": ai_fallback_config.get("provider"),
        "model": ai_fallback_config.get("model"),
        "attempted_page_count": attempted_page_count,
        "applied_page_count": applied_page_count,
        "ai_cache_hit_count": ai_cache_hit_count,
        "ocr_cache_hit_count": ocr_cache_hit_count,
        "ocr_cache_miss_count": ocr_cache_miss_count,
        "status_counts": status_counts,
        "route_counts": route_counts,
        "route_tier_counts": route_tier_counts,
        "model_fallbacks": model_fallbacks,
    }


def _template_to_dict(template: LayoutTemplate) -> dict[str, Any]:
    return {
        "name": template.name,
        "board_page_count": template.board_page_count,
        "base_slot_height_pages": template.base_slot_height_pages,
        "fixed_left_zone_ratio": template.fixed_left_zone_ratio,
        "preserve_right_writing_zone": template.preserve_right_writing_zone,
        "default_overflow_subjects": [subject.value for subject in template.default_overflow_subjects],
        "metadata": dict(template.metadata),
    }


GENERIC_PROBLEM_TITLE_RE = re.compile(r"^\s*(?:문항|문제|臾명빆)\s*\d+(?:\s*[·쨌:\-].*)?$")
INPUT_INTENTS = {"auto", "single-problem", "multi-problem", "page-as-is"}


def _normalize_input_intent(value: str | None) -> str:
    normalized = (value or "auto").strip().lower().replace("_", "-")
    return normalized if normalized in INPUT_INTENTS else "auto"


def _normalize_problem_title(title: str | None, index: int, source_page_id: str, problem_number: int | None = None) -> str:
    raw = (title or "").strip()
    if raw and "problem" not in raw.lower() and not GENERIC_PROBLEM_TITLE_RE.match(raw):
        return raw
    if isinstance(problem_number, int) and problem_number > 0:
        return f"문항 {problem_number}"
    return f"문항 {index + 1:02d} · {source_page_id}"


def recrop_problem(
    page_image: Image.Image,
    bbox: Box,
    crop_path: Path,
) -> tuple[int, int]:
    """Re-crop a problem from its source page image for split/merge mutations.

    Writes a rectangular crop to ``crop_path``. Background removal / cutout
    regeneration is intentionally skipped — that path is reserved for the
    AI (Step 2) workflow. Callers that need a board_render_path can reuse the
    crop_path itself; the board renderer will composite onto the dark theme
    at EDB build time.
    """
    left = int(max(0, min(page_image.width, bbox.left)))
    top = int(max(0, min(page_image.height, bbox.top)))
    right = int(max(left + 1, min(page_image.width, bbox.right)))
    bottom = int(max(top + 1, min(page_image.height, bbox.bottom)))
    crop = page_image.crop((left, top, right, bottom))
    crop = _trim_edge_vertical_guides(crop)
    crop = _pad_problem_crop_bottom(crop)
    crop_path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(crop_path)
    return crop.size


_GLOBAL_RISK_REASONS = {"merged_problem_block", "marker_conflicts"}


def _collect_page_risk_flags(page_metadata: dict[str, Any]) -> list[str]:
    route_decision = page_metadata.get("route_decision")
    if not isinstance(route_decision, dict):
        return []
    profile = route_decision.get("profile") or {}
    reasons = profile.get("reasons") if isinstance(profile, dict) else None
    if not isinstance(reasons, list):
        return []
    return [str(reason) for reason in reasons if isinstance(reason, str)]


def _hwp_source_key(page: PageModel) -> str | None:
    if page.metadata.get("source_type") != "hwp":
        return None
    for key in ("source_hwp_path", "original_source_path", "source_pdf_path", "converted_pdf_path"):
        value = page.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return page.source_path


def _hwp_text_problem_signal_count(metadata: dict[str, Any]) -> int:
    quality = metadata.get("hwp_conversion_quality")
    if not isinstance(quality, dict):
        return 0
    numbered = _coerce_int(quality.get("hwp_text_numbered_problem_count")) or 0
    stem = _coerce_int(quality.get("hwp_text_stem_problem_count")) or 0
    return numbered if numbered > 0 else stem


def _metadata_list_count(metadata: dict[str, Any], key: str) -> int:
    value = metadata.get(key)
    if isinstance(value, list):
        return len(value)
    return _coerce_int(value) or 0


def _is_marker_document_continuation_problem(problem: ProblemUnit) -> bool:
    if bool(problem.metadata.get("marker_document_continuation")):
        return True
    for key in ("review_flags", "risk_flags"):
        values = problem.metadata.get(key)
        if isinstance(values, list) and "marker_document_continuation" in {str(value) for value in values}:
            return True
    return False


def _count_core_hwp_problems(page: PageModel, final_problem_ids: list[str] | None = None) -> int:
    continuation_ids = {
        problem.unit_id
        for problem in page.problems
        if _is_marker_document_continuation_problem(problem)
    }
    if final_problem_ids is not None:
        return sum(1 for problem_id in final_problem_ids if problem_id not in continuation_ids)
    return sum(1 for problem in page.problems if not _is_marker_document_continuation_problem(problem))


HWP_OVERSEGMENTATION_MIN_EXTRA = 10
HWP_OVERSEGMENTATION_RATIO = 2.0


def _is_hwp_oversegmentation(expected: int, detected: int) -> bool:
    if expected <= 0 or detected <= expected:
        return False
    return detected - expected >= HWP_OVERSEGMENTATION_MIN_EXTRA and detected >= expected * HWP_OVERSEGMENTATION_RATIO


def _hwp_problem_counts_match(
    pages: list[PageModel],
    final_problem_ids_by_page_id: dict[str, list[str]] | None = None,
) -> bool:
    by_source: dict[str, dict[str, int]] = {}
    for page in pages:
        source_key = _hwp_source_key(page)
        if not source_key:
            continue
        bucket = by_source.setdefault(source_key, {"detected": 0, "signal": 0})
        if final_problem_ids_by_page_id is None:
            bucket["detected"] += _count_core_hwp_problems(page)
        else:
            bucket["detected"] += _count_core_hwp_problems(
                page,
                final_problem_ids_by_page_id.get(page.page_id, []),
            )
        bucket["signal"] = max(int(bucket["signal"]), _hwp_text_problem_signal_count(page.metadata))
    for bucket in by_source.values():
        signal = int(bucket["signal"])
        if signal <= 0:
            continue
        expected = max(0, signal)
        if expected > 0 and int(bucket["detected"]) == expected:
            return True
    return False


def _session_problem_is_supplemental(problem: dict[str, Any]) -> bool:
    risk_flags = problem.get("riskFlags") or problem.get("risk_flags") or []
    if isinstance(risk_flags, list) and "marker_document_continuation" in {str(flag) for flag in risk_flags}:
        return True
    metadata = problem.get("metadata")
    if isinstance(metadata, dict) and metadata.get("marker_document_continuation"):
        return True
    problem_id = str(problem.get("id") or problem.get("problem_id") or "")
    return problem_id.endswith("-continuation")


def _session_problem_count_payload(problems: list[dict[str, Any]]) -> dict[str, int]:
    detected_count = len(problems)
    supplemental_count = sum(1 for problem in problems if _session_problem_is_supplemental(problem))
    return {
        "detected_problem_count": detected_count,
        "core_problem_count": max(0, detected_count - supplemental_count),
        "supplemental_item_count": supplemental_count,
    }


def _problem_passage_payload(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    payload: dict[str, Any] = {}

    group_id = str(metadata.get("passage_group_id") or "").strip()
    if group_id:
        payload["passageGroupId"] = group_id
        payload["passage_group_id"] = group_id

    passage_range = metadata.get("passage_range")
    if isinstance(passage_range, dict):
        normalized_range = dict(passage_range)
        payload["passageRange"] = normalized_range
        payload["passage_range"] = normalized_range

    role = str(metadata.get("passage_role") or "").strip()
    if role:
        payload["passageRole"] = role
        payload["passage_role"] = role

    shared_block_ids = metadata.get("shared_passage_block_ids")
    if isinstance(shared_block_ids, list):
        normalized_shared_block_ids = [str(block_id) for block_id in shared_block_ids if str(block_id)]
        if normalized_shared_block_ids:
            payload["sharedPassageBlockIds"] = normalized_shared_block_ids
            payload["shared_passage_block_ids"] = normalized_shared_block_ids

    child_numbers = metadata.get("passage_child_problem_numbers")
    if isinstance(child_numbers, list):
        normalized_child_numbers: list[int] = []
        for raw in child_numbers:
            try:
                normalized_child_numbers.append(int(raw))
            except (TypeError, ValueError):
                continue
        if normalized_child_numbers:
            payload["passageChildProblemNumbers"] = normalized_child_numbers
            payload["passage_child_problem_numbers"] = normalized_child_numbers

    source_page_ids = metadata.get("passage_source_page_ids")
    if isinstance(source_page_ids, list):
        normalized_source_page_ids = [str(page_id) for page_id in source_page_ids if str(page_id)]
        if normalized_source_page_ids:
            payload["passageSourcePageIds"] = normalized_source_page_ids
            payload["passage_source_page_ids"] = normalized_source_page_ids

    if "passage_continues_across_pages" in metadata:
        continues_across_pages = bool(metadata.get("passage_continues_across_pages"))
        payload["passageContinuesAcrossPages"] = continues_across_pages
        payload["passage_continues_across_pages"] = continues_across_pages

    fragment_count = _coerce_int(metadata.get("passage_fragment_count"))
    if fragment_count is not None and fragment_count > 0:
        payload["passageFragmentCount"] = fragment_count
        payload["passage_fragment_count"] = fragment_count

    continuation_block_ids = metadata.get("passage_pre_question_continuation_block_ids")
    if isinstance(continuation_block_ids, list):
        normalized_continuation_block_ids = [
            str(block_id)
            for block_id in continuation_block_ids
            if str(block_id)
        ]
        if normalized_continuation_block_ids:
            payload["passagePreQuestionContinuationBlockIds"] = normalized_continuation_block_ids
            payload["passage_pre_question_continuation_block_ids"] = normalized_continuation_block_ids

    return payload


def _coerce_problem_number(value: Any) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def _ordered_unique_strings(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique


def _ordered_unique_ints(values: Iterable[Any]) -> list[int]:
    seen: set[int] = set()
    unique: list[int] = []
    for value in values:
        number = _coerce_problem_number(value)
        if number is None or number in seen:
            continue
        seen.add(number)
        unique.append(number)
    return unique


def _is_missing_session_field(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, list) and not value:
        return True
    if isinstance(value, dict) and not value:
        return True
    return False


def _session_problem_field(problem: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in problem and not _is_missing_session_field(problem[key]):
            return problem[key]
    metadata = problem.get("metadata")
    if isinstance(metadata, dict):
        for key in keys:
            if key in metadata and not _is_missing_session_field(metadata[key]):
                return metadata[key]
    return None


def _session_problem_passage_group_id(problem: dict[str, Any]) -> str:
    return str(
        _session_problem_field(problem, "passageGroupId", "passage_group_id") or ""
    ).strip()


def _session_problem_passage_range(problem: dict[str, Any]) -> tuple[int, int] | None:
    value = _session_problem_field(problem, "passageRange", "passage_range")
    if not isinstance(value, dict):
        return None
    start = _coerce_problem_number(value.get("start"))
    end = _coerce_problem_number(value.get("end"))
    if start is None or end is None or end < start:
        return None
    return start, end


def _session_problem_passage_numbers(problem: dict[str, Any], *keys: str) -> list[int]:
    value = _session_problem_field(problem, *keys)
    if isinstance(value, list):
        return _ordered_unique_ints(value)
    return []


def _session_problem_passage_source_page_ids(problem: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    explicit = _session_problem_field(problem, "passageSourcePageIds", "passage_source_page_ids")
    if isinstance(explicit, list):
        values.extend(explicit)
    values.append(_session_problem_field(problem, "sourcePageId", "source_page_id"))
    return _ordered_unique_strings(values)


def _passage_number_label(start: int | None, end: int | None, child_numbers: list[int]) -> str:
    if start is not None and end is not None:
        return str(start) if start == end else f"{start}-{end}"
    if child_numbers:
        first = min(child_numbers)
        last = max(child_numbers)
        return str(first) if first == last else f"{first}-{last}"
    return ""


def _session_passage_groups(problems: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for problem in problems:
        if not isinstance(problem, dict):
            continue
        group_id = _session_problem_passage_group_id(problem)
        if not group_id:
            continue
        group = groups.setdefault(
            group_id,
            {
                "groupId": group_id,
                "problemNumbers": [],
                "childProblemNumbers": [],
                "problemIds": [],
                "sourcePageIds": [],
                "roles": [],
                "continuesAcrossPages": False,
                "_rangeStart": None,
                "_rangeEnd": None,
            },
        )

        problem_number = _coerce_problem_number(
            _session_problem_field(problem, "problemNumber", "problem_number")
        )
        if problem_number is not None and problem_number not in group["problemNumbers"]:
            group["problemNumbers"].append(problem_number)

        child_numbers = _session_problem_passage_numbers(
            problem,
            "passageChildProblemNumbers",
            "passage_child_problem_numbers",
        )
        for child_number in child_numbers:
            if child_number not in group["childProblemNumbers"]:
                group["childProblemNumbers"].append(child_number)

        problem_id = str(_session_problem_field(problem, "id", "problem_id") or "").strip()
        if problem_id and problem_id not in group["problemIds"]:
            group["problemIds"].append(problem_id)

        for page_id in _session_problem_passage_source_page_ids(problem):
            if page_id not in group["sourcePageIds"]:
                group["sourcePageIds"].append(page_id)

        role = str(
            _session_problem_field(problem, "passageRole", "passage_role") or ""
        ).strip()
        if role and role not in group["roles"]:
            group["roles"].append(role)

        passage_range = _session_problem_passage_range(problem)
        if passage_range is not None:
            start, end = passage_range
            current_start = group["_rangeStart"]
            current_end = group["_rangeEnd"]
            group["_rangeStart"] = start if current_start is None else min(current_start, start)
            group["_rangeEnd"] = end if current_end is None else max(current_end, end)

        group["continuesAcrossPages"] = bool(group["continuesAcrossPages"]) or bool(
            _session_problem_field(
                problem,
                "passageContinuesAcrossPages",
                "passage_continues_across_pages",
            )
        )

    items: list[dict[str, Any]] = []
    for group in groups.values():
        child_numbers = _ordered_unique_ints(group["childProblemNumbers"])
        start = group.pop("_rangeStart")
        end = group.pop("_rangeEnd")
        if (start is None or end is None) and child_numbers:
            start = min(child_numbers)
            end = max(child_numbers)
        source_page_ids = _ordered_unique_strings(group["sourcePageIds"])
        continues_across_pages = bool(group["continuesAcrossPages"]) or len(source_page_ids) > 1
        label = _passage_number_label(start, end, child_numbers)
        source_page_count = len(source_page_ids)
        problem_count = len(group["problemIds"])
        message_label = label or str(group["groupId"])
        group.update(
            {
                "numberStart": start,
                "numberEnd": end,
                "numberLabel": label,
                "problemNumbers": _ordered_unique_ints(group["problemNumbers"]),
                "childProblemNumbers": child_numbers,
                "sourcePageIds": source_page_ids,
                "sourcePageCount": source_page_count,
                "problemCount": problem_count,
                "continuesAcrossPages": continues_across_pages,
                "message": (
                    f"긴 지문 그룹 {message_label}이 {source_page_count}개 원본 페이지와 "
                    f"{problem_count}개 감지 문항에 걸쳐 있습니다."
                ),
            }
        )
        items.append(group)
    return items


def _session_duplicate_problem_number_groups(problems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    numbered: list[dict[str, Any]] = []
    for index, problem in enumerate(problems):
        number = _coerce_problem_number(problem.get("problemNumber") or problem.get("problem_number"))
        if number is None:
            continue
        numbered.append(
            {
                "index": index,
                "number": number,
                "problemId": str(problem.get("id") or problem.get("problem_id") or ""),
                "sourcePageId": str(problem.get("sourcePageId") or problem.get("source_page_id") or ""),
            }
        )
    if not numbered:
        return []

    counts: dict[int, int] = {}
    for item in numbered:
        counts[item["number"]] = counts.get(item["number"], 0) + 1
    duplicate_numbers = sorted(number for number, count in counts.items() if count > 1)
    if not duplicate_numbers:
        return []

    ranges: list[tuple[int, int]] = []
    start = previous = duplicate_numbers[0]
    for number in duplicate_numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append((start, previous))
        start = previous = number
    ranges.append((start, previous))

    groups: list[dict[str, Any]] = []
    for start, end in ranges:
        numbers = [number for number in range(start, end + 1) if counts.get(number, 0) > 1]
        if not numbers:
            continue
        group_items = [item for item in numbered if item["number"] in set(numbers)]
        occurrence_counts = [counts[number] for number in numbers]
        min_occurrences = min(occurrence_counts)
        max_occurrences = max(occurrence_counts)
        label = str(start) if start == end else f"{start}-{end}"
        if min_occurrences == max_occurrences:
            message = f"문항 번호 {label}가 각 {min_occurrences}회 등장합니다."
        else:
            message = f"문항 번호 {label} 범위에서 중복 번호가 {sum(counts[number] - 1 for number in numbers)}개 있습니다."
        classification = "alternate_section" if _duplicate_number_group_looks_like_alternate_section(
            start=start,
            end=end,
            min_occurrences=min_occurrences,
            max_occurrences=max_occurrences,
        ) else "duplicate"
        blocking = classification != "alternate_section"
        groups.append(
            {
                "numberStart": start,
                "numberEnd": end,
                "numberLabel": label,
                "problemNumbers": numbers,
                "occurrencesPerNumber": max_occurrences,
                "duplicateRecordCount": sum(counts[number] - 1 for number in numbers),
                "totalRecordCount": sum(counts[number] for number in numbers),
                "sourcePageIds": _ordered_unique_strings(item["sourcePageId"] for item in group_items),
                "problemIds": _ordered_unique_strings(item["problemId"] for item in group_items),
                "classification": classification,
                "blocking": blocking,
                "message": message,
            }
        )
    return groups


def _duplicate_number_group_looks_like_alternate_section(
    *,
    start: int,
    end: int,
    min_occurrences: int,
    max_occurrences: int,
) -> bool:
    range_size = end - start + 1
    return (
        start >= 20
        and end <= 45
        and 6 <= range_size <= 12
        and min_occurrences == max_occurrences
        and max_occurrences in {2, 3}
    )


DUPLICATE_PROBLEM_NUMBER_RISK_FLAG = "duplicate_problem_number"
SOURCE_PROBLEM_BBOX_OVERLAP_RISK_FLAG = "source_problem_bbox_overlap"


def _mark_duplicate_problem_number_review_flags(
    problems: list[dict[str, Any]],
    groups: Sequence[dict[str, Any]],
) -> None:
    duplicate_problem_ids: set[str] = set()
    for group in groups:
        if group.get("blocking") is False:
            continue
        for problem_id in group.get("problemIds") or group.get("problem_ids") or []:
            problem_id_text = str(problem_id or "").strip()
            if problem_id_text:
                duplicate_problem_ids.add(problem_id_text)
    if not duplicate_problem_ids:
        return

    for problem in problems:
        problem_id = str(problem.get("id") or problem.get("problem_id") or "").strip()
        if problem_id not in duplicate_problem_ids:
            continue
        flags = [str(flag) for flag in (problem.get("riskFlags") or problem.get("risk_flags") or []) if flag]
        flags.append(DUPLICATE_PROBLEM_NUMBER_RISK_FLAG)
        problem["riskFlags"] = list(dict.fromkeys(flags))
        if str(problem.get("reviewStatus") or problem.get("review_status") or "").strip() != "failed":
            problem["reviewStatus"] = "check_needed"


def _session_source_problem_overlap_groups(problems: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for issue in _classin_source_bbox_overlap_issues(problems):
        problem_id = str(issue.get("problemId") or "").strip()
        next_problem_id = str(issue.get("nextProblemId") or "").strip()
        if not problem_id or not next_problem_id:
            continue
        source_page_id = str(issue.get("sourcePageId") or "").strip()
        overlap_area_ratio = _coerce_float(issue.get("overlapAreaRatio")) or 0.0
        groups.append(
            {
                "sourcePageId": source_page_id,
                "source_page_id": source_page_id,
                "problemIds": [problem_id, next_problem_id],
                "problem_ids": [problem_id, next_problem_id],
                "problemTitles": [
                    str(issue.get("problemTitle") or problem_id),
                    str(issue.get("nextProblemTitle") or next_problem_id),
                ],
                "overlapAreaRatio": round(overlap_area_ratio, 6),
                "overlap_area_ratio": round(overlap_area_ratio, 6),
                "intersectionOverUnion": issue.get("intersectionOverUnion", 0.0),
                "intersection_over_union": issue.get("intersectionOverUnion", 0.0),
                "bbox": issue.get("bbox") or {},
                "nextBbox": issue.get("nextBbox") or {},
                "next_bbox": issue.get("nextBbox") or {},
                "message": str(issue.get("message") or ""),
            }
        )
    return groups


def _mark_source_problem_overlap_review_flags(
    problems: list[dict[str, Any]],
    groups: Sequence[dict[str, Any]],
) -> None:
    overlap_problem_ids: set[str] = set()
    for group in groups:
        for problem_id in group.get("problemIds") or group.get("problem_ids") or []:
            problem_id_text = str(problem_id or "").strip()
            if problem_id_text:
                overlap_problem_ids.add(problem_id_text)
    if not overlap_problem_ids:
        return

    for problem in problems:
        problem_id = str(problem.get("id") or problem.get("problem_id") or "").strip()
        if problem_id not in overlap_problem_ids:
            continue
        flags = [str(flag) for flag in (problem.get("riskFlags") or problem.get("risk_flags") or []) if flag]
        flags.append(SOURCE_PROBLEM_BBOX_OVERLAP_RISK_FLAG)
        problem["riskFlags"] = list(dict.fromkeys(flags))
        if str(problem.get("reviewStatus") or problem.get("review_status") or "").strip() != "failed":
            problem["reviewStatus"] = "check_needed"


def _duplicate_problem_number_note(groups: Sequence[dict[str, Any]]) -> str:
    parts: list[str] = []
    for group in groups:
        if group.get("blocking") is False:
            continue
        label = str(group.get("numberLabel") or "").strip()
        occurrences = int(group.get("occurrencesPerNumber") or 0)
        if label and occurrences > 1:
            parts.append(f"{label} x{occurrences}")
    return f"Duplicate problem numbers: {', '.join(parts)}" if parts else ""


def _session_asset_path(value: Any) -> Path | None:
    if not value:
        return None
    raw = str(value)
    parsed = urlparse(raw)
    if parsed.scheme == "file":
        return Path(url2pathname(unquote(parsed.path))).resolve()
    if parsed.scheme:
        return None
    return Path(raw).resolve()


def _dark_pixel_ratio(image: Image.Image, *, threshold: int = CLASSIN_PREFLIGHT_INK_THRESHOLD) -> float:
    grayscale = image.convert("L")
    histogram = grayscale.histogram()
    total = max(1, sum(histogram))
    dark_pixels = sum(histogram[: max(0, min(256, threshold))])
    return float(dark_pixels) / float(total)


def _classin_preflight_issue(
    issue_type: str,
    *,
    severity: str,
    message: str,
    problem: dict[str, Any] | None = None,
    path: Path | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "type": issue_type,
        "severity": severity,
        "message": message,
    }
    if problem is not None:
        issue["problemId"] = str(problem.get("id") or "")
        issue["problemTitle"] = str(problem.get("title") or problem.get("problemNumber") or "")
    if path is not None:
        issue["path"] = str(path)
    if details:
        issue.update(details)
    return issue


def _problem_float(problem: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _coerce_float(problem.get(key))
        if value is not None:
            return value
    return None


def _problem_source_page_id(problem: dict[str, Any]) -> str:
    return str(
        problem.get("sourcePageId")
        or problem.get("source_page_id")
        or problem.get("pageId")
        or problem.get("page_id")
        or ""
    ).strip()


def _problem_bbox(problem: dict[str, Any]) -> tuple[float, float, float, float] | None:
    raw_bbox = problem.get("bbox") or problem.get("sourceBbox") or problem.get("source_bbox")
    if not isinstance(raw_bbox, dict):
        return None

    left = _coerce_float(raw_bbox.get("left"))
    if left is None:
        left = _coerce_float(raw_bbox.get("x"))
    top = _coerce_float(raw_bbox.get("top"))
    if top is None:
        top = _coerce_float(raw_bbox.get("y"))
    width = _coerce_float(raw_bbox.get("width"))
    if width is None:
        width = _coerce_float(raw_bbox.get("w"))
    height = _coerce_float(raw_bbox.get("height"))
    if height is None:
        height = _coerce_float(raw_bbox.get("h"))
    right = _coerce_float(raw_bbox.get("right"))
    bottom = _coerce_float(raw_bbox.get("bottom"))

    if left is None or top is None:
        return None
    if width is not None:
        right = left + width
    if height is not None:
        bottom = top + height
    if right is None or bottom is None:
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _bbox_payload(box: tuple[float, float, float, float]) -> dict[str, float]:
    left, top, right, bottom = box
    return {
        "left": round(left, 6),
        "top": round(top, 6),
        "width": round(right - left, 6),
        "height": round(bottom - top, 6),
    }


def _classin_source_bbox_overlap_issues(problems: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates_by_page: dict[str, list[tuple[tuple[float, float, float, float], dict[str, Any]]]] = {}
    for problem in problems:
        if not isinstance(problem, dict):
            continue
        source_page_id = _problem_source_page_id(problem)
        bbox = _problem_bbox(problem)
        if not source_page_id or bbox is None:
            continue
        candidates_by_page.setdefault(source_page_id, []).append((bbox, problem))

    issues: list[dict[str, Any]] = []
    threshold = CLASSIN_PREFLIGHT_SOURCE_BBOX_OVERLAP_RATIO
    for source_page_id, candidates in candidates_by_page.items():
        candidates.sort(
            key=lambda item: (
                item[0][1],
                item[0][0],
                str(item[1].get("id") or item[1].get("problem_id") or ""),
            )
        )
        for index, (bbox, problem) in enumerate(candidates):
            left, top, right, bottom = bbox
            area = (right - left) * (bottom - top)
            if area <= 0:
                continue
            for next_bbox, next_problem in candidates[index + 1:]:
                next_left, next_top, next_right, next_bottom = next_bbox
                next_area = (next_right - next_left) * (next_bottom - next_top)
                if next_area <= 0:
                    continue
                intersection_width = max(0.0, min(right, next_right) - max(left, next_left))
                intersection_height = max(0.0, min(bottom, next_bottom) - max(top, next_top))
                intersection_area = intersection_width * intersection_height
                if intersection_area <= 0:
                    continue
                overlap_area_ratio = intersection_area / min(area, next_area)
                if overlap_area_ratio < threshold:
                    continue
                union_area = area + next_area - intersection_area
                issues.append(
                    _classin_preflight_issue(
                        "source_problem_bbox_overlap",
                        severity="warning",
                        message=(
                            "두 문항의 원본 인식 영역이 크게 겹칩니다. "
                            "긴 지문 병합/하위 문항 분리 결과가 EDB에 중복 등록되지 않는지 확인해 주세요."
                        ),
                        problem=problem,
                        details={
                            "nextProblemId": str(next_problem.get("id") or next_problem.get("problem_id") or ""),
                            "nextProblemTitle": str(
                                next_problem.get("title") or next_problem.get("problemNumber") or ""
                            ),
                            "sourcePageId": source_page_id,
                            "overlapAreaRatio": round(overlap_area_ratio, 6),
                            "intersectionOverUnion": round(intersection_area / union_area, 6) if union_area > 0 else 0.0,
                            "intersectionAreaPx": round(intersection_area, 6),
                            "sourceBBoxOverlapThreshold": threshold,
                            "bbox": _bbox_payload(bbox),
                            "nextBbox": _bbox_payload(next_bbox),
                        },
                    )
                )
    return issues


def _classin_board_placement_overlap_issues(problems: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    placements: list[tuple[float, float, dict[str, Any]]] = []
    for problem in problems:
        if not isinstance(problem, dict):
            continue
        start_y_pages = _problem_float(problem, "startYPages", "start_y_pages")
        height_pages = _problem_float(
            problem,
            "actualHeightPages",
            "actual_height_pages",
            "actualContentHeightPages",
            "actual_content_height_pages",
        )
        if start_y_pages is None or height_pages is None:
            continue
        scale_ratio = _problem_float(problem, "placementScaleRatio", "placement_scale_ratio")
        if scale_ratio is None:
            scale_ratio = 1.0
        rendered_bottom_y_pages = start_y_pages + max(0.0, height_pages) * max(0.0, scale_ratio)
        placements.append((start_y_pages, rendered_bottom_y_pages, problem))

    placements.sort(key=lambda item: (item[0], str(item[2].get("id") or "")))

    issues: list[dict[str, Any]] = []
    tolerance = CLASSIN_PREFLIGHT_PLACEMENT_OVERLAP_TOLERANCE_PAGES
    for current, next_item in zip(placements, placements[1:]):
        current_start_y_pages, current_bottom_y_pages, problem = current
        next_start_y_pages, _next_bottom_y_pages, next_problem = next_item
        overlap_pages = current_bottom_y_pages - next_start_y_pages
        if overlap_pages <= tolerance:
            continue
        issues.append(
            _classin_preflight_issue(
                "board_placement_overlap",
                severity="warning",
                message=(
                    "문항 배치가 다음 문항 시작 위치를 침범할 수 있습니다. "
                    "긴 지문/확대 배율을 줄이거나 자동 재배치를 확인해 주세요."
                ),
                problem=problem,
                details={
                    "nextProblemId": str(next_problem.get("id") or ""),
                    "nextProblemTitle": str(
                        next_problem.get("title") or next_problem.get("problemNumber") or ""
                    ),
                    "startYPages": round(current_start_y_pages, 6),
                    "renderedBottomYPages": round(current_bottom_y_pages, 6),
                    "nextStartYPages": round(next_start_y_pages, 6),
                    "overlapPages": round(overlap_pages, 6),
                    "placementOverlapTolerancePages": tolerance,
                },
            )
        )
    return issues


def _classin_preflight_has_actionable_review_state(risk_flags: Sequence[str], review_status: str) -> bool:
    status = str(review_status or "").strip()
    if status == "failed":
        return True
    normalized_flags = {str(flag or "").strip() for flag in risk_flags if str(flag or "").strip()}
    if normalized_flags and normalized_flags.issubset(CLASSIN_PREFLIGHT_NON_ACTIONABLE_REVIEW_RISK_FLAGS):
        return "marker_document_continuation" not in normalized_flags
    if normalized_flags:
        return True
    return status == "check_needed" and not risk_flags


def _classin_handoff_preflight(ui_session: dict[str, Any]) -> dict[str, Any]:
    raw_problems = ui_session.get("problems")
    problems = raw_problems if isinstance(raw_problems, list) else []
    issues: list[dict[str, Any]] = []
    min_width = CLASSIN_PREFLIGHT_MIN_IMAGE_WIDTH_PX
    min_height = CLASSIN_PREFLIGHT_MIN_IMAGE_HEIGHT_PX

    for problem in problems:
        if not isinstance(problem, dict):
            continue
        if len(issues) >= CLASSIN_PREFLIGHT_MAX_ISSUES:
            break

        risk_flags = [str(flag) for flag in (problem.get("riskFlags") or []) if str(flag)]
        review_status = str(problem.get("reviewStatus") or "").strip()
        if _classin_preflight_has_actionable_review_state(risk_flags, review_status):
            issues.append(
                _classin_preflight_issue(
                    "review_flags_remaining",
                    severity="warning",
                    message="검수 플래그가 남아 있어 ClassIn에서 열기 전 원본 박스를 확인해야 합니다.",
                    problem=problem,
                    details={
                        "riskFlags": risk_flags,
                        "reviewStatus": review_status,
                    },
                )
            )
            if len(issues) >= CLASSIN_PREFLIGHT_MAX_ISSUES:
                break

        image_path = _session_asset_path(problem.get("imagePath"))
        if image_path is None or not image_path.is_file():
            issues.append(
                _classin_preflight_issue(
                    "missing_problem_image",
                    severity="error",
                    message="문항 이미지 파일을 찾을 수 없습니다.",
                    problem=problem,
                    path=image_path,
                )
            )
            if len(issues) >= CLASSIN_PREFLIGHT_MAX_ISSUES:
                break
            continue

        try:
            with Image.open(image_path) as image:
                width, height = image.size
                dark_pixel_ratio = _dark_pixel_ratio(image)
        except OSError as exc:
            issues.append(
                _classin_preflight_issue(
                    "unreadable_problem_image",
                    severity="error",
                    message=f"문항 이미지 파일을 열 수 없습니다: {exc}",
                    problem=problem,
                    path=image_path,
                )
            )
            if len(issues) >= CLASSIN_PREFLIGHT_MAX_ISSUES:
                break
            continue

        if width < min_width or height < min_height:
            issues.append(
                _classin_preflight_issue(
                    "small_problem_image",
                    severity="warning",
                    message=f"문항 이미지가 작습니다 ({width}x{height}px). ClassIn에서 확대 시 가독성을 확인해 주세요.",
                    problem=problem,
                    path=image_path,
                    details={
                        "width": width,
                        "height": height,
                        "minWidth": min_width,
                        "minHeight": min_height,
                    },
                )
            )

        if dark_pixel_ratio < CLASSIN_PREFLIGHT_MIN_DARK_PIXEL_RATIO:
            issues.append(
                _classin_preflight_issue(
                    "low_ink_problem_image",
                    severity="warning",
                    message=(
                        "문항 이미지에 실제 글자/선 픽셀이 거의 없습니다. "
                        "HWP 렌더 누락이나 잘못 잘린 박스인지 확인해 주세요."
                    ),
                    problem=problem,
                    path=image_path,
                    details={
                        "darkPixelRatio": round(dark_pixel_ratio, 6),
                        "minDarkPixelRatio": CLASSIN_PREFLIGHT_MIN_DARK_PIXEL_RATIO,
                        "darkPixelThreshold": CLASSIN_PREFLIGHT_INK_THRESHOLD,
                    },
                )
            )
            if len(issues) >= CLASSIN_PREFLIGHT_MAX_ISSUES:
                break

    if len(issues) < CLASSIN_PREFLIGHT_MAX_ISSUES:
        for issue in _classin_board_placement_overlap_issues(problems):
            issues.append(issue)
            if len(issues) >= CLASSIN_PREFLIGHT_MAX_ISSUES:
                break
    if len(issues) < CLASSIN_PREFLIGHT_MAX_ISSUES:
        for issue in _classin_source_bbox_overlap_issues(problems):
            issues.append(issue)
            if len(issues) >= CLASSIN_PREFLIGHT_MAX_ISSUES:
                break

    status = "passed" if not issues else "needs_attention"
    return {
        "status": status,
        "passed": not issues,
        "checkedProblemCount": len(problems),
        "issueCount": len(issues),
        "issues": issues,
        "thresholds": {
            "minProblemImageWidth": min_width,
            "minProblemImageHeight": min_height,
            "minDarkPixelRatio": CLASSIN_PREFLIGHT_MIN_DARK_PIXEL_RATIO,
            "darkPixelThreshold": CLASSIN_PREFLIGHT_INK_THRESHOLD,
            "placementOverlapTolerancePages": CLASSIN_PREFLIGHT_PLACEMENT_OVERLAP_TOLERANCE_PAGES,
            "sourceBboxOverlapRatio": CLASSIN_PREFLIGHT_SOURCE_BBOX_OVERLAP_RATIO,
        },
    }


def _collect_hwp_problem_count_mismatches(
    pages: list[PageModel],
    final_problem_ids_by_page_id: dict[str, list[str]] | None = None,
) -> tuple[dict[str, list[str]], list[str]]:
    by_source: dict[str, dict[str, Any]] = {}
    for page in pages:
        source_key = _hwp_source_key(page)
        if not source_key:
            continue
        bucket = by_source.setdefault(
            source_key,
            {
                "page_ids": [],
                "detected": 0,
                "signal": 0,
            },
        )
        bucket["page_ids"].append(page.page_id)
        if final_problem_ids_by_page_id is None:
            bucket["detected"] += _count_core_hwp_problems(page)
        else:
            bucket["detected"] += _count_core_hwp_problems(
                page,
                final_problem_ids_by_page_id.get(page.page_id, []),
            )
        bucket["signal"] = max(int(bucket["signal"]), _hwp_text_problem_signal_count(page.metadata))

    flags_by_page_id: dict[str, list[str]] = {}
    messages: list[str] = []
    for bucket in by_source.values():
        signal = int(bucket["signal"])
        if signal <= 0:
            continue
        detected = int(bucket["detected"])
        expected = max(0, signal)
        if expected <= 0 or detected == expected:
            continue
        is_oversegmentation = _is_hwp_oversegmentation(expected, detected)
        page_flags = ["hwp_problem_count_mismatch"]
        if is_oversegmentation:
            page_flags.append("hwp_oversegmentation")
        for page_id in bucket["page_ids"]:
            flags_by_page_id.setdefault(str(page_id), []).extend(page_flags)
        if is_oversegmentation:
            messages.append(
                f"HWP 내부 텍스트 기준 문항 수는 {expected}개인데 최종 감지 문항 수는 {detected}개입니다. "
                "과분할 가능성이 큽니다. 검수 화면에서 원본 페이지의 분리 상태를 먼저 확인해 주세요."
            )
        else:
            messages.append(
                f"HWP 내부 텍스트 기준 문항 수는 {expected}개인데 최종 감지 문항 수는 {detected}개입니다. 검수 화면에서 분리 상태를 확인해 주세요."
            )
    return flags_by_page_id, messages


def _page_quality_payload(page: PageModel | None) -> dict[str, Any]:
    metadata = page.metadata if page is not None else {}
    route_decision = metadata.get("route_decision") if isinstance(metadata, dict) else {}
    if not isinstance(route_decision, dict):
        route_decision = {}
    profile = route_decision.get("profile")
    if not isinstance(profile, dict):
        profile = {}
    ai_fallback = metadata.get("ai_fallback") if isinstance(metadata, dict) else {}
    if not isinstance(ai_fallback, dict):
        ai_fallback = {}

    risk_score = float(profile.get("overall_risk") or 0.0)
    segmentation_risk = float(profile.get("segmentation_risk") or 0.0)
    ocr_risk = float(profile.get("ocr_risk") or 0.0)
    grouping_risk = float(profile.get("grouping_risk") or 0.0)
    return {
        "riskScore": round(max(0.0, min(1.0, risk_score)), 4),
        "riskTier": str(profile.get("tier") or "green"),
        "parseConfidence": round(max(0.0, min(1.0, 1.0 - risk_score)), 4),
        "confidence": {
            "overall": round(max(0.0, min(1.0, 1.0 - risk_score)), 4),
            "segmentation": round(max(0.0, min(1.0, 1.0 - segmentation_risk)), 4),
            "ocr": round(max(0.0, min(1.0, 1.0 - ocr_risk)), 4),
            "grouping": round(max(0.0, min(1.0, 1.0 - grouping_risk)), 4),
        },
        "aiStatus": str(ai_fallback.get("status") or "unknown"),
    }


def build_ui_session(
    prepared_pages: list[PreparedPage],
    placements: list[dict[str, object]],
    output_dir: Path,
    edb_path: Path | None,
    source_paths: Sequence[str | Path],
    *,
    record_mode: str,
    ai_fallback_config: dict[str, Any] | None = None,
    ai_summary: dict[str, Any] | None = None,
    pages: list[PageModel] | None = None,
    template: LayoutTemplate | None = None,
    input_intent: str = "auto",
    input_notes: str | None = None,
    board_theme: str = DEFAULT_BOARD_THEME,
    crop_format: str = DEFAULT_CROP_FORMAT,
) -> dict[str, Any]:
    rendered_page_paths = [Path(page.source_path).resolve() for page in prepared_pages]
    resolved_input_intent = _normalize_input_intent(input_intent)
    placement_problem_ids_by_page: dict[str, list[str]] = {}
    for placement in placements:
        source_page_id = str(placement.get("source_page_id") or "")
        problem_id = str(placement.get("problem_id") or "")
        if source_page_id and problem_id:
            placement_problem_ids_by_page.setdefault(source_page_id, []).append(problem_id)
    hwp_counts_match = _hwp_problem_counts_match(pages or [], placement_problem_ids_by_page)
    warning_messages: list[str] = []
    if (
        placements
        and len(placements) <= len(prepared_pages)
        and resolved_input_intent not in {"single-problem", "page-as-is"}
        and not hwp_counts_match
    ):
        warning_messages.append(
            "감지된 문항 수가 원본 페이지 수와 비슷합니다. 여러 문제가 있는 페이지라면 검수 화면에서 분리 상태를 확인해 주세요."
        )
    resolved_template = template or LayoutTemplate(
        name="academy-default",
        board_page_count=max(50, len(placements) * 2 or 50),
        base_slot_height_pages=ONE_PROBLEM_SLOT_HEIGHT_PAGES,
        metadata={"placement_mode": "one-problem-per-page"},
    )

    # Map page_id → PageModel for risk-flag lookup, and page_id → list[problem_id]
    # so the UI can group detected problems by their source page in the review view.
    if pages:
        _annotate_cross_page_passage_groups(pages)
    pages_by_id: dict[str, PageModel] = {}
    if pages:
        for page in pages:
            pages_by_id[page.page_id] = page
    problem_metadata_by_id: dict[str, dict[str, Any]] = {}
    for page in pages_by_id.values():
        for problem in page.problems:
            problem_metadata_by_id[problem.unit_id] = dict(problem.metadata)
    base_page_risk_flags: dict[str, list[str]] = {
        page_id: _collect_page_risk_flags(page.metadata)
        for page_id, page in pages_by_id.items()
    }

    problems: list[dict[str, Any]] = []
    problems_by_page: dict[str, list[str]] = {}
    for index, placement in enumerate(placements):
        crop_path = Path(str(placement["crop_path"])).resolve()
        source_path = Path(str(placement["source_path"])).resolve()
        source_page_id = str(placement["source_page_id"])
        bbox = placement.get("bbox") or {}
        page_quality = _page_quality_payload(pages_by_id.get(source_page_id))
        page_flags = base_page_risk_flags.get(source_page_id, [])
        # Only propagate "this specific problem may be merged / auto-grouped"
        # reasons to per-problem flags; page-wide signals stay on the page.
        problem_flags = list(placement.get("risk_flags") or [])
        problem_flags.extend(reason for reason in page_flags if reason in _GLOBAL_RISK_REASONS)
        problem_id = str(placement["problem_id"])
        problem_metadata = problem_metadata_by_id.get(problem_id)
        if _problem_passage_continues_across_pages(problem_metadata):
            problem_flags.append(PASSAGE_CROSS_PAGE_MERGE_CHECK_RISK_FLAG)
        problem_flags = list(dict.fromkeys(str(reason) for reason in problem_flags if reason))
        passage_payload = _problem_passage_payload(problem_metadata)
        processing_step = _normalize_processing_step(
            placement.get("processing_step") or placement.get("step")
        )
        problems.append(
            {
                "id": problem_id,
                "title": _normalize_problem_title(
                    str(placement.get("title") or ""),
                    index,
                    source_page_id,
                    int(placement["problem_number"]) if str(placement.get("problem_number") or "").isdigit() else None,
                ),
                "problemNumber": int(placement["problem_number"]) if str(placement.get("problem_number") or "").isdigit() else None,
                "subject": str(placement["subject"]),
                "imagePath": _to_file_uri(crop_path),
                "sourceImagePath": _to_file_uri(source_path),
                "sourceFileName": source_path.name,
                "boardRenderPath": _to_file_uri(placement.get("board_render_path")),
                "actualHeightPages": float(placement["actual_content_height_pages"]),
                "overflowAllowed": bool(placement["overflow_allowed"]),
                "readingHeavy": bool(placement["overflow_allowed"]),
                "sourcePageId": source_page_id,
                "startYPages": float(placement["start_y_pages"]),
                "snappedNextStartYPages": float(placement["snapped_next_start_y_pages"]),
                "overflowAmountPages": float(placement["overflow_amount_pages"]),
                "overflowViolation": bool(placement["overflow_violation"]),
                "slotSpanCount": int(placement["slot_span_count"]),
                "placementXRatio": float(placement.get("placement_x_ratio") or 0.0),
                "placementYRatio": float(placement.get("placement_y_ratio") or 0.0),
                "placementScaleRatio": float(placement.get("placement_scale_ratio") or 1.0),
                "step": processing_step,
                "processingStep": processing_step,
                "recordMode": str(placement.get("record_mode") or record_mode),
                "textRecordCount": int(placement.get("text_record_count", 0)),
                "imageRecordCount": int(placement.get("image_record_count", 0)),
                "bbox": {
                    "left": float(bbox.get("left", 0.0)),
                    "top": float(bbox.get("top", 0.0)),
                    "width": float(bbox.get("width", 0.0)),
                    "height": float(bbox.get("height", 0.0)),
                },
                "riskFlags": problem_flags,
                "reviewStatus": "check_needed" if problem_flags else "normal",
                "parseConfidence": page_quality["parseConfidence"],
                "confidence": page_quality["confidence"],
                "aiStatus": page_quality["aiStatus"],
                **passage_payload,
            }
        )
        problems_by_page.setdefault(source_page_id, []).append(problem_id)

    hwp_flags_by_page_id, hwp_warning_messages = _collect_hwp_problem_count_mismatches(
        list(pages_by_id.values()),
        problems_by_page,
    )
    warning_messages.extend(hwp_warning_messages)
    page_risk_flags: dict[str, list[str]] = {
        page_id: list(
            dict.fromkeys(
                [
                    *base_page_risk_flags.get(page_id, []),
                    *hwp_flags_by_page_id.get(page_id, []),
                ]
            )
        )
        for page_id in pages_by_id
    }

    pages_payload: list[dict[str, Any]] = []
    for prepared_page in prepared_pages:
        width, height = prepared_page.image.size
        resolved_path = Path(prepared_page.source_path).resolve()
        page_quality = _page_quality_payload(pages_by_id.get(prepared_page.page_id))
        problem_ids = problems_by_page.get(prepared_page.page_id, [])
        risk_flags = page_risk_flags.get(prepared_page.page_id, [])
        review_status = "failed" if not problem_ids else "check_needed" if risk_flags else "normal"
        pages_payload.append(
            {
                "id": prepared_page.page_id,
                "pageNumber": prepared_page.page_number,
                "sourceImageUri": _to_file_uri(resolved_path),
                "sourceImagePath": str(resolved_path),
                "width": int(width),
                "height": int(height),
                "problemIds": problem_ids,
                "riskFlags": risk_flags,
                "reviewStatus": review_status,
                **page_quality,
            }
        )

    problem_counts = _session_problem_count_payload(problems)
    duplicate_problem_number_groups = _session_duplicate_problem_number_groups(problems)
    _mark_duplicate_problem_number_review_flags(problems, duplicate_problem_number_groups)
    blocking_duplicate_problem_number_groups = [
        group for group in duplicate_problem_number_groups if group.get("blocking") is not False
    ]
    source_problem_overlap_groups = _session_source_problem_overlap_groups(problems)
    _mark_source_problem_overlap_review_flags(problems, source_problem_overlap_groups)

    return {
        "session_name": output_dir.name,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "data_source": "question_export",
        "output_dir": str(output_dir.resolve()),
        "source_mode": "batch" if len(source_paths) > 1 else "single",
        "input_intent": resolved_input_intent,
        "input_notes": (input_notes or "").strip(),
        "input_file_count": len(source_paths),
        "input_files": [str(Path(path).resolve()) for path in source_paths],
        "source_page_count": len(prepared_pages),
        **problem_counts,
        "duplicate_problem_number_groups": duplicate_problem_number_groups,
        "duplicateProblemNumberGroups": duplicate_problem_number_groups,
        "duplicate_problem_number_group_count": len(duplicate_problem_number_groups),
        "duplicateProblemNumberGroupCount": len(duplicate_problem_number_groups),
        "blocking_duplicate_problem_number_groups": blocking_duplicate_problem_number_groups,
        "blockingDuplicateProblemNumberGroups": blocking_duplicate_problem_number_groups,
        "blocking_duplicate_problem_number_group_count": len(blocking_duplicate_problem_number_groups),
        "blockingDuplicateProblemNumberGroupCount": len(blocking_duplicate_problem_number_groups),
        "source_problem_overlap_groups": source_problem_overlap_groups,
        "sourceProblemOverlapGroups": source_problem_overlap_groups,
        "source_problem_overlap_group_count": len(source_problem_overlap_groups),
        "sourceProblemOverlapGroupCount": len(source_problem_overlap_groups),
        "export_mode": "question",
        "record_mode": record_mode,
        "board_theme": _resolve_board_theme(board_theme),
        "crop_format": crop_format,
        "pages_json_path": str((output_dir / "pages.json").resolve()),
        "placements_json_path": str((output_dir / "placements.json").resolve()),
        "board_render_dir": str(
            Path(str(placements[0]["board_render_path"])).resolve().parent
            if placements and placements[0].get("board_render_path")
            else (output_dir / "problem_cutouts").resolve()
        ),
        "edb_path": str(edb_path.resolve()) if edb_path else None,
        "edb_file_uri": _to_file_uri(edb_path),
        "rendered_page_paths": [str(path) for path in rendered_page_paths],
        "rendered_page_file_uris": [_to_file_uri(path) for path in rendered_page_paths],
        "template": _template_to_dict(resolved_template),
        "ai_fallback": ai_fallback_config,
        "ai_summary": ai_summary,
        "warning_messages": warning_messages,
        "problems": problems,
        "pages": pages_payload,
    }


def write_ui_session_bundle(output_dir: Path, ui_session: dict[str, Any], *, sync_ui: bool) -> tuple[Path, Path | None]:
    session_path = output_dir / "ui_session.json"
    session_path.write_text(json.dumps(ui_session, ensure_ascii=False, indent=2), encoding="utf-8")

    synced_path: Path | None = None
    if sync_ui:
        synced_path = Path(__file__).resolve().parent / "ui_prototype" / "generated_session.js"
        synced_path.write_text(
            "window.EDB_UI_SESSION = " + json.dumps(ui_session, ensure_ascii=False, indent=2) + ";\n",
            encoding="utf-8",
        )
    return session_path, synced_path


def write_classin_handoff_manifest(
    output_dir: Path,
    *,
    source_paths: Sequence[Path],
    edb_path: Path,
    ui_session: dict[str, Any],
    summary: dict[str, Any],
    template: LayoutTemplate,
) -> tuple[Path, Path]:
    expected_record_count = int(summary.get("record_count") or len(summary.get("placements") or []))
    review_summary = ui_session.get("reviewSummary") or ui_session.get("review_summary") or {}
    duplicate_problem_number_groups = (
        ui_session.get("duplicateProblemNumberGroups")
        or ui_session.get("duplicate_problem_number_groups")
        or []
    )
    if not isinstance(duplicate_problem_number_groups, list):
        duplicate_problem_number_groups = []
    blocking_duplicate_problem_number_groups = (
        ui_session.get("blockingDuplicateProblemNumberGroups")
        or ui_session.get("blocking_duplicate_problem_number_groups")
        or [
            group
            for group in duplicate_problem_number_groups
            if isinstance(group, dict) and group.get("blocking") is not False
        ]
    )
    if not isinstance(blocking_duplicate_problem_number_groups, list):
        blocking_duplicate_problem_number_groups = []
    duplicate_problem_number_note = _duplicate_problem_number_note(duplicate_problem_number_groups)
    classin_preflight = _classin_handoff_preflight(ui_session)
    raw_problems = ui_session.get("problems")
    problems = raw_problems if isinstance(raw_problems, list) else []
    passage_groups = _session_passage_groups(problems)
    cross_page_passage_group_count = sum(
        1 for group in passage_groups if group.get("continuesAcrossPages")
    )
    ready_for_classin = bool(classin_preflight.get("passed")) and not blocking_duplicate_problem_number_groups
    handoff_status = "ready_for_classin_review" if ready_for_classin else "needs_attention_before_classin"
    payload = {
        "status": handoff_status,
        "readyForClassIn": ready_for_classin,
        "manualReviewRequired": True,
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "sourcePaths": [str(path.resolve()) for path in source_paths],
        "outputDir": str(output_dir.resolve()),
        "edbPath": str(edb_path.resolve()),
        "edbFileName": edb_path.name,
        "expectedRecordCount": expected_record_count,
        "expectedCoreProblemCount": int(ui_session.get("core_problem_count") or 0),
        "expectedSupplementalItemCount": int(ui_session.get("supplemental_item_count") or 0),
        "detectedProblemCount": int(ui_session.get("detected_problem_count") or expected_record_count),
        "sourcePageCount": int(ui_session.get("source_page_count") or 0),
        "classinPageCountHint": int(template.board_page_count),
        "recordMode": str(summary.get("record_mode") or ui_session.get("record_mode") or ""),
        "cropFormat": str(summary.get("crop_format") or ui_session.get("crop_format") or ""),
        "boardTheme": str(summary.get("board_theme") or ui_session.get("board_theme") or ""),
        "duplicateProblemNumberGroups": duplicate_problem_number_groups,
        "blockingDuplicateProblemNumberGroups": blocking_duplicate_problem_number_groups,
        "duplicateProblemNumberNote": duplicate_problem_number_note,
        "passageGroups": passage_groups,
        "passageGroupCount": len(passage_groups),
        "passageProblemCount": sum(int(group.get("problemCount") or 0) for group in passage_groups),
        "crossPagePassageGroupCount": cross_page_passage_group_count,
        "classinPreflight": classin_preflight,
        "classin_preflight": classin_preflight,
        "reviewRiskCounts": review_summary.get("riskFlagCounts", {}) if isinstance(review_summary, dict) else {},
        "classinReviewChecklist": [
            "ClassIn에서 EDB 파일 열기",
            "문항 수와 순서가 기대값과 일치하는지 확인",
            "각 문항 이미지가 잘리지 않고 읽히는지 확인",
            "긴 지문/공통 지문 그룹이 하위 문항과 함께 자연스럽게 배치됐는지 확인",
            "보충 자료/이어지는 자료가 문항 뒤에 자연스럽게 배치됐는지 확인",
            "확대/축소와 페이지 이동 시 썸네일/보드가 깨지지 않는지 확인",
        ],
        "manualReviewResult": {
            "classinOpened": None,
            "recordCountOk": None,
            "orderOk": None,
            "readabilityOk": None,
            "supplementalItemsOk": None,
            "notes": "",
        },
    }
    json_path = output_dir / "classin_handoff.json"
    markdown_path = output_dir / "classin_handoff.md"
    payload["classinHandoffMarkdownPath"] = str(markdown_path.resolve())
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    checklist = "\n".join(f"- [ ] {item}" for item in payload["classinReviewChecklist"])
    duplicate_problem_number_lines = (
        ["", f"- {duplicate_problem_number_note}"]
        if duplicate_problem_number_note
        else []
    )
    passage_group_lines: list[str] = []
    if passage_groups:
        passage_group_lines = ["", "## Passage Groups"]
        for group in passage_groups:
            label = str(group.get("numberLabel") or group.get("groupId") or "").strip()
            page_label = ", ".join(str(page_id) for page_id in group.get("sourcePageIds") or [])
            status_parts = [
                f"{int(group.get('problemCount') or 0)} problems",
                f"pages {page_label}" if page_label else "",
                "cross-page" if group.get("continuesAcrossPages") else "single-page",
            ]
            status = " · ".join(part for part in status_parts if part)
            passage_group_lines.append(
                f"- `{group.get('groupId')}` {label}"
                + (f" · {status}" if status else "")
            )
    if classin_preflight["passed"]:
        preflight_lines = ["- OK: no automatic asset issues found."]
    else:
        preflight_lines = [
            f"- `{issue['type']}` ({issue['severity']}): {issue['message']}"
            + (f" [{issue.get('problemId')}]" if issue.get("problemId") else "")
            for issue in classin_preflight["issues"]
        ]
    markdown_path.write_text(
        "\n".join(
            [
                "# ClassIn EDB Handoff",
                "",
                f"- Handoff status: `{payload['status']}`",
                f"- Ready for ClassIn: {'yes' if payload['readyForClassIn'] else 'no'}",
                f"- EDB: `{payload['edbPath']}`",
                f"- Expected records: {payload['expectedRecordCount']}",
                f"- Core problems: {payload['expectedCoreProblemCount']}",
                f"- Supplemental items: {payload['expectedSupplementalItemCount']}",
                f"- ClassIn page hint: {payload['classinPageCountHint']}",
                *duplicate_problem_number_lines,
                *passage_group_lines,
                "",
                "## Manual Checklist",
                checklist,
                "",
                "## ClassIn Preflight",
                f"- Status: {classin_preflight['status']}",
                f"- Checked problems: {classin_preflight['checkedProblemCount']}",
                f"- Issues: {classin_preflight['issueCount']}",
                *preflight_lines,
                "",
                "## Notes",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return json_path.resolve(), markdown_path.resolve()


def _classin_handoff_session_fields(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}

    status = str(payload.get("status") or "").strip()
    ready = (
        bool(payload.get("readyForClassIn"))
        if "readyForClassIn" in payload
        else status == "ready_for_classin_review"
    )
    preflight = payload.get("classinPreflight") if isinstance(payload.get("classinPreflight"), dict) else {}
    issue_count = int(preflight.get("issueCount") or preflight.get("issue_count") or 0)
    return {
        "classinHandoffStatus": status,
        "classin_handoff_status": status,
        "readyForClassIn": ready,
        "ready_for_classin": ready,
        "classinPreflight": preflight,
        "classin_preflight": preflight,
        "classinPreflightStatus": str(preflight.get("status") or ""),
        "classin_preflight_status": str(preflight.get("status") or ""),
        "classinPreflightIssueCount": issue_count,
        "classin_preflight_issue_count": issue_count,
    }


def normalize_text_payload(text: str | None) -> str:
    if not text:
        return ""
    lines = [line.strip() for line in text.replace("\r", "\n").split("\n")]
    cleaned = [line for line in lines if line]
    return "\n".join(cleaned)


def choose_block_record_mode(block: ContentBlock, *, text_confidence_threshold: float) -> str:
    if block.metadata.get("force_image_record"):
        return "image"
    if block.block_type in IMAGE_ONLY_BLOCK_TYPES:
        return "image"
    text = normalize_text_payload(block.text)
    if not text:
        return "image"
    if block.block_type not in TEXT_ELIGIBLE_BLOCK_TYPES:
        return "image"
    confidence = block.confidence if block.confidence is not None else 0.0
    if confidence < text_confidence_threshold:
        return "image"
    return "text"


def resolve_font_size(block: ContentBlock, scale: float) -> int:
    base_size = block.style.font_size if block.style and block.style.font_size else max(14.0, block.bbox.height * 0.32)
    scaled = base_size * max(scale, 0.25) * 0.9
    return int(max(10, min(40, round(scaled))))


def placement_inputs(problem_entries: list[ProblemEntry]) -> list[ProblemLayoutInput]:
    return [
        ProblemLayoutInput(
            problem_id=entry.problem_id,
            subject=entry.subject,
            actual_content_height_pages=entry.actual_height_pages,
            overflow_allowed=entry.overflow_allowed,
            reading_heavy=entry.reading_heavy,
            metadata={
                "title": entry.title,
                "problem_number": entry.problem_number,
                "crop_path": str(entry.crop_path),
                "board_render_path": str(entry.board_render_path),
                "source_page_id": entry.source_page_id,
                "source_path": entry.source_path,
                "bbox": {
                    "left": entry.bounds.left,
                    "top": entry.bounds.top,
                    "width": entry.bounds.width,
                    "height": entry.bounds.height,
                },
                "risk_flags": list(entry.risk_flags),
                "processing_step": _normalize_processing_step(entry.processing_step),
            },
        )
        for entry in problem_entries
    ]


def _resize_to_target_width(image: Image.Image, target_width_px: int) -> Image.Image:
    if target_width_px <= 0 or image.width == target_width_px:
        return image
    aspect = image.height / max(image.width, 1)
    new_height = max(1, int(round(target_width_px * aspect)))
    return image.resize((target_width_px, new_height), Image.Resampling.LANCZOS)


def _v1_source_layout_transform(problem_entries: list[ProblemEntry]) -> tuple[float, float, float] | None:
    if len(problem_entries) <= 1:
        return None
    bounds = [entry.bounds for entry in problem_entries if entry.bounds.width > 0 and entry.bounds.height > 0]
    if len(bounds) != len(problem_entries):
        return None

    left = min(bound.left for bound in bounds)
    top = min(bound.top for bound in bounds)
    right = max(bound.left + bound.width for bound in bounds)
    bottom = max(bound.top + bound.height for bound in bounds)
    layout_width = right - left
    layout_height = bottom - top
    if layout_width <= 0 or layout_height <= 0:
        return None

    max_width = CANVAS_HEIGHT - V1_LAYOUT_MARGIN_X_PX * 2
    max_height = CANVAS_WIDTH * V1_LAYOUT_MAX_HEIGHT_PAGES - V1_LAYOUT_MARGIN_Y_PX * 2
    scale = min(max_width / layout_width, max_height / layout_height)
    scale = max(0.2, min(1.0, scale))
    return left, top, scale


def _v1_default_display_width_px(template: LayoutTemplate) -> float:
    legacy_width = CANVAS_HEIGHT * template.fixed_left_zone_ratio - LEFT_MARGIN_PX - RIGHT_PADDING_PX
    max_width = CANVAS_HEIGHT - LEFT_MARGIN_PX - RIGHT_PADDING_PX
    return max(legacy_width, min(V1_DEFAULT_DISPLAY_WIDTH_PX, max_width))


def build_image_only_records(
    problem_entries: list[ProblemEntry],
    template: LayoutTemplate,
    *,
    dark_board: bool = True,
    board_theme: str = DEFAULT_BOARD_THEME,
    crop_format: str = DEFAULT_CROP_FORMAT,
) -> tuple[list[bytes], list[dict[str, object]]]:
    placements = place_problems(placement_inputs(problem_entries), template=template)
    entries_by_problem_id = {entry.problem_id: entry for entry in problem_entries}
    if crop_format == CROP_FORMAT_V2:
        # v2 displays every problem at a fixed pixel width on the board, so
        # the image is resized to V2_TARGET_IMAGE_WIDTH_PX before encoding
        # and the width_hint is computed from that exact value rather than
        # from the template's fixed_left_zone_ratio.
        target_image_width_px = float(V2_TARGET_IMAGE_WIDTH_PX)
        available_width_px = target_image_width_px
    else:
        target_image_width_px = 0.0  # 0 means "do not resize"
        available_width_px = _v1_default_display_width_px(template)

    records: list[bytes] = []
    placement_summaries: list[dict[str, object]] = []
    next_record_id = 0
    preserve_source_layout = bool(template.metadata.get("preserve_source_layout"))
    source_layout = (
        _v1_source_layout_transform(problem_entries)
        if crop_format == CROP_FORMAT_V1 and preserve_source_layout
        else None
    )

    for placement in placements:
        entry = entries_by_problem_id[placement.problem_id]
        processing_step = _normalize_processing_step(
            entry.processing_step or placement.metadata.get("processing_step")
        )
        crop_path = Path(str(placement.metadata["crop_path"]))
        board_render_path = Path(str(placement.metadata["board_render_path"]))
        loaded_crop = Image.open(crop_path)
        crop_image = loaded_crop.convert("RGBA" if "A" in loaded_crop.getbands() else "RGB")
        if dark_board and processing_step == PROCESSING_STEP_RECONSTRUCT:
            board_image = _build_transparent_reconstruction_image(
                crop_image,
                board_theme=board_theme,
            )
        elif dark_board:
            board_image = _load_board_export_image(
                board_render_path,
                crop_image,
                board_theme=board_theme,
                target_size=crop_image.size if crop_format == CROP_FORMAT_V1 else None,
            )
        else:
            board_image = crop_image
        if target_image_width_px > 0:
            board_image = _resize_to_target_width(board_image, int(target_image_width_px))
        image_bytes, image_format = _encode_image_bytes(board_image, quality=92)
        if crop_format == CROP_FORMAT_V2:
            secondary_bytes = build_tight_crop_image_bytes(
                image_bytes, format_hint=image_format, quality=88
            )
        else:
            secondary_bytes = build_preview_image_bytes(
                image_bytes, max_size=(768, 768), format_hint=image_format, quality=88
            )

        if source_layout is not None:
            layout_left, layout_top, layout_scale = source_layout
            scale_ratio = layout_scale
            rendered_width_px = entry.bounds.width * layout_scale
            rendered_height_px = entry.bounds.height * layout_scale
            x_px = V1_LAYOUT_MARGIN_X_PX + (entry.bounds.left - layout_left) * layout_scale
            y_px = V1_LAYOUT_MARGIN_Y_PX + (entry.bounds.top - layout_top) * layout_scale
            width_hint = normalize_width_px(rendered_width_px)
            height_hint = normalize_height_px(rendered_height_px, page_count_hint=template.board_page_count)
        elif crop_format == CROP_FORMAT_V2:
            base_rendered_width_px = float(board_image.width)
            base_rendered_height_px = float(board_image.height)
            scale_ratio = _problem_scale_ratio(
                entry,
                placement,
                base_rendered_width_px,
                base_rendered_height_px,
            )
            if abs(scale_ratio - 1.0) > 0.001:
                scaled_width = max(1, int(round(board_image.width * scale_ratio)))
                scaled_height = max(1, int(round(board_image.height * scale_ratio)))
                board_image = board_image.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS)
                image_bytes, image_format = _encode_image_bytes(board_image, quality=92)
                secondary_bytes = build_tight_crop_image_bytes(
                    image_bytes, format_hint=image_format, quality=88
                )
            width_hint = normalize_width_px(float(board_image.width))
            height_hint = normalize_height_px(
                float(board_image.height), page_count_hint=template.board_page_count
            )
            rendered_width_px = float(board_image.width)
            rendered_height_px = float(board_image.height)
            x_px = _problem_origin_x_px(entry, rendered_width_px)
            y_px = _problem_origin_y_px(entry, placement, rendered_height_px)
        else:
            base_rendered_width_px = available_width_px
            base_rendered_height_px = available_width_px * (
                float(board_image.height) / max(float(board_image.width), 1.0)
            )
            scale_ratio = _problem_scale_ratio(
                entry,
                placement,
                base_rendered_width_px,
                base_rendered_height_px,
            )
            height_px = base_rendered_height_px * scale_ratio
            width_hint = normalize_width_px(available_width_px * scale_ratio)
            height_hint = normalize_height_px(height_px, page_count_hint=template.board_page_count)
            rendered_width_px = available_width_px * scale_ratio
            rendered_height_px = height_px
            x_px = _problem_origin_x_px(entry, rendered_width_px)
            y_px = _problem_origin_y_px(entry, placement, rendered_height_px)

        parent_record_id = next_record_id
        records.append(
            build_image_record(
                ImageRecordSpec(
                    record_id=parent_record_id,
                    image_primary=image_bytes,
                    image_secondary=secondary_bytes,
                    x=normalize_x_px(x_px),
                    y=normalize_y_px(y_px, page_count_hint=template.board_page_count),
                    width_hint=width_hint,
                    height_hint=height_hint,
                )
            )
        )
        next_record_id += 1
        image_record_count = 1

        placement_summaries.append(
            {
                "problem_id": placement.problem_id,
                "title": placement.metadata["title"],
                "problem_number": placement.metadata.get("problem_number"),
                "subject": str(placement.subject),
                "crop_path": str(crop_path),
                "board_render_path": str(board_render_path),
                "source_page_id": placement.metadata["source_page_id"],
                "source_path": placement.metadata["source_path"],
                "start_y_pages": placement.start_y_pages,
                "actual_content_height_pages": placement.actual_content_height_pages,
                "actual_bottom_y_pages": placement.actual_bottom_y_pages,
                "snapped_next_start_y_pages": placement.snapped_next_start_y_pages,
                "overflow_allowed": placement.overflow_allowed,
                "overflow_amount_pages": placement.overflow_amount_pages,
                "overflow_violation": placement.overflow_violation,
                "slot_span_count": placement.slot_span_count,
                "bbox": placement.metadata["bbox"],
                "risk_flags": list(placement.metadata.get("risk_flags") or []),
                "record_mode": "image-only",
                "step": processing_step,
                "processing_step": processing_step,
                "text_record_count": 0,
                "image_record_count": image_record_count,
                "board_theme": _resolve_board_theme(board_theme),
                "crop_format": crop_format,
                "image_pixel_width": int(board_image.width),
                "image_pixel_height": int(board_image.height),
                "rendered_width_px": float(rendered_width_px),
                "rendered_height_px": float(rendered_height_px),
                "placement_x_ratio": float(_clamp_placement_x_ratio(entry.placement_x_ratio) or 0.0),
                "placement_y_ratio": float(_clamp_placement_y_ratio(entry.placement_y_ratio) or 0.0),
                "placement_scale_ratio": float(scale_ratio),
            }
        )

    return records, placement_summaries


def build_mixed_records(
    problem_entries: list[ProblemEntry],
    template: LayoutTemplate,
    *,
    output_dir: Path,
    text_confidence_threshold: float,
    dark_board: bool = True,
    board_theme: str = DEFAULT_BOARD_THEME,
) -> tuple[list[bytes], list[dict[str, object]]]:
    placements = place_problems(placement_inputs(problem_entries), template=template)
    entries_by_problem_id = {entry.problem_id: entry for entry in problem_entries}
    available_width_px = CANVAS_HEIGHT * template.fixed_left_zone_ratio - LEFT_MARGIN_PX - RIGHT_PADDING_PX
    block_crop_dir = output_dir / "block_crops"
    block_crop_dir.mkdir(parents=True, exist_ok=True)
    chalk_color = _resolve_chalk_color(board_theme)

    records: list[bytes] = []
    placement_summaries: list[dict[str, object]] = []
    next_record_id = 0

    for placement in placements:
        entry = entries_by_problem_id[placement.problem_id]
        scale_ratio = _problem_scale_ratio(
            entry,
            placement,
            available_width_px,
            placement.actual_content_height_pages * CANVAS_WIDTH,
        )
        scaled_available_width_px = available_width_px * scale_ratio
        scale = scaled_available_width_px / max(entry.bounds.width, 1.0)
        problem_origin_x_px = _problem_origin_x_px(entry, scaled_available_width_px)
        problem_origin_y_px = _problem_origin_y_px(
            entry,
            placement,
            placement.actual_content_height_pages * CANVAS_WIDTH * scale_ratio,
        )
        block_summaries: list[dict[str, object]] = []
        text_record_count = 0
        image_record_count = 0

        for block in entry.blocks:
            x_px = problem_origin_x_px + max(0.0, block.bbox.left - entry.bounds.left) * scale
            y_px = problem_origin_y_px + max(0.0, block.bbox.top - entry.bounds.top) * scale
            width_px = max(40.0, min(scaled_available_width_px, block.bbox.width * scale))
            height_px = max(22.0, block.bbox.height * scale)
            record_mode = choose_block_record_mode(block, text_confidence_threshold=text_confidence_threshold)

            if record_mode == "text":
                text_payload = normalize_text_payload(block.text)
                records.append(
                    build_text_record(
                        TextRecordSpec(
                            record_id=next_record_id,
                            text=text_payload,
                            x=normalize_x_px(x_px),
                            y=normalize_y_px(y_px, page_count_hint=template.board_page_count),
                            width_hint=normalize_width_px(width_px),
                            font_size=resolve_font_size(block, scale),
                        )
                    )
                )
                text_record_count += 1
            else:
                crop = entry.prepared_page.image.crop(
                    (
                        int(block.bbox.left),
                        int(block.bbox.top),
                        int(block.bbox.right),
                        int(block.bbox.bottom),
                    )
                )
                crop_name = f"p{len(placement_summaries) + 1:03d}_b{len(block_summaries) + 1:03d}_{hashlib.sha1((entry.problem_id + block.block_id).encode('utf-8', errors='ignore')).hexdigest()[:8]}.png"
                crop_path = block_crop_dir / crop_name
                crop.save(crop_path)  # Save original for UI/debugging
                board_crop = _extract_problem_cutout(crop, chalk_color=chalk_color) if dark_board else crop.convert("RGB")
                image_bytes, image_format = _encode_image_bytes(board_crop, quality=92)
                preview_bytes = build_preview_image_bytes(image_bytes, max_size=(768, 768), format_hint=image_format, quality=88)
                records.append(
                    build_image_record(
                        ImageRecordSpec(
                            record_id=next_record_id,
                            image_primary=image_bytes,
                            image_secondary=preview_bytes,
                            x=normalize_x_px(x_px),
                            y=normalize_y_px(y_px, page_count_hint=template.board_page_count),
                            width_hint=normalize_width_px(width_px),
                            height_hint=normalize_height_px(height_px, page_count_hint=template.board_page_count),
                        )
                    )
                )
                image_record_count += 1

            block_summaries.append(
                {
                    "block_id": block.block_id,
                    "block_type": str(block.block_type),
                    "record_mode": record_mode,
                    "text_present": bool(normalize_text_payload(block.text)),
                    "confidence": block.confidence,
                    "bbox": {
                        "left": block.bbox.left,
                        "top": block.bbox.top,
                        "width": block.bbox.width,
                        "height": block.bbox.height,
                    },
                }
            )
            next_record_id += 1

        if not block_summaries:
            fallback_image = Image.open(entry.crop_path).convert("RGB")
            board_fallback = Image.open(entry.board_render_path) if dark_board else fallback_image
            image_bytes, image_format = _encode_image_bytes(board_fallback, quality=92)
            preview_bytes = build_preview_image_bytes(image_bytes, max_size=(768, 768), format_hint=image_format, quality=88)
            records.append(
                build_image_record(
                    ImageRecordSpec(
                        record_id=next_record_id,
                        image_primary=image_bytes,
                        image_secondary=preview_bytes,
                        x=normalize_x_px(problem_origin_x_px),
                        y=normalize_y_px(problem_origin_y_px, page_count_hint=template.board_page_count),
                        width_hint=normalize_width_px(scaled_available_width_px),
                        height_hint=normalize_height_px(
                            placement.actual_content_height_pages * CANVAS_WIDTH * scale_ratio,
                            page_count_hint=template.board_page_count,
                        ),
                    )
                )
            )
            image_record_count += 1
            next_record_id += 1

        placement_summaries.append(
            {
                "problem_id": placement.problem_id,
                "title": entry.title,
                "problem_number": entry.problem_number,
                "subject": str(entry.subject),
                "crop_path": str(entry.crop_path),
                "board_render_path": str(entry.board_render_path),
                "source_page_id": entry.source_page_id,
                "source_path": entry.source_path,
                "start_y_pages": placement.start_y_pages,
                "actual_content_height_pages": placement.actual_content_height_pages,
                "actual_bottom_y_pages": placement.actual_bottom_y_pages,
                "snapped_next_start_y_pages": placement.snapped_next_start_y_pages,
                "overflow_allowed": placement.overflow_allowed,
                "overflow_amount_pages": placement.overflow_amount_pages,
                "overflow_violation": placement.overflow_violation,
                "slot_span_count": placement.slot_span_count,
                "bbox": {
                    "left": entry.bounds.left,
                    "top": entry.bounds.top,
                    "width": entry.bounds.width,
                    "height": entry.bounds.height,
                },
                "risk_flags": list(entry.risk_flags),
                "record_mode": "mixed",
                "text_record_count": text_record_count,
                "image_record_count": image_record_count,
                "board_theme": _resolve_board_theme(board_theme),
                "placement_x_ratio": float(_clamp_placement_x_ratio(entry.placement_x_ratio) or 0.0),
                "placement_y_ratio": float(_clamp_placement_y_ratio(entry.placement_y_ratio) or 0.0),
                "placement_scale_ratio": float(scale_ratio),
                "blocks": block_summaries,
            }
        )

    return records, placement_summaries


def build_records(
    problem_entries: list[ProblemEntry],
    template: LayoutTemplate,
    *,
    record_mode: str,
    output_dir: Path,
    text_confidence_threshold: float,
    dark_board: bool = True,
    board_theme: str = DEFAULT_BOARD_THEME,
    crop_format: str = DEFAULT_CROP_FORMAT,
) -> tuple[list[bytes], list[dict[str, object]], int]:
    if record_mode == "image-only":
        records, placement_summaries = build_image_only_records(
            problem_entries,
            template,
            dark_board=dark_board,
            board_theme=board_theme,
            crop_format=crop_format,
        )
        return records, placement_summaries, header_flag_for_crop_format(crop_format, mode="image")

    records, placement_summaries = build_mixed_records(
        problem_entries,
        template,
        output_dir=output_dir,
        text_confidence_threshold=text_confidence_threshold,
        dark_board=dark_board,
        board_theme=board_theme,
    )
    has_images = any(item["image_record_count"] for item in placement_summaries)
    if has_images:
        header_flag = header_flag_for_crop_format(crop_format, mode="image")
    else:
        header_flag = 3
    return records, placement_summaries, header_flag


def write_ui_prototype_data(output_path: Path, placements: list[dict[str, object]]) -> None:
    payload = {
        "problems": [
            {
                "id": item["problem_id"],
                "title": item["title"],
                "subject": item["subject"],
                "imagePath": Path(item["crop_path"]).resolve().as_uri(),
                "boardRenderPath": Path(item["board_render_path"]).resolve().as_uri() if item.get("board_render_path") else None,
                "actualHeightPages": item["actual_content_height_pages"],
                "overflowAllowed": item["overflow_allowed"],
                "readingHeavy": item["overflow_allowed"],
                "placementXRatio": float(item.get("placement_x_ratio") or 0.0),
                "placementYRatio": float(item.get("placement_y_ratio") or 0.0),
                "placementScaleRatio": float(item.get("placement_scale_ratio") or 1.0),
            }
            for item in placements
        ]
    }
    output_path.write_text(
        "window.PROTOTYPE_DATA = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8",
    )


def build_placement_summary(placements: list[dict[str, object]]) -> dict[str, object]:
    if not placements:
        return {
            "problem_count": 0,
            "overflow_count": 0,
            "overflow_violation_count": 0,
            "max_bottom_y_pages": 0.0,
            "text_record_count": 0,
            "image_record_count": 0,
        }
    return {
        "problem_count": len(placements),
        "overflow_count": sum(1 for item in placements if float(item["overflow_amount_pages"]) > 0),
        "overflow_violation_count": sum(1 for item in placements if bool(item["overflow_violation"])),
        "max_bottom_y_pages": max(float(item["actual_bottom_y_pages"]) for item in placements),
        "text_record_count": sum(int(item.get("text_record_count", 0)) for item in placements),
        "image_record_count": sum(int(item.get("image_record_count", 0)) for item in placements),
    }


def run_problem_export(
    source: str | Path | Sequence[str | Path],
    *,
    output_dir: str | Path = "mvp_export_question",
    subject_name: str = "unknown",
    ocr: str = "auto",
    pdf_dpi: int = 200,
    detect_perspective: bool = False,
    skip_deskew: bool = False,
    skip_crop: bool = False,
    max_dimension: int | None = None,
    export_edb: bool = True,
    edb_name: str = "mvp_board.edb",
    record_mode: str = "mixed",
    text_confidence_threshold: float = 0.78,
    dark_board: bool = True,
    board_theme: str = DEFAULT_BOARD_THEME,
    sync_ui: bool = False,
    crop_format: str = DEFAULT_CROP_FORMAT,
    input_intent: str = "auto",
    input_notes: str | None = None,
    ai_fallback_enabled: bool = False,
    ai_fallback: str | None = None,
    ai_fallback_provider: str = "gemini",
    ai_fallback_model: str = "",
    ai_fallback_prompt: str = "",
    ai_fallback_max_tokens: int | None = None,
    ai_fallback_temperature: float | None = None,
    ai_fallback_threshold: float = 0.72,
    ai_fallback_max_regions: int = 48,
    ai_fallback_timeout_ms: int = 30000,
    ai_fallback_save_debug: bool = False,
    fail_on_ai_error: bool = False,
) -> dict[str, Any]:
    if isinstance(source, (str, Path)):
        source_paths = [Path(source).resolve()]
    else:
        source_paths = [Path(path).resolve() for path in source]
    if not source_paths:
        raise ValueError("At least one source path is required")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    subject = resolve_subject(subject_name)
    resolved_input_intent = _normalize_input_intent(input_intent)
    resolved_board_theme = _resolve_board_theme(board_theme)
    template = LayoutTemplate(
        name="academy-default",
        base_slot_height_pages=ONE_PROBLEM_SLOT_HEIGHT_PAGES,
        metadata={"placement_mode": "one-problem-per-page"},
    )
    ai_fallback_config = _build_ai_fallback_config(
        enabled=ai_fallback_enabled,
        mode=ai_fallback,
        provider=ai_fallback_provider,
        model=ai_fallback_model,
        prompt=ai_fallback_prompt,
        max_tokens=ai_fallback_max_tokens,
        temperature=ai_fallback_temperature,
        threshold=ai_fallback_threshold,
        max_regions=ai_fallback_max_regions,
        timeout_ms=ai_fallback_timeout_ms,
        save_debug=ai_fallback_save_debug,
        fail_on_error=fail_on_ai_error,
    )

    def _build_source_pages(source_path: Path) -> tuple[list[PreparedPage], list[PageModel]]:
        return build_pages(
            source_path,
            subject=subject,
            ocr_mode=ocr,
            ai_fallback_config=ai_fallback_config,
            pdf_dpi=pdf_dpi,
            detect_perspective=detect_perspective,
            deskew=not skip_deskew,
            crop_margins=not skip_crop,
            max_dimension=max_dimension,
        )

    source_worker_count = resolve_recognition_worker_count(
        len(source_paths),
        ocr_mode=ocr,
        ai_config=_to_page_ai_config(ai_fallback_config),
    )
    if source_worker_count <= 1:
        source_results = [_build_source_pages(source_path) for source_path in source_paths]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=source_worker_count) as executor:
            source_results = list(executor.map(_build_source_pages, source_paths))

    prepared_pages: list[PreparedPage] = []
    pages: list[PageModel] = []
    for prepared, page_models in source_results:
        prepared_pages.extend(prepared)
        pages.extend(page_models)

    if resolved_input_intent in {"single-problem", "page-as-is"}:
        pages = _force_single_problem_per_page(pages, input_intent=resolved_input_intent)

    save_pages_json(pages, out_dir / "pages.json")
    ai_summary = _summarize_ai_fallback_usage(pages, ai_fallback_config)
    ocr_summary = _summarize_ocr_usage(pages)
    if ocr_summary["no_ocr_fallback_active"]:
        print(
            "[run_problem_export] WARNING: OCR resolved to 'none' for every block - "
            "problem-number detection will be disabled and each detected band "
            "will become its own pseudo-problem. Set GEMINI_API_KEY (or pass "
            "ocr='gemini') to enable Gemini OCR.",
            flush=True,
        )
    problem_entries = build_problem_entries(
        prepared_pages,
        pages,
        out_dir,
        template,
        board_theme=resolved_board_theme,
    )
    save_pages_json(pages, out_dir / "pages.json")
    # Match ClassIn's observed publish behaviour: page_count_hint scales with the
    # number of problems on the board so the logical canvas always covers the
    # actual content height. Real published EDBs use ~2x the record count
    # (e.g. 44 problems -> pages_hint=88); keep 50 as the floor for short boards.
    template.board_page_count = max(50, len(problem_entries) * 2)
    resolved_crop_format = crop_format if crop_format in (CROP_FORMAT_V1, CROP_FORMAT_V2) else DEFAULT_CROP_FORMAT
    records, placements, header_flag = build_records(
        problem_entries,
        template,
        record_mode=record_mode,
        output_dir=out_dir,
        text_confidence_threshold=text_confidence_threshold,
        dark_board=dark_board,
        board_theme=resolved_board_theme,
        crop_format=resolved_crop_format,
    )

    summary = {
        "source_paths": [str(path) for path in source_paths],
        "output_dir": str(out_dir.resolve()),
        "pages_json_path": str((out_dir / "pages.json").resolve()),
        "problem_crop_dir": str((out_dir / "problem_crops").resolve()),
        "board_render_dir": str((out_dir / "board_renders").resolve()),
        "block_crop_dir": str((out_dir / "block_crops").resolve()),
        "record_count": len(records),
        "record_mode": record_mode,
        "dark_board": dark_board,
        "board_theme": resolved_board_theme,
        "crop_format": resolved_crop_format,
        "header_flag": header_flag,
        "text_confidence_threshold": text_confidence_threshold,
        "ai_fallback": ai_fallback_config,
        "ai_summary": ai_summary,
        "placement_summary": build_placement_summary(placements),
        "placements": placements,
        "ocr_backend_requested": ocr,
        "ocr_summary": ocr_summary,
        "input_intent": resolved_input_intent,
    }

    placements_path = out_dir / "placements.json"
    placements_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    edb_path: Path | None = None
    if export_edb:
        edb_path = out_dir / edb_name
        write_edb(
            edb_path,
            build_edb(
                records,
                header_flag=header_flag,
                version=version_string_for_crop_format(resolved_crop_format),
                page_count_hint=template.board_page_count,
            ),
        )
        summary["edb_path"] = str(edb_path.resolve())
        placements_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    ui_session = build_ui_session(
        prepared_pages,
        placements,
        out_dir,
        edb_path if export_edb else None,
        source_paths,
        record_mode=record_mode,
        ai_fallback_config=ai_fallback_config,
        ai_summary=ai_summary,
        pages=pages,
        template=template,
        input_intent=resolved_input_intent,
        input_notes=input_notes,
        board_theme=resolved_board_theme,
        crop_format=resolved_crop_format,
    )
    classin_handoff_path: Path | None = None
    classin_handoff_markdown_path: Path | None = None
    if edb_path is not None and edb_path.exists():
        classin_handoff_path, classin_handoff_markdown_path = write_classin_handoff_manifest(
            out_dir,
            source_paths=source_paths,
            edb_path=edb_path,
            ui_session=ui_session,
            summary=summary,
            template=template,
        )
        summary["classin_handoff_path"] = str(classin_handoff_path)
        summary["classin_handoff_markdown_path"] = str(classin_handoff_markdown_path)
        handoff_session_fields = _classin_handoff_session_fields(classin_handoff_path)
        summary.update(handoff_session_fields)
        placements_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        ui_session["classin_handoff_path"] = str(classin_handoff_path)
        ui_session["classinHandoffPath"] = str(classin_handoff_path)
        ui_session["classin_handoff_markdown_path"] = str(classin_handoff_markdown_path)
        ui_session["classinHandoffMarkdownPath"] = str(classin_handoff_markdown_path)
        ui_session.update(handoff_session_fields)
    ui_session_path, synced_ui_path = write_ui_session_bundle(out_dir, ui_session, sync_ui=sync_ui)

    return {
        "output_dir": out_dir.resolve(),
        "edb_path": edb_path.resolve() if edb_path and edb_path.exists() else None,
        "pages_json_path": (out_dir / "pages.json").resolve(),
        "placements_json_path": placements_path.resolve(),
        "classin_handoff_path": classin_handoff_path,
        "classin_handoff_markdown_path": classin_handoff_markdown_path,
        "ui_session": ui_session,
        "ui_session_path": ui_session_path.resolve(),
        "synced_ui_path": synced_ui_path.resolve() if synced_ui_path else None,
        "summary": summary,
        "ai_fallback": ai_fallback_config,
        "ai_summary": ai_summary,
        "input_intent": resolved_input_intent,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a problem-board EDB from a source image or PDF.")
    parser.add_argument("source", help="Path to a PDF or image source")
    parser.add_argument("--output-dir", default="mvp_export", help="Directory for pipeline artifacts and EDB output")
    parser.add_argument("--subject", default="unknown", help="Subject hint: math, science, korean, english, social, unknown")
    parser.add_argument("--ocr", default="noop", help="OCR backend: noop, auto, paddleocr, tesseract")
    parser.add_argument("--pdf-dpi", type=int, default=200, help="PDF render DPI")
    parser.add_argument("--detect-perspective", action="store_true", help="Try perspective correction for photographed sources")
    parser.add_argument("--skip-deskew", action="store_true", help="Disable deskew")
    parser.add_argument("--skip-crop", action="store_true", help="Disable margin crop")
    parser.add_argument("--max-dimension", type=int, default=None, help="Resize long edge to this many pixels")
    parser.add_argument("--template-name", default="academy-default", help="Layout template name")
    parser.add_argument("--board-pages", type=int, default=50, help="Board page count hint")
    parser.add_argument("--slot-height", type=float, default=ONE_PROBLEM_SLOT_HEIGHT_PAGES, help="Base slot height in board pages")
    parser.add_argument("--record-mode", choices=("mixed", "image-only"), default="mixed", help="Record generation strategy")
    parser.add_argument("--input-intent", choices=tuple(sorted(INPUT_INTENTS)), default="auto", help="How to treat uploaded source pages")
    parser.add_argument(
        "--crop-format",
        choices=(CROP_FORMAT_V1, CROP_FORMAT_V2),
        default=DEFAULT_CROP_FORMAT,
        help=(
            "EDB crop layout version. v2 (default) matches the current ClassIn "
            "crop sample (header_flag=0, ~301px image width, tight-cropped image_secondary, "
            "version 6.0.5.3913). v1 keeps the legacy wide layout (header_flag=4, "
            "wider images, downsampled preview, version 6.0.5.3911)."
        ),
    )
    parser.add_argument("--text-confidence-threshold", type=float, default=0.78, help="Minimum OCR confidence for text records in mixed mode")
    parser.add_argument(
        "--board-theme",
        choices=tuple(BOARD_THEME_PALETTES.keys()),
        default=DEFAULT_BOARD_THEME,
        help="Dark board palette used when converting light-background crops",
    )
    parser.add_argument("--light-board", action="store_true", help="Disable dark-board color conversion (keep original light background in image records)")
    parser.add_argument("--debug-segments", action="store_true", help="Save block overlay images to <output-dir>/debug_segments/ for segmentation inspection")
    parser.add_argument("--ai-fallback-enabled", action="store_true", help="Enable optional AI fallback settings")
    parser.add_argument("--ai-fallback", default=None, help="AI fallback mode override: off, auto, force")
    parser.add_argument("--ai-fallback-provider", default="gemini", help="AI fallback provider name")
    parser.add_argument("--ai-fallback-model", default="", help="AI fallback model name")
    parser.add_argument("--ai-fallback-prompt", default="", help="AI fallback prompt template")
    parser.add_argument("--ai-fallback-max-tokens", type=int, default=None, help="AI fallback max output tokens")
    parser.add_argument("--ai-fallback-temperature", type=float, default=None, help="AI fallback sampling temperature")
    parser.add_argument("--ai-fallback-threshold", type=float, default=0.72, help="Low-confidence trigger threshold for AI fallback")
    parser.add_argument("--ai-fallback-max-regions", type=int, default=48, help="Maximum number of regions sent to AI fallback")
    parser.add_argument("--ai-fallback-timeout-ms", type=int, default=30000, help="Timeout in milliseconds for AI fallback")
    parser.add_argument("--ai-fallback-save-debug", action="store_true", help="Write AI fallback debug artifacts")
    parser.add_argument("--fail-on-ai-error", action="store_true", help="Raise an error if AI fallback fails")
    parser.add_argument(
        "--prototype-data-out",
        default=str(Path("ui_prototype") / "prototype_data.js"),
        help="Path to write UI prototype data JS",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    subject = resolve_subject(args.subject)
    resolved_board_theme = _resolve_board_theme(args.board_theme)
    ai_fallback_config = _build_ai_fallback_config(
        enabled=args.ai_fallback_enabled,
        mode=args.ai_fallback,
        provider=args.ai_fallback_provider,
        model=args.ai_fallback_model,
        prompt=args.ai_fallback_prompt,
        max_tokens=args.ai_fallback_max_tokens,
        temperature=args.ai_fallback_temperature,
        threshold=args.ai_fallback_threshold,
        max_regions=args.ai_fallback_max_regions,
        timeout_ms=args.ai_fallback_timeout_ms,
        save_debug=args.ai_fallback_save_debug,
        fail_on_error=args.fail_on_ai_error,
    )
    debug_segments_dir = output_dir / "debug_segments" if args.debug_segments else None
    prepared_pages, pages = build_pages(
        args.source,
        subject=subject,
        ocr_mode=args.ocr,
        ai_fallback_config=ai_fallback_config,
        pdf_dpi=args.pdf_dpi,
        detect_perspective=args.detect_perspective,
        deskew=not args.skip_deskew,
        crop_margins=not args.skip_crop,
        max_dimension=args.max_dimension,
        debug_segments_dir=debug_segments_dir,
    )
    resolved_input_intent = _normalize_input_intent(args.input_intent)
    if resolved_input_intent in {"single-problem", "page-as-is"}:
        pages = _force_single_problem_per_page(pages, input_intent=resolved_input_intent)
    save_pages_json(pages, output_dir / "pages.json")

    template = LayoutTemplate(
        name=args.template_name,
        board_page_count=args.board_pages,
        base_slot_height_pages=args.slot_height,
    )
    problem_entries = build_problem_entries(
        prepared_pages,
        pages,
        output_dir,
        template,
        board_theme=resolved_board_theme,
    )
    save_pages_json(pages, output_dir / "pages.json")
    resolved_crop_format = args.crop_format if args.crop_format in (CROP_FORMAT_V1, CROP_FORMAT_V2) else DEFAULT_CROP_FORMAT
    records, placements, header_flag = build_records(
        problem_entries,
        template,
        record_mode=args.record_mode,
        output_dir=output_dir,
        text_confidence_threshold=args.text_confidence_threshold,
        dark_board=not args.light_board,
        board_theme=resolved_board_theme,
        crop_format=resolved_crop_format,
    )

    edb_path = output_dir / f"{Path(args.source).stem}.edb"
    write_edb(
        edb_path,
        build_edb(
            records,
            header_flag=header_flag,
            version=version_string_for_crop_format(resolved_crop_format),
            page_count_hint=template.board_page_count,
        ),
    )
    ai_summary = _summarize_ai_fallback_usage(pages, ai_fallback_config)
    ocr_summary = _summarize_ocr_usage(pages)

    summary = {
        "source": str(args.source),
        "output_dir": str(output_dir),
        "edb_path": str(edb_path),
        "pages_json_path": str(output_dir / "pages.json"),
        "problem_crop_dir": str(output_dir / "problem_crops"),
        "block_crop_dir": str(output_dir / "block_crops"),
        "record_count": len(records),
        "record_mode": args.record_mode,
        "dark_board": not args.light_board,
        "board_theme": resolved_board_theme,
        "crop_format": resolved_crop_format,
        "header_flag": header_flag,
        "text_confidence_threshold": args.text_confidence_threshold,
        "ai_fallback": ai_fallback_config,
        "ai_summary": ai_summary,
        "placement_summary": build_placement_summary(placements),
        "placements": placements,
        "ocr_backend_requested": args.ocr,
        "ocr_summary": ocr_summary,
        "input_intent": resolved_input_intent,
    }
    summary_path = output_dir / "board_run_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    prototype_path = Path(args.prototype_data_out)
    prototype_path.parent.mkdir(parents=True, exist_ok=True)
    write_ui_prototype_data(prototype_path, placements)

    print(
        json.dumps(
            {
                "edb_path": str(edb_path),
                "pages_json_path": str(output_dir / "pages.json"),
                "board_run_summary_path": str(summary_path),
                "ui_prototype_data_path": str(prototype_path),
                "problem_count": len(placements),
                "record_mode": args.record_mode,
                "text_record_count": summary["placement_summary"]["text_record_count"],
                "image_record_count": summary["placement_summary"]["image_record_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
