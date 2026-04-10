#!/usr/bin/env python3
from __future__ import annotations

import base64
import io
import json
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from structured_schema import Box

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"

_OCR_TOOL_SCHEMA = {
    "name": "extract_text_and_classify",
    "description": "Extract all text from the exam block image and classify its type.",
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Full extracted text from the image, preserving line breaks with \\n.",
            },
            "block_type": {
                "type": "string",
                "enum": ["stem", "choice", "figure", "formula", "title", "explanation", "unknown"],
                "description": (
                    "Classification of the block. Use 'stem' for problem body text, "
                    "'choice' for answer options (①②③④⑤ or ㄱㄴㄷ lists), "
                    "'figure' for diagrams/tables/images with little text, "
                    "'formula' for math equations, "
                    "'title' for problem number headings, "
                    "'explanation' for solution or commentary text."
                ),
            },
            "confidence": {
                "type": "number",
                "description": "Confidence in the OCR result, 0.0 to 1.0.",
            },
            "lines": {
                "type": "array",
                "description": "List of individual text lines recognized.",
                "items": {"type": "string"},
            },
        },
        "required": ["text", "block_type", "confidence", "lines"],
    },
}

try:
    from paddleocr import PaddleOCR  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    PaddleOCR = None

try:
    import pytesseract  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    pytesseract = None


@dataclass(slots=True)
class OCRLine:
    text: str
    confidence: float
    bbox: Box


@dataclass(slots=True)
class OCRResult:
    text: str
    confidence: float | None
    lines: list[OCRLine] = field(default_factory=list)
    backend_name: str = "none"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def engine(self) -> str:
        return self.backend_name


class OCRBackend:
    name = "base"

    @property
    def engine_name(self) -> str:
        return self.name

    def ocr_image(self, image: Image.Image) -> OCRResult:
        raise NotImplementedError

    def ocr_box(self, image: Image.Image, box: Box) -> OCRResult:
        crop = image.crop((int(box.left), int(box.top), int(box.right), int(box.bottom)))
        return self.ocr_image(crop)

    def recognize(self, image: Image.Image) -> OCRResult:
        return self.ocr_image(image)


class NoOcrBackend(OCRBackend):
    name = "none"

    def ocr_image(self, image: Image.Image) -> OCRResult:
        return OCRResult(text="", confidence=None, backend_name=self.name, metadata={"backend": self.name})


NoOpOCRBackend = NoOcrBackend


class PaddleOCRBackend(OCRBackend):
    name = "paddleocr"

    def __init__(self, *, lang: str = "korean", use_angle_cls: bool = True) -> None:
        if PaddleOCR is None:
            raise RuntimeError("paddleocr is not installed")
        self.engine = PaddleOCR(lang=lang, use_angle_cls=use_angle_cls, show_log=False)

    def ocr_image(self, image: Image.Image) -> OCRResult:
        try:
            raw = self.engine.ocr(image.convert("RGB"), cls=True)
        except Exception as exc:  # pragma: no cover - runtime fallback
            return OCRResult(
                text="",
                confidence=None,
                lines=[],
                backend_name=self.name,
                metadata={"backend": self.name, "error": str(exc)},
            )

        entries = raw[0] if raw else []
        lines: list[OCRLine] = []
        collected: list[str] = []
        confidences: list[float] = []

        for entry in entries or []:
            polygon, payload = entry
            text = str(payload[0]).strip()
            if not text:
                continue
            confidence = float(payload[1]) if len(payload) > 1 else 0.0
            xs = [point[0] for point in polygon]
            ys = [point[1] for point in polygon]
            lines.append(
                OCRLine(
                    text=text,
                    confidence=confidence,
                    bbox=Box.from_points(min(xs), min(ys), max(xs), max(ys)),
                )
            )
            collected.append(text)
            confidences.append(confidence)

        average_confidence = sum(confidences) / len(confidences) if confidences else None
        return OCRResult(
            text="\n".join(collected),
            confidence=average_confidence,
            lines=lines,
            backend_name=self.name,
            metadata={"backend": self.name},
        )


class TesseractOCRBackend(OCRBackend):
    name = "tesseract"

    def __init__(self, *, lang: str = "kor+eng") -> None:
        if pytesseract is None:
            raise RuntimeError("pytesseract is not installed")
        self.lang = lang

    def ocr_image(self, image: Image.Image) -> OCRResult:
        data = pytesseract.image_to_data(image, lang=self.lang, output_type=pytesseract.Output.DICT)
        lines: list[OCRLine] = []
        collected: list[str] = []
        confidences: list[float] = []

        for idx, text in enumerate(data.get("text", [])):
            cleaned = str(text).strip()
            if not cleaned:
                continue
            raw_conf = data["conf"][idx]
            confidence = float(raw_conf) / 100.0 if raw_conf not in {"-1", -1} else 0.0
            left = float(data["left"][idx])
            top = float(data["top"][idx])
            width = float(data["width"][idx])
            height = float(data["height"][idx])
            lines.append(
                OCRLine(
                    text=cleaned,
                    confidence=confidence,
                    bbox=Box(left=left, top=top, width=width, height=height),
                )
            )
            collected.append(cleaned)
            confidences.append(confidence)

        average_confidence = sum(confidences) / len(confidences) if confidences else None
        return OCRResult(
            text="\n".join(collected),
            confidence=average_confidence,
            lines=lines,
            backend_name=self.name,
            metadata={"backend": self.name},
        )


class ClaudeOCRBackend(OCRBackend):
    """OCR backend that uses Claude vision API for text extraction and block classification."""

    name = "claude"

    def __init__(
        self,
        *,
        model: str = "claude-haiku-4-5-20251001",
        api_key: str | None = None,
        timeout_ms: int = 15000,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable is required for ClaudeOCRBackend")
        self.timeout_s = timeout_ms / 1000.0

    def _image_to_base64(self, image: Image.Image) -> str:
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="JPEG", quality=90)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    def ocr_image(self, image: Image.Image) -> OCRResult:
        prompt = (
            "This is a cropped block from a Korean exam paper. "
            "Extract all visible text exactly as written (preserve Korean characters, math symbols, "
            "circled numbers ①②③④⑤, and special markers like ㄱ/ㄴ/ㄷ). "
            "Then classify the block type based on its content and layout. "
            "Return empty text and block_type='figure' if the block contains only a diagram or table with no significant text."
        )

        payload = {
            "model": self.model,
            "max_tokens": 512,
            "tools": [_OCR_TOOL_SCHEMA],
            "tool_choice": {"type": "tool", "name": "extract_text_and_classify"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": self._image_to_base64(image),
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            ANTHROPIC_MESSAGES_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_API_VERSION,
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                response_data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            return OCRResult(
                text="",
                confidence=None,
                lines=[],
                backend_name=self.name,
                metadata={"backend": self.name, "error": str(exc)},
            )

        # Extract tool_use result
        tool_result: dict[str, Any] = {}
        for content_block in response_data.get("content", []):
            if content_block.get("type") == "tool_use":
                tool_result = content_block.get("input", {})
                break

        if not tool_result:
            return OCRResult(
                text="",
                confidence=None,
                lines=[],
                backend_name=self.name,
                metadata={"backend": self.name, "error": "no tool_use in response"},
            )

        raw_text = str(tool_result.get("text", "")).strip()
        raw_lines = tool_result.get("lines", [])
        confidence = float(tool_result.get("confidence", 0.8))
        block_type_hint = str(tool_result.get("block_type", "unknown"))

        # Build OCRLine list from returned lines (no bbox info from Claude, use dummy bbox)
        lines: list[OCRLine] = []
        image_h = float(image.height) or 1.0
        image_w = float(image.width) or 1.0
        for idx, line_text in enumerate(raw_lines):
            cleaned = str(line_text).strip()
            if not cleaned:
                continue
            line_h = image_h / max(len(raw_lines), 1)
            lines.append(
                OCRLine(
                    text=cleaned,
                    confidence=confidence,
                    bbox=Box(left=0.0, top=idx * line_h, width=image_w, height=line_h),
                )
            )

        return OCRResult(
            text=raw_text,
            confidence=confidence,
            lines=lines,
            backend_name=self.name,
            metadata={"backend": self.name, "block_type_hint": block_type_hint},
        )


def build_ocr_backend(name: str = "auto") -> OCRBackend:
    normalized = name.lower()
    if normalized in {"none", "noop"}:
        return NoOcrBackend()
    if normalized in {"paddle", "paddleocr"}:
        return PaddleOCRBackend()
    if normalized == "tesseract":
        return TesseractOCRBackend()
    if normalized in {"claude", "anthropic"}:
        return ClaudeOCRBackend()

    if PaddleOCR is not None:
        return PaddleOCRBackend()
    if pytesseract is not None:
        return TesseractOCRBackend()
    return NoOcrBackend()


def create_ocr_backend(name: str = "auto") -> OCRBackend:
    return build_ocr_backend(name)


OcrResult = OCRResult
