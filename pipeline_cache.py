#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from ocr_backend import OCRLine, OCRResult
from structured_schema import BlockType, Box, ContentBlock, PageModel


def _sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _image_hash(image: Image.Image) -> str:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=True)
    return hashlib.sha1(buffer.getvalue()).hexdigest()


def _safe_slug(value: str) -> str:
    slug = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value.strip())
    return slug or "default"


def default_pipeline_cache_dir(source_path: str | Path | None) -> Path:
    if source_path:
        return Path(source_path).resolve().parent / ".pipeline_cache"
    return Path.cwd() / ".pipeline_cache"


def _serialize_box(box: Box) -> dict[str, float]:
    return {
        "left": float(box.left),
        "top": float(box.top),
        "width": float(box.width),
        "height": float(box.height),
    }


def _deserialize_box(payload: dict[str, Any]) -> Box:
    return Box(
        left=float(payload.get("left", 0.0)),
        top=float(payload.get("top", 0.0)),
        width=float(payload.get("width", 0.0)),
        height=float(payload.get("height", 0.0)),
    )


def _serialize_ocr_result(result: OCRResult) -> dict[str, Any]:
    return {
        "text": result.text,
        "confidence": result.confidence,
        "backend_name": result.backend_name,
        "metadata": dict(result.metadata),
        "lines": [
            {
                "text": line.text,
                "confidence": line.confidence,
                "bbox": _serialize_box(line.bbox),
            }
            for line in result.lines
        ],
    }


def _deserialize_ocr_result(payload: dict[str, Any]) -> OCRResult:
    lines = [
        OCRLine(
            text=str(line.get("text", "")),
            confidence=float(line.get("confidence", 0.0) or 0.0),
            bbox=_deserialize_box(dict(line.get("bbox") or {})),
        )
        for line in payload.get("lines") or []
        if isinstance(line, dict)
    ]
    metadata = dict(payload.get("metadata") or {})
    metadata["cache_hit"] = True
    return OCRResult(
        text=str(payload.get("text", "")),
        confidence=float(payload["confidence"]) if payload.get("confidence") is not None else None,
        lines=lines,
        backend_name=str(payload.get("backend_name") or "none"),
        metadata=metadata,
    )


def _page_signature(page: PageModel) -> str:
    block_payload = []
    for block in page.blocks:
        block_payload.append(
            {
                "block_id": block.block_id,
                "block_type": block.block_type.value,
                "text": block.text or "",
                "confidence": round(block.confidence, 4) if block.confidence is not None else None,
                "bbox": {
                    "left": round(block.bbox.left, 1),
                    "top": round(block.bbox.top, 1),
                    "width": round(block.bbox.width, 1),
                    "height": round(block.bbox.height, 1),
                },
                "meta": {
                    key: block.metadata.get(key)
                    for key in (
                        "ocr_backend",
                        "display_title",
                        "column_index",
                        "question_band_index",
                        "fallback_reason",
                    )
                    if key in block.metadata
                },
            }
        )
    payload = {
        "page_id": page.page_id,
        "width_px": page.width_px,
        "height_px": page.height_px,
        "ocr_mode": page.metadata.get("ocr_mode"),
        "blocks": block_payload,
    }
    return _sha1_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


class PipelineCache:
    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_source(cls, source_path: str | Path | None) -> "PipelineCache":
        return cls(default_pipeline_cache_dir(source_path))

    def _ocr_cache_path(self, image: Image.Image, backend_name: str) -> Path:
        image_key = _image_hash(image)
        return self.root_dir / "ocr" / _safe_slug(backend_name) / f"{image_key}.json"

    def load_ocr_result(self, image: Image.Image, *, backend_name: str) -> OCRResult | None:
        path = self._ocr_cache_path(image, backend_name)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        result = _deserialize_ocr_result(payload)
        result.metadata["cache_path"] = str(path)
        return result

    def save_ocr_result(self, image: Image.Image, result: OCRResult, *, backend_name: str) -> Path:
        path = self._ocr_cache_path(image, backend_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = _serialize_ocr_result(result)
        payload["cached_backend_name"] = backend_name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _ai_cache_path(
        self,
        *,
        page: PageModel,
        provider: str,
        model: str,
        trigger_reasons: list[str],
    ) -> Path:
        signature_payload = {
            "provider": provider,
            "model": model,
            "trigger_reasons": list(trigger_reasons),
            "page_signature": _page_signature(page),
        }
        signature = _sha1_text(json.dumps(signature_payload, ensure_ascii=False, sort_keys=True))
        return self.root_dir / "ai_repairs" / _safe_slug(provider) / _safe_slug(model) / f"{signature}.json"

    def load_ai_repair(
        self,
        *,
        page: PageModel,
        provider: str,
        model: str,
        trigger_reasons: list[str],
    ) -> tuple[dict[str, Any], str | None] | None:
        path = self._ai_cache_path(page=page, provider=provider, model=model, trigger_reasons=trigger_reasons)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        repair_payload = payload.get("repair_payload")
        if not isinstance(repair_payload, dict):
            return None
        return dict(repair_payload), payload.get("response_id")

    def save_ai_repair(
        self,
        *,
        page: PageModel,
        provider: str,
        model: str,
        trigger_reasons: list[str],
        repair_payload: dict[str, Any],
        response_id: str | None,
    ) -> Path:
        path = self._ai_cache_path(page=page, provider=provider, model=model, trigger_reasons=trigger_reasons)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "provider": provider,
            "model": model,
            "trigger_reasons": list(trigger_reasons),
            "response_id": response_id,
            "repair_payload": repair_payload,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def summarize_ocr_cache(blocks: list[ContentBlock]) -> dict[str, int]:
    eligible_blocks = [block for block in blocks if block.block_type not in {BlockType.IMAGE, BlockType.DIAGRAM, BlockType.TABLE}]
    return {
        "eligible_block_count": len(eligible_blocks),
        "ocr_cache_hit_count": sum(1 for block in blocks if block.metadata.get("ocr_cache_hit")),
        "ocr_cache_miss_count": sum(1 for block in blocks if block.metadata.get("ocr_cache_miss")),
    }
