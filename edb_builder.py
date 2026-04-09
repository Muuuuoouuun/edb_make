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


def build_edb(
    records: list[bytes],
    header_flag: int,
    version: str = "6.0.5.3911",
    terminal_eof_plus_one: bool = True,
    page_count_hint: int = DEFAULT_PAGE_COUNT_HINT,
) -> bytes:
    final_records = list(records)
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
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    image.thumbnail(max_size, Image.Resampling.LANCZOS)
    output = io.BytesIO()
    ext = (format_hint or image.format or "JPEG").upper()
    if ext == "PNG":
        image.save(output, format="PNG")
    else:
        image.save(output, format="JPEG", quality=quality, optimize=True)
    return output.getvalue()


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
