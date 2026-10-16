#!/usr/bin/env python3
"""Benchmark a Stage-2 render that preserves selected source rectangles.

The manifest is a JSON object with a ``cases`` list. Each case needs an image
path and one or more ``[left, top, right, bottom]`` regions. The benchmark
keeps the normal transparent chalk render everywhere, then pastes the exact
source pixels back only inside those regions.
"""

from __future__ import annotations

import argparse
import io
import json
import statistics
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw

from build_problem_board_edb import (
    DEFAULT_BOARD_THEME,
    _composite_on_board_background,
    _enhance_problem_crop,
    _extract_problem_cutout,
    _resolve_chalk_color,
)


def _clamp_region(region: list[Any], size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = size
    left, top, right, bottom = (int(round(float(value))) for value in region)
    left = max(0, min(width, left))
    top = max(0, min(height, top))
    right = max(left, min(width, right))
    bottom = max(top, min(height, bottom))
    return left, top, right, bottom


def _render_stage2(source: Image.Image, *, text_priority: bool) -> Image.Image:
    return _extract_problem_cutout(
        _enhance_problem_crop(source, text_priority=text_priority),
        chalk_color=_resolve_chalk_color(DEFAULT_BOARD_THEME),
        text_priority=text_priority,
    )


def _render_selective(
    source: Image.Image,
    regions: list[tuple[int, int, int, int]],
    *,
    text_priority: bool,
) -> Image.Image:
    rendered = _render_stage2(source, text_priority=text_priority)
    opaque_source = source.convert("RGBA")
    rendered_regions = _scale_regions(regions, source.size, rendered.size)
    for source_region, rendered_region in zip(regions, rendered_regions, strict=True):
        if source_region[2] <= source_region[0] or source_region[3] <= source_region[1]:
            continue
        patch = opaque_source.crop(source_region)
        target_size = (
            rendered_region[2] - rendered_region[0],
            rendered_region[3] - rendered_region[1],
        )
        if patch.size != target_size:
            patch = patch.resize(target_size, Image.Resampling.LANCZOS)
        rendered.paste(patch, (rendered_region[0], rendered_region[1]))
    return rendered


def _scale_regions(
    regions: list[tuple[int, int, int, int]],
    source_size: tuple[int, int],
    target_size: tuple[int, int],
) -> list[tuple[int, int, int, int]]:
    source_width, source_height = source_size
    target_width, target_height = target_size
    x_scale = target_width / max(1, source_width)
    y_scale = target_height / max(1, source_height)
    return [
        (
            int(round(left * x_scale)),
            int(round(top * y_scale)),
            int(round(right * x_scale)),
            int(round(bottom * y_scale)),
        )
        for left, top, right, bottom in regions
    ]


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _median_ms(callback, runs: int) -> float:
    samples = []
    for _ in range(max(1, runs)):
        started = time.perf_counter()
        callback()
        samples.append((time.perf_counter() - started) * 1000.0)
    return statistics.median(samples)


def _outside_region_difference_bbox(
    current: Image.Image,
    selective: Image.Image,
    regions: list[tuple[int, int, int, int]],
) -> tuple[int, int, int, int] | None:
    difference = ImageChops.difference(current.convert("RGBA"), selective.convert("RGBA"))
    draw = ImageDraw.Draw(difference)
    for region in regions:
        draw.rectangle(region, fill=(0, 0, 0, 0))
    return difference.getbbox()


def _comparison_image(
    source: Image.Image,
    current: Image.Image,
    selective: Image.Image,
    regions: list[tuple[int, int, int, int]],
) -> Image.Image:
    current_board = _composite_on_board_background(current)
    selective_board = _composite_on_board_background(selective)
    raw = source.convert("RGB")
    if raw.size != current_board.size:
        raw = raw.resize(current_board.size, Image.Resampling.LANCZOS)
    panels = [raw, current_board, selective_board]
    width = max(panel.width for panel in panels)
    height = max(panel.height for panel in panels)
    preview_scale = min(1.0, 1000.0 / max(height, 1))
    preview_size = (max(1, int(round(width * preview_scale))), max(1, int(round(height * preview_scale))))
    resized = [panel.resize(preview_size, Image.Resampling.LANCZOS) for panel in panels]
    canvas = Image.new("RGB", (preview_size[0] * 3, preview_size[1]), "white")
    for index, panel in enumerate(resized):
        canvas.paste(panel, (preview_size[0] * index, 0))
    draw = ImageDraw.Draw(canvas)
    for region in regions:
        scaled = tuple(int(round(value * preview_scale)) for value in region)
        draw.rectangle(scaled, outline=(220, 40, 40), width=3)
        shifted = (scaled[0] + preview_size[0] * 2, scaled[1], scaled[2] + preview_size[0] * 2, scaled[3])
        draw.rectangle(shifted, outline=(80, 220, 120), width=3)
    return canvas


def benchmark_case(case: dict[str, Any], output_dir: Path, runs: int) -> dict[str, Any]:
    source_path = Path(str(case["image"])).expanduser().resolve()
    with Image.open(source_path) as loaded:
        source = loaded.convert("RGBA" if "A" in loaded.getbands() else "RGB")
    regions = [_clamp_region(region, source.size) for region in case.get("regions") or []]
    text_priority = bool(case.get("text_priority", False))

    current_ms = _median_ms(lambda: _render_stage2(source, text_priority=text_priority), runs)
    selective_ms = _median_ms(
        lambda: _render_selective(source, regions, text_priority=text_priority),
        runs,
    )
    current = _render_stage2(source, text_priority=text_priority)
    selective = _render_selective(source, regions, text_priority=text_priority)
    rendered_regions = _scale_regions(regions, source.size, current.size)
    current_payload = _png_bytes(current)
    selective_payload = _png_bytes(selective)

    case_dir = output_dir / str(case.get("name") or source_path.stem)
    case_dir.mkdir(parents=True, exist_ok=True)
    current.save(case_dir / "current_stage2.png")
    selective.save(case_dir / "selective_stage2.png")
    _comparison_image(source, current, selective, rendered_regions).save(case_dir / "comparison.png")

    preserved_pixels = sum((right - left) * (bottom - top) for left, top, right, bottom in regions)
    total_pixels = max(1, source.width * source.height)
    return {
        "name": str(case.get("name") or source_path.stem),
        "source": str(source_path),
        "size_px": [source.width, source.height],
        "regions": [list(region) for region in regions],
        "rendered_regions": [list(region) for region in rendered_regions],
        "preserved_area_ratio": round(preserved_pixels / total_pixels, 6),
        "current_render_median_ms": round(current_ms, 3),
        "selective_render_median_ms": round(selective_ms, 3),
        "incremental_render_median_ms": round(selective_ms - current_ms, 3),
        "current_png_bytes": len(current_payload),
        "selective_png_bytes": len(selective_payload),
        "png_size_ratio": round(len(selective_payload) / max(1, len(current_payload)), 4),
        "outside_preserved_regions_pixel_identical": _outside_region_difference_bbox(
            current, selective, rendered_regions
        )
        is None,
        "comparison_path": str((case_dir / "comparison.png").resolve()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=10)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cases = [benchmark_case(case, args.output_dir, args.runs) for case in manifest.get("cases") or []]
    report = {
        "case_count": len(cases),
        "runs_per_variant": max(1, args.runs),
        "cases": cases,
        "summary": {
            "current_render_median_ms": round(
                statistics.median(case["current_render_median_ms"] for case in cases), 3
            )
            if cases
            else 0.0,
            "selective_render_median_ms": round(
                statistics.median(case["selective_render_median_ms"] for case in cases), 3
            )
            if cases
            else 0.0,
            "incremental_render_median_ms": round(
                statistics.median(case["incremental_render_median_ms"] for case in cases), 3
            )
            if cases
            else 0.0,
            "png_size_ratio_median": round(
                statistics.median(case["png_size_ratio"] for case in cases), 4
            )
            if cases
            else 0.0,
            "outside_preserved_regions_all_pixel_identical": all(
                case["outside_preserved_regions_pixel_identical"] for case in cases
            ),
        },
    }
    report_path = args.output_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
