from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image

import build_problem_board_edb as board


@pytest.mark.parametrize("pixels,available,workers", [
    (1_000_000, 32 * 1024**3, 4),
    (8_000_000, 32 * 1024**3, 2),
    (8_000_000, 512 * 1024**2, 1),
    (8_000_000, None, 2),
    (32_000_000, 32 * 1024**3, 1),
])
def test_large_record_frames_have_bounded_parallel_working_sets(pixels, available, workers):
    with patch.dict(board.os.environ, {"EDB_IMAGE_RECORD_WORKERS": ""}), patch.object(board.os, "cpu_count", return_value=16), patch.object(board, "_available_image_record_memory_bytes", return_value=available):
        assert board._resolve_image_record_worker_count(10, source_pixels=pixels) == workers


@pytest.mark.parametrize("override,expected", [("1", 1), ("4", 2), ("0", 1), ("invalid", 2)])
def test_explicit_worker_setting_remains_a_cap_inside_memory_budget(override, expected):
    with patch.dict(board.os.environ, {"EDB_IMAGE_RECORD_WORKERS": override}), patch.object(board.os, "cpu_count", return_value=16), patch.object(board, "_available_image_record_memory_bytes", return_value=32 * 1024**3):
        assert board._resolve_image_record_worker_count(8, source_pixels=8_000_000) == expected


@pytest.mark.skipif(board.os.name == "nt", reason="POSIX/cgroup headroom probe")
def test_headroom_uses_smaller_cgroup_remaining_memory_instead_of_host_ram():
    values = {"/sys/fs/cgroup/memory.max": str(2 * 1024**3), "/sys/fs/cgroup/memory.current": str(1536 * 1024**2)}
    def read(path, *args, **kwargs):
        if str(path) not in values:
            raise FileNotFoundError(str(path))
        return values[str(path)]
    with patch.object(board.os, "sysconf", side_effect=lambda key: {"SC_AVPHYS_PAGES": 8 * 1024**3 // 4096, "SC_PAGE_SIZE": 4096}[key]), patch.object(Path, "read_text", read):
        assert board._available_image_record_memory_bytes() == 512 * 1024**2


@pytest.mark.skipif(board.os.name == "nt", reason="POSIX/cgroup headroom probe")
def test_unknown_headroom_does_not_disable_parallel_small_images():
    with patch.object(board.os, "sysconf", side_effect=ValueError), patch.object(Path, "read_text", side_effect=FileNotFoundError):
        assert board._available_image_record_memory_bytes() is None


def test_record_worker_budget_reads_headers_without_decoding_all_sources(tmp_path):
    placements = []
    entries = {}
    for index in range(4):
        source = tmp_path / f"page-{index}.png"
        Image.new("RGB", (2000, 4000), "white").save(source)
        problem_id = f"page-{index}"
        placements.append(SimpleNamespace(problem_id=problem_id, metadata={"crop_path": str(source)}))
        entries[problem_id] = SimpleNamespace(input_intent="page-as-is")
    with patch.object(Image.Image, "load", side_effect=AssertionError("all pages decoded during header probe")), patch.object(board, "_available_image_record_memory_bytes", return_value=32 * 1024**3), patch.dict(board.os.environ, {"EDB_IMAGE_RECORD_WORKERS": ""}), patch.object(board, "_build_image_only_record_image", side_effect=lambda placement, *args, **kwargs: placement.problem_id), patch.object(board, "_resolve_image_record_worker_count", wraps=board._resolve_image_record_worker_count) as resolve:
        result = board._build_image_only_record_images(placements, entries, source_layout=None, dark_board=True, board_theme=board.DEFAULT_BOARD_THEME, crop_format=board.CROP_FORMAT_V1, target_image_width_px=0)
    assert result == [placement.problem_id for placement in placements]
    resolve.assert_called_once_with(4, source_pixels=8_000_000)


def test_geometry_only_planning_does_not_open_sources_or_probe_memory():
    placement = SimpleNamespace(problem_id="p", metadata={"crop_path": "/missing.png"})
    with patch.object(Image, "open", side_effect=AssertionError("planning opened source")), patch.object(board, "_available_image_record_memory_bytes", side_effect=AssertionError("planning probed memory")), patch.object(board, "_build_image_only_record_image", return_value="geometry"):
        result = board._build_image_only_record_images([placement], {"p": SimpleNamespace(input_intent="page-as-is")}, source_layout=None, dark_board=True, board_theme=board.DEFAULT_BOARD_THEME, crop_format=board.CROP_FORMAT_V1, target_image_width_px=0, generate_records=False)
    assert result == ["geometry"]
