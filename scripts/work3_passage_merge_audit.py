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
    PASSAGE_FRAGMENT_STITCH_GAP_PX,
    _composite_on_board_background,
    _enhance_problem_crop,
    _extract_problem_cutout,
    _prepare_passage_segments_for_stitch,
)
from edb_builder import CANVAS_HEIGHT, CANVAS_WIDTH


BODY_MARKERS = ((214, 46, 46), (39, 86, 214), (35, 154, 88))
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
    prepared = _prepare_passage_segments_for_stitch(loaded)
    width = max(image.width for image in prepared)
    total_height = sum(image.height for image in prepared)
    total_height += PASSAGE_FRAGMENT_STITCH_GAP_PX * max(0, len(prepared) - 1)
    stitched = Image.new("RGB", (width, total_height), "white")
    cursor = 0
    for image in prepared:
        stitched.paste(image, (0, cursor))
        cursor += image.height + PASSAGE_FRAGMENT_STITCH_GAP_PX
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stitched.save(output_path)
    return stitched, prepared


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
        "configured_join_gap": PASSAGE_FRAGMENT_STITCH_GAP_PX == 16,
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
            "note": "840px keeps passage-only output below a 45-page reserve target; questions or extra records require a lower width or a second EDB part",
        },
    }


def render_actual_s2_preview(benchmark_path: Path, output_path: Path) -> dict[str, Any] | None:
    """Render the tallest benchmark passage through the dense-text S2 path."""

    payload = json.loads(benchmark_path.read_text(encoding="utf-8"))
    groups = list(payload.get("groups") or [])
    if not groups:
        return None
    group = max(groups, key=lambda item: int(item["reference_size"][1]))
    label = str(group["label"])
    source_path = benchmark_path.parent / "v1-direct-clip" / f"passage_{_safe_name(label)}.png"
    if not source_path.is_file():
        return None
    with Image.open(source_path) as loaded:
        crop = loaded.convert("RGB")
    cutout = _extract_problem_cutout(
        _enhance_problem_crop(crop, text_priority=True),
        text_priority=True,
    )
    preview = _composite_on_board_background(cutout, board_theme="charcoal")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(output_path, optimize=True)
    return {
        "label": label,
        "source_size": list(crop.size),
        "preview_size": list(preview.size),
        "board_theme": "charcoal",
        "processing": "s2-dense-text-priority",
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
    max_chunk_px = max_record_height_pages * CANVAS_WIDTH * scale / (FULL_WIDTH_DISPLAY_PX / max(img.width for img in prepared))
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
                draw.rectangle((x - 2, y - 2, x + record.width + 1, y + record.height + 1), outline=(91, 171, 255), width=2)
            y += record.height + gap
    output_path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(output_path)


def run_synthetic_audit(
    output_dir: Path,
    *,
    display_width_px: float = FULL_WIDTH_DISPLAY_PX,
    max_record_height_pages: float = 1.8,
    benchmark_path: Path | None = None,
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
        "schema_version": 1,
        "experiment": "work3-cross-page-passage-merge-audit",
        "fixture": "three-page-synthetic-passage-with-page-chrome",
        "display_width_px": round(display_width_px, 1),
        "max_record_height_pages": max_record_height_pages,
        "merge_audit": audit,
        "layouts": [asdict(layout) for layout in layouts],
        "recommendation": {
            "default": "single-stitched-record",
            "fallback": "adaptive-fragment-boundary",
            "fallback_trigger": f"merged rendered height above {max_record_height_pages:.1f} board pages",
            "reason": "single record has the shortest continuous span; adaptive splitting limits one-record height while preserving source order and a 20px non-overlap gap",
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
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["merge_audit"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
