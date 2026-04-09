#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from PIL import Image

from build_structured_page_json import build_page_model
from edb_builder import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
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
    blocks: list[ContentBlock]
    actual_height_pages: float
    overflow_allowed: bool
    reading_heavy: bool


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
    return prepared_pages, page_models


def build_problem_entries(
    prepared_pages: list[PreparedPage],
    pages: list[PageModel],
    output_dir: Path,
    template: LayoutTemplate,
) -> list[ProblemEntry]:
    crop_dir = output_dir / "problem_crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    prepared_by_page_id = {page.page_id: page for page in prepared_pages}
    entries: list[ProblemEntry] = []

    for page in pages:
        prepared_page = prepared_by_page_id.get(page.page_id)
        if prepared_page is None:
            continue
        block_by_id = {block.block_id: block for block in page.blocks}

        for index, problem in enumerate(page.problems):
            problem_block_ids = iter_problem_block_ids(page, problem)
            blocks = [block_by_id[block_id] for block_id in problem_block_ids if block_id in block_by_id]
            raw_problem_number = problem.metadata.get("problem_number")
            if isinstance(raw_problem_number, int):
                problem_number = raw_problem_number
            elif isinstance(raw_problem_number, str) and raw_problem_number.isdigit():
                problem_number = int(raw_problem_number)
            else:
                problem_number = None
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
                    blocks=sorted(blocks, key=lambda block: (block.reading_order, block.bbox.top, block.bbox.left)),
                    actual_height_pages=estimate_height_pages(crop.size, template),
                    overflow_allowed=reading_heavy,
                    reading_heavy=reading_heavy,
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
    max_regions = 18 if max_regions is None else int(max_regions)
    timeout_ms = 12000 if timeout_ms is None else int(timeout_ms)
    resolved_mode = (mode or "").strip().lower() or ("auto" if enabled else "off")
    if resolved_mode not in {"off", "auto", "force"}:
        resolved_mode = "auto" if enabled else "off"
    effective_enabled = resolved_mode != "off"
    if (
        not effective_enabled
        and provider == "openai"
        and not model
        and not prompt
        and max_tokens is None
        and temperature is None
        and threshold == 0.72
        and max_regions == 18
        and timeout_ms == 12000
        and not save_debug
        and not fail_on_error
    ):
        return None
    return {
        "enabled": effective_enabled,
        "mode": resolved_mode,
        "provider": provider,
        "model": model or "gpt-5.4-mini",
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
        provider=str(ai_fallback_config.get("provider") or "openai"),
        model=str(ai_fallback_config.get("model") or "gpt-5.4-mini"),
        threshold=float(ai_fallback_config.get("threshold") or 0.72),
        max_regions=int(ai_fallback_config.get("max_regions") or 18),
        timeout_ms=int(ai_fallback_config.get("timeout_ms") or 12000),
        save_debug=bool(ai_fallback_config.get("save_debug")),
        fail_on_error=bool(ai_fallback_config.get("fail_on_error")),
    )


def _summarize_ai_fallback_usage(pages: list[PageModel], ai_fallback_config: dict[str, Any] | None) -> dict[str, Any] | None:
    if not ai_fallback_config:
        return None
    attempted_page_count = 0
    applied_page_count = 0
    status_counts: dict[str, int] = {}

    for page in pages:
        ai_summary = page.metadata.get("ai_fallback")
        if not isinstance(ai_summary, dict):
            continue
        if ai_summary.get("attempted"):
            attempted_page_count += 1
        if ai_summary.get("applied"):
            applied_page_count += 1
        status = str(ai_summary.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "requested": bool(ai_fallback_config.get("enabled")),
        "mode": ai_fallback_config.get("mode"),
        "provider": ai_fallback_config.get("provider"),
        "model": ai_fallback_config.get("model"),
        "attempted_page_count": attempted_page_count,
        "applied_page_count": applied_page_count,
        "status_counts": status_counts,
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


GENERIC_PROBLEM_TITLE_RE = re.compile(r"^\s*臾명빆\s*\d+(?:\s*[쨌:\-].*)?$")


def _normalize_problem_title(title: str | None, index: int, source_page_id: str, problem_number: int | None = None) -> str:
    raw = (title or "").strip()
    if raw and "problem" not in raw.lower() and not GENERIC_PROBLEM_TITLE_RE.match(raw):
        return raw
    if isinstance(problem_number, int) and problem_number > 0:
        return f"臾명빆 {problem_number}"
    return f"臾명빆 {index + 1:02d} 쨌 {source_page_id}"


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
) -> dict[str, Any]:
    rendered_page_paths = [Path(page.source_path).resolve() for page in prepared_pages]
    warning_messages: list[str] = []
    if placements and len(placements) <= len(prepared_pages):
        warning_messages.append("臾명빆 遺꾨━媛 異⑸텇?섏? ?딆븘 ?섏씠吏 ?⑥쐞??媛源앷쾶 臾띠씤 ??ぉ???덉뒿?덈떎.")

    problems: list[dict[str, Any]] = []
    for index, placement in enumerate(placements):
        crop_path = Path(str(placement["crop_path"])).resolve()
        source_path = Path(str(placement["source_path"])).resolve()
        problems.append(
            {
                "id": placement["problem_id"],
                "title": _normalize_problem_title(
                    str(placement.get("title") or ""),
                    index,
                    str(placement["source_page_id"]),
                    int(placement["problem_number"]) if str(placement.get("problem_number") or "").isdigit() else None,
                ),
                "problemNumber": int(placement["problem_number"]) if str(placement.get("problem_number") or "").isdigit() else None,
                "subject": str(placement["subject"]),
                "imagePath": _to_file_uri(crop_path),
                "sourceImagePath": _to_file_uri(source_path),
                "sourceFileName": source_path.name,
                "boardRenderPath": None,
                "actualHeightPages": float(placement["actual_content_height_pages"]),
                "overflowAllowed": bool(placement["overflow_allowed"]),
                "readingHeavy": bool(placement["overflow_allowed"]),
                "sourcePageId": str(placement["source_page_id"]),
                "startYPages": float(placement["start_y_pages"]),
                "snappedNextStartYPages": float(placement["snapped_next_start_y_pages"]),
                "overflowAmountPages": float(placement["overflow_amount_pages"]),
                "overflowViolation": bool(placement["overflow_violation"]),
                "slotSpanCount": int(placement["slot_span_count"]),
                "recordMode": str(placement.get("record_mode") or record_mode),
                "textRecordCount": int(placement.get("text_record_count", 0)),
                "imageRecordCount": int(placement.get("image_record_count", 0)),
            }
        )

    return {
        "session_name": output_dir.name,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "data_source": "question_export",
        "output_dir": str(output_dir.resolve()),
        "source_mode": "batch" if len(source_paths) > 1 else "single",
        "input_file_count": len(source_paths),
        "input_files": [str(Path(path).resolve()) for path in source_paths],
        "source_page_count": len(prepared_pages),
        "detected_problem_count": len(placements),
        "export_mode": "question",
        "record_mode": record_mode,
        "pages_json_path": str((output_dir / "pages.json").resolve()),
        "placements_json_path": str((output_dir / "placements.json").resolve()),
        "edb_path": str(edb_path.resolve()) if edb_path else None,
        "edb_file_uri": _to_file_uri(edb_path),
        "rendered_page_paths": [str(path) for path in rendered_page_paths],
        "rendered_page_file_uris": [_to_file_uri(path) for path in rendered_page_paths],
        "template": _template_to_dict(
            LayoutTemplate(
                name="academy-default",
                board_page_count=max(50, len(placements) * 2 or 50),
                base_slot_height_pages=1.2,
            )
        ),
        "ai_fallback": ai_fallback_config,
        "ai_summary": ai_summary,
        "warning_messages": warning_messages,
        "problems": problems,
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
                "source_page_id": entry.source_page_id,
                "source_path": entry.source_path,
                "bbox": {
                    "left": entry.bounds.left,
                    "top": entry.bounds.top,
                    "width": entry.bounds.width,
                    "height": entry.bounds.height,
                },
            },
        )
        for entry in problem_entries
    ]


def build_image_only_records(problem_entries: list[ProblemEntry], template: LayoutTemplate) -> tuple[list[bytes], list[dict[str, object]]]:
    placements = place_problems(placement_inputs(problem_entries), template=template)
    available_width_px = CANVAS_HEIGHT * template.fixed_left_zone_ratio - LEFT_MARGIN_PX - RIGHT_PADDING_PX

    records: list[bytes] = []
    placement_summaries: list[dict[str, object]] = []
    for record_id, placement in enumerate(placements):
        crop_path = Path(str(placement.metadata["crop_path"]))
        image_bytes = crop_path.read_bytes()
        preview_bytes = build_preview_image_bytes(image_bytes, max_size=(768, 768), quality=88)
        height_px = placement.actual_content_height_pages * CANVAS_WIDTH
        y_px = placement.start_y_pages * CANVAS_WIDTH + TOP_PADDING_PX
        records.append(
            build_image_record(
                ImageRecordSpec(
                    record_id=record_id,
                    image_primary=image_bytes,
                    image_secondary=preview_bytes,
                    x=normalize_x_px(LEFT_MARGIN_PX),
                    y=normalize_y_px(y_px, page_count_hint=template.board_page_count),
                    width_hint=normalize_width_px(available_width_px),
                    height_hint=normalize_height_px(height_px, page_count_hint=template.board_page_count),
                )
            )
        )
        placement_summaries.append(
            {
                "problem_id": placement.problem_id,
                "title": placement.metadata["title"],
                "problem_number": placement.metadata.get("problem_number"),
                "subject": str(placement.subject),
                "crop_path": str(crop_path),
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
                "record_mode": "image-only",
                "text_record_count": 0,
                "image_record_count": 1,
            }
        )

    return records, placement_summaries


def build_mixed_records(
    problem_entries: list[ProblemEntry],
    template: LayoutTemplate,
    *,
    output_dir: Path,
    text_confidence_threshold: float,
) -> tuple[list[bytes], list[dict[str, object]]]:
    placements = place_problems(placement_inputs(problem_entries), template=template)
    entries_by_problem_id = {entry.problem_id: entry for entry in problem_entries}
    available_width_px = CANVAS_HEIGHT * template.fixed_left_zone_ratio - LEFT_MARGIN_PX - RIGHT_PADDING_PX
    block_crop_dir = output_dir / "block_crops"
    block_crop_dir.mkdir(parents=True, exist_ok=True)

    records: list[bytes] = []
    placement_summaries: list[dict[str, object]] = []
    next_record_id = 0

    for placement in placements:
        entry = entries_by_problem_id[placement.problem_id]
        scale = available_width_px / max(entry.bounds.width, 1.0)
        problem_origin_x_px = LEFT_MARGIN_PX
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
                crop.save(crop_path)
                image_bytes = crop_path.read_bytes()
                preview_bytes = build_preview_image_bytes(image_bytes, max_size=(768, 768), format_hint="PNG", quality=88)
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
            image_bytes = entry.crop_path.read_bytes()
            preview_bytes = build_preview_image_bytes(image_bytes, max_size=(768, 768), quality=88)
            records.append(
                build_image_record(
                    ImageRecordSpec(
                        record_id=next_record_id,
                        image_primary=image_bytes,
                        image_secondary=preview_bytes,
                        x=normalize_x_px(LEFT_MARGIN_PX),
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
                "record_mode": "mixed",
                "text_record_count": text_record_count,
                "image_record_count": image_record_count,
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
) -> tuple[list[bytes], list[dict[str, object]], int]:
    if record_mode == "image-only":
        return (*build_image_only_records(problem_entries, template), 4)

    records, placement_summaries = build_mixed_records(
        problem_entries,
        template,
        output_dir=output_dir,
        text_confidence_threshold=text_confidence_threshold,
    )
    header_flag = 4 if any(item["image_record_count"] for item in placement_summaries) else 3
    return records, placement_summaries, header_flag


def write_ui_prototype_data(output_path: Path, placements: list[dict[str, object]]) -> None:
    payload = {
        "problems": [
            {
                "id": item["problem_id"],
                "title": item["title"],
                "subject": item["subject"],
                "imagePath": Path(item["crop_path"]).resolve().as_uri(),
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
    sync_ui: bool = False,
    ai_fallback_enabled: bool = False,
    ai_fallback: str | None = None,
    ai_fallback_provider: str = "openai",
    ai_fallback_model: str = "",
    ai_fallback_prompt: str = "",
    ai_fallback_max_tokens: int | None = None,
    ai_fallback_temperature: float | None = None,
    ai_fallback_threshold: float = 0.72,
    ai_fallback_max_regions: int = 18,
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

    save_pages_json(pages, out_dir / "pages.json")
    ai_summary = _summarize_ai_fallback_usage(pages, ai_fallback_config)
    problem_entries = build_problem_entries(prepared_pages, pages, out_dir, template)
    records, placements, header_flag = build_records(
        problem_entries,
        template,
        record_mode=record_mode,
        output_dir=out_dir,
        text_confidence_threshold=text_confidence_threshold,
    )

    summary = {
        "source_paths": [str(path) for path in source_paths],
        "output_dir": str(out_dir.resolve()),
        "pages_json_path": str((out_dir / "pages.json").resolve()),
        "problem_crop_dir": str((out_dir / "problem_crops").resolve()),
        "block_crop_dir": str((out_dir / "block_crops").resolve()),
        "record_count": len(records),
        "record_mode": record_mode,
        "header_flag": header_flag,
        "text_confidence_threshold": text_confidence_threshold,
        "ai_fallback": ai_fallback_config,
        "ai_summary": ai_summary,
        "placement_summary": build_placement_summary(placements),
        "placements": placements,
        "ocr_backend_requested": ocr,
    }

    placements_path = out_dir / "placements.json"
    placements_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    edb_path: Path | None = None
    if export_edb:
        edb_path = out_dir / edb_name
        write_edb(
            edb_path,
            build_edb(records, header_flag=header_flag, page_count_hint=template.board_page_count),
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
    )
    ui_session_path, synced_ui_path = write_ui_session_bundle(out_dir, ui_session, sync_ui=sync_ui)

    return {
        "output_dir": out_dir.resolve(),
        "edb_path": edb_path.resolve() if edb_path.exists() else None,
        "pages_json_path": (out_dir / "pages.json").resolve(),
        "placements_json_path": placements_path.resolve(),
        "ui_session": ui_session,
        "ui_session_path": ui_session_path.resolve(),
        "synced_ui_path": synced_ui_path.resolve() if synced_ui_path else None,
        "summary": summary,
        "ai_fallback": ai_fallback_config,
        "ai_summary": ai_summary,
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
    parser.add_argument("--text-confidence-threshold", type=float, default=0.78, help="Minimum OCR confidence for text records in mixed mode")
    parser.add_argument("--ai-fallback-enabled", action="store_true", help="Enable optional AI fallback settings")
    parser.add_argument("--ai-fallback", default=None, help="AI fallback mode override: off, auto, force")
    parser.add_argument("--ai-fallback-provider", default="openai", help="AI fallback provider name")
    parser.add_argument("--ai-fallback-model", default="", help="AI fallback model name")
    parser.add_argument("--ai-fallback-prompt", default="", help="AI fallback prompt template")
    parser.add_argument("--ai-fallback-max-tokens", type=int, default=None, help="AI fallback max output tokens")
    parser.add_argument("--ai-fallback-temperature", type=float, default=None, help="AI fallback sampling temperature")
    parser.add_argument("--ai-fallback-threshold", type=float, default=0.72, help="Low-confidence trigger threshold for AI fallback")
    parser.add_argument("--ai-fallback-max-regions", type=int, default=18, help="Maximum number of regions sent to AI fallback")
    parser.add_argument("--ai-fallback-timeout-ms", type=int, default=12000, help="Timeout in milliseconds for AI fallback")
    parser.add_argument("--ai-fallback-save-debug", action="store_true", help="Write AI fallback debug artifacts")
    parser.add_argument("--fail-on-ai-error", action="store_true", help="Raise an error if AI fallback fails")
    parser.add_argument("--prototype-data-out", default="ui_prototype\\prototype_data.js", help="Path to write UI prototype data JS")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    subject = resolve_subject(args.subject)
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
    )
    save_pages_json(pages, output_dir / "pages.json")

    template = LayoutTemplate(
        name=args.template_name,
        board_page_count=args.board_pages,
        base_slot_height_pages=args.slot_height,
    )
    problem_entries = build_problem_entries(prepared_pages, pages, output_dir, template)
    records, placements, header_flag = build_records(
        problem_entries,
        template,
        record_mode=args.record_mode,
        output_dir=output_dir,
        text_confidence_threshold=args.text_confidence_threshold,
    )

    edb_path = output_dir / f"{Path(args.source).stem}.edb"
    write_edb(
        edb_path,
        build_edb(records, header_flag=header_flag, page_count_hint=template.board_page_count),
    )
    ai_summary = _summarize_ai_fallback_usage(pages, ai_fallback_config)

    summary = {
        "source": str(args.source),
        "output_dir": str(output_dir),
        "edb_path": str(edb_path),
        "pages_json_path": str(output_dir / "pages.json"),
        "problem_crop_dir": str(output_dir / "problem_crops"),
        "block_crop_dir": str(output_dir / "block_crops"),
        "record_count": len(records),
        "record_mode": args.record_mode,
        "header_flag": header_flag,
        "text_confidence_threshold": args.text_confidence_threshold,
        "ai_fallback": ai_fallback_config,
        "ai_summary": ai_summary,
        "placement_summary": build_placement_summary(placements),
        "placements": placements,
        "ocr_backend_requested": args.ocr,
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
