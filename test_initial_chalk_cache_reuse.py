from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw

import build_problem_board_edb as board
from raster_media import stage2_media_regions
from structured_schema import Subject


def sample_crop():
    image = Image.new("RGB", (400, 320), "white")
    draw = ImageDraw.Draw(image)
    for top in range(30, 150, 22):
        draw.rectangle((25, top, 365, top + 5), fill="black")
    draw.rectangle((120, 180, 270, 280), fill=(30, 90, 190))
    draw.line((20, 299, 380, 299), fill=(60, 60, 60), width=2)
    return image


def task_for(path, *, text_priority=False, mode="off", chalk=(248, 249, 246)):
    region = {"kind": "image", "source": "pdf_image_block", "bbox": {"left": 120, "top": 180, "right": 271, "bottom": 281, "width": 151, "height": 101}}
    return board._ProblemAssetTask(
        source_image=sample_crop(), bounds=board.Box(left=0, top=0, width=400, height=320),
        crop_path=path, board_render_path=path.with_name("render.png"),
        chalk_color=chalk, text_priority=text_priority, subject=Subject.UNKNOWN,
        preserve_media_mode=mode, rendered_media_regions=[region],
    )


def legacy_reference(crop, task):
    enhanced = board._enhance_problem_crop(crop, text_priority=task.text_priority)
    cutout = board._extract_problem_cutout(enhanced, chalk_color=task.chalk_color, text_priority=task.text_priority)
    regions = stage2_media_regions(crop, task.rendered_media_regions if task.preserve_media_mode != "off" else [], chalk_math=False)
    return board._apply_selective_media_preservation(cutout, crop, regions)


@pytest.mark.parametrize("text_priority", [False, True])
@pytest.mark.parametrize("mode", ["off", "auto", "grayscale"])
def test_initial_cached_cutout_is_pixel_identical_to_previous_render(tmp_path, text_priority, mode):
    path = tmp_path / "source.png"
    crop = sample_crop(); crop.save(path)
    task = task_for(path, text_priority=text_priority, mode=mode)
    expected = legacy_reference(crop, task)
    actual = board._build_problem_board_asset(crop, task)
    assert actual.mode == expected.mode and actual.size == expected.size
    assert actual.tobytes() == expected.tobytes()
    assert list((path.parent / board.CHALK_CUTOUT_CACHE_DIRNAME).glob("*.png"))


def test_preview_seeds_publish_cache_without_repeating_cutout(tmp_path):
    path = tmp_path / "source.png"
    crop = sample_crop(); crop.save(path)
    task = task_for(path)
    first = board._build_problem_board_asset(crop, task)
    with patch.object(board, "_extract_problem_cutout", side_effect=AssertionError("publish rebuilt initial cutout")):
        published = board._cached_chalk_cutout(path, crop, chalk_color=task.chalk_color, text_priority=task.text_priority)
    assert first.tobytes() == published.tobytes()


def test_media_restore_does_not_poison_pure_chalk_cache(tmp_path):
    path = tmp_path / "source.png"
    crop = sample_crop(); crop.save(path)
    task = task_for(path, mode="auto")
    preserved = board._build_problem_board_asset(crop, task)
    direct = board._extract_problem_cutout(board._enhance_problem_crop(crop), chalk_color=task.chalk_color)
    with patch.object(board, "_extract_problem_cutout", side_effect=AssertionError("pure cache missing")):
        pure = board._cached_chalk_cutout(path, crop, chalk_color=task.chalk_color, text_priority=False)
    assert pure.tobytes() == direct.tobytes()
    assert preserved.tobytes() != pure.tobytes()


@pytest.mark.parametrize("changed", ["crop", "chalk", "text_priority"])
def test_initial_cache_is_invalidated_for_changed_source_or_policy(tmp_path, changed):
    path = tmp_path / "source.png"
    crop = sample_crop(); crop.save(path)
    task = task_for(path)
    board._build_problem_board_asset(crop, task)
    if changed == "crop":
        crop = crop.copy(); ImageDraw.Draw(crop).rectangle((2, 2, 14, 14), fill="black"); crop.save(path)
    elif changed == "chalk":
        task.chalk_color = (180, 210, 230)
    else:
        task.text_priority = True
    original = board._extract_problem_cutout
    with patch.object(board, "_extract_problem_cutout", wraps=original) as build:
        actual = board._cached_chalk_cutout(path, crop, chalk_color=task.chalk_color, text_priority=task.text_priority)
    assert build.call_count == 1
    expected = board._extract_problem_cutout(board._enhance_problem_crop(crop, text_priority=task.text_priority), chalk_color=task.chalk_color, text_priority=task.text_priority)
    assert actual.tobytes() == expected.tobytes()


def test_virtual_crop_does_not_create_cache_beside_unmaterialized_source(tmp_path):
    path = tmp_path / "not-written.png"
    crop = sample_crop()
    actual = board._build_problem_board_asset(crop, task_for(path))
    expected = legacy_reference(crop, task_for(path))
    assert actual.tobytes() == expected.tobytes()
    assert not (tmp_path / board.CHALK_CUTOUT_CACHE_DIRNAME).exists()
