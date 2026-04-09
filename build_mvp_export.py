#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from build_structured_page_json import build_page_model
from edb_builder import ImageRecordSpec, build_edb, build_image_record, write_edb
from layout_template_schema import build_default_template
from placement_engine import build_export_plan
from preprocess import prepare_source_pages, prepare_source_pages_batch
from structured_schema import Box, PageModel, ProblemUnit, Subject, save_pages_json


def _resolve_subject(name: str | None) -> Subject:
    if not name:
        return Subject.UNKNOWN
    try:
        return Subject(name.lower())
    except ValueError:
        return Subject.UNKNOWN


def _encode_jpeg(image: Image.Image, quality: int = 92) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def _make_thumbnail(image: Image.Image, max_size: tuple[int, int] = (640, 640)) -> Image.Image:
    thumb = image.copy()
    thumb.thumbnail(max_size, Image.Resampling.LANCZOS)
    return thumb


def _to_file_uri(path: str | Path | None) -> str | None:
    if path is None:
        return None
    return Path(path).resolve().as_uri()


def _coerce_source_paths(source: str | Path | Sequence[str | Path]) -> list[Path]:
    if isinstance(source, (str, Path)):
        return [Path(source).resolve()]
    resolved = [Path(item).resolve() for item in source]
    if not resolved:
        raise ValueError("At least one source path is required")
    return resolved


def _problem_block_ids(problem: ProblemUnit) -> list[str]:
    return (
        list(problem.stem_block_ids)
        + list(problem.choice_block_ids)
        + list(problem.explanation_block_ids)
        + list(problem.figure_block_ids)
    )


def _problem_bounds(page_model: PageModel, problem: ProblemUnit) -> Box:
    block_lookup = {block.block_id: block for block in page_model.blocks}
    selected = [block_lookup[block_id] for block_id in _problem_block_ids(problem) if block_id in block_lookup]
    if not selected:
        return Box(left=0.0, top=0.0, width=float(page_model.width_px), height=float(page_model.height_px))

    left = min(block.bbox.left for block in selected)
    top = min(block.bbox.top for block in selected)
    right = max(block.bbox.right for block in selected)
    bottom = max(block.bbox.bottom for block in selected)
    return Box.from_points(left, top, right, bottom).expanded(24.0, max_width=page_model.width_px, max_height=page_model.height_px)


def _problem_title(page_model: PageModel, problem: ProblemUnit, index: int) -> str:
    if problem.title and problem.title.strip():
        return problem.title.strip()
    return f"{page_model.page_id} problem {index + 1}"


def _problem_is_reading_heavy(problem: ProblemUnit) -> bool:
    return (
        problem.subject in {Subject.KOREAN, Subject.ENGLISH}
        or len(problem.choice_block_ids) > 0
        or len(problem.figure_block_ids) > 0
    )


def _render_problem_crops(
    page_models: list[PageModel],
    prepared_pages,
    output_dir: Path,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prepared_by_page_id = {page.page_id: page for page in prepared_pages}
    crop_paths: dict[str, Path] = {}

    for page_model in page_models:
        prepared_page = prepared_by_page_id.get(page_model.page_id)
        if prepared_page is None:
            continue
        for index, problem in enumerate(page_model.problems):
            bounds = _problem_bounds(page_model, problem)
            crop = prepared_page.image.crop(
                (
                    int(bounds.left),
                    int(bounds.top),
                    int(bounds.right),
                    int(bounds.bottom),
                )
            )
            crop_path = output_dir / f"{problem.unit_id}.png"
            crop.save(crop_path)
            crop_paths[problem.unit_id] = crop_path

    return crop_paths


def _template_to_dict(template) -> dict[str, Any]:
    return {
        "name": template.name,
        "board_page_count": template.board_page_count,
        "base_slot_height_pages": template.base_slot_height_pages,
        "fixed_left_zone_ratio": template.fixed_left_zone_ratio,
        "preserve_right_writing_zone": template.preserve_right_writing_zone,
        "default_overflow_subjects": [subject.value for subject in template.default_overflow_subjects],
        "metadata": template.metadata,
    }


def render_board_page(page_model: PageModel, prepared_image: Image.Image, left_zone_ratio: float) -> Image.Image:
    canvas_width = 2160
    canvas_height = 3840
    canvas = Image.new("RGB", (canvas_width, canvas_height), "white")
    draw = ImageDraw.Draw(canvas)

    divider_x = int(round(canvas_width * left_zone_ratio))
    margin = 96
    max_width = max(1, divider_x - margin * 2)
    max_height = max(1, canvas_height - margin * 2)

    scaled = prepared_image.convert("RGB")
    scale = min(max_width / scaled.width, max_height / scaled.height)
    target_size = (max(1, int(round(scaled.width * scale))), max(1, int(round(scaled.height * scale))))
    scaled = scaled.resize(target_size, Image.Resampling.LANCZOS)

    canvas.paste(scaled, (margin, margin))
    draw.line([(divider_x, 0), (divider_x, canvas_height)], fill=(210, 210, 210), width=4)
    draw.text((24, 24), page_model.page_id, fill=(70, 70, 70))
    return canvas


def export_board_edb(board_images: list[Image.Image], output_path: Path, template_name: str) -> None:
    records: list[bytes] = []
    for index, image in enumerate(board_images):
        primary = _encode_jpeg(image, quality=92)
        secondary = _encode_jpeg(_make_thumbnail(image), quality=86)
        records.append(
            build_image_record(
                ImageRecordSpec(
                    record_id=index,
                    image_primary=primary,
                    image_secondary=secondary,
                    x=0.0,
                    y=round(index * 0.024, 6),
                    width_hint=0.52,
                    height_hint=0.024,
                )
            )
        )
    payload = build_edb(records, header_flag=4, version="6.0.5.3911")
    write_edb(output_path, payload)


def page_model_to_board_plan_dict(page_models: list[PageModel], export_plan) -> dict[str, object]:
    placements = []
    for placement in export_plan.placements:
        placements.append(
            {
                "problem_id": placement.problem_id,
                "subject": placement.subject,
                "start_y_pages": placement.start_y_pages,
                "nominal_slot_height_pages": placement.nominal_slot_height_pages,
                "actual_content_height_pages": placement.actual_content_height_pages,
                "actual_bottom_y_pages": placement.actual_bottom_y_pages,
                "snapped_next_start_y_pages": placement.snapped_next_start_y_pages,
                "overflow_allowed": placement.overflow_allowed,
                "overflow_amount_pages": placement.overflow_amount_pages,
                "overflow_violation": placement.overflow_violation,
                "slot_span_count": placement.slot_span_count,
                "board_capacity_exceeded": placement.board_capacity_exceeded,
                "metadata": placement.metadata,
            }
        )
    return {
        "template": _template_to_dict(export_plan.template),
        "placements": placements,
        "page_count": len(page_models),
        "problem_count": len(placements),
    }


def build_ui_session(
    page_models: list[PageModel],
    export_plan,
    rendered_board_paths: list[Path],
    problem_crop_paths: dict[str, Path],
    output_dir: Path,
    edb_path: Path | None,
    source_paths: Sequence[Path] | None = None,
) -> dict[str, Any]:
    resolved_source_paths = [Path(path).resolve() for path in (source_paths or [])]
    placements_by_id = {placement.problem_id: placement for placement in export_plan.placements}
    board_path_by_page_id = {
        page_model.page_id: rendered_board_paths[index]
        for index, page_model in enumerate(page_models)
        if index < len(rendered_board_paths)
    }

    problems: list[dict[str, Any]] = []
    for page_model in page_models:
        for index, problem in enumerate(page_model.problems):
            placement = placements_by_id.get(problem.unit_id)
            board_path = board_path_by_page_id.get(page_model.page_id)
            crop_path = problem_crop_paths.get(problem.unit_id)
            problems.append(
                {
                    "id": problem.unit_id,
                    "title": _problem_title(page_model, problem, index),
                    "subject": problem.subject.value,
                    "imagePath": _to_file_uri(crop_path),
                    "sourceImagePath": _to_file_uri(page_model.source_path),
                    "sourceFileName": Path(page_model.source_path).name,
                    "boardRenderPath": _to_file_uri(board_path),
                    "actualHeightPages": placement.actual_content_height_pages if placement else 1.0,
                    "overflowAllowed": placement.overflow_allowed if placement else False,
                    "readingHeavy": _problem_is_reading_heavy(problem),
                    "sourcePageId": page_model.page_id,
                    "startYPages": placement.start_y_pages if placement else 0.0,
                    "snappedNextStartYPages": placement.snapped_next_start_y_pages if placement else 0.0,
                    "overflowAmountPages": placement.overflow_amount_pages if placement else 0.0,
                    "overflowViolation": placement.overflow_violation if placement else False,
                    "slotSpanCount": placement.slot_span_count if placement else 1,
                }
            )

    return {
        "session_name": output_dir.name,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "data_source": "build_mvp_export",
        "output_dir": str(output_dir.resolve()),
        "source_mode": "batch" if len(resolved_source_paths) > 1 else "single",
        "input_file_count": max(1, len(resolved_source_paths)) if page_models else len(resolved_source_paths),
        "input_files": [str(path) for path in resolved_source_paths],
        "pages_json_path": str((output_dir / "pages.json").resolve()),
        "placements_json_path": str((output_dir / "placements.json").resolve()),
        "edb_path": str(edb_path.resolve()) if edb_path else None,
        "edb_file_uri": _to_file_uri(edb_path),
        "rendered_page_paths": [str(path.resolve()) for path in rendered_board_paths],
        "rendered_page_file_uris": [_to_file_uri(path) for path in rendered_board_paths],
        "template": _template_to_dict(export_plan.template),
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


def run_export(
    source: str | Path | Sequence[str | Path],
    *,
    output_dir: str | Path = "mvp_export",
    subject_name: str = "unknown",
    ocr: str = "auto",
    pdf_dpi: int = 200,
    detect_perspective: bool = False,
    skip_deskew: bool = False,
    skip_crop: bool = False,
    max_dimension: int | None = None,
    export_edb: bool = False,
    edb_name: str = "mvp_board.edb",
    sync_ui: bool = True,
) -> dict[str, Any]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "board_pages").mkdir(parents=True, exist_ok=True)
    (out_dir / "problem_crops").mkdir(parents=True, exist_ok=True)

    subject = _resolve_subject(subject_name)
    source_paths = _coerce_source_paths(source)
    prepared_pages = (
        prepare_source_pages(
            source_paths[0],
            pdf_dpi=pdf_dpi,
            detect_perspective=detect_perspective,
            deskew=not skip_deskew,
            crop_margins=not skip_crop,
            max_dimension=max_dimension,
        )
        if len(source_paths) == 1
        else prepare_source_pages_batch(
            source_paths,
            pdf_dpi=pdf_dpi,
            detect_perspective=detect_perspective,
            deskew=not skip_deskew,
            crop_margins=not skip_crop,
            max_dimension=max_dimension,
        )
    )

    page_models: list[PageModel] = [
        build_page_model(prepared_page, subject=subject, ocr_mode=ocr)
        for prepared_page in prepared_pages
    ]
    save_pages_json(page_models, out_dir / "pages.json")

    export_plan = build_export_plan(page_models, template=build_default_template())
    board_plan_dict = page_model_to_board_plan_dict(page_models, export_plan)
    (out_dir / "placements.json").write_text(json.dumps(board_plan_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    problem_crop_paths = _render_problem_crops(page_models, prepared_pages, out_dir / "problem_crops")

    rendered_board_paths: list[Path] = []
    board_images: list[Image.Image] = []
    for index, prepared_page in enumerate(prepared_pages):
        board_image = render_board_page(page_models[index], prepared_page.image, export_plan.template.fixed_left_zone_ratio)
        board_path = out_dir / "board_pages" / f"{prepared_page.page_id}.png"
        board_image.save(board_path)
        rendered_board_paths.append(board_path)
        board_images.append(board_image)

    board_plan_dict["rendered_page_paths"] = [str(path) for path in rendered_board_paths]
    (out_dir / "placements.json").write_text(json.dumps(board_plan_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    edb_path: Path | None = None
    if export_edb:
        edb_path = out_dir / edb_name
        export_board_edb(board_images, edb_path, export_plan.template.name)
        board_plan_dict["edb_path"] = str(edb_path)
        (out_dir / "placements.json").write_text(json.dumps(board_plan_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    ui_session = build_ui_session(
        page_models,
        export_plan,
        rendered_board_paths,
        problem_crop_paths,
        out_dir,
        edb_path,
        source_paths=source_paths,
    )
    ui_session_path, synced_ui_path = write_ui_session_bundle(out_dir, ui_session, sync_ui=sync_ui)

    return {
        "output_dir": out_dir,
        "source_paths": [str(path) for path in source_paths],
        "page_models": page_models,
        "problem_crop_paths": problem_crop_paths,
        "rendered_board_paths": rendered_board_paths,
        "edb_path": edb_path,
        "ui_session": ui_session,
        "ui_session_path": ui_session_path,
        "synced_ui_path": synced_ui_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the first MVP export for ClassIn EDB.")
    parser.add_argument("source", type=Path, nargs="+", help="Input image(s) or PDF")
    parser.add_argument("--output-dir", type=Path, default=Path("mvp_export"), help="Output directory")
    parser.add_argument("--subject", default="unknown", help="Subject override: math/science/korean/english/social")
    parser.add_argument("--ocr", default="auto", help="OCR backend: auto/paddle/tesseract/none")
    parser.add_argument("--pdf-dpi", type=int, default=200, help="PDF render DPI")
    parser.add_argument("--detect-perspective", action="store_true", help="Try perspective correction for photographed sources")
    parser.add_argument("--skip-deskew", action="store_true", help="Disable deskew")
    parser.add_argument("--skip-crop", action="store_true", help="Disable margin crop")
    parser.add_argument("--max-dimension", type=int, default=None, help="Resize long edge to this many pixels")
    parser.add_argument("--export-edb", action="store_true", help="Also export a board-image .edb")
    parser.add_argument("--edb-name", default="mvp_board.edb", help="Output .edb filename")
    parser.add_argument("--skip-ui-sync", action="store_true", help="Do not refresh ui_prototype/generated_session.js")
    args = parser.parse_args()

    result = run_export(
        args.source,
        output_dir=args.output_dir,
        subject_name=args.subject,
        ocr=args.ocr,
        pdf_dpi=args.pdf_dpi,
        detect_perspective=args.detect_perspective,
        skip_deskew=args.skip_deskew,
        skip_crop=args.skip_crop,
        max_dimension=args.max_dimension,
        export_edb=args.export_edb,
        edb_name=args.edb_name,
        sync_ui=not args.skip_ui_sync,
    )

    print(
        f"wrote pages.json, placements.json, ui_session.json, "
        f"{len(result['problem_crop_paths'])} problem crops, and {len(result['rendered_board_paths'])} board page renders -> {result['output_dir']}"
    )
    if args.export_edb:
        print(f"exported EDB -> {result['edb_path']}")
    print(f"wrote UI session -> {result['ui_session_path']}")
    if result["synced_ui_path"] is not None:
        print(f"synced UI session -> {result['synced_ui_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
