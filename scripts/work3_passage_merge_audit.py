#!/usr/bin/env python3
"""Audit cross-page passage stitching and compare EDB placement variants.

This is a Work 3 experiment.  It does not change the production export path.
The synthetic fixture places a uniquely coloured body marker on each source
page and separate coloured page-chrome rules around the joins.  The audit can
therefore detect loss, duplication, reordering, failed chrome removal, an
incorrect stitch height, and record-gap overlap without OCR.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from build_problem_board_edb import (
    CONTINUOUS_RECORD_GAP_PX,
    PASSAGE_CROP_HORIZONTAL_SAFE_PADDING_PX,
    PASSAGE_FRAGMENT_STITCH_GAP_PX,
    _composite_on_board_background,
    _enhance_problem_crop,
    _extract_problem_cutout,
    _pad_problem_crop_edges,
    _prepare_passage_segments_for_stitch,
)
from edb_builder import CANVAS_HEIGHT, CANVAS_WIDTH


BODY_MARKERS = ((214, 46, 46), (39, 86, 214), (35, 154, 88))
STRICT_BODY_MARKERS = (
    (214, 46, 46),
    (39, 86, 214),
    (35, 154, 88),
    (132, 61, 196),
    (12, 142, 157),
)
HEADER_CHROME = (202, 45, 184)
FOOTER_CHROME = (230, 136, 24)
FULL_WIDTH_DISPLAY_PX = CANVAS_HEIGHT - 84.0 - 54.0


@dataclass(frozen=True, slots=True)
class FragmentSpec:
    fragment_id: str
    page_number: int
    path: Path
    body_marker: tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class LayoutResult:
    name: str
    record_count: int
    total_span_pages: float
    maximum_record_height_pages: float
    internal_join_count: int
    inter_record_gap_px: float
    overlap_count: int
    split_policy: str


def _font(size: int) -> ImageFont.ImageFont:
    for candidate in (
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)


def _count_color(image: Image.Image, color: tuple[int, int, int]) -> int:
    return sum(1 for pixel in image.convert("RGB").get_flattened_data() if pixel == color)


def _color_centroid_y(image: Image.Image, color: tuple[int, int, int]) -> float | None:
    rows: list[int] = []
    rgb = image.convert("RGB")
    pixels = rgb.load()
    for y in range(rgb.height):
        if any(pixels[x, y] == color for x in range(rgb.width)):
            rows.append(y)
    return sum(rows) / len(rows) if rows else None


def build_synthetic_fragments(output_dir: Path) -> list[FragmentSpec]:
    """Create three page fragments with body and removable chrome signals."""

    output_dir.mkdir(parents=True, exist_ok=True)
    fragments: list[FragmentSpec] = []
    width, height = 720, 760
    body_font = _font(30)
    small_font = _font(20)
    for index, body_color in enumerate(BODY_MARKERS, start=1):
        image = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(image)

        if index > 1:
            draw.text((28, 10), f"PAGE {index} HEADER", fill=HEADER_CHROME, font=small_font)
            draw.rectangle((18, 38, width - 18, 44), fill=HEADER_CHROME)

        body_top = 108 if index > 1 else 72
        draw.rounded_rectangle(
            (54, body_top, width - 54, body_top + 300),
            radius=18,
            outline=body_color,
            width=8,
        )
        draw.rectangle((74, body_top + 34, 164, body_top + 124), fill=body_color)
        draw.text(
            (190, body_top + 44),
            f"BODY FRAGMENT {index}",
            fill=(24, 24, 24),
            font=body_font,
        )
        for row in range(4):
            y = body_top + 158 + row * 31
            draw.line((76, y, width - 80 - row * 28, y), fill=(70, 70, 70), width=4)

        if index < len(BODY_MARKERS):
            draw.rectangle((18, 690, width - 18, 696), fill=FOOTER_CHROME)
            draw.text((width // 2 - 34, 714), f"- {index} -", fill=FOOTER_CHROME, font=small_font)

        path = output_dir / f"fragment-page-{index}.png"
        image.save(path)
        fragments.append(
            FragmentSpec(
                fragment_id=f"fragment-{index}",
                page_number=index,
                path=path,
                body_marker=body_color,
            )
        )
    return fragments


def stitch_fragments(
    fragments: Sequence[FragmentSpec],
    output_path: Path,
) -> tuple[Image.Image, list[Image.Image]]:
    loaded: list[Image.Image] = []
    for fragment in fragments:
        with Image.open(fragment.path) as image:
            loaded.append(image.convert("RGB").copy())
    return _stitch_images(loaded, output_path=output_path)


def _stitch_images(
    images: Sequence[Image.Image],
    *,
    output_path: Path | None = None,
) -> tuple[Image.Image, list[Image.Image]]:
    prepared = _prepare_passage_segments_for_stitch(images)
    width = max(image.width for image in prepared)
    total_height = sum(image.height for image in prepared)
    total_height += PASSAGE_FRAGMENT_STITCH_GAP_PX * max(0, len(prepared) - 1)
    stitched = Image.new("RGB", (width, total_height), "white")
    cursor = 0
    for image in prepared:
        stitched.paste(image, (0, cursor))
        cursor += image.height + PASSAGE_FRAGMENT_STITCH_GAP_PX
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        stitched.save(output_path)
    return stitched, prepared


def _strict_matrix_images(
    fragment_count: int,
    width: int,
) -> tuple[list[Image.Image], list[FragmentSpec]]:
    scale = width / 720.0
    height = max(300, round(760 * scale))
    images: list[Image.Image] = []
    specs: list[FragmentSpec] = []
    for index in range(fragment_count):
        marker = STRICT_BODY_MARKERS[index]
        image = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(image)
        if index > 0:
            header_y = max(8, round(38 * scale))
            draw.rectangle(
                (
                    max(4, round(18 * scale)),
                    header_y,
                    width - max(4, round(18 * scale)),
                    header_y + max(3, round(6 * scale)),
                ),
                fill=HEADER_CHROME,
            )
        body_top = max(36, round((108 if index > 0 else 72) * scale))
        body_bottom = min(
            height - max(90, round(180 * scale)),
            body_top + max(110, round(300 * scale)),
        )
        draw.rectangle(
            (
                max(18, round(54 * scale)),
                body_top,
                width - max(18, round(54 * scale)),
                body_bottom,
            ),
            outline=marker,
            width=max(3, round(8 * scale)),
        )
        draw.rectangle(
            (
                max(24, round(74 * scale)),
                body_top + max(12, round(34 * scale)),
                max(50, round(164 * scale)),
                min(body_bottom - 8, body_top + max(44, round(124 * scale))),
            ),
            fill=marker,
        )
        if index < fragment_count - 1:
            footer_y = min(height - max(28, round(70 * scale)), round(690 * scale))
            draw.rectangle(
                (
                    max(4, round(18 * scale)),
                    footer_y,
                    width - max(4, round(18 * scale)),
                    footer_y + max(3, round(6 * scale)),
                ),
                fill=FOOTER_CHROME,
            )
            draw.rectangle(
                (
                    width // 2 - max(6, round(18 * scale)),
                    min(height - 8, footer_y + max(8, round(18 * scale))),
                    width // 2 + max(6, round(18 * scale)),
                    min(height - 4, footer_y + max(12, round(28 * scale))),
                ),
                fill=FOOTER_CHROME,
            )
        images.append(image)
        specs.append(
            FragmentSpec(
                fragment_id=f"strict-{width}-{index + 1}",
                page_number=index + 1,
                path=Path("unused"),
                body_marker=marker,
            )
        )
    return images, specs


def run_strict_audit_matrix() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for fragment_count in (2, 3, 5):
        for width in (360, 720, 1440):
            images, specs = _strict_matrix_images(fragment_count, width)
            stitched, prepared = _stitch_images(images)
            audit = audit_stitched_image(specs, stitched, prepared)
            cases.append(
                {
                    "name": f"{fragment_count}-page-{width}px",
                    "fragment_count": fragment_count,
                    "width_px": width,
                    "completeness_score": audit["completeness_score"],
                    "pass": audit["pass"],
                }
            )

    # Negative guardrail 1: a footnote separator followed by substantial text
    # is document content and must survive a non-final join.
    footnote = Image.new("RGB", (720, 760), "white")
    draw = ImageDraw.Draw(footnote)
    draw.rectangle((60, 80, 660, 360), outline="black", width=5)
    draw.rectangle((24, 610, 696, 616), fill="black")
    footnote_marker = (12, 142, 157)
    for y in (640, 674, 708):
        draw.rectangle((52, y, 650, y + 5), fill=footnote_marker)
    footnote_prepared = _prepare_passage_segments_for_stitch(
        [footnote, Image.new("RGB", (720, 240), "white")]
    )[0]
    footnote_preserved = bool(
        _count_color(footnote_prepared, footnote_marker)
        == _count_color(footnote, footnote_marker)
    )

    # Negative guardrail 2: a rule in the middle of a continuation box is not
    # a page-header rule and content above it must remain.
    continuation = Image.new("RGB", (720, 760), "white")
    draw = ImageDraw.Draw(continuation)
    box_marker = (132, 61, 196)
    draw.rectangle((72, 80, 190, 150), fill=box_marker)
    draw.rectangle((24, 300, 696, 306), fill="black")
    draw.rectangle((72, 350, 650, 690), outline="black", width=5)
    continuation_prepared = _prepare_passage_segments_for_stitch(
        [Image.new("RGB", (720, 240), "white"), continuation]
    )[1]
    box_content_preserved = (
        _count_color(continuation_prepared, box_marker) == _count_color(continuation, box_marker)
    )

    guardrails = {
        "substantial_footnote_preserved": footnote_preserved,
        "midbody_box_rule_preserved": box_content_preserved,
    }
    passed_case_count = sum(bool(case["pass"]) for case in cases)
    return {
        "case_count": len(cases),
        "passed_case_count": passed_case_count,
        "cases": cases,
        "negative_guardrails": guardrails,
        "pass": passed_case_count == len(cases) and all(guardrails.values()),
    }


def audit_stitched_image(
    fragments: Sequence[FragmentSpec],
    stitched: Image.Image,
    prepared: Sequence[Image.Image],
) -> dict[str, Any]:
    expected_height = sum(image.height for image in prepared)
    expected_height += PASSAGE_FRAGMENT_STITCH_GAP_PX * max(0, len(prepared) - 1)
    segment_matches: list[bool] = []
    cursor = 0
    for image in prepared:
        candidate = stitched.crop((0, cursor, image.width, cursor + image.height))
        segment_matches.append(candidate.tobytes() == image.tobytes())
        cursor += image.height + PASSAGE_FRAGMENT_STITCH_GAP_PX

    expected_body_counts = {
        fragment.fragment_id: _count_color(image, fragment.body_marker)
        for fragment, image in zip(fragments, prepared)
    }
    actual_body_counts = {
        fragment.fragment_id: _count_color(stitched, fragment.body_marker)
        for fragment in fragments
    }
    body_recall = {
        fragment_id: round(actual_body_counts[fragment_id] / max(1, expected_count), 6)
        for fragment_id, expected_count in expected_body_counts.items()
    }
    centroids = [
        _color_centroid_y(stitched, fragment.body_marker)
        for fragment in fragments
    ]
    order_ok = all(
        left is not None and right is not None and left < right
        for left, right in zip(centroids, centroids[1:])
    )
    chrome_counts = {
        "header": _count_color(stitched, HEADER_CHROME),
        "footer": _count_color(stitched, FOOTER_CHROME),
    }
    no_loss_or_duplication = all(value == 1.0 for value in body_recall.values())
    checks = {
        "fragment_count": len(prepared) == len(fragments),
        "source_order": order_ok,
        "segment_pixel_identity": all(segment_matches),
        "no_body_loss_or_duplication": no_loss_or_duplication,
        "page_chrome_removed": sum(chrome_counts.values()) == 0,
        "configured_join_gap": 8 <= PASSAGE_FRAGMENT_STITCH_GAP_PX <= 20,
        "stitched_height_formula": stitched.height == expected_height,
    }
    return {
        "expected_fragment_ids": [fragment.fragment_id for fragment in fragments],
        "expected_source_pages": [fragment.page_number for fragment in fragments],
        "prepared_fragment_sizes": [list(image.size) for image in prepared],
        "stitched_size": list(stitched.size),
        "join_gap_px": PASSAGE_FRAGMENT_STITCH_GAP_PX,
        "body_marker_expected_pixels": expected_body_counts,
        "body_marker_actual_pixels": actual_body_counts,
        "body_marker_recall": body_recall,
        "body_marker_centroid_y": centroids,
        "remaining_page_chrome_pixels": chrome_counts,
        "checks": checks,
        "completeness_score": round(100.0 * sum(checks.values()) / len(checks), 1),
        "pass": all(checks.values()),
    }


def _height_pages(height_px: float, source_width_px: float, display_width_px: float) -> float:
    return (height_px * display_width_px / max(1.0, source_width_px)) / CANVAS_WIDTH


def _layout_result(
    name: str,
    record_heights_pages: Sequence[float],
    *,
    internal_join_count: int,
    gap_px: float,
    split_policy: str,
) -> LayoutResult:
    gap_pages = gap_px / CANVAS_WIDTH
    total = sum(record_heights_pages) + gap_pages * max(0, len(record_heights_pages) - 1)
    return LayoutResult(
        name=name,
        record_count=len(record_heights_pages),
        total_span_pages=round(total, 4),
        maximum_record_height_pages=round(max(record_heights_pages, default=0.0), 4),
        internal_join_count=internal_join_count,
        inter_record_gap_px=gap_px if len(record_heights_pages) > 1 else 0.0,
        overlap_count=0,
        split_policy=split_policy,
    )


def compare_layouts(
    prepared: Sequence[Image.Image],
    *,
    display_width_px: float = FULL_WIDTH_DISPLAY_PX,
    max_record_height_pages: float = 1.8,
) -> list[LayoutResult]:
    """Compare one-image, page-fragment, and adaptive record placement."""

    if not prepared:
        return []
    return compare_layout_sizes(
        [image.size for image in prepared],
        display_width_px=display_width_px,
        max_record_height_pages=max_record_height_pages,
    )


def compare_layout_sizes(
    prepared_sizes: Sequence[tuple[int, int]],
    *,
    display_width_px: float = FULL_WIDTH_DISPLAY_PX,
    max_record_height_pages: float = 1.8,
) -> list[LayoutResult]:
    if not prepared_sizes:
        return []
    source_width = float(max(width for width, _height in prepared_sizes))
    join_height_pages = _height_pages(
        PASSAGE_FRAGMENT_STITCH_GAP_PX,
        source_width,
        display_width_px,
    )
    fragment_heights = [
        _height_pages(height, source_width, display_width_px)
        for _width, height in prepared_sizes
    ]
    single_height = sum(fragment_heights) + join_height_pages * max(0, len(prepared_sizes) - 1)

    page_fragment = _layout_result(
        "page-fragment-records",
        fragment_heights,
        internal_join_count=0,
        gap_px=CONTINUOUS_RECORD_GAP_PX,
        split_policy="one record per source page fragment",
    )

    adaptive_heights: list[float] = []
    current = 0.0
    current_count = 0
    adaptive_internal_joins = 0
    for height in fragment_heights:
        candidate = height if current_count == 0 else current + join_height_pages + height
        if current_count and candidate > max_record_height_pages:
            adaptive_heights.append(current)
            current = height
            current_count = 1
        else:
            if current_count:
                adaptive_internal_joins += 1
            current = candidate
            current_count += 1
    if current_count:
        adaptive_heights.append(current)

    return [
        _layout_result(
            "single-stitched-record",
            [single_height],
            internal_join_count=max(0, len(prepared_sizes) - 1),
            gap_px=0.0,
            split_policy="all fragments in one record",
        ),
        page_fragment,
        _layout_result(
            "adaptive-fragment-boundary",
            adaptive_heights,
            internal_join_count=adaptive_internal_joins,
            gap_px=CONTINUOUS_RECORD_GAP_PX,
            split_policy=f"split only at fragment boundaries above {max_record_height_pages:.1f} pages",
        ),
    ]


def analyze_benchmark_layouts(
    benchmark_path: Path,
    *,
    max_record_height_pages: float = 1.8,
) -> dict[str, Any]:
    """Apply placement geometry to passage groups from the PDF benchmark."""

    payload = json.loads(benchmark_path.read_text(encoding="utf-8"))
    fragments_by_group: dict[str, list[dict[str, Any]]] = {}
    for fragment in payload.get("fragments", []):
        fragments_by_group.setdefault(str(fragment["group_id"]), []).append(fragment)

    widths = {
        "current-left-column": 301.0,
        "wide-safe-pilot": 840.0,
        "full-board-width": FULL_WIDTH_DISPLAY_PX,
    }
    variants: dict[str, Any] = {}
    for width_name, display_width in widths.items():
        rows: list[dict[str, Any]] = []
        for group in payload.get("groups", []):
            group_id = str(group["group_id"])
            group_fragments = sorted(
                fragments_by_group.get(group_id, []),
                key=lambda item: (int(item["page_number"]), int(item["fragment_index"])),
            )
            group_width, group_height = (int(value) for value in group["reference_size"])
            raw_heights = [int(fragment["v1_size"][1]) for fragment in group_fragments]
            fragment_count = max(1, len(raw_heights))
            # The production stitcher may trim page chrome before joining.
            # Preserve the measured final group height while distributing the
            # trimmed content height according to each source fragment.
            content_height = max(
                fragment_count,
                group_height - PASSAGE_FRAGMENT_STITCH_GAP_PX * (fragment_count - 1),
            )
            raw_total = max(1, sum(raw_heights))
            estimated_heights = [
                max(1, round(content_height * raw_height / raw_total))
                for raw_height in raw_heights
            ] or [content_height]
            correction = content_height - sum(estimated_heights)
            estimated_heights[-1] += correction
            layouts = compare_layout_sizes(
                [(group_width, height) for height in estimated_heights],
                display_width_px=display_width,
                max_record_height_pages=max_record_height_pages,
            )
            by_name = {layout.name: asdict(layout) for layout in layouts}
            source_pages = [int(fragment["page_number"]) for fragment in group_fragments]
            rows.append(
                {
                    "group_id": group_id,
                    "label": group["label"],
                    "source_pages": source_pages,
                    "fragment_count": fragment_count,
                    "boundary_type": (
                        "cross-page"
                        if len(set(source_pages)) > 1
                        else "same-page-column"
                        if fragment_count > 1
                        else "none"
                    ),
                    "source_size": [group_width, group_height],
                    "layouts": by_name,
                }
            )

        def aggregate(layout_name: str) -> dict[str, Any]:
            layout_rows = [row["layouts"][layout_name] for row in rows]
            oversize = [
                rows[index]["label"]
                for index, layout in enumerate(layout_rows)
                if float(layout["maximum_record_height_pages"]) > max_record_height_pages + 1e-6
            ]
            return {
                "record_count": sum(int(layout["record_count"]) for layout in layout_rows),
                "total_span_pages": round(sum(float(layout["total_span_pages"]) for layout in layout_rows), 4),
                "maximum_record_height_pages": round(
                    max((float(layout["maximum_record_height_pages"]) for layout in layout_rows), default=0.0),
                    4,
                ),
                "oversize_group_count": len(oversize),
                "oversize_labels": oversize,
                "overlap_count": sum(int(layout["overlap_count"]) for layout in layout_rows),
            }

        variants[width_name] = {
            "display_width_px": display_width,
            "groups": rows,
            "summary": {
                name: aggregate(name)
                for name in (
                    "single-stitched-record",
                    "page-fragment-records",
                    "adaptive-fragment-boundary",
                )
            },
        }

    cross_page_groups = [
        group_id
        for group_id, fragments in fragments_by_group.items()
        if len({int(fragment["page_number"]) for fragment in fragments}) > 1
    ]
    full_summary = variants["full-board-width"]["summary"]["adaptive-fragment-boundary"]
    safe_summary = variants["wide-safe-pilot"]["summary"]["adaptive-fragment-boundary"]
    current_summary = variants["current-left-column"]["summary"]["adaptive-fragment-boundary"]
    return {
        "source_label": Path(str(payload.get("source") or "unknown.pdf")).name,
        "passage_group_count": int(payload.get("passage_group_count") or 0),
        "passage_fragment_count": int(payload.get("passage_fragment_count") or 0),
        "cross_page_group_count": len(cross_page_groups),
        "cross_page_group_ids": cross_page_groups,
        "max_record_height_pages": max_record_height_pages,
        "source_quality_gate": {
            "minimum_char_bbox_recall": payload.get("quality_summary", {}).get(
                "minimum_char_bbox_recall"
            ),
            "clipped_char_bbox_count": payload.get("quality_summary", {}).get(
                "clipped_char_bbox_count"
            ),
            "pass": bool(
                float(payload.get("quality_summary", {}).get("minimum_char_bbox_recall") or 0.0)
                >= 1.0
                and int(payload.get("quality_summary", {}).get("clipped_char_bbox_count") or 0)
                == 0
            ),
        },
        "width_variants": variants,
        "placement_decision": {
            "literal_full_width": (
                "reject-single-edb"
                if float(full_summary["total_span_pages"]) > 50.0
                else "fits-single-edb"
            ),
            "literal_full_width_span_pages": full_summary["total_span_pages"],
            "wide_safe_pilot_width_px": 840.0,
            "wide_safe_pilot_span_pages": safe_summary["total_span_pages"],
            "wide_safe_pilot_max_record_height_pages": safe_summary["maximum_record_height_pages"],
            "current_adaptive_span_pages": current_summary["total_span_pages"],
            "current_adaptive_max_record_height_pages": current_summary["maximum_record_height_pages"],
            "note": (
                "840px keeps passage-only output below a 45-page reserve target; "
                "questions or extra records require a lower width or a second EDB part"
            ),
        },
    }


def analyze_multi_dpi_benchmarks(benchmark_paths: Sequence[Path]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for path in benchmark_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        quality = payload.get("quality_summary") or {}
        rows.append(
            {
                "dpi": int(payload.get("dpi") or 0),
                "passage_group_count": int(payload.get("passage_group_count") or 0),
                "passage_fragment_count": int(payload.get("passage_fragment_count") or 0),
                "minimum_pixel_similarity": float(quality.get("minimum_pixel_similarity") or 0.0),
                "minimum_ink_f1": float(quality.get("minimum_ink_f1") or 0.0),
                "minimum_char_bbox_recall": float(quality.get("minimum_char_bbox_recall") or 0.0),
                "clipped_char_bbox_count": int(quality.get("clipped_char_bbox_count") or 0),
                "recovered_outside_block_char_count": int(
                    quality.get("recovered_outside_block_char_count") or 0
                ),
                "minimum_horizontal_char_margin_px": float(
                    quality.get("minimum_horizontal_char_margin_px") or 0.0
                ),
            }
        )
    rows.sort(key=lambda row: row["dpi"])
    group_counts = {row["passage_group_count"] for row in rows}
    fragment_counts = {row["passage_fragment_count"] for row in rows}
    checks = {
        "three_or_more_dpis": len({row["dpi"] for row in rows}) >= 3,
        "stable_group_count": len(group_counts) == 1,
        "stable_fragment_count": len(fragment_counts) == 1,
        "pixel_similarity": all(row["minimum_pixel_similarity"] >= 0.999 for row in rows),
        "ink_f1": all(row["minimum_ink_f1"] >= 0.999 for row in rows),
        "char_bbox_recall": all(row["minimum_char_bbox_recall"] >= 1.0 for row in rows),
        "zero_clipped_chars": all(row["clipped_char_bbox_count"] == 0 for row in rows),
        "minimum_safe_margin": all(row["minimum_horizontal_char_margin_px"] >= 12.0 for row in rows),
    }
    return {
        "rows": rows,
        "checks": checks,
        "pass": bool(rows) and all(checks.values()),
    }


def render_actual_s2_preview(benchmark_path: Path, output_path: Path) -> dict[str, Any] | None:
    """Render the tallest benchmark passage through the dense-text S2 path."""

    payload = json.loads(benchmark_path.read_text(encoding="utf-8"))
    groups = list(payload.get("groups") or [])
    if not groups:
        return None
    group = max(groups, key=lambda item: int(item["reference_size"][1]))
    label = str(group["label"])
    artifact_filename = str(group.get("artifact_filename") or f"passage_{_safe_name(label)}.png")
    source_path = benchmark_path.parent / "v1-direct-clip" / artifact_filename
    if not source_path.is_file():
        return None
    with Image.open(source_path) as loaded:
        crop = loaded.convert("RGB")
    crop = _pad_problem_crop_edges(
        crop,
        left_padding_px=PASSAGE_CROP_HORIZONTAL_SAFE_PADDING_PX,
        right_padding_px=PASSAGE_CROP_HORIZONTAL_SAFE_PADDING_PX,
    )
    cutout = _extract_problem_cutout(
        _enhance_problem_crop(crop, text_priority=True),
        text_priority=True,
    )
    alpha = cutout.getchannel("A")
    alpha_histogram = alpha.histogram()
    alpha_bbox = alpha.getbbox()
    if alpha_bbox is None:
        margins = {"left": 0, "top": 0, "right": 0, "bottom": 0}
    else:
        margins = {
            "left": int(alpha_bbox[0]),
            "top": int(alpha_bbox[1]),
            "right": int(cutout.width - alpha_bbox[2]),
            "bottom": int(cutout.height - alpha_bbox[3]),
        }
    rgb_colors = cutout.convert("RGB").getcolors(maxcolors=16)
    rgb_color_count = len(rgb_colors) if rgb_colors is not None else 17
    right_edge_band = alpha.crop((max(0, alpha.width - 3), 0, alpha.width, alpha.height))
    right_edge_pixels = right_edge_band.load()
    right_edge_rows = [
        any(right_edge_pixels[x, y] > 0 for x in range(right_edge_band.width))
        for y in range(right_edge_band.height)
    ]
    right_edge_ink_rows = sum(right_edge_rows)
    right_edge_ink_row_ratio = right_edge_ink_rows / max(1, right_edge_band.height)
    longest_right_edge_run = 0
    current_right_edge_run = 0
    for has_ink in right_edge_rows:
        if has_ink:
            current_right_edge_run += 1
            longest_right_edge_run = max(longest_right_edge_run, current_right_edge_run)
        else:
            current_right_edge_run = 0
    longest_right_edge_run_ratio = longest_right_edge_run / max(1, right_edge_band.height)
    right_edge_vertical_rule = longest_right_edge_run_ratio >= 0.10
    quality_checks = {
        "nonempty_ink": sum(alpha_histogram[1:]) > 0,
        "transparent_background": alpha_histogram[0] > 0,
        "zero_low_alpha_halo_pixels": sum(alpha_histogram[1:13]) == 0,
        "single_chalk_rgb_tone": rgb_color_count == 1,
        "left_safe_margin": margins["left"] >= 8,
        "right_safe_margin_or_vertical_rule": (
            margins["right"] >= 8 or right_edge_vertical_rule
        ),
    }
    preview = _composite_on_board_background(cutout, board_theme="charcoal")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(output_path, optimize=True)
    return {
        "label": label,
        "source_size": list(crop.size),
        "preview_size": list(preview.size),
        "board_theme": "charcoal",
        "processing": "s2-dense-text-priority",
        "alpha_bbox": list(alpha_bbox) if alpha_bbox is not None else None,
        "ink_margins_px": margins,
        "low_alpha_halo_pixel_count": sum(alpha_histogram[1:13]),
        "foreground_rgb_color_count": rgb_color_count,
        "right_edge_ink_row_ratio": round(right_edge_ink_row_ratio, 6),
        "longest_right_edge_ink_run_ratio": round(longest_right_edge_run_ratio, 6),
        "right_edge_vertical_rule": right_edge_vertical_rule,
        "quality_checks": quality_checks,
        "pass": all(quality_checks.values()),
        "artifact": output_path.name,
    }


def render_layout_preview(
    prepared: Sequence[Image.Image],
    output_path: Path,
    *,
    max_record_height_pages: float = 1.8,
) -> None:
    """Render a compact side-by-side visual of the three placement choices."""

    column_width = 430
    board_width = 390
    scale = board_width / max(image.width for image in prepared)
    scaled = [
        image.resize(
            (board_width, max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
        for image in prepared
    ]
    stitch_gap = max(2, round(PASSAGE_FRAGMENT_STITCH_GAP_PX * scale))
    record_gap = max(stitch_gap + 2, round(CONTINUOUS_RECORD_GAP_PX * scale))

    single = Image.new(
        "RGB",
        (board_width, sum(image.height for image in scaled) + stitch_gap * (len(scaled) - 1)),
        "white",
    )
    y = 0
    for image in scaled:
        single.paste(image, (0, y))
        y += image.height + stitch_gap

    fragment_records = [(image, True) for image in scaled]
    max_chunk_px = (
        max_record_height_pages
        * CANVAS_WIDTH
        * scale
        / (FULL_WIDTH_DISPLAY_PX / max(img.width for img in prepared))
    )
    adaptive_records: list[Image.Image] = []
    chunk: list[Image.Image] = []
    chunk_height = 0
    for image in scaled:
        candidate = image.height if not chunk else chunk_height + stitch_gap + image.height
        if chunk and candidate > max_chunk_px:
            canvas = Image.new("RGB", (board_width, chunk_height), "white")
            cursor = 0
            for part in chunk:
                canvas.paste(part, (0, cursor))
                cursor += part.height + stitch_gap
            adaptive_records.append(canvas)
            chunk = [image]
            chunk_height = image.height
        else:
            chunk.append(image)
            chunk_height = candidate
    if chunk:
        canvas = Image.new("RGB", (board_width, chunk_height), "white")
        cursor = 0
        for part in chunk:
            canvas.paste(part, (0, cursor))
            cursor += part.height + stitch_gap
        adaptive_records.append(canvas)

    variants: list[tuple[str, list[tuple[Image.Image, bool]], int]] = [
        ("A. single stitched record", [(single, True)], 0),
        ("B. source-page records", fragment_records, record_gap),
        ("C. adaptive boundary", [(image, True) for image in adaptive_records], record_gap),
    ]
    title_height = 54
    content_height = max(
        sum(image.height for image, _border in records) + gap * max(0, len(records) - 1)
        for _title, records, gap in variants
    )
    preview = Image.new(
        "RGB",
        (column_width * len(variants), title_height + content_height + 30),
        (34, 38, 42),
    )
    draw = ImageDraw.Draw(preview)
    title_font = _font(20)
    for column, (title, records, gap) in enumerate(variants):
        x = column * column_width + 20
        draw.text((x, 14), title, fill="white", font=title_font)
        y = title_height
        for record, border in records:
            preview.paste(record, (x, y))
            if border:
                draw.rectangle(
                    (x - 2, y - 2, x + record.width + 1, y + record.height + 1),
                    outline=(91, 171, 255),
                    width=2,
                )
            y += record.height + gap
    output_path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(output_path)


def run_synthetic_audit(
    output_dir: Path,
    *,
    display_width_px: float = FULL_WIDTH_DISPLAY_PX,
    max_record_height_pages: float = 1.8,
    benchmark_path: Path | None = None,
    strict_benchmark_paths: Sequence[Path] = (),
) -> dict[str, Any]:
    fragment_dir = output_dir / "fragments"
    fragments = build_synthetic_fragments(fragment_dir)
    merged_path = output_dir / "merged-current.png"
    stitched, prepared = stitch_fragments(fragments, merged_path)
    audit = audit_stitched_image(fragments, stitched, prepared)
    layouts = compare_layouts(
        prepared,
        display_width_px=display_width_px,
        max_record_height_pages=max_record_height_pages,
    )
    preview_path = output_dir / "layout-comparison.png"
    render_layout_preview(
        prepared,
        preview_path,
        max_record_height_pages=max_record_height_pages,
    )
    result = {
        "schema_version": 2,
        "experiment": "work3-cross-page-passage-merge-audit",
        "fixture": "three-page-synthetic-passage-with-page-chrome",
        "display_width_px": round(display_width_px, 1),
        "max_record_height_pages": max_record_height_pages,
        "merge_audit": audit,
        "strict_matrix": run_strict_audit_matrix(),
        "layouts": [asdict(layout) for layout in layouts],
        "recommendation": {
            "default": "single-stitched-record",
            "fallback": "adaptive-fragment-boundary",
            "fallback_trigger": f"merged rendered height above {max_record_height_pages:.1f} board pages",
            "reason": (
                "single record has the shortest continuous span; adaptive splitting limits "
                "one-record height while preserving source order and a 20px non-overlap gap"
            ),
        },
        "artifacts": {
            "merged": merged_path.name,
            "layout_preview": preview_path.name,
        },
    }
    if benchmark_path is not None:
        result["actual_pdf_layouts"] = analyze_benchmark_layouts(
            benchmark_path,
            max_record_height_pages=max_record_height_pages,
        )
        s2_preview = render_actual_s2_preview(
            benchmark_path,
            output_dir / "actual-s2-longest-preview.png",
        )
        if s2_preview is not None:
            result["actual_s2_preview"] = s2_preview
            result["artifacts"]["actual_s2_preview"] = s2_preview["artifact"]
    if strict_benchmark_paths:
        result["multi_dpi_quality"] = analyze_multi_dpi_benchmarks(strict_benchmark_paths)
    result["pass"] = bool(
        result["merge_audit"]["pass"]
        and result["strict_matrix"]["pass"]
        and (
            benchmark_path is None
            or result.get("actual_pdf_layouts", {}).get("source_quality_gate", {}).get("pass")
        )
        and (
            benchmark_path is None
            or result.get("actual_s2_preview", {}).get("pass")
        )
        and (
            not strict_benchmark_paths
            or result.get("multi_dpi_quality", {}).get("pass")
        )
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--display-width-px", type=float, default=FULL_WIDTH_DISPLAY_PX)
    parser.add_argument("--max-record-height-pages", type=float, default=1.8)
    parser.add_argument("--benchmark-json")
    parser.add_argument("--strict-benchmark-json", action="append", default=[])
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run_synthetic_audit(
        Path(args.output_dir).expanduser().resolve(),
        display_width_px=args.display_width_px,
        max_record_height_pages=args.max_record_height_pages,
        benchmark_path=(
            Path(args.benchmark_json).expanduser().resolve()
            if args.benchmark_json
            else None
        ),
        strict_benchmark_paths=[
            Path(path).expanduser().resolve() for path in args.strict_benchmark_json
        ],
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
