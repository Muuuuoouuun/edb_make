#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import time
from pathlib import Path
from typing import Any

from ocr_backend import GeminiOCRBackend, NoOcrBackend, create_ocr_backend
from page_repair import AIFallbackConfig, build_ai_fallback_config, repair_page_model
from pipeline_cache import PipelineCache
from preprocess import PreparedPage, prepare_source_pages
from segment import crop_block_image, draw_segment_debug, segment_page
from structured_schema import BlockType, ContentBlock, PageModel, Subject, classify_text_block, infer_math_like_text, save_pages_json, TextStyle


def load_env_local() -> None:
    # edb_make 전용 .env.local 만 읽어옵니다. (Classin_Home 프로젝트와 완전히 분리)
    env_path = Path(__file__).resolve().parent / ".env.local"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

load_env_local()


def _resolve_subject(name: str | None) -> Subject:
    if not name:
        return Subject.UNKNOWN
    try:
        return Subject(name.lower())
    except ValueError:
        return Subject.UNKNOWN


def build_run_summary(
    pages: list[PageModel],
    *,
    output_dir: str | Path,
    source: str | Path,
    ocr_mode: str,
    ai_config: AIFallbackConfig | None = None,
) -> dict[str, object]:
    fallback_block_count = 0
    text_block_count = 0
    image_block_count = 0
    ai_attempted_pages = 0
    ai_applied_pages = 0
    ai_cache_hits = 0
    ocr_cache_hits = 0
    ocr_cache_misses = 0
    route_counts: dict[str, int] = {}
    route_tier_counts: dict[str, int] = {}

    for page in pages:
        ai_summary = page.metadata.get("ai_fallback")
        if isinstance(ai_summary, dict):
            if ai_summary.get("attempted"):
                ai_attempted_pages += 1
            if ai_summary.get("applied"):
                ai_applied_pages += 1
            if ai_summary.get("cache_hit"):
                ai_cache_hits += 1
        route_summary = page.metadata.get("route_decision")
        if isinstance(route_summary, dict):
            route = str(route_summary.get("route") or "unknown")
            route_counts[route] = route_counts.get(route, 0) + 1
            profile = route_summary.get("profile")
            if isinstance(profile, dict):
                tier = str(profile.get("tier") or "unknown")
                route_tier_counts[tier] = route_tier_counts.get(tier, 0) + 1
        for block in page.blocks:
            if block.text:
                text_block_count += 1
            if block.block_type == BlockType.IMAGE:
                image_block_count += 1
            if block.metadata.get("fallback_reason"):
                fallback_block_count += 1
            if block.metadata.get("ocr_cache_hit"):
                ocr_cache_hits += 1
            if block.metadata.get("ocr_cache_miss"):
                ocr_cache_misses += 1

    return {
        "source": str(source),
        "output_dir": str(output_dir),
        "ocr_mode": ocr_mode,
        "page_count": len(pages),
        "problem_count": sum(len(page.problems) for page in pages),
        "block_count": sum(len(page.blocks) for page in pages),
        "text_block_count": text_block_count,
        "image_block_count": image_block_count,
        "fallback_block_count": fallback_block_count,
        "ai_fallback": (ai_config or AIFallbackConfig()).to_metadata(),
        "ai_attempted_page_count": ai_attempted_pages,
        "ai_applied_page_count": ai_applied_pages,
        "ai_cache_hit_count": ai_cache_hits,
        "ocr_cache_hit_count": ocr_cache_hits,
        "ocr_cache_miss_count": ocr_cache_misses,
        "route_counts": route_counts,
        "route_tier_counts": route_tier_counts,
        "pages_json_path": str(Path(output_dir) / "pages.json"),
    }


def _maybe_build_gemini_escalation(
    *, ai_config: AIFallbackConfig | None, primary_backend_name: str
) -> GeminiOCRBackend | None:
    """Build a Gemini OCR backend for per-block escalation when AI fallback is
    enabled and the primary backend isn't already Gemini. Returns None when
    escalation is unavailable (no API key) or unnecessary."""
    if ai_config is None or not ai_config.enabled:
        return None
    if primary_backend_name == "gemini":
        return None
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        return None
    try:
        return GeminiOCRBackend()
    except Exception:
        return None


def _ocr_needs_escalation(ocr_result, *, threshold: float) -> bool:
    """A block should be re-OCRed by Claude when the primary engine returned
    empty text or low confidence — both strong signals the local engine
    struggled with this crop."""
    text = (ocr_result.text or "").strip()
    if not text:
        return True
    confidence = ocr_result.confidence
    if confidence is None:
        return False
    return float(confidence) < float(threshold)


def _should_skip_ocr_for_trusted_block(
    block: ContentBlock,
    *,
    page_segmenter: str | None,
) -> bool:
    return (
        page_segmenter == "pdf-text-markers"
        and block.metadata.get("segmenter") == "pdf-text-markers"
        and bool(block.metadata.get("force_image_record"))
        and bool(block.metadata.get("force_problem_start"))
        and isinstance(block.metadata.get("problem_number"), int)
        and block.metadata.get("problem_number_source") == "pdf_text_marker"
        and bool((block.text or "").strip())
        and block.confidence is not None
    )


_OCR_IDENTITY_NORMALIZATION_KEYS = (
    "source_type",
    "dpi",
    "pdf_renderer",
    "pdf_page_width_pt",
    "pdf_page_height_pt",
    "perspective_corrected",
    "deskewed",
    "deskew_skipped_reason",
    "margin_cropped",
    "margin_crop_box",
    "resized_to_max_dimension",
    "document_like",
)


def _file_identity(path_value: object) -> dict[str, Any]:
    path_text = str(path_value or "").strip()
    if not path_text:
        return {"path": ""}
    path = Path(path_text).expanduser().resolve()
    identity: dict[str, Any] = {"path": str(path)}
    try:
        stat = path.stat()
    except OSError:
        return identity
    identity["stat"] = {
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }
    return identity


def _bucket_coordinate(value: float, *, bucket_size: int = 4) -> int:
    return int(round(float(value) / float(bucket_size)) * bucket_size)


def _normalization_identity(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: metadata[key]
        for key in _OCR_IDENTITY_NORMALIZATION_KEYS
        if key in metadata
    }


def _build_ocr_cache_identity(
    prepared_page: PreparedPage,
    block: ContentBlock,
) -> dict[str, Any]:
    metadata = dict(prepared_page.metadata)
    source_path = metadata.get("original_source_path") or prepared_page.source_path
    bucket_size = 4
    return {
        "version": "ocr_block_identity_v1",
        "source": _file_identity(source_path),
        "page": {
            "id": prepared_page.page_id,
            "number": int(prepared_page.page_number),
            "size": {
                "width": int(prepared_page.image.width),
                "height": int(prepared_page.image.height),
            },
        },
        "normalization": _normalization_identity(metadata),
        "block": {
            "bbox_bucket_size_px": bucket_size,
            "bbox_bucket_px": {
                "left": _bucket_coordinate(block.bbox.left, bucket_size=bucket_size),
                "top": _bucket_coordinate(block.bbox.top, bucket_size=bucket_size),
                "right": _bucket_coordinate(block.bbox.right, bucket_size=bucket_size),
                "bottom": _bucket_coordinate(block.bbox.bottom, bucket_size=bucket_size),
            },
        },
    }


def _elapsed_ms(started_at: float) -> int:
    return int(round(max(0.0, time.perf_counter() - started_at) * 1000.0))


def _is_ai_backend(backend_name: str | None) -> bool:
    return (backend_name or "").strip().lower() in {
        "gemini",
        "google",
        "claude",
        "anthropic",
        "openai",
    }


def resolve_block_ocr_worker_count(
    item_count: int,
    *,
    ocr_mode: str,
    backend_name: str,
) -> int:
    bounded_item_count = max(0, int(item_count))
    if bounded_item_count <= 0:
        return 1
    max_workers = max(1, min(8, bounded_item_count or 1))
    raw_worker_count = os.environ.get("EDB_RECOGNITION_BLOCK_WORKERS", "").strip()
    if raw_worker_count:
        try:
            requested_workers = int(raw_worker_count)
        except ValueError:
            requested_workers = 0
        if requested_workers > 0:
            return max(1, min(max_workers, requested_workers))

    normalized_mode = (ocr_mode or "").strip().lower()
    normalized_backend = (backend_name or "").strip().lower()
    if normalized_mode in {"none", "noop"} or normalized_backend in {"none", "noop"}:
        return 1
    if normalized_mode in {"gemini", "google", "claude", "anthropic"}:
        return 3
    if normalized_backend in {"gemini", "google", "claude", "anthropic"}:
        return 3

    default_workers = min(max_workers, os.cpu_count() or 2)
    return max(1, default_workers)


def resolve_recognition_worker_count(
    item_count: int,
    *,
    ocr_mode: str = "auto",
    ai_config: AIFallbackConfig | None = None,
) -> int:
    if item_count <= 1:
        return 1

    raw_worker_count = (
        os.environ.get("EDB_RECOGNITION_PAGE_WORKERS", "").strip()
        or os.environ.get("EDB_RECOGNITION_WORKERS", "").strip()
    )
    if raw_worker_count:
        try:
            requested_workers = int(raw_worker_count)
        except ValueError:
            requested_workers = 2
        return max(1, min(item_count, requested_workers))

    # OCR/AI repair is mostly network-bound when Gemini is active, but each
    # page already parallelizes block OCR. Keep the default modest to overlap
    # page waits without inviting rate-limit failures.
    normalized_ocr = (ocr_mode or "auto").strip().lower()
    gemini_primary = normalized_ocr in {"auto", "gemini", "google", "claude", "anthropic"} and bool(
        os.environ.get("GEMINI_API_KEY", "").strip()
    )
    ai_enabled = bool(ai_config and ai_config.enabled)
    default_workers = 2 if gemini_primary or ai_enabled else min(2, os.cpu_count() or 2)
    return max(1, min(item_count, default_workers))


def build_page_models_for_prepared_pages(
    prepared_pages: list[PreparedPage],
    *,
    subject: Subject,
    ocr_mode: str,
    ai_config: AIFallbackConfig | None = None,
) -> list[PageModel]:
    worker_count = resolve_recognition_worker_count(
        len(prepared_pages),
        ocr_mode=ocr_mode,
        ai_config=ai_config,
    )
    if worker_count <= 1:
        pages = [
            build_page_model(
                prepared_page,
                subject=subject,
                ocr_mode=ocr_mode,
                ai_config=ai_config,
            )
            for prepared_page in prepared_pages
        ]
    else:
        def _build(prepared_page: PreparedPage) -> PageModel:
            return build_page_model(
                prepared_page,
                subject=subject,
                ocr_mode=ocr_mode,
                ai_config=ai_config,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            pages = list(executor.map(_build, prepared_pages))

    for page in pages:
        page.metadata["recognition_page_worker_count"] = worker_count
    return pages


def build_page_model(
    prepared_page: PreparedPage,
    subject: Subject,
    ocr_mode: str,
    *,
    ai_config: AIFallbackConfig | None = None,
    cache: PipelineCache | None = None,
) -> PageModel:
    recognition_started_at = time.perf_counter()
    pipeline_cache = cache or PipelineCache.for_source(prepared_page.source_path)
    segmentation_started_at = time.perf_counter()
    segmented_page = segment_page(prepared_page, page_id=prepared_page.page_id, subject=subject)
    segmentation_elapsed_ms = _elapsed_ms(segmentation_started_at)
    blocks = segmented_page.blocks
    escalation_threshold = float(ai_config.threshold) if ai_config else 0.0
    page_segmenter = segmented_page.metadata.get("segmenter")

    ocr_eligible_block_count = sum(
        1
        for block in blocks
        if block.block_type not in {BlockType.IMAGE, BlockType.DIAGRAM, BlockType.TABLE}
        and not _should_skip_ocr_for_trusted_block(block, page_segmenter=page_segmenter)
    )
    ocr_skipped_block_count = len(blocks) - ocr_eligible_block_count
    backend = create_ocr_backend(ocr_mode) if ocr_eligible_block_count > 0 else None
    backend_name = backend.engine_name if backend is not None else "none"
    escalation_backend = (
        _maybe_build_gemini_escalation(
            ai_config=ai_config,
            primary_backend_name=backend_name,
        )
        if backend is not None
        else None
    )
    block_worker_count = resolve_block_ocr_worker_count(
        ocr_eligible_block_count,
        ocr_mode=ocr_mode,
        backend_name=backend_name,
    )

    def _process_block(block):
        if _should_skip_ocr_for_trusted_block(block, page_segmenter=page_segmenter):
            block.metadata["ocr_backend"] = "pdf_text_marker"
            block.metadata["ocr_skipped"] = True
            block.metadata["ocr_skipped_reason"] = "trusted_pdf_text_marker"
            block.metadata["ocr_empty_text"] = False
            block.metadata["ocr_text_length"] = len((block.text or "").strip())
            block.metadata["ocr_line_count"] = len(block.ocr_lines)
            return

        if block.block_type in {BlockType.IMAGE, BlockType.DIAGRAM, BlockType.TABLE}:
            return
        if backend is None:
            return

        crop = crop_block_image(prepared_page, block)
        cache_identity = _build_ocr_cache_identity(prepared_page, block)
        cached_ocr = pipeline_cache.load_ocr_result(
            crop,
            backend_name=backend.engine_name,
            cache_identity=cache_identity,
        )
        block.metadata["ocr_stage"] = "primary_ocr"
        block.metadata["ocr_primary_backend"] = backend.engine_name
        if cached_ocr is not None:
            ocr_result = cached_ocr
            block.metadata["ocr_cache_hit"] = True
            block.metadata["ocr_cache_miss"] = False
        else:
            started_at = time.perf_counter()
            ocr_result = backend.recognize(crop)
            elapsed_ms = int(round((time.perf_counter() - started_at) * 1000.0))
            ocr_result.metadata.setdefault("backend_latency_ms", elapsed_ms)
            pipeline_cache.save_ocr_result(
                crop,
                ocr_result,
                backend_name=backend.engine_name,
                cache_identity=cache_identity,
            )
            block.metadata["ocr_cache_hit"] = False
            block.metadata["ocr_cache_miss"] = True

        if escalation_backend is not None and _ocr_needs_escalation(
            ocr_result, threshold=escalation_threshold
        ):
            block.metadata["ocr_escalation_attempted"] = True
            block.metadata["ocr_escalation_backend"] = escalation_backend.engine_name
            escalated_cached = pipeline_cache.load_ocr_result(
                crop,
                backend_name="gemini_escalated",
                cache_identity=cache_identity,
            )
            if escalated_cached is not None:
                escalated_result = escalated_cached
                block.metadata["ocr_escalation_cache_hit"] = True
            else:
                escalation_started = time.perf_counter()
                escalated_result = escalation_backend.recognize(crop)
                escalation_elapsed_ms = int(
                    round((time.perf_counter() - escalation_started) * 1000.0)
                )
                escalated_result.metadata.setdefault(
                    "backend_latency_ms", escalation_elapsed_ms
                )
                pipeline_cache.save_ocr_result(
                    crop,
                    escalated_result,
                    backend_name="gemini_escalated",
                    cache_identity=cache_identity,
                )
                block.metadata["ocr_escalation_cache_hit"] = False

            primary_text = (ocr_result.text or "").strip()
            escalated_text = (escalated_result.text or "").strip()
            primary_conf = ocr_result.confidence if ocr_result.confidence is not None else 0.0
            escalated_conf = (
                escalated_result.confidence if escalated_result.confidence is not None else 0.0
            )
            # Accept escalation when it produced text and either the primary
            # produced nothing, or the escalation is at least as confident.
            if escalated_text and (not primary_text or escalated_conf >= primary_conf):
                ocr_result = escalated_result
                block.metadata["ocr_escalated"] = True
                block.metadata["ocr_escalation_reason"] = (
                    "empty_primary" if not primary_text else "low_confidence_primary"
                )
            else:
                block.metadata["ocr_escalated"] = False

        block.metadata["ocr_backend"] = ocr_result.backend_name
        if isinstance(ocr_result.metadata.get("backend_latency_ms"), (int, float)):
            block.metadata["ocr_latency_ms"] = int(ocr_result.metadata["backend_latency_ms"])
        block.metadata["ocr_line_count"] = int(ocr_result.metadata.get("line_count") or len(ocr_result.lines))
        block.metadata["ocr_empty_text"] = bool(ocr_result.metadata.get("empty_text")) or not bool(ocr_result.text.strip())
        block.metadata["ocr_text_length"] = int(ocr_result.metadata.get("text_length") or len((ocr_result.text or "").strip()))
        block_type_hint = ocr_result.metadata.get("block_type_hint", "")
        if ocr_result.text.strip():
            block.text = ocr_result.text.strip()
            block.confidence = ocr_result.confidence
            block.ocr_lines = list(ocr_result.lines)
            block.style = TextStyle(
                font_size=max(10.0, block.bbox.height * 0.35),
                math_like=infer_math_like_text(block.text),
            )
            # Prefer the vision backend's block_type_hint when available;
            # otherwise infer from text.
            if block_type_hint and block_type_hint not in {"unknown", "stem"}:
                hint_map = {
                    "choice": BlockType.CHOICE,
                    "figure": BlockType.IMAGE,
                    "formula": BlockType.FORMULA,
                    "title": BlockType.TITLE,
                    "explanation": BlockType.EXPLANATION,
                }
                if block_type_hint in hint_map:
                    block.block_type = hint_map[block_type_hint]
                    block.metadata["block_type_source"] = "vision_hint"
                else:
                    inferred = classify_text_block(block.text)
                    if inferred != BlockType.STEM or block.block_type == BlockType.STEM:
                        block.block_type = inferred
            else:
                inferred = classify_text_block(block.text)
                if inferred != BlockType.STEM or block.block_type == BlockType.STEM:
                    block.block_type = inferred
        elif block_type_hint == "figure":
            block.block_type = BlockType.IMAGE
            block.metadata["fallback_reason"] = "vision_figure_hint"
        elif isinstance(backend, (NoOcrBackend, GeminiOCRBackend)) and block.block_type == BlockType.STEM:
            block.block_type = BlockType.IMAGE
            block.metadata["fallback_reason"] = "noop_ocr" if isinstance(backend, NoOcrBackend) else "gemini_no_text"

    block_ocr_started_at = time.perf_counter()
    if block_worker_count <= 1:
        for block in blocks:
            _process_block(block)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=block_worker_count) as executor:
            list(executor.map(_process_block, blocks))
    block_ocr_elapsed_ms = _elapsed_ms(block_ocr_started_at)

    ocr_cache_hit_count = sum(1 for block in blocks if block.metadata.get("ocr_cache_hit"))
    ocr_cache_miss_count = sum(1 for block in blocks if block.metadata.get("ocr_cache_miss"))
    ocr_escalation_attempted_block_count = sum(
        1 for block in blocks if block.metadata.get("ocr_escalation_attempted")
    )
    ocr_escalation_cache_hit_count = sum(
        1 for block in blocks if block.metadata.get("ocr_escalation_cache_hit")
    )
    ocr_escalated_block_count = sum(1 for block in blocks if block.metadata.get("ocr_escalated"))
    total_before_repair_ms = _elapsed_ms(recognition_started_at)
    ai_stages = {
        "ocr": {
            "stage": "ocr",
            "order": 1,
            "label": "1단계 OCR 인식",
            "status": "used" if ocr_eligible_block_count > 0 else "skipped",
            "provider": backend_name,
            "ai_powered": _is_ai_backend(backend_name),
            "eligible_block_count": ocr_eligible_block_count,
            "processed_block_count": ocr_cache_hit_count + ocr_cache_miss_count,
            "api_call_block_count": ocr_cache_miss_count,
            "cache_hit_count": ocr_cache_hit_count,
            "cache_miss_count": ocr_cache_miss_count,
            "skipped_block_count": ocr_skipped_block_count,
        },
        "ocr_escalation": {
            "stage": "ocr_escalation",
            "order": 2,
            "label": "2단계 블록 보강",
            "status": "used" if ocr_escalation_attempted_block_count > 0 else "skipped",
            "provider": "gemini",
            "ai_powered": True,
            "enabled": escalation_backend is not None,
            "attempted_block_count": ocr_escalation_attempted_block_count,
            "applied_block_count": ocr_escalated_block_count,
            "cache_hit_count": ocr_escalation_cache_hit_count,
        },
    }

    page = PageModel(
        page_id=prepared_page.page_id,
        width_px=prepared_page.image.width,
        height_px=prepared_page.image.height,
        subject=subject,
        source_path=prepared_page.source_path,
        blocks=blocks,
        metadata={
            **dict(prepared_page.metadata),
            **dict(segmented_page.metadata),
            "ocr_mode": ocr_mode,
            "pipeline_cache_dir": str(pipeline_cache.root_dir),
            "recognition_timing_ms": {
                "segmentation": segmentation_elapsed_ms,
                "block_ocr": block_ocr_elapsed_ms,
                "total_before_repair": total_before_repair_ms,
            },
            "ocr_block_worker_count": block_worker_count,
            "ocr_eligible_block_count": ocr_eligible_block_count,
            "ocr_skipped_block_count": ocr_skipped_block_count,
            "ocr_cache_hit_count": ocr_cache_hit_count,
            "ocr_cache_miss_count": ocr_cache_miss_count,
            "ocr_escalation_attempted_block_count": ocr_escalation_attempted_block_count,
            "ocr_escalation_cache_hit_count": ocr_escalation_cache_hit_count,
            "ocr_escalated_block_count": ocr_escalated_block_count,
            "ai_stages": ai_stages,
        },
    )
    return repair_page_model(prepared_page, page, ocr_mode=ocr_mode, config=ai_config, cache=pipeline_cache)


def build_pages_from_source(
    source: str | Path,
    *,
    subject: Subject = Subject.UNKNOWN,
    ocr_mode: str = "auto",
    ai_config: AIFallbackConfig | None = None,
    pdf_dpi: int = 200,
    detect_perspective: bool = False,
    deskew: bool = True,
    crop_margins: bool = True,
    max_dimension: int | None = None,
) -> list[PageModel]:
    prepared_pages = prepare_source_pages(
        source,
        pdf_dpi=pdf_dpi,
        detect_perspective=detect_perspective,
        deskew=deskew,
        crop_margins=crop_margins,
        max_dimension=max_dimension,
    )
    return build_page_models_for_prepared_pages(
        prepared_pages,
        subject=subject,
        ocr_mode=ocr_mode,
        ai_config=ai_config,
    )


def process_source(
    source: str | Path,
    output_dir: str | Path,
    *,
    subject: Subject = Subject.UNKNOWN,
    ocr_mode: str = "auto",
    ai_config: AIFallbackConfig | None = None,
    pdf_dpi: int = 200,
    detect_perspective: bool = False,
    deskew: bool = True,
    crop_margins: bool = True,
    max_dimension: int | None = None,
    debug_segments: bool = False,
) -> list[PageModel]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prepared_pages = prepare_source_pages(
        source,
        pdf_dpi=pdf_dpi,
        detect_perspective=detect_perspective,
        deskew=deskew,
        crop_margins=crop_margins,
        max_dimension=max_dimension,
    )
    pages = build_page_models_for_prepared_pages(
        prepared_pages,
        subject=subject,
        ocr_mode=ocr_mode,
        ai_config=ai_config,
    )
    for page in pages:
        page.metadata["schema_version"] = "v0.2"
        page.metadata["ocr_mode"] = ocr_mode
        page.metadata["ai_config"] = (ai_config or AIFallbackConfig()).to_metadata()

    if debug_segments:
        debug_dir = out_dir / "debug_segments"
        for prepared_page, page in zip(prepared_pages, pages):
            debug_path = debug_dir / f"{page.page_id}_segments.png"
            draw_segment_debug(prepared_page, page.blocks, debug_path)

    save_pages_json(pages, out_dir / "pages.json")
    return pages


def main() -> int:
    parser = argparse.ArgumentParser(description="Build structured page JSON from a PDF or image source.")
    parser.add_argument("source", help="Path to a PDF or image file")
    parser.add_argument("--output-dir", default="pipeline_output", help="Directory for generated JSON and assets")
    parser.add_argument("--subject", default="unknown", help="Subject hint: math, science, korean, english, social, unknown")
    parser.add_argument("--ocr", default="auto", help="OCR backend: auto, paddleocr, tesseract, gemini, none")
    parser.add_argument("--pdf-dpi", type=int, default=200, help="PDF render DPI")
    parser.add_argument("--detect-perspective", action="store_true", help="Try perspective correction for photographed sources")
    parser.add_argument("--skip-deskew", action="store_true", help="Disable deskew")
    parser.add_argument("--skip-crop", action="store_true", help="Disable margin crop")
    parser.add_argument("--max-dimension", type=int, default=None, help="Resize long edge to this many pixels")
    parser.add_argument("--ai-fallback", default="off", help="AI fallback mode: off, auto, force")
    parser.add_argument("--ai-provider", default="gemini", help="AI fallback provider: gemini (GEMINI_API_KEY required)")
    parser.add_argument("--ai-model", default="", help="AI model override (default: gemini-3.1-pro-preview for page repair)")
    parser.add_argument("--ai-threshold", type=float, default=0.72, help="Low-confidence trigger threshold for AI fallback")
    parser.add_argument("--ai-max-tokens", type=int, default=None, help="Maximum output tokens for AI fallback")
    parser.add_argument("--ai-max-regions", type=int, default=48, help="Maximum number of blocks to send to AI fallback")
    parser.add_argument("--ai-timeout-ms", type=int, default=30000, help="Timeout in milliseconds for AI fallback requests")
    parser.add_argument("--ai-save-debug", action="store_true", help="Write AI fallback debug artifacts under .pipeline_cache/ai_debug")
    parser.add_argument("--fail-on-ai-error", action="store_true", help="Raise an error instead of silently skipping on AI fallback failures")
    parser.add_argument("--debug-segments", action="store_true", help="Save block overlay images to <output-dir>/debug_segments/ for segmentation inspection")
    args = parser.parse_args()
    ai_config = build_ai_fallback_config(
        mode=args.ai_fallback,
        provider=args.ai_provider,
        model=args.ai_model,
        threshold=args.ai_threshold,
        max_tokens=args.ai_max_tokens,
        max_regions=args.ai_max_regions,
        timeout_ms=args.ai_timeout_ms,
        save_debug=args.ai_save_debug,
        fail_on_error=args.fail_on_ai_error,
    )

    pages = process_source(
        args.source,
        args.output_dir,
        subject=_resolve_subject(args.subject),
        ocr_mode=args.ocr,
        ai_config=ai_config,
        pdf_dpi=args.pdf_dpi,
        detect_perspective=args.detect_perspective,
        deskew=not args.skip_deskew,
        crop_margins=not args.skip_crop,
        max_dimension=args.max_dimension,
        debug_segments=args.debug_segments,
    )
    run_summary = build_run_summary(
        pages,
        output_dir=args.output_dir,
        source=args.source,
        ocr_mode=args.ocr,
        ai_config=ai_config,
    )
    summary_path = Path(args.output_dir) / "run_summary.json"
    summary_path.write_text(json.dumps(run_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(run_summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
