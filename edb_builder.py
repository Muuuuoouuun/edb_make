#!/usr/bin/env python3
from __future__ import annotations

import gzip
import io
import struct
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


OUTER_PREFIX = bytes.fromhex("00 00 00 04 65 64 62 00 00 32 01")
CANVAS_WIDTH = 590.0
CANVAS_HEIGHT = 1280.0
DEFAULT_PAGE_COUNT_HINT = 50

# Crop format versions seen in real ClassIn EDB exports.
# v1 = legacy wide layout (header_flag=4, image_secondary is a downsampled preview).
# v2 = current crop layout (header_flag=0, image_secondary is a whitespace-trimmed tight crop,
#       displayed image is resized to a fixed pixel width so more problems fit per board page).
CROP_FORMAT_V1 = "v1"
CROP_FORMAT_V2 = "v2"
DEFAULT_CROP_FORMAT = CROP_FORMAT_V2

# Observed in real v2 exports: every problem image is rendered at ~301px wide
# (width_hint ≈ 0.2352). Keep this aligned with the ClassIn sample EDBs in
# Downloads/edb.v2.edb so downstream rendering matches.
V2_TARGET_IMAGE_WIDTH_PX = 301
V2_HEADER_FLAG_IMAGE_ONLY = 0
V2_VERSION_STRING = "6.0.5.3913"
V1_HEADER_FLAG_IMAGE_ONLY = 4
V1_VERSION_STRING = "6.0.7.3141"

# ClassIn can read either embedded bitmap from a v1 image record. Regular
# problem crops retain the legacy lightweight preview, while a full-page
# record keeps enough secondary pixels for small text to survive zooming.
V1_SECONDARY_PREVIEW_MAX_SIZE = (768, 768)
V1_PAGE_AS_IS_SECONDARY_MAX_EDGE_PX = 4096
V1_PAGE_AS_IS_SECONDARY_MAX_PIXELS = 8_000_000
V1_PAGE_AS_IS_SECONDARY_JPEG_QUALITY = 92

# Whitespace-trim threshold used when producing the tight image_secondary in v2 mode.
# Pixels with all RGB channels above this value are treated as background.
V2_TIGHT_CROP_WHITE_THRESHOLD = 244


def pack_u16(value: int) -> bytes:
    return struct.pack(">H", value)


def pack_u32(value: int) -> bytes:
    return struct.pack(">I", value)


def pack_i32(value: int) -> bytes:
    return struct.pack(">i", value)


def pack_f32(value: float) -> bytes:
    return struct.pack(">f", value)


@dataclass(slots=True)
class TextRecordSpec:
    record_id: int
    text: str
    x: float
    y: float
    width_hint: float
    font_size: int = 10
    color_i32: int = -1
    tail: bytes = b"\x03"


@dataclass(slots=True)
class ImageRecordSpec:
    record_id: int
    image_primary: bytes
    image_secondary: bytes
    x: float
    y: float
    width_hint: float
    height_hint: float


def build_inner_header(
    record_count_hint: int,
    version: str = "6.0.5.3911",
    timestamp_epoch: int | None = None,
    header_flag: int = 3,
    page_count_hint: int = 50,
) -> bytes:
    if timestamp_epoch is None:
        timestamp_epoch = int(time.time())

    version_bytes = version.encode("utf-8")
    return b"".join(
        [
            pack_u16(page_count_hint),
            pack_u16(record_count_hint),
            pack_f32(CANVAS_WIDTH),
            pack_f32(CANVAS_HEIGHT),
            pack_u32(0),
            bytes([len(version_bytes)]),
            version_bytes,
            pack_u32(0),
            pack_u32(timestamp_epoch),
            pack_u32(0),
            pack_u32(0),
            bytes([header_flag]),
        ]
    )


def build_text_record(spec: TextRecordSpec) -> bytes:
    text_bytes = spec.text.encode("utf-8")
    body = b"".join(
        [
            b"\x28",
            pack_u16(spec.record_id),
            pack_u32(spec.record_id),
            pack_u32(0),
            pack_u32(0),
            pack_f32(1.0),
            pack_f32(1.0),
            pack_f32(spec.x),
            pack_f32(spec.y),
            bytes([spec.font_size]),
            pack_i32(spec.color_i32),
            pack_f32(spec.width_hint),
            pack_u32(len(text_bytes)),
            text_bytes,
            spec.tail,
        ]
    )
    return pack_u32(len(body) + 4) + body


def build_image_record(spec: ImageRecordSpec) -> bytes:
    body = b"".join(
        [
            b"\x28",
            pack_u16(spec.record_id),
            pack_u32(spec.record_id),
            pack_u32(0),
            pack_u32(0),
            pack_f32(1.0),
            pack_f32(1.0),
            pack_f32(spec.x),
            pack_f32(spec.y),
            pack_f32(spec.width_hint),
            pack_f32(spec.height_hint),
            b"\x00\x00",
            pack_u32(len(spec.image_primary)),
            spec.image_primary,
            pack_u32(len(spec.image_secondary)),
            spec.image_secondary,
        ]
    )
    return pack_u32(len(body) + 4) + body


def _bump_record_size_by_one(record: bytes) -> bytes:
    size = pack_u32(struct.unpack(">I", record[:4])[0] + 1)
    return size + record[4:]


def _append_record_separator(record: bytes, separator: bytes = b"\x04") -> bytes:
    size = pack_u32(struct.unpack(">I", record[:4])[0] + len(separator))
    return size + record[4:] + separator


def build_edb(
    records: list[bytes],
    header_flag: int,
    version: str = "6.0.5.3911",
    terminal_eof_plus_one: bool = True,
    page_count_hint: int = DEFAULT_PAGE_COUNT_HINT,
) -> bytes:
    if not records:
        raise ValueError("EDB requires at least one record")
    final_records = list(records)
    if len(final_records) > 1:
        final_records[:-1] = [_append_record_separator(record) for record in final_records[:-1]]
    if terminal_eof_plus_one and final_records:
        final_records[-1] = _bump_record_size_by_one(final_records[-1])

    inner = build_inner_header(
        record_count_hint=len(final_records),
        version=version,
        header_flag=header_flag,
        page_count_hint=page_count_hint,
    ) + b"".join(final_records)
    return OUTER_PREFIX + gzip.compress(inner, compresslevel=9, mtime=0)


def write_edb(path: str | Path, payload: bytes) -> None:
    Path(path).write_bytes(payload)


def build_preview_image_bytes(image_bytes: bytes, max_size: tuple[int, int] = (512, 512), format_hint: str | None = None, quality: int = 88) -> bytes:
    source = Image.open(io.BytesIO(image_bytes))
    ext = (format_hint or source.format or "JPEG").upper()
    image = source.convert("RGBA" if ext == "PNG" and "A" in source.getbands() else "RGB")
    image.thumbnail(max_size, Image.Resampling.LANCZOS)
    output = io.BytesIO()
    if ext == "PNG":
        image.save(output, format="PNG")
    else:
        image.save(output, format="JPEG", quality=quality, optimize=True)
    return output.getvalue()


def build_v1_secondary_image_bytes(
    image_bytes: bytes,
    *,
    page_as_is: bool,
    format_hint: str | None = None,
) -> bytes:
    """Build the v1 secondary bitmap without degrading full-page text."""
    if not page_as_is:
        return build_preview_image_bytes(
            image_bytes,
            max_size=V1_SECONDARY_PREVIEW_MAX_SIZE,
            format_hint=format_hint,
            quality=88,
        )

    with Image.open(io.BytesIO(image_bytes)) as source:
        width, height = source.size
        if (
            max(width, height) <= V1_PAGE_AS_IS_SECONDARY_MAX_EDGE_PX
            and width * height <= V1_PAGE_AS_IS_SECONDARY_MAX_PIXELS
        ):
            # Reusing the immutable bytes avoids a second decode/encode pass and
            # preserves every source pixel for ordinary 200-DPI pages.
            return image_bytes

        edge_scale = V1_PAGE_AS_IS_SECONDARY_MAX_EDGE_PX / max(width, height, 1)
        pixel_scale = (
            V1_PAGE_AS_IS_SECONDARY_MAX_PIXELS / max(width * height, 1)
        ) ** 0.5
        scale = min(1.0, edge_scale, pixel_scale)
        target_size = (
            max(1, int(width * scale)),
            max(1, int(height * scale)),
        )
        ext = (format_hint or source.format or "JPEG").upper()
        image = source.convert("RGBA" if ext == "PNG" and "A" in source.getbands() else "RGB")
        if image.size != target_size:
            image = image.resize(target_size, Image.Resampling.LANCZOS)

    output = io.BytesIO()
    if ext == "PNG":
        image.save(output, format="PNG")
    else:
        image.save(
            output,
            format="JPEG",
            quality=V1_PAGE_AS_IS_SECONDARY_JPEG_QUALITY,
            optimize=True,
        )
    return output.getvalue()


def _content_bbox(image: Image.Image, *, white_threshold: int) -> tuple[int, int, int, int] | None:
    if "A" in image.getbands():
        alpha = image.getchannel("A")
        bbox = alpha.point(lambda px: 255 if px > 2 else 0, mode="L").getbbox()
        return bbox

    rgb = image.convert("RGB")
    width, height = rgb.size
    pixels = rgb.load()
    left = width
    right = -1
    top = height
    bottom = -1
    for y in range(height):
        for x in range(width):
            r, g, b = pixels[x, y]
            if r < white_threshold or g < white_threshold or b < white_threshold:
                if x < left:
                    left = x
                if x > right:
                    right = x
                if y < top:
                    top = y
                if y > bottom:
                    bottom = y
    if right < left or bottom < top:
        return None
    return left, top, right + 1, bottom + 1


def build_tight_crop_image_bytes(
    image_bytes: bytes,
    *,
    format_hint: str | None = None,
    quality: int = 88,
    white_threshold: int = V2_TIGHT_CROP_WHITE_THRESHOLD,
    min_trim_ratio: float = 0.02,
) -> bytes:
    """Return JPEG/PNG bytes of a whitespace-trimmed copy of the source image.

    Used as the image_secondary in v2 crop format. Matches the behaviour
    observed in real ClassIn v2 EDBs where img_1 is sometimes smaller than
    img_0 because trailing whitespace has been removed.
    """
    source = Image.open(io.BytesIO(image_bytes))
    ext = (format_hint or source.format or "JPEG").upper()
    image = source.convert("RGBA" if ext == "PNG" and "A" in source.getbands() else "RGB")
    bbox = _content_bbox(image, white_threshold=white_threshold)
    if bbox is not None:
        left, top, right, bottom = bbox
        trimmed_w = right - left
        trimmed_h = bottom - top
        orig_w, orig_h = image.size
        # Only apply the trim if it removes a meaningful amount of margin,
        # otherwise the secondary image stays the same size as the primary
        # (which matches v2 records where img_0 and img_1 share dimensions).
        if (
            orig_w - trimmed_w > orig_w * min_trim_ratio
            or orig_h - trimmed_h > orig_h * min_trim_ratio
        ):
            image = image.crop(bbox)
    output = io.BytesIO()
    if ext == "PNG":
        image.save(output, format="PNG")
    else:
        image.save(output, format="JPEG", quality=quality, optimize=True)
    return output.getvalue()


def header_flag_for_crop_format(crop_format: str, *, mode: str = "image") -> int:
    """Return the header_flag value to use for the given crop_format and record mode."""
    if mode == "image":
        if crop_format == CROP_FORMAT_V2:
            return V2_HEADER_FLAG_IMAGE_ONLY
        return V1_HEADER_FLAG_IMAGE_ONLY
    return 3


def version_string_for_crop_format(crop_format: str) -> str:
    if crop_format == CROP_FORMAT_V2:
        return V2_VERSION_STRING
    return V1_VERSION_STRING


def normalize_x_px(x_px: float) -> float:
    return x_px / CANVAS_HEIGHT


def normalize_width_px(width_px: float) -> float:
    return width_px / CANVAS_HEIGHT


def normalize_y_px(y_px: float, *, page_count_hint: int = DEFAULT_PAGE_COUNT_HINT) -> float:
    return y_px / (CANVAS_WIDTH * page_count_hint)


def normalize_height_px(height_px: float, *, page_count_hint: int = DEFAULT_PAGE_COUNT_HINT) -> float:
    return height_px / (CANVAS_WIDTH * page_count_hint)


def build_text_only_example() -> bytes:
    records = [
        build_text_record(TextRecordSpec(record_id=0, text="test -text", x=0.0774305537, y=0.0026854991, width_hint=0.0531250015, tail=b"\x03\x03")),
        build_text_record(TextRecordSpec(record_id=1, text="text only", x=0.0802083313, y=0.0043427497, width_hint=0.0515624993, tail=b"\x03")),
    ]
    return build_edb(records, header_flag=3)


def build_image_only_example(image_primary: bytes, image_secondary: bytes) -> bytes:
    records = [
        build_image_record(
            ImageRecordSpec(
                record_id=0,
                image_primary=image_primary,
                image_secondary=image_secondary,
                x=0.0687500015,
                y=0.0029830509,
                width_hint=0.5359374881,
                height_hint=0.0130847460,
            )
        )
    ]
    return build_edb(records, header_flag=4)
