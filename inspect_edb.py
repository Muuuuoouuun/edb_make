#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


OUTER_PREFIX_LEN = 11


def read_u16be(buf: bytes, offset: int) -> int:
    return struct.unpack_from(">H", buf, offset)[0]


def read_u32be(buf: bytes, offset: int) -> int:
    return struct.unpack_from(">I", buf, offset)[0]


def read_i32be(buf: bytes, offset: int) -> int:
    return struct.unpack_from(">i", buf, offset)[0]


def read_f32be(buf: bytes, offset: int) -> float:
    return struct.unpack_from(">f", buf, offset)[0]


def decode_utf8_lossy(buf: bytes) -> str:
    return buf.decode("utf-8", errors="replace")


def parse_png_size(image: bytes) -> tuple[int, int] | None:
    sig = b"\x89PNG\r\n\x1a\n"
    if not image.startswith(sig) or len(image) < 24:
        return None
    width = read_u32be(image, 16)
    height = read_u32be(image, 20)
    return width, height


def parse_jpeg_size(image: bytes) -> tuple[int, int] | None:
    if not image.startswith(b"\xff\xd8"):
        return None

    i = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }

    while i + 4 <= len(image):
        if image[i] != 0xFF:
            i += 1
            continue
        while i < len(image) and image[i] == 0xFF:
            i += 1
        if i >= len(image):
            break
        marker = image[i]
        i += 1
        if marker in {0xD8, 0xD9}:
            continue
        if i + 2 > len(image):
            break
        seg_len = read_u16be(image, i)
        if seg_len < 2 or i + seg_len > len(image):
            break
        if marker in sof_markers and seg_len >= 7:
            height = read_u16be(image, i + 3)
            width = read_u16be(image, i + 5)
            return width, height
        i += seg_len
    return None


def detect_image_format(image: bytes) -> str | None:
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if image.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    return None


def detect_image_size(image: bytes) -> tuple[int, int] | None:
    fmt = detect_image_format(image)
    if fmt == "png":
        return parse_png_size(image)
    if fmt == "jpeg":
        return parse_jpeg_size(image)
    return None


@dataclass
class EmbeddedImage:
    index: int
    offset: int
    length: int
    fmt: str | None
    width: int | None
    height: int | None


@dataclass
class RecordInfo:
    index: int
    offset: int
    size: int
    marker: int
    id16: int
    id32: int
    kind_hint: int
    pos_x: float | None = None
    pos_y: float | None = None
    width_hint: float | None = None
    height_hint: float | None = None
    text: str | None = None
    font_size: int | None = None
    color_i32: int | None = None
    tail_bytes: str | None = None
    embedded_images: list[EmbeddedImage] = field(default_factory=list)


@dataclass
class ParsedEdb:
    path: str
    outer_size: int
    inner_size: int
    outer_prefix_hex: str
    page_count_hint: int
    record_count_hint: int
    canvas_width: float
    canvas_height: float
    version: str
    timestamp_epoch: int
    timestamp_utc: str
    header_flag: int
    record_count_actual: int
    records: list[RecordInfo]


def parse_embedded_images(record: bytes) -> list[EmbeddedImage]:
    images: list[EmbeddedImage] = []
    cursor = 49
    image_index = 0

    while cursor + 4 <= len(record):
        image_len = read_u32be(record, cursor - 4)
        if image_len <= 0 or cursor + image_len > len(record):
            break
        image_bytes = record[cursor : cursor + image_len]
        fmt = detect_image_format(image_bytes)
        if fmt is None:
            break
        size = detect_image_size(image_bytes)
        images.append(
            EmbeddedImage(
                index=image_index,
                offset=cursor,
                length=image_len,
                fmt=fmt,
                width=size[0] if size else None,
                height=size[1] if size else None,
            )
        )
        image_index += 1
        cursor += image_len + 4
        if cursor >= len(record) - 1:
            break

    return images


def try_parse_text_record(record: bytes) -> tuple[str, int, int, str] | None:
    if len(record) < 48:
        return None
    text_len = read_u32be(record, 44)
    text_start = 48
    text_end = text_start + text_len
    if text_len <= 0 or text_end > len(record):
        return None
    payload = record[text_start:text_end]
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    tail = record[text_end:]
    if not tail:
        return None
    if any(b not in {0x00, 0x01, 0x02, 0x03, 0x04} for b in tail):
        return None
    font_size = record[35]
    color_i32 = read_i32be(record, 36)
    return text, font_size, color_i32, tail.hex(" ")


def parse_record(index: int, offset: int, record: bytes) -> RecordInfo:
    info = RecordInfo(
        index=index,
        offset=offset,
        size=read_u32be(record, 0),
        marker=record[4],
        id16=read_u16be(record, 5),
        id32=read_u32be(record, 7),
        kind_hint=read_u32be(record, 11),
    )

    if len(record) >= 43:
        info.pos_x = read_f32be(record, 27)
        info.pos_y = read_f32be(record, 31)
        info.width_hint = read_f32be(record, 35)
        info.height_hint = read_f32be(record, 39)

    images = parse_embedded_images(record)
    if images:
        info.embedded_images = images
        info.tail_bytes = record[-1:].hex(" ")
        return info

    text_info = try_parse_text_record(record)
    if text_info is not None:
        text, font_size, color_i32, tail_hex = text_info
        info.text = text
        info.font_size = font_size
        info.color_i32 = color_i32
        info.tail_bytes = tail_hex
        info.width_hint = read_f32be(record, 40)
        info.height_hint = None
        return info

    if len(record) > 48:
        info.tail_bytes = record[-16:].hex(" ")
    return info


def parse_edb(path: Path) -> ParsedEdb:
    data = path.read_bytes()
    if len(data) <= OUTER_PREFIX_LEN:
        raise ValueError("file too small to be a valid .edb")
    if data[4:7] != b"edb":
        raise ValueError("missing expected 'edb' marker in outer header")

    inner = gzip.decompress(data[OUTER_PREFIX_LEN:])

    version_len = inner[16]
    version_start = 17
    version_end = version_start + version_len
    version = decode_utf8_lossy(inner[version_start:version_end])
    timestamp_offset = version_end + 4
    timestamp_epoch = read_u32be(inner, timestamp_offset)
    header_flag = inner[timestamp_offset + 12]
    record_offset = timestamp_offset + 13

    records: list[RecordInfo] = []
    current = record_offset
    record_index = 0
    while current + 4 <= len(inner):
        size = read_u32be(inner, current)
        if size < 5:
            break
        max_end = current + size
        if max_end > len(inner) + 1:
            break
        record = inner[current : min(max_end, len(inner))]
        records.append(parse_record(record_index, current, record))
        current = max_end
        record_index += 1
        if current >= len(inner):
            break

    timestamp_utc = dt.datetime.fromtimestamp(timestamp_epoch, tz=dt.timezone.utc).isoformat()
    return ParsedEdb(
        path=str(path),
        outer_size=len(data),
        inner_size=len(inner),
        outer_prefix_hex=data[:OUTER_PREFIX_LEN].hex(" "),
        page_count_hint=read_u16be(inner, 0),
        record_count_hint=read_u16be(inner, 2),
        canvas_width=read_f32be(inner, 4),
        canvas_height=read_f32be(inner, 8),
        version=version,
        timestamp_epoch=timestamp_epoch,
        timestamp_utc=timestamp_utc,
        header_flag=header_flag,
        record_count_actual=len(records),
        records=records,
    )


def summarize(parsed: ParsedEdb) -> str:
    image_records = [r for r in parsed.records if r.embedded_images]
    text_records = [r for r in parsed.records if r.text is not None]
    other_records = [r for r in parsed.records if not r.embedded_images and r.text is None]

    lines = [
        f"path: {parsed.path}",
        f"outer_size: {parsed.outer_size} bytes",
        f"inner_size: {parsed.inner_size} bytes",
        f"outer_prefix: {parsed.outer_prefix_hex}",
        (
            "header:"
            f" pages_hint={parsed.page_count_hint},"
            f" records_hint={parsed.record_count_hint},"
            f" canvas={parsed.canvas_width:.1f}x{parsed.canvas_height:.1f},"
            f" version={parsed.version},"
            f" timestamp_utc={parsed.timestamp_utc},"
            f" header_flag={parsed.header_flag}"
        ),
        (
            "records:"
            f" actual={parsed.record_count_actual},"
            f" text={len(text_records)},"
            f" image={len(image_records)},"
            f" other={len(other_records)}"
        ),
    ]

    preview_texts = [r.text for r in text_records[:5] if r.text]
    if preview_texts:
        lines.append("text_preview:")
        lines.extend(f"  - {text}" for text in preview_texts)

    if image_records:
        lines.append("image_preview:")
        for record in image_records[:5]:
            dims = ", ".join(
                f"{img.fmt}:{img.width}x{img.height}:{img.length}"
                for img in record.embedded_images
            )
            lines.append(
                "  - "
                f"record#{record.index} id={record.id32}"
                f" xywh=({record.pos_x:.6f}, {record.pos_y:.6f}, {record.width_hint:.6f}, {record.height_hint:.6f})"
                f" images=[{dims}]"
            )

    return "\n".join(lines)


def to_jsonable(parsed: ParsedEdb) -> dict[str, Any]:
    return asdict(parsed)


def extract_images(parsed: ParsedEdb, source_path: Path, out_dir: Path) -> list[Path]:
    data = source_path.read_bytes()
    inner = gzip.decompress(data[OUTER_PREFIX_LEN:])
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for record in parsed.records:
        if not record.embedded_images:
            continue
        record_start = record.offset
        record_end = min(record.offset + record.size, len(inner))
        record_bytes = inner[record_start:record_end]
        for image in record.embedded_images:
            image_bytes = record_bytes[image.offset : image.offset + image.length]
            suffix = ".png" if image.fmt == "png" else ".jpg" if image.fmt == "jpeg" else ".bin"
            name = f"record_{record.index:04d}_img_{image.index}{suffix}"
            target = out_dir / name
            target.write_bytes(image_bytes)
            written.append(target)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a ClassIn .edb file.")
    parser.add_argument("path", type=Path, help="Path to the .edb file")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a text summary")
    parser.add_argument(
        "--extract-images",
        type=Path,
        help="Optional output directory for embedded image extraction",
    )
    args = parser.parse_args()

    parsed = parse_edb(args.path)
    if args.extract_images:
        written = extract_images(parsed, args.path, args.extract_images)
        print(f"extracted_images: {len(written)} -> {args.extract_images}")
        for path in written[:20]:
            print(f"  {path}")
        if len(written) > 20:
            print(f"  ... and {len(written) - 20} more")
        if not args.json:
            print()
    if args.json:
        print(json.dumps(to_jsonable(parsed), ensure_ascii=False, indent=2))
    else:
        print(summarize(parsed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
