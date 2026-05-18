#!/usr/bin/env python3
from __future__ import annotations

import base64
import io
import json
import os
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from structured_schema import Box

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Gemini's responseSchema follows an OpenAPI 3.0 subset: no `additionalProperties`,
# no `$ref`, no `oneOf`. Keep the shape simple and rely on `required` instead.
_OCR_RESPONSE_SCHEMA = {
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
                "Classification of the block. 'stem' for problem body text, "
                "'choice' for answer options (①②③④⑤ or ㄱㄴㄷ lists), "
                "'figure' for diagrams/tables/images with little text, "
                "'formula' for math equations, 'title' for problem-number headings, "
                "'explanation' for solution/commentary text."
            ),
        },
        "confidence": {
            "type": "number",
            "description": "Confidence in the OCR result, 0.0 to 1.0.",
        },
        "lines": {
            "type": "array",
            "description": "Individual text lines recognized, in reading order.",
            "items": {"type": "string"},
        },
    },
    "required": ["text", "block_type", "confidence", "lines"],
}

try:
    from paddleocr import PaddleOCR  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    PaddleOCR = None

try:
    import pytesseract  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    pytesseract = None


# Tiny crops (height below this) get upscaled before OCR. Korean OCR engines
# need ~28–40 px x-height to hit decent accuracy; cropped exam blocks are
# often half that.
_OCR_MIN_HEIGHT_PX = 64
_OCR_UPSCALE_CAP = 3.0
_OCR_CROP_PADDING_PX = 6


def _prep_crop_for_ocr(image: Image.Image) -> Image.Image:
    """Pad and upscale a block crop so per-character pixels are within the
    range Korean OCR engines expect. Returns a new image; the input is not
    mutated."""
    if image.width <= 0 or image.height <= 0:
        return image

    padded = image
    pad = _OCR_CROP_PADDING_PX
    if pad > 0:
        new_w = image.width + pad * 2
        new_h = image.height + pad * 2
        padded = Image.new("RGB", (new_w, new_h), (255, 255, 255))
        padded.paste(image.convert("RGB"), (pad, pad))

    if padded.height >= _OCR_MIN_HEIGHT_PX:
        return padded

    scale = min(_OCR_UPSCALE_CAP, _OCR_MIN_HEIGHT_PX / max(padded.height, 1))
    if scale <= 1.0:
        return padded
    new_size = (int(round(padded.width * scale)), int(round(padded.height * scale)))
    return padded.resize(new_size, Image.Resampling.LANCZOS)


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


def _line_confidence_summary(lines: list[OCRLine]) -> dict[str, Any]:
    confidences = [line.confidence for line in lines if line.confidence is not None]
    if not confidences:
        return {
            "line_confidence_count": 0,
            "line_confidence_mean": None,
            "line_confidence_min": None,
            "line_confidence_max": None,
        }
    return {
        "line_confidence_count": len(confidences),
        "line_confidence_mean": sum(confidences) / len(confidences),
        "line_confidence_min": min(confidences),
        "line_confidence_max": max(confidences),
    }


def _build_ocr_metadata(
    *,
    backend: str,
    started_at: float,
    text: str,
    confidence: float | None,
    lines: list[OCRLine],
    extra: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    normalized_text = text.strip()
    diagnostics: dict[str, Any] = {
        "backend": backend,
        "backend_latency_ms": int(max(0.0, (time.perf_counter() - started_at) * 1000.0)),
        "line_count": len(lines),
        "empty_text": not bool(normalized_text),
        "text_length": len(normalized_text),
        "confidence_available": confidence is not None,
        "result_confidence": confidence,
    }
    diagnostics.update(_line_confidence_summary(lines))
    if error:
        diagnostics["error"] = error
    if extra:
        diagnostics.update(extra)
    return diagnostics


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
        started_at = time.perf_counter()
        return OCRResult(
            text="",
            confidence=None,
            backend_name=self.name,
            metadata=_build_ocr_metadata(
                backend=self.name,
                started_at=started_at,
                text="",
                confidence=None,
                lines=[],
            ),
        )


NoOpOCRBackend = NoOcrBackend


class PaddleOCRBackend(OCRBackend):
    name = "paddleocr"

    def __init__(self, *, lang: str = "korean", use_angle_cls: bool = True) -> None:
        if PaddleOCR is None:
            raise RuntimeError("paddleocr is not installed")
        self.engine = PaddleOCR(lang=lang, use_angle_cls=use_angle_cls, show_log=False)

    def ocr_image(self, image: Image.Image) -> OCRResult:
        started_at = time.perf_counter()
        prepped = _prep_crop_for_ocr(image)
        try:
            raw = self.engine.ocr(prepped.convert("RGB"), cls=True)
        except Exception as exc:  # pragma: no cover - runtime fallback
            return OCRResult(
                text="",
                confidence=None,
                lines=[],
                backend_name=self.name,
                metadata=_build_ocr_metadata(
                    backend=self.name,
                    started_at=started_at,
                    text="",
                    confidence=None,
                    lines=[],
                    error=str(exc),
                ),
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
            metadata=_build_ocr_metadata(
                backend=self.name,
                started_at=started_at,
                text="\n".join(collected),
                confidence=average_confidence,
                lines=lines,
            ),
        )


class TesseractOCRBackend(OCRBackend):
    name = "tesseract"

    def __init__(self, *, lang: str = "kor+eng") -> None:
        if pytesseract is None:
            raise RuntimeError("pytesseract is not installed")
        self.lang = lang

    def ocr_image(self, image: Image.Image) -> OCRResult:
        started_at = time.perf_counter()
        prepped = _prep_crop_for_ocr(image)
        data = pytesseract.image_to_data(prepped, lang=self.lang, output_type=pytesseract.Output.DICT)
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
            metadata=_build_ocr_metadata(
                backend=self.name,
                started_at=started_at,
                text="\n".join(collected),
                confidence=average_confidence,
                lines=lines,
            ),
        )


class GeminiOCRBackend(OCRBackend):
    """OCR backend that uses Google Gemini vision API for text extraction and block classification."""

    name = "gemini"

    def __init__(
        self,
        *,
        model: str = "gemini-2.5-flash",
        api_key: str | None = None,
        timeout_ms: int = 15000,
        max_tokens: int = 1024,
        max_retries: int = 1,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY environment variable is required for GeminiOCRBackend")
        self.timeout_s = timeout_ms / 1000.0
        self.max_tokens = max_tokens
        self.max_retries = max(0, max_retries)

    def _encode_image(self, image: Image.Image) -> tuple[str, str]:
        """Pick JPEG for large crops (smaller payload), PNG for small crops
        (lossless for tiny text). Returns (media_type, base64-data)."""
        rgb = image.convert("RGB")
        if rgb.height < 200 or rgb.width < 200:
            buf = io.BytesIO()
            rgb.save(buf, format="PNG", optimize=True)
            return "image/png", base64.b64encode(buf.getvalue()).decode("ascii")
        buf = io.BytesIO()
        rgb.save(buf, format="JPEG", quality=92, optimize=True)
        return "image/jpeg", base64.b64encode(buf.getvalue()).decode("ascii")

    def _image_to_base64(self, image: Image.Image) -> str:
        # Kept for backward compatibility with external callers.
        _, data = self._encode_image(image)
        return data

    def ocr_image(self, image: Image.Image) -> OCRResult:
        started_at = time.perf_counter()
        prompt = (
            "This is a cropped block from a Korean exam paper. "
            "Extract ALL visible text exactly as written — do not summarize, paraphrase, or skip anything. "
            "Preserve Korean characters, math symbols, circled numbers ①②③④⑤, and ㄱ/ㄴ/ㄷ markers. "
            "Use \\n between visual lines. Keep digits, parentheses, and punctuation as-is. "
            "Classify the block by content:\n"
            "  - 'title' : a problem-number heading like '1.', '문제 3', '[4]'\n"
            "  - 'stem'  : main question body text\n"
            "  - 'choice': stand-alone answer-option block (① ② ③ ④ ⑤ or A–E)\n"
            "  - 'formula': a math equation or expression line\n"
            "  - 'figure': diagram/table/photo with little or no text\n"
            "  - 'explanation': solution or commentary text\n"
            "Set confidence based on how legible the text is (0.0–1.0). "
            "If the block is mostly figure with no useful text, return text='' and block_type='figure'."
        )

        prepped = _prep_crop_for_ocr(image)
        media_type, image_data = self._encode_image(prepped)
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"inline_data": {"mime_type": media_type, "data": image_data}},
                        {"text": prompt},
                    ],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": _OCR_RESPONSE_SCHEMA,
                "maxOutputTokens": self.max_tokens,
                "temperature": 0.0,
            },
        }
        url = f"{GEMINI_API_BASE}/{self.model}:generateContent?key={self.api_key}"

        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}

        last_exc: Exception | None = None
        response_data: dict[str, Any] | None = None
        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                time.sleep(min(1.5 * attempt, 4.0))
            try:
                req = urllib.request.Request(url, data=body, method="POST", headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    response_data = json.loads(resp.read().decode("utf-8"))
                break
            except Exception as exc:  # network / API errors → retry
                last_exc = exc

        if response_data is None:
            return OCRResult(
                text="",
                confidence=None,
                lines=[],
                backend_name=self.name,
                metadata=_build_ocr_metadata(
                    backend=self.name,
                    started_at=started_at,
                    text="",
                    confidence=None,
                    lines=[],
                    error=str(last_exc) if last_exc else "no response",
                    extra={"retry_count": self.max_retries},
                ),
            )

        # Gemini returns structured output as a JSON string inside the first
        # text part of candidate[0].
        json_text = _gemini_extract_text(response_data)
        if not json_text:
            finish_reason = _gemini_finish_reason(response_data)
            return OCRResult(
                text="",
                confidence=None,
                lines=[],
                backend_name=self.name,
                metadata=_build_ocr_metadata(
                    backend=self.name,
                    started_at=started_at,
                    text="",
                    confidence=None,
                    lines=[],
                    error=f"no text in response (finish={finish_reason})",
                ),
            )

        try:
            parsed: dict[str, Any] = json.loads(json_text)
        except json.JSONDecodeError as exc:
            return OCRResult(
                text="",
                confidence=None,
                lines=[],
                backend_name=self.name,
                metadata=_build_ocr_metadata(
                    backend=self.name,
                    started_at=started_at,
                    text="",
                    confidence=None,
                    lines=[],
                    error=f"json decode failed: {exc}",
                ),
            )

        raw_text = str(parsed.get("text", "")).strip()
        raw_lines = parsed.get("lines") or []
        confidence = float(parsed.get("confidence", 0.8))
        block_type_hint = str(parsed.get("block_type", "unknown"))

        # Build OCRLine list (Gemini does not return per-line bboxes; use even
        # vertical splits of the original crop so downstream consumers still
        # have a usable coordinate hint).
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
            metadata=_build_ocr_metadata(
                backend=self.name,
                started_at=started_at,
                text=raw_text,
                confidence=confidence,
                lines=lines,
                extra={"block_type_hint": block_type_hint, "model": self.model},
            ),
        )


def _gemini_extract_text(response: dict[str, Any]) -> str:
    """Return the concatenated text from the first candidate's parts, or ''."""
    candidates = response.get("candidates") or []
    if not candidates:
        return ""
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    collected: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            collected.append(text)
    return "".join(collected)


def _gemini_finish_reason(response: dict[str, Any]) -> str:
    candidates = response.get("candidates") or []
    if not candidates:
        return "no_candidates"
    return str(candidates[0].get("finishReason") or "unknown")


def build_ocr_backend(name: str = "auto") -> OCRBackend:
    normalized = name.lower()
    if normalized in {"none", "noop"}:
        return NoOcrBackend()
    if normalized in {"paddle", "paddleocr"}:
        return PaddleOCRBackend()
    if normalized == "tesseract":
        return TesseractOCRBackend()
    if normalized in {"gemini", "google", "claude", "anthropic"}:
        # 'claude'/'anthropic' kept as aliases for transitional configs; both
        # resolve to the Gemini backend now.
        return GeminiOCRBackend()

    if PaddleOCR is not None:
        return PaddleOCRBackend()
    if pytesseract is not None:
        return TesseractOCRBackend()
    return NoOcrBackend()


def create_ocr_backend(name: str = "auto") -> OCRBackend:
    return build_ocr_backend(name)


OcrResult = OCRResult
