#!/usr/bin/env python3
from __future__ import annotations

import math
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


def render_pdf_pages(source: str | Path, output_dir: str | Path, dpi: int = 160) -> list[NormalizedPageImage]:
    if fitz is None:
        raise RuntimeError("PyMuPDF is required to render PDF pages")

    source_path = Path(source)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

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
                    metadata={"source_type": "pdf", "dpi": dpi},
                )
            )
    finally:
        doc.close()
    return pages


def load_image(source: str | Path) -> Image.Image:
    return Image.open(source).convert("RGB")


def crop_uniform_margin(image: Image.Image, background_threshold: int = 245, padding: int = 12) -> Image.Image:
    gray = ImageOps.grayscale(image)
    mask = gray.point(lambda px: 255 if px < background_threshold else 0)
    bbox = mask.getbbox()
    if bbox is None:
        return image
    left = max(0, bbox[0] - padding)
    top = max(0, bbox[1] - padding)
    right = min(image.width, bbox[2] + padding)
    bottom = min(image.height, bbox[3] + padding)
    return image.crop((left, top, right, bottom))


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
) -> NormalizedPageImage:
    source_path = Path(source)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    image = load_image(source_path)
    metadata: dict[str, Any] = {"source_type": "image"}

    if enable_perspective:
        image, changed = perspective_correct(image)
        metadata["perspective_corrected"] = changed
    if enable_deskew:
        image = deskew_image(image)
        metadata["deskewed"] = True
    if enable_margin_crop:
        image = crop_uniform_margin(image)
        metadata["margin_cropped"] = True

    if max_dimension:
        width, height = image.size
        scale = min(max_dimension / max(width, height), 1.0)
        if scale < 1.0:
            new_size = (int(round(width * scale)), int(round(height * scale)))
            image = image.resize(new_size, Image.Resampling.LANCZOS)
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
            normalized_pages.append(
                normalize_image(
                    page.normalized_path,
                    normalized_dir / "normalized",
                    page_id=page.page_id,
                    page_index=page.page_index,
                    enable_perspective=False,
                    enable_deskew=enable_deskew,
                    enable_margin_crop=enable_margin_crop,
                    max_dimension=max_dimension,
                )
            )
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
                source_path=page.source_path,
                page_number=page.page_index + 1,
                image=image,
                original_size=(page.width_px, page.height_px),
                metadata=dict(page.metadata),
            )
    )
    return prepared


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
