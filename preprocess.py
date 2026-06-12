#!/usr/bin/env python3
from __future__ import annotations

import math
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

try:
    import fitz  # type: ignore
except ImportError:  # pragma: no cover
    fitz = None

try:
    import cv2  # type: ignore
except ImportError:  # pragma: no cover
    cv2 = None

try:
    import numpy as np  # type: ignore
except ImportError:  # pragma: no cover
    np = None


SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
HWP_DOCUMENT_EXTENSIONS = {".hwp", ".hwpx"}


@dataclass(slots=True)
class PreprocessOptions:
    dpi: int = 160
    enable_perspective: bool = True
    enable_deskew: bool = True
    enable_margin_crop: bool = True
    max_dimension: int | None = None


@dataclass(slots=True)
class PreparedPage:
    page_id: str
    source_path: str
    page_number: int
    image: Image.Image
    original_size: tuple[int, int]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size


@dataclass(slots=True)
class NormalizedPageImage:
    page_id: str
    source_path: str
    normalized_path: str
    page_index: int
    width_px: int
    height_px: int
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def image(self) -> Image.Image:
        return Image.open(self.normalized_path).convert("RGB")


def _require_cv2_numpy() -> None:
    if cv2 is None or np is None:
        raise RuntimeError("opencv-python and numpy are required for this preprocessing step")


def _pil_to_bgr(image: Image.Image):
    _require_cv2_numpy()
    rgb = image.convert("RGB")
    return cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)


def _bgr_to_pil(image_bgr) -> Image.Image:
    _require_cv2_numpy()
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


_EXTERNAL_PYMUPDF_RENDER_SCRIPT = r"""
import json
import re
import sys
from pathlib import Path

import fitz


def extract_pdf_problem_markers(page, scale):
    markers = []
    try:
        data = page.get_text("dict")
    except Exception:
        return markers
    for block in data.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        for line in block.get("lines") or []:
            if not isinstance(line, dict):
                continue
            text = "".join(
                str(span.get("text") or "")
                for span in line.get("spans") or []
                if isinstance(span, dict)
            ).strip()
            match = re.match(r"^([1-9][0-9]?)\.\s*", text)
            if not match:
                continue
            bbox = line.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            number = int(match.group(1))
            left, top, right, bottom = [float(value) * scale for value in bbox]
            markers.append(
                {
                    "number": number,
                    "text": text[:120],
                    "bbox": {
                        "left": left,
                        "top": top,
                        "right": right,
                        "bottom": bottom,
                        "width": max(0.0, right - left),
                        "height": max(0.0, bottom - top),
                    },
                }
            )
    return markers


source_path = Path(sys.argv[1])
target_dir = Path(sys.argv[2])
dpi = int(sys.argv[3])
target_dir.mkdir(parents=True, exist_ok=True)
scale = dpi / 72.0
matrix = fitz.Matrix(scale, scale)
doc = fitz.open(source_path)
pages = []
try:
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        out_path = target_dir / f"{source_path.stem}_page_{page_index + 1:03d}.png"
        pix.save(out_path.as_posix())
        pages.append(
            {
                "page_id": f"{source_path.stem}-page-{page_index + 1:03d}",
                "source_path": str(source_path),
                "normalized_path": str(out_path),
                "page_index": page_index,
                "width_px": pix.width,
                "height_px": pix.height,
                "metadata": {
                    "source_type": "pdf",
                    "dpi": dpi,
                    "pdf_page_width_pt": float(page.rect.width),
                    "pdf_page_height_pt": float(page.rect.height),
                    "pdf_problem_markers": extract_pdf_problem_markers(page, scale),
                },
            }
        )
finally:
    doc.close()
print(json.dumps(pages, ensure_ascii=True))
"""


def _iter_external_pymupdf_python_candidates() -> list[Path]:
    raw_candidates: list[str | Path | None] = [
        os.environ.get("EDB_PYMUPDF_PYTHON"),
        sys.executable,
        Path(__file__).resolve().parent / ".venv" / "Scripts" / "python.exe",
        Path.home() / "AppData" / "Local" / "Python" / "bin" / "python.exe",
        shutil.which("python"),
        shutil.which("python3"),
        shutil.which("py"),
    ]
    candidates: list[Path] = []
    seen: set[str] = set()
    for raw_candidate in raw_candidates:
        if not raw_candidate:
            continue
        candidate = Path(raw_candidate)
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


def _render_pdf_pages_with_external_pymupdf(
    source_path: Path,
    target_dir: Path,
    *,
    dpi: int,
) -> list[NormalizedPageImage]:
    errors: list[str] = []
    for python_exe in _iter_external_pymupdf_python_candidates():
        command = [
            str(python_exe),
            "-c",
            _EXTERNAL_PYMUPDF_RENDER_SCRIPT,
            str(source_path),
            str(target_dir),
            str(dpi),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{python_exe}: {exc}")
            continue
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip().replace("\n", " ")[:240]
            errors.append(f"{python_exe}: exit {completed.returncode} {detail}")
            continue
        try:
            payload = json.loads(completed.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            errors.append(f"{python_exe}: invalid renderer output {exc}")
            continue

        pages: list[NormalizedPageImage] = []
        for item in payload:
            metadata = dict(item.get("metadata") or {})
            metadata["pdf_renderer"] = "external_pymupdf"
            metadata["pdf_renderer_python"] = str(python_exe)
            pages.append(
                NormalizedPageImage(
                    page_id=str(item["page_id"]),
                    source_path=str(item["source_path"]),
                    normalized_path=str(item["normalized_path"]),
                    page_index=int(item["page_index"]),
                    width_px=int(item["width_px"]),
                    height_px=int(item["height_px"]),
                    metadata=metadata,
                )
            )
        return pages

    detail = "; ".join(errors) if errors else "no Python candidates found"
    if len(detail) > 1200:
        detail = f"{detail[:1200]}..."
    raise RuntimeError(f"PyMuPDF is required to render PDF pages ({detail})")


def render_pdf_pages(source: str | Path, output_dir: str | Path, dpi: int = 160) -> list[NormalizedPageImage]:
    source_path = Path(source)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    if fitz is None:
        return _render_pdf_pages_with_external_pymupdf(source_path, target_dir, dpi=dpi)

    doc = fitz.open(source_path)
    pages: list[NormalizedPageImage] = []
    try:
        scale = dpi / 72.0
        matrix = fitz.Matrix(scale, scale)
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            out_path = target_dir / f"{source_path.stem}_page_{page_index + 1:03d}.png"
            pix.save(out_path.as_posix())
            pages.append(
                NormalizedPageImage(
                    page_id=f"{source_path.stem}-page-{page_index + 1:03d}",
                    source_path=str(source_path),
                    normalized_path=str(out_path),
                    page_index=page_index,
                    width_px=pix.width,
                    height_px=pix.height,
                    metadata={
                        "source_type": "pdf",
                        "dpi": dpi,
                        "pdf_page_width_pt": float(page.rect.width),
                        "pdf_page_height_pt": float(page.rect.height),
                        "pdf_problem_markers": _extract_pdf_problem_markers(page, scale),
                    },
                )
            )
    finally:
        doc.close()
    return pages


def _iter_hwp_pdf_converter_commands() -> list[list[str]]:
    candidates: list[list[str]] = []
    for executable in ("soffice", "libreoffice"):
        resolved = shutil.which(executable)
        if resolved:
            candidates.append([resolved, "--headless"])

    mac_soffice = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    if mac_soffice.exists():
        candidates.append([str(mac_soffice), "--headless"])

    hwp5pdf = shutil.which("hwp5pdf")
    if hwp5pdf:
        candidates.append([hwp5pdf])

    seen: set[str] = set()
    unique: list[list[str]] = []
    for command in candidates:
        key = "\0".join(command)
        if key in seen:
            continue
        seen.add(key)
        unique.append(command)
    return unique


def _iter_hwp_hwpx_converter_commands() -> list[list[str]]:
    hwpilot = shutil.which("hwpilot")
    return [[hwpilot]] if hwpilot else []


def _hwp_pdf_candidates(output_dir: Path, source_path: Path) -> list[Path]:
    expected = output_dir / f"{source_path.stem}.pdf"
    candidates = [expected]
    for path in sorted(
        output_dir.glob("*.pdf"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):
        if path not in candidates:
            candidates.append(path)
    return candidates


def _file_sha1(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _hwp_conversion_cache_path(target_dir: Path, source_path: Path) -> Path:
    return target_dir / f".{source_path.stem}.conversion.json"


def _load_cached_hwp_pdf(source_path: Path, target_dir: Path) -> Path | None:
    cache_path = _hwp_conversion_cache_path(target_dir, source_path)
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    try:
        source_sha1 = _file_sha1(source_path)
    except OSError:
        return None
    if payload.get("source_sha1") != source_sha1:
        return None
    if payload.get("source_suffix") != source_path.suffix.lower():
        return None

    pdf_name = payload.get("pdf_name")
    if not isinstance(pdf_name, str) or not pdf_name:
        return None
    pdf_path = target_dir / pdf_name
    if not pdf_path.exists() or pdf_path.stat().st_size <= 0:
        return None
    return pdf_path


def _save_hwp_pdf_cache(source_path: Path, target_dir: Path, pdf_path: Path) -> None:
    try:
        source_sha1 = _file_sha1(source_path)
        pdf_name = pdf_path.relative_to(target_dir).as_posix()
    except (OSError, ValueError):
        return
    payload = {
        "version": 1,
        "source_name": source_path.name,
        "source_suffix": source_path.suffix.lower(),
        "source_sha1": source_sha1,
        "pdf_name": pdf_name,
        "pdf_size": pdf_path.stat().st_size,
    }
    _hwp_conversion_cache_path(target_dir, source_path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _run_hwp_pdf_converter_commands(
    source_path: Path,
    target_dir: Path,
    commands: list[list[str]],
    timeout_seconds: int,
) -> tuple[Path | None, list[str]]:
    errors: list[str] = []
    for command_prefix in commands:
        tool_name = Path(command_prefix[0]).name.lower()
        expected_pdf = target_dir / f"{source_path.stem}.pdf"
        if "hwp5pdf" in tool_name:
            command = [*command_prefix, str(source_path), str(expected_pdf)]
        else:
            command = [
                *command_prefix,
                "--convert-to",
                "pdf",
                "--outdir",
                str(target_dir),
                str(source_path),
            ]
        before_mtime_ns = {
            candidate: candidate.stat().st_mtime_ns
            for candidate in _hwp_pdf_candidates(target_dir, source_path)
            if candidate.exists()
        }
        try:
            result = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{command[0]}: {exc}")
            continue

        for pdf_path in _hwp_pdf_candidates(target_dir, source_path):
            if not pdf_path.exists():
                continue
            previous_mtime_ns = before_mtime_ns.get(pdf_path)
            if previous_mtime_ns is None or pdf_path.stat().st_mtime_ns != previous_mtime_ns:
                return pdf_path, errors
        output = " ".join(
            part for part in [result.stdout.strip(), result.stderr.strip()] if part
        )
        errors.append(
            f"{command[0]} exited {result.returncode}: {output or 'no PDF output'}"
        )
    return None, errors


def _convert_hwp_to_hwpx_with_hwpilot(
    source_path: Path,
    output_dir: Path,
    timeout_seconds: int,
) -> tuple[Path | None, list[str]]:
    if source_path.suffix.lower() != ".hwp":
        return None, []

    commands = _iter_hwp_hwpx_converter_commands()
    if not commands:
        return None, []

    output_dir.mkdir(parents=True, exist_ok=True)
    target_path = output_dir / f"{source_path.stem}.hwpx"
    errors: list[str] = []
    for command_prefix in commands:
        command = [*command_prefix, "convert", str(source_path), str(target_path)]
        try:
            result = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{command[0]}: {exc}")
            continue

        if target_path.exists():
            return target_path, errors
        output = " ".join(
            part for part in [result.stdout.strip(), result.stderr.strip()] if part
        )
        errors.append(f"{command[0]} exited {result.returncode}: {output or 'no HWPX output'}")
    return None, errors


def convert_hwp_to_pdf(source: str | Path, output_dir: str | Path, timeout_seconds: int = 90) -> Path:
    source_path = Path(source)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    cached_pdf = _load_cached_hwp_pdf(source_path, target_dir)
    if cached_pdf:
        return cached_pdf

    commands = _iter_hwp_pdf_converter_commands()
    if not commands:
        raise ValueError(
            "HWP/HWPX input requires a local converter such as LibreOffice with HWP support "
            "or hwp5pdf. HWPilot can only help normalize HWP to HWPX and still needs a PDF "
            "converter. Install one, or convert the HWP file to PDF first."
        )

    pdf_path, errors = _run_hwp_pdf_converter_commands(source_path, target_dir, commands, timeout_seconds)
    if pdf_path:
        _save_hwp_pdf_cache(source_path, target_dir, pdf_path)
        return pdf_path

    hwpx_path, hwpilot_errors = _convert_hwp_to_hwpx_with_hwpilot(source_path, target_dir / "_hwpilot", timeout_seconds)
    errors.extend(hwpilot_errors)
    if hwpx_path:
        pdf_path, hwpx_pdf_errors = _run_hwp_pdf_converter_commands(hwpx_path, target_dir, commands, timeout_seconds)
        if pdf_path:
            _save_hwp_pdf_cache(source_path, target_dir, pdf_path)
            return pdf_path
        errors.extend(f"after HWPilot bridge: {error}" for error in hwpx_pdf_errors)

    detail = "; ".join(errors) if errors else "no converter produced a PDF"
    raise ValueError(
        "HWP/HWPX conversion failed. Install LibreOffice with HWP support, "
        f"or convert the HWP file to PDF first. Details: {detail}"
    )


def _extract_pdf_problem_markers(page: Any, scale: float) -> list[dict[str, Any]]:
    """Extract problem-number line anchors from a PDF text layer.

    Coordinates are returned in rendered-pixel space so downstream image
    segmentation can create page crops without calling OCR.
    """
    import re

    markers: list[dict[str, Any]] = []
    try:
        data = page.get_text("dict")
    except Exception:
        return markers

    for block in data.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        for line in block.get("lines") or []:
            if not isinstance(line, dict):
                continue
            text = "".join(
                str(span.get("text") or "")
                for span in line.get("spans") or []
                if isinstance(span, dict)
            ).strip()
            match = re.match(r"^([1-9][0-9]?)\.\s*", text)
            if not match:
                continue
            number = int(match.group(1))
            if not 1 <= number <= 99:
                continue
            bbox = line.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            left, top, right, bottom = [float(value) * scale for value in bbox]
            markers.append(
                {
                    "number": number,
                    "text": text[:120],
                    "bbox": {
                        "left": left,
                        "top": top,
                        "right": right,
                        "bottom": bottom,
                        "width": max(0.0, right - left),
                        "height": max(0.0, bottom - top),
                    },
                }
            )

    return markers


def load_image(source: str | Path) -> Image.Image:
    return Image.open(source).convert("RGB")


def _crop_uniform_margin_with_box(
    image: Image.Image,
    background_threshold: int = 245,
    padding: int = 12,
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    gray = ImageOps.grayscale(image)
    mask = gray.point(lambda px: 255 if px < background_threshold else 0)
    bbox = mask.getbbox()
    if bbox is None:
        return image, (0, 0, image.width, image.height)
    left = max(0, bbox[0] - padding)
    top = max(0, bbox[1] - padding)
    right = min(image.width, bbox[2] + padding)
    bottom = min(image.height, bbox[3] + padding)
    return image.crop((left, top, right, bottom)), (left, top, right, bottom)


def crop_uniform_margin(image: Image.Image, background_threshold: int = 245, padding: int = 12) -> Image.Image:
    cropped, _ = _crop_uniform_margin_with_box(
        image,
        background_threshold=background_threshold,
        padding=padding,
    )
    return cropped


def _transform_pdf_problem_markers(
    metadata: dict[str, Any],
    *,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    scale: float = 1.0,
) -> None:
    markers = metadata.get("pdf_problem_markers")
    if not isinstance(markers, list):
        return

    transformed: list[dict[str, Any]] = []
    for marker in markers:
        if not isinstance(marker, dict):
            continue
        bbox = marker.get("bbox")
        if not isinstance(bbox, dict):
            continue
        try:
            left = (float(bbox.get("left", 0.0)) - offset_x) * scale
            top = (float(bbox.get("top", 0.0)) - offset_y) * scale
            right = (float(bbox.get("right", bbox.get("left", 0.0))) - offset_x) * scale
            bottom = (float(bbox.get("bottom", bbox.get("top", 0.0))) - offset_y) * scale
        except (TypeError, ValueError):
            continue
        updated = dict(marker)
        updated["bbox"] = {
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "width": max(0.0, right - left),
            "height": max(0.0, bottom - top),
        }
        transformed.append(updated)
    metadata["pdf_problem_markers"] = transformed


def _should_skip_deskew_for_pdf_text_layer(metadata: dict[str, Any]) -> bool:
    """PDF text markers are tied to the original rendered page coordinates."""
    markers = metadata.get("pdf_problem_markers")
    return metadata.get("source_type") == "pdf" and isinstance(markers, list) and bool(markers)


def _is_blank_rendered_page(path: str | Path, *, dark_threshold: int = 245, min_dark_ratio: float = 0.001) -> bool:
    try:
        image = Image.open(path).convert("L")
    except OSError:
        return False
    image.thumbnail((256, 256))
    histogram = image.histogram()
    dark_pixels = sum(histogram[:dark_threshold])
    total_pixels = max(1, sum(histogram))
    return (dark_pixels / total_pixels) < min_dark_ratio


def _summarize_pdf_render_quality(pages: list[NormalizedPageImage]) -> dict[str, Any]:
    marker_counts: list[int] = []
    blank_page_count = 0
    for page in pages:
        markers = page.metadata.get("pdf_problem_markers")
        marker_counts.append(len(markers) if isinstance(markers, list) else 0)
        if _is_blank_rendered_page(page.normalized_path):
            blank_page_count += 1

    marker_count = sum(marker_counts)
    pages_with_markers = sum(1 for count in marker_counts if count > 0)
    warnings: list[str] = []
    if not pages:
        warnings.append("no_rendered_pages")
    if pages and marker_count == 0:
        warnings.append("no_pdf_text_markers")
    if pages_with_markers and pages_with_markers < len(pages):
        warnings.append("some_pages_without_text_markers")
    if blank_page_count:
        warnings.append("blank_pages_detected")

    return {
        "page_count": len(pages),
        "pdf_text_marker_count": marker_count,
        "pdf_pages_with_text_markers": pages_with_markers,
        "pdf_pages_without_text_markers": max(0, len(pages) - pages_with_markers),
        "blank_page_count": blank_page_count,
        "has_pdf_text_markers": marker_count > 0,
        "preferred_segmentation_path": "pdf_text_markers" if marker_count > 0 else "ocr_fallback",
        "warnings": warnings,
    }


def deskew_image(image: Image.Image) -> Image.Image:
    if cv2 is None or np is None:
        return image

    image_bgr = _pil_to_bgr(image)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) < 50:
        return image

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) < 0.2:
        return image

    center = (image_bgr.shape[1] // 2, image_bgr.shape[0] // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        image_bgr,
        matrix,
        (image_bgr.shape[1], image_bgr.shape[0]),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return _bgr_to_pil(rotated)


def _order_quad_points(points):
    _require_cv2_numpy()
    pts = np.array(points, dtype="float32")
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1)
    top_left = pts[np.argmin(s)]
    bottom_right = pts[np.argmax(s)]
    top_right = pts[np.argmin(diff)]
    bottom_left = pts[np.argmax(diff)]
    return np.array([top_left, top_right, bottom_right, bottom_left], dtype="float32")


def detect_document_quad(image: Image.Image):
    if cv2 is None or np is None:
        return None

    image_bgr = _pil_to_bgr(image)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)

    min_area = image.width * image.height * 0.2
    for contour in contours[:20]:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        perimeter = cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(polygon) == 4:
            return _order_quad_points(polygon.reshape(4, 2))
    return None


def perspective_correct(image: Image.Image):
    if cv2 is None or np is None:
        return image, False

    quad = detect_document_quad(image)
    if quad is None:
        return image, False

    width_top = math.dist(quad[0], quad[1])
    width_bottom = math.dist(quad[3], quad[2])
    height_left = math.dist(quad[0], quad[3])
    height_right = math.dist(quad[1], quad[2])
    target_width = int(max(width_top, width_bottom))
    target_height = int(max(height_left, height_right))
    if target_width < 100 or target_height < 100:
        return image, False

    destination = np.array(
        [
            [0, 0],
            [target_width - 1, 0],
            [target_width - 1, target_height - 1],
            [0, target_height - 1],
        ],
        dtype="float32",
    )
    image_bgr = _pil_to_bgr(image)
    matrix = cv2.getPerspectiveTransform(quad, destination)
    warped = cv2.warpPerspective(image_bgr, matrix, (target_width, target_height))
    return _bgr_to_pil(warped), True


def normalize_image(
    source: str | Path,
    output_dir: str | Path,
    *,
    page_id: str | None = None,
    page_index: int = 0,
    enable_perspective: bool = True,
    enable_deskew: bool = True,
    enable_margin_crop: bool = True,
    max_dimension: int | None = None,
    base_metadata: dict[str, Any] | None = None,
) -> NormalizedPageImage:
    source_path = Path(source)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    image = load_image(source_path)
    metadata: dict[str, Any] = dict(base_metadata or {})
    metadata.setdefault("source_type", "image")

    if enable_perspective:
        image, changed = perspective_correct(image)
        metadata["perspective_corrected"] = changed
    if enable_deskew:
        if _should_skip_deskew_for_pdf_text_layer(metadata):
            metadata["deskewed"] = False
            metadata["deskew_skipped_reason"] = "pdf_text_layer"
        else:
            image = deskew_image(image)
            metadata["deskewed"] = True
    if enable_margin_crop:
        image, crop_box = _crop_uniform_margin_with_box(image)
        metadata["margin_crop_box"] = {
            "left": crop_box[0],
            "top": crop_box[1],
            "right": crop_box[2],
            "bottom": crop_box[3],
        }
        _transform_pdf_problem_markers(metadata, offset_x=float(crop_box[0]), offset_y=float(crop_box[1]))
        metadata["margin_cropped"] = True

    if max_dimension:
        width, height = image.size
        scale = min(max_dimension / max(width, height), 1.0)
        if scale < 1.0:
            new_size = (int(round(width * scale)), int(round(height * scale)))
            image = image.resize(new_size, Image.Resampling.LANCZOS)
            _transform_pdf_problem_markers(metadata, scale=scale)
            metadata["resized_to_max_dimension"] = max_dimension

    resolved_page_id = page_id or f"{source_path.stem}-page-{page_index + 1:03d}"
    out_path = out_dir / f"{resolved_page_id}.png"
    image.save(out_path)
    return NormalizedPageImage(
        page_id=resolved_page_id,
        source_path=str(source_path),
        normalized_path=str(out_path),
        page_index=page_index,
        width_px=image.width,
        height_px=image.height,
        metadata=metadata,
    )


def prepare_pages(
    source: str | Path,
    output_dir: str | Path,
    *,
    dpi: int = 160,
    enable_perspective: bool = True,
    enable_deskew: bool = True,
    enable_margin_crop: bool = True,
    max_dimension: int | None = None,
) -> list[NormalizedPageImage]:
    source_path = Path(source)
    suffix = source_path.suffix.lower()
    normalized_dir = Path(output_dir)
    normalized_dir.mkdir(parents=True, exist_ok=True)

    if suffix == ".pdf":
        rendered = render_pdf_pages(source_path, normalized_dir / "rendered", dpi=dpi)
        normalized_pages: list[NormalizedPageImage] = []
        for page in rendered:
            normalized = normalize_image(
                page.normalized_path,
                normalized_dir / "normalized",
                page_id=page.page_id,
                page_index=page.page_index,
                enable_perspective=False,
                enable_deskew=enable_deskew,
                enable_margin_crop=enable_margin_crop,
                max_dimension=max_dimension,
                base_metadata=dict(page.metadata),
            )
            normalized.metadata.setdefault("source_pdf_path", str(source_path))
            normalized.metadata["source_type"] = "pdf"
            normalized.metadata["document_like"] = True
            normalized_pages.append(normalized)
        return normalized_pages

    if suffix in HWP_DOCUMENT_EXTENSIONS:
        converted_pdf = convert_hwp_to_pdf(source_path, normalized_dir / "converted")
        rendered = render_pdf_pages(converted_pdf, normalized_dir / "rendered", dpi=dpi)
        conversion_quality = _summarize_pdf_render_quality(rendered)
        normalized_pages: list[NormalizedPageImage] = []
        for page in rendered:
            normalized = normalize_image(
                page.normalized_path,
                normalized_dir / "normalized",
                page_id=page.page_id,
                page_index=page.page_index,
                enable_perspective=False,
                enable_deskew=enable_deskew,
                enable_margin_crop=enable_margin_crop,
                max_dimension=max_dimension,
                base_metadata=dict(page.metadata),
            )
            normalized.metadata.setdefault("source_pdf_path", str(converted_pdf))
            normalized.metadata["source_type"] = "hwp"
            normalized.metadata["document_like"] = True
            normalized.metadata["source_hwp_path"] = str(source_path)
            normalized.metadata["converted_pdf_path"] = str(converted_pdf)
            normalized.metadata["hwp_conversion_quality"] = dict(conversion_quality)
            normalized_pages.append(normalized)
        return normalized_pages

    if suffix in SUPPORTED_IMAGE_EXTENSIONS:
        return [
            normalize_image(
                source_path,
                normalized_dir / "normalized",
                page_index=0,
                enable_perspective=enable_perspective,
                enable_deskew=enable_deskew,
                enable_margin_crop=enable_margin_crop,
                max_dimension=max_dimension,
            )
        ]

    raise ValueError(f"Unsupported input type: {source_path.suffix}")


def prepare_source_pages(
    path: str | Path,
    pdf_dpi: int = 200,
    detect_perspective: bool = False,
    deskew: bool = True,
    crop_margins: bool = True,
    max_dimension: int | None = None,
) -> list[PreparedPage]:
    normalized_pages = prepare_pages(
        path,
        Path(path).parent / ".pipeline_cache",
        dpi=pdf_dpi,
        enable_perspective=detect_perspective,
        enable_deskew=deskew,
        enable_margin_crop=crop_margins,
        max_dimension=max_dimension,
    )
    prepared: list[PreparedPage] = []
    for page in normalized_pages:
        image = Image.open(page.normalized_path).convert("RGB")
        if max_dimension:
            width, height = image.size
            scale = min(max_dimension / max(width, height), 1.0)
            if scale < 1.0:
                new_size = (int(round(width * scale)), int(round(height * scale)))
                image = image.resize(new_size, Image.Resampling.LANCZOS)
        prepared.append(
            PreparedPage(
                page_id=page.page_id,
                source_path=str(Path(page.normalized_path).resolve()),
                page_number=page.page_index + 1,
                image=image,
                original_size=(page.width_px, page.height_px),
                metadata={
                    **dict(page.metadata),
                    "original_source_path": str(Path(page.source_path).resolve()),
                    "normalized_path": str(Path(page.normalized_path).resolve()),
                },
            )
    )
    return prepared


def prepare_source_pages_batch(
    paths: Sequence[str | Path],
    pdf_dpi: int = 200,
    detect_perspective: bool = False,
    deskew: bool = True,
    crop_margins: bool = True,
    max_dimension: int | None = None,
) -> list[PreparedPage]:
    source_paths = [Path(path) for path in paths]
    if not source_paths:
        return []
    if len(source_paths) == 1:
        return prepare_source_pages(
            source_paths[0],
            pdf_dpi=pdf_dpi,
            detect_perspective=detect_perspective,
            deskew=deskew,
            crop_margins=crop_margins,
            max_dimension=max_dimension,
        )

    prepared_pages: list[PreparedPage] = []
    page_counter = 0
    for source_index, source_path in enumerate(source_paths, start=1):
        cache_dir = source_path.parent / ".pipeline_cache" / f"batch_{source_index:03d}_{source_path.stem}"
        normalized_pages = prepare_pages(
            source_path,
            cache_dir,
            dpi=pdf_dpi,
            enable_perspective=detect_perspective,
            enable_deskew=deskew,
            enable_margin_crop=crop_margins,
            max_dimension=max_dimension,
        )

        for local_page_index, page in enumerate(normalized_pages, start=1):
            page_counter += 1
            image = Image.open(page.normalized_path).convert("RGB")
            if max_dimension:
                width, height = image.size
                scale = min(max_dimension / max(width, height), 1.0)
                if scale < 1.0:
                    new_size = (int(round(width * scale)), int(round(height * scale)))
                    image = image.resize(new_size, Image.Resampling.LANCZOS)

            prepared_pages.append(
                PreparedPage(
                    page_id=f"{source_path.stem}-{source_index:02d}-page-{local_page_index:03d}",
                    source_path=str(Path(page.normalized_path).resolve()),
                    page_number=page_counter,
                    image=image,
                    original_size=(page.width_px, page.height_px),
                    metadata={
                        **dict(page.metadata),
                        "original_source_path": str(source_path.resolve()),
                        "normalized_path": str(Path(page.normalized_path).resolve()),
                        "batch_source_index": source_index,
                        "batch_total_sources": len(source_paths),
                        "original_page_index": page.page_index + 1,
                    },
                )
            )
    return prepared_pages


def load_pages(source: str | Path, options: PreprocessOptions) -> list[NormalizedPageImage]:
    normalized_pages = prepare_pages(
        source,
        Path(source).parent / ".pipeline_cache",
        dpi=options.dpi,
        enable_perspective=options.enable_perspective,
        enable_deskew=options.enable_deskew,
        enable_margin_crop=options.enable_margin_crop,
        max_dimension=options.max_dimension,
    )
    return normalized_pages
