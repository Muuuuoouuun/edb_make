#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
import io
import json
from pathlib import Path

from PIL import Image

from assemble_page import group_problem_units
from build_structured_page_json import _resolve_subject
from edb_builder import (
    DEFAULT_PAGE_COUNT_HINT,
    ImageRecordSpec,
    TextRecordSpec,
    build_edb,
    build_image_record,
    build_preview_image_bytes,
    build_text_record,
    normalize_height_px,
    normalize_width_px,
    normalize_x_px,
    normalize_y_px,
    write_edb,
)
from layout_template_schema import LayoutTemplate, ProblemLayoutInput
from ocr_backend import build_ocr_backend
from placement_engine import place_problems, summarize_placements
from preprocess import prepare_pages
from segment import crop_block_images, segment_page
from structured_schema import AssetRef, AssetType, BlockType, PageModel, Subject, TextStyle, classify_text_block, infer_math_like_text, save_pages_json


BOARD_LEFT_MARGIN_PX = 88.0
BOARD_TOP_MARGIN_PX = 80.0


def _load_block_image_bytes(block) -> bytes:
    if block.asset and block.asset.source_path:
        return Path(block.asset.source_path).read_bytes()
    raise FileNotFoundError(f"Block asset missing for {block.block_id}")


def _encode_page_crop(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path, format="PNG")


def _estimate_problem_height_pages(page: PageModel, template: LayoutTemplate, content_zone_width_px: float) -> float:
    if page.width_px <= 0:
        return template.base_slot_height_pages
    scaled_height_px = content_zone_width_px * (page.height_px / page.width_px)
    return max(0.6, round((BOARD_TOP_MARGIN_PX + scaled_height_px + 32.0) / 590.0, 4))


def _page_to_problem_input(page: PageModel, template: LayoutTemplate, content_zone_width_px: float) -> ProblemLayoutInput:
    return ProblemLayoutInput(
        problem_id=page.page_id,
        subject=page.subject,
        actual_content_height_pages=_estimate_problem_height_pages(page, template, content_zone_width_px),
        overflow_allowed=page.subject in template.default_overflow_subjects,
        reading_heavy=page.subject in {Subject.KOREAN, Subject.ENGLISH},
        metadata={"page_id": page.page_id},
    )


def _normalize_ai_fallback_settings(
    ai_fallback_settings: dict[str, object] | None = None,
    **extra_settings: object,
) -> dict[str, object]:
    settings: dict[str, object] = {}

    if ai_fallback_settings:
        settings.update(ai_fallback_settings)

    for key, value in extra_settings.items():
        if key == "ai_fallback":
            if isinstance(value, dict):
                settings.update(value)
            else:
                settings["enabled"] = bool(value)
        elif key.startswith("ai_fallback_"):
            settings[key.removeprefix("ai_fallback_")] = value

    if "enabled" in settings:
        settings["enabled"] = bool(settings["enabled"])
    elif settings:
        settings["enabled"] = True
    else:
        settings["enabled"] = False

    for numeric_key in ("min_confidence", "max_blocks"):
        if numeric_key in settings and settings[numeric_key] is not None:
            try:
                settings[numeric_key] = float(settings[numeric_key]) if numeric_key == "min_confidence" else int(settings[numeric_key])
            except (TypeError, ValueError):
                settings[numeric_key] = None

    if "min_confidence" not in settings:
        settings["min_confidence"] = None
    if "max_blocks" not in settings:
        settings["max_blocks"] = None

    return settings


def _redact_ai_fallback_settings(settings: dict[str, object]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in settings.items():
        lower_key = key.lower()
        if any(token in lower_key for token in ("key", "token", "secret", "password")):
            redacted[key] = "<redacted>"
        elif callable(value):
            redacted[key] = getattr(value, "__name__", "<callable>")
        else:
            redacted[key] = value
    return redacted


def _serialize_ai_lines(lines: list[object]) -> list[object]:
    serialized: list[object] = []
    for line in lines:
        if isinstance(line, (str, int, float, bool)) or line is None:
            serialized.append(line)
        elif isinstance(line, dict):
            serialized.append(line)
        else:
            try:
                serialized.append(asdict(line))
            except Exception:
                serialized.append(str(line))
    return serialized


def _invoke_ai_fallback_resolver(
    resolver,
    *,
    image: Image.Image,
    block,
    page: PageModel,
    settings: dict[str, object],
) -> tuple[str, float | None, list[object]]:
    try:
        result = resolver(image=image, block=block, page=page, settings=settings)
    except TypeError:
        result = resolver(image, block, page, settings)

    if isinstance(result, dict):
        text = str(result.get("text", "")).strip()
        confidence = result.get("confidence")
        lines = _serialize_ai_lines(list(result.get("lines", []))) if result.get("lines") else []
        return text, float(confidence) if confidence is not None else None, lines

    if isinstance(result, tuple):
        text = str(result[0]).strip() if result else ""
        confidence = result[1] if len(result) > 1 else None
        lines = _serialize_ai_lines(list(result[2])) if len(result) > 2 and result[2] is not None else []
        return text, float(confidence) if confidence is not None else None, lines

    text = str(result).strip() if result is not None else ""
    return text, None, []


def _prepare_pages_with_assets(
    source: str | Path,
    output_dir: Path,
    *,
    subject: Subject,
    ocr_name: str,
    dpi: int,
    ai_fallback_settings: dict[str, object] | None = None,
) -> tuple[list[PageModel], dict[str, object]]:
    normalized_pages = prepare_pages(source, output_dir / "preprocess", dpi=dpi)
    ocr_backend = build_ocr_backend(ocr_name)
    normalized_ai_fallback_settings = _normalize_ai_fallback_settings(ai_fallback_settings)
    ai_fallback_requested = bool(normalized_ai_fallback_settings.get("enabled"))
    ai_fallback_summary = _redact_ai_fallback_settings(normalized_ai_fallback_settings)
    pages: list[PageModel] = []
    fallback_attempt_count = 0
    fallback_success_count = 0
    fallback_image_count = 0
    fallback_attempt_limit_raw = normalized_ai_fallback_settings.get("max_blocks")
    fallback_attempt_limit = int(fallback_attempt_limit_raw) if isinstance(fallback_attempt_limit_raw, int) and fallback_attempt_limit_raw >= 0 else None
    fallback_min_confidence_raw = normalized_ai_fallback_settings.get("min_confidence")
    fallback_min_confidence = (
        float(fallback_min_confidence_raw)
        if isinstance(fallback_min_confidence_raw, (int, float)) and fallback_min_confidence_raw is not None
        else None
    )

    for normalized in normalized_pages:
        page = segment_page(normalized.normalized_path, page_id=normalized.page_id, subject=subject)
        asset_paths = crop_block_images(normalized.normalized_path, page.blocks, output_dir / "assets" / normalized.page_id)
        normalized_image = normalized.image

        for block in page.blocks:
            block.asset = AssetRef(
                asset_id=f"{block.block_id}-crop",
                asset_type=AssetType.CROP,
                source_path=asset_paths.get(block.block_id),
                crop_box=block.bbox,
                width_px=int(block.bbox.width),
                height_px=int(block.bbox.height),
            )

            if block.block_type == BlockType.IMAGE:
                continue

            result = ocr_backend.ocr_box(normalized_image, block.bbox)
            block.confidence = result.confidence
            ocr_text = result.text.strip()
            needs_ai_fallback = False
            if ai_fallback_requested and fallback_attempt_limit is not None and fallback_attempt_count >= fallback_attempt_limit:
                block.metadata["ai_fallback_status"] = "skipped_limit"
            elif ai_fallback_requested and (not ocr_text or (fallback_min_confidence is not None and result.confidence is not None and result.confidence < fallback_min_confidence)):
                needs_ai_fallback = True

            if ocr_text:
                block.text = ocr_text
                block.ocr_lines = list(getattr(result, "lines", []))
                block.style = TextStyle(
                    font_size=max(10.0, block.bbox.height * 0.35),
                    math_like=infer_math_like_text(block.text),
                )
                block.block_type = classify_text_block(block.text)
            if needs_ai_fallback:
                ai_result_text = ""
                ai_result_confidence: float | None = None
                ai_result_lines: list[object] = []
                block.metadata["ai_fallback_requested"] = True
                block.metadata["ai_fallback_reason"] = "ocr_empty" if not ocr_text else "ocr_low_confidence"
                fallback_attempt_count += 1
                resolver = normalized_ai_fallback_settings.get("resolver")
                if callable(resolver):
                    try:
                        ai_result_text, ai_result_confidence, ai_result_lines = _invoke_ai_fallback_resolver(
                            resolver,
                            image=normalized_image.crop((int(block.bbox.left), int(block.bbox.top), int(block.bbox.right), int(block.bbox.bottom))),
                            block=block,
                            page=page,
                            settings=normalized_ai_fallback_settings,
                        )
                    except Exception as exc:  # pragma: no cover - defensive around external callbacks
                        block.metadata["ai_fallback_status"] = "failed"
                        block.metadata["ai_fallback_error"] = str(exc)
                    else:
                        if ai_result_text:
                            block.text = ai_result_text
                            block.ocr_lines = ai_result_lines  # type: ignore[assignment]
                            block.confidence = ai_result_confidence
                            block.style = TextStyle(
                                font_size=max(10.0, block.bbox.height * 0.35),
                                math_like=infer_math_like_text(block.text),
                            )
                            block.block_type = classify_text_block(block.text)
                            block.metadata["fallback_reason"] = "ai_fallback"
                            block.metadata["ai_fallback_status"] = "applied"
                            fallback_success_count += 1
                elif normalized_ai_fallback_settings.get("provider") or normalized_ai_fallback_settings.get("model"):
                    block.metadata["ai_fallback_status"] = "requested"
                if not ai_result_text:
                    block.metadata.setdefault("ai_fallback_status", "unavailable")
            if not block.text:
                block.block_type = BlockType.IMAGE
                if not ai_fallback_requested:
                    block.metadata["fallback_reason"] = "ocr_empty"
                else:
                    status = str(block.metadata.get("ai_fallback_status") or "")
                    if status == "failed":
                        block.metadata["fallback_reason"] = "ai_fallback_failed"
                    elif status == "skipped_limit":
                        block.metadata["fallback_reason"] = "ai_fallback_limit_reached"
                    else:
                        block.metadata["fallback_reason"] = "ai_fallback_unavailable"
                fallback_image_count += 1

        page.source_path = normalized.normalized_path
        page.metadata.update(normalized.metadata)
        if ai_fallback_requested:
            page.metadata["ai_fallback"] = {
                "settings": ai_fallback_summary,
                "attempt_count": fallback_attempt_count,
                "success_count": fallback_success_count,
                "image_fallback_count": fallback_image_count,
            }
        pages.append(group_problem_units(page))

    save_pages_json(pages, output_dir / "pages.json")
    return pages, {
        "enabled": ai_fallback_requested,
        "settings": ai_fallback_summary,
        "attempt_count": fallback_attempt_count,
        "success_count": fallback_success_count,
        "image_fallback_count": fallback_image_count,
    }


def _build_records_for_pages(
    pages: list[PageModel],
    placements,
    *,
    template: LayoutTemplate,
    page_count_hint: int,
) -> list[bytes]:
    content_zone_width_px = 1280.0 * template.fixed_left_zone_ratio
    record_id = 0
    records: list[bytes] = []

    for page, placement in zip(pages, placements):
        page_scale = content_zone_width_px / max(page.width_px, 1)
        page_origin_x = BOARD_LEFT_MARGIN_PX
        page_origin_y = placement.start_y_pages * 590.0 + BOARD_TOP_MARGIN_PX

        for block in page.sorted_blocks():
            x_px = page_origin_x + block.bbox.left * page_scale
            y_px = page_origin_y + block.bbox.top * page_scale
            width_px = max(16.0, block.bbox.width * page_scale)
            height_px = max(16.0, block.bbox.height * page_scale)

            if block.text and block.block_type not in {BlockType.IMAGE, BlockType.DIAGRAM, BlockType.TABLE}:
                font_size = int(max(10, min(28, round(height_px * 0.22))))
                records.append(
                    build_text_record(
                        TextRecordSpec(
                            record_id=record_id,
                            text=block.text,
                            x=normalize_x_px(x_px),
                            y=normalize_y_px(y_px, page_count_hint=page_count_hint),
                            width_hint=normalize_width_px(width_px),
                            font_size=font_size,
                            color_i32=-1,
                            tail=b"\x03",
                        )
                    )
                )
            else:
                image_bytes = _load_block_image_bytes(block)
                preview_bytes = build_preview_image_bytes(image_bytes, max_size=(768, 768))
                records.append(
                    build_image_record(
                        ImageRecordSpec(
                            record_id=record_id,
                            image_primary=image_bytes,
                            image_secondary=preview_bytes,
                            x=normalize_x_px(x_px),
                            y=normalize_y_px(y_px, page_count_hint=page_count_hint),
                            width_hint=normalize_width_px(width_px),
                            height_hint=normalize_height_px(height_px, page_count_hint=page_count_hint),
                        )
                    )
                )
            record_id += 1

    return records


def export_source_to_mvp_edb(
    source: str | Path,
    output_dir: str | Path,
    *,
    subject: Subject,
    ocr_name: str = "auto",
    dpi: int = 160,
    page_count_hint: int = DEFAULT_PAGE_COUNT_HINT,
    ai_fallback_settings: dict[str, object] | None = None,
    **extra_options: object,
) -> dict:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    template = LayoutTemplate(name="mvp-default")
    merged_ai_fallback_settings = _normalize_ai_fallback_settings(ai_fallback_settings, **extra_options)
    pages, ai_fallback_stats = _prepare_pages_with_assets(
        source,
        out_dir,
        subject=subject,
        ocr_name=ocr_name,
        dpi=dpi,
        ai_fallback_settings=merged_ai_fallback_settings,
    )
    content_zone_width_px = 1280.0 * template.fixed_left_zone_ratio
    problem_inputs = [_page_to_problem_input(page, template, content_zone_width_px) for page in pages]
    placements = place_problems(problem_inputs, template=template)
    placement_payload = {
        "template": {
            "name": template.name,
            "board_page_count": template.board_page_count,
            "base_slot_height_pages": template.base_slot_height_pages,
            "fixed_left_zone_ratio": template.fixed_left_zone_ratio,
        },
        "placements": [asdict(placement) for placement in placements],
        "summary": summarize_placements(placements),
    }
    (out_dir / "placements.json").write_text(json.dumps(placement_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    records = _build_records_for_pages(pages, placements, template=template, page_count_hint=page_count_hint)
    has_image_records = any(page.blocks and any(block.block_type == BlockType.IMAGE for block in page.blocks) for page in pages)
    payload = build_edb(records, header_flag=4 if has_image_records else 3)
    output_edb = out_dir / "output_mvp.edb"
    write_edb(output_edb, payload)

    summary = {
        "source": str(source),
        "output_edb": str(output_edb),
        "page_count": len(pages),
        "record_count": len(records),
        "placement_summary": placement_payload["summary"],
        "ocr_backend": ocr_name,
        "ai_fallback": ai_fallback_stats,
    }
    (out_dir / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a first MVP ClassIn .edb from one image or PDF source.")
    parser.add_argument("source", help="Path to the source image or PDF")
    parser.add_argument("--output-dir", default="mvp_output", help="Output directory for JSON, crops, placements, and .edb")
    parser.add_argument("--subject", default="unknown", help="Subject hint: math, science, korean, english, social, unknown")
    parser.add_argument("--ocr", default="auto", help="OCR backend: auto, paddleocr, tesseract, none, noop")
    parser.add_argument("--dpi", type=int, default=160, help="PDF render DPI")
    parser.add_argument("--ai-fallback", action="store_true", help="Enable optional AI fallback for empty OCR results")
    parser.add_argument("--ai-fallback-provider", default=None, help="Optional AI fallback provider name")
    parser.add_argument("--ai-fallback-model", default=None, help="Optional AI fallback model name")
    parser.add_argument("--ai-fallback-prompt", default=None, help="Optional AI fallback prompt or instruction")
    parser.add_argument("--ai-fallback-min-confidence", type=float, default=None, help="Trigger AI fallback below this OCR confidence")
    parser.add_argument("--ai-fallback-max-blocks", type=int, default=None, help="Optional cap on AI fallback attempts")
    args = parser.parse_args()

    summary = export_source_to_mvp_edb(
        args.source,
        args.output_dir,
        subject=_resolve_subject(args.subject),
        ocr_name=args.ocr,
        dpi=args.dpi,
        ai_fallback_settings={
            "enabled": args.ai_fallback,
            "provider": args.ai_fallback_provider,
            "model": args.ai_fallback_model,
            "prompt": args.ai_fallback_prompt,
            "min_confidence": args.ai_fallback_min_confidence,
            "max_blocks": args.ai_fallback_max_blocks,
        },
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
