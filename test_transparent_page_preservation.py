from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageChops, ImageDraw, ImageOps

import preprocess
from build_problem_board_edb import CROP_FORMAT_V1, CROP_FORMAT_V2, run_problem_export
from inspect_edb import extract_images, parse_edb


def alpha_page(mode: str = "RGBA") -> Image.Image:
    image = Image.new("RGBA", (160, 240), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for box in ((0, 0, 5, 5), (154, 0, 159, 5), (0, 234, 5, 239), (154, 234, 159, 239), (20, 35, 140, 43), (20, 55, 140, 63)):
        draw.rectangle(box, fill=(0, 0, 0, 255))
    draw.rectangle((20, 170, 140, 180), fill=(220, 10, 10, 128))
    if mode == "LA":
        return image.convert("LA")
    if mode == "P":
        palette = Image.new("P", image.size, 0)
        palette.putpalette([0, 0, 0, 0, 0, 0, 220, 10, 10] + [0] * (768 - 9))
        pdraw = ImageDraw.Draw(palette)
        for box in ((0, 0, 5, 5), (154, 0, 159, 5), (0, 234, 5, 239), (154, 234, 159, 239), (20, 35, 140, 43), (20, 55, 140, 63)):
            pdraw.rectangle(box, fill=1)
        pdraw.rectangle((20, 170, 140, 180), fill=2)
        palette.info["transparency"] = bytes([0, 255, 128] + [255] * 253)
        return palette
    return image


def paper_reference(source: Path) -> Image.Image:
    with Image.open(source) as image:
        oriented = ImageOps.exif_transpose(image).convert("RGBA")
        return Image.alpha_composite(Image.new("RGBA", oriented.size, "white"), oriented).convert("RGB")


@pytest.mark.parametrize("mode", ["RGBA", "LA", "P"])
def test_transparent_black_pixels_are_composited_over_white_without_losing_opaque_ink(tmp_path, mode):
    source = tmp_path / f"source-{mode}.png"
    alpha_page(mode).save(source)
    original_bytes = source.read_bytes()
    expected = paper_reference(source)
    actual = preprocess.load_image(source)
    assert actual.mode == "RGB"
    assert ImageChops.difference(expected, actual).getbbox() is None
    assert actual.getpixel((10, 10)) == (255, 255, 255)
    assert actual.getpixel((25, 38)) == (0, 0, 0)
    assert source.read_bytes() == original_bytes


@pytest.mark.parametrize("no_transforms", [False, True])
def test_transparent_page_normalizes_instead_of_reusing_unsafe_alpha_bytes(tmp_path, no_transforms):
    source = tmp_path / "transparent.png"
    alpha_page().save(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    pages = preprocess.prepare_source_pages(source, detect_perspective=False, deskew=not no_transforms, crop_margins=not no_transforms)
    assert len(pages) == 1
    assert Path(pages[0].source_path).resolve() != source.resolve()
    assert not pages[0].metadata.get("image_passthrough")
    assert ImageChops.difference(paper_reference(source), pages[0].image).getbbox() is None
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    # The disk-backed accessor must apply the same conversion to old/raw paths.
    lazy = preprocess.PreparedPage("raw-alpha", str(source), 1, None, pages[0].size)
    assert ImageChops.difference(paper_reference(source), lazy.image).getbbox() is None
    lazy.release_image()


def test_opaque_rgb_passthrough_is_unchanged_and_exif_alpha_orientation_applied_once(tmp_path):
    opaque = tmp_path / "opaque.png"
    expected_rgb = Image.new("RGB", (70, 100), (21, 35, 50))
    expected_rgb.save(opaque)
    before = opaque.read_bytes()
    pages = preprocess.prepare_pages(opaque, tmp_path / "opaque-out", enable_perspective=False, enable_deskew=False, enable_margin_crop=False)
    assert Path(pages[0].normalized_path).resolve() == opaque.resolve()
    assert opaque.read_bytes() == before
    assert ImageChops.difference(expected_rgb, preprocess.load_image(opaque)).getbbox() is None

    alpha = tmp_path / "rotated-alpha.png"
    exif = Image.Exif(); exif[274] = 6
    alpha_page().save(alpha, exif=exif)
    rotated = preprocess.prepare_source_pages(alpha, detect_perspective=False, deskew=False, crop_margins=False)[0]
    assert rotated.size == (240, 160)
    assert ImageChops.difference(paper_reference(alpha), rotated.image).getbbox() is None
    assert not rotated.image.getexif().get(274)


def test_old_blackened_alpha_cache_is_invalidated(tmp_path):
    source = tmp_path / "transparent.png"
    alpha_page().save(source)
    cache = tmp_path / "cache"
    kwargs = dict(enable_perspective=False, enable_deskew=False, enable_margin_crop=False)
    first = preprocess.prepare_pages(source, cache, **kwargs)
    manifests = list(cache.glob("image-normalized-*.json"))
    if not manifests:
        manifests = [p for p in cache.rglob("*.json") if "image" in p.name]
    assert len(manifests) == 1
    payload = json.loads(manifests[0].read_text())
    payload["version"] = 2
    manifests[0].write_text(json.dumps(payload))
    Image.new("RGB", (160, 240), "black").save(first[0].normalized_path)
    second = preprocess.prepare_pages(source, cache, **kwargs)
    assert not second[0].metadata.get("image_normalized_cache_hit")
    assert ImageChops.difference(paper_reference(source), second[0].image).getbbox() is None


@pytest.mark.parametrize("no_transforms", [False, True])
@pytest.mark.parametrize("crop_format", [CROP_FORMAT_V1, CROP_FORMAT_V2])
def test_whole_page_export_keeps_alpha_source_readable_through_edb(tmp_path, no_transforms, crop_format):
    source = tmp_path / "transparent-page.png"
    alpha_page().save(source)
    before = source.read_bytes()
    result = run_problem_export(
        source, output_dir=tmp_path / "out", input_intent="page-as-is", ocr="none",
        pdf_dpi=72, detect_perspective=False, skip_crop=no_transforms, skip_deskew=no_transforms,
        max_dimension=None, export_edb=True, crop_format=crop_format, dark_board=False,
    )
    session = result["ui_session"]
    assert len(session["pages"]) == len(session["problems"]) == 1
    normalized = Path(session["pages"][0]["sourceImagePath"].removeprefix("file://"))
    with Image.open(normalized) as image:
        assert image.mode == "RGB"
        assert ImageChops.difference(paper_reference(source), image).getbbox() is None
    primary_parts = result["edb_parts"]
    assert len(primary_parts) == 1 and primary_parts[0]["pageCountHint"] <= 50
    parsed = parse_edb(Path(primary_parts[0]["edbPath"]))
    assert parsed.record_count_actual == 1
    extracted = extract_images(parsed, Path(primary_parts[0]["edbPath"]), tmp_path / "images")
    assert extracted
    # Every source black glyph remains distinguishable from its white paper.
    with Image.open(extracted[0]) as embedded:
        pixels = np.asarray(embedded.convert("RGB"))
        assert np.any(np.all(pixels > 240, axis=2))
        assert np.any(np.all(pixels < 30, axis=2))
    assert source.read_bytes() == before
