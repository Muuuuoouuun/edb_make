#!/usr/bin/env python3
"""Conservative, OCR-free tiling for two-column document pages.

The public :func:`tile_page_columns` API is deliberately fail-open: a page is
only split when a broad, nearly ink-free central gutter and substantive content
on both sides can be demonstrated.  Otherwise the original ``Image`` object is
returned as a single tile.

No resampling or colour conversion is applied to output tiles.  Successful
tiles are direct Pillow crops whose source boxes form an exact, non-overlapping
partition of the input pixels.
"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from PIL import Image


Box = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class PageTilingOptions:
    """Thresholds for conservative two-column gutter detection."""

    min_page_width_px: int = 320
    min_page_height_px: int = 240
    body_margin_ratio: float = 0.025
    center_search_min_ratio: float = 0.30
    center_search_max_ratio: float = 0.70
    max_gutter_center_offset_ratio: float = 0.10
    min_gutter_width_px: int = 18
    min_gutter_width_ratio: float = 0.035
    max_sparse_column_ink_ratio: float = 0.006
    max_gutter_ink_ratio: float = 0.0025
    min_side_width_ratio: float = 0.30
    min_side_ink_ratio: float = 0.0015
    min_side_active_row_ratio: float = 0.06
    min_side_ink_width_ratio: float = 0.24
    min_side_ink_height_ratio: float = 0.18
    background_ink_delta: int = 24
    min_supported_background: int = 160
    vertical_rule_min_ink_ratio: float = 0.62
    vertical_rule_max_width_px: int = 8
    vertical_rule_max_width_ratio: float = 0.012
    allow_center_vertical_rule: bool = False
    center_rule_search_min_ratio: float = 0.45
    center_rule_search_max_ratio: float = 0.55


@dataclass(frozen=True, slots=True)
class PageTile:
    """One tile and its exact source-pixel box."""

    image: Image.Image
    source_box: Box
    column_index: int


@dataclass(frozen=True, slots=True)
class PageTilingResult:
    """Result of an OCR-free page tiling decision."""

    tiles: tuple[PageTile, ...]
    was_split: bool
    reason: str
    gutter_bounds: tuple[int, int] | None = None
    split_x: int | None = None
    confidence: float = 0.0

    @property
    def images(self) -> tuple[Image.Image, ...]:
        """Return output images in reading order (left, then right)."""

        return tuple(tile.image for tile in self.tiles)


@dataclass(frozen=True, slots=True)
class _SideEvidence:
    ink_ratio: float
    active_row_ratio: float
    ink_width_ratio: float
    ink_height_ratio: float


def tile_page_columns(
    image: Image.Image,
    options: PageTilingOptions | None = None,
) -> PageTilingResult:
    """Split a confidently detected two-column page into lossless crops.

    The returned crops are ordered left-to-right.  If the evidence is
    insufficient, ``result.tiles`` contains the original image object and
    ``result.was_split`` is false.
    """

    options = options or PageTilingOptions()
    width, height = image.size
    if width < options.min_page_width_px or height < options.min_page_height_px:
        return _unsplit(image, "page_too_small")

    grayscale = _grayscale_array(image)
    background = float(np.percentile(grayscale, 90))
    if background < options.min_supported_background:
        return _unsplit(image, "unsupported_dark_background")

    threshold = max(40, min(235, int(round(background)) - options.background_ink_delta))
    ink = grayscale < threshold

    body_margin = max(1, int(round(height * options.body_margin_ratio)))
    body = ink[body_margin : height - body_margin, :]
    if body.size == 0:
        return _unsplit(image, "empty_body")

    column_density = body.mean(axis=0)
    search_left = max(0, int(round(width * options.center_search_min_ratio)))
    search_right = min(width, int(round(width * options.center_search_max_ratio)))
    min_gutter_width = max(
        options.min_gutter_width_px,
        int(round(width * options.min_gutter_width_ratio)),
    )

    candidates = _sparse_runs(
        column_density,
        search_left,
        search_right,
        options.max_sparse_column_ink_ratio,
        min_gutter_width,
    )
    if not candidates:
        if options.allow_center_vertical_rule:
            rule_result = _tile_at_center_vertical_rule(image, body, column_density, options)
            if rule_result is not None:
                return rule_result
        return _unsplit(image, "no_wide_central_gutter")

    page_center = width / 2.0
    candidates.sort(
        key=lambda run: (
            -(run[1] - run[0]),
            abs(((run[0] + run[1]) / 2.0) - page_center),
        )
    )

    failure_reason = "ambiguous_gutter"
    for gutter_left, gutter_right in candidates:
        gutter_center = (gutter_left + gutter_right) / 2.0
        if abs(gutter_center - page_center) > width * options.max_gutter_center_offset_ratio:
            failure_reason = "gutter_too_far_from_center"
            continue

        split_x = int(round(gutter_center))
        if (
            split_x < width * options.min_side_width_ratio
            or width - split_x < width * options.min_side_width_ratio
        ):
            failure_reason = "column_too_narrow"
            continue

        gutter_density = float(body[:, gutter_left:gutter_right].mean())
        if gutter_density > options.max_gutter_ink_ratio:
            failure_reason = "gutter_contains_ink"
            continue

        if _has_nearby_vertical_rule(
            column_density,
            gutter_left,
            gutter_right,
            min_gutter_width,
            width,
            options,
        ):
            failure_reason = "central_vertical_rule"
            continue

        left_evidence = _side_evidence(body[:, :gutter_left])
        right_evidence = _side_evidence(body[:, gutter_right:])
        if not _side_is_substantive(left_evidence, options):
            failure_reason = "insufficient_left_column_ink"
            continue
        if not _side_is_substantive(right_evidence, options):
            failure_reason = "insufficient_right_column_ink"
            continue

        # The two crop boxes cover every source pixel exactly once.  The blank
        # gutter is retained, divided at its midpoint, so even decorative or
        # background pixels are never discarded.
        left_box: Box = (0, 0, split_x, height)
        right_box: Box = (split_x, 0, width, height)
        gutter_strength = min(1.0, (gutter_right - gutter_left) / max(1, min_gutter_width * 2))
        whitespace_strength = max(
            0.0,
            1.0 - gutter_density / max(options.max_gutter_ink_ratio, 1e-9),
        )
        side_strength = min(
            1.0,
            left_evidence.active_row_ratio / max(options.min_side_active_row_ratio * 2, 1e-9),
            right_evidence.active_row_ratio / max(options.min_side_active_row_ratio * 2, 1e-9),
        )
        confidence = round(
            0.45 * whitespace_strength + 0.30 * gutter_strength + 0.25 * side_strength,
            4,
        )
        return PageTilingResult(
            tiles=(
                PageTile(image=image.crop(left_box), source_box=left_box, column_index=0),
                PageTile(image=image.crop(right_box), source_box=right_box, column_index=1),
            ),
            was_split=True,
            reason="two_column_gutter",
            gutter_bounds=(gutter_left, gutter_right),
            split_x=split_x,
            confidence=confidence,
        )

    if options.allow_center_vertical_rule:
        rule_result = _tile_at_center_vertical_rule(image, body, column_density, options)
        if rule_result is not None:
            return rule_result
    return _unsplit(image, failure_reason)


def _tile_at_center_vertical_rule(
    image: Image.Image,
    body: np.ndarray,
    column_density: np.ndarray,
    options: PageTilingOptions,
) -> PageTilingResult | None:
    """Use a narrow, near-full-height center rule as explicit opt-in evidence."""

    width, height = image.size
    rule = _detect_center_vertical_rule(column_density, width, options)
    if rule is None:
        return None
    rule_left, rule_right = rule
    split_x = int(round((rule_left + rule_right) / 2.0))
    if (
        split_x < width * options.min_side_width_ratio
        or width - split_x < width * options.min_side_width_ratio
    ):
        return None

    # Exclude the rule itself from both evidence regions.  This prevents a
    # lone decorative line on an otherwise blank page from supplying the ink
    # needed to pass the bilateral-content check.
    left_evidence = _side_evidence(body[:, :rule_left])
    right_evidence = _side_evidence(body[:, rule_right:])
    if not _side_is_substantive(left_evidence, options):
        return None
    if not _side_is_substantive(right_evidence, options):
        return None

    left_box: Box = (0, 0, split_x, height)
    right_box: Box = (split_x, 0, width, height)
    rule_density = float(column_density[rule_left:rule_right].mean())
    center_offset = abs(split_x - width / 2.0) / max(1.0, width / 2.0)
    center_strength = max(
        0.0,
        1.0 - center_offset / max(options.center_rule_search_max_ratio - 0.5, 1e-9),
    )
    side_strength = min(
        1.0,
        left_evidence.active_row_ratio / max(options.min_side_active_row_ratio * 2, 1e-9),
        right_evidence.active_row_ratio / max(options.min_side_active_row_ratio * 2, 1e-9),
    )
    confidence = round(
        0.50 * min(1.0, rule_density)
        + 0.25 * center_strength
        + 0.25 * side_strength,
        4,
    )
    return PageTilingResult(
        tiles=(
            PageTile(image=image.crop(left_box), source_box=left_box, column_index=0),
            PageTile(image=image.crop(right_box), source_box=right_box, column_index=1),
        ),
        was_split=True,
        reason="two_column_center_rule",
        split_x=split_x,
        confidence=confidence,
    )


def _detect_center_vertical_rule(
    column_density: np.ndarray,
    page_width: int,
    options: PageTilingOptions,
) -> tuple[int, int] | None:
    search_left = max(0, int(round(page_width * options.center_rule_search_min_ratio)))
    search_right = min(page_width, int(round(page_width * options.center_rule_search_max_ratio)))
    max_rule_width = max(
        1,
        min(
            options.vertical_rule_max_width_px,
            int(round(page_width * options.vertical_rule_max_width_ratio)),
        ),
    )
    dense = column_density[search_left:search_right] >= options.vertical_rule_min_ink_ratio
    runs: list[tuple[int, int]] = []
    run_start: int | None = None
    for offset, is_dense in enumerate(dense):
        if is_dense:
            if run_start is None:
                run_start = offset
            continue
        if run_start is not None:
            run_width = offset - run_start
            if 1 <= run_width <= max_rule_width:
                runs.append((search_left + run_start, search_left + offset))
        run_start = None
    if run_start is not None:
        run_width = len(dense) - run_start
        if 1 <= run_width <= max_rule_width:
            runs.append((search_left + run_start, search_right))
    if not runs:
        return None

    page_center = page_width / 2.0
    return min(
        runs,
        key=lambda run: (
            abs(((run[0] + run[1]) / 2.0) - page_center),
            -float(column_density[run[0] : run[1]].mean()),
        ),
    )


def _unsplit(image: Image.Image, reason: str) -> PageTilingResult:
    width, height = image.size
    return PageTilingResult(
        tiles=(PageTile(image=image, source_box=(0, 0, width, height), column_index=0),),
        was_split=False,
        reason=reason,
    )


def _grayscale_array(image: Image.Image) -> np.ndarray:
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        background.alpha_composite(rgba)
        grayscale_image = background.convert("L")
    else:
        grayscale_image = image.convert("L")
    return np.asarray(grayscale_image, dtype=np.uint8)


def _sparse_runs(
    column_density: np.ndarray,
    search_left: int,
    search_right: int,
    max_density: float,
    min_width: int,
) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    run_start: int | None = None
    for x in range(search_left, search_right):
        if column_density[x] <= max_density:
            if run_start is None:
                run_start = x
            continue
        if run_start is not None and x - run_start >= min_width:
            runs.append((run_start, x))
        run_start = None
    if run_start is not None and search_right - run_start >= min_width:
        runs.append((run_start, search_right))
    return runs


def _has_nearby_vertical_rule(
    column_density: np.ndarray,
    gutter_left: int,
    gutter_right: int,
    min_gutter_width: int,
    page_width: int,
    options: PageTilingOptions,
) -> bool:
    """Reject a narrow, near-full-height rule beside an apparent gutter."""

    radius = min_gutter_width
    start = max(0, gutter_left - radius)
    end = min(page_width, gutter_right + radius)
    dense = column_density[start:end] >= options.vertical_rule_min_ink_ratio
    max_rule_width = max(
        1,
        min(
            options.vertical_rule_max_width_px,
            int(round(page_width * options.vertical_rule_max_width_ratio)),
        ),
    )
    run_start: int | None = None
    for offset, is_dense in enumerate(dense):
        if is_dense:
            if run_start is None:
                run_start = offset
            continue
        if run_start is not None and 1 <= offset - run_start <= max_rule_width:
            return True
        run_start = None
    if run_start is not None and 1 <= len(dense) - run_start <= max_rule_width:
        return True
    return False


def _side_evidence(side: np.ndarray) -> _SideEvidence:
    if side.size == 0:
        return _SideEvidence(0.0, 0.0, 0.0, 0.0)
    ink_ratio = float(side.mean())
    active_rows = np.any(side, axis=1)
    active_row_ratio = float(active_rows.mean())
    ys, xs = np.nonzero(side)
    if len(xs) == 0:
        return _SideEvidence(ink_ratio, active_row_ratio, 0.0, 0.0)
    ink_width_ratio = float(xs.max() - xs.min() + 1) / max(1, side.shape[1])
    ink_height_ratio = float(ys.max() - ys.min() + 1) / max(1, side.shape[0])
    return _SideEvidence(
        ink_ratio=ink_ratio,
        active_row_ratio=active_row_ratio,
        ink_width_ratio=ink_width_ratio,
        ink_height_ratio=ink_height_ratio,
    )


def _side_is_substantive(evidence: _SideEvidence, options: PageTilingOptions) -> bool:
    return (
        evidence.ink_ratio >= options.min_side_ink_ratio
        and evidence.active_row_ratio >= options.min_side_active_row_ratio
        and evidence.ink_width_ratio >= options.min_side_ink_width_ratio
        and evidence.ink_height_ratio >= options.min_side_ink_height_ratio
    )


__all__ = [
    "PageTile",
    "PageTilingOptions",
    "PageTilingResult",
    "tile_page_columns",
]
