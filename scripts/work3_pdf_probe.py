#!/usr/bin/env python3
"""Measure whether Korean/English PDF pages can use structure-first extraction.

Work 3 is an experimental track.  This probe does not change the production
pipeline; it records text-layer coverage, page routing, and rasterization cost
so the current raster path can be compared with PDF-structure and hybrid paths.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence

import fitz


SUPPORTED_SUBJECTS = ("korean", "english")
DEFAULT_RENDER_DPIS = (144, 200, 300)

ROUTE_PDF_STRUCTURE = "pdf-structure"
ROUTE_HYBRID = "hybrid"
ROUTE_RASTER_OCR = "raster-ocr"


def _parse_dpis(raw: str | Iterable[int]) -> tuple[int, ...]:
    values = raw.split(",") if isinstance(raw, str) else raw
    dpis = tuple(sorted({int(value) for value in values if int(value) > 0}))
    if not dpis:
        raise ValueError("at least one positive render DPI is required")
    return dpis


def _raw_chars(raw_dict: dict[str, Any]) -> list[dict[str, Any]]:
    chars: list[dict[str, Any]] = []
    for block in raw_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                chars.extend(char for char in span.get("chars", []) if isinstance(char, dict))
    return chars


def _bbox_area(bbox: Sequence[float], page_rect: fitz.Rect) -> float:
    if len(bbox) != 4:
        return 0.0
    rect = fitz.Rect(*(float(value) for value in bbox)) & page_rect
    return max(0.0, rect.width) * max(0.0, rect.height)


def _route_page(*, char_count: int, text_block_count: int, text_coverage: float) -> str:
    # Long exam passages comfortably exceed these thresholds. Short headers
    # alone intentionally land in hybrid so they cannot make a scanned page
    # look structure-ready.
    if char_count >= 80 and text_block_count >= 1 and text_coverage >= 0.008:
        return ROUTE_PDF_STRUCTURE
    if char_count >= 20 and text_block_count >= 1:
        return ROUTE_HYBRID
    return ROUTE_RASTER_OCR


def profile_page(page: fitz.Page, *, render_dpis: Sequence[int]) -> dict[str, Any]:
    parse_started = time.perf_counter()
    raw_dict = page.get_text("rawdict")
    parse_seconds = time.perf_counter() - parse_started

    chars = _raw_chars(raw_dict)
    text = "".join(str(char.get("c") or "") for char in chars)
    meaningful_chars = [char for char in chars if str(char.get("c") or "").strip()]
    page_area = max(1.0, page.rect.width * page.rect.height)
    text_area = sum(_bbox_area(char.get("bbox", ()), page.rect) for char in meaningful_chars)
    text_coverage = min(1.0, text_area / page_area)
    text_block_count = sum(1 for block in raw_dict.get("blocks", []) if block.get("type") == 0)
    image_block_count = sum(1 for block in raw_dict.get("blocks", []) if block.get("type") == 1)

    render_profiles: list[dict[str, Any]] = []
    for dpi in render_dpis:
        render_started = time.perf_counter()
        pixmap = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
        png_bytes = pixmap.tobytes("png")
        render_seconds = time.perf_counter() - render_started
        render_profiles.append(
            {
                "dpi": dpi,
                "width_px": pixmap.width,
                "height_px": pixmap.height,
                "pixel_count": pixmap.width * pixmap.height,
                "png_bytes": len(png_bytes),
                "render_seconds": round(render_seconds, 6),
            }
        )

    route = _route_page(
        char_count=len(meaningful_chars),
        text_block_count=text_block_count,
        text_coverage=text_coverage,
    )
    return {
        "page_index": page.number,
        "page_number": page.number + 1,
        "width_points": round(page.rect.width, 3),
        "height_points": round(page.rect.height, 3),
        "char_count": len(meaningful_chars),
        "hangul_char_count": sum("가" <= value <= "힣" for value in text),
        "latin_char_count": sum(value.isascii() and value.isalpha() for value in text),
        "text_block_count": text_block_count,
        "image_block_count": image_block_count,
        "text_bbox_coverage": round(text_coverage, 6),
        "text_parse_seconds": round(parse_seconds, 6),
        "route": route,
        "render_profiles": render_profiles,
    }


def _median(values: Iterable[float]) -> float:
    materialized = list(values)
    return round(statistics.median(materialized), 6) if materialized else 0.0


def profile_pdf(
    source: str | Path,
    *,
    subject: str,
    max_pages: int | None = None,
    render_dpis: Sequence[int] = DEFAULT_RENDER_DPIS,
) -> dict[str, Any]:
    normalized_subject = str(subject).strip().lower()
    if normalized_subject not in SUPPORTED_SUBJECTS:
        raise ValueError(f"Work 3 supports only: {', '.join(SUPPORTED_SUBJECTS)}")
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    dpis = _parse_dpis(render_dpis)

    started = time.perf_counter()
    with fitz.open(source_path) as document:
        page_limit = document.page_count if max_pages is None else min(document.page_count, max(0, max_pages))
        pages = [profile_page(document[index], render_dpis=dpis) for index in range(page_limit)]
        total_page_count = document.page_count
    wall_seconds = time.perf_counter() - started

    route_counts = Counter(page["route"] for page in pages)
    structure_ready_count = route_counts[ROUTE_PDF_STRUCTURE]
    if pages and structure_ready_count == len(pages):
        recommendation = "pdf-structure-first"
    elif route_counts[ROUTE_RASTER_OCR] == len(pages) and pages:
        recommendation = "raster-ocr"
    else:
        recommendation = "hybrid-page-routing"

    render_summary = []
    for dpi in dpis:
        matches = [
            profile
            for page in pages
            for profile in page["render_profiles"]
            if profile["dpi"] == dpi
        ]
        render_summary.append(
            {
                "dpi": dpi,
                "median_render_seconds_per_page": _median(item["render_seconds"] for item in matches),
                "median_png_bytes_per_page": int(statistics.median(item["png_bytes"] for item in matches)) if matches else 0,
                "median_pixel_count_per_page": int(statistics.median(item["pixel_count"] for item in matches)) if matches else 0,
            }
        )

    return {
        "schema_version": 1,
        "experiment": "work3-pdf-hybrid-probe",
        "source": str(source_path),
        "subject": normalized_subject,
        "total_page_count": total_page_count,
        "sampled_page_count": len(pages),
        "wall_seconds": round(wall_seconds, 6),
        "median_text_parse_seconds_per_page": _median(page["text_parse_seconds"] for page in pages),
        "route_counts": dict(sorted(route_counts.items())),
        "structure_ready_page_ratio": round(structure_ready_count / len(pages), 6) if pages else 0.0,
        "estimated_ocr_page_count": route_counts[ROUTE_RASTER_OCR],
        "external_api_cost_usd": 0.0,
        "recommended_pipeline": recommendation,
        "render_summary": render_summary,
        "pages": pages,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Korean or English PDF to profile")
    parser.add_argument("--subject", required=True, choices=SUPPORTED_SUBJECTS)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument(
        "--render-dpis",
        default=",".join(str(value) for value in DEFAULT_RENDER_DPIS),
        help="Comma-separated DPI values used only for raster cost comparison",
    )
    parser.add_argument("--output", default="", help="Optional JSON result path")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = profile_pdf(
        args.source,
        subject=args.subject,
        max_pages=args.max_pages,
        render_dpis=_parse_dpis(args.render_dpis),
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
