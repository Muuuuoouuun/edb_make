#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from structured_schema import Box

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


def build_ocr_backend(name: str = "auto") -> OCRBackend:
    normalized = name.lower()
    if normalized in {"none", "noop"}:
        return NoOcrBackend()
    if normalized in {"paddle", "paddleocr"}:
        return PaddleOCRBackend()
    if normalized == "tesseract":
        return TesseractOCRBackend()

    if PaddleOCR is not None:
        return PaddleOCRBackend()
    if pytesseract is not None:
        return TesseractOCRBackend()
    return NoOcrBackend()


def create_ocr_backend(name: str = "auto") -> OCRBackend:
    return build_ocr_backend(name)


OcrResult = OCRResult
