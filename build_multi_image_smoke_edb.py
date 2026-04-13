#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import struct
from pathlib import Path

import inspect_edb


OUTER_PREFIX = bytes.fromhex("00 00 00 04 65 64 62 00 00 32 01")


def pack_u16(value: int) -> bytes:
    return struct.pack(">H", value)


def pack_u32(value: int) -> bytes:
    return struct.pack(">I", value)


def pack_f32(value: float) -> bytes:
    return struct.pack(">f", value)


def build_inner_header(
    *,
    page_count_hint: int,
    record_count_hint: int,
    version: str,
    timestamp_epoch: int,
    header_flag: int,
    canvas_width: float,
    canvas_height: float,
) -> bytes:
    version_bytes = version.encode("utf-8")
    return b"".join(
        [
            pack_u16(page_count_hint),
            pack_u16(record_count_hint),
            pack_f32(canvas_width),
            pack_f32(canvas_height),
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


def build_image_record(
    *,
    record_id: int,
    image_primary: bytes,
    image_secondary: bytes,
    x: float,
    y: float,
    width_hint: float,
    height_hint: float,
) -> bytes:
    body = b"".join(
        [
            b"\x28",
            pack_u16(record_id),
            pack_u32(record_id),
            pack_u32(0),
            pack_u32(0),
            pack_f32(1.0),
            pack_f32(1.0),
            pack_f32(x),
            pack_f32(y),
            pack_f32(width_hint),
            pack_f32(height_hint),
            b"\x00\x00",
            pack_u32(len(image_primary)),
            image_primary,
            pack_u32(len(image_secondary)),
            image_secondary,
        ]
    )
    return pack_u32(len(body) + 4) + body


def bump_record_size_by_one(record: bytes) -> bytes:
    return pack_u32(struct.unpack(">I", record[:4])[0] + 1) + record[4:]


def extract_first_image_record(source_path: Path) -> tuple[inspect_edb.ParsedEdb, inspect_edb.RecordInfo, bytes, bytes]:
    parsed = inspect_edb.parse_edb(source_path)
    image_record = next((record for record in parsed.records if record.embedded_images), None)
    if image_record is None or len(image_record.embedded_images) < 2:
        raise ValueError("Source EDB must contain an image record with primary and secondary embedded images")

    raw = source_path.read_bytes()
    inner = gzip.decompress(raw[inspect_edb.OUTER_PREFIX_LEN :])
    record_bytes = inner[image_record.offset : image_record.offset + image_record.size]

    primary_info = image_record.embedded_images[0]
    secondary_info = image_record.embedded_images[1]
    primary = record_bytes[primary_info.offset : primary_info.offset + primary_info.length]
    secondary = record_bytes[secondary_info.offset : secondary_info.offset + secondary_info.length]
    return parsed, image_record, primary, secondary


def build_multi_image_edb(
    *,
    source_edb: Path,
    output_edb: Path,
    count: int,
    step_pages: float,
) -> Path:
    parsed, image_record, primary, secondary = extract_first_image_record(source_edb)
    page_count_hint = max(1, parsed.page_count_hint)
    y_step = step_pages / float(page_count_hint)

    base_x = image_record.pos_x or 0.0
    base_y = image_record.pos_y or 0.0
    width_hint = image_record.width_hint or 0.52
    height_hint = image_record.height_hint or 0.024

    records = [
        build_image_record(
            record_id=index,
            image_primary=primary,
            image_secondary=secondary,
            x=base_x,
            y=round(base_y + index * y_step, 6),
            width_hint=width_hint,
            height_hint=height_hint,
        )
        for index in range(count)
    ]
    if records:
        records[-1] = bump_record_size_by_one(records[-1])

    inner = build_inner_header(
        page_count_hint=page_count_hint,
        record_count_hint=len(records),
        version=parsed.version,
        timestamp_epoch=parsed.timestamp_epoch,
        header_flag=4,
        canvas_width=parsed.canvas_width,
        canvas_height=parsed.canvas_height,
    ) + b"".join(records)

    output_edb.write_bytes(OUTER_PREFIX + gzip.compress(inner, compresslevel=9, mtime=0))
    return output_edb


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a multi-image smoke-test .edb by duplicating the first image record from a source .edb.")
    parser.add_argument("source_edb", type=Path, help="Path to a source .edb that already opens and contains one image record")
    parser.add_argument("output_edb", type=Path, help="Path to write the duplicated multi-image .edb")
    parser.add_argument("--count", type=int, default=3, help="Number of duplicated image records to write")
    parser.add_argument("--step-pages", type=float, default=1.2, help="Vertical spacing between duplicated records in board pages")
    args = parser.parse_args()

    if args.count <= 0:
        raise ValueError("--count must be greater than 0")

    output_path = build_multi_image_edb(
        source_edb=args.source_edb.resolve(),
        output_edb=args.output_edb.resolve(),
        count=args.count,
        step_pages=args.step_pages,
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
