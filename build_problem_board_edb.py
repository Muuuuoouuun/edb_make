#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageFilter, ImageOps, ImageStat

try:
    import numpy as np  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    np = None

from build_structured_page_json import build_page_model
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
from page_repair import AIFallbackConfig, build_ai_fallback_config as build_page_ai_fallback_config
from placement_engine import place_problems
from preprocess import PreparedPage, prepare_source_pages
from structured_schema import BlockType, Box, ContentBlock, PageModel, ProblemUnit, Subject, save_pages_json


LEFT_MARGIN_PX = 84.0
TOP_PADDING_PX = 20.0
RIGHT_PADDING_PX = 54.0
PROBLEM_PADDING_PX = 18.0
MIN_HEIGHT_PAGES = 0.72
MAX_HEIGHT_PAGES = 4.8
MIN_PROBLEM_AREA_RATIO = 0.12
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


def _problem_origin_x_px(entry: "ProblemEntry", rendered_width_px: float) -> float:
    ratio = _clamp_placement_x_ratio(entry.placement_x_ratio)
    if ratio is None:
        return LEFT_MARGIN_PX
    max_x_px = max(LEFT_MARGIN_PX, CANVAS_HEIGHT - RIGHT_PADDING_PX - rendered_width_px)
    return LEFT_MARGIN_PX + ratio * (max_x_px - LEFT_MARGIN_PX)


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


# Minimum width (px) for a problem crop before chalk rendering. Smaller crops
# get upscaled with LANCZOS so OCR and the dark-board composite have enough
# pixel-detail to render legibly. Chosen empirically: 1024 px wide is roughly
# the width of a printed Korean exam problem at 200 DPI.
PROBLEM_CROP_TARGET_MIN_WIDTH_PX = 1024
PROBLEM_CROP_MAX_UPSCALE = 2.6


def _enhance_problem_crop(image: Image.Image) -> Image.Image:
    """Upscale small crops and sharpen ink so the chalk render reads cleanly.

    Run BEFORE alpha-extraction so the upscale uses the original ink edges
    rather than a binary cutout. Returns a new image; the input is not
    mutated.
    """
    if image.width <= 0 or image.height <= 0:
        return image

    rgb = image.convert("RGB")
    if rgb.width < PROBLEM_CROP_TARGET_MIN_WIDTH_PX:
        scale = min(
            PROBLEM_CROP_MAX_UPSCALE,
            PROBLEM_CROP_TARGET_MIN_WIDTH_PX / max(rgb.width, 1),
        )
        if scale > 1.05:
            new_size = (int(round(rgb.width * scale)), int(round(rgb.height * scale)))
            rgb = rgb.resize(new_size, Image.Resampling.LANCZOS)

    # Unsharp mask brings ink-on-paper transitions back after upscale and also
    # helps thin-stroke text survive the alpha-mask threshold inside
    # _extract_problem_cutout.
    return rgb.filter(ImageFilter.UnsharpMask(radius=1.4, percent=140, threshold=2))


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


def merge_boxes(boxes: list[Box], *, page_width: int, page_height: int, padding_px: int = PROBLEM_PADDING_PX) -> Box:
    left = min(box.left for box in boxes)
    top = min(box.top for box in boxes)
    right = max(box.right for box in boxes)
    bottom = max(box.bottom for box in boxes)
    return Box.from_points(left, top, right, bottom).expanded(
        float(padding_px),
        max_width=float(page_width),
        max_height=float(page_height),
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
    page_models = [
        build_page_model(prepared_page, subject=subject, ocr_mode=ocr_mode, ai_config=page_ai_config)
        for prepared_page in prepared_pages
    ]
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

    included: list[ContentBlock] = []
    for block in page.blocks:
        if block.block_id in own_ids:
            included.append(block)
            continue
        if block.block_id in other_problem_block_ids:
            continue
        centre = (block.bbox.top + block.bbox.bottom) / 2.0
        if start_y <= centre < end_y:
            included.append(block)
    return included


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
        raw = problem.metadata.get("problem_number")
        if isinstance(raw, int) and raw >= 1:
            first_numbered_index = index
            break
        if isinstance(raw, str) and raw.isdigit():
            first_numbered_index = index
            break

    if first_numbered_index is None or first_numbered_index == 0:
        return problems

    return problems[first_numbered_index:]


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


def _collect_problem_risk_flags(problem: ProblemUnit) -> list[str]:
    flags: list[str] = []
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
    entries: list[ProblemEntry] = []

    for page in pages:
        prepared_page = prepared_by_page_id.get(page.page_id)
        if prepared_page is None:
            continue
        block_by_id = {block.block_id: block for block in page.blocks}

        # Reorder problems by their first block's top y so the "next problem"
        # boundary used for gap-filling matches reading order even when the
        # grouping pass produced them out of order.
        ordered_problems = sorted(
            page.problems,
            key=lambda p: (_problem_top_y(p, block_by_id), p.unit_id),
        )

        # Drop pre-first-problem header bands (e.g. cover-page title, 성명 /
        # 수험번호 form, "물리학I" / "과학탐구" subject header). When the page
        # contains a numbered problem, anything that lands above the first
        # numbered problem with no number of its own and no choice marker is
        # treated as page chrome — it would otherwise get bundled into the
        # first problem's crop (or worse, surface as its own pseudo-problem).
        ordered_problems = _drop_pre_first_problem_headers(ordered_problems, block_by_id)

        _fill_missing_problem_numbers(ordered_problems)

        all_assigned_ids: set[str] = set()
        for prob in ordered_problems:
            all_assigned_ids.update(_iter_problem_block_ids_raw(prob))

        for index, problem in enumerate(ordered_problems):
            next_problem = ordered_problems[index + 1] if index + 1 < len(ordered_problems) else None
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
                if not boxes:
                    boxes = [Box(left=0.0, top=0.0, width=float(page.width_px), height=float(page.height_px))]
                merged_box = merge_boxes(boxes, page_width=page.width_px, page_height=page.height_px)
                has_document_band_metadata = any("question_band_index" in block.metadata for block in blocks)
                if not has_document_band_metadata and merged_box.area < float(page.width_px * page.height_px) * MIN_PROBLEM_AREA_RATIO:
                    merged_box = Box(left=0.0, top=0.0, width=float(page.width_px), height=float(page.height_px))
                    blocks = list(page.sorted_blocks())

            crop = prepared_page.image.crop(
                (
                    int(merged_box.left),
                    int(merged_box.top),
                    int(merged_box.right),
                    int(merged_box.bottom),
                )
            )
            crop_name = f"problem_{len(entries) + 1:03d}_{hashlib.sha1(problem.unit_id.encode('utf-8', errors='ignore')).hexdigest()[:8]}.png"
            crop_path = crop_dir / crop_name
            crop.save(crop_path)
            # The cutout becomes the chalk render — upscale + sharpen first so
            # small or low-DPI crops produce a legible alpha mask on the dark
            # board.
            enhanced_crop = _enhance_problem_crop(crop)
            cutout_image = _extract_problem_cutout(enhanced_crop, chalk_color=chalk_color)
            board_render_path = cutout_dir / crop_name
            _write_render_image(cutout_image, board_render_path)
            reading_heavy = problem.subject in {Subject.KOREAN, Subject.ENGLISH}
            problem_title = problem.title or (f"\ubb38\ud56d {problem_number}" if problem_number is not None else f"\ubb38\ud56d {len(entries) + 1}")
            entries.append(
                ProblemEntry(
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
                    actual_height_pages=estimate_height_pages(crop.size, template),
                    overflow_allowed=reading_heavy,
                    reading_heavy=reading_heavy,
                    risk_flags=_collect_problem_risk_flags(problem),
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
    timeout_ms = 12000 if timeout_ms is None else int(timeout_ms)
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
        and timeout_ms == 12000
        and not save_debug
        and not fail_on_error
    ):
        return None
    return {
        "enabled": effective_enabled,
        "mode": resolved_mode,
        "provider": provider or "gemini",
        "model": model or "gemini-2.5-pro",
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
        timeout_ms=int(ai_fallback_config.get("timeout_ms") or 12000),
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
    crop_path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(crop_path)
    return crop.size


_GLOBAL_RISK_REASONS = {"merged_problem_block", "fallback_grouping", "marker_conflicts"}


def _collect_page_risk_flags(page_metadata: dict[str, Any]) -> list[str]:
    route_decision = page_metadata.get("route_decision")
    if not isinstance(route_decision, dict):
        return []
    profile = route_decision.get("profile") or {}
    reasons = profile.get("reasons") if isinstance(profile, dict) else None
    if not isinstance(reasons, list):
        return []
    return [str(reason) for reason in reasons if isinstance(reason, str)]


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
) -> dict[str, Any]:
    rendered_page_paths = [Path(page.source_path).resolve() for page in prepared_pages]
    resolved_input_intent = _normalize_input_intent(input_intent)
    warning_messages: list[str] = []
    if placements and len(placements) <= len(prepared_pages) and resolved_input_intent not in {"single-problem", "page-as-is"}:
        warning_messages.append(
            "감지된 문항 수가 원본 페이지 수와 비슷합니다. 여러 문제가 있는 페이지라면 검수 화면에서 분리 상태를 확인해 주세요."
        )
    resolved_template = template or LayoutTemplate(
        name="academy-default",
        board_page_count=max(50, len(placements) * 2 or 50),
        base_slot_height_pages=1.2,
    )

    # Map page_id → PageModel for risk-flag lookup, and page_id → list[problem_id]
    # so the UI can group detected problems by their source page in the review view.
    pages_by_id: dict[str, PageModel] = {}
    if pages:
        for page in pages:
            pages_by_id[page.page_id] = page
    page_risk_flags: dict[str, list[str]] = {
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
        page_flags = page_risk_flags.get(source_page_id, [])
        # Only propagate "this specific problem may be merged / auto-grouped"
        # reasons to per-problem flags; page-wide signals stay on the page.
        problem_flags = list(placement.get("risk_flags") or [])
        problem_flags.extend(reason for reason in page_flags if reason in _GLOBAL_RISK_REASONS)
        problem_flags = list(dict.fromkeys(str(reason) for reason in problem_flags if reason))
        problem_id = str(placement["problem_id"])
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
            }
        )
        problems_by_page.setdefault(source_page_id, []).append(problem_id)

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
        "detected_problem_count": len(placements),
        "export_mode": "question",
        "record_mode": record_mode,
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


def normalize_text_payload(text: str | None) -> str:
    if not text:
        return ""
    lines = [line.strip() for line in text.replace("\r", "\n").split("\n")]
    cleaned = [line for line in lines if line]
    return "\n".join(cleaned)


def choose_block_record_mode(block: ContentBlock, *, text_confidence_threshold: float) -> str:
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
        available_width_px = CANVAS_HEIGHT * template.fixed_left_zone_ratio - LEFT_MARGIN_PX - RIGHT_PADDING_PX

    records: list[bytes] = []
    placement_summaries: list[dict[str, object]] = []
    next_record_id = 0

    for placement in placements:
        entry = entries_by_problem_id[placement.problem_id]
        crop_path = Path(str(placement.metadata["crop_path"]))
        board_render_path = Path(str(placement.metadata["board_render_path"]))
        crop_image = Image.open(crop_path).convert("RGB")
        board_image = Image.open(board_render_path) if dark_board else crop_image
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
        if crop_format == CROP_FORMAT_V2:
            width_hint = normalize_width_px(float(board_image.width))
            height_hint = normalize_height_px(
                float(board_image.height), page_count_hint=template.board_page_count
            )
        else:
            height_px = placement.actual_content_height_pages * CANVAS_WIDTH
            width_hint = normalize_width_px(available_width_px)
            height_hint = normalize_height_px(height_px, page_count_hint=template.board_page_count)
        rendered_width_px = float(board_image.width) if crop_format == CROP_FORMAT_V2 else available_width_px
        x_px = _problem_origin_x_px(entry, rendered_width_px)
        y_px = placement.start_y_pages * CANVAS_WIDTH + TOP_PADDING_PX
        
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
                "text_record_count": 0,
                "image_record_count": image_record_count,
                "board_theme": _resolve_board_theme(board_theme),
                "crop_format": crop_format,
                "image_pixel_width": int(board_image.width),
                "image_pixel_height": int(board_image.height),
                "placement_x_ratio": float(_clamp_placement_x_ratio(entry.placement_x_ratio) or 0.0),
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
        scale = available_width_px / max(entry.bounds.width, 1.0)
        problem_origin_x_px = _problem_origin_x_px(entry, available_width_px)
        problem_origin_y_px = placement.start_y_pages * CANVAS_WIDTH + TOP_PADDING_PX
        block_summaries: list[dict[str, object]] = []
        text_record_count = 0
        image_record_count = 0

        for block in entry.blocks:
            x_px = problem_origin_x_px + max(0.0, block.bbox.left - entry.bounds.left) * scale
            y_px = problem_origin_y_px + max(0.0, block.bbox.top - entry.bounds.top) * scale
            width_px = max(40.0, min(available_width_px, block.bbox.width * scale))
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
                        width_hint=normalize_width_px(available_width_px),
                        height_hint=normalize_height_px(
                            placement.actual_content_height_pages * CANVAS_WIDTH,
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
    ai_fallback_timeout_ms: int = 12000,
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
    template = LayoutTemplate(name="academy-default")
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

    prepared_pages: list[PreparedPage] = []
    pages: list[PageModel] = []
    for source_path in source_paths:
        prepared, page_models = build_pages(
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
        prepared_pages.extend(prepared)
        pages.extend(page_models)

    if resolved_input_intent in {"single-problem", "page-as-is"}:
        pages = _force_single_problem_per_page(pages, input_intent=resolved_input_intent)

    save_pages_json(pages, out_dir / "pages.json")
    ai_summary = _summarize_ai_fallback_usage(pages, ai_fallback_config)
    ocr_summary = _summarize_ocr_usage(pages)
    if ocr_summary["no_ocr_fallback_active"]:
        print(
            "[run_problem_export] WARNING: OCR resolved to 'none' for every block — "
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
    )
    ui_session_path, synced_ui_path = write_ui_session_bundle(out_dir, ui_session, sync_ui=sync_ui)

    return {
        "output_dir": out_dir.resolve(),
        "edb_path": edb_path.resolve() if edb_path and edb_path.exists() else None,
        "pages_json_path": (out_dir / "pages.json").resolve(),
        "placements_json_path": placements_path.resolve(),
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
    parser.add_argument("--slot-height", type=float, default=1.2, help="Base slot height in board pages")
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
    parser.add_argument("--ai-fallback-timeout-ms", type=int, default=12000, help="Timeout in milliseconds for AI fallback")
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
