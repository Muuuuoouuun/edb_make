#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any
from difflib import SequenceMatcher

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ocr_backend import GeminiOCRBackend
from preprocess import prepare_source_pages
from segment import crop_block_image, segment_page
from structured_schema import BlockType, Subject
from user_settings import apply_to_env, load_user_settings


MODELS = ("gemini-3.1-pro-preview", "gemini-3.5-flash")


def _apply_saved_keys() -> dict[str, str]:
    settings = load_user_settings(Path(".app_runtime"))
    return apply_to_env(settings, overwrite=False)


def _source_candidates() -> list[Path]:
    candidates: list[Path] = []
    for path in (
        Path("tmp_test_inputs/physics_input.pdf"),
        Path("tmp_test_inputs/earth_input.pdf"),
    ):
        if path.exists():
            candidates.append(path)

    upload_dir = Path(".app_runtime/uploads")
    if upload_dir.exists():
        preferred_images = [
            path
            for path in sorted(upload_dir.iterdir())
            if path.suffix.lower() in {".png", ".jpg", ".jpeg"}
            and ("6" in path.name or "복소수" in path.name or "images" in path.name)
        ]
        candidates.extend(preferred_images[:2])

    return list(dict.fromkeys(path.resolve() for path in candidates))


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ):
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _synthetic_crop(text: str, *, size: tuple[int, int], font_size: int, blur: float = 0.0) -> Image.Image:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    font = _load_font(font_size)
    y = 18
    for line in text.splitlines():
        draw.text((22, y), line, fill=(20, 20, 20), font=font)
        try:
            bbox = draw.textbbox((22, y), line, font=font)
            line_height = max(font_size + 7, bbox[3] - bbox[1] + 8)
        except Exception:
            line_height = font_size + 8
        y += line_height
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(radius=blur))
    return image


def _build_synthetic_samples() -> list[dict[str, Any]]:
    synthetic_specs = [
        (
            "synthetic-korean-choice",
            "1. 다음 중 옳은 것을 고르시오.\n① ㄱ ② ㄴ ③ ㄷ ④ ㄱ, ㄴ ⑤ ㄴ, ㄷ",
            (760, 170),
            30,
            0.0,
        ),
        (
            "synthetic-math-expression",
            "2. 함수 f(x)=x^2-3x+2의 최솟값을 구하시오.",
            (760, 120),
            30,
            0.15,
        ),
        (
            "synthetic-science-view",
            "보기 ㄱ. 산화수가 증가한다.\nㄴ. 전자를 얻으면 환원된다.\nㄷ. 촉매는 반응 엔탈피를 바꾼다.",
            (780, 205),
            28,
            0.2,
        ),
        (
            "synthetic-small-print",
            "3. 0<x<π에서 sin x = 1/2을 만족하는 x의 값을 모두 구하시오.",
            (620, 82),
            22,
            0.25,
        ),
    ]
    samples: list[dict[str, Any]] = []
    for index, (name, expected_text, size, font_size, blur) in enumerate(synthetic_specs, start=1):
        crop = _synthetic_crop(expected_text, size=size, font_size=font_size, blur=blur)
        samples.append(
            {
                "source": f"synthetic:{name}",
                "page_id": "synthetic",
                "page_number": 1,
                "block_id": f"synthetic_block_{index}",
                "bbox": {"left": 0, "top": 0, "width": crop.width, "height": crop.height},
                "crop": crop,
                "expected_text": expected_text,
            }
        )
    return samples


def _select_blocks(source: Path, *, max_pages: int, blocks_per_source: int) -> list[dict[str, Any]]:
    prepared_pages = prepare_source_pages(
        source,
        pdf_dpi=160,
        detect_perspective=False,
        deskew=False,
        crop_margins=True,
        max_dimension=1800,
    )
    selected: list[dict[str, Any]] = []
    for prepared_page in prepared_pages[:max_pages]:
        page = segment_page(prepared_page, page_id=prepared_page.page_id, subject=Subject.UNKNOWN)
        text_like = [
            block
            for block in page.blocks
            if block.block_type not in {BlockType.IMAGE, BlockType.DIAGRAM, BlockType.TABLE}
            and block.bbox.width >= 20
            and block.bbox.height >= 12
        ]
        # Keep a mix of early/middle/later blocks instead of only the top of the page.
        if len(text_like) > blocks_per_source:
            step = max(1, len(text_like) // blocks_per_source)
            text_like = text_like[::step][:blocks_per_source]
        for block in text_like[:blocks_per_source]:
            crop = crop_block_image(prepared_page, block)
            if crop.width <= 0 or crop.height <= 0:
                continue
            selected.append(
                {
                    "source": str(source),
                    "page_id": prepared_page.page_id,
                    "page_number": prepared_page.page_number,
                    "block_id": block.block_id,
                    "bbox": {
                        "left": round(block.bbox.left, 1),
                        "top": round(block.bbox.top, 1),
                        "width": round(block.bbox.width, 1),
                        "height": round(block.bbox.height, 1),
                    },
                    "crop": crop,
                }
            )
            if len(selected) >= blocks_per_source:
                break
        if len(selected) >= blocks_per_source:
            break
    return selected


def _quality_score(row: dict[str, Any]) -> float:
    if row.get("error"):
        return 0.0
    text = str(row.get("text") or "").strip()
    if not text:
        return 0.0
    confidence = row.get("confidence")
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.6
    line_count = int(row.get("line_count") or 0)
    has_korean_or_digit = any(("가" <= ch <= "힣") or ch.isdigit() for ch in text)
    has_choice_marker = any(marker in text for marker in ("①", "②", "③", "④", "⑤"))
    return round(
        min(1.0, 0.45 * conf + 0.2 * min(len(text) / 80.0, 1.0) + 0.2 * min(line_count / 4.0, 1.0) + (0.1 if has_korean_or_digit else 0.0) + (0.05 if has_choice_marker else 0.0)),
        4,
    )


def _text_similarity(expected: str, actual: str) -> float | None:
    expected = " ".join((expected or "").split())
    actual = " ".join((actual or "").split())
    if not expected:
        return None
    return round(SequenceMatcher(None, expected, actual).ratio(), 4)


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for model in MODELS:
        model_rows = [row for row in rows if row["model"] == model]
        latencies = [float(row["latency_ms"]) for row in model_rows if isinstance(row.get("latency_ms"), (int, float))]
        nonempty = [row for row in model_rows if str(row.get("text") or "").strip()]
        errors = [row for row in model_rows if row.get("error")]
        scores = [float(row["quality_score"]) for row in model_rows]
        similarities = [
            float(row["similarity_to_expected"])
            for row in model_rows
            if isinstance(row.get("similarity_to_expected"), (int, float))
        ]
        summary[model] = {
            "call_count": len(model_rows),
            "error_count": len(errors),
            "nonempty_count": len(nonempty),
            "empty_count": len(model_rows) - len(nonempty),
            "avg_latency_ms": round(statistics.mean(latencies), 1) if latencies else None,
            "median_latency_ms": round(statistics.median(latencies), 1) if latencies else None,
            "p95_latency_ms": round(sorted(latencies)[min(len(latencies) - 1, int(len(latencies) * 0.95))], 1) if latencies else None,
            "avg_quality_score": round(statistics.mean(scores), 4) if scores else None,
            "avg_similarity_to_expected": round(statistics.mean(similarities), 4) if similarities else None,
            "avg_text_length": round(statistics.mean(len(str(row.get("text") or "")) for row in model_rows), 1) if model_rows else None,
            "avg_line_count": round(statistics.mean(int(row.get("line_count") or 0) for row in model_rows), 1) if model_rows else None,
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blocks-per-source", type=int, default=4)
    parser.add_argument("--max-sources", type=int, default=3)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--timeout-ms", type=int, default=30000)
    parser.add_argument("--sleep-ms", type=int, default=250)
    parser.add_argument("--out", default="tmp/gemini_ocr_model_benchmark.json")
    parser.add_argument("--synthetic", action="store_true", help="Use generated non-private test crops instead of local files")
    args = parser.parse_args()

    applied = _apply_saved_keys()
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        raise SystemExit("GEMINI_API_KEY is not available in env or .app_runtime/user_settings.json")

    sources: list[Path] = []
    if args.synthetic:
        samples = _build_synthetic_samples()
    else:
        sources = _source_candidates()[: max(1, args.max_sources)]
        if not sources:
            raise SystemExit("No benchmark sources found")
        samples: list[dict[str, Any]] = []
        for source in sources:
            samples.extend(
                _select_blocks(
                    source,
                    max_pages=max(1, args.max_pages),
                    blocks_per_source=max(1, args.blocks_per_source),
                )
            )

    rows: list[dict[str, Any]] = []
    for sample_index, sample in enumerate(samples, start=1):
        crop: Image.Image = sample["crop"]
        for model in MODELS:
            backend = GeminiOCRBackend(
                model=model,
                timeout_ms=args.timeout_ms,
                max_tokens=1024,
                max_retries=0,
            )
            started_at = time.perf_counter()
            try:
                result = backend.recognize(crop)
                latency_ms = int(round((time.perf_counter() - started_at) * 1000.0))
                text = (result.text or "").strip()
                row = {
                    "sample_index": sample_index,
                    "model": model,
                    "source": sample["source"],
                    "page_id": sample["page_id"],
                    "page_number": sample["page_number"],
                    "block_id": sample["block_id"],
                    "bbox": sample["bbox"],
                    "crop_size": [crop.width, crop.height],
                    "latency_ms": latency_ms,
                    "backend_latency_ms": result.metadata.get("backend_latency_ms"),
                    "text": text,
                    "text_length": len(text),
                    "line_count": len(result.lines),
                    "confidence": result.confidence,
                    "block_type_hint": result.metadata.get("block_type_hint"),
                    "model_used": result.metadata.get("model"),
                    "error": result.metadata.get("error"),
                }
            except Exception as exc:  # noqa: BLE001 - benchmark should keep going
                latency_ms = int(round((time.perf_counter() - started_at) * 1000.0))
                row = {
                    "sample_index": sample_index,
                    "model": model,
                    "source": sample["source"],
                    "page_id": sample["page_id"],
                    "page_number": sample["page_number"],
                    "block_id": sample["block_id"],
                    "bbox": sample["bbox"],
                    "crop_size": [crop.width, crop.height],
                    "latency_ms": latency_ms,
                    "text": "",
                    "text_length": 0,
                    "line_count": 0,
                    "confidence": None,
                    "block_type_hint": "",
                    "model_used": model,
                    "error": str(exc),
                }
            row["quality_score"] = _quality_score(row)
            row["expected_text"] = sample.get("expected_text", "")
            row["similarity_to_expected"] = _text_similarity(
                str(sample.get("expected_text") or ""),
                str(row.get("text") or ""),
            )
            row["text_preview"] = str(row.get("text") or "").replace("\n", " ")[:160]
            rows.append(row)
            if args.sleep_ms > 0:
                time.sleep(args.sleep_ms / 1000.0)

    payload = {
        "models": list(MODELS),
        "source_count": len(sources),
        "synthetic": bool(args.synthetic),
        "sample_count": len(samples),
        "env_key_source": applied.get("GEMINI_API_KEY") or "env",
        "summary": _summarize(rows),
        "samples": [
            {k: v for k, v in sample.items() if k != "crop"}
            for sample in samples
        ],
        "results": rows,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(out_path), "summary": payload["summary"], "sample_count": len(samples)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
